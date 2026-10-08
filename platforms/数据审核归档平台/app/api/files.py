# -*- coding: utf-8 -*-
"""产品文件读取、原网页策略探测与原网页代理。"""
import re
import time
from urllib.parse import unquote, urljoin, urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from ..infra import scanner

router = APIRouter()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


@router.get("/files/{rel_path:path}")
def serve_file(rel_path: str):
    f = scanner.safe_file(unquote(rel_path))
    if not f:
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(f))


# ---------- 原网页策略探测 ----------
# 官网发 Cross-Origin-Opener-Policy 时，浏览器把弹窗从 opener 切断：平台此后既关不掉它、
# 也换不了它的页面，连 window.open 同名复用都失效（名字只在同一个浏览上下文组内可见），
# 于是每审一个产品就多攒一个关不掉的窗口。前端要提前知道这件事，才能改用同源跳板窗口。
# X-Frame-Options / CSP frame-ancestors 则决定跳板窗口里能不能直连内嵌，不能就走 /api/proxy。
_POLICY_CACHE: dict[str, tuple[float, dict]] = {}
_POLICY_TTL = 600


@router.get("/api/pagecheck")
def pagecheck(url: str):
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL 不合法")
    host = urlsplit(url).netloc
    hit = _POLICY_CACHE.get(host)
    if hit and time.time() - hit[0] < _POLICY_TTL:
        return hit[1]
    info = {"host": host, "ok": False, "coop": False, "embeddable": True, "detail": ""}
    try:
        import requests
        # stream=True：只要响应头，正文不下载
        r = requests.get(url, timeout=12, stream=True, headers={"User-Agent": UA})
        h = {k.lower(): v for k, v in r.headers.items()}
        r.close()
    except Exception as e:
        # 探测不到就按现状（真窗口）走，前端还有运行时兜底：真开一次发现被切断，
        # 会自己把这个域名记成"要走跳板"。
        info["detail"] = f"{type(e).__name__}: {e}"
        return info
    coop = (h.get("cross-origin-opener-policy") or "").split(",")[0].strip().lower()
    xfo = (h.get("x-frame-options") or "").strip().lower()
    fa = ""
    for part in (h.get("content-security-policy") or "").lower().split(";"):
        if part.strip().startswith("frame-ancestors"):
            fa = part.strip()
    info["ok"] = True
    info["coop"] = bool(coop) and coop != "unsafe-none"
    info["embeddable"] = not (xfo in ("deny", "sameorigin") or xfo.startswith("allow-from")
                              or (bool(fa) and "*" not in fa))
    _POLICY_CACHE[host] = (time.time(), info)
    return info


# 代理页里注入：让站内链接继续走代理，否则一点链接就跳回真站，又被 X-Frame-Options 拦成空白
_KEEP_IN_PROXY = """<script>document.addEventListener("click",function(e){
var a=e.target&&e.target.closest&&e.target.closest("a[href]");if(!a)return;
var t=(a.getAttribute("target")||"").toLowerCase();if(t==="_blank")return;
var u;try{u=new URL(a.getAttribute("href"),document.baseURI)}catch(x){return}
if(u.protocol!=="http:"&&u.protocol!=="https:")return;
e.preventDefault();location.href="/api/proxy?url="+encodeURIComponent(u.href);},true);</script>"""

_META_CSP = re.compile(r"<meta[^>]+http-equiv=[\"']?content-security-policy[\"']?[^>]*>", re.I)


@router.get("/api/proxy")
def proxy(url: str):
    """内嵌兜底：部分官网发 X-Frame-Options/CSP 拒绝被 iframe 内嵌，
    由平台代抓后同源重发，并注入 <base> 让相对路径的图片/样式还能加载。"""
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL 不合法")
    try:
        import requests
        r = requests.get(url, timeout=20, headers={"User-Agent": UA})
        r.encoding = r.apparent_encoding or r.encoding
    except Exception as e:
        return PlainTextResponse(f"代理抓取失败: {e}", status_code=502)
    ct = r.headers.get("content-type", "")
    if "text/html" not in ct.lower():
        return PlainTextResponse(f"该地址不是网页（{ct}），无法内嵌展示", status_code=415)
    # 页面自带的 <meta CSP> 会连我们注入的脚本一起禁掉（响应头里的 CSP 因为重发已经没了）
    html = _META_CSP.sub("", r.text)
    inject = f'<base href="{urljoin(r.url, ".")}">' + _KEEP_IN_PROXY
    low = html.lower()
    i = low.find("<head")
    if i >= 0:
        j = html.find(">", i)
        html = html[:j + 1] + inject + html[j + 1:]
    else:
        html = inject + html
    return HTMLResponse(html)
