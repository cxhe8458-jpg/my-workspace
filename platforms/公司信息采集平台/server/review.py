# -*- coding: utf-8 -*-
"""人工审核：抓取结果 + 核查状态的唯一存放处（落盘 review_state.json）。

为什么必须落盘：抓取结果原本只活在 app.py 的内存 _state 里，刷新页面或重启服务
就全没了。审核几十家公司要花上小时，中途丢掉等于白干——所以审核批次连同
记录本身一起写文件，服务重启后接着审。

状态模型（与「数据审核归档平台」保持一致，减少两套系统的心智负担）：
    未检 → 通过 | 有问题        「有问题」必须写一行说明
公司级状态是人给的结论；字段级只有「已核对 / 存疑」两种标记，是辅助不是结论。

人工修正的原则：**改动留痕、原值永不丢**。record 是会被写进 MySQL 的那份，
original 保存 AI 最初给的值，edits 记下每次改动，随时可还原、可追责。
"""
import json
import os
import threading
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(BASE_DIR, "review_state.json")

UNCHECKED, PASSED, PROBLEM = "未检", "通过", "有问题"
STATUSES = (UNCHECKED, PASSED, PROBLEM)

_lock = threading.RLock()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------
def _jsonable(v):
    """datetime 转字符串后再落盘；MySQL 接收字符串同样能存进 DATETIME 列。"""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def _atomic_write(path, text):
    """写临时文件再改名。改名失败要重试并最终原地覆写——绝不让一次保存无声丢失。

    项目路径在 WSL，但服务跑在 Windows 侧、经 \\\\wsl.localhost 访问，
    os.replace 在这条网络路径上偶发失败；静默失败等于把人刚审完的一批标记丢掉。
    """
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        last = None
        for i in range(4):
            try:
                os.replace(tmp, path)
                return
            except OSError as e:
                last = e
                time.sleep(0.15 * (i + 1))
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print("[warn] 审核状态原子替换失败，已改为原地覆写：%s（%s）" % (path, last))
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _empty():
    return {"version": 1, "run_tag": "", "created": "", "items": []}


def load():
    """读审核状态。文件损坏时隔离留证，绝不当成空白直接返回——
    返回空白会让下一次保存把这份「空」当全量写回去，整批审核记录清零。"""
    if not os.path.exists(STATE_PATH):
        return _empty()
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "items" not in data:
            raise ValueError("结构不对")
        return data
    except Exception as e:
        bad = "%s.corrupt-%s" % (STATE_PATH, datetime.now().strftime("%Y%m%d_%H%M%S"))
        try:
            os.rename(STATE_PATH, bad)
            print("[error] review_state.json 解析失败（%s），已隔离为 %s" % (e, os.path.basename(bad)))
        except OSError:
            pass
        return _empty()


