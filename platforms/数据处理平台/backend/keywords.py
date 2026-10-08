# -*- coding: utf-8 -*-
"""分类逻辑（关键词库）：
- 原始库：config/mapping_*.json（product-flider-standardizer 双语版，只读零改动）
- 自定义库：data/custom_keywords.json（人工添加 / 人工已确认_ai研判 两种标签，可禁用/删除）
映射引擎运行时同时加载两个库；分类逻辑页按 类别/标签 浏览与维护。
"""
import os, json, glob, re, time, uuid, threading
from .paths import CONFIG_DIR, DATA_DIR

CUSTOM_KW_PATH = os.path.join(DATA_DIR, "custom_keywords.json")
_lock = threading.Lock()

TAG_MANUAL = "人工添加"
TAG_AI = "人工已确认_ai研判"
TAG_REMATCH = "人工匹配归档"
APP_FILES = ("mapping_application.json", "mapping_service.json")


def _has_zh(s):
    return bool(re.search(r"[一-鿿]", s or ""))


def load_custom():
    if os.path.exists(CUSTOM_KW_PATH):
        try:
            return json.load(open(CUSTOM_KW_PATH, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(d):
    tmp = CUSTOM_KW_PATH + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, CUSTOM_KW_PATH)


def custom_engine_entries():
    """供映射引擎加载：启用中的自定义关键词 → MAPS 条目。"""
    out = []
    for kid, e in load_custom().items():
        if not e.get("enabled", True):
            continue
        kw = e.get("keyword", "").strip()
        if not kw:
            continue
        out.append({"eng": "" if _has_zh(kw) else kw,
                    "zh": kw if _has_zh(kw) else e.get("zh", ""),
                    "c1": e["category1"], "c2": e["category2"],
                    "conf": e.get("confidence", 0.95)})
    return out


def add_custom(keyword, category1, category2, confidence=0.95, tag=TAG_MANUAL, note="", zh=""):
    keyword = (keyword or "").strip()
    if not keyword:
        raise ValueError("关键词不能为空")
    with _lock:
        d = load_custom()
        for e in d.values():  # 去重：同词同类
            if e["keyword"].lower() == keyword.lower() and \
               e["category1"] == category1 and e["category2"] == category2:
                raise ValueError(f"该关键词已存在于 {category1}/{category2}（标签：{e.get('tag','')}）")
        kid = uuid.uuid4().hex[:8]
        d[kid] = {"keyword": keyword, "zh": zh.strip(), "category1": category1,
                  "category2": category2, "confidence": float(confidence),
                  "tag": tag, "enabled": True, "note": note,
                  "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        _save(d)
        return kid


def add_custom_many(items, tag=TAG_MANUAL):
    """批量归档。items = [{keyword, category1, category2, confidence?, note?, zh?}]

    **一次 load、内存去重、一次 save。** 别用 for 循环调 add_custom() —— 那样每条都要
    读 + 解析 + 全量扫描 + 写回整个 custom_keywords.json。实测这个文件已经 990 KB / 2608 条，
    单条 12 ms 且随库增长呈二次方恶化：AI 复核的「一键确认」127 条要 2~4 秒、
    1000 条要 20 秒以上，期间界面完全静止。批量化之后 1832 条 < 1 秒。

    返回 (新增数, 跳过的重复数, [新增的 id])。重复不算错误，照常跳过。
    """
    with _lock:
        d = load_custom()
        seen = {(e["keyword"].lower(), e["category1"], e["category2"]) for e in d.values()}
        added, dup = [], 0
        for it in items:
            kw = (it.get("keyword") or "").strip()
            c1, c2 = it.get("category1", ""), it.get("category2", "")
            if not kw or not c1 or not c2:
                dup += 1
                continue
            key = (kw.lower(), c1, c2)
            if key in seen:
                dup += 1
                continue
            seen.add(key)
            kid = uuid.uuid4().hex[:8]
            d[kid] = {"keyword": kw, "zh": (it.get("zh") or "").strip(),
                      "category1": c1, "category2": c2,
                      "confidence": float(it.get("confidence", 0.95)),
                      "tag": it.get("tag", tag), "enabled": True,
                      "note": it.get("note", ""),
                      "time": time.strftime("%Y-%m-%d %H:%M:%S")}
            added.append(kid)
        if added:
            _save(d)
        return len(added), dup, added


def set_custom(kid, enabled=None, delete=False):
    with _lock:
        d = load_custom()
        if kid not in d:
            raise ValueError("关键词不存在")
        if delete:
            del d[kid]
        elif enabled is not None:
            d[kid]["enabled"] = bool(enabled)
        _save(d)


def list_keywords(category1="", category2="", tag=""):
    """合并原始+自定义关键词清单，支持按类别/标签筛选。"""
    rows = []
    for f in sorted(glob.glob(os.path.join(CONFIG_DIR, "mapping_*.json"))):
        base = os.path.basename(f)
        if base in APP_FILES:
            continue
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for kw, v in d.items():
            rows.append({"id": "", "keyword": kw, "zh": v.get("zh", ""),
                         "category1": v.get("category1", ""), "category2": v.get("category2", ""),
                         "confidence": v.get("confidence", 1.0),
                         "enabled": v.get("enable", True),
                         "tag": "原始", "source": base, "time": "", "note": ""})
    for kid, e in load_custom().items():
        rows.append({"id": kid, "keyword": e["keyword"], "zh": e.get("zh", ""),
                     "category1": e["category1"], "category2": e["category2"],
                     "confidence": e.get("confidence", 0.95),
                     "enabled": e.get("enabled", True),
                     "tag": e.get("tag", TAG_MANUAL), "source": "自定义库",
                     "time": e.get("time", ""), "note": e.get("note", "")})
    if category1:
        rows = [r for r in rows if r["category1"] == category1]
    if category2:
        rows = [r for r in rows if r["category2"] == category2]
    if tag:
        rows = [r for r in rows if r["tag"] == tag]
    rows.sort(key=lambda r: (r["category1"], r["category2"], r["tag"] != "原始", r["keyword"].lower()))
    return rows
