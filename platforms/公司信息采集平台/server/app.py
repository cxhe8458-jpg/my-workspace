# -*- coding: utf-8 -*-
"""FastAPI 后端：公司信息采集服务（局域网部署，单人 Basic Auth 密码）。

启动：python -m server.app   （或 uvicorn server.app:app --host 0.0.0.0 --port 8000）
"""
import os
import io
import re
import sys
import secrets
import threading
from typing import Optional
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from server import config, db, mapping, crawler, listfile, review
import script  # noqa: E402  复用 SCHEMA_FIELDS 导出原始字段 sheet

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

security = HTTPBasic()


# ---------------------------------------------------------------------------
# 认证：单人密码（HTTP Basic），账号密码存在 server_config.json
# ---------------------------------------------------------------------------
def require_auth(credentials: HTTPBasicCredentials = Depends(security)):
    cfg = config.load_config()
    a = cfg["auth"]
    ok_user = secrets.compare_digest(credentials.username.encode(), a["username"].encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), a["password"].encode())
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": 'Basic realm="company-crawler"'},
        )
    return credentials.username


app = FastAPI(title="公司信息采集服务", dependencies=[Depends(require_auth)])


# ---------------------------------------------------------------------------
# 抓取任务状态（内存态；单机单人够用）
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_state = {
    "status": "idle",     # idle / running / done / error
    "stage": "",          # 当前阶段提示（抓取中/翻译中）
    "message": "",
    "total": 0,
    "done": 0,
    "urls": [],
    "records": [],        # 映射后的记录（含 _raw 原始字段）
    "failed": [],         # 失败任务
}


def _run_crawl(urls):
    global _state
    try:
        with _lock:
            _state.update(status="running", stage="抓取中…", message="", total=len(urls),
                          done=0, urls=urls, records=[], failed=[])
        records, failed, evidence = crawler.crawl_urls(urls)
        with _lock:
            _state["stage"] = "翻译映射中…"
        cfg = config.load_config()
        defaults = cfg["defaults"]
        mapped = []
        for r in records:
            try:
                mapped.append(mapping.build_db_record(r, defaults))
            except Exception:
                mapped.append({"name": r.get("name"), "cnName": r.get("cn_name"),
                               "_raw": r, "_error": "映射失败"})
        # 抓完不再直接进入「可入库」状态：先落进审核批次，由人逐条核过才算数。
        # 并进旧批次而不是覆盖，这样分几次抓的结果能一起审；同一家公司重抓则
        # 数据换新、审核结论作废（数据都变了，旧结论不能留）。
        items = review.build_items(mapped, records, evidence)
        review.merge_batch(items, run_tag=(evidence[0].get("run_tag") if evidence else ""))
        c = review.counts()
        with _lock:
            _state["records"] = mapped
            _state["failed"] = failed
            _state["done"] = len(mapped)
            _state["status"] = "done"
            _state["stage"] = ""
            _state["message"] = ("抓取完成：成功 %d 家，失败 %d 家；已进入审核清单，"
                                 "当前待审 %d 家" % (len(mapped), len(failed), c["未检"]))
    except Exception as e:
        with _lock:
            _state["status"] = "error"
            _state["stage"] = ""
            _state["message"] = "抓取出错：%s" % e


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------
class DbConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 3306
    database: str = ""
    username: str = ""
    password: str = ""
    table: str = "company"


class AuthConfig(BaseModel):
    username: str = "admin"
    password: str = ""


class DefaultsConfig(BaseModel):
    updatedBy: str = "admin"
    isEnabled: int = 1
    isRecommended: int = 0
    accessCount: int = 0
    checkStatus: int = 0
    type: Optional[int] = None
    industryId: int = 0


class AiConfig(BaseModel):
    provider: str = "deepseek"
    api_url: str = ""
    api_key: str = ""
    model: str = ""
    max_tokens: int = 8192
    temperature: float = 0
    timeout: int = 180
    reasoning_effort: str = ""
    json_mode: bool = True


class FullConfig(BaseModel):
    db: DbConfig
    auth: AuthConfig
    defaults: DefaultsConfig
    ai: Optional[AiConfig] = None


class CrawlRequest(BaseModel):
    urls: list[str]


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
@app.get("/")
def index(_: str = Depends(require_auth)):
    return FileResponse(os.path.join(STATIC_DIR, "index.html"),
                        headers={"Cache-Control": "no-store"})


