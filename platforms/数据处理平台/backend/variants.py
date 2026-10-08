# -*- coding: utf-8 -*-
"""同名变体：同一家公司被录成了 `公司名` 和 `公司名_` / `公司名_1` 两份目录。

后缀是抓取/映射阶段为区分同名目录加的，不是公司名的一部分。到了 ⑤ id 映射，
两个目录会 `lookup()` 到**同一个公司 id**，于是后写的覆盖先写的、下一次清场再把剩下的
整棵删掉 —— 实测已经这样让最终交付少了 16 个产品，而且 `已映射` 状态是按输出目录
反推的，变体会白蹭本体的状态，界面上一直显示"已映射"，没人看得出来。

所以这里做三件事：
1. **找出**所有变体组（基名也在列表里才算，避免把正常带下划线的公司名误判）。
2. **比对**两份数据是否一致——按用户口径：产品目录数 + PDF 数；再加一层产品名集合比对，
   数量相同但产品不同的情况必须看得出来（上海本诺就是这样：各 2 个，产品完全不同）。
3. **合并/取舍**由用户点，代码只给建议：完全一致 → 建议留名称完整的那个（即基名，
   没有后缀的）；不一致 → 建议先合并再删，绝不自动删。

一条硬规则：**绝不自动动数据**。所有删除都要前端明确传 `keep` 和 `drop`。
"""
import os, re, json, shutil, threading

from .paths import DATA_DIR
from .classify3 import CAT1, CAT2, CAT3
from . import products as P

_reg_lock = threading.Lock()
# 四份记忆都以公司目录名为键，删掉目录就必须同步删记录，
# 否则「已完成」的幽灵记录会让保留方的状态判断出错。
REG_FILES = ("mapped_companies.json", "cleaned_companies.json",
             "audited_companies.json", "idmapped_companies.json",
             "shelved_products.json")

CATS = (CAT1, CAT2, CAT3)
SUFFIX_RE = re.compile(r"_\d*$")


def base_name(name):
    """去掉尾部消歧后缀（`_` / `_1`）。没有后缀则原样返回。"""
    return SUFFIX_RE.sub("", name)


def _scan_products(root, company):
    """公司目录下的产品叶子 {相对路径: {files, pdfs, htmls, name}}。
    root 可以是映射输出或清洗后的类别目录。"""
    out = {}
    croot = os.path.join(root, company)
    if not os.path.isdir(croot):
        return out
    for dp, dns, fns in P.walk(croot):
        if dns or not fns:
            continue
        rel = os.path.relpath(dp, croot)
        out[rel] = {
            "name": os.path.basename(dp),
            "files": len(fns),
            "pdfs": sum(1 for f in fns if f.lower().endswith(".pdf")),
            "htmls": len(P.html_files(fns)),
        }
    return out


def _scan_clean(clean_out, company):
    """清洗后目录是 <类别>/<公司>/… 三棵树，合并成一份。"""
    out = {}
    for cat in CATS:
        d = os.path.join(clean_out, cat)
        if os.path.isdir(d):
            for rel, v in _scan_products(d, company).items():
                out[rel] = {**v, "cat": cat}
    return out


def _companies_under(root):
    if not os.path.isdir(root):
        return []
    return [c for c in os.listdir(root)
            if os.path.isdir(os.path.join(root, c)) and not c.startswith((".", "_"))]


def _all_companies(cfg):
    names = set(_companies_under(cfg["mapping_out"])) | set(_companies_under(cfg["unmatched"]))
    for cat in CATS:
        names |= set(_companies_under(os.path.join(cfg["clean_out"], cat)))
    return names


def _compare(a, b):
    """两份产品清单的一致性。返回 (是否一致, 说明, 明细)。

    三个维度：产品名集合 + PDF 数 + html 数。集合那一项不能省 —— 上海本诺两份各 2 个产品、
    数量完全相同但根本不是同一批，只比数量会误判成"一致"，然后一键删掉丢产品。
    """
    an, bn = set(a), set(b)
    a_pdf = sum(v["pdfs"] for v in a.values())
    b_pdf = sum(v["pdfs"] for v in b.values())
    a_html = sum(v.get("htmls", 0) for v in a.values())
    b_html = sum(v.get("htmls", 0) for v in b.values())
    same_count = len(an) == len(bn)
    same_set = an == bn
    same_pdf = a_pdf == b_pdf
    same_html = a_html == b_html
    if same_set and same_pdf and same_html:
        return True, "两份完全一致（产品目录、PDF 数、html 数都相同）", {}
    bits = []
    if not same_count:
        bits.append(f"产品数不同（{len(an)} / {len(bn)}）")
    elif not same_set:
        bits.append(f"产品数相同（各 {len(an)} 个），但产品不是同一批")
    if not same_pdf:
        bits.append(f"PDF 数不同（{a_pdf} / {b_pdf}）")
    if not same_html:
        bits.append(f"html 数不同（{a_html} / {b_html}）")
    return False, "；".join(bits) or "内容有差异", {
        "only_a": sorted(an - bn)[:50], "only_b": sorted(bn - an)[:50],
    }


