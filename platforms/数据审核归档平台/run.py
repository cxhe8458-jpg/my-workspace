# -*- coding: utf-8 -*-
"""启动: python3 run.py

监听地址取自状态目录里的 config.json（host / port），环境变量 AUDIT_HOST、
AUDIT_PORT 可临时覆盖。默认 127.0.0.1:8010 —— 只有内网部署才该改成 0.0.0.0，
改之前先确认登录鉴权在用（见 内网部署说明.md）。
"""
import os
import socket

import uvicorn

from app.infra import paths


def _lan_ip() -> str:
    """本机在内网里的地址，只用来把启动提示打全，取不到就算了。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # 不发包，只让内核挑出出口网卡
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "本机内网IP"


if __name__ == "__main__":
    cfg = paths.load_config()
    host = os.environ.get("AUDIT_HOST") or cfg.get("host") or "127.0.0.1"
    port = int(os.environ.get("AUDIT_PORT") or cfg.get("port") or 8010)

    if host in ("0.0.0.0", "::"):
        print(f"⚠ 内网模式：本机之外也能访问 http://{_lan_ip()}:{port}")
        print("  访问需登录；改口令见 内网部署说明.md")
    else:
        print(f"本机模式：http://{host}:{port}")

    uvicorn.run("app.main:app", host=host, port=port, log_level="info")
