# -*- coding: utf-8 -*-
"""总览：把「每家公司走到流水线第几步」聚合成一张表。

两件事值得先说清楚，改这里之前务必读懂：

1. **这个聚合很慢，必须走缓存。**
   `registry.clean_status()` 要在 /mnt/d（NTFS）上全树 walk 近 4000 个产品，实测 43 秒；
   `idmap.company_overview()` 再要 17 秒。总览页**绝不能同步调它们**，否则每次进首页干等一分钟。
   所以：`load_cache()` 秒开（读一个 json），`build()` 只在用户点「重新扫描」时以后台任务跑，
   跑完写缓存。页头永远显示「扫描于 …」，让用户知道自己看的是什么时候的状态。

2. **复查状态需要自己的记忆。**
   ②映射有 mapped_companies.json、③清洗有 cleaned_companies.json、⑤id映射能从输出目录推导，
   唯独 ④复查 只往 history.json 写（而用户可以删历史）。所以这里补一份
   audited_companies.json，指纹沿用清洗那套（映射输出的 产品数 + 子树最大 mtime）——
   指纹变了说明清洗前的数据动过，那次复查的结论就不再作数，状态回到「有更新」。
   不这么做的话，「复查通过」这个状态会骗人。
"""
import os, json, time, threading

from .paths import DATA_DIR
from . import registry, idmap, manual, review, pipeline, mapping, variants, shelf

AUDIT_PATH = os.path.join(DATA_DIR, "audited_companies.json")
CACHE_PATH = os.path.join(DATA_DIR, "overview_cache.json")
_lock = threading.Lock()


def _read(path, default):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            return default
    return default


def _write(path, data):
    tmp = path + ".tmp"
    json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------------- 复查记忆 ----------------
def load_audit():
    return _read(AUDIT_PATH, {})


def record_audit(mapping_out, per_company):
    """run_audit 跑完后调用。per_company: {公司: {ok, checked, losses, pdf_missing, unmatched}}。
    指纹当场取，代表「这个结论是针对这一版数据得出的」。"""
    if not per_company:
        return
    with _lock:
        reg = load_audit()
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for comp, r in per_company.items():
            n, mt = registry.fingerprint(mapping_out, comp)
            reg[comp] = {**r, "products": n, "mtime": mt, "time": now}
        _write(AUDIT_PATH, reg)


def audit_status(reg, company, products, mtime):
    """→ done（通过）/ failed（有损失）/ stale（数据变过，结论作废）/ none（没查过）"""
    r = reg.get(company)
    if not r:
        return "none", {}
    if r.get("products") != products or abs(float(r.get("mtime") or 0) - mtime) > 0.5:
        return "stale", r
    return ("done" if r.get("ok") else "failed"), r


# ---------------- 聚合 ----------------
def _variants(names, extra=()):
    """目录名带消歧后缀（`_` / `_1`）且基名也在候选集里 → 同一家公司被当成两家在跑。
    这是重复劳动最常见的来源，所以在总览就点出来（只提示，不自作主张合并）。

    后缀规则走 variants.base_name()，与「同名变体」卡片同一处口径 —— 两边各写一份
    会让矩阵里的标签和卡片里的组对不上（它们并排显示在同一页上）。
    extra 补充候选集（如只存在于清洗后目录的公司），但只有 names 里的才会被标记。
    """
    s = set(names) | set(extra)
    out = {}
    for n in names:
        base = variants.base_name(n)
        if base != n and base in s:
            out[n] = base
    return out


def _input_index(cfg, known=None):
    """输入目录里的公司 {名: {name, products, status}}。

    没有这一步，**刚爬下来还没映射的公司在总览里根本不存在** —— 其余数据源
    （映射输出 / 未匹配 / 过程记录）都要等映射跑完才有内容。用户只能靠"先跑一次
    全流程、再点刷新"才看得见新公司，那时候活已经干完了，总览也就失去了意义。

    ⚠ 数产品要全树 walk，实测输入目录 87 家 / 10573 个产品要 **97 秒**，
    所以这里支持增量：known={公司名: 上次数到的产品数}，命中就不再 walk 那一家。
    build() 传 known=None 做全量核对；refresh()/秒开路径只 walk 没见过的新公司。
    """
    root = cfg.get("input") or ""
    names = mapping.list_company_dirs(root)
    if not names:
        return {}
    known = known or {}
    comps = []
    for c in names:
        n, mt = known.get(c), None
        if n is None:
            try:
                n, mt = registry.fingerprint(root, c)   # 一遍 walk 拿到数量 + 最大 mtime
            except OSError:
                continue
        if n:
            comps.append({"name": c, "products": int(n),
                          **({"mtime": mt} if mt is not None else {})})
    return {c["name"]: c for c in registry.status_of(comps)}


