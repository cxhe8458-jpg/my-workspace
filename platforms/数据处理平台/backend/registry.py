# -*- coding: utf-8 -*-
"""处理记忆：
- 映射记忆 mapped_companies.json：记录已完成映射的公司（时间/产品数/结果分布），
  扫描输入目录时据此识别 新增 / 已完成 / 有更新（产品数变化），支持只做增量。
- 清洗记忆 cleaned_companies.json：记录已完成 分类+清洗 的公司及其在【映射输出】下的
  指纹（产品数 + 子树最大 mtime）。因为原始输入目录会被用户挪走，"哪些公司要清洗"
  只能从映射输出推导，指纹变了才重做——这是避免全量重复清洗的关键。
  用 mtime 而不只是产品数，是为了兜住"人工复核搬走一个 + 人工匹配加回一个"数量净零的情况。
"""
import os, json, time, threading
from .paths import DATA_DIR
from . import products as P

REG_PATH = os.path.join(DATA_DIR, "mapped_companies.json")
CLEAN_PATH = os.path.join(DATA_DIR, "cleaned_companies.json")
_lock = threading.Lock()


def load():
    if os.path.exists(REG_PATH):
        try:
            return json.load(open(REG_PATH, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def update(company, rec):
    with _lock:
        reg = load()
        rec["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        reg[company] = rec
        tmp = REG_PATH + ".tmp"
        json.dump(reg, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, REG_PATH)


def status_of(companies):
    """companies: [{name, products, mtime?}] → 附加 status(新增/已完成/有更新) 与 last_time。

    ⚠ **只比产品数量是不够的**（2026-08-05 补上 mtime）：往已有产品目录里补一份 html、
    改一次 info.json、换张图、加个 PDF —— 产品数一个不变，这家公司就被判成「已完成」，
    永远不会被重跑，那份 html 也就**永远进不了映射输出和下游三棵树**，而且界面上
    完全看不出来。③ 清洗一直用的是 (产品数 + 子树最大 mtime) 指纹，② 映射却只比数量，
    整整弱一档。现在两边对齐。

    **向后兼容**：2026-08-05 之前写的记录里没有 `in_mtime`，这种情况下退回只比数量 ——
    否则升级那一刻 89 家会全部被判成「有更新」，用户被迫重跑整个库。
    这些老记录会在各自下一次映射时自然补上指纹。

    容差 0.5 秒，与 `_judge_clean` 同口径（吸收文件系统时间戳抖动）。
    """
    reg = load()
    out = []
    for c in companies:
        r = reg.get(c["name"])
        mt = c.get("mtime")
        if not r:
            st = "新增"
        elif r.get("products") != c["products"]:
            st = "有更新"
        elif (mt is not None and r.get("in_mtime") is not None
              and abs(float(r["in_mtime"]) - float(mt)) > 0.5):
            st = "有更新"          # 数量没变但内容动过（补 html / 改 json / 换图…）
        else:
            st = "已完成"
        out.append({**c, "status": st, "last_time": (r or {}).get("time", "")})
    return out


# ---------------- 清洗记忆（分类+清洗 的增量判定） ----------------
def fingerprint(root, company):
    """公司在某棵树下的指纹：(产品数, 子树最大 mtime)。目录不存在返回 (0, 0)。

    ②（输入目录）和 ③（映射输出）现在共用这一个函数，口径必须一致 ——
    两边各写一份迟早会分叉，而分叉的表现是"某一步永远不认为有更新"。
    """
    return fingerprint_dir(os.path.join(root, company))


def fingerprint_dir(croot):
    """同上，但直接给公司目录的完整路径。"""
    if not os.path.isdir(croot):
        return 0, 0.0
    n = 0
    mt = 0.0
    for dp, dns, fns in P.walk(croot):
        try:
            mt = max(mt, os.path.getmtime(dp))
        except OSError:
            pass
        if not P.is_product_dir(dns, fns):
            continue
        n += 1
        for f in fns:
            try:
                mt = max(mt, os.path.getmtime(os.path.join(dp, f)))
            except OSError:
                pass
    return n, round(mt, 3)


def load_clean():
    if os.path.exists(CLEAN_PATH):
        try:
            return json.load(open(CLEAN_PATH, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def clean_products(company):
    """该公司上次清洗时每个产品的指纹 {公司内相对路径: [类别, 文件数, mtime]}。
    没有记录（老版本记忆）返回 {} —— 那会退化成整家重做，安全。"""
    r = load_clean().get(company) or {}
    p = r.get("products_map")
    return p if isinstance(p, dict) else {}


def update_clean(company, products, mtime, rec=None, products_map=None):
    with _lock:
        reg = load_clean()
        reg[company] = {**(rec or {}), "products": products, "mtime": mtime,
                        "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        if products_map is not None:
            reg[company]["products_map"] = products_map
        tmp = CLEAN_PATH + ".tmp"
        json.dump(reg, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, CLEAN_PATH)


def drop_clean(companies):
    """删除若干公司的清洗记忆（下次强制重做）。"""
    with _lock:
        reg = load_clean()
        for c in companies:
            reg.pop(c, None)
        tmp = CLEAN_PATH + ".tmp"
        json.dump(reg, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, CLEAN_PATH)


def _judge_clean(reg, clean_out, company, n, mt):
    """给定指纹判清洗状态。clean_status 与 clean_status_one 共用，口径只有这一处。"""
    r = reg.get(company)
    if not r:
        return "新增"
    if r.get("products") != n or abs(float(r.get("mtime") or 0) - mt) > 0.5:
        return "有更新"
    if clean_out and not _has_output(clean_out, company):
        return "有更新"           # 记忆里有、磁盘上却没有（被手工删过）→ 重做
    return "已完成"


def clean_status(mapping_out, clean_out=""):
    """扫描映射输出下的全部公司，判定每家的清洗状态。
    返回 [{name, products, status(新增/有更新/已完成), last_time}]，按公司名排序。"""
    reg = load_clean()
    out = []
    if not os.path.isdir(mapping_out):
        return out
    for c in sorted(os.listdir(mapping_out)):
        if not os.path.isdir(os.path.join(mapping_out, c)) or c.startswith(("_", ".")):
            continue
        n, mt = fingerprint(mapping_out, c)
        if not n:
            continue
        out.append({"name": c, "products": n, "mtime": mt,
                    "status": _judge_clean(reg, clean_out, c, n, mt),
                    "last_time": (reg.get(c) or {}).get("time", "")})
    return out


def clean_status_one(mapping_out, clean_out, company, reg=None):
    """单家公司的清洗状态。只 walk 这一家，用于任务跑完后就地刷新总览缓存
    ——全量 clean_status 在 /mnt/d 上要 40+ 秒，不能为了更新一行去跑它。

    reg：预先 `load_clean()` 的结果。**批量为多家调用时务必传进来** —— 否则每家都会把
    cleaned_companies.json（实测 4.8 MB / 71 ms）读+解析一遍，点名几百家就是几十秒的
    纯浪费。判定口径与 clean_status() 完全一致（同一个 `_judge_clean`）。
    """
    if not os.path.isdir(os.path.join(mapping_out, company)):
        return None
    n, mt = fingerprint(mapping_out, company)
    if not n:
        return None
    if reg is None:
        reg = load_clean()
    return {"name": company, "products": n, "mtime": mt,
            "status": _judge_clean(reg, clean_out, company, n, mt),
            "last_time": (reg.get(company) or {}).get("time", "")}


def _has_output(clean_out, company):
    """这家公司在清洗输出下**三个标准类别**里有没有产物。

    ⚠ 只认标准类别，不能 listdir 整个 clean_out：旧口径遗留的类别目录（如改名前的
    类别3_有pdf和图片）里躺着一批过期产品，按目录存在与否反推的话，一家只在遗留目录里
    有东西的公司会被判成「已完成」，于是永远不会被重新清洗（旧缺陷 #5）。
    与 audit._canonical_cats / idmap 的 present 过滤是同一套口径。
    """
    if not os.path.isdir(clean_out):
        return False
    from .classify3 import CAT1, CAT2, CAT3
    return any(os.path.isdir(os.path.join(clean_out, cat, company))
               for cat in (CAT1, CAT2, CAT3))
