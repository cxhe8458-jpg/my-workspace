# -*- coding: utf-8 -*-
"""审核相关路由。HTTP 层只做参数校验与调用，业务规则一律在 services 里。"""
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..infra import scanner, store
from ..services import fixes, review
from ..services.archive_svc import eligibility

router = APIRouter(prefix="/api")


def _wrap(fn, *a, **kw):
    """把业务异常翻成 400，其余交给全局异常处理器（会带真实原因 + 落日志）。"""
    try:
        return fn(*a, **kw)
    except (review.ReviewError, fixes.FixError) as e:
        raise HTTPException(400, str(e))


# ---------- 公司 / 产品 ----------

@router.get("/companies")
def companies():
    return [review.company_summary(c) for c in scanner.known_companies()]


@router.get("/company/{name}")
def company_detail(name: str):
    prods = scanner.scan_company(name)
    if not prods:
        raise HTTPException(404, "公司不存在或无产品")
    recs = store.load_records(name)
    items = [{
        "path": p["path"], "name": p["name"], "categories": p["categories"],
        "status": review.status_of(recs.get(p["path"])),
        "note": (recs.get(p["path"]) or {}).get("note", ""),
        "param_type": p["param_type"], "has_pdf": bool(p["pdfs"]),
        "has_html": p["has_html"], "url": p["url"],
    } for p in prods]
    summary = review.company_summary(name)
    return {"name": name, "products": items, "summary": summary,
            "archived": summary["archived"], "eligibility": eligibility(name)}


@router.get("/product")
def product_detail(path: str):
    p = scanner.get_product(unquote(path))
    if not p:
        raise HTTPException(404, "产品不存在")
    company = Path(p["path"]).parts[0]
    # info.json 里参数/图片/PDF 之外的字段（产品描述、应用、特点…）现读现取，
    # 不进内存索引——理由见 scanner.extra_info 的注释
    return {"product": p,
            "record": store.load_records(company).get(p["path"], {}),
            "archived": scanner.is_archived(company),
            "info": scanner.extra_info(p["path"], p["name"])}


@router.post("/refresh")
def refresh(company: str = ""):
    if company:
        _, new, changed = scanner.refresh_company(company)
        scanner.save_snapshot()          # 全量刷新在 refresh_all 里已存，单公司这里补上
        return {"ok": True, "total_new": new, "total_changed": changed}
    diffs = scanner.refresh_all()
    return {"ok": True, "detail": diffs,
            "total_new": sum(d["new"] for d in diffs.values()),
            "total_changed": sum(d["changed"] for d in diffs.values())}


# ---------- 标记 ----------

class MarkBody(BaseModel):
    path: str
    status: str
    note: str = ""


@router.post("/mark")
def mark(body: MarkBody):
    rec = _wrap(review.mark, body.path, body.status, body.note)
    company = Path(body.path).parts[0]
    nxt = review.next_unchecked(company, body.path)
    return {"ok": True, "record": rec, "summary": review.company_summary(company),
            "next": nxt["path"] if nxt else None}


class PassAllBody(BaseModel):
    company: str


@router.post("/company/pass_all")
def pass_all(body: PassAllBody):
    r = _wrap(review.pass_all, body.company)
    return {"ok": True, **r, "summary": review.company_summary(body.company)}


# ---------- 数据修正 ----------

class UploadBody(BaseModel):
    path: str
    kind: str
    data: str
    replace: str = ""


@router.post("/fix/upload")
def fix_upload(body: UploadBody):
    return {"ok": True, **_wrap(fixes.upload_file, body.path, body.kind, body.data, body.replace)}


class FileBody(BaseModel):
    path: str
    filename: str


@router.post("/fix/delete_file")
def fix_delete(body: FileBody):
    return {"ok": True, **_wrap(fixes.delete_file, body.path, body.filename)}


class RestoreBody(BaseModel):
    path: str
    filename: str
    backup: str


@router.post("/fix/restore_file")
def fix_restore(body: RestoreBody):
    return {"ok": True, **_wrap(fixes.restore_file, body.path, body.filename, body.backup)}


class ReclassBody(BaseModel):
    path: str
    filename: str
    to: str


@router.post("/fix/reclassify")
def fix_reclassify(body: ReclassBody):
    return {"ok": True, **_wrap(fixes.reclassify, body.path, body.filename, body.to)}


class FieldBody(BaseModel):
    path: str
    field: str
    value: str = ""


@router.put("/fix/field")
def fix_field(body: FieldBody):
    return {"ok": True, **_wrap(fixes.edit_field, body.path, body.field, body.value)}


# ---------- 产品增删 ----------

class CreateBody(BaseModel):
    company: str
    category: str = ""
    name: str
    url: str = ""


@router.post("/product/create")
def product_create(body: CreateBody):
    return {"ok": True, **_wrap(fixes.create_product, body.company, body.category, body.name, body.url)}


class PathBody(BaseModel):
    path: str


@router.post("/product/delete")
def product_delete(body: PathBody):
    return {"ok": True, "undo": _wrap(fixes.delete_product, body.path)}


class IdBody(BaseModel):
    id: str


@router.post("/product/restore")
def product_restore(body: IdBody):
    return {"ok": True, **_wrap(fixes.restore_product, body.id)}