_alive_memo = {"t": 0.0, "key": "", "names": frozenset()}
_alive_lock = threading.Lock()


def _alive_names(cfg, ttl=10.0):
    """流水线各处「还有实际数据」的公司名全集。

    以前是每家公司 5 次 isdir（`_alive`），行数一多就是几百次跨 drvfs 的系统调用 ——
    136 行 × 5 = 680 次，而 `_ghosts` 是挂在 `sync_input` 上、**每次打开总览都要跑**的。
    改成 5 次 listdir 一次性取回全集，判定退化成集合查表。缓存十秒，与 `_dirs_memo` 同理。
    """
    key = "|".join(str(cfg.get(k) or "") for k in ("mapping_out", "unmatched", "clean_out"))
    with _alive_lock:
        m = _alive_memo
        if m["key"] == key and time.time() - m["t"] < ttl:
            return m["names"]
    roots = [cfg.get("mapping_out") or "", cfg.get("unmatched") or ""]
    roots += [os.path.join(cfg.get("clean_out") or "", c) for c in variants.CATS]
    names = set()
    for r in roots:
        try:
            names.update(os.listdir(r))
        except OSError:
            pass          # 路径不存在/读不了：当作这一处没有数据
    names = frozenset(names)
    with _alive_lock:
        _alive_memo.update(t=time.time(), key=key, names=names)
    return names


def _alive(cfg, name):
    """这家公司在流水线的任何一处还有实际数据吗。"""
    return name in _alive_names(cfg)


_allco_memo = {"t": 0.0, "key": "", "names": frozenset()}
_allco_lock = threading.Lock()


def _all_companies_cached(cfg, ttl=10.0):
    """`variants._all_companies()` 的十秒缓存 —— 变体标记要用的全体公司名。

    原函数是 14 次跨 drvfs 的 listdir + isdir（映射输出、未匹配、清洗后的 12 个类别），
    而它挂在 `sync_input` 上、**每次打开总览都要跑一遍**。实测 `/api/overview`
    因此要 7~20 秒，还全程占着 `_lock`，把后台清点线程一起堵死。
    缓存十秒，与 `_alive_names` / `_dirs_memo` 同理：目录增减本来就靠刷新发现，
    晚十秒完全无所谓。
    """
    key = "|".join(str(cfg.get(k) or "") for k in ("mapping_out", "unmatched", "clean_out"))
    with _allco_lock:
        m = _allco_memo
        if m["key"] == key and time.time() - m["t"] < ttl:
            return m["names"]
    names = frozenset(variants._all_companies(cfg))
    with _allco_lock:
        _allco_memo.update(t=time.time(), key=key, names=names)
    return names


def _ghosts(cfg, rows):
    """只剩「过程记录」残留、实际已经没有任何产品的公司。

    过程记录（研判归类.json / 人工映射记录.json）在公司目录被删掉之后不会跟着消失，
    而 review.list_companies 只看这份记录，于是这些公司会以「0 产品 + N 待复核」
    的形态永远挂在总览上 —— 那些待复核条目指向的产品目录早就不在了，点进去也处理不了。
    实测 4 组同名变体合并之后，被删掉的那一半就是这么留下来的。

    ⚠ **判定只看「输入目录里有没有」和「流水线各处有没有」，绝不看 `products`。**
    这里原先第一句是 `if r.get("products") or r.get("in_input"): continue` ——
    拿快照里缓存的产品数当"这家公司是真的"的凭据。可对一个已经不存在的公司，
    那个数字**必然**是陈旧的，于是它正好豁免了最该被清掉的那一类行：

      用户「移除公司」时没勾「同时删除输入目录」→ 平台产出与五份记忆都清了，
      但源目录还在 → sync_input 把它当新公司重新入表（products 记着旧值）→
      用户再去资源管理器里手工删掉源目录 → in_input 变 False，可 products 还是旧值
      → 这行**永远清不掉**。

    实测「株式会社大阪真空机器制作所」：输入目录、映射输出、未匹配、过程记录全都没有，
    五份记忆里一条记录都没有，却带着 `products=101` 挂在总览上，表现为
    流水线状态永远「1 家未映射 / 1 家未处理 / 1 家待查」、移除公司列表永远多一家（136/135）。

    `_alive_names` 是集合查表，所以对所有不在输入目录的行做检查也很便宜。
    """
    alive = _alive_names(cfg)
    # 安全阀：一个都列不出来（路径配错、盘没挂上）时**绝不清任何行** ——
    # 否则一次读盘失败就会把整张总览判成幽灵全部抹掉。
    if not alive:
        return []
    return [r["name"] for r in rows
            if not r.get("in_input") and r["name"] not in alive]


