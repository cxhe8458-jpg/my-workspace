# -*- coding: utf-8 -*-
"""归档相关路由。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..infra import paths, store
from ..services import archive_svc, review

router = APIRouter(prefix="/api/archive")


@router.get("/ledger")
def ledger():
    return {"entries": archive_svc.ledger(), "archive_dir": str(paths.archive_dir())}


@router.get("/candidates")
def candidates():
    """归档页列表：每家公司带上能否归档与卡住的原因。"""
    from ..infra import scanner
    out = []
    for c in scanner.known_companies():
        s = review.company_summary(c)
        s["eligibility"] = {"ok": False, "reason": "已归档"} if s["archived"] else archive_svc.eligibility(c)
        out.append(s)
    return out


class ArchiveBody(BaseModel):
    companies: list
    target: str = ""


@router.post("")
def do_archive(body: ArchiveBody):
    """逐个归档，单个失败不影响其余；结果一条条回报，让人知道谁成谁败。"""
    results, moved = [], 0
    for c in body.companies:
        try:
            r = archive_svc.archive(c, body.target)
            moved += 1
            # note = 归档已生效、但有需要人知道的瑕疵（源目录没删净 / 补完了上次的残局）。
            # 不带上来的话，用户会以为一切干净，而磁盘上还留着个空壳。
            msg = f"已归档到 {r['target']}"
            if r.get("note"):
                msg += f"　⚠ {r['note']}"
            results.append({"company": c, "ok": True, "msg": msg})
        except archive_svc.ArchiveError as e:
            results.append({"company": c, "ok": False, "msg": str(e)})
    return {"ok": True, "moved": moved, "results": results}


class CompanyBody(BaseModel):
    company: str


@router.post("/revoke")
def revoke(body: CompanyBody):
    try:
        return {"ok": True, **archive_svc.revoke(body.company)}
    except archive_svc.ArchiveError as e:
        raise HTTPException(400, str(e))


class ConfigBody(BaseModel):
    archive_dir: str = ""
    data_dir: str = ""


@router.put("/config")
def set_config(body: ConfigBody):
    patch = {k: v.strip() for k, v in body.dict().items() if v and v.strip()}
    if not patch:
        raise HTTPException(400, "没有要修改的配置")
    return {"ok": True, "config": store.save_config(patch)}
