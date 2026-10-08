# -*- coding: utf-8 -*-
"""审核业务：三态判定、公司聚合、一键通过。

状态模型只有三态，没有子状态机：
    未检 → 通过 | 有问题（有问题带一行自由备注）
公司阶段由产品聚合得出，不单独存储，避免两处状态互相打架。
"""
from pathlib import Path

from ..infra import scanner, store

UNCHECKED, PASSED, PROBLEM = "未检", "通过", "有问题"
STATUSES = (UNCHECKED, PASSED, PROBLEM)


class ReviewError(Exception):
    """业务规则不允许的操作，由 API 层转成 400。"""


def status_of(rec) -> str:
    return (rec or {}).get("status") or UNCHECKED


def count_products(company: str):
    """返回 (products, recs, counts)。counts 覆盖全部三态，前端可直接用。"""
    prods = scanner.scan_company(company)
    recs = store.load_records(company)
    counts = {UNCHECKED: 0, PASSED: 0, PROBLEM: 0}
    for p in prods:
        counts[status_of(recs.get(p["path"]))] += 1
    return prods, recs, counts


def stage_of(company: str, counts: dict, total: int, archived=None) -> str:
    """未审核 / 审核中 / 可归档 / 已归档 —— 公司卡片的分组依据。

    archived 由调用方传入，避免 company_summary 里一家公司算两遍归档状态。
    """
    if scanner.is_archived(company) if archived is None else archived:
        return "已归档"
    if not total:
        return "未审核"
    if counts[UNCHECKED] == total:
        return "未审核"
    if counts[UNCHECKED] == 0 and counts[PROBLEM] == 0:
        return "可归档"
    return "审核中"


def company_summary(company: str) -> dict:
    if not scanner.company_ready(company):
        return {"name": company, "ready": False, "total": 0,
                "counts": {s: 0 for s in STATUSES}, "stage": "未审核",
                "archived": False, "coverage": 0, "no_html": 0}
    prods, recs, counts = count_products(company)
    total = len(prods)
    archived = scanner.is_archived(company)
    entry = store.ledger_entry(company) if archived else None
    return {
        "name": company,
        "ready": True,
        "total": total,
        "counts": counts,
        "stage": stage_of(company, counts, total, archived),
        "archived": archived,
        "archive_time": (entry or {}).get("time", ""),
        "archive_target": (entry or {}).get("target", ""),
        "coverage": round((total - counts[UNCHECKED]) / total * 100, 1) if total else 0,
        # 仅提示用，不阻断任何操作
        "no_html": sum(1 for p in prods if not p["has_html"]),
    }


def assert_writable(company: str):
    if scanner.is_archived(company):
        raise ReviewError("该公司已归档，处于只读复查状态；要修改请先「撤回归档」")


def mark(rel_path: str, status: str, note: str = "") -> dict:
    if status not in (PASSED, PROBLEM):
        raise ReviewError("状态只能是 通过 或 有问题")
    p = scanner.get_product(rel_path)
    if not p:
        raise ReviewError("产品不存在")
    company = Path(rel_path).parts[0]
    assert_writable(company)
    note = (note or "").strip()
    if status == PROBLEM and not note:
        raise ReviewError("标记「有问题」必须填写说明")
    event = f"标记{status}" + (f"：{note}" if note else "")
    rec = store.save_record(company, rel_path, {
        "status": status,
        "note": note if status == PROBLEM else "",
        "html": p["has_html"],
        "_event": event,
    })
    return rec


def pass_all(company: str) -> dict:
    """一键通过：只把「未检」置为通过，已标「有问题」的保持不动。"""
    assert_writable(company)
    prods, recs, _ = count_products(company)
    targets = {p["path"]: p for p in prods if status_of(recs.get(p["path"])) == UNCHECKED}
    if not targets:
        return {"passed": 0, "skipped_problem": sum(
            1 for p in prods if status_of(recs.get(p["path"])) == PROBLEM)}
    updates = {rel: {"status": PASSED, "note": "", "html": p["has_html"]}
               for rel, p in targets.items()}
    n = store.save_records_bulk(company, updates, "一键通过（批量）")
    return {"passed": n, "skipped_problem": sum(
        1 for p in prods if status_of(recs.get(p["path"])) == PROBLEM)}


def next_unchecked(company: str, after: str = ""):
    """按目录顺序找下一个未检产品；after 之后没有就从头找。"""
    prods, recs, _ = count_products(company)
    todo = [p for p in prods if status_of(recs.get(p["path"])) == UNCHECKED]
    if not todo:
        return None
    if after:
        later = [p for p in todo if p["path"] > after]
        if later:
            return later[0]
    return todo[0]
