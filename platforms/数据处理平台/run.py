# -*- coding: utf-8 -*-
"""启动自动化数据处理平台：python run.py  →  http://<本机>:8688

局域网部署相关（详见 DEPLOY.md）：
- 默认监听 0.0.0.0（所有网卡），局域网内其他机器可通过本机内网 IP 访问。
  开发环境想只本机访问：HOST=127.0.0.1 python run.py
- 端口可用 PORT 环境变量覆盖：PORT=8080 python run.py
- 开启 Basic Auth：设置 DEPLOY_USER / DEPLOY_PASS 环境变量（详见 backend/auth.py）。
  监听非回环地址而未设认证时，启动会打印安全警告。
"""
import os
import uvicorn

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8688"))
    auth = bool(os.environ.get("DEPLOY_USER") and os.environ.get("DEPLOY_PASS"))
    if host != "127.0.0.1" and not auth:
        print(f"⚠ 正在监听 {host}:{port} 且未设置 DEPLOY_USER/DEPLOY_PASS —— "
              "局域网内任何人可访问，建议先配置认证再暴露到内网！")
    print(f"自动化数据处理平台启动中 →  http://{host}:{port}"
          + ("（已开启 Basic Auth）" if auth else "（未开启认证）"))
    # log_level="info"：开启 uvicorn 访问日志（每条请求的时间/路径/状态码），
    # 会写入 start.sh 重定向的 /tmp/platform.log。真实客户端 IP 见 Windows 防火墙日志
    # （pfirewall.log）——走 netsh portproxy 的请求到后端时源 IP 都是 127.0.0.1。
    uvicorn.run("backend.main:app", host=host, port=port, log_level="info")
