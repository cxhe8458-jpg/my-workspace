# -*- coding: utf-8 -*-
"""④ id 映射：把清洗后的中文目录树转成 id 目录树，作为最终交付物。

    输入  <清洗后>/<类别X>/<公司中文名>/<一级分类>/<二级分类>/<产品名>/<产品名>.json|.pdf
    输出  <最终数据>/<类别X>/<公司id>/<分类id>/<产品名>/<产品名>.json|.pdf

两处替换 + 一处压层：
  · 公司中文名 → 公司id      （公司产品数据_id表/公司id.json）
  · 一级/二级两层 → 单层 分类id：**优先用二级分类的 id**；二级在 id 表里查不到时，
    退回用一级分类的 id（用户确认的规则）。
  · 产品名目录层保留不动。

id 表格式（两张表同构，与既有 公司id.json 一致）：[{"id": "...", "cnName": "..."}]
额外字段（_level/_parent 等）一律忽略，方便用模板文件直接填。

零丢失原则（沿用既有 ID 替换）：查不到 id 的公司/分类**按原中文名输出**，绝不丢弃或跳过，
并在结果里单列出来供补录。同名冲突加消歧后缀并单列。
"""
import os, re, json, time, shutil, unicodedata, threading
from . import history
from . import products as P
from .paths import DATA_DIR
from .classify3 import CAT1, CAT2, CAT3

CATS = (CAT1, CAT2, CAT3)

# 别名表：目录名 → id。用于 id 表里查不到、或目录名与表中名字对不上又不宜改动外部表的情况
# （例："Guangdong Tumtec Communication Technology Co Ltd" 是 广东拓姆泰克 的完整英文名，
#  而表里 name 字段存的是品牌简称 GdTumtec）。别名优先级最高，存平台自己的 data/ 下，
#  不污染用户维护的外部 id 表。
ALIAS_PATH = os.path.join(DATA_DIR, "id_aliases.json")
_alias_lock = threading.Lock()


def load_aliases():
    if os.path.exists(ALIAS_PATH):
        try:
            d = json.load(open(ALIAS_PATH, encoding="utf-8"))
            return {"company": d.get("company", {}), "category": d.get("category", {})}
        except Exception:
            pass
    return {"company": {}, "category": {}}


def set_alias(kind, name, target_id):
    """kind = company | category。target_id 为空则删除该别名。"""
    if kind not in ("company", "category"):
        raise ValueError("别名类型只能是 company 或 category")
    name = (name or "").strip()
    if not name:
        raise ValueError("目录名不能为空")
    with _alias_lock:
        d = load_aliases()
        tid = (target_id or "").strip()
        if tid:
            d[kind][name] = tid
        else:
            d[kind].pop(name, None)
        tmp = ALIAS_PATH + ".tmp"
        json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, ALIAS_PATH)
        return d


# id 映射记忆：{目录名: {cid, products, time}}。
# 必须按【源目录名】记账，不能靠"输出目录 <cid> 存不存在"反推——同名变体（公司名 / 公司名_1）
# 解析出同一个 cid，反推会让变体白蹭本体的状态、永远显示"已映射"，
# 于是它永远不会被勾选重跑，产品悄悄缺席交付。这正是丢那 16 个产品时没人发现的原因。
IDREG_PATH = os.path.join(DATA_DIR, "idmapped_companies.json")
_idreg_lock = threading.Lock()


