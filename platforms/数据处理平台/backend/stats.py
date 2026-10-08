# -*- coding: utf-8 -*-
"""产品映射统计分析，两套口径并存（页面上分区标注）：

1. 趋势/构成类 —— 来源 映射记忆(mapped_companies.json) + 历史记录 + 新增分类注册表，
   带周期筛选（本周固定周界 周六→周五 / 自定义近 N 天 / 全部）。函数：compute(days, mode)
2. 类别分布类 —— 实时扫描【映射输出目录树】，反映含人工匹配与 AI 复核修改在内的当前真实
   状态，无周期维度。函数：scan_distribution(mapping_out)
   之所以不用映射记忆算分布：记忆里的 dist 是映射那一刻的快照，人工后续改动不会回写。
"""
import os, json, time, threading
from collections import Counter
from datetime import datetime, timedelta
from .paths import DATA_DIR
from . import registry, history, manual as manualmod
from . import llm as llmmod
from . import products as P


JUNK = {".DS_Store", "Thumbs.db", "desktop.ini"}

# ---------------- 类别分布的服务端缓存 ----------------
# scan_distribution 要全树 walk 一万多个产品（实测 94 秒）。以前它挂在同步接口
# /api/stats/dist 上，用户点了「重新扫描」只要切一下页面，浏览器就把 fetch abort 掉，
# 结果连个落脚的地方都没有 —— 回来只能从零再扫一遍。总览早就改成「缓存 + 后台任务」了，
# 统计页一直漏着。现在同一套：build_dist() 只在后台任务里跑并写缓存，接口只读缓存。
DIST_CACHE = os.path.join(DATA_DIR, "dist_cache.json")
_dist_lock = threading.Lock()


def load_dist_cache():
    if os.path.exists(DIST_CACHE):
        try:
            return json.load(open(DIST_CACHE, encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_dist_cache(d):
    tmp = DIST_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, DIST_CACHE)


# ---------------- 按公司分片的扫描缓存（增量的依据） ----------------
# dist_cache.json 存的是**聚合结果**，全有或全无 —— 它解决"切页面别把结果弄丢"，
# 不解决"别重复扫"。改一家公司也要重走 14,867 个目录、122 秒。
#
# 所以再存一份**分片**：每家公司的 (c1,c2) 计数各存一份，配一个 dirty 集合。
# 重扫时只 walk dirty 里的公司（单家约 1 秒），其余直接用分片，最后重新聚合。
#
# dirty 从哪来：`overview.refresh(names)` 挂在**每一个**会改动数据的出口上
# （②映射 / 人工匹配 / AI复核 / 变体合并 / ③④⑤…），在那里统一打标。
# 有几个步骤（③清洗/④复查/⑤id映射）其实不动映射输出，标了是多扫一家 ≈ 1 秒；
# 而**漏标的代价是统计悄悄算错**。所以宁可多标，不做精细区分。
SHARD_PATH = os.path.join(DATA_DIR, "dist_shards.json")
SHARD_V = 1
_shard_lock = threading.Lock()


def _load_shards():
    if os.path.exists(SHARD_PATH):
        try:
            d = json.load(open(SHARD_PATH, encoding="utf-8"))
            if d.get("_v") == SHARD_V:
                return d
        except Exception:
            pass
    return {"_v": SHARD_V, "root": "", "companies": {}, "dirty": []}


def _save_shards(d):
    tmp = SHARD_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, SHARD_PATH)


def mark_dist_dirty(names):
    """把这几家公司标成「统计需要重扫」。由 overview.refresh 统一调用。"""
    names = [n for n in (names or []) if n]
    if not names:
        return
    with _shard_lock:
        d = _load_shards()
        dirty = set(d.get("dirty") or []) | set(names)
        d["dirty"] = sorted(dirty)
        _save_shards(d)


def dist_pending():
    """还有多少家公司等着重扫（供界面提示「增量重扫 N 家」）。"""
    d = _load_shards()
    return len(d.get("dirty") or [])