def detect_input(cfg, counts=None):
    """**输入目录里**的同名变体 —— 在进流程之前就发现。

    为什么要单独有这个：`detect()` 靠比对产品清单（目录数 + PDF 数 + 产品名集合），
    那些数据要等映射跑完才有；对刚丢进输入目录的公司，我们手里只有目录名。
    但目录名恰恰够用 —— 基名相同就足以提示"这两个很可能是同一家"，
    而这时候处理成本最低：原始数据还没铺开成四棵树。

    只报不动：按用户定的规矩，平台**不改名也不删输入目录**，合并由人工在资源管理器里做。
    counts={公司名: 产品数} 可选，有就带上，没有就不数（数一次要全树 walk）。
    """
    from . import mapping
    root = cfg.get("input") or ""
    names = mapping.list_company_dirs(root) or []
    s = set(names)
    groups = {}
    for n in names:
        b = base_name(n)
        if b != n and b in s:
            groups.setdefault(b, set()).add(n)
    counts = counts or {}
    out = []
    for b, vs in sorted(groups.items()):
        members = [b] + sorted(vs)
        out.append({
            "base": b,
            "members": [{"name": m, "products": counts.get(m), "is_base": m == b} for m in members],
            "root": root,
        })
    return out


def detect(cfg):
    """列出所有变体组。每组：基名 + 各成员在三处的产品数 + 一致性判定 + 建议。"""
    names = _all_companies(cfg)
    groups = {}
    for n in names:
        b = base_name(n)
        if b != n and b in names:
            groups.setdefault(b, set()).add(n)
    out = []
    for b, vs in sorted(groups.items()):
        members = [b] + sorted(vs)
        info = []
        maps = {}
        for m in members:
            mp = _scan_products(cfg["mapping_out"], m)
            cl = _scan_clean(cfg["clean_out"], m)
            un = _scan_products(cfg["unmatched"], m)
            maps[m] = mp
            info.append({
                "name": m, "is_base": m == b,
                "mapped": len(mp), "mapped_pdfs": sum(v["pdfs"] for v in mp.values()),
                "mapped_htmls": sum(v.get("htmls", 0) for v in mp.values()),
                "cleaned": len(cl), "cleaned_pdfs": sum(v["pdfs"] for v in cl.values()),
                "cleaned_htmls": sum(v.get("htmls", 0) for v in cl.values()),
                "unmatched": len(un),
            })
        # 一致性按【映射输出】比对：那是"全部已分类产品"的唯一真源
        pairs = []
        consistent = True
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                ok, why, detail = _compare(maps[members[i]], maps[members[j]])
                consistent = consistent and ok
                pairs.append({"a": members[i], "b": members[j], "same": ok,
                              "why": why, **detail})
        total_union = len(set().union(*[set(maps[m]) for m in members])) if members else 0
        out.append({
            "base": b, "members": info, "pairs": pairs,
            "consistent": consistent,
            "union_products": total_union,
            "suggest": b if consistent else "",
            "advice": ("两份内容一致，建议保留名称完整的「%s」，删除带后缀的副本。" % b)
                      if consistent else
                      "两份内容不一致，直接删任何一份都会丢产品。建议先「合并到保留方」再删。",
        })
    return out


