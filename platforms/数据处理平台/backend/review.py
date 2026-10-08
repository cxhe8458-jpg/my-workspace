# -*- coding: utf-8 -*-
"""AI 研判复核：浏览各公司经 AI 研判归类的产品，逐条【确认】或【修改】。
- 确认 → 研判逻辑以关键词形式归档进分类逻辑库（标签：人工已确认_ai研判），
  后续映射遇到相同关键词直接规则命中；研判记录标记 复核=已确认。
- 修改 → 产品自动转入人工匹配流：从映射输出移至 _产品人工映射（或新增分类目录），
  写入人工映射记录；研判记录标记 复核=已修改（避免下游重复）。
数据源：过程记录/<公司>/研判归类.json（映射时生成，含 依据；此处追加 复核 字段）。
"""
import os, json, time, shutil, threading
from . import keywords as kwlib
from . import manual as manualmod
from . import history
from . import products as P

_lock = threading.Lock()


def _yp_path(process_root, company):
    return os.path.join(process_root, company, "研判归类.json")


def _load_yp(process_root, company):
    p = _yp_path(process_root, company)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_yp(process_root, company, d):
    """原子写（tmp + os.replace）。

    这份文件同时是**断点续跑缓存**（mapping 靠它免掉重复的 LLM 调用）和**复核凭据**
    （无 `复核` 字段 = 待复核）。以前是 `json.dump(open(p,"w"))` —— 进程在写到一半时被
    杀掉就留下一个截断的 json：`_load_yp` 读不出来（回退成 `{}`），整家公司的复核状态
    与研判缓存一起归零，那一批 LLM 调用的钱也白花了。mapping._save_yanpan 一直是原子写的，
    这里三条路径（确认/批量确认/修改）一直漏着。
    """
    p = _yp_path(process_root, company)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def _find_product_dir(mapping_out, company, entry, rel):
    """定位 AI 研判产品在映射输出中的目录：公司/cat1/cat2/<叶子名>（含消歧后缀兜底）。"""
    leaf = rel.split("/")[-1]
    base = os.path.join(mapping_out, company, entry.get("category1", ""), entry.get("category2", ""))
    cand = os.path.join(base, leaf)
    if os.path.isdir(cand):
        return cand
    if os.path.isdir(base):  # 同名叶子消歧后缀（源_xxx）
        for d in os.listdir(base):
            if d == leaf or d.startswith(leaf + "（源_"):
                return os.path.join(base, d)
    return None


def list_companies(process_root):
    """有 AI 研判记录的公司及 待复核/已确认/已修改 计数。"""
    out = []
    if not os.path.isdir(process_root):
        return out
    for c in sorted(os.listdir(process_root)):
        yp = _load_yp(process_root, c)
        items = [(k, v) for k, v in yp.items() if v.get("category2")]  # 只算研判成功的
        if not items:
            continue
        pending = sum(1 for _, v in items if not v.get("复核"))
        confirmed = sum(1 for _, v in items if v.get("复核") == "已确认")
        modified = sum(1 for _, v in items if v.get("复核") == "已修改")
        out.append({"name": c, "pending": pending, "confirmed": confirmed,
                    "modified": modified, "total": len(items)})
    out.sort(key=lambda x: (-x["pending"], x["name"]))   # 待复核最多的排前面
    return out


def company_stat(process_root, company):
    """单家公司的研判计数（没有研判记录返回 None）。供总览增量刷新用。"""
    yp = _load_yp(process_root, company)
    items = [(k, v) for k, v in yp.items() if v.get("category2")]
    if not items:
        return None
    return {"name": company,
            "pending": sum(1 for _, v in items if not v.get("复核")),
            "confirmed": sum(1 for _, v in items if v.get("复核") == "已确认"),
            "modified": sum(1 for _, v in items if v.get("复核") == "已修改"),
            "total": len(items)}


def list_products(process_root, mapping_out, company):
    yp = _load_yp(process_root, company)
    out = []
    for rel, v in yp.items():
        if not v.get("category2"):
            continue
        d = _find_product_dir(mapping_out, company, v, rel)
        url = ""
        name = rel.split("/")[-1]
        if d:
            info = P.load_info(d)       # 无 json 时是 {}，name 保持目录叶子名
            url = info.get("页面URL", "")
            name = info.get("产品名") or name
        out.append({"rel": rel, "name": name, "url": url,
                    "category1": v.get("category1", ""), "category2": v.get("category2", ""),
                    "basis": v.get("依据", ""), "status": v.get("复核", "待复核") or "待复核",
                    "located": bool(d)})
    return sorted(out, key=lambda x: (x["status"] != "待复核", x["rel"]))


def confirm(process_root, company, rel, keyword, confidence=0.95):
    """确认研判正确：关键词归档进分类逻辑库（标签 人工已确认_ai研判）。"""
    with _lock:
        yp = _load_yp(process_root, company)
        if rel not in yp or not yp[rel].get("category2"):
            raise ValueError("研判记录不存在")
        v = yp[rel]
        if v.get("复核"):
            raise ValueError(f"该产品已复核（{v['复核']}）")
        kid = None
        try:
            kid = kwlib.add_custom(keyword, v["category1"], v["category2"],
                                   confidence=confidence, tag=kwlib.TAG_AI,
                                   note=f"AI研判确认归档：{company}/{rel}")
        except ValueError as e:
            if "已存在" not in str(e):   # 重复关键词不算错，照常确认
                raise
        v["复核"] = "已确认"
        v["复核时间"] = time.strftime("%Y-%m-%d %H:%M:%S")
        v["归档关键词"] = keyword
        _save_yp(process_root, company, yp)
    history.add("review", f"AI研判确认 {company}/{rel.split('/')[-1]}", [company],
                {"分类": f"{v['category1']}/{v['category2']}", "关键词": keyword},
                {}, True)
    return {"ok": True, "keyword_id": kid}