# 注：曾有一个 _known_input(snap)，把快照里缓存的输入产品数喂给 refresh 以免重复 walk。
# 那正是「重跑完了还赖在待映射里」的根源（见下面 refresh 里的注释），已删除。
# build() 走 _input_index(known=None) 全量现数，sync_input 自己按行取值，都不需要它。


def _input_index_for(cfg, names):
    """只针对这几家公司查输入目录状态（现场重数，不接受外部传入的旧值）。

    refresh() 每次只重算几家公司，绝不能顺手把整个输入目录 walk 一遍
    （87 家 / 10573 产品 = 97 秒，而 refresh 的存在意义就是"别等一分钟"）。
    但**这几家必须真数一遍** —— 原先有个 `known` 参数用来沿用快照里的旧值，
    那正是「重跑完了还赖在待映射里」的根源，已连同 `_known_input` 一起删掉。
    """
    root = cfg.get("input") or ""
    dirs = set(mapping.list_company_dirs(root) or [])
    comps = []
    for c in names:
        if c not in dirs:
            continue
        try:
            n, mt = registry.fingerprint(root, c)
        except OSError:
            continue
        if n:
            comps.append({"name": c, "products": int(n), "mtime": mt})
    return {c["name"]: c for c in registry.status_of(comps)}


# 这里曾经有 SYNC_BUDGET=8 / SYNC_SECONDS=2.0 两个"现场清点预算"。**别再加回来** ——
# 任何形式的预算都挡不住这件事，因为 `registry.fingerprint()` 是不可中断的：
# 预算只能决定"要不要开始数下一家"，不能在数到一半时喊停。实测单家最坏 39.9 秒。
# 正确做法是根本不在请求线程上数（见 _count_bg）。
# 输入目录 listdir 在 NTFS 上要 1.2 秒，而 /api/overview/badge 是**每个页面加载都调**的，
# 每次都去读一遍磁盘等于给全站每个页面加 1.2 秒。缓存十秒，新公司晚十秒出现完全无所谓。
_dirs_memo = {"t": 0.0, "root": "", "names": []}
_dirs_lock = threading.Lock()


def _company_dirs_cached(root, ttl=10.0):
    with _dirs_lock:
        m = _dirs_memo
        if m["root"] == root and time.time() - m["t"] < ttl:
            return m["names"]
    names = mapping.list_company_dirs(root) or []
    with _dirs_lock:
        _dirs_memo.update(t=time.time(), root=root, names=names)
    return names