def load_idreg():
    if os.path.exists(IDREG_PATH):
        try:
            return json.load(open(IDREG_PATH, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def update_idreg(recs):
    """recs: {目录名: {cid, products}}。"""
    if not recs:
        return
    with _idreg_lock:
        reg = load_idreg()
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for comp, r in recs.items():
            reg[comp] = {**r, "time": now}
        tmp = IDREG_PATH + ".tmp"
        json.dump(reg, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, IDREG_PATH)


def _norm(s):
    """名称归一：NFKC + 去空格 + 全角括号转半角 + 小写。与 audit.py 保持一致。"""
    return unicodedata.normalize("NFKC", s).replace(" ", "").replace("　", "") \
        .replace("（", "(").replace("）", ")").lower()


SUFFIX_RE = re.compile(r"_\d*$")          # 目录名尾部的消歧后缀：_ / _1 / _2 …


def load_id_table(path, label):
    """读 [{"id","cnName","name"}] → {归一化名: id}。中文名与英文名都建索引
    （目录名可能是中文全称，也可能是英文名）。中文名优先，冲突时不覆盖。
    返回 (映射, 条目数)。"""
    if not path or not os.path.isfile(path):
        raise ValueError(f"{label} 不存在: {path}")
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"{label} 解析失败: {e}")
    if not isinstance(data, list):
        raise ValueError(f"{label} 格式应为数组 [{{\"id\":\"…\",\"cnName\":\"…\"}}, …]")
    m = {}
    for e in data:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("id") or "").strip()
        if not eid:
            continue
        cn = str(e.get("cnName") or "").strip()
        if cn:
            m[_norm(cn)] = eid
    for e in data:                         # 英文名第二轮写入，不覆盖已有的中文名键
        if not isinstance(e, dict):
            continue
        eid = str(e.get("id") or "").strip()
        en = str(e.get("name") or "").strip()
        if eid and en:
            m.setdefault(_norm(en), eid)
    return m, len(data)


def lookup(m, name, alias=None):
    """查 id，优先级：别名 → 原名精确 → 去掉尾部消歧后缀（_ / _1）重查。
    后缀是平台自己为区分同名目录加的，不属于公司/分类名本身。
    返回 (id 或 None, 命中方式 ''|'alias'|'strip')。"""
    if not name:
        return None, ""
    if alias:
        hit = alias.get(name) or alias.get(_norm(name))
        if hit:
            return hit, "alias"
    hit = m.get(_norm(name))
    if hit:
        return hit, ""
    stripped = SUFFIX_RE.sub("", name)
    if stripped and stripped != name:
        hit = m.get(_norm(stripped))
        if hit:
            return hit, "strip"
    return None, ""


def _company_products(clean_out, company):
    """单家公司在清洗后目录下的产品数（跨三个类别累加）。"""
    n = 0
    for cat in CATS:
        croot = os.path.join(clean_out, cat, company)
        if os.path.isdir(croot):
            n += sum(1 for dp, dns, fns in P.walk(croot) if not dns and fns)
    return n


def _company_row(comp, n, comp_map, al, out_root, idreg, ambiguous=False):
    """ambiguous=True 表示还有别的源目录也解析到这个 cid（同名变体）。

    已映射的判据分两层：
      1. 有本目录自己的映射记录，且记录里的 cid 与现在解析出的一致、输出目录还在 —— 确凿。
      2. 没有记录时，回退看 out_root/<cid> 存不存在。这对绝大多数公司是准的
         （记忆文件是这次才引入的，老数据没有记录）。
    但 **ambiguous 时绝不走第 2 层** —— 变体和本体共用一个 cid，目录存在只能证明
    "有人写过"，不能证明是这一份写的。以前正是这样让变体一直显示「已映射」，
    它的产品其实从没进过交付。
    """
    cid, how = lookup(comp_map, comp, al["company"])
    rec = idreg.get(comp) or {}
    out_exists = bool(cid) and os.path.isdir(out_root) and \
        any(os.path.isdir(os.path.join(out_root, cat, cid)) for cat in CATS)
    if rec and rec.get("cid") == cid:
        mapped = out_exists
    else:
        mapped = out_exists and not ambiguous and not rec
    return {"name": comp, "products": n, "id": cid or "",
            "resolved": bool(cid),
            "how": {"alias": "别名指定", "strip": "去后缀命中"}.get(how, "直接命中" if cid else "未解析"),
            "mapped": mapped, "ambiguous": ambiguous,
            "mapped_products": rec.get("products", 0) if mapped else 0,
            "mapped_time": rec.get("time", "") if mapped else ""}


