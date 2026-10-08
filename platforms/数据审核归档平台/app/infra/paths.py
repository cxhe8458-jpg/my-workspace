# -*- coding: utf-8 -*-
"""全平台唯一的路径出口。

两条铁律：
1. **产品数据在 /mnt/d**（9p 挂载 NTFS，每次系统调用比原生盘慢约 110 倍）——
   只读它、按需改它，绝不在请求路径里遍历它。
2. **审核状态在 WSL 原生盘**——上一代平台把状态放 /mnt/d，os.replace() 间歇性
   失败导致检验标记静默消失（实测丢过数据）。原生 ext4 上 rename 才是真原子。
"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---- 状态：WSL 原生盘 ----
STATE_DIR = Path.home() / ".local/share/审核归档平台"
RECORDS_DIR = STATE_DIR / "records"          # 每公司一个 JSON
BACKUP_DIR = STATE_DIR / "backups"           # 改动产品文件前的备份
LEDGER_FILE = STATE_DIR / "归档台账.json"
CONFIG_FILE = STATE_DIR / "config.json"
ERROR_LOG = STATE_DIR / "errors.log"
# 产品索引快照。纯缓存，删了只是下次启动要重扫一遍，不含任何审核状态。
# 冷扫一家 215 产品的公司要 4.7 秒，108 家就是好几分钟——进程被 OOM 杀掉重启后，
# 没有它整个界面会有几分钟全是"后台扫描中"。
INDEX_CACHE = STATE_DIR / "索引缓存.json"
STATIC_DIR = BASE_DIR / "static"

# ---- 可配置项的默认值 ----
DEFAULT_CONFIG = {
    # 沿用上一代平台正在用的那份产品数据，不复制、不搬动
    "data_dir": "/mnt/d/公司数据自动化检验平台/公司数据",
    # 沿用旧交付路径，否则迁移过来的 58 家归档记录会指向空目录
    "archive_dir": "/mnt/d/已审核数据",
    # 监听地址。默认只听本机——内网部署要显式改成 0.0.0.0，别让它悄悄对外开。
    # 改成 0.0.0.0 之前先确认鉴权是开着的（app/infra/auth.py）。
    "host": "127.0.0.1",
    "port": 8010,
}


def ensure_dirs():
    for d in (STATE_DIR, RECORDS_DIR, BACKUP_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_config():
    """配置读得很频繁，但它只有几百字节且在原生盘上，直接读即可。"""
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass  # 配置坏了不能让平台起不来，回落默认值
    return cfg


def data_dir() -> Path:
    return Path(load_config()["data_dir"])


def archive_dir() -> Path:
    return Path(load_config()["archive_dir"])