def sync_input(cfg=None, snap=None):
    """把输入目录里「总览还不知道」的公司补进缓存。GET /api/overview 会调它。

    只 listdir（瞬时）比对名字，仅对**缓存里没有的**公司现场数一次产品数，
    因此稳定状态下几乎零成本，而新爬的公司打开总览就能看见 —— 不必先跑一次
    全流程再点刷新。超出预算的新公司先按 0 产品占位，重新扫描时会补准。
    """
    cfg = cfg or pipeline.load_config()
    with _lock:
        snap = snap or load_cache()
        if not snap:
            return None
        rows = {r["name"]: r for r in snap.get("rows", [])}
        names = _company_dirs_cached(cfg.get("input") or "")
        if not names:
            return snap
        inset = set(names)
        # 先廉价回填已有行的 in_input 标记（只靠 listdir，不数产品）。
        # 没有这一步，旧快照里的公司永远不会被算进「待映射」，除非跑一次三分钟的全量重扫。
        touched = (snap.get("summary") or {}).get("_v") != SUMMARY_VERSION
        for name, row in rows.items():
            want = name in inset
            if bool(row.get("in_input")) != want:
                row["in_input"] = want
                touched = True
        # 待补清点的：上次超预算只占了个位、或早先被旧口径记成 0 产品的。
        # 每次刷新顺手补几家，几次页面加载之后自然就补齐了，不必等一次全量重扫。
        stale_count = [c for c in names
                       if c in rows and not rows[c].get("input_products")
                       and rows[c].get("map") != "done"]
        # 只剩过程记录残留的幽灵公司：顺手剔掉，否则它们会永远挂在总览和待办徽标上
        ghosts = _ghosts(cfg, list(rows.values()))
        for g in ghosts:
            rows.pop(g, None)
            touched = True
        # 真正没见过的新公司，与「已经在表里、只是产品数没数准」分开记 ——
        # 前端的「发现 N 家新公司」toast 只该为前者弹（见下面 input_added）。
        brand_new = [c for c in names if c not in rows]
        missing = brand_new + stale_count
        if not missing:
            if touched:
                var = _variants(list(rows), _all_companies_cached(cfg)) if ghosts else None
                if var is not None:
                    for name, row in rows.items():
                        row["variant_of"] = var.get(name, "")
                new_rows = sorted(rows.values(), key=lambda r: r["name"].lower())
                snap["rows"] = new_rows
                snap["summary"] = _summarize(new_rows)
                snap.pop("input_added", None)
                if ghosts:
                    snap["pruned"] = ghosts
                _write(CACHE_PATH, snap)
            return snap
        # 一律先占位、立刻返回，真正的清点交给后台（见 _count_bg 上面那段说明）。
        # 这里一次 fingerprint 都不做 —— 请求线程上任何一次子树 walk 都可能是几十秒。
        counted = {c: (0, None) for c in missing}    # 二元组：(数量, 最大 mtime)
        uncounted = list(missing)
        _count_bg(missing)
        fresh = {c["name"]: c for c in registry.status_of(
            [{"name": k, "products": v[0],
              **({"mtime": v[1]} if v[1] is not None else {})}
             for k, v in counted.items()])}
        map_reg, audit_reg = registry.load(), load_audit()
        un = set(uncounted)
        for c, rec in fresh.items():
            old = rows.get(c) or {}
            if un and c in un and old:
                continue          # 这一轮没轮到它清点，保留原样，下次再补
            # 补清点的行只更新输入相关字段，别把已有的清洗/复查/id 状态覆盖成空
            row = _row(c, None, None, {}, 0, map_reg, audit_reg, old.get("variant_of", ""), rec)
            if old:
                for k in ("clean", "clean_time", "audit", "audit_time", "audit_detail",
                          "idmap", "cid", "how", "unmatched", "shelved",
                          "review_pending", "review_total"):
                    row[k] = old.get(k, row[k])
                if old.get("products"):
                    row["products"] = old["products"]
            # 超预算没来得及清点的新公司：先占位入表，让人看得见、点得到，
            # 产品数由后续几次刷新或一次全量扫描补准，而不是把首页卡在磁盘上。
            row["count_pending"] = c in un
            rows[c] = row
        var = _variants(list(rows), _all_companies_cached(cfg))
        for name, row in rows.items():
            row["variant_of"] = var.get(name, "")
        new_rows = sorted(rows.values(), key=lambda r: r["name"].lower())
        snap["rows"] = new_rows
        snap["summary"] = _summarize(new_rows)
        snap["partial"] = True
        # 只报真正第一次见到的公司。原先写的是 `sorted(fresh)`，而 fresh 覆盖整个
        # missing（含 stale_count 那批"已在表里、只是产品数没数准"的老公司）——
        # 一家公司只要产品数没补准，它就每次都进 missing、每次都被写进 input_added，
        # 前端每收到一次就弹一次「输入目录里发现 1 家新公司」，3 秒一遍没完没了。
        added = sorted(set(fresh) & set(brand_new))
        if added:
            snap["input_added"] = added
        else:
            snap.pop("input_added", None)
        _write(CACHE_PATH, snap)
        return snap


def _map_state(n, c, map_reg, in_st):
    """② 映射列。in_st = 该公司在输入目录里的映射记忆状态（不在输入目录则为 None）。

    输入目录里有、但记忆判定「新增」→ 还没映射过；判定「有更新」→ 源数据变了要重跑。
    这两种都必须在总览里显示出来，否则新公司永远是隐形的。
    """
    if in_st == "新增":
        return "none"
    if in_st == "有更新":
        return "stale"
    return "done" if (c or map_reg.get(n)) else "none"


def _row(n, c, i, r, unmatched_n, map_reg, audit_reg, variant_of, in_rec=None, shelved_n=0):
    """拼一家公司的总览行。build（全量）与 refresh（增量）共用，口径只有这一处。
    c=清洗状态行 i=id解析行 r=研判计数 in_rec=输入目录记录 —— 都可能是 None/空。"""
    products = c["products"] if c else int((in_rec or {}).get("products") or 0)
    a_st, a_rec = audit_status(audit_reg, n, (c or {}).get("products", 0), (c or {}).get("mtime", 0))

    clean_st = "none" if not c else \
        {"已完成": "done", "有更新": "stale", "新增": "none"}.get(c["status"], "none")

    if i is None:
        id_st = "none"
    elif not i["resolved"]:
        id_st = "noid"
    elif i["mapped"]:
        id_st = "done"
    else:
        id_st = "todo"

    return {
        "name": n,
        "products": products,
        "in_input": bool(in_rec),
        # 存下来供下次增量刷新复用，免得为了一行去 walk 整个输入目录（97 秒）
        "input_products": int((in_rec or {}).get("products") or 0),
        "map": _map_state(n, c, map_reg, (in_rec or {}).get("status"))
        ,
        "map_time": (map_reg.get(n) or {}).get("time", ""),
        "clean": clean_st,
        "clean_time": (c or {}).get("last_time", ""),
        "audit": a_st,
        "audit_time": a_rec.get("time", ""),
        "audit_detail": {k: a_rec.get(k, 0) for k in
                         ("checked", "losses", "pdf_missing", "unmatched", "html_missing")} if a_rec else {},
        "idmap": id_st,
        "cid": (i or {}).get("id", ""),
        "how": (i or {}).get("how", ""),
        "unmatched": unmatched_n,
        "shelved": shelved_n,
        "review_pending": (r or {}).get("pending", 0),
        "review_total": (r or {}).get("total", 0),
        "variant_of": variant_of,
    }