@app.get("/static/{sub:path}")
def static_files(sub: str, _: str = Depends(require_auth)):
    """前端拆成了 index.html + app.css + app.js，这里负责发后两个。

    禁缓存：改完样式/脚本普通刷新就能看到新版本，不必教人按 Ctrl+F5。
    路径级归属校验，挡住 ../ 穿越。
    """
    full = os.path.abspath(os.path.join(STATIC_DIR, sub))
    root = os.path.abspath(STATIC_DIR)
    try:
        if os.path.commonpath([full, root]) != root or not os.path.isfile(full):
            raise HTTPException(404, "静态文件不存在")
    except ValueError:
        raise HTTPException(404, "静态文件不存在")
    return FileResponse(full, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/config")
def get_config(_: str = Depends(require_auth)):
    cfg = config.load_config()
    return config.public_config(cfg)


@app.post("/api/save-config")
def save_config(body: FullConfig, _: str = Depends(require_auth)):
    data = body.model_dump()
    old = config.load_config()
    # 密码字段若被脱敏（"******"），则保留旧密码
    if data["db"]["password"] == "******":
        data["db"]["password"] = old["db"].get("password", "")
    if data["auth"]["password"] == "******":
        data["auth"]["password"] = old["auth"].get("password", "")
    # ai 段允许不传（老前端/只改数据库时）：不传就原样保留，别把它清空
    if data.get("ai") is None:
        data["ai"] = old.get("ai", {})
    elif data["ai"].get("api_key") == "******":
        data["ai"]["api_key"] = old.get("ai", {}).get("api_key", "")
    config.save_config(data)
    return {"ok": True, "message": "配置已保存"}


@app.post("/api/test-db")
def test_db(body: DbConfig, _: str = Depends(require_auth)):
    cfg = config.load_config()
    cfg["db"] = body.model_dump()
    if body.password == "******":
        cfg["db"]["password"] = config.load_config()["db"].get("password", "")
    ok, msg, columns = db.test_connection(cfg)
    return {"ok": ok, "message": msg, "columns": columns}


@app.get("/api/ai/presets")
def ai_presets(_: str = Depends(require_auth)):
    """常见供应商预设。全是 OpenAI 兼容接口，换供应商本质只是换 URL + 模型名。"""
    return {"presets": config.AI_PRESETS}


def _ai_payload(body: AiConfig):
    """把界面上的配置拼成一次真实调用所需的 url/headers/payload。"""
    cfg = config.load_config()
    key = cfg.get("ai", {}).get("api_key", "") if body.api_key == "******" else body.api_key
    if not key:
        key = script.DEEPSEEK_API_KEY          # 留空则回落 script.py 里的默认 Key
    url = (body.api_url or "").strip() or script.DEEPSEEK_API_URL
    payload = {
        "model": (body.model or "").strip() or script.DEEPSEEK_MODEL,
        "messages": [{"role": "system", "content": "只输出 JSON。"},
                     {"role": "user", "content": '回复 {"ok":true}'}],
        "temperature": body.temperature,
        "max_tokens": max(64, min(body.max_tokens, 2048)),   # 测试用不着几千 token
        "stream": False,
    }
    if body.json_mode:
        payload["response_format"] = {"type": "json_object"}
    if (body.reasoning_effort or "").strip():
        payload["reasoning_effort"] = body.reasoning_effort.strip()
    return url, {"Authorization": "Bearer " + key, "Content-Type": "application/json"}, payload


@app.post("/api/test-ai")
def test_ai(body: AiConfig, _: str = Depends(require_auth)):
    """真打一次接口验证配置。

    只发一句极短的提示词，几十 token 的成本，能验出：地址对不对、Key 有没有效、
    模型名存不存在、json_mode 支不支持，以及**思维链是不是开着的**。

    ⚠ 它**不能**预测真实抓取时会不会思维链烧穿：测试提示词只有一句话，
    再"爱想"的模型也只烧几十 token（实测 reasoning_effort=high + max_tokens=64
    照样通过）。真正的风险出现在 4000+ 字符的解析提示词上。
    所以这里的口径是：只要 reasoning_tokens > 0 就明确预警，让人把它关掉。
    """
    import time as _t
    url, headers, payload = _ai_payload(body)
    t0 = _t.time()
    try:
        r = script.get_session().post(url, json=payload, headers=headers,
                                      timeout=min(body.timeout or 60, 60))
    except Exception as e:
        return {"ok": False, "message": "连不上：%s: %s" % (type(e).__name__, e)}
    dt = _t.time() - t0
    if r.status_code != 200:
        return {"ok": False,
                "message": "HTTP %d —— %s" % (r.status_code, (r.text or "")[:300])}
    try:
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}
        rt = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
        fr = choice.get("finish_reason")
    except Exception as e:
        return {"ok": False, "message": "响应不是预期结构：%s（前 200 字：%s）" % (e, (r.text or "")[:200])}

    if not content.strip():
        tip = ""
        if fr == "length":
            tip = ("——这是**推理模型把 max_tokens 全烧在思维链上**了（思维链 %d token）。"
                   "把「思维链」设为 none，或把 max_tokens 调大。" % rt)
        return {"ok": False, "message": "接口通了但正文为空(finish_reason=%s)%s" % (fr, tip)}

    warn = ("（已关闭，正确）" if rt == 0 else
            "（⚠ 思维链是开着的：真实抓取的提示词有 4000+ 字符，"
            "思维链会计入 max_tokens 并可能把额度吃光导致正文为空。建议把「思维链」设为 none）")
    return {"ok": True, "message": "连接成功：模型 %s，用时 %.1fs，思维链 %d token%s"
                                   % (data.get("model") or payload["model"], dt, rt, warn),
            "model": data.get("model"), "elapsed": round(dt, 1), "reasoning_tokens": rt}


