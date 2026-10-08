# -*- coding: utf-8 -*-
"""局域网部署的访问控制：HTTP Basic Auth。

凭据从环境变量读取：DEPLOY_USER / DEPLOY_PASS。
- 两者都设置时 → 开启认证：所有请求必须携带正确的 Authorization 头，
  否则返回 401 + WWW-Authenticate（浏览器会自动弹登录框，输对一次后同源
  请求自动带凭据，含导出下载链接）。
- 任一未设置时 → 直接放行（保持开发模式可用，此时务必保证监听地址是 127.0.0.1）。

密码比较用 hmac.compare_digest 做常数时间比较，避免时序侧信道。
配合 Windows 防火墙的「内网网段白名单」形成纵深防御：
  防火墙决定"谁能到端口"，这里的认证决定"到了端口后谁能进"。
"""
import os, base64, hmac
from fastapi import Request
from fastapi.responses import Response


def auth_enabled():
    """DEPLOY_USER 与 DEPLOY_PASS 都设置才开启认证。"""
    return bool(os.environ.get("DEPLOY_USER") and os.environ.get("DEPLOY_PASS"))


def _verify(authorization):
    if not authorization or not authorization.startswith("Basic "):
        return False
    try:
        raw = base64.b64decode(authorization[6:]).decode("utf-8", "replace")
    except Exception:
        return False
    user, _, pwd = raw.partition(":")
    return (hmac.compare_digest(user, os.environ["DEPLOY_USER"])
            and hmac.compare_digest(pwd, os.environ["DEPLOY_PASS"]))


def _deny():
    # ⚠ realm 必须是 ASCII：HTTP 头只能 latin-1 编码，中文 realm 会让 Starlette
    #   抛 UnicodeEncodeError（每个请求都 500）。中文提示放 body（UTF-8 编码合法）。
    return Response(
        "需要登录：请输入部署时设置的用户名与密码",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="data-platform", charset="UTF-8"'},
        media_type="text/plain; charset=utf-8",
    )


async def auth_middleware(request: Request, call_next):
    if not auth_enabled():
        return await call_next(request)
    if not _verify(request.headers.get("Authorization")):
        return _deny()
    return await call_next(request)
