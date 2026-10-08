# -*- coding: utf-8 -*-
"""自动化数据处理平台 — FastAPI 后端入口。"""
import os, json
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .paths import FRONTEND_DIR, SKILLS_DIR, normalize_path
from . import store, llm as llmmod, mapping, consolidate, audit, idmap, pipeline, manual, history, registry, stats, keywords, review, report, overview, variants, shelf, htmlreport, reports2, purge
from .jobs import manager

app = FastAPI(title="自动化数据处理平台")


@app.middleware("http")
async def no_cache_static(request, call_next):
    """前端 html/js/css 禁缓存：平台更新后浏览器普通刷新即可看到新页面，避免旧导航/旧脚本残留。"""
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.endswith((".html", ".js", ".css")):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


# 局域网部署的 Basic Auth（auth.py）。设置 DEPLOY_USER/DEPLOY_PASS 环境变量即开启；
# 未设置时放行（开发模式）。中间件按注册顺序栈式执行，本块后注册 → 最先执行。
from .auth import auth_middleware as _auth_middleware

app.middleware("http")(_auth_middleware)


# ---------------- 页面1：配置 ----------------
@app.get("/api/providers")
def providers():
    return store.PROVIDERS


@app.get("/api/models")
def models():
    return {"models": store.list_models(), "max": store.MAX_MODELS}


class ModelPayload(BaseModel):
    id: str | None = None
    name: str
    provider: str = "custom"
    api: str = "openai"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    role: str = ""
    prompt: str = ""
    skills: list[str] = []
    temperature: float = 0.2


@app.post("/api/models")
def save_model(p: ModelPayload):
    m, err = store.save_model(p.model_dump())
    if err:
        raise HTTPException(400, err)
    return {"ok": True, "id": m["id"]}


@app.delete("/api/models/{mid}")
def delete_model(mid: str):
    if not store.delete_model(mid):
        raise HTTPException(404, "未找到该任务模型")
    return {"ok": True}


@app.post("/api/models/{mid}/test")
def test_model(mid: str):
    cfg = store.get_model(mid)
    if not cfg:
        raise HTTPException(404, "未找到该任务模型")
    text, err = llmmod.chat(cfg, "请只回复两个字：正常")
    return {"ok": err is None, "reply": (text or "")[:200], "error": err}


# ---------------- skill 库 ----------------
@app.get("/api/skills")
def skills():
    out = []
    for f in sorted(os.listdir(SKILLS_DIR)):
        if f.lower().endswith(".md"):
            p = os.path.join(SKILLS_DIR, f)
            out.append({"file": f, "size": os.path.getsize(p)})
    return out


@app.get("/api/skills/{name}", response_class=PlainTextResponse)
def skill_content(name: str):
    p = os.path.join(SKILLS_DIR, os.path.basename(name))
    if not os.path.isfile(p):
        raise HTTPException(404, "skill 不存在")
    return open(p, encoding="utf-8").read()


@app.post("/api/skills/upload")
async def upload_skill(file: UploadFile = File(...)):
    name = os.path.basename(file.filename or "skill.md")
    if not name.lower().endswith(".md"):
        raise HTTPException(400, "只支持 .md 文件")
    data = await file.read()
    open(os.path.join(SKILLS_DIR, name), "wb").write(data)
    return {"ok": True, "file": name}


# ---------------- 通用：路径与任务 ----------------
class PathPayload(BaseModel):
    path: str


# 注：曾有一个 WRITE_KINDS 常量列着五种写盘任务，但 0 处引用 —— 写盘任务早就改走
# manager.submit 串行排队了，互斥由队列本身保证，不再需要 kind 集合。留着会让人以为
# 还有一道基于它的闸门。已删除。


def _guard(kind):
    """非写盘任务（overview / export）的重入闸门：同类型只允许一个在跑。

    写盘任务不再用闸门，改走 manager.submit 排队 —— 总览是"按状态分档批量执行"的用法，
    用户会把几个档位都点一遍再去干别的，每次都 409 拒绝很烦。
    """
    busy = manager.running_in({kind})
    if busy:
        raise HTTPException(409, f"已有任务在运行：{busy.title}。等它跑完再试。")


def _same_or_under(a, b):
    """a 是否**等于** b、或在 b 内部（两侧都已 abspath）。"""
    a, b = os.path.abspath(a), os.path.abspath(b)
    return a == b or a.startswith(b + os.sep)


# 输出目录清单：(中文名, 配置键)。顺序只影响报错文案。
OUT_KEYS = [("映射输出", "mapping_out"), ("未匹配", "unmatched"), ("过程记录", "process"),
            ("清洗输出", "clean_out"), ("最终数据", "final_out")]
# 输出之间的嵌套关系：后者不得是前者本身、也不得在它内部。
OUT_PAIRS = [("清洗输出", "clean_out", "映射输出", "mapping_out"),
             ("最终数据", "final_out", "清洗输出", "clean_out")]