def drop_company_shard(company):
    """把一家公司从分布分片与聚合快照里摘掉（按公司彻底移除时用）。

    只清分片不改聚合快照是不够的：`GET /api/stats/dist` 读的是聚合结果，
    那家公司会一直挂在统计页上，直到下一次重扫为止 —— 而用户刚刚才把它删掉，
    转头在统计页上又看见它，只会以为没删干净。所以两边一起改，聚合部分就地重算。
    """
    if not company:
        return False
    hit = False
    with _shard_lock:
        d = _load_shards()
        if (d.get("companies") or {}).pop(company, None) is not None:
            hit = True
        d["dirty"] = [x for x in (d.get("dirty") or []) if x != company]
        _save_shards(d)
    with _dist_lock:
        cache = load_dist_cache()
        if cache and company in (cache.get("by_company") or {}):
            hit = True
            per = cache["by_company"].pop(company)
            cache["companies"] = [c for c in cache.get("companies", []) if c != company]
            cache["total"] = max(0, cache.get("total", 0) - per.get("total", 0))
            # 一级/二级的总量要跟着减，否则饼里少了一块、分母还是老的
            _sub(cache.get("overall_cat1"), {x["name"]: x["n"] for x in per.get("cat1", [])})
            for item in per.get("cat2", []):
                c1, _, c2 = item["name"].partition("/")
                _sub((cache.get("cat2_by_cat1") or {}).get(c1), {c2: item["n"]})
                if not (cache.get("cat2_by_cat1") or {}).get(c1):
                    (cache.get("cat2_by_cat1") or {}).pop(c1, None)
            for s in cache.get("sources", []):
                if s["label"] in (per.get("source") or ""):
                    s["products"] = max(0, s["products"] - per.get("total", 0))
                    s["companies"] = max(0, s["companies"] - 1)
            _save_dist_cache(cache)
    return hit


def _sub(items, minus):
    """items = [{name, n}]，就地减去 minus 里的数量，减到 0 的条目移除。"""
    if not items:
        return
    for it in list(items):
        d = minus.get(it["name"])
        if d:
            it["n"] -= d
            if it["n"] <= 0:
                items.remove(it)


def _pack(per):
    return {f"{c1}\t{c2}": n for (c1, c2), n in per.items()}


def _unpack(d):
    out = {}
    for k, n in (d or {}).items():
        c1, _, c2 = k.partition("\t")
        out[(c1, c2)] = n
    return out