def build(job=None, cfg=None):
    """全量扫描并写缓存。很慢（映射输出 40s + id 17s + 输入目录 90s），只应作后台任务。"""
    cfg = cfg or pipeline.load_config()
    log = job.log if job else (lambda *a: None)
    if job:
        job.step(0, 6)

    log("扫描映射输出下每家公司的清洗状态 …（这一步要全树 walk，约 40 秒）")
    clean_rows = registry.clean_status(cfg["mapping_out"], cfg["clean_out"])
    if job:
        job.step(1)

    log("读取 id 解析情况 …")
    id_rows, id_err = [], ""
    try:
        id_rows = idmap.company_overview(cfg["clean_out"], cfg["final_out"],
                                         cfg["company_id"], cfg["product_id"])
    except Exception as e:
        id_err = str(e)
        log(f"⚠ id 解析读取失败（不影响其余列）：{e}")
    if job:
        job.step(2)

    log("统计未匹配与待复核 …")
    mn_rows = manual.list_companies(cfg["unmatched"])
    mn = {c["name"]: c["products"] for c in mn_rows}
    sh = {g["name"]: g["n"] for g in shelf.overview(cfg["unmatched"])["companies"]}
    rv = {c["name"]: c for c in review.list_companies(cfg["process"])}
    if job:
        job.step(3)

    log("清点输入目录（全量核对，这一步也要全树 walk，约 1.5 分钟）…")
    in_idx = _input_index(cfg)          # 还没映射的新公司也要出现在总览里
    if in_idx:
        log(f"输入目录里有 {len(in_idx)} 家公司（其中 "
            f"{sum(1 for v in in_idx.values() if v['status'] != '已完成')} 家待映射）")
    if job:
        job.step(4)

    map_reg, audit_reg = registry.load(), load_audit()
    idx = {r["name"]: r for r in id_rows}
    clean_idx = {r["name"]: r for r in clean_rows}

    names = sorted(set(clean_idx) | set(mn) | set(rv) | set(in_idx) | set(sh),
                   key=lambda s: s.lower())
    var = _variants(names, variants._all_companies(cfg))
    rows = [_row(n, clean_idx.get(n), idx.get(n), rv.get(n, {}), mn.get(n, 0),
                 map_reg, audit_reg, var.get(n, ""), in_idx.get(n), sh.get(n, 0))
            for n in names]
    # 只剩过程记录残留的幽灵公司不进快照（review.list_companies 会把它们捞出来）
    ghosts = set(_ghosts(cfg, rows))
    if ghosts:
        rows = [r for r in rows if r["name"] not in ghosts]
        log(f"剔除 {len(ghosts)} 家只剩过程记录残留的公司：" + "、".join(sorted(ghosts)[:5]))
    if job:
        job.step(5)

    snap = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rows": rows,
        "paths": {k: cfg.get(k, "") for k in
                  ("input", "mapping_out", "unmatched", "process", "clean_out", "final_out")},
        "id_error": id_err,
        "summary": _summarize(rows),
    }
    with _lock:
        _write(CACHE_PATH, snap)
    log(f"总览已更新：{len(rows)} 家公司，{snap['summary']['pending']} 家有待办")
    if job:
        job.step(6)
    return snap


# 每次给 _summarize 加/改字段就 +1。缓存里的 summary 是旧版本算的时候会被自动重算，
# 否则界面上会读到 undefined，而缓存又可能几天都不会被整体重建。
SUMMARY_VERSION = 3


