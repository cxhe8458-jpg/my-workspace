# -*- coding: utf-8 -*-
"""历史记录：记录每次处理任务（时间/步骤/公司/统计/路径），可单条/批量删除。
删除只删记录本身，不动磁盘上已输出的数据。"""
import os, json, time, uuid, threading
from .paths import DATA_DIR

HIST_PATH = os.path.join(DATA_DIR, "history.json")
_lock = threading.Lock()


def _load():
    if os.path.exists(HIST_PATH):
        try:
            return json.load(open(HIST_PATH, encoding="utf-8"))
        except Exception:
            return []
    return []


def _save(items):
    tmp = HIST_PATH + ".tmp"
    json.dump(items, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, HIST_PATH)


def add(step, title, companies=None, stats=None, paths=None, ok=True):
    rec = {"id": uuid.uuid4().hex[:10],
           "time": time.strftime("%Y-%m-%d %H:%M:%S"),
           "step": step, "title": title,
           "companies": companies or [], "stats": stats or {},
           "paths": paths or {}, "ok": bool(ok)}
    with _lock:
        items = _load()
        items.insert(0, rec)
        if len(items) > 500:
            items = items[:500]
        _save(items)
    return rec["id"]


def list_all():
    with _lock:
        return _load()


def delete(ids):
    ids = set(ids)
    with _lock:
        items = _load()
        left = [x for x in items if x["id"] not in ids]
        _save(left)
        return len(items) - len(left)