def build_dist(job, cfg, full=False):
    """后台任务：扫描类别分布并写缓存。

    full=False（默认）→ **只重扫标脏的公司和新出现的公司**，其余用分片；
    full=True          → 整棵重走（手工在资源管理器里挪过目录时用这个，平台不知道那种改动）。

    额外来源（待分类/*）不在流水线里、平台无从判断它们变没变，所以每次都全量走 ——
    它俩加起来只有 14 秒，不值得为此再引一套过期逻辑。
    """
    t0 = time.time()
    root = cfg["mapping_out"]
    alias = _company_alias_map(cfg["company_id"]) if cfg.get("company_id") else {}
    extras = extra_paths(cfg.get("extra_sources"))
    with _shard_lock:
        sh = _load_shards()
    if sh.get("root") != root:            # 配置改过映射输出路径 → 分片全部作废
        if sh.get("companies"):
            job.log(f"映射输出路径变了（{sh.get('root') or '空'} → {root}），分片缓存作废，本次全量")
        sh = {"_v": SHARD_V, "root": root, "companies": {}, "dirty": []}
        full = True

    present = company_dirs(root)
    cached = sh.get("companies") or {}
    dirty = set(sh.get("dirty") or [])
    gone = [c for c in cached if c not in present]
    fresh = [c for c in present if c not in cached]
    redo = [c for c in present if c in cached and c in dirty]
    todo = present if full else (fresh + redo)
    # ⚠ **已经不在映射输出里的公司，必须从 dirty 里摘掉。**
    # 它们永远进不了 todo（todo 只从 present 里挑），而收尾时 `cur_dirty = dirty - todo`
    # 又减不掉它们 —— 于是「N 家有改动待重扫」这个提示会**永远挂着**，用户点多少次
    # 「重新扫描」都不消失。实测卡住的正是两家早就被合并掉的同名变体
    # （埃地沃兹…_1、福建福晶…_）：目录早没了，脏标记却清不掉。
    vanished = sorted(c for c in dirty if c not in present)

    job.log(f"映射输出：{len(present)} 家公司 —— "
            + (f"**全量重扫**" if full else
               f"本次重扫 {len(todo)} 家（新出现 {len(fresh)}、有改动 {len(redo)}），"
               f"沿用分片 {len(present) - len(todo)} 家"))
    if gone:
        job.log(f"  已从映射输出消失的 {len(gone)} 家，同步清出缓存：" + "、".join(gone[:5]))
    if vanished:
        job.log(f"  清掉 {len(vanished)} 家**已不存在公司**的过期脏标记（它们永远等不到重扫）："
                + "、".join(vanished[:5]))
    if not todo and not full:
        job.log("  没有公司需要重扫（上次之后没有任何改动）")
    # 进度以「要走的公司数 + 每个额外来源 + 一次汇总」为分母，这样进度条和 ETA 才有意义。
    # 写死成固定几步的话，重扫 95 家和重扫 1 家的进度条长得一模一样。
    #
    # ⚠ **分母是「步数」，不是「公司数」，这件事必须说出来。** 以前进度条写着 1/138、
    # 而旁边的当前项写着「扫描 US Conec Ltd（2/135）」—— 两个数字互相打架，
    # 用户只能理解为"平台多算了 3 家公司"（实测被问过一次）。
    # 现在：开头把 138 的构成打进日志，当前项也一律用同一个分母。
    n_steps = len(todo) + len(extras) + 1
    job.log(f"  本次共 {n_steps} 步 = {len(todo)} 家公司"
            + (f" + {len(extras)} 个额外来源" if extras else "")
            + " + 1 次汇总")
    job.step(0, n_steps)

    # ---- 只走要走的那几家 ----
    for i, comp in enumerate(todo, 1):
        if job.cancelled:
            job.log("已取消，本次不写缓存")
            return None
        job.set_current(f"扫描公司 {comp}（第 {i}/{n_steps} 步 · 公司 {i}/{len(todo)}）")
        cached[comp] = _pack(scan_company(root, comp, True))
        if i % 10 == 0 or i == len(todo):
            job.log(f"  · 已扫 {i}/{len(todo)} 家 · 已用 {int(time.time() - t0)} 秒")
        job.step(i)
    for c in gone:
        cached.pop(c, None)

    main_tree = {c: _unpack(v) for c, v in cached.items() if v}
    trees = [("映射输出", root, main_tree)]

    # ---- 额外来源：每次全量（14 秒，且平台无从判断它们变没变）----
    for k, p in enumerate(extras, 1):
        if job.cancelled:
            job.log("已取消，本次不写缓存")
            return None
        job.set_current(f"扫描额外来源 {_extra_label(p)}（第 {len(todo) + k}/{n_steps} 步）")
        trees.append((_extra_label(p), p, _scan_tree(p, False, alias)))
        job.step(len(todo) + k)

    d = aggregate(trees, root)
    for s in d.get("sources", []):
        job.log(f"  {s['label']}：{s['companies']} 家公司 / {s['products']} 个产品"
                + ("" if s["exists"] else "  ⚠ 目录不存在"))

    job.set_current(f"汇总并写入缓存 …（第 {n_steps}/{n_steps} 步）")
    with _dist_lock:
        _save_dist_cache(d)
    with _shard_lock:
        cur = _load_shards()
        if cur.get("root") == root:
            # 只清掉本次真正重扫过的那些脏标记：扫描期间新标脏的公司要留到下一轮。
            # vanished 也要减掉 —— 那些公司已经不存在，留着就是永远清不掉的幽灵提示。
            cur_dirty = set(cur.get("dirty") or []) - set(todo) - set(vanished)
        else:
            cur_dirty = set()
        _save_shards({"_v": SHARD_V, "root": root, "companies": cached,
                      "dirty": sorted(cur_dirty)})
    job.step(len(todo) + len(extras) + 1)
    job.log(f"✓ 完成：{len(d['companies'])} 家公司 / {d['total']} 个产品，"
            f"耗时 {int(time.time() - t0)} 秒"
            + ("（全量）" if full else f"（增量，只重扫了 {len(todo)} 家）")
            + "。结果已缓存，切页面回来也还在。")
    return {"total": d["total"], "companies": len(d["companies"]),
            "scanned_at": d["scanned_at"], "rescanned": len(todo),
            "reused": len(present) - len(todo), "full": bool(full)}


