# -*- coding: utf-8 -*-
"""平台路径常量 + 用户输入路径归一化（Windows / WSL 双兼容）。"""
import os, re


def normalize_path(p):
    """把用户输入的路径归一化为当前运行平台可用的绝对路径。
    支持：D:\\自动化数据处理、D:/x、/mnt/d/x、带引号的复制路径、/mnt/D:\\x 这类混写。
    - 在 Linux/WSL 上运行：盘符风格 → /mnt/<盘符小写>/…
    - 在 Windows 上运行：/mnt/<盘符>/… → <盘符大写>:/…
    """
    if not p:
        return p
    p = p.strip().strip('"').strip("'").strip()
    m = re.match(r"^(?:[/\\]mnt[/\\])?([A-Za-z]):[\\/]?(.*)$", p)   # 盘符风格(含 /mnt/D:\x 混写)
    if m:
        drive, rest = m.group(1), m.group(2).replace("\\", "/")
        if os.name == "nt":
            p = f"{drive.upper()}:/{rest}"
        else:
            p = f"/mnt/{drive.lower()}/{rest}"
    else:
        m2 = re.match(r"^[/\\]mnt[/\\]([A-Za-z])([/\\].*)?$", p)     # /mnt/d/x 风格
        if m2 and os.name == "nt":
            drive, rest = m2.group(1), (m2.group(2) or "/").replace("\\", "/")
            p = f"{drive.upper()}:{rest}"
        else:
            p = p.replace("\\", "/")
    p = re.sub(r"(?<!:)/{2,}", "/", p)
    if len(p) > 1:
        p = p.rstrip("/") or "/"
    return p

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(APP_ROOT, "config")      # 双语 mapping + company_categories.json
SKILLS_DIR = os.path.join(APP_ROOT, "skills")      # skill 库 (md)
DATA_DIR = os.path.join(APP_ROOT, "data")          # 平台运行数据 (任务模型配置等)
FRONTEND_DIR = os.path.join(APP_ROOT, "frontend")

os.makedirs(DATA_DIR, exist_ok=True)