def _merge_tree(src_root, dst_root, company_src, company_dst, moved,
                apply=True, present=None):
    """把 src 公司目录下 dst 没有的产品复制过去。已存在的一律不覆盖（保留方优先）。

    present：保留方**已经拥有的产品相对路径集合**。给了就用它判存在，不再只看
    `os.path.exists(dst)`。清洗后目录必须传这个 —— 它是 <类别X>/<公司>/… 三棵树，
    同一个产品在 drop 方可能落在 类别1、在 keep 方落在 类别3，只在同类别内查存在性
    会认为"keep 没有"，于是复制过去造出跨类别重复（旧缺陷 #4）。

    apply=False 时只登记会搬什么、不动盘（预演与落地共用一套判定，避免两份逻辑对不上）。
    """
    src = os.path.join(src_root, company_src)
    if not os.path.isdir(src):
        return
    for dp, dns, fns in P.walk(src):
        if dns or not fns:
            continue
        rel = os.path.relpath(dp, src)
        if present is not None:
            if rel in present:
                continue
        elif os.path.exists(os.path.join(dst_root, company_dst, rel)):
            continue
        moved.append(f"{company_src}/{rel}")
        if present is not None:
            present.add(rel)     # 同一批里别搬进两份同名产品
        if not apply:
            continue
        dst = os.path.join(dst_root, company_dst, rel)
        if os.path.exists(dst):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copytree(dp, dst)


def _clean_rels(clean_out, company):
    """一家公司在清洗后目录下**跨三个类别**的产品相对路径集合。"""
    out = set()
    for cat in CATS:
        out |= set(_scan_products(os.path.join(clean_out, cat), company))
    return out


def resolve(cfg, keep, drop, merge=True, apply=False):
    """把 drop 里的公司合并进 keep 并删除它们。

    merge=True  先把 drop 独有的产品复制进 keep（同名产品不覆盖），再删 drop。
    merge=False 直接删 drop（只有在确认两份完全一致时才该这么用）。
    apply=False 预演：只报告会搬多少、会删什么，不动盘。
    """
    keep = (keep or "").strip()
    drops = [d.strip() for d in (drop or []) if d and d.strip() != keep]
    if not keep or not drops:
        raise ValueError("请指定保留的公司和至少一个要合并/删除的同名变体")
    all_names = _all_companies(cfg)
    if keep not in all_names:
        raise ValueError(f"保留方不存在：{keep}")
    bad = [d for d in drops if base_name(d) != base_name(keep)]
    if bad:
        raise ValueError("只能合并同名变体（基名必须一致）：" + "、".join(bad))

    roots = [("mapping_out", cfg["mapping_out"]), ("unmatched", cfg["unmatched"])]
    moved, removed = [], []
    # 清洗后目录的存在性必须**跨类别**算一次，并在整个合并过程中持续更新 ——
    # 见 _merge_tree 的 present 注释（旧缺陷 #4）。
    clean_present = _clean_rels(cfg["clean_out"], keep) if merge else set()
    for d in drops:
        if merge:
            for _, root in roots:
                _merge_tree(root, root, d, keep, moved, apply=apply)
            for cat in CATS:
                root = os.path.join(cfg["clean_out"], cat)
                _merge_tree(root, root, d, keep, moved, apply=apply,
                            present=clean_present)
        for _, root in roots:
            p = os.path.join(root, d)
            if os.path.isdir(p):
                removed.append(p)
                if apply:
                    shutil.rmtree(p)
        for cat in CATS:
            p = os.path.join(cfg["clean_out"], cat, d)
            if os.path.isdir(p):
                removed.append(p)
                if apply:
                    shutil.rmtree(p)
    dropped_regs = _forget(drops) if apply else []
    return {"keep": keep, "drop": drops, "merged": len(moved),
            "merged_sample": moved[:50], "removed_dirs": removed,
            "forgot": dropped_regs, "applied": bool(apply)}


def _forget(names):
    """把被删掉的变体从四份处理记忆里摘掉。留着的话，保留方的状态判断会被幽灵记录带偏。"""
    out = []
    with _reg_lock:
        for f in REG_FILES:
            p = os.path.join(DATA_DIR, f)
            if not os.path.isfile(p):
                continue
            try:
                d = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            hit = [n for n in names if n in d]
            if not hit:
                continue
            for n in hit:
                d.pop(n, None)
            tmp = p + ".tmp"
            json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, p)
            out.append({"file": f, "removed": hit})
    return out

# 注：曾有一个 _preview_merge()，与 _merge_tree 各写一份"要不要搬"的判定。
# 两份判定迟早会分叉（预演说搬 3 个、落地搬了 5 个，而删除是不可撤销的），
# 现已合并进 _merge_tree 的 apply 开关，预演与落地共用同一条判定路径。