def _count_leaves(root, require_json=True):
    """root 下的产品叶子数。

    require_json=True  —— 主流水线口径：走 products.py（叶子目录 + 含 json/pdf/图片）。
      2026-07-29 前这里要求必须含 .json，与映射引擎"只认 info.json"是同一个毛病。
    require_json=False —— 额外来源口径：叶子目录含任意实质文件即算一个产品。
      外部来源（如 待分类/pdf）是别的同事按另一套约定整理的，目录里可能是任意文件格式，
      比主口径更宽，所以这个开关还留着。
    """
    n = 0
    for dp, dns, fns in P.walk(root):
        if dns:
            continue
        real = [f for f in fns if f not in JUNK]
        if not real:
            continue
        if require_json and not P.is_product_files(real):
            continue
        n += 1
    return n


def _company_alias_map(company_idjson):
    """{归一化名: 中文名}，用于把外部来源的英文 slug 目录名还原成中文公司名。"""
    out = {}
    try:
        from .idmap import _norm
        data = json.load(open(company_idjson, encoding="utf-8"))
    except Exception:
        return out
    for e in data:
        if not isinstance(e, dict):
            continue
        cn = str(e.get("cnName") or "").strip()
        if not cn:
            continue
        out[_norm(cn)] = cn
        en = str(e.get("name") or "").strip()
        if en:
            out.setdefault(_norm(en), cn)
    return out


def company_dirs(root):
    """root 下的公司目录名（只 listdir，不下钻 —— 很便宜）。"""
    if not os.path.isdir(root):
        return []
    return [c for c in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, c)) and not c.startswith(("_", "."))]


def scan_company(root, comp, require_json=True):
    """**一家公司**的 {(c1,c2): 产品数}。增量扫描的最小单位。

    单家实测约 1 秒（/mnt/d 上每个目录项 ~6.8ms），而整棵映射输出 13,801 个目录要 94 秒。
    """
    per = {}
    croot = os.path.join(root, comp)
    if not os.path.isdir(croot):
        return per
    for c1 in sorted(os.listdir(croot)):
        p1 = os.path.join(croot, c1)
        if not os.path.isdir(p1) or c1.startswith(("_", ".")) or c1 in P.SKIP_DIRS:
            continue
        for c2 in sorted(os.listdir(p1)):
            p2 = os.path.join(p1, c2)
            if not os.path.isdir(p2) or c2.startswith(("_", ".")) or c2 in P.SKIP_DIRS:
                continue
            n = _count_leaves(p2, require_json)
            if n:
                per[(c1, c2)] = per.get((c1, c2), 0) + n
    return per


def _scan_tree(root, require_json, alias):
    """扫 <root>/<公司>/<一级>/<二级>/<产品>，返回 {公司显示名: {(c1,c2): n}}。全量。"""
    from .idmap import _norm
    out = {}
    for comp in company_dirs(root):
        per = scan_company(root, comp, require_json)
        if per:
            out[alias.get(_norm(comp), comp)] = per      # 英文 slug → 中文名
    return out


def scan_distribution(mapping_out, extra_sources=None, company_idjson=""):
    """实时扫描类别分布。

    主来源 = <映射输出>/<公司>/<一级>/<二级>/<产品>/（产品需含 .json）。
    额外来源 = extra_sources 里的每个目录，结构相同但产品叶子不要求含 .json；
    公司目录名若是英文 slug，会经 公司id.json 的 name 字段还原成中文名。
    两者合并计入总量，每家公司带 source 标明出处。
    """
    alias = _company_alias_map(company_idjson) if company_idjson else {}
    trees = [("映射输出", mapping_out, _scan_tree(mapping_out, True, {}))]
    for p in extra_paths(extra_sources):
        trees.append((_extra_label(p), p, _scan_tree(p, False, alias)))
    return aggregate(trees, mapping_out)


