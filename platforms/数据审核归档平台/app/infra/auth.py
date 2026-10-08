# -*- coding: utf-8 -*-
"""内网部署用的登录鉴权：单账号 + 签名会话 cookie，除登录页外全站拦截。

单机自用时这个平台没有任何鉴权，靠的是「只监听 127.0.0.1，外面根本连不进来」。
一旦绑到 0.0.0.0 供内网访问，这条前提就没了：能连到端口的人可以归档、删产品、
移动 D 盘上的真实数据。所以内网模式必须先过这一道。

这层解决的是「谁能进来」，**不解决「谁改的」**——全公司共用一个账号，
审核历史里只记得下"标记通过"，记不下是谁标的。

口令不落明文：配置里只存 PBKDF2 盐 + 派生值。会话 cookie 用 HMAC 签名，
密钥存在状态目录的 config.json 里，因此重启服务不会把所有人踢下线。
"""
import base64
import hashlib
import hmac
import json
import secrets
import time

from . import store

COOKIE = "audit_session"        # cookie 名走 ASCII，这是协议字段不是界面文案
TTL = 12 * 3600                 # 会话有效期：一个工作日
DEFAULT_USER = "admin"
DEFAULT_PASSWORD = "123456"
PBKDF2_ROUNDS = 200_000

# 不登录就能访问的路径，**只有登录页自己和登录接口**。
# 登录页把样式内联了，所以不必放行 /static —— 否则没登录的人也能把整套前端源码拖走。
OPEN_PATHS = {"/login", "/api/login", "/favicon.ico"}


def _derive(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                               bytes.fromhex(salt), PBKDF2_ROUNDS).hex()


def _config() -> dict:
    """读配置，缺账号/密钥就地补齐。

    懒初始化而不是在 init() 里建：自检脚本会把 config.json 整份覆写掉，
    放在启动时建就会被冲掉，之后所有请求都验不过。
    """
    cfg = store.load_config()
    patch = {}
    if not cfg.get("auth_salt") or not cfg.get("auth_hash"):
        salt = secrets.token_hex(16)
        patch["auth_user"] = cfg.get("auth_user") or DEFAULT_USER
        patch["auth_salt"] = salt
        patch["auth_hash"] = _derive(DEFAULT_PASSWORD, salt)
    if not cfg.get("secret_key"):
        patch["secret_key"] = secrets.token_hex(32)
    if patch:
        cfg = store.save_config(patch)
    return cfg


def verify(user: str, password: str) -> bool:
    cfg = _config()
    # 两个比较都走 compare_digest 且不短路，避免从响应快慢上试出用户名对不对
    ok_user = hmac.compare_digest((user or "").strip(), cfg.get("auth_user", DEFAULT_USER))
    ok_pw = hmac.compare_digest(_derive(password, cfg["auth_salt"]), cfg["auth_hash"])
    return ok_user and ok_pw


def set_password(password: str, user: str = "") -> dict:
    """改口令（命令行用，见 内网部署说明.md）。改完所有旧会话立即失效。"""
    if len(password or "") < 6:
        raise ValueError("口令至少 6 位")
    salt = secrets.token_hex(16)
    patch = {"auth_salt": salt, "auth_hash": _derive(password, salt),
             "secret_key": secrets.token_hex(32)}   # 换密钥＝把所有人踢下线重登
    if user.strip():
        patch["auth_user"] = user.strip()
    return store.save_config(patch)


# ---------- 会话 ----------

def _sign(payload: str) -> str:
    return hmac.new(_config()["secret_key"].encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue(user: str) -> str:
    raw = json.dumps({"u": user, "exp": int(time.time()) + TTL}, ensure_ascii=False)
    payload = base64.urlsafe_b64encode(raw.encode("utf-8")).decode().rstrip("=")
    return f"{payload}.{_sign(payload)}"


def check(token: str):
    """验签 + 查过期，通过则返回用户名，否则 None。"""
    if not token or "." not in token:
        return None
    payload, _, sig = token.rpartition(".")
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:
        return None
    if float(data.get("exp", 0)) < time.time():
        return None
    return data.get("u")


def needs_login(path: str) -> bool:
    return path not in OPEN_PATHS