def _summarize(rows):
    def tot(pred):
        return sum(r["products"] for r in rows if pred(r))
    # ③ 与 ⑤ 的口径必须让 ③ ⊇ ⑤ —— 否则轨道上会出现「最终交付比清洗后还多」，
    # 而整张图讲的正是「每一段的产品数都应当对得上」。clean=="stale" 表示
    # 已经清洗过、之后源又变了，它的产物确实躺在清洗后目录里，要算进已清洗。
    cleaned_any = lambda r: r["clean"] in ("done", "stale")
    return {
        "companies": len(rows),
        "products": tot(lambda r: True),
        "mapped": sum(1 for r in rows if r["map"] == "done"),
        # ② 的产品数只能算已映射公司的。算 products 总和会把待映射公司的输入产品也加进去，
        # 于是 ② 的产品数比 ③ 还大 —— 与 P1-6 是同一类错。
        "mapped_products": tot(lambda r: r["map"] == "done"),
        # 待映射只数「输入目录里确实有源数据」的 —— 只在未匹配/过程记录里露过面的
        # 公司没有源可映射，把它们算进来会让待办数字虚高。
        "unmapped": sum(1 for r in rows if r.get("in_input") and r["map"] != "done"),
        # 输入产品数在廉价回填的行上可能还没数过（数一次要 walk 全树），退回用已知产品数
        "unmapped_products": sum(r.get("input_products") or r.get("products", 0) for r in rows
                                 if r.get("in_input") and r["map"] != "done"),
        "map_stale": sum(1 for r in rows if r["map"] == "stale"),
        "cleaned": sum(1 for r in rows if cleaned_any(r)),
        "cleaned_products": tot(cleaned_any),
        "clean_todo": sum(1 for r in rows if r["clean"] == "none"),
        "clean_stale": sum(1 for r in rows if r["clean"] == "stale"),
        "audited": sum(1 for r in rows if r["audit"] == "done"),
        "audit_failed": sum(1 for r in rows if r["audit"] == "failed"),
        "audit_unknown": sum(1 for r in rows if r["audit"] in ("none", "stale")),
        "idmapped": sum(1 for r in rows if r["idmap"] == "done"),
        "idmapped_products": tot(lambda r: r["idmap"] == "done" and cleaned_any(r)),
        "idmap_todo": sum(1 for r in rows if r["idmap"] in ("todo", "none")),
        "no_id": sum(1 for r in rows if r["idmap"] == "noid"),
        "with_unmatched": sum(1 for r in rows if r["unmatched"] > 0),
        "unmatched_products": sum(r["unmatched"] for r in rows),
        # 搁置＝人工确认"这个归不了类"，已经不算待办，但必须看得见 ——
        # 否则它就是一个只进不出的黑洞，没人知道自己埋了多少东西进去。
        "with_shelved": sum(1 for r in rows if r.get("shelved")),
        "shelved_products": sum(r.get("shelved") or 0 for r in rows),
        "with_review": sum(1 for r in rows if r["review_pending"] > 0),
        "review_products": sum(r["review_pending"] for r in rows),
        "variants": sum(1 for r in rows if r.get("variant_of")),
        "pending": sum(1 for r in rows if needs_attention(r)),
        "_v": SUMMARY_VERSION,
    }