def extra_paths(extra_sources):
    return [p.strip() for p in (extra_sources or []) if (p or "").strip()]


def _extra_label(p):
    return "待分类/" + (os.path.basename(p.rstrip("/\\")) or p)


def aggregate(trees, mapping_out):
    """把 [(来源标签, 路径, {公司: {(c1,c2): n}})] 聚合成前端要的那份结构。

    抽出来是因为增量路径也要用它 —— 分片缓存里存的就是各来源的 tree，
    聚合本身是纯内存计算（毫秒级），每次重算即可，不必也缓存。
    """
    n1, n2 = taxonomy_size()
    out = {"total": 0, "companies": [], "overall_cat1": [], "cat2_by_cat1": {},
           "by_company": {}, "mapping_out": mapping_out,
           "sources": [], "extra_total": 0,
           # 分母跟着数据一起给前端：KPI 上光写"覆盖 124 个二级分类"没有意义，
           # 得写 124 / 160 才知道是多是少。
           "taxonomy": {"cat1": n1, "cat2": n2},
           "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    cat1_all, cat2_all = Counter(), Counter()
    for label, path, tree in trees:
        n_src = sum(sum(per.values()) for per in tree.values())
        out["sources"].append({"label": label, "path": path,
                               "companies": len(tree), "products": n_src,
                               "exists": os.path.isdir(path)})
        if label != "映射输出":
            out["extra_total"] += n_src
        for comp, per in tree.items():
            c1c, c2c = Counter(), Counter()
            for (c1, c2), n in per.items():
                c1c[c1] += n
                c2c[f"{c1}/{c2}"] += n
                cat1_all[c1] += n
                cat2_all[f"{c1}/{c2}"] += n
            tot = sum(c1c.values())
            if not tot:
                continue
            # 同名公司出现在多个来源时合并计数
            if comp in out["by_company"]:
                e = out["by_company"][comp]
                old1 = Counter({x["name"]: x["n"] for x in e["cat1"]})
                old2 = Counter({x["name"]: x["n"] for x in e["cat2"]})
                old1.update(c1c)
                old2.update(c2c)
                e["total"] += tot
                e["cat1"] = [{"name": k, "n": v} for k, v in old1.most_common()]
                e["cat2"] = [{"name": k, "n": v} for k, v in old2.most_common()]
                if label not in e["source"]:
                    e["source"] += "+" + label
            else:
                out["companies"].append(comp)
                out["by_company"][comp] = {
                    "total": tot, "source": label,
                    "cat1": [{"name": k, "n": v} for k, v in c1c.most_common()],
                    "cat2": [{"name": k, "n": v} for k, v in c2c.most_common()],
                }
            out["total"] += tot

    out["companies"].sort(key=lambda c: -out["by_company"][c]["total"])
    out["overall_cat1"] = [{"name": k, "n": v} for k, v in cat1_all.most_common()]
    by1 = {}
    for key, v in cat2_all.items():
        c1, _, c2 = key.partition("/")
        by1.setdefault(c1, []).append({"name": c2, "n": v})
    for c1 in by1:
        by1[c1].sort(key=lambda x: -x["n"])
    out["cat2_by_cat1"] = by1
    return out


def week_bounds(now=None):
    """本周期的固定周界：上周六 00:00:00 → 本周五 23:59:59。

    周五是收尾统计日，算本周的**最后一天**，所以一周从周六起算。
    这样每周五跑出来的数就是不重叠、不滑动的一整段，隔天再看也不会变。
    """
    now = now or datetime.now()
    back = (now.weekday() - 5) % 7          # Mon=0 … Sat=5 … Sun=6
    start = (now - timedelta(days=back)).replace(hour=0, minute=0,
                                                 second=0, microsecond=0)
    return start, start + timedelta(days=7) - timedelta(seconds=1)


def resolve_range(mode="week", days=7):
    """→ (start, end, label)。start/end 为 None 表示该侧不设界。

    mode="week" 固定周界（默认）；"days" 自定义近 N 天滑动窗口；"all" 全部。
    """
    now = datetime.now()
    mode = (mode or "week").lower()
    if mode == "all" or (mode == "days" and not days):
        return None, None, "全部"
    if mode == "week":
        s, e = week_bounds(now)
        return s, e, "本周 %s(六) → %s(五)" % (s.strftime("%m-%d"), e.strftime("%m-%d"))
    n = max(1, int(days))
    return now - timedelta(days=n), None, "近 %d 天" % n


def companies_in_range(mode="week", days=7):
    """→ (公司名集合 or None, 周期标签)。None 表示不限周期（mode=all）。

    周期判定走映射记忆里的时间戳，跟统计页 compute() 用的是同一套 resolve_range，
    所以导出的范围和页面上看到的周期数字天然对得上。
    ⚠ 实时扫描（scan_distribution）本身没有时间维度，只能靠这里回来的名单去收窄。
    """
    start, end, label = resolve_range(mode, days)
    if not start and not end:
        return None, label
    reg = registry.load()
    return {k for k, v in reg.items() if _in_range(v.get("time", ""), start, end)}, label


def _in_range(tstr, start, end=None):
    if not start and not end:
        return True
    try:
        t = datetime.strptime(tstr, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return False
    if start and t < start:
        return False
    if end and t > end:
        return False
    return True


def compute(days=7, mode="week"):
    """mode="week" 固定周界（周六→周五）；"days" 近 N 天；"all" 全部。"""
    start, end, label = resolve_range(mode, days)
    reg = registry.load()
    comps = {k: v for k, v in reg.items() if _in_range(v.get("time", ""), start, end)}

    # ---- 总量与公司维度 ----
    per_company = []
    cat1_cnt = Counter()
    cat2_cnt = Counter()
    for name, r in comps.items():
        per_company.append({"company": name, "total": r.get("total", 0),
                            "mapped": r.get("mapped", 0), "ai": r.get("ai", 0),
                            "unmatched": r.get("unmatched", 0), "time": r.get("time", "")})
        for k, n in (r.get("dist") or {}).items():
            c1 = k.split("/")[0]
            cat1_cnt[c1] += n
            cat2_cnt[k] += n
    per_company.sort(key=lambda x: -x["total"])
    total = sum(c["total"] for c in per_company)
    mapped = sum(c["mapped"] for c in per_company)
    ai = sum(c["ai"] for c in per_company)
    unmatched = sum(c["unmatched"] for c in per_company)

    # 未匹配率排行（产品数≥3 才有统计意义）
    un_rank = sorted(
        [{"company": c["company"], "total": c["total"], "unmatched": c["unmatched"],
          "rate": round(c["unmatched"] * 100 / c["total"], 1)}
         for c in per_company if c["total"] >= 3 and c["unmatched"] > 0],
        key=lambda x: (-x["rate"], -x["unmatched"]))[:10]

    # ---- 人工重匹配事件（来自历史记录） ----
    manual_cat = Counter()
    manual_new = 0
    manual_total = 0
    for h in history.list_all():
        if h.get("step") != "manual" or not _in_range(h.get("time", ""), start, end):
            continue
        manual_total += 1
        cat = (h.get("stats") or {}).get("分类", "")
        if cat:
            manual_cat[cat] += 1
        if (h.get("stats") or {}).get("新增分类"):
            manual_new += 1

    # ---- 新增分类注册表 ----
    custom = []
    cu = manualmod._custom()
    for c1, v in cu.items():
        for c2, d2 in v.get("children", {}).items():
            custom.append({"category1": c1, "category2": c2,
                           "new_cat1": v.get("new_cat1", False),
                           "definition": d2, "count": manual_cat.get(f"{c1}/{c2}", 0)})

    return {
        "range_days": days,
        "range_mode": (mode or "week").lower(),
        "range_label": label,
        "range_start": start.strftime("%Y-%m-%d %H:%M:%S") if start else "",
        "range_end": end.strftime("%Y-%m-%d %H:%M:%S") if end else "",
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "companies": len(per_company), "total": total, "mapped": mapped,
        "ai": ai, "unmatched": unmatched,
        "match_rate": round((mapped + ai) * 100 / total, 1) if total else 0,
        "cat1_dist": [{"name": k, "n": n} for k, n in cat1_cnt.most_common()],
        "cat2_top": [{"name": k, "n": n} for k, n in cat2_cnt.most_common(15)],
        "per_company": per_company[:20],
        # 全量公司名（per_company 只给前 20，导出面板要按周期标记全部公司）
        "companies_in_range": [c["company"] for c in per_company],
        "unmatched_rank": un_rank,
        "manual_total": manual_total, "manual_new": manual_new,
        "manual_cat": [{"name": k, "n": n} for k, n in manual_cat.most_common(12)],
        "custom_categories": custom,
    }


ANALYZE_PROMPT = """你是一名资深的光电产品数据治理分析师。下面是本平台在指定统计周期内的
产品分类映射统计数据（JSON）。请基于这些数据做**整体、全面**的分析，用中文输出，
使用 Markdown 小标题分节，内容务必具体、结论有数据支撑，不要泛泛而谈：

## 一、总体概览
产品总量、映射成功率（规则+AI）、AI 研判占比、未匹配规模，给出总体健康度判断。

## 二、类别集中度分析
产品主要集中在哪些一级/二级分类？集中度说明了什么（业务重心、爬取来源特点）？

## 三、公司维度分析
各公司产品数量差异；哪些公司未匹配率高？结合公司名和数据推测可能原因
（如产品命名不规范、纯型号名、分类树未覆盖该公司产品域等）。

## 四、人工重匹配与新增分类洞察
人工重匹配集中在哪些分类？说明现有 mapping 关键词在哪些领域召回不足；
新增分类反映了标准分类树的哪些缺口，是否建议正式纳入标准分类体系。

## 五、改进建议（可执行）
按优先级给出 3-5 条具体建议：例如应为哪些 category2 补充哪些中英文关键词、
哪些新增分类值得转正、哪些公司的数据值得人工重点复核。

统计数据：
"""


def taxonomy_size():
    """标准分类体系规模 (一级数, 二级数)。给 UI 当分母、也喂给模型。"""
    from .report import _taxonomy_size
    return _taxonomy_size()


def _empty_cat1(dist):
    """标准体系里完全没有产品的一级分类。同样由代码算好，不让模型自己推断。"""
    try:
        from .paths import CONFIG_DIR
        cats = json.load(open(os.path.join(CONFIG_DIR, "company_categories.json"),
                              encoding="utf-8"))["categories"]
    except Exception:
        return []
    has = {x["name"] for x in dist.get("overall_cat1", [])}
    return [c for c in cats if c not in has]


def analyze(model_cfg, days=7, mapping_out="", extra_sources=None, company_idjson="",
            mode="week"):
    data = compute(days, mode)
    payload = dict(data)
    # ⚠ 分母必须由代码算好喂进去。不给的话模型会自己编一个来算覆盖率——
    # 实测编成过"162 个子类"，真实是 160。导出 Excel 那条路早就这么做了（report._payload_for_ai），
    # 页面上的 AI 分析却一直漏着，同一个模型同一类提问，一边不会编、一边会编。
    n1, n2 = taxonomy_size()
    payload["标准分类体系规模"] = {"一级分类总数": n1, "二级分类总数": n2,
                                   "说明": "覆盖率一律以这两个数为分母，不得自行推断"}
    if mapping_out:
        d = scan_distribution(mapping_out, extra_sources, company_idjson)
        payload["实时类别分布_全量"] = {
            "产品总数": d["total"], "公司数": len(d["companies"]),
            "一级分类分布": d["overall_cat1"],
            "各一级下的二级分布Top10": {k: v[:10] for k, v in d["cat2_by_cat1"].items()},
            "各公司产品总数": {c: d["by_company"][c]["total"] for c in d["companies"]},
        }
        covered1 = len(d["overall_cat1"])
        covered2 = sum(len(v) for v in d["cat2_by_cat1"].values())
        payload["分类覆盖率"] = {
            "已覆盖一级": covered1, "一级总数": n1,
            "已覆盖二级": covered2, "二级总数": n2,
            "完全没有产品的一级分类": _empty_cat1(d),
        }
    text, err = llmmod.chat(model_cfg,
                            ANALYZE_PROMPT + json.dumps(payload, ensure_ascii=False, indent=1))
    return {"stats": data, "analysis": text, "error": err}