def save(state):
    _atomic_write(STATE_PATH, json.dumps(_jsonable(state), ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# 构建批次
# ---------------------------------------------------------------------------
def _new_review():
    return {"status": UNCHECKED, "note": "", "fields": {}, "edits": {},
            "updated": "", "history": []}


def build_items(mapped, raws, evidences):
    """把一次抓取的结果转成待审条目。key 用域名：同一家公司重抓也认得出来。"""
    items = []
    for i, rec in enumerate(mapped):
        raw = raws[i] if i < len(raws) else {}
        ev = evidences[i] if i < len(evidences) else {}
        key = (ev.get("domain") or raw.get("_domain")
               or (rec.get("website") or "").strip() or "item-%d" % i)
        clean = {k: v for k, v in rec.items() if not k.startswith("_")}
        items.append({
            "key": key,
            "name": rec.get("cnName") or rec.get("name") or key,
            "record": _jsonable(clean),          # 会写进 MySQL 的那份（人工修正改这里）
            "original": _jsonable(clean),        # AI 最初给的值，只读，用于对照与还原
            "raw": _jsonable({k: v for k, v in (raw or {}).items() if not k.startswith("_")}),
            "evidence": ev,
            "review": _new_review(),
        })
    return items


def replace_batch(items, run_tag=""):
    """一次新抓取覆盖上一批。旧批次若还有没审完的，调用方负责提示。"""
    with _lock:
        state = {"version": 1, "run_tag": run_tag, "created": now(), "items": items}
        save(state)
        return state


def merge_batch(items, run_tag=""):
    """把新抓的并进现有批次：同 key 覆盖记录但**保留已有审核状态**（重抓补数据的场景）。"""
    with _lock:
        state = load()
        old = {it["key"]: it for it in state.get("items", [])}
        for it in items:
            prev = old.get(it["key"])
            if prev:
                # 重抓过的公司：数据换新，人已经给过的结论作废（数据都变了，结论不能留）
                it["review"] = _new_review()
                it["review"]["history"] = list(prev.get("review", {}).get("history", []))
                it["review"]["history"].append({"time": now(), "event": "重新抓取，审核状态已重置"})
            old[it["key"]] = it
        state["items"] = list(old.values())
        state["run_tag"] = run_tag or state.get("run_tag", "")
        state["created"] = state.get("created") or now()
        save(state)
        return state


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def counts(state=None):
    st = state or load()
    c = {s: 0 for s in STATUSES}
    for it in st.get("items", []):
        c[it.get("review", {}).get("status") or UNCHECKED] += 1
    c["total"] = len(st.get("items", []))
    return c


def summary_list(state=None):
    """列表视图用的精简结构，不带 record/raw 这些大块头。"""
    st = state or load()
    out = []
    for it in st.get("items", []):
        rv = it.get("review", {})
        ev = it.get("evidence", {})
        out.append({
            "key": it["key"],
            "name": it.get("name") or it["key"],
            "website": (it.get("record") or {}).get("website") or ev.get("site"),
            "status": rv.get("status") or UNCHECKED,
            "note": rv.get("note", ""),
            "edited": len(rv.get("edits") or {}),
            "checked_fields": sum(1 for v in (rv.get("fields") or {}).values() if v == "ok"),
            "flagged_fields": sum(1 for v in (rv.get("fields") or {}).values() if v == "problem"),
            "about_fallback": bool(ev.get("about_fallback")),
            "contact_missing": bool(ev.get("contact_missing")),
            "has_logo": bool(ev.get("logo")),
        })
    return out


def get_item(key, state=None):
    st = state or load()
    for it in st.get("items", []):
        if it["key"] == key:
            return it
    return None


def passed_records(state=None):
    """只取「通过」的记录用于入库——这是审核作为入库闸门的落点。"""
    st = state or load()
    return [it["record"] for it in st.get("items", [])
            if (it.get("review", {}).get("status") or UNCHECKED) == PASSED]


# ---------------------------------------------------------------------------
# 写操作
# ---------------------------------------------------------------------------
class ReviewError(Exception):
    """业务规则不允许的操作，由 API 层翻成 400。"""


def _touch(rv, event):
    rv["updated"] = now()
    rv.setdefault("history", []).append({"time": rv["updated"], "event": event})


def mark(key, status, note=""):
    if status not in (PASSED, PROBLEM, UNCHECKED):
        raise ReviewError("状态只能是 通过 / 有问题 / 未检")
    note = (note or "").strip()
    if status == PROBLEM and not note:
        raise ReviewError("标记「有问题」必须写清问题在哪")
    with _lock:
        state = load()
        it = get_item(key, state)
        if not it:
            raise ReviewError("找不到这条记录")
        rv = it.setdefault("review", _new_review())
        rv["status"] = status
        rv["note"] = note if status == PROBLEM else ""
        _touch(rv, "标记%s" % status + ("：%s" % note if note else ""))
        save(state)
        return it


def set_logo(key, rel):
    """更新（或清空）一家公司的 logo 证据文件路径。rel=None 表示删除 logo。

    logo 不走 DB 的 logoExt（那是留给人工上传后才填的），只改 evidence.logo 指针——
    审核展示、logo 下载、导出打包读的都是它，改这一处全部生效。
    """
    with _lock:
        state = load()
        it = get_item(key, state)
        if not it:
            raise ReviewError("找不到这条记录")
        ev = it.setdefault("evidence", {})
        ev["logo"] = rel
        save(state)
        return it


def mark_field(key, field, action):
    """字段级标记：ok=已核对 / problem=存疑 / clear=取消标记。"""
    if action not in ("ok", "problem", "clear"):
        raise ReviewError("字段标记只能是 ok / problem / clear")
    with _lock:
        state = load()
        it = get_item(key, state)
        if not it:
            raise ReviewError("找不到这条记录")
        rv = it.setdefault("review", _new_review())
        fields = rv.setdefault("fields", {})
        if action == "clear":
            fields.pop(field, None)
        else:
            fields[field] = action
        rv["updated"] = now()
        save(state)
        return it


def edit_field(key, field, value, allowed):
    """人工修正一个字段。原值保存在 original 里，改动记进 edits，可还原。"""
    if field not in allowed:
        raise ReviewError("字段「%s」不允许修改" % field)
    with _lock:
        state = load()
        it = get_item(key, state)
        if not it:
            raise ReviewError("找不到这条记录")
        rec = it.setdefault("record", {})
        before = rec.get(field)
        value = value if value != "" else None
        if before == value:
            return it
        rec[field] = value
        rv = it.setdefault("review", _new_review())
        rv.setdefault("edits", {})[field] = {
            "from": before, "to": value, "time": now(),
        }
        # 改过的字段自动算作已核对：人都动手改了，没道理还标未核对
        rv.setdefault("fields", {})[field] = "ok"
        _touch(rv, "修正 %s：%s → %s" % (field, _short(before), _short(value)))
        it["name"] = rec.get("cnName") or rec.get("name") or it["key"]
        save(state)
        return it


def revert_field(key, field):
    """把某个字段还原成 AI 最初给的值。"""
    with _lock:
        state = load()
        it = get_item(key, state)
        if not it:
            raise ReviewError("找不到这条记录")
        rv = it.setdefault("review", _new_review())
        if field not in (rv.get("edits") or {}):
            raise ReviewError("该字段没有被修改过")
        orig = (it.get("original") or {}).get(field)
        it.setdefault("record", {})[field] = orig
        rv["edits"].pop(field, None)
        _touch(rv, "还原 %s 为抓取原值" % field)
        it["name"] = it["record"].get("cnName") or it["record"].get("name") or it["key"]
        save(state)
        return it


def _short(v, n=40):
    s = "（空）" if v in (None, "") else str(v)
    return s if len(s) <= n else s[:n] + "…"


def clear_all():
    with _lock:
        save(_empty())