def _guard_out_paths(c, keys=None):
    """路径闸门：输出目录不得嵌在输入目录内，且两两不得**相等**。

    ⚠ 两条都是补上的，起因是 CLAUDE.md 第十四节第 1 条写着「`main.py` 有校验拒绝输出目录
    嵌在输入目录内」，而那条校验**根本不存在** —— `input` 一次都没进过比较，全靠"大家都
    记得别配错"。

    第二条更隐蔽：原来写的是 `abspath(输出).startswith(abspath(另一个输出) + os.sep)`，
    这个写法**放过了相等**。把 clean_out 配成 mapping_out 能一路过闸门，然后 ③ 会把
    `类别X/公司/…` 写进映射输出树，`registry.clean_status` 再把 `类别1_json_非空` 当成
    一家"公司"参与增量判定与统计 —— 而"原始数据零改动"是一条硬约束，不能靠自觉。

    keys：只校验**本次真的要写**的那几个输出目录（None = 全部）。按勾选执行时不能全查 ——
    一个用不上的目录配错了，不该拦住与之无关的步骤。
    空值一律跳过（未配置的目录不参与判定）；判空与报错文案仍由各入口自己负责，
    因为每个入口对"哪个目录必须配"的要求不同。
    """
    keys = set(keys) if keys is not None else {k for _l, k in OUT_KEYS}
    inp = (c.get("input") or "").strip()
    for label, k in OUT_KEYS:
        if k not in keys:
            continue
        p = (c.get(k) or "").strip()
        if not p:
            continue
        if inp and _same_or_under(p, inp):
            raise HTTPException(400, f"{label}目录不能是输入目录本身、也不能在它内部"
                                     f"（原始数据零改动）：{p}")
    for la, ka, lb, kb in OUT_PAIRS:
        if ka not in keys or kb not in keys:
            continue
        a, b = (c.get(ka) or "").strip(), (c.get(kb) or "").strip()
        if a and b and _same_or_under(a, b):
            raise HTTPException(400, f"{la}目录不能是{lb}目录本身、也不能在它内部：{a}")


@app.get("/api/joblatest")
def job_latest(kind: str):
    j = manager.latest(kind)
    return j.to_dict(0) if j else {"id": None}


@app.get("/api/queue")
def queue_state():
    """当前在跑的 + 排队中的任务。总览用它显示「排队中 N 个」。"""
    return manager.queue_state()


@app.get("/api/jobs")
def jobs_list(limit: int = 80):
    """全部任务（含进程重启前、从 data/jobs 恢复的）。总览的「任务日志」抽屉用它。

    每条不带日志正文（可能上万行），只给摘要；正文按需走 /api/jobs/{jid}。
    """
    return {"jobs": manager.list(limit, light=True), "total": len(manager.jobs)}


@app.delete("/api/jobs/{jid}")
def job_delete(jid: str):
    ok, err = manager.remove(jid)
    if not ok:
        raise HTTPException(400 if "运行" in err else 404, err)
    return {"ok": True}


@app.post("/api/jobs/purge")
def jobs_purge():
    """清空全部已结束的任务记录与日志文件。在跑的和排队的不动。"""
    return {"removed": manager.purge()}


@app.get("/api/jobs/{jid}")
def job_status(jid: str, log_from: int = 0):
    j = manager.get(jid)
    if not j:
        raise HTTPException(404, "任务不存在")
    return j.to_dict(log_from)


@app.get("/api/jobs/{jid}/log", response_class=PlainTextResponse)
def job_log(jid: str):
    """完整日志原文。内存窗口只留最近 3000 行，要看全的走这里（读 data/jobs/<id>.log）。"""
    txt = manager.read_log(jid)
    if txt is None:
        raise HTTPException(404, "任务不存在")
    j = manager.get(jid)
    return PlainTextResponse(
        txt, headers={"Content-Disposition":
                      f'attachment; filename="job-{jid}-{j.kind}.log"'})


@app.post("/api/jobs/{jid}/cancel")
def job_cancel(jid: str):
    return {"ok": manager.cancel(jid)}


@app.post("/api/jobs/{jid}/dismiss")
def job_dismiss(jid: str):
    """把一张「已结束」卡片从队列区收起。记录与日志都还在，随时能再查。"""
    return {"ok": manager.dismiss(jid)}


# ---------------- 总览缓存的收尾刷新 ----------------
def _ov_after(pick):
    """任务跑完就地刷新总览里受影响的那几家公司。

    以前总览只有「手动重新扫描」一条更新途径（全量 60 秒），于是跑完一家公司、
    人工匹配完一条，总览还停在几天前的状态，用户根本不知道哪家需要重跑。
    pick(job) → 受影响的公司名列表；返回空就什么都不做。
    """
    def hook(job):
        try:
            names = pick(job) or []
        except Exception:
            names = []
        if names:
            overview.refresh(names)
    return hook


def _companies_of(job, key="companies"):
    r = job.result or {}
    v = r.get(key) or []
    if v and isinstance(v[0], dict):
        return [x.get("company") or x.get("name") or "" for x in v]
    return list(v)


def _meta(steps, companies=None):
    """任务元信息，随 /api/queue 一起给前端。

    有了它，前端在提交前才能看出「你要排的这一步，正在跑的那个任务已经包含了」——
    上次就是因为看不出来，用户在一个已经勾了 ⑤ 的任务后面又排了一个 ⑤，
    白等了三个多小时。steps 用 map/clean/audit/idmap 四个码。
    """
    return {"steps": list(steps), "companies": list(companies or [])}


