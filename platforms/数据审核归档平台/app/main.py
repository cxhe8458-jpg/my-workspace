# -*- coding: utf-8 -*-
"""应用装配：只做中间件、异常处理、路由注册。业务不写在这里。"""
import traceback
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from .api import archive_api, files, inspect
from .infra import auth, paths, scanner, store

APP_VERSION = "8"   # 与 static/js/api.js 的 FRONT_VERSION 一致

app = FastAPI(title="数据审核归档平台")
store.init()
scanner.start_background_scan()


# ---------- 未捕获异常：说人话 + 留证据 ----------
# 上一代平台所有意外都变成裸 500「Internal Server Error」，界面只显示这七个字，
# 既看不出哪一步失败也无从复现，排查全靠猜。
@app.exception_handler(Exception)
def unhandled(request: Request, exc: Exception):
    try:
        with paths.ERROR_LOG.open("a", encoding="utf-8") as fp:
            fp.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} "
                     f"{request.method} {request.url.path} =====\n")
            # 此处不在 except 块内，format_exc() 只会写出 "NoneType: None"，
            # 必须从异常对象本身取回溯
            fp.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:
        pass
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


# ---------- 禁掉页面与前端资源的缓存 ----------
# 上一代靠 index.html 里的 ?v=N 破缓存，但版本号本身就写在会被缓存的 index.html 里：
# 浏览器一旦缓存住旧 HTML，就永远请求旧 JS，后端已更新而界面还跑旧代码，
# 顶部"请重启服务"的横幅怎么刷新都不消失——而让横幅消失的代码恰恰在加载不到的新 JS 里。
# 单机自用工具缓存没有任何收益，直接全部禁掉。
@app.middleware("http")
async def no_cache(request: Request, call_next):
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp


# ---------- 登录鉴权 ----------
# 内网部署后端口对全公司开放，除登录页外一律先验会话 cookie。
# 注册在 no_cache 之后 = 位于更外层，先于其它中间件执行。
@app.middleware("http")
async def require_login(request: Request, call_next):
    if not auth.needs_login(request.url.path) or auth.check(request.cookies.get(auth.COOKIE)):
        return await call_next(request)
    # 接口返 401 让前端跳登录页；直接敲地址栏的返 302，别让人对着一段 JSON 发愣
    if request.url.path.startswith(("/api/", "/files/")):
        return JSONResponse(status_code=401, content={"detail": "未登录或会话已过期，请重新登录"})
    return RedirectResponse("/login", status_code=302)


class LoginBody(BaseModel):
    user: str = ""
    password: str = ""


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return HTMLResponse((paths.STATIC_DIR / "login.html").read_text(encoding="utf-8"))


@app.post("/api/login")
def do_login(body: LoginBody):
    if not auth.verify(body.user, body.password):
        return JSONResponse(status_code=401, content={"detail": "用户名或口令不对"})
    resp = JSONResponse({"ok": True, "user": body.user.strip()})
    # 内网是 HTTP，不能加 secure，否则 cookie 根本不会被带上
    resp.set_cookie(auth.COOKIE, auth.issue(body.user.strip()),
                    max_age=auth.TTL, httponly=True, samesite="lax")
    return resp


@app.post("/api/logout")
def do_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((paths.STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api/version")
def version():
    cfg = store.load_config()
    return {"version": APP_VERSION, "data_dir": cfg["data_dir"],
            "archive_dir": cfg["archive_dir"], "state_dir": str(paths.STATE_DIR)}


@app.get("/static/{sub:path}")
def static_files(sub: str):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    f = (paths.STATIC_DIR / sub).resolve()
    root = paths.STATIC_DIR.resolve()
    if root not in f.parents or not f.is_file():
        raise HTTPException(404, "静态文件不存在")
    return FileResponse(str(f))


app.include_router(inspect.router)
app.include_router(archive_api.router)
app.include_router(files.router)