def company_overview(clean_out, out_root, company_idjson, product_idjson):
    """清洗后目录下每家公司的 id 解析情况 + 是否已映射，供前端单独执行 id 映射时选择。"""
    comp_map, _ = load_id_table(company_idjson, "公司id.json")
    al = load_aliases()
    idreg = load_idreg()
    seen = {}
    present = [c for c in CATS if os.path.isdir(os.path.join(clean_out, c))]
    for cat in (present or (os.listdir(clean_out) if os.path.isdir(clean_out) else [])):
        cdir = os.path.join(clean_out, cat)
        if not os.path.isdir(cdir):
            continue
        for comp in os.listdir(cdir):
            croot = os.path.join(cdir, comp)
            if not os.path.isdir(croot):
                continue
            n = sum(1 for dp, dns, fns in P.walk(croot) if not dns and fns)
            seen[comp] = seen.get(comp, 0) + n
    # 先算出哪些 cid 被多个源目录共用（同名变体），这些不允许靠目录反推状态
    by_cid = {}
    for c in seen:
        cid = lookup(comp_map, c, al["company"])[0]
        if cid:
            by_cid.setdefault(cid, []).append(c)
    amb = {c for cs in by_cid.values() if len(cs) > 1 for c in cs}
    return [_company_row(c, n, comp_map, al, out_root, idreg, c in amb)
            for c, n in sorted(seen.items())]


def company_row_one(clean_out, out_root, company_idjson, company):
    """单家公司的 id 解析行。只 walk 这一家，供总览增量刷新用。"""
    comp_map, _ = load_id_table(company_idjson, "公司id.json")
    n = _company_products(clean_out, company)
    if not n:
        return None
    al = load_aliases()
    cid = lookup(comp_map, company, al["company"])[0]
    # 同名变体判定只需要列目录名（三次 listdir），不必 walk
    amb = False
    if cid:
        peers = set()
        for cat in CATS:
            d = os.path.join(clean_out, cat)
            if os.path.isdir(d):
                peers |= set(os.listdir(d))
        amb = sum(1 for pn in peers
                  if pn != company and lookup(comp_map, pn, al["company"])[0] == cid) > 0
    return _company_row(company, n, comp_map, al, out_root, load_idreg(), amb)


def category_overview(clean_out, product_idjson):
    """清洗后目录下出现过的 一级/二级 分类各自的 id 解析情况。
    公司 id 一直有这张表，分类 id 却只能等跑完看日志——补上，让「哪个分类没 id」
    在跑之前就看得见。返回按 未解析 → 退回一级 → 直接命中 排序。"""
    cat_map, _ = load_id_table(product_idjson, "产品id.json")
    al = load_aliases()
    seen = {}
    present = [c for c in CATS if os.path.isdir(os.path.join(clean_out, c))]
    for cat in (present or (os.listdir(clean_out) if os.path.isdir(clean_out) else [])):
        cdir = os.path.join(clean_out, cat)
        if not os.path.isdir(cdir):
            continue
        for comp in os.listdir(cdir):
            croot = os.path.join(cdir, comp)
            if not os.path.isdir(croot):
                continue
            for src, rel in _products_of(croot):
                parts = rel.split(os.sep)
                if len(parts) < 2:
                    continue
                key = (parts[0], parts[1] if len(parts) >= 3 else "")
                seen[key] = seen.get(key, 0) + 1
    rows = []
    for (c1, c2), n in seen.items():
        kid, how = lookup(cat_map, c2, al["category"]) if c2 else (None, "")
        via = "二级"
        if not kid:
            kid, how = lookup(cat_map, c1, al["category"])
            via = "退回一级" if kid else "未解析"
        rows.append({"category1": c1, "category2": c2, "products": n,
                     "id": kid or (c2 or c1), "resolved": bool(kid), "via": via,
                     "how": {"alias": "别名指定", "strip": "去后缀命中"}.get(how, "")})
    order = {"未解析": 0, "退回一级": 1, "二级": 2}
    rows.sort(key=lambda r: (order[r["via"]], -r["products"]))
    return rows


def _products_of(croot):
    """公司目录下的产品叶子：无子目录且含文件。返回 [(绝对路径, 公司内相对路径)]。"""
    out = []
    for dp, dns, fns in P.walk(croot):
        if dns or not fns:
            continue
        out.append((dp, os.path.relpath(dp, croot)))
    return sorted(out, key=lambda x: x[1])