def modify(process_root, mapping_out, company, rel, category1, category2,
           is_new=False, def1="", def2=""):
    """修改研判结果：在【映射输出内】把产品移到人工指定的分类下（不再搬去独立的人工映射目录）。
    同时写人工映射记录，使该公司重跑映射时直接沿用这个人工分类。"""
    with _lock:
        yp = _load_yp(process_root, company)
        if rel not in yp or not yp[rel].get("category2"):
            raise ValueError("研判记录不存在")
        v = yp[rel]
        if v.get("复核"):
            raise ValueError(f"该产品已复核（{v['复核']}）")
        src = _find_product_dir(mapping_out, company, v, rel)
        if not src:
            raise ValueError("在映射输出中找不到该产品目录（可能已被移动或重跑覆盖）")

    category1 = (category1 or "").strip()
    category2 = (category2 or "").strip()
    if not category1 or not category2:
        raise ValueError("请先选择 分类级别1 和 分类级别2")
    standard = manualmod._is_standard(category1, category2)
    if standard:
        if is_new:
            raise ValueError("该分类已存在于标准分类体系，无需新增")
    else:
        cu = manualmod._custom()
        cats, _ = manualmod._std()
        if not manualmod._is_custom(category1, category2):
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
            manualmod._save_custom(cu)

    prod = os.path.basename(src)
    # 在映射输出内移动到新分类下
    dst = os.path.join(mapping_out, company, category1, category2, prod)
    if os.path.abspath(dst) == os.path.abspath(src):
        raise ValueError(f"新分类与当前研判分类相同（{category1}/{category2}），无需修改")
    if os.path.exists(dst):
        base = dst; k = 2
        dst = f"{base}（源_AI复核）"
        while os.path.exists(dst):
            dst = f"{base}（源_AI复核_{k}）"; k += 1
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    manualmod._cleanup_empty(os.path.dirname(src), mapping_out)

    # 人工映射记录 + 研判记录标记
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
                "落地": dst, "来源": f"AI研判修改（原研判 {v['category1']}/{v['category2']}）"}
    json.dump(rec, open(rec_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    with _lock:
        yp = _load_yp(process_root, company)
        yp[rel]["复核"] = "已修改"
        yp[rel]["复核时间"] = time.strftime("%Y-%m-%d %H:%M:%S")
        yp[rel]["修改为"] = f"{category1}/{category2}"
        _save_yp(process_root, company, yp)
    history.add("review", f"AI研判修改 {company}/{prod} → {category1}/{category2}", [company],
                {"原研判": f"{v['category1']}/{v['category2']}",
                 "修改为": f"{category1}/{category2}", "新增分类": 1 if not standard else 0},
                {"落地": dst}, True)
    return {"ok": True, "dest": dst, "is_new": not standard}


def confirm_all(process_root, mapping_out, company, rels=None, confidence=0.95):
    """批量确认：默认全部待复核（或指定 rels），归档关键词=产品名。

    关键词走 `kwlib.add_custom_many` **一次性写库**。以前是逐条 `add_custom`，
    每条都读+扫+写回整个 990 KB 的 custom_keywords.json —— 127 条要 2~4 秒、
    1000 条 20 秒以上，而前端在这期间毫无反馈，看起来就是点了没反应。

    加 `_lock`（单条 confirm 一直是加的，这里漏了），并且**先写研判记录再归档关键词**：
    万一关键词库写失败，复核状态也已经落盘，不会白点一遍。
    """
    with _lock:
        yp = _load_yp(process_root, company)
        targets = []
        for rel, v in yp.items():
            if not v.get("category2") or v.get("复核"):
                continue
            if rels is not None and rel not in rels:
                continue
            targets.append(rel)
        if not targets:
            raise ValueError("没有待复核的研判产品")

        items, now = [], time.strftime("%Y-%m-%d %H:%M:%S")
        for rel in targets:
            v = yp[rel]
            # 关键词 = 产品名（json 里的产品名，回退目录叶子名）
            name = rel.split("/")[-1]
            d = _find_product_dir(mapping_out, company, v, rel)
            if d:
                name = P.product_name(d) or name
            items.append({"keyword": name, "category1": v["category1"],
                          "category2": v["category2"], "confidence": confidence,
                          "tag": kwlib.TAG_AI,
                          "note": f"AI研判批量确认：{company}/{rel}"})
            v["复核"] = "已确认"
            v["复核时间"] = now
            v["归档关键词"] = name
        _save_yp(process_root, company, yp)
        n_new, n_dup, _ids = kwlib.add_custom_many(items, tag=kwlib.TAG_AI)

    n_ok = len(targets)
    history.add("review", f"AI研判批量确认 × {n_ok}（{company}）", [company],
                {"确认": n_ok, "归档关键词": n_new, "已存在跳过": n_dup}, {}, True)
    return {"ok": True, "confirmed": n_ok, "archived": n_new, "duplicated": n_dup}