# ---------------- 页面2：产品数据映射 ----------------
@app.post("/api/mapping/scan")
def mapping_scan(p: PathPayload):
    path = normalize_path(p.path)
    res = mapping.scan_companies(path)
    if res is None:
        raise HTTPException(400, f"路径不存在: {path}")
    return {"companies": registry.status_of(res), "path": path}


# 注：曾经还有一个 POST /api/mapping/run（kind=mapping），与 /api/process/run 的 do_map
# 功能完全重复、各维护一份一模一样的入参校验，且前端零调用。已删除，映射统一走 process。


@app.get("/api/company/report")
def company_report(company: str):
    """某公司的映射报告（过程记录/<公司>/映射报告.md）。总览的 ② 状态格点开就是它。"""
    c = pipeline.load_config()
    p = os.path.join(c["process"], company, "映射报告.md")
    if not os.path.isfile(p):
        raise HTTPException(404, "这家公司还没有映射报告（跑一次 ② 映射就会生成）")
    return PlainTextResponse(open(p, encoding="utf-8").read())


@app.get("/api/company/audit")
def company_audit(company: str):
    """某公司最近一次复查的结论与明细。总览的 ④ 状态格点开就是它。"""
    rec = overview.load_audit().get(company)
    if not rec:
        raise HTTPException(404, "这家公司还没有复查记录")
    return rec


@app.post("/api/mapping/report")
def mapping_report(p: PathPayload):
    path = normalize_path(p.path)
    if not os.path.isfile(path) or not path.endswith("映射报告.md"):
        raise HTTPException(404, "报告不存在")
    return PlainTextResponse(open(path, encoding="utf-8").read())


# ---------------- 分类 + 清洗（合一，按公司增量） ----------------
@app.get("/api/consolidate/status")
def consolidate_status():
    """映射输出下每家公司的清洗状态（新增/有更新/已完成），供前端展示增量范围。"""
    c = pipeline.load_config()
    rows = registry.clean_status(c["mapping_out"], c["clean_out"])
    return {"companies": rows, "mapping_out": c["mapping_out"], "clean_out": c["clean_out"],
            "pending": sum(1 for r in rows if r["status"] != "已完成")}


class ConsolidateRun(BaseModel):
    companies: list[str] = []      # 空 = 自动按增量选取
    apply: bool = True
    force: bool = False            # 忽略清洗记忆，强制重做


@app.post("/api/consolidate/run")
def consolidate_run(p: ConsolidateRun):
    c = pipeline.load_config()
    if not os.path.isdir(c["mapping_out"]):
        raise HTTPException(400, f"映射输出目录不存在: {c['mapping_out']}")
    if not c["clean_out"]:
        raise HTTPException(400, "请先在数据处理页配置清洗输出目录")
    _guard_out_paths(c, ["mapping_out", "clean_out"])
    job = manager.submit("consolidate", ("分类+清洗(落地)" if p.apply else "分类+清洗(预演)"),
                        consolidate.run_consolidate, c["mapping_out"], c["clean_out"],
                        p.companies or None, p.apply, p.force,
                        meta=_meta(["clean"], p.companies),
                        on_done=_ov_after(lambda j: _companies_of(j)))
    return {"job_id": job.id}


# ---------------- 复查 + 公司ID ----------------
class AuditRun(BaseModel):
    cleaned: str = ""
    pristine: str = ""
    companies: list[str] = []      # 空 = 全量复查


@app.post("/api/audit/run")
def audit_run(p: AuditRun):
    c = pipeline.load_config()
    cleaned = normalize_path(p.cleaned) or c["clean_out"]
    pristine = normalize_path(p.pristine) or c["mapping_out"]
    title = "数据复查(审计)" + (f" · {len(p.companies)} 家公司" if p.companies else " · 全量")
    job = manager.submit("audit", title, audit.run_audit,
                        cleaned, pristine, p.companies or None,
                        meta=_meta(["audit"], p.companies),
                        on_done=_ov_after(lambda j: _companies_of(j)))
    return {"job_id": job.id}


# ---------------- id 映射（公司名→公司id，一级/二级→分类id） ----------------
class IdMapRun(BaseModel):
    companies: list[str] = []      # 空 = 全量
    apply: bool = True


@app.get("/api/idmap/status")
def idmap_status():
    """id 表是否齐备、各有多少条可用，供前端提示。"""
    c = pipeline.load_config()
    out = {"final_out": c["final_out"], "company_id": c["company_id"],
           "product_id": c["product_id"], "ready": True, "detail": {}}
    for k, label in (("company_id", "公司id.json"), ("product_id", "产品id.json")):
        try:
            m, n = idmap.load_id_table(c[k], label)
            out["detail"][k] = {"ok": True, "usable": len(m), "total": n}
        except ValueError as e:
            out["ready"] = False
            out["detail"][k] = {"ok": False, "error": str(e)}
    return out


