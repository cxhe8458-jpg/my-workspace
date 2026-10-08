# -*- coding: utf-8 -*-
"""状态持久化：审核记录 / 归档台账 / 配置 / 备份。

写入策略是这个平台最关键的一段代码，上一代就是在这里丢的数据：
写 tmp → os.replace() 覆盖，replace 失败时既不重试也不报错，
那次标记就凭空消失，只在磁盘上留下一个没人看的 .tmp。

这里的保证：
- 状态文件在 WSL 原生盘（见 paths.py），replace 本身就可靠；
- 即便如此仍然重试，最后退回原地覆写，绝不让一次写入无声无息地失败；
- 任何路径下都不留 .tmp 残留；
- 读到坏文件时隔离而非静默当空——静默当空会让下一次写入把整份记录清零。
"""
import json
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from . import paths

_lock = threading.RLock()


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- 原子写 ----------

def atomic_write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        last = None
        for i in range(4):
            try:
                tmp.replace(path)
                return
            except OSError as e:          # PermissionError 也是 OSError
                last = e
                time.sleep(0.15 * (i + 1))
        # 重命名始终不成：原地覆写。非原子，但调用方已先做过备份，
        # 总好过"写入静默丢失"——那是上一代平台真实发生过的事故。
        path.write_text(text, encoding="utf-8")
        print(f"[warn] 原子替换失败，已改为原地覆写: {path}（{last}）")
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def write_json(path: Path, data):
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        # 存在但读不动 = 半截写入/编码损坏。绝不能直接返回空默认值：
        # 下一次保存会把这份"空"当全量写回去，整个公司的审核记录就没了。
        try:
            bad = path.with_name(f"{path.name}.corrupt-{datetime.now():%Y%m%d_%H%M%S}")
            path.rename(bad)
            print(f"[error] {path.name} 解析失败（{e}），已隔离为 {bad.name}")
        except OSError:
            pass
        return default


# ---------- 审核记录 ----------
# 结构：{产品相对路径: {status, note, html, updated, history: [{time, event}]}}

def _rec_file(company: str) -> Path:
    return paths.RECORDS_DIR / f"{company}.json"


def load_records(company: str) -> dict:
    return read_json(_rec_file(company), {})


def save_record(company: str, rel_path: str, patch: dict) -> dict:
    """把 patch 合并进一条记录；patch 里带 _event 则追加一条历史。"""
    with _lock:
        recs = load_records(company)
        rec = recs.get(rel_path) or {"history": []}
        rec.setdefault("history", [])
        event = patch.pop("_event", None)
        rec.update(patch)
        rec["updated"] = now()
        if event:
            rec["history"].append({"time": rec["updated"], "event": event})
        recs[rel_path] = rec
        write_json(_rec_file(company), recs)
        return rec


def save_records_bulk(company: str, updates: dict, event: str) -> int:
    """一次性写多条（一键通过用）。单次落盘，避免 N 个产品 N 次写文件。"""
    with _lock:
        recs = load_records(company)
        stamp = now()
        for rel_path, patch in updates.items():
            rec = recs.get(rel_path) or {"history": []}
            rec.setdefault("history", [])
            rec.update(patch)
            rec["updated"] = stamp
            rec["history"].append({"time": stamp, "event": event})
            recs[rel_path] = rec
        write_json(_rec_file(company), recs)
        return len(updates)


def delete_record(company: str, rel_path: str):
    with _lock:
        recs = load_records(company)
        rec = recs.pop(rel_path, None)
        if rec is not None:
            write_json(_rec_file(company), recs)
        return rec


def put_record(company: str, rel_path: str, rec: dict):
    """原样写回（撤销删除产品时恢复用，不走合并、不加历史）。"""
    with _lock:
        recs = load_records(company)
        recs[rel_path] = rec
        write_json(_rec_file(company), recs)


def rename_company_records(old: str, new: str):
    f = _rec_file(old)
    if f.exists():
        f.rename(_rec_file(new))


# ---------- 归档台账 ----------

def load_ledger() -> list:
    return read_json(paths.LEDGER_FILE, [])


def add_ledger(entry: dict):
    with _lock:
        led = load_ledger()
        led.insert(0, {**entry, "time": now()})
        write_json(paths.LEDGER_FILE, led)


def remove_ledger(company: str):
    with _lock:
        led = [e for e in load_ledger() if e.get("company") != company]
        write_json(paths.LEDGER_FILE, led)


def ledger_entry(company: str):
    for e in load_ledger():
        if e.get("company") == company:
            return e
    return None


# ---------- 配置 ----------

def load_config() -> dict:
    return paths.load_config()


def save_config(patch: dict) -> dict:
    with _lock:
        cfg = paths.load_config()
        cfg.update(patch)
        write_json(paths.CONFIG_FILE, cfg)
        return cfg


# ---------- 备份 ----------

def backup_file(src: Path, rel_path: str):
    """改动/删除产品文件前先备份到 状态盘/backups/<产品相对路径>/时间戳_文件名。"""
    if not src.exists():
        return None
    dest_dir = paths.BACKUP_DIR / rel_path
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{src.name}"
    n = 1
    while dest.exists():                       # 同一秒内连续操作不互相覆盖
        dest = dest_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{n}_{src.name}"
        n += 1
    shutil.copy2(src, dest)
    return str(dest)


def inside_backup(p: Path) -> bool:
    """恢复接口只允许从备份区取文件，挡住任意路径读取。"""
    try:
        p = p.resolve()
        root = paths.BACKUP_DIR.resolve()
        return p != root and root in p.parents
    except OSError:
        return False


def init():
    paths.ensure_dirs()
    if not paths.CONFIG_FILE.exists():
        write_json(paths.CONFIG_FILE, paths.DEFAULT_CONFIG)