def refresh(names, cfg=None):
    """任务跑完后就地更新缓存里这几家公司的行。

    为什么不直接重跑 build()：build 要全树 walk 近 4000 个产品、约 60 秒，
    跑完一家公司就等一分钟没人受得了，结果就是没人刷新、总览永远是旧的。
    这里只对传进来的公司做单家扫描（毫秒级），其余行原样保留。

    没有缓存时直接返回 None —— 那种情况本来就要引导用户去做一次完整扫描。
    """
    names = [n for n in dict.fromkeys(names or []) if n]
    if not names:
        return None
    # 统计页的类别分布按公司分片缓存，这里是**所有改动路径的唯一汇合点**
    # （②映射/人工匹配/AI复核/变体合并/③④⑤ 都会走到 refresh），
    # 在这里统一打脏标，下次统计重扫只走这几家。局部导入避开循环引用。
    try:
        from . import stats as _stats
        _stats.mark_dist_dirty(names)
    except Exception:
        pass
    cfg = cfg or pipeline.load_config()
    if load_cache() is None:
        return None                    # 没快照就别白扫一遍盘

    # ---- 磁盘扫描全部在锁外做 ----
    #
    # ⚠ **这一段曾经整个包在 `with _lock:` 里，是「公司永远停在正在后台清点」的根源。**
    # 每家要一次子树 walk（357 个产品的「鞍山市激埃特光电有限公司」实测 18.4 秒），
    # 而 `_lock` 同时保护着 `GET /api/overview` 里的 sync_input。于是形成活锁：
    #   后台清点线程抢不到锁 → 那家公司一直留在 `_counting` 里 → 接口返回的
    #   `counting` 不空 → 前端 watchCounting 每 3 秒回取一次 → 请求线程反复抢锁
    #   → 清点线程继续饿死。
    # 表现：产品数永远 0、`count_pending` 永远为真、「发现 1 家新公司」的 toast
    # 每 3 秒弹一次，快照 generated 停在几小时前不动（sync_input 不写 generated，
    # 只有 refresh 写 —— 这正是判断"refresh 一次都没跑完"的依据）。
    # 锁只用来保护「读快照 → 改几行 → 写回」这段纯内存操作，不许再放任何 I/O 进去。
    map_reg, audit_reg = registry.load(), load_audit()
    # 只查要刷新的这几家，别顺手扫整个输入目录（那要 97 秒，refresh 就失去意义了）。
    #
    # ⚠ **但这几家必须现场重数，不能传 known 沿用快照里的旧值**（曾经传了 `_known_input(snap)`）。
    # refresh 存在的全部意义就是"这几家变了、重算它们"，沿用变化前的输入产品数是自相矛盾的：
    # `registry.status_of` 拿【映射记忆里的产品数】和【输入目录的产品数】比，缓存的那个
    # 一旦偏了，就会永远判成「有更新」→ map=stale → 这家公司**永远赖在「待映射」里**，
    # 重跑多少次都不消失（重跑只更新记忆，而缓存那一侧纹丝不动，差值原样存在）。
    # 实测「广州飒特红外」：剪掉爬虫缓存目录后输入实际 25 个，快照里还写着 49，
    # 于是 25≠49 → 有更新 → 一直挂在待映射上。
    in_idx = _input_index_for(cfg, names)
    scanned = {}
    for n in names:
        try:
            c = registry.clean_status_one(cfg["mapping_out"], cfg["clean_out"], n)
            r = review.company_stat(cfg["process"], n)
            un = manual.count_company(cfg["unmatched"], n)
        except Exception:
            continue                   # 这一家读不动：保留它原来的行，别动
        try:
            i = idmap.company_row_one(cfg["clean_out"], cfg["final_out"],
                                      cfg["company_id"], n)
            id_failed = False
        except Exception:
            i, id_failed = None, True  # id 表读不出来：保留上次的 id 列，别误报成 none
        # alive_count 也是读盘，原先在下面被调了两次，这里一次算完带进锁里
        scanned[n] = (c, i, r, un, shelf.alive_count(cfg["unmatched"], n), id_failed)
    all_comps = _all_companies_cached(cfg)     # 变体标记要用，同样是 walk，同样放锁外

    with _lock:
        snap = load_cache()
        if not snap:
            return None
        rows = {x["name"]: x for x in snap.get("rows", [])}
        for n, (c, i, r, un, sh, id_failed) in scanned.items():
            old = rows.get(n, {})
            if not c and not un and not (r and r["total"]) and n not in in_idx and not sh:
                rows.pop(n, None)          # 四处都没了 → 这家公司已不在流水线里
                continue
            row = _row(n, c, i, r, un, map_reg, audit_reg,
                       old.get("variant_of", ""), in_idx.get(n), sh)
            if id_failed and old:
                row.update({k: old.get(k, row[k]) for k in ("idmap", "cid", "how")})
            rows[n] = row
        # 变体标记依赖全体公司名，行增删后要重算
        var = _variants(list(rows), all_comps)
        for name, row in rows.items():
            row["variant_of"] = var.get(name, "")
        new_rows = sorted(rows.values(), key=lambda r: r["name"].lower())
        snap["rows"] = new_rows
        snap["summary"] = _summarize(new_rows)
        snap["generated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        snap["partial"] = True          # 让页头能说明"这是增量更新过的"
        _write(CACHE_PATH, snap)
        return snap


def needs_attention(r):
    """一家公司「需要你去看一眼」的判据。徽标按公司去重计数——
    一家欠三件事也只算一家，否则数字会比实际要处理的对象多，反而不可信。

    「输入目录里有、但还没映射（或源数据变过）」也算 —— 那正是最该被看见的一类，
    以前它连行都没有，自然也进不了徽标。"""
    return (r["unmatched"] > 0 or r["review_pending"] > 0
            or r["clean"] == "stale" or r["idmap"] == "noid"
            or r["audit"] == "failed"
            or bool(r.get("in_input") and r["map"] != "done"))


# ---------------- 新公司的后台清点 ----------------
#
# ⚠ **清点新公司绝不能发生在 `GET /api/overview` 的请求线程上。**
# 数一家公司要 `registry.fingerprint()` 走完它整棵子树，而这是**不可中断**的：
# 早先那套「预算 8 家 / 2 秒」只在每家**开始之前**判断一次，一旦进去就必须走完。
# 实测输入目录里 `US Conec Ltd`（2512 个产品）单家 **39.9 秒**、横河 684 个 16.3 秒 ——
# 也就是说只要新来一家大公司，**打开总览就卡四十秒**；一次进来十几家时，
# 第一家吃满预算、其余记成 0 产品占位，下次打开又挑一家现场数，于是**每开一次页面卡一次**。
#
# 现在改成：请求线程只 listdir 比名字（毫秒级），新公司立刻以「待清点」占位入表并返回，
# 真正的清点丢进这里的后台线程，数完一家就 `refresh()` 写回一家，前端轮询自然取到。
_counting = set()                       # 正在后台清点的公司
_counting_lock = threading.Lock()
# 刚清点完的公司 → 完成时刻。冷却期内不再重复投递。
#
# ⚠ 没有这道闸的话，任何一家「数出来就是 0 个产品」的公司都会变成永久的后台磨盘：
# sync_input 的 stale_count 判据是「input_products 为空且 map != done」，
# 而输入目录里的空目录（或子树读不动、`_input_rows` 里 `if n:` 把它整个丢掉的那些）
# 数完仍然是 0 —— 于是下一次 sync_input 又把它排进 missing，又一次全树 walk，
# 单家几十秒地无限循环，白白占着 drvfs。冷却期让它最多每 5 分钟重试一次。
_counted_at = {}
_COUNT_COOLDOWN = 300.0


def counting_now():
    """当前正在后台清点的公司名（前端据此决定要不要过几秒再取一次总览）。"""
    with _counting_lock:
        return sorted(_counting)


def _count_bg(names):
    """把这几家新公司丢进后台逐家清点。已在清点中、或刚清点过的不重复投递。

    **逐家串行**：并发去 walk 只会在 drvfs 上互相抢 I/O，而且这活是陪跑的，
    不该跟用户手动提交的任务抢磁盘。数完一家就写回一家，界面上是一家一家浮现。
    """
    names = [n for n in dict.fromkeys(names or []) if n]
    if not names:
        return
    now = time.time()
    with _counting_lock:
        todo = [c for c in names
                if c not in _counting
                and now - _counted_at.get(c, 0.0) > _COUNT_COOLDOWN]
        _counting.update(todo)
    if not todo:
        return

    def run():
        try:
            for c in todo:
                try:
                    refresh([c])        # 内部会现场 fingerprint 这一家，并写回缓存
                except Exception:
                    pass
                finally:
                    with _counting_lock:
                        _counting.discard(c)
                        _counted_at[c] = time.time()
        finally:                        # 兜底：异常路径也不能把名字永久留在集合里，
            with _counting_lock:        # 否则这家公司再也不会被清点
                _counting.difference_update(todo)
                stamp = time.time()
                for c in todo:
                    _counted_at.setdefault(c, stamp)

    threading.Thread(target=run, daemon=True).start()


def refresh_bg(names):
    """后台线程里刷新，别让人工匹配/复核这种交互式接口等在磁盘 walk 上。"""
    names = [n for n in dict.fromkeys(names or []) if n]
    if not names:
        return
    threading.Thread(target=lambda: _safe_refresh(names), daemon=True).start()


def _safe_refresh(names):
    try:
        refresh(names)
    except Exception:
        pass


def drop_rows(names):
    """把这几家公司的行从总览快照里摘掉（按公司彻底移除时用）。

    不能只靠 refresh()：refresh 里"四处都没了就 pop"那条分支要求磁盘上确实什么都不剩，
    而移除操作有可能因为目录被 Windows 句柄占住而漏删一处 —— 那时 refresh 会把这家公司
    原样留在总览上，用户刚删完转头又看见它。这里是显式移除，与磁盘状态无关。
    """
    names = [n for n in dict.fromkeys(names or []) if n]
    if not names:
        return 0
    with _lock:
        snap = load_cache()
        if not snap:
            return 0
        rows = [r for r in snap.get("rows", []) if r["name"] not in set(names)]
        n = len(snap.get("rows", [])) - len(rows)
        if not n:
            return 0
        snap["rows"] = rows
        snap["summary"] = _summarize(rows)
        snap["generated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        snap["partial"] = True
        _write(CACHE_PATH, snap)
        return n


def load_cache():
    return _read(CACHE_PATH, None)


def badge():
    """导航徽标：只读缓存，绝不触发扫描。"""
    c = load_cache()
    if not c:
        return {"pending": 0, "detail": "尚未扫描"}
    s = c["summary"]
    bits = []
    if s.get("unmapped"):
        bits.append(f"{s['unmapped']} 家待映射")
    if s["with_unmatched"]:
        bits.append(f"{s['with_unmatched']} 家有未匹配")
    if s["with_review"]:
        bits.append(f"{s['with_review']} 家待复核")
    if s["clean_stale"]:
        bits.append(f"{s['clean_stale']} 家需重跑清洗")
    if s["no_id"]:
        bits.append(f"{s['no_id']} 家未解析 id")
    return {"pending": s.get("pending", 0), "detail": " · ".join(bits) or "没有待办",
            "generated": c.get("generated", "")}