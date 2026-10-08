# -*- coding: utf-8 -*-
"""人工匹配：浏览 _未匹配 目录中的产品（公司名/产品名/页面URL），人工选定分类后重匹配。
- 结果直接【回写映射输出】 映射输出/公司/cat1/cat2/产品，使映射输出成为全部已分类产品的唯一真源；
  下游"分类+清洗"按公司增量自然覆盖人工结果，不再需要独立的人工映射目录与分类合并链。
- 新增分类（新一级+二级 或 已有一级下新二级）强制填写定义，注册进 data/custom_categories.json，
  绝不写回标准分类树；落地位置与标准分类一致，靠记录区分。
- 学习闭环：关键词归档进分类逻辑库（标签 人工匹配归档）+ 写 过程记录/<公司>/人工映射记录.json，
  后者让公司重跑映射时直接沿用人工归类，不会再次落进 _未匹配 要求重做。
- 成功后从 _未匹配 删除该副本（原始数据另有保留）。
- 分类定义：标准定义 config/category_definitions.json；新增分类定义 data/custom_categories.json
"""
import os, json, time, shutil, threading
from .paths import CONFIG_DIR, DATA_DIR
from . import history
from . import keywords as kwlib
from . import products as P
from . import shelf

CUSTOM_PATH = os.path.join(DATA_DIR, "custom_categories.json")
_lock = threading.Lock()


def _std():
    cats = json.load(open(os.path.join(CONFIG_DIR, "company_categories.json"), encoding="utf-8"))["categories"]
    try:
        defs = json.load(open(os.path.join(CONFIG_DIR, "category_definitions.json"), encoding="utf-8"))
    except Exception:
        defs = {"_cat1": {}}
    return cats, defs