@app.get("/api/idmap/companies")
def idmap_companies():
    """清洗后目录下每家公司的 id 解析情况，供单独执行 id 映射时勾选。"""
    c = pipeline.load_config()
    if not os.path.isdir(c["clean_out"]):
        raise HTTPException(400, f"清洗后目录不存在: {c['clean_out']}")
    try:
        rows = idmap.company_overview(c["clean_out"], c["final_out"],
                                      c["company_id"], c["product_id"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"companies": rows, "clean_out": c["clean_out"], "final_out": c["final_out"],
            "unresolved": sum(1 for r in rows if not r["resolved"])}


class AliasReq(BaseModel):
    kind: str = "company"      # company | category
    name: str
    id: str = ""               # 空 = 删除该别名


@app.post("/api/idmap/alias")
def idmap_alias(p: AliasReq):
    """给 id 表里查不到的目录名指定 id（存平台 data/id_aliases.json，不改动外部 id 表）。"""
    try:
        d = idmap.set_alias(p.kind, p.name, p.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "aliases": d}


@app.post("/api/idmap/run")
def idmap_run(p: IdMapRun):
    c = pipeline.load_config()
    if not os.path.isdir(c["clean_out"]):
        raise HTTPException(400, f"清洗后目录不存在: {c['clean_out']}")
    _guard_out_paths(c, ["clean_out", "final_out"])
    for k, label in (("company_id", "公司id.json"), ("product_id", "产品id.json")):
        if not os.path.isfile(c[k]):
            raise HTTPException(400, f"{label} 不存在: {c[k] or '未配置'}")
    job = manager.submit("idmap", ("id映射(落地)" if p.apply else "id映射(预演)"),
                        idmap.run_idmap, c["clean_out"], c["final_out"],
                        c["company_id"], c["product_id"], p.apply, p.companies or None,
                        meta=_meta(["idmap"], p.companies),
                        on_done=_ov_after(lambda j: _companies_of(j)))
    return {"job_id": job.id}


# ---------------- 同名变体（同一家公司被录成两份目录） ----------------
@app.get("/api/variants")
def variants_list():
    """变体组 + 一致性判定 + 建议。只读，不动任何数据。

    分两级：
    - input  —— 输入目录里就重名的，**在进流程之前**发现，此时处理成本最低。
                 只报不动（原始数据零改动），合并由人工做完再刷新。
    - groups —— 已经进了流程的，能比对产品清单，可以合并。
    """
    cfg = pipeline.load_config()
    snap = overview.load_cache() or {}
    counts = {r["name"]: (r.get("input_products") or r.get("products"))
              for r in snap.get("rows", [])}
    return {"groups": variants.detect(cfg),
            "input": variants.detect_input(cfg, counts)}


class VariantResolve(BaseModel):
    keep: str
    drop: list[str]
    merge: bool = True             # 先把 drop 独有的产品并进 keep，再删 drop
    apply: bool = False            # 默认预演


@app.post("/api/variants/resolve")
def variants_resolve(p: VariantResolve):
    cfg = pipeline.load_config()
    try:
        r = variants.resolve(cfg, p.keep, p.drop, p.merge, p.apply)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if p.apply:
        overview.refresh_bg([p.keep] + list(p.drop))
    return r


# ---------------- 分类 id 解析情况 ----------------
@app.get("/api/idmap/categories")
def idmap_categories():
    """清洗后目录里出现过的 一级/二级 分类各自能不能查到分类 id。
    公司 id 一直有表可看，分类 id 以前只能跑完翻日志。"""
    c = pipeline.load_config()
    if not os.path.isfile(c["product_id"]):
        raise HTTPException(400, f"产品id.json 不存在: {c['product_id'] or '未配置'}")
    try:
        rows = idmap.category_overview(c["clean_out"], c["product_id"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"rows": rows,
            "unresolved": sum(1 for r in rows if not r["resolved"]),
            "fallback": sum(1 for r in rows if r["via"] == "退回一级")}


# ---------------- 总览（公司 × 阶段）----------------
@app.get("/api/overview")
def overview_get():
    """秒开：只读缓存 + 一次廉价的输入目录同步。

    绝不在这里跑全盘扫描（那要两分多钟，页面会像卡死一样）。但**新爬的公司必须
    一进输入目录就能在总览里看到** —— 以前它们要等「跑完一键全流程再点刷新」才出现，
    那时候活都干完了。sync_input 只 listdir 比对名字，新公司立刻占位入表，
    产品数交给后台线程去数，**这个接口里一次子树 walk 都不做**。

    `counting` = 后台正在清点的公司名。前端据此决定要不要过几秒再取一次
    —— 不给这个信号的话，新公司就会一直停在「待清点」上，直到用户自己刷新页面。"""
    c = overview.load_cache()
    if not c:
        return {"ready": False, "reason": "尚未扫描过，点「重新扫描」建立第一份快照。"}
    try:
        c = overview.sync_input(snap=c) or c
    except Exception:
        pass          # 同步失败不能拖累主用途：看缓存
    return {"ready": True, **c, "counting": overview.counting_now()}


@app.get("/api/overview/badge")
def overview_badge():
    try:
        overview.sync_input()
    except Exception:
        pass
    return overview.badge()


@app.post("/api/overview/scan")
def overview_scan():
    _guard("overview")
    job = manager.start("overview", "扫描流水线状态", overview.build)
    return {"job_id": job.id}


# ---------------- 一键全流程 ----------------
@app.get("/api/pipeline/config")
def pipeline_config():
    return pipeline.load_config()


@app.post("/api/pipeline/config")
def pipeline_save(cfg: dict):
    return pipeline.save_config(cfg)


@app.post("/api/pipeline/run")
def pipeline_run(cfg: dict):
    # companies 只用于本次运行范围，不进 pipeline.json（它不是配置）
    comps = [c for c in (cfg.pop("companies", None) or []) if str(c).strip()]
    c = pipeline.save_config(cfg)          # 先保存本次配置
    model_cfg = store.get_model(c.get("model_id")) if (c.get("use_ai") and c.get("model_id")) else None
    if c.get("use_ai") and not model_cfg:
        raise HTTPException(400, "已开启 AI 研判但未选择有效的任务模型（请先在配置页创建）")
    if not os.path.isdir(c["input"]):
        raise HTTPException(400, f"输入路径不存在: {c['input']}")
    _guard_out_paths(c)
    job = manager.submit("pipeline",
                        "一键全流程（映射→分类→清洗→复查）"
                        + (f" · {len(comps)} 家公司" if comps else "（全部待处理）"),
                        pipeline.run_pipeline, c, model_cfg, comps or None,
                        meta=_meta(["map", "clean", "audit", "idmap"], comps),
                        on_done=_ov_after(lambda j: (j.result or {}).get("touched", [])))
    return {"job_id": job.id}


# ---------------- 历史记录 ----------------
@app.get("/api/history")
def history_list():
    return history.list_all()


class HistDelete(BaseModel):
    ids: list[str]


@app.post("/api/history/delete")
def history_delete(p: HistDelete):
    return {"deleted": history.delete(p.ids)}


# ---------------- 人工匹配 ----------------
@app.get("/api/manual/companies")
def manual_companies():
    root = pipeline.load_config()["unmatched"]
    return {"root": root, "companies": manual.list_companies(root)}


@app.get("/api/manual/products")
def manual_products(company: str):
    root = pipeline.load_config()["unmatched"]
    return {"products": manual.list_products(root, company)}


@app.get("/api/manual/categories")
def manual_categories():
    return manual.categories()


class RematchReq(BaseModel):
    company: str
    rel: str
    category1: str
    category2: str
    is_new: bool = False
    def1: str = ""
    def2: str = ""
    keyword: str = ""      # 归档进分类逻辑库的关键词，空则取产品名


@app.post("/api/manual/rematch")
def manual_rematch(p: RematchReq):
    c = pipeline.load_config()
    try:
        r = manual.rematch(c["unmatched"], p.company, p.rel, p.category1, p.category2,
                           c["mapping_out"], c["process"],
                           p.is_new, p.def1, p.def2, p.keyword)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # 人工匹配改了映射输出 → 这家公司的清洗指纹变了、未匹配数也变了。
    # 不刷新的话总览会一直显示旧状态，用户不知道这家需要重跑清洗。
    overview.refresh_bg([p.company])
    return r


# ---------------- 搁置：确实归不了类的未匹配产品 ----------------
class ShelveReq(BaseModel):
    company: str
    rel: str
    reason: str = ""
    undo: bool = False


@app.post("/api/manual/shelve")
def manual_shelve(p: ShelveReq):
    """把一个未匹配产品标为「无法归类」，不再计入待办（可撤销，数据原地不动）。

    没有这个出口，一家公司只要有一个归不了类的产品就永远到不了「已走完全程」，
    总览的终态从一开始就不可达。
    """
    try:
        r = shelf.unshelve(p.company, p.rel) if p.undo else shelf.shelve(p.company, p.rel, p.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))
    overview.refresh_bg([p.company])
    return r


class ShelveMany(BaseModel):
    company: str
    rels: list[str]
    reason: str = ""
    undo: bool = False


@app.post("/api/manual/shelve_many")
def manual_shelve_many(p: ShelveMany):
    """批量搁置 / 批量取消搁置。一家公司里几十个归不了类的产品，
    逐个点一次弹窗实在太慢。"""
    c = pipeline.load_config()
    try:
        r = (shelf.unshelve_many(p.company, p.rels) if p.undo
             else shelf.shelve_many(p.company, p.rels, p.reason))
    except ValueError as e:
        raise HTTPException(400, str(e))
    overview.refresh_bg([p.company])
    return {**r, "left": manual.count_company(c["unmatched"], p.company)}


@app.get("/api/manual/shelved")
def manual_shelved():
    """全部搁置产品，按公司分组。只返回磁盘上还在的，顺带报告有多少条陈旧记录。"""
    c = pipeline.load_config()
    return shelf.overview(c["unmatched"])


@app.post("/api/manual/shelved/prune")
def manual_shelved_prune():
    """清掉指向已不存在产品的陈旧搁置记录（这类记录会让待办计数偏小）。"""
    c = pipeline.load_config()
    return {"removed": shelf.prune(c["unmatched"])}


# ---------------- 数据处理（合并页：映射→分类→清洗） ----------------
class ProcessRun(BaseModel):
    companies: list[str] = []
    model_id: str | None = None
    use_ai: bool = True
    threads: int = 1
    do_map: bool = True
    do_clean: bool = True          # 分类+清洗（合一）
    do_audit: bool = False
    do_idmap: bool = False
    reclean_all: bool = False
    ai_workers: int = 0            # 0 = 沿用配置里的值
    paths: dict = {}


@app.post("/api/process/run")
def process_run(p: ProcessRun):
    saving = {**p.paths, "model_id": p.model_id or "", "use_ai": p.use_ai,
              "threads": p.threads, "reclean_all": p.reclean_all}
    if p.ai_workers:
        saving["ai_workers"] = p.ai_workers
    c = pipeline.save_config(saving)
    model_cfg = store.get_model(p.model_id) if (p.use_ai and p.model_id) else None
    if p.do_map:
        if p.use_ai and not model_cfg:
            raise HTTPException(400, "已开启 AI 研判但未选择有效的任务模型")
        if not os.path.isdir(c["input"]):
            raise HTTPException(400, f"输入路径不存在: {c['input']}")
        if not p.companies:
            raise HTTPException(400, "请至少勾选一家公司")
    if not (p.do_map or p.do_clean or p.do_idmap):
        raise HTTPException(400, "请至少选择一个步骤")
    if p.do_clean:
        # 判空仍在最前：clean_out 为空串时 os.path.abspath("") 会取到进程的当前工作目录，
        # 旧写法拿它去比 startswith，嵌套检查会被绕过（旧缺陷 #8）。
        # （_guard_out_paths 自己也跳过空值，但这里的报错文案更贴合"你还没配"这个原因。）
        if not c["clean_out"]:
            raise HTTPException(400, "请先在设置页配置清洗输出目录")
    # 只校验本次真的要写的目录：一个用不上的目录配错了，不该拦住无关的步骤。
    gkeys = []
    if p.do_map:
        gkeys += ["mapping_out", "unmatched", "process"]
    if p.do_clean:
        gkeys.append("clean_out")
    if p.do_idmap:
        gkeys.append("final_out")
    _guard_out_paths(c, gkeys)
    if p.do_idmap:
        for k, label in (("company_id", "公司id.json"), ("product_id", "产品id.json")):
            if not os.path.isfile(c[k]):
                raise HTTPException(400, f"已勾选 id 映射，但 {label} 不存在: {c[k] or '未配置'}")
    steps = [k for k, f in (("map", p.do_map), ("clean", p.do_clean),
                            ("audit", p.do_audit), ("idmap", p.do_idmap)) if f]
    title = "数据处理（" + "→".join([n for n, f in
             (("映射", p.do_map), ("分类+清洗", p.do_clean), ("复查", p.do_audit),
              ("id映射", p.do_idmap)) if f]) + "）"
    job = manager.submit("process", title, pipeline.run_process, c, model_cfg,
                        p.companies, p.do_map, p.do_clean, p.do_audit, p.do_idmap,
                        meta=_meta(steps, p.companies),
                        on_done=_ov_after(lambda j: (j.result or {}).get("touched", []) or p.companies))
    return {"job_id": job.id}


# ---------------- 分类详情 ----------------
class CategoryAdd(BaseModel):
    category1: str
    category2: str
    def1: str = ""
    def2: str = ""


@app.post("/api/categories/add")
def category_add(p: CategoryAdd):
    try:
        return manual.add_category(p.category1, p.category2, p.def1, p.def2)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------- 分类逻辑（关键词库） ----------------
@app.get("/api/keywords")
def keywords_list(category1: str = "", category2: str = "", tag: str = ""):
    return {"rows": keywords.list_keywords(category1, category2, tag)}


class KeywordAdd(BaseModel):
    keyword: str
    category1: str
    category2: str
    confidence: float = 0.95
    zh: str = ""
    note: str = ""


@app.post("/api/keywords/add")
def keyword_add(p: KeywordAdd):
    if not manual._is_standard(p.category1, p.category2) and not manual._is_custom(p.category1, p.category2):
        raise HTTPException(400, "分类不存在（标准体系与新增分类注册表中均未找到）")
    try:
        kid = keywords.add_custom(p.keyword, p.category1, p.category2,
                                  confidence=p.confidence, tag=keywords.TAG_MANUAL,
                                  note=p.note, zh=p.zh)
        return {"ok": True, "id": kid}
    except ValueError as e:
        raise HTTPException(400, str(e))


class KeywordSet(BaseModel):
    id: str
    enabled: bool | None = None
    delete: bool = False


@app.post("/api/keywords/set")
def keyword_set(p: KeywordSet):
    try:
        keywords.set_custom(p.id, p.enabled, p.delete)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------- AI 研判复核 ----------------
@app.get("/api/review/companies")
def review_companies():
    c = pipeline.load_config()
    return {"companies": review.list_companies(c["process"])}


@app.get("/api/review/products")
def review_products(company: str):
    c = pipeline.load_config()
    return {"products": review.list_products(c["process"], c["mapping_out"], company)}


class ReviewConfirm(BaseModel):
    company: str
    rel: str
    keyword: str
    confidence: float = 0.95


@app.post("/api/review/confirm")
def review_confirm(p: ReviewConfirm):
    c = pipeline.load_config()
    try:
        r = review.confirm(c["process"], p.company, p.rel, p.keyword, p.confidence)
    except ValueError as e:
        raise HTTPException(400, str(e))
    overview.refresh_bg([p.company])
    return r


class ReviewConfirmAll(BaseModel):
    company: str
    rels: list[str] | None = None
    confidence: float = 0.95


@app.post("/api/review/confirm_all")
def review_confirm_all(p: ReviewConfirmAll):
    c = pipeline.load_config()
    try:
        r = review.confirm_all(c["process"], c["mapping_out"], p.company, p.rels, p.confidence)
    except ValueError as e:
        raise HTTPException(400, str(e))
    overview.refresh_bg([p.company])
    return r


class ReviewModify(BaseModel):
    company: str
    rel: str
    category1: str
    category2: str
    is_new: bool = False
    def1: str = ""
    def2: str = ""


@app.post("/api/review/modify")
def review_modify(p: ReviewModify):
    c = pipeline.load_config()
    try:
        r = review.modify(c["process"], c["mapping_out"], p.company, p.rel,
                          p.category1, p.category2, p.is_new, p.def1, p.def2)
    except ValueError as e:
        raise HTTPException(400, str(e))
    overview.refresh_bg([p.company])
    return r


# ---------------- 统计分析 ----------------
@app.get("/api/stats")
def stats_get(days: int = 7, mode: str = "week"):
    return stats.compute(days, mode)


@app.get("/api/stats/dist")
def stats_dist():
    """**只读缓存，秒开。** 绝不在这里跑扫描。

    以前这里是同步全树 walk（实测 94 秒）：用户点了「重新扫描」只要切一下页面，
    浏览器就把 fetch abort 掉，结果连个落脚处都没有，回来从零再扫。
    现在扫描一律走 POST /api/stats/dist/scan 的后台任务，跑完写缓存。
    """
    d = stats.load_dist_cache()
    if not d:
        return {"ready": False, "pending": stats.dist_pending(),
                "reason": "尚未扫描过，点「重新扫描」建立第一份快照。"}
    # pending = 快照之后有多少家公司变过 —— 界面据此提示"这份快照已经不是最新的"
    return {"ready": True, "pending": stats.dist_pending(), **d}


@app.post("/api/stats/dist/scan")
def stats_dist_scan(full: bool = False):
    """full=false 只重扫标脏的公司（常态，约 15 秒）；true 整棵重走（约 122 秒）。"""
    _guard("dist")
    c = pipeline.load_config()
    n = stats.dist_pending()
    title = ("扫描类别分布（全量）" if full else
             f"扫描类别分布（增量 · {n} 家有改动）" if n else "扫描类别分布（增量）")
    job = manager.start("dist", title, stats.build_dist, c, full)
    return {"job_id": job.id, "full": bool(full), "pending": n}


# ---------------- 统计结果导出（Excel） ----------------
class ExportReq(BaseModel):
    companies: list[str] = []       # 空 = 全部公司
    model_id: str = ""              # 空 = 不做 AI 分析
    format: str = "html"            # html = 自包含报告（可筛选、图表联动）；xlsx = 表格
    mode: str = "week"              # 周期口径，与统计页一致：week / days / all
    days: int = 7                   # mode=days 时的天数
    template: str = "classic"       # classic=分布报告(带下钻) / company=公司覆盖 / period=周期进展


@app.post("/api/stats/export")
def stats_export(p: ExportReq):
    """起一个后台任务。**扫描放在任务里，不能放在这个请求里** ——
    实时扫描要全树 walk 一万多个产品（几分钟），放在请求里前端必然超时，
    连 job_id 都拿不到，看起来就是"点了没反应"。"""
    _guard("export")
    model_cfg = store.get_model(p.model_id) if p.model_id else None
    if p.model_id and not model_cfg:
        raise HTTPException(400, "所选任务模型不存在，请到配置页确认")
    kind = "html" if (p.format or "html").lower() != "xlsx" else "xlsx"
    c = pipeline.load_config()
    range_label = stats.resolve_range(p.mode, p.days)[2]
    tpl = p.template if kind == "html" else "classic"   # xlsx 只有一种
    tname = {"company": "公司覆盖报告", "period": "周期进展报告"}.get(
        tpl, "统计报告(HTML)" if kind == "html" else "统计表格(Excel)")
    job = manager.start("export",
                        f"导出{tname}（{range_label} · {len(p.companies) or '全部'} 家公司）",
                        _export_job, c, list(p.companies), model_cfg, kind, p.mode, p.days, tpl)
    return {"job_id": job.id, "format": kind, "template": tpl, "range_label": range_label}


def _export_job(job, cfg, companies, model_cfg, kind, mode="week", days=7, template="classic"):
    # 周期进展报告的数据全部来自映射记忆，用不着扫描目录树 —— 那个全树 walk 要几分钟。
    # 周报是每周五都要出的东西，不能每次都干等，所以这条路径直接跳过扫描。
    if kind == "html" and template == "period":
        job.step(0, 3)
        job.log("周期进展报告只读映射记忆，跳过全树扫描（省下几分钟）")
        reg = registry.load()
        if not reg:
            raise ValueError("映射记忆是空的，还没有任何公司完成过映射")
        _, range_label = stats.companies_in_range(mode, days)
        note = f"统计周期：{range_label}"
        return reports2.run_export(job, "period", model_cfg, note, reg=reg,
                                   ai_payload=stats.compute(days, mode))

    job.step(0, 4)
    job.set_current("正在实时扫描映射输出与额外来源 …")
    job.log("实时扫描（要全树 walk 一万多个产品，可能要几分钟）…")
    dist = stats.scan_distribution(cfg["mapping_out"], cfg.get("extra_sources"), cfg["company_id"])
    job.log(f"扫描完成：{len(dist['companies'])} 家公司 / {dist['total']} 个产品")
    if not dist["companies"]:
        raise ValueError("没有可导出的统计数据")
    bad = [x for x in companies if x not in dist["by_company"]]
    if bad:
        raise ValueError("以下公司不在统计范围内: " + "、".join(bad[:5]))

    # ---- 周期收窄 ----
    # 扫描结果是"当前目录树的真实状态"，本身没有时间；周期靠映射记忆的时间戳筛公司名，
    # 用的是统计页同一个 resolve_range，所以导出的家数与页面上的周期数字一致。
    in_range, range_label = stats.companies_in_range(mode, days)
    if in_range is not None:
        known = set(dist["by_company"])
        base = set(companies) if companies else known
        companies = sorted(base & in_range)
        job.log(f"周期筛选：{range_label} —— 记忆内 {len(in_range)} 家，"
                f"其中 {len(companies)} 家有扫描数据可导出")
        missed = len(in_range & known) - len(companies)
        if companies and missed > 0:
            job.log(f"（另有 {missed} 家在周期内但不在本次所选范围，已排除）")
        if not companies:
            raise ValueError(f"{range_label} 内没有可导出的公司 —— "
                             "该周期还没有公司完成映射，或所选公司都不在这个周期内")
    else:
        job.log(f"周期筛选：{range_label}（不限时间）")

    note = f"统计周期：{range_label}｜数据来源：" + "；".join(
        f"{s['label']} {s['products']} 个产品" for s in dist.get("sources", []) if s["products"])
    job.step(1)
    if kind == "html" and template == "company":
        return reports2.run_export(job, "company", model_cfg, note, dist=dist,
                                   companies=companies, reg=registry.load(),
                                   ai_payload=report._payload_for_ai(dist, companies))
    fn = htmlreport.run_export_html if kind == "html" else report.run_export
    return fn(job, dist, companies, model_cfg, note)


@app.get("/api/stats/export/{jid}")
def stats_export_download(jid: str):
    j = manager.get(jid)
    if not j or j.status != "done" or not j.result:
        raise HTTPException(404, "导出任务不存在或尚未完成")
    path = j.result.get("file")
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "导出文件已不存在，请重新导出")
    is_html = j.result.get("format") == "html"
    return FileResponse(path, filename=j.result.get("name", "统计结果"),
                        media_type="text/html; charset=utf-8" if is_html else
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------- 按公司彻底移除（抓错公司的出口） ----------------
@app.get("/api/company/purge/preview")
def company_purge_preview(company: str, with_input: bool = False):
    """只看不动：这家公司会被删掉哪些目录、多少产品、命中哪几份记忆。

    前端强制先调它再调 POST —— 这是全平台唯一一个会成片删除数据的入口，
    不给"删之前看清楚"的机会是不行的。
    with_input=true 时把输入目录（原始爬取数据）也算进删除范围。
    """
    try:
        return purge.preview(pipeline.load_config(), company, with_input)
    except ValueError as e:
        raise HTTPException(400, str(e))


class PurgeReq(BaseModel):
    company: str
    confirm: str = ""            # 必须与 company 一字不差，防手滑
    with_input: bool = False     # 连输入目录（原始爬取数据）一起删


@app.post("/api/company/purge")
def company_purge(p: PurgeReq):
    """删除该公司的平台产出与记忆；`with_input=true` 时连原始爬取数据一起删。

    走 manager.submit 排队而不是同步执行：删几千个产品目录在 /mnt/d 上是分钟级的，
    放在请求里前端必然超时，看起来就是"点了没反应"，而它其实已经删到一半了。
    """
    if (p.confirm or "").strip() != (p.company or "").strip():
        raise HTTPException(400, "请在确认框里一字不差地输入公司名")
    cfg = pipeline.load_config()
    try:
        pv = purge.preview(cfg, p.company, p.with_input)   # 先验一遍，错的公司名不该进队列
    except ValueError as e:
        raise HTTPException(400, str(e))
    job = manager.submit("purge",
                         f"移除公司 {p.company}（{pv['products']} 个产品"
                         + ("，含原始数据）" if p.with_input else "）"),
                         purge.execute, cfg, p.company, p.with_input,
                         meta=_meta(["purge"], [p.company]))
    return {"job_id": job.id, "products": pv["products"]}


class AnalyzeReq(BaseModel):
    days: int = 7
    mode: str = "week"
    model_id: str


@app.post("/api/stats/analyze")
def stats_analyze(p: AnalyzeReq):
    cfg = store.get_model(p.model_id)
    if not cfg:
        raise HTTPException(400, "请先在配置页创建任务模型")
    c = pipeline.load_config()
    r = stats.analyze(cfg, p.days, c["mapping_out"], c.get("extra_sources"), c["company_id"],
                      p.mode)
    if r["error"]:
        raise HTTPException(502, "AI 分析调用失败: " + r["error"])
    return r


# ---------------- 前端静态资源 ----------------
@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")