def run_idmap(job, clean_out, out_root, company_idjson, product_idjson, apply=True,
              companies=None):
    """companies 非空时只映射这些公司（增量，与分类+清洗保持同样的增量语义）；
    为空则全量。增量下只重建这些公司的输出子树，其他公司原样保留。"""
    if not os.path.isdir(clean_out):
        raise ValueError(f"清洗后目录不存在: {clean_out}")
    comp_map, n_comp = load_id_table(company_idjson, "公司id.json")
    cat_map, n_cat = load_id_table(product_idjson, "产品id.json")
    al = load_aliases()
    job.log(f"id 表：公司 {n_comp} 条（中英文名共 {len(comp_map)} 个可查键），"
            f"产品分类 {n_cat} 条（共 {len(cat_map)} 个可查键）"
            + (f"；别名 公司{len(al['company'])}/分类{len(al['category'])} 条" if (al['company'] or al['category']) else ""))

    # ---- 收集待映射产品 ----
    scope = set(companies or [])
    if scope:
        job.log(f"增量范围：{len(scope)} 家公司")
    tasks = []          # (类别, 公司名, 一级, 二级, 产品名, 源目录)
    # 只认三个标准类别目录：旧口径遗留的类别目录（如改名前的 类别3_有pdf和图片）不参与交付
    present = [c for c in CATS if os.path.isdir(os.path.join(clean_out, c))]
    scan_cats = present or sorted(os.listdir(clean_out))
    for cat in scan_cats:
        cdir = os.path.join(clean_out, cat)
        if not os.path.isdir(cdir):
            continue
        for comp in sorted(os.listdir(cdir)):
            croot = os.path.join(cdir, comp)
            if not os.path.isdir(croot) or (scope and comp not in scope):
                continue
            for src, rel in _products_of(croot):
                parts = rel.split(os.sep)
                if len(parts) < 2:
                    job.log(f"  ⚠ 层级异常已跳过（期望 一级/[二级/]产品）: {cat}/{comp}/{rel}")
                    continue
                c1 = parts[0]
                c2 = parts[1] if len(parts) >= 3 else ""
                prod = parts[-1]
                tasks.append((cat, comp, c1, c2, prod, src))
    if not tasks:
        raise ValueError(f"清洗后目录下未找到任何产品: {clean_out}")
    job.step(0, len(tasks))
    job.log(f"共 {len(tasks)} 个产品待映射 → {out_root}")

    # ---- 公司 id 唯一性闸门 ----
    # 两个源目录解析到同一个公司 id（几乎总是「同名变体」：同一家公司被录成了
    # 公司名 和 公司名_1 两份），会让后者的产品写进前者的目录、并被下一次清场整棵删掉。
    # 实测已经这样丢过 16 个产品，所以这里一律拦下、不猜、不合并。
    cid_of = {}
    for comp in sorted({t[1] for t in tasks}):
        cid_of[comp] = lookup(comp_map, comp, al["company"])[0] or comp
    dupes = {}
    for comp, cid in cid_of.items():
        dupes.setdefault(cid, []).append(comp)
    clash = {cid: cs for cid, cs in dupes.items() if len(cs) > 1}
    if clash:
        for cid, cs in sorted(clash.items()):
            job.log(f"✗ 公司 id 冲突：{ '、'.join(cs) } 都解析成 {cid}")
        raise ValueError(
            "以下目录解析到了同一个公司 id，继续跑会让其中一份的产品被另一份覆盖：\n"
            + "\n".join(f"  {cid} ← " + "、".join(cs) for cid, cs in sorted(clash.items()))
            + "\n\n请先到总览的「同名变体」把它们合并或分别指定 id，再执行 id 映射。")

    # 重做这些公司前，先清掉它们在输出目录下的旧产物（跨全部类别 —— 分类口径变动后
    # 同一公司的产品可能整体换了类别，只清任务里出现的类别会留下陈旧残留）。
    # 注意：cid 必须走 lookup()（别名/去后缀/未解析三种都要覆盖）。早先这里用的是
    # comp_map.get(_norm(comp)) 裸查，与写盘路径口径不一致 —— 别名公司和带后缀目录
    # 的清场全部打空，留下陈旧残留；而对本体公司又会连带删掉变体刚写进去的产品。
    if apply and os.path.isdir(out_root):
        for comp, cid in sorted(cid_of.items()):
            for cat in os.listdir(out_root):
                old = os.path.join(out_root, cat, cid)
                if os.path.isdir(old):
                    shutil.rmtree(old)

    # ---- 逐个解析 id ----
    un_comp, un_cat, fallbacks, collisions = set(), set(), set(), []
    stripped_hits, alias_hits = set(), set()
    written = set()          # 本次写过的 (类别, 公司id)，用于把校验限定在本次范围
    used = {}
    rows = []
    n_done = 0
    for cat, comp, c1, c2, prod, src in tasks:
        if job.cancelled:
            break
        cid, how = lookup(comp_map, comp, al["company"])
        if not cid:
            cid = comp
            un_comp.add(comp)
            job.bump("公司未解析")
        elif how == "strip":
            stripped_hits.add(comp)
            job.bump("公司去后缀命中")
        elif how == "alias":
            alias_hits.add(comp)
            job.bump("公司别名命中")
        else:
            job.bump("公司已解析")

        # 分类：优先二级，退回一级
        kid, _ = lookup(cat_map, c2, al["category"]) if c2 else (None, "")
        if kid:
            job.bump("分类用二级id")
        else:
            kid, _ = lookup(cat_map, c1, al["category"])
            if kid:
                fallbacks.add(f"{c1}/{c2}" if c2 else c1)
                job.bump("分类退回一级id")
            else:
                kid = c2 or c1
                un_cat.add(f"{c1}/{c2}" if c2 else c1)
                job.bump("分类未解析")

        dst = os.path.join(out_root, cat, cid, kid, prod)
        if dst in used:      # 压层后产品同名（多见于两个二级都退回同一个一级id）
            base, k = dst, 2
            while dst in used:
                dst = f"{base}（源_{c2 or c1}）" if k == 2 else f"{base}（源_{c2 or c1}_{k}）"
                k += 1
            collisions.append({"产品": prod, "原分类": f"{c1}/{c2}", "落地": dst})
            job.bump("同名消歧")
        used[dst] = src
        written.add((cat, cid))

        if apply:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        rows.append({"类别": cat, "公司": comp, "公司id": cid,
                     "原分类": f"{c1}/{c2}" if c2 else c1, "分类id": kid, "产品": prod})
        n_done += 1
        job.step(n_done)
        if n_done % 25 == 0:
            job.set_current(f"{comp} / {prod}")

    # ---- 校验：本次产出的产品数 = 本次处理数 ----
    # 只数【本次写过的 类别X/公司id 子树】。两个都不能少：
    #  · 增量跑一家公司时，若统计整个输出根，会拿 742 去比 9 而误报不一致；
    #  · 输出根下还可能有历史遗留内容（旧 ID 替换流程的 公司产品数据_二次分类_清洗后_时间戳/），
    #    那不是本流程的产物，单列提示、不计入校验。
    got = 0
    strays = []
    if apply and os.path.isdir(out_root):
        for cat, cid in sorted(written):
            p = os.path.join(out_root, cat, cid)
            if os.path.isdir(p):
                got += sum(1 for dp, dns, fns in P.walk(p) if not dns and fns)
        for d in sorted(os.listdir(out_root)):
            if d in CATS or not os.path.isdir(os.path.join(out_root, d)):
                continue
            n = sum(1 for dp, dns, fns in P.walk(os.path.join(out_root, d))
                    if not dns and fns)
            if n:
                strays.append({"dir": d, "products": n})
    # html 保全：⑤ 是整目录复制，html 本该一份不少地跟到最终交付物里。
    # 与产品数校验一样，**只统计本次写过的子树** —— 拿全量输出去比本次处理数会误报。
    html_src = html_out = 0
    if apply:
        for _cat, _comp, _c1, _c2, _prod, src in tasks[:n_done]:
            try:
                html_src += len(P.html_files(os.listdir(src)))
            except OSError:
                pass
        for cat, cid in sorted(written):
            p = os.path.join(out_root, cat, cid)
            if os.path.isdir(p):
                html_out += P.count_html(p)
    html_ok = (not apply) or (html_out >= html_src)
    verify_ok = ((not apply) or (got == n_done)) and html_ok
    job.log(f"{'[落地]' if apply else '[预演]'} 处理 {n_done}/{len(tasks)} 个产品"
            + (f"，输出 {got} 个，校验 {'✓通过' if verify_ok else '✗数量不一致!'}" if apply else ""))
    if apply and html_src:
        job.log(f"  html 源文件：源 {html_src} 份 → 交付 {html_out} 份"
                + ("，全部保留 ✓" if html_ok else " ✗少了，最终数据不完整!"))
    for st in strays:
        job.log(f"ℹ 输出根下的历史遗留目录（未计入校验，可自行清理）：{st['dir']}（{st['products']} 个产品）")
    if alias_hits:
        job.log(f"ℹ 靠别名表指定 id 的公司 {len(alias_hits)} 家：" + "、".join(sorted(alias_hits)[:10]))
    if stripped_hits:
        job.log(f"ℹ 去掉目录名尾部消歧后缀（_ / _1）后命中的公司 {len(stripped_hits)} 家："
                + "、".join(sorted(stripped_hits)[:10]))
    if un_comp:
        job.log(f"⚠ 未解析公司 {len(un_comp)} 家（已按原中文名输出、零丢失，请补录 公司id.json）：")
        for c in sorted(un_comp)[:20]:
            job.log(f"    - {c}")
    if un_cat:
        job.log(f"⚠ 未解析分类 {len(un_cat)} 个（已按原中文名输出，请补录 产品id.json）：")
        for c in sorted(un_cat)[:20]:
            job.log(f"    - {c}")
    if fallbacks:
        job.log(f"ℹ 二级分类无 id、已退回用一级 id 的 {len(fallbacks)} 处："
                + "、".join(sorted(fallbacks)[:10]) + ("…" if len(fallbacks) > 10 else ""))
    if collisions:
        job.log(f"⚠ 压层后产品同名 {len(collisions)} 处，已加消歧后缀（详见结果表）")

    result = {"total": len(tasks), "done": n_done, "output": got if apply else 0,
              "strays": strays, "companies": sorted({r["公司"] for r in rows}),
              "applied": bool(apply), "verify_ok": verify_ok, "out_root": out_root,
              "html_src": html_src, "html_out": html_out,
              "cancelled": bool(job.cancelled),
              "unresolved_companies": sorted(un_comp), "unresolved_categories": sorted(un_cat),
              "fallbacks": sorted(fallbacks), "collisions": collisions[:50],
              "rows": rows[:500]}
    # ⚠ **被取消的任务绝不写"已映射"记忆**（旧缺陷 #2）。
    # 清场的 rmtree 在循环之前就执行了，中途取消意味着输出树是残缺的：
    # 记下部分产品数并判为「已映射」，这家公司就不会再被勾选重跑，产品悄悄缺席交付 ——
    # 这正是当年丢 16 个产品那一类失效。audit.run_audit 一直是对的，这里漏了。
    # 取消时什么都不写，下一轮自然会把它当成"未映射"重做一遍。
    if apply and job.cancelled:
        job.log("⚠ 任务已取消：本次不写 id 映射记忆，也不记历史 —— "
                "输出目录里是残缺的一半，这些公司下次会被重新映射。")
    elif apply:
        per_comp = {}
        for r in rows:
            e = per_comp.setdefault(r["公司"], {"cid": r["公司id"], "products": 0})
            e["products"] += 1
        update_idreg(per_comp)
        history.add("idmap", f"id映射 × {n_done} 个产品",
                    sorted({r["公司"] for r in rows})[:100],
                    {"产品": n_done, "未解析公司": len(un_comp),
                     "未解析分类": len(un_cat), "退回一级": len(fallbacks),
                     "html": f"{html_out}/{html_src}"},
                    {"输入": clean_out, "输出": out_root}, verify_ok and not un_comp and not un_cat)
    return result