def _custom():
    if os.path.exists(CUSTOM_PATH):
        try:
            return json.load(open(CUSTOM_PATH, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_custom(d):
    tmp = CUSTOM_PATH + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, CUSTOM_PATH)


def categories():
    """合并标准+新增分类树（带定义与新增标记），供人工匹配下拉使用。"""
    cats, defs = _std()
    custom = _custom()
    out = []
    for c1, v in cats.items():
        children = [{"name": c2.strip(), "def": defs.get(c1, {}).get(c2.strip(), ""), "new": False}
                    for c2 in v["children"]]
        cu = custom.get(c1)
        if cu:  # 已有一级下的新增二级
            for c2, d2 in cu.get("children", {}).items():
                children.append({"name": c2, "def": d2, "new": True})
        out.append({"name": c1, "def": defs.get("_cat1", {}).get(c1, ""), "new": False,
                    "children": children})
    for c1, cu in custom.items():
        if c1 in cats:
            continue  # 上面已并入
        out.append({"name": c1, "def": cu.get("definition", ""), "new": True,
                    "children": [{"name": c2, "def": d2, "new": True}
                                 for c2, d2 in cu.get("children", {}).items()]})
    return out


def _is_standard(c1, c2):
    cats, _ = _std()
    return c1 in cats and c2 in [x.strip() for x in cats[c1]["children"]]


def _is_custom(c1, c2):
    cu = _custom()
    return c1 in cu and c2 in cu[c1].get("children", {})


def list_companies(unmatched_root):
    if not os.path.isdir(unmatched_root):
        return []
    out = []
    for c in sorted(os.listdir(unmatched_root)):
        p = os.path.join(unmatched_root, c)
        if not os.path.isdir(p) or c.startswith(("_", ".")):
            continue
        # 已搁置的不计入待办：否则一家公司只要有一个归不了类的产品，
        # 就永远到不了「已走完全程」，终态从一开始就不可达。
        # ⚠ 必须用 alive()（与磁盘实际产品取交集），不能用 count()：陈旧记录会让
        # 这个减法结果偏小，一家还有未匹配产品的公司可能因此整个从列表里消失。
        sh = shelf.alive_count(unmatched_root, c)
        n = P.count_products(p) - sh
        if n > 0:
            out.append({"name": c, "products": n, "shelved": sh})
    # 按待办量降序：41 家公司 800 多个未匹配，按目录名排的话最该先处理的
    # （一家 132 个）会排到第十几位，用户只能肉眼找大头。
    out.sort(key=lambda x: (-x["products"], x["name"]))
    return out


def count_company(unmatched_root, company):
    """单家公司在 _未匹配 下的产品数（0 = 没有待人工匹配的）。供总览增量刷新用。"""
    croot = os.path.join(unmatched_root, company)
    if not os.path.isdir(croot):
        return 0
    return max(0, P.count_products(croot) - shelf.alive_count(unmatched_root, company))


def list_products(unmatched_root, company):
    croot = os.path.join(unmatched_root, company)
    if not os.path.isdir(croot):
        return []
    out = []
    # 判据走 products.py：以前要求必须有 info.json，没有 json 的产品即使落进 _未匹配
    # 也不会出现在人工匹配列表里 —— 用户看不见，也就永远处理不掉。
    sh = shelf.of_company(company)
    for dp, rel in P.iter_products(croot):
        fns = os.listdir(dp)
        info = P.load_info(dp, fns)
        r = rel.replace(os.sep, "/")
        out.append({"rel": r,
                    "name": P.product_name(dp, fns),
                    "url": info.get("页面URL", ""),
                    "no_json": not P.has_json(fns),
                    "shelved": r in sh,
                    "shelf_reason": (sh.get(r) or {}).get("reason", ""),
                    "shelf_time": (sh.get(r) or {}).get("time", "")})
    # 搁置的沉到底部：它们不是待办，只是留个念想
    return sorted(out, key=lambda x: (x["shelved"], x["rel"]))


def _cleanup_empty(path, stop_root):
    """删除空目录，向上直到 stop_root（不含）。"""
    p = path
    while os.path.abspath(p) != os.path.abspath(stop_root):
        try:
            if os.path.isdir(p) and not os.listdir(p):
                os.rmdir(p)
            else:
                break
        except OSError:
            break
        p = os.path.dirname(p)


def rematch(unmatched_root, company, rel, category1, category2,
            mapping_out, process_root,
            is_new=False, def1="", def2="", keyword=""):
    """人工重匹配一个产品：直接回写【映射输出】，使映射输出成为全部已分类产品的唯一真源。
    同时把关键词归档进分类逻辑库（下次映射自动命中），并写人工映射记录（重跑时直接沿用）。
    返回 result_dict 或抛 ValueError。"""
    category1 = (category1 or "").strip()
    category2 = (category2 or "").strip()
    if not category1 or not category2:
        raise ValueError("请先选择 分类级别1 和 分类级别2")
    src = os.path.join(unmatched_root, company, rel.replace("/", os.sep))
    if not os.path.isdir(src):
        raise ValueError(f"未匹配产品不存在（可能已被处理）: {company}/{rel}")

    standard = _is_standard(category1, category2)
    with _lock:
        if standard:
            if is_new:
                raise ValueError("该分类已存在于标准分类体系，请直接用标准分类重匹配，无需新增")
        else:
            # 新增分类：必须已注册或本次携带定义注册
            cu = _custom()
            cats, _ = _std()
            if not _is_custom(category1, category2):
                if not is_new:
                    raise ValueError("该分类不在标准体系中：请勾选【新增分类】并填写分类定义后提交")
                if category1 not in cats and category1 not in cu and not def1.strip():
                    raise ValueError("新增一级分类必须填写该一级分类的定义")
                if not def2.strip():
                    raise ValueError("新增二级分类必须填写该二级分类的定义")
                entry = cu.setdefault(category1, {"definition": def1.strip(),
                                                  "new_cat1": category1 not in cats,
                                                  "children": {}})
                if def1.strip() and not entry.get("definition"):
                    entry["definition"] = def1.strip()
                entry["children"][category2] = def2.strip()
                _save_custom(cu)
    tag = "" if standard else "【新增分类】"

    prod = os.path.basename(src)
    # 回写映射输出：<映射输出>/<公司>/<一级>/<二级>/<产品>
    dst = os.path.join(mapping_out, company, category1, category2, prod)
    if os.path.exists(dst):  # 同名叶子消歧
        base = dst; k = 2
        dst = f"{base}（源_{rel.split('/')[0]}）"
        while os.path.exists(dst):
            dst = f"{base}（源_{rel.split('/')[0]}_{k}）"; k += 1
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copytree(src, dst)

    # 学习闭环：关键词归档进分类逻辑库，下次映射同类产品直接规则命中
    kw = (keyword or "").strip()
    if not kw:
        kw = P.product_name(src)      # 没有 json 就回退目录名
    kid = None
    try:
        kid = kwlib.add_custom(kw, category1, category2, confidence=0.95,
                               tag=kwlib.TAG_REMATCH,
                               note=f"人工匹配归档：{company}/{rel}")
    except ValueError as e:
        if "已存在" not in str(e):    # 重复关键词不算错，照常完成重匹配
            raise

    # 过程记录：人工映射记录.json
    proc_dir = os.path.join(process_root, company)
    os.makedirs(proc_dir, exist_ok=True)
    rec_path = os.path.join(proc_dir, "人工映射记录.json")
    rec = {}
    if os.path.exists(rec_path):
        try:
            rec = json.load(open(rec_path, encoding="utf-8"))
        except Exception:
            rec = {}
    rec[rel] = {"category1": category1, "category2": category2,
                "新增分类": not standard, "时间": time.strftime("%Y-%m-%d %H:%M:%S"),
                "落地": dst, "来源": "人工匹配", "归档关键词": kw}
    if not standard:
        rec[rel]["分类定义"] = {"级别1": def1.strip() or _custom().get(category1, {}).get("definition", ""),
                               "级别2": def2.strip() or _custom().get(category1, {}).get("children", {}).get(category2, "")}
    json.dump(rec, open(rec_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 从 _未匹配 删除该副本（本身是副本，原始数据另有保留），并清理空目录
    shutil.rmtree(src)
    _cleanup_empty(os.path.dirname(src), unmatched_root)
    # 产品已经归好类，它的搁置记录必须一起摘掉。留着的话 list_companies 里
    # 「实际产品数 - 搁置数」会偏小，这家公司可能整个从待办列表里消失。
    shelf.unshelve(company, rel)

    history.add("manual", f"人工匹配 {tag}{company}/{prod}", [company],
                {"分类": f"{category1}/{category2}", "新增分类": 1 if not standard else 0,
                 "归档关键词": kw},
                {"落地": dst}, True)
    return {"ok": True, "dest": dst, "is_new": not standard, "keyword": kw,
            "keyword_id": kid, "category1": category1, "category2": category2}


def add_category(category1, category2, def1="", def2=""):
    """分类详情页：直接新增分类+定义（不绑定产品）。"""
    category1 = (category1 or "").strip()
    category2 = (category2 or "").strip()
    if not category1 or not category2:
        raise ValueError("请填写 分类级别1 和 分类级别2")
    if _is_standard(category1, category2):
        raise ValueError("该分类已存在于标准分类体系")
    if _is_custom(category1, category2):
        raise ValueError("该分类已在新增分类注册表中")
    if not def2.strip():
        raise ValueError("必须填写二级分类定义")
    with _lock:
        cu = _custom()
        cats, _ = _std()
        if category1 not in cats and category1 not in cu and not def1.strip():
            raise ValueError("新增一级分类必须填写该一级分类的定义")
        entry = cu.setdefault(category1, {"definition": def1.strip(),
                                          "new_cat1": category1 not in cats,
                                          "children": {}})
        if def1.strip() and not entry.get("definition"):
            entry["definition"] = def1.strip()
        entry["children"][category2] = def2.strip()
        _save_custom(cu)
    history.add("category", f"新增分类 {category1}/{category2}", [],
                {"新增分类": 1}, {}, True)
    return {"ok": True}
