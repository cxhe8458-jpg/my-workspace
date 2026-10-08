# -*- coding: utf-8 -*-
"""搁置：把「确实归不了类」的未匹配产品标记掉，让它不再计入待办。

为什么需要这个：总览的终态是「已走完全程」，而只要一家公司还有未匹配产品，
它就永远到不了终态。但有些产品是真的归不了类 —— 型号名毫无语义、或者 160 个二级分类
里确实没有对应品类。不给出口的话，「全部走完」这个目标从一开始就不可达，
那么"其他状态都应该是无"这句话也就永远无法验证。

三条约束：
- **产品级**，不是公司级：一家公司里搁置 3 个，其余 77 个照样要处理。
- **可撤销**：搁置只是一条记录，随时能捞回来。
- **不动数据**：产品仍留在 `_未匹配` 目录里原地不动，只是不再计入待办。
  搁置不是删除，将来分类体系扩了、或者你想通了，它还在那儿。

存 data/shelved_products.json：{公司名: {公司内相对路径: {time, reason}}}
"""
import os, json, time, threading

from .paths import DATA_DIR

PATH = os.path.join(DATA_DIR, "shelved_products.json")
_lock = threading.Lock()


def load():
    if os.path.exists(PATH):
        try:
            d = json.load(open(PATH, encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


def _write(d):
    tmp = PATH + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)


def of_company(company):
    """该公司已搁置的 {相对路径: 记录}。"""
    return load().get(company) or {}


def is_shelved(company, rel):
    return rel in of_company(company)


def shelve(company, rel, reason=""):
    if not company or not rel:
        raise ValueError("缺少公司或产品路径")
    with _lock:
        d = load()
        d.setdefault(company, {})[rel] = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "reason": (reason or "").strip(),
        }
        _write(d)
    return {"ok": True, "company": company, "rel": rel}


def unshelve(company, rel):
    with _lock:
        d = load()
        if company in d:
            d[company].pop(rel, None)
            if not d[company]:
                d.pop(company)
            _write(d)
    return {"ok": True}


def count(company):
    """记录条数。**只在你已经确认记录都还有效时才用它** —— 一般请用 alive()。"""
    return len(of_company(company))


def alive(unmatched_root, company):
    """与磁盘上**实际还在** `_未匹配` 里的产品取交集后的搁置集合。

    为什么不能直接用 count()：搁置只是一条记录，产品被重匹配走之后记录会残留
    （rematch 以前不清记录）。而 manual.list_companies 算的是
    `实际产品数 - 搁置数 > 0`，多出来的陈旧记录会让计数偏小 ——
    极端情况下一家还有未匹配产品的公司会**整个从待办列表里消失**，剩下的产品再也点不到。
    """
    rec = of_company(company)
    if not rec:
        return {}
    from . import products as P
    croot = os.path.join(unmatched_root or "", company)
    if not os.path.isdir(croot):
        return {}
    have = {rel.replace(os.sep, "/") for _dp, rel in P.iter_products(croot)}
    return {r: v for r, v in rec.items() if r in have}


def alive_count(unmatched_root, company):
    return len(alive(unmatched_root, company))


def shelve_many(company, rels, reason=""):
    """批量搁置。一次读写，供「搁置选中的 N 个」用。"""
    rels = [r for r in (rels or []) if r]
    if not company or not rels:
        raise ValueError("缺少公司或产品路径")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        d = load()
        e = d.setdefault(company, {})
        n = 0
        for r in rels:
            if r not in e:
                n += 1
            e[r] = {"time": now, "reason": (reason or "").strip()}
        _write(d)
    return {"ok": True, "company": company, "shelved": n, "total": len(rels)}


def unshelve_many(company, rels):
    rels = [r for r in (rels or []) if r]
    if not company or not rels:
        raise ValueError("缺少公司或产品路径")
    with _lock:
        d = load()
        e = d.get(company)
        if not e:
            return {"ok": True, "removed": 0}
        n = sum(1 for r in rels if e.pop(r, None) is not None)
        if not e:
            d.pop(company, None)
        _write(d)
    return {"ok": True, "removed": n}


def forget(companies):
    """整家公司的搁置记录一起摘掉（同名变体合并时用）。"""
    names = [c for c in (companies or []) if c]
    if not names:
        return 0
    with _lock:
        d = load()
        n = sum(len(d.pop(c, {})) for c in names)
        if n:
            _write(d)
    return n


def total():
    d = load()
    return sum(len(v) for v in d.values()), len(d)


def overview(unmatched_root):
    """全部搁置产品，按公司分组，只保留磁盘上还在的（陈旧记录顺手清掉）。

    返回 {companies: [{name, products:[{rel, time, reason}]}], products, company_count, stale}
    """
    d = load()
    out, n, stale = [], 0, 0
    for comp in sorted(d):
        live = alive(unmatched_root, comp)
        stale += len(d[comp]) - len(live)
        if not live:
            continue
        items = [{"rel": r, "time": v.get("time", ""), "reason": v.get("reason", "")}
                 for r, v in sorted(live.items())]
        out.append({"name": comp, "n": len(items), "products": items})
        n += len(items)
    out.sort(key=lambda x: (-x["n"], x["name"]))
    return {"companies": out, "products": n, "company_count": len(out), "stale": stale}


def prune(unmatched_root):
    """清掉指向已不存在产品的陈旧记录。返回清掉的条数。"""
    d = load()
    drop = {}
    for comp in list(d):
        live = alive(unmatched_root, comp)
        gone = [r for r in d[comp] if r not in live]
        if gone:
            drop[comp] = gone
    if not drop:
        return 0
    with _lock:
        d = load()
        n = 0
        for comp, rels in drop.items():
            for r in rels:
                if d.get(comp, {}).pop(r, None) is not None:
                    n += 1
            if comp in d and not d[comp]:
                d.pop(comp)
        _write(d)
    return n