@app.post("/api/ai/models")
def ai_models(body: AiConfig, _: str = Depends(require_auth)):
    """拉取该供应商可用的模型清单（把 /chat/completions 换成 /models）。

    不是所有服务都提供这个接口，取不到就让界面回落到预设里的候选名单。
    """
    url = ((body.api_url or "").strip() or script.DEEPSEEK_API_URL)
    url = re.sub(r"/chat/completions/?$", "/models", url)
    cfg = config.load_config()
    key = cfg.get("ai", {}).get("api_key", "") if body.api_key == "******" else body.api_key
    try:
        r = script.get_session().get(url, headers={"Authorization": "Bearer " + (key or script.DEEPSEEK_API_KEY)},
                                     timeout=30)
        if r.status_code != 200:
            return {"ok": False, "message": "HTTP %d，该服务可能不提供模型列表接口" % r.status_code,
                    "models": []}
        ids = [m.get("id") for m in (r.json().get("data") or []) if m.get("id")]
        return {"ok": True, "models": sorted(ids), "message": "拉到 %d 个模型" % len(ids)}
    except Exception as e:
        return {"ok": False, "message": "拉取失败：%s" % e, "models": []}


@app.post("/api/crawl")
def crawl(body: CrawlRequest, _: str = Depends(require_auth)):
    urls = [u.strip() for u in body.urls if u and u.strip()]
    if not urls:
        return {"ok": False, "message": "请先输入至少一个网址"}
    with _lock:
        if _state["status"] == "running":
            return {"ok": False, "message": "已有抓取任务进行中，请稍候"}
    threading.Thread(target=_run_crawl, args=(urls,), daemon=True).start()
    return {"ok": True, "message": "已开始抓取 %d 个网址" % len(urls)}


@app.get("/api/crawl/status")
def crawl_status(_: str = Depends(require_auth)):
    with _lock:
        snap = dict(_state)
    snap["records"] = jsonable_encoder(snap["records"])
    snap["failed"] = jsonable_encoder(snap["failed"])
    return snap


@app.post("/api/write-db")
def write_db(_: str = Depends(require_auth)):
    """入库闸门：**只写人工标了「通过」的记录**。

    未检和有问题的一律不写。数据进的是生产库、且只增不改，写错一条就得去库里
    手工收拾——闸门放在写入之前，比写完再补救便宜得多。
    """
    state = review.load()
    records = review.passed_records(state)
    c = review.counts(state)
    if not records:
        if not c["total"]:
            return {"ok": False, "message": "审核清单是空的，请先抓取"}
        return {"ok": False,
                "message": "没有「通过」的记录可写入（未检 %d 家、有问题 %d 家）——"
                           "请先在「人工审核」里逐条核对" % (c["未检"], c["有问题"])}
    cfg = config.load_config()
    inserted, skipped, errors = db.insert_records(cfg, records)
    tail = ""
    if c["未检"] or c["有问题"]:
        tail = "；未写入：未检 %d 家、有问题 %d 家" % (c["未检"], c["有问题"])
    return {
        "ok": True,
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
        "message": "写入完成：新增 %d 条，跳过(已存在) %d 条，失败 %d 条%s"
                   % (inserted, skipped, len(errors), tail),
    }


# ---------------------------------------------------------------------------
# 人工审核
# ---------------------------------------------------------------------------
# 抓来的 25 个字段全部由大模型从 HTML 里读出，没有任何人核对过；其中中文名称、
# 国别、产品分类、属性还是「必填」项，错了会直接污染生产库。所以抓取与入库之间
# 必须有一道人工闸门：逐字段对着原始依据核，错的当场改，改动留痕。
class MarkBody(BaseModel):
    key: str
    status: str
    note: str = ""


class FieldMarkBody(BaseModel):
    key: str
    field: str
    action: str          # ok / problem / clear


class FieldEditBody(BaseModel):
    key: str
    field: str
    value: str = ""


class KeyFieldBody(BaseModel):
    key: str
    field: str


def _review_err(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except review.ReviewError as e:
        raise HTTPException(400, str(e))


@app.get("/api/review/list")
def review_list(_: str = Depends(require_auth)):
    state = review.load()
    return {"items": review.summary_list(state), "counts": review.counts(state),
            "run_tag": state.get("run_tag", ""), "created": state.get("created", "")}


@app.get("/api/review/item")
def review_item(key: str, _: str = Depends(require_auth)):
    it = review.get_item(key)
    if not it:
        raise HTTPException(404, "找不到这条记录")
    return {
        "item": it,
        # 字段元数据随详情一起下发：前端不必自己维护一份字段清单，
        # 后端加字段时界面自动跟上，不会出现「采了却没人看得见」的字段。
        "meta": {
            "groups": mapping.DB_FIELD_GROUPS,
            "cn": mapping.DB_FIELD_CN,
            "risky": sorted(mapping.RISKY_COLUMNS),
            "editable": mapping.EDITABLE_COLUMNS,
            "raw_key_fields": mapping.RAW_KEY_FIELDS,
            "raw_cn": {k: h for k, h in script.SCHEMA_FIELDS},
            "raw_order": [k for k, _h in script.SCHEMA_FIELDS],
        },
    }


@app.post("/api/review/mark")
def review_mark(body: MarkBody, _: str = Depends(require_auth)):
    _review_err(review.mark, body.key, body.status, body.note)
    return {"ok": True, "counts": review.counts()}


@app.post("/api/review/field")
def review_field(body: FieldMarkBody, _: str = Depends(require_auth)):
    it = _review_err(review.mark_field, body.key, body.field, body.action)
    return {"ok": True, "review": it["review"]}


@app.post("/api/review/edit")
def review_edit(body: FieldEditBody, _: str = Depends(require_auth)):
    it = _review_err(review.edit_field, body.key, body.field, body.value,
                     set(mapping.EDITABLE_COLUMNS))
    return {"ok": True, "record": it["record"], "review": it["review"], "name": it["name"]}


@app.post("/api/review/revert")
def review_revert(body: KeyFieldBody, _: str = Depends(require_auth)):
    it = _review_err(review.revert_field, body.key, body.field)
    return {"ok": True, "record": it["record"], "review": it["review"], "name": it["name"]}


@app.post("/api/review/clear")
def review_clear(_: str = Depends(require_auth)):
    review.clear_all()
    return {"ok": True, "message": "审核清单已清空"}


# ---------------------------------------------------------------------------
# 证据文件：抓取时落盘的首页/关于页/联系页 HTML 与 logo 图片
# ---------------------------------------------------------------------------
# 人工核查的核心是「对着来源看」，所以要能直接翻出 AI 当时读的那份 HTML。
# ⚠ 路径来自审核状态文件而非请求参数，且仍做一次归属校验：只允许读 runs/ 下的文件，
#   用路径级判断（os.path.commonpath）而不是字符串前缀比较——前缀比较会放行 runs2/。
EVIDENCE_KINDS = {"home": "home_html", "about": "about_html",
                  "contact": "contact_html", "logo": "logo"}


def _safe_run_file(rel):
    if not rel:
        return None
    full = os.path.abspath(os.path.join(BASE_DIR, rel))
    root = os.path.abspath(os.path.join(BASE_DIR, "runs"))
    try:
        if os.path.commonpath([full, root]) != root:
            return None
    except ValueError:
        return None
    return full if os.path.isfile(full) else None


@app.get("/api/evidence")
def evidence(key: str, kind: str, _: str = Depends(require_auth)):
    if kind not in EVIDENCE_KINDS:
        raise HTTPException(400, "kind 只能是 home / about / contact / logo")
    it = review.get_item(key)
    if not it:
        raise HTTPException(404, "找不到这条记录")
    full = _safe_run_file((it.get("evidence") or {}).get(EVIDENCE_KINDS[kind]))
    if not full:
        raise HTTPException(404, "这家公司没有留下该文件（抓取时就没拿到）")
    if kind == "logo":
        # logo 会被人工替换，URL 不变，若不置 no-store 浏览器会用旧缓存（替换后仍显示旧图）
        return FileResponse(full, headers={"Cache-Control": "no-store"})
    # HTML 直接内嵌展示：注入 <base> 让页面里的相对路径图片/样式仍能从原站加载，
    # 否则存下来的页面会是一堆裸文字，看不出原貌、也就核对不了。
    site = (it.get("evidence") or {}).get("site") or ""
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            html = f.read()
    except OSError as e:
        raise HTTPException(500, "读取失败：%s" % e)
    if site:
        inject = '<base href="%s/">' % site.rstrip("/")
        low = html.lower()
        i = low.find("<head")
        if i >= 0:
            j = html.find(">", i)
            html = html[:j + 1] + inject + html[j + 1:]
        else:
            html = inject + html
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# logo 人工替换：审核发现抓错了 → 删掉，再 Ctrl+V 粘贴一张新的
# ---------------------------------------------------------------------------
class LogoDeleteBody(BaseModel):
    key: str


def _image_ext(filename, ctype, data):
    """从文件名 / MIME / 魔数判断图片后缀；非图片返回 None。"""
    fn = (filename or "").lower()
    for e in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        if fn.endswith(e):
            return e.lstrip(".")
    ct = (ctype or "").lower()
    m = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
    if ct in m:
        return m[ct]
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


@app.post("/api/logo-delete")
def logo_delete(body: LogoDeleteBody, _: str = Depends(require_auth)):
    """发现抓到的 logo 是错的：删掉，等下人工粘贴一张新的上去。"""
    _review_err(review.set_logo, body.key, None)
    return {"ok": True}


@app.post("/api/logo-upload")
async def logo_upload(key: str = Form(...), file: UploadFile = File(...),
                      _: str = Depends(require_auth)):
    """人工替换 logo：从剪贴板粘贴上传一张新图，覆盖这家公司的 evidence.logo。

    落盘与下载/导出同款命名逻辑（文件名=记录 id + 后缀），并更新审核状态里的
    evidence.logo 指针——审核展示、logo 下载、导出打包读的都是它，改这一处全生效。
    """
    it = review.get_item(key)
    if not it:
        raise HTTPException(404, "找不到这条记录")
    data = await file.read()
    if not data:
        raise HTTPException(400, "没读到图片内容")
    ext = _image_ext(file.filename, file.content_type, data)
    if not ext:
        raise HTTPException(400, "只支持图片（png / jpg / jpeg / gif / webp）")

    # 落盘目录：沿用现有 logo 所在目录；从没有过 logo 就放当前 run 目录下新建的 logos
    cur = (it.get("evidence") or {}).get("logo")
    if cur:
        dest_dir = os.path.dirname(os.path.abspath(os.path.join(BASE_DIR, cur)))
    else:
        run_tag = (review.load().get("run_tag") or "web采集").replace(os.sep, "_")
        dest_dir = os.path.join(BASE_DIR, "runs", "%s_logo" % run_tag, "logos")
    os.makedirs(dest_dir, exist_ok=True)

    cid = (it.get("record") or {}).get("id") or it["key"]
    fname = "%s_%s.%s" % (cid, datetime.now().strftime("%H%M%S"), ext)
    dest = os.path.join(dest_dir, fname)
    with open(dest, "wb") as f:
        f.write(data)
    rel = os.path.relpath(dest, BASE_DIR).replace(os.sep, "/")
    review.set_logo(key, rel)
    return {"ok": True, "logo": rel}


# ---------------------------------------------------------------------------
# 导出字段选择 + logo 下载
# ---------------------------------------------------------------------------
@app.get("/api/export/fields")
def export_fields(_: str = Depends(require_auth)):
    """「导出字段」选择框要用的清单：全部 DB 字段、中文名、默认不导出的那几个。"""
    return {"columns": mapping.DB_COLUMNS,
            "cn": mapping.DB_FIELD_CN,
            "skip": sorted(mapping.EXPORT_DEFAULT_SKIP)}


@app.get("/api/logo-download")
def logo_download(key: str, _: str = Depends(require_auth)):
    """下载抓到的 logo，文件名 = 这家公司的记录 id（人工传服务器时按 id 命名）。

    Content-Disposition 用 attachment，浏览器拿到直接存文件而不是打开预览。
    """
    it = review.get_item(key)
    if not it:
        raise HTTPException(404, "找不到这条记录")
    full = _safe_run_file((it.get("evidence") or {}).get("logo"))
    if not full:
        raise HTTPException(404, "这家公司抓取时没有留下 logo")
    ext = os.path.splitext(full)[1].lower() or ".png"
    cid = (it.get("record") or {}).get("id") or it["key"]
    import mimetypes
    return FileResponse(full, media_type=mimetypes.guess_type(full)[0] or "image/png",
                        filename="%s%s" % (cid, ext),
                        headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# 名单导入（上传 Excel / CSV / TXT，自动识别网址列 + 公司名称列）
# ---------------------------------------------------------------------------
@app.post("/api/import-list")
async def import_list(file: UploadFile = File(...), _: str = Depends(require_auth)):
    content = await file.read()
    items, err = listfile.parse_upload(content, file.filename or "")
    if err:
        return {"ok": False, "message": err}
    return {
        "ok": True,
        "count": len(items),
        "items": [{"url": u, "name": n} for u, n in items],
        "message": "识别到 %d 条网址" % len(items),
    }


def _build_workbook(records, raws, reviews, cols):
    """把导出行拼成一个 xlsx 的 BytesIO。records/raws/reviews 与 items 逐行对齐。

    sheet1 = 映射后的正式字段，sheet2 = 抓取的原始 25 字段（一个不丢）。
    """
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    wb = Workbook()

    # ---- sheet1：字段信息（映射后正式数据，34 字段）----
    ws1 = wb.active
    ws1.title = "字段信息"
    headers1 = ["审核状态", "问题说明", "人工修正字段"] + list(cols)
    ws1.append(headers1)
    for i, rec in enumerate(records):
        rv = reviews[i]
        edits = rv.get("edits") or {}
        row = [rv.get("status") or review.UNCHECKED,
               rv.get("note") or "",
               # 「人工修正字段」列只列这次导出的列里被人改过的，别提一个表里没有的字段
               "；".join(f for f in edits if f in cols)]
        for c in cols:
            v = rec.get(c)
            if isinstance(v, datetime):
                v = v.strftime("%Y-%m-%d %H:%M:%S")
            row.append(v)
        ws1.append(row)
    for i, h in enumerate(headers1, 1):
        ws1.column_dimensions[get_column_letter(i)].width = max(10, min(40, len(str(h)) * 2))
    ws1.freeze_panes = "A2"

    # ---- sheet2：原始字段（抓取的 25 字段，一个不丢）----
    ws2 = wb.create_sheet("原始字段")
    raw_headers = [k for k, _h in script.SCHEMA_FIELDS]   # 表头用英文原始字段名
    ws2.append(raw_headers)
    for raw in raws:
        ws2.append([raw.get(k) for k in raw_headers])
    for i, h in enumerate(raw_headers, 1):
        ws2.column_dimensions[get_column_letter(i)].width = max(10, min(40, len(str(h)) * 2))
    ws2.freeze_panes = "A2"

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


# ---------------------------------------------------------------------------
# 导出 Excel：sheet1 = 字段信息（映射后 34 字段），sheet2 = 原始字段（25 字段）
# ---------------------------------------------------------------------------
@app.get("/api/export")
def export_excel(scope: str = "all", keys: str = "", fields: str = "",
                with_logos: int = 0, _: str = Depends(require_auth)):
    """导出 Excel。scope=passed 只导「通过」的，scope=all 导全部（带审核状态）；
    keys 是逗号分隔的条目 key 列表（公司勾选导出），给了就先按它过滤，两者可叠加。
    fields 是逗号分隔的 DB 字段名列表（导出字段选择）；不给就用默认列：
    **排除** mapping.EXPORT_DEFAULT_SKIP 里那 8 个系统维护字段。

    导的**一律是审核清单里的当前值**——人工修正过的字段必须体现在导出结果里，
    否则拿去人工入库的那份和界面上看到的对不上，等于白审。

    连不上 MySQL 时，这个导出就是唯一的交付路径，所以前三列专门交代审核结论：
    审核状态 / 问题说明 / 人工修正字段——让做人工导入的人一眼看出哪些值是人改过的、
    哪些行压根不该导进去。
    """
    state = review.load()
    items = state.get("items", [])
    want = [k for k in (keys or "").split(",") if k.strip()]
    if want:
        w = set(want)
        items = [it for it in items if it["key"] in w]
    if scope == "passed":
        items = [it for it in items
                 if ((it.get("review") or {}).get("status") or review.UNCHECKED) == review.PASSED]
    records = [it["record"] for it in items]
    raws = [it.get("raw") or {} for it in items]
    reviews = [it.get("review") or {} for it in items]
    if not records:
        if want:
            msg = "所选公司在清单里没有可导出的记录（可能已被清空或重新抓取）"
        elif scope == "passed":
            msg = "没有「通过」的记录可导出——请先在「人工审核」里逐条核对"
        else:
            msg = "暂无数据可导出，请先完成抓取"
        return JSONResponse(status_code=400, content={"ok": False, "message": msg})

    # 要导出哪些字段：给了 fields 参数就按它（按传参顺序），不认识的字段名忽略；
    # 没给就用默认列 = 全部 DB 字段 - 那 8 个系统维护字段。
    want_fields = [f.strip() for f in (fields or "").split(",") if f.strip()]
    if want_fields:
        cols = [c for c in want_fields if c in mapping.DB_COLUMNS]
        if not cols:
            return JSONResponse(status_code=400, content={"ok": False, "message": "没有选择任何可导出的字段"})
    else:
        cols = [c for c in mapping.DB_COLUMNS if c not in mapping.EXPORT_DEFAULT_SKIP]

    try:
        import openpyxl  # noqa: F401  缺依赖时报友好错误
    except ImportError:
        return JSONResponse(status_code=500, content={"ok": False, "message": "未安装 openpyxl，无法导出"})

    bio = _build_workbook(records, raws, reviews, cols)

    tag = "所选" if want else ("已通过" if scope == "passed" else "全部")
    base = "公司信息_%s_%d家_%s" % (tag, len(records), datetime.now().strftime("%Y%m%d_%H%M%S"))
    from urllib.parse import quote

    # 勾选「同时导出 logo」时，把 Excel 和各公司 logo 打包成一个 zip（放在同一保存路径）。
    # 浏览器一次下载只能定位到一个文件，没法把 N 个 logo 直接铺进用户选的文件夹；
    # zip 解压后 Excel 与 logos 子目录就都在那个文件夹里了。
    if with_logos:
        import zipfile
        zipio = io.BytesIO()
        with zipfile.ZipFile(zipio, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(base + ".xlsx", bio.getvalue())
            for it, rec in zip(items, records):
                rel = (it.get("evidence") or {}).get("logo")
                if not rel:
                    continue
                full = _safe_run_file(rel)
                if not full:
                    continue
                cid = rec.get("id") or it["key"]
                ext = os.path.splitext(full)[1].lower() or ".png"
                zf.write(full, "logos/%s%s" % (cid, ext))
        zipio.seek(0)
        fname = base + "_含logo.zip"
        return StreamingResponse(
            zipio,
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(fname)},
        )

    disp = "attachment; filename*=UTF-8''%s" % quote(base + ".xlsx")
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disp},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.app:app", host="0.0.0.0", port=8000, reload=False)
