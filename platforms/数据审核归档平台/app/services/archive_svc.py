# -*- coding: utf-8 -*-
"""归档业务：整个公司文件夹移动到归档路径，并记台账。

门槛（用户确认过的规则）：**公司下所有产品必须都是「通过」**才允许归档。
有未检或有问题的一律拦下，并把拦下的原因和具体产品带回给前端，
让人能一键直达那些产品，而不是只看到一句"不能归档"。
"""
import os
import shutil
import time
from pathlib import Path

from ..infra import paths, scanner, store
from . import review


class ArchiveError(Exception):
    pass


# ---------- 目录搬移 ----------
# ⚠ **不要退回去直接用 `shutil.move()`。**
# 它在 `os.rename()` 失败后会自动退化成 copytree + rmtree，而 rmtree 在 drvfs 上
# 经常被 Windows 侧的句柄挡住（资源管理器预览、杀毒软件扫描都会占住目录几秒）。
# 一旦它在删最后几个空目录时抛 PermissionError，异常会一路冒到接口层，于是留下：
#   数据已经完整躺在归档区 + 源目录剩几个空壳 + **台账没写**
# 这个状态最难受的地方在于它两边都不算：公司不在台账里所以不是「已归档」，
# 源目录还在所以仍然出现在审核列表上（0 个产品的空卡片），
# 而再点一次归档会撞上 `dest.exists()` 那句"目标目录已存在同名文件夹"，永远归档不掉。
# 实测 2026-08-12「杭州微影传感科技有限公司」：归档区 1678 个条目齐全、133 个产品
# 与审核记录一一对上，库内只剩 2 个空目录，台账无记录。
#
# 关键认识：「移动成功后才写台账」里的**「成功」指的是数据到位，不是源目录消失**。
# 源目录删不干净只是需要提示的瑕疵，不该让整笔归档回滚成"什么都没发生"的假象。

def _has_files(p: Path) -> bool:
    """目录里还有没有真正的文件（只剩空目录 = 无害的壳）。"""
    try:
        return any(f.is_file() for f in p.rglob("*"))
    except OSError:
        return True          # 读不动就按"有内容"处理，宁可保守


def _rmtree_retry(p: Path, tries: int = 4) -> str:
    """删源目录，失败重试。返回残留说明（空串 = 删干净了）。"""
    last = None
    for i in range(tries):
        try:
            shutil.rmtree(p)
            return ""
        except OSError as e:                 # PermissionError 也是 OSError
            last = e
            time.sleep(0.3 * (i + 1))
    if not p.exists():
        return ""
    if _has_files(p):
        return f"源目录仍有文件未能删除（{last}）——请手工核对后再删，别直接删掉"
    return f"源目录只剩空目录未能删除（{last}），数据已完整归档，可手工删掉这个空壳"


def _move_company(src: Path, dest: Path) -> str:
    """把公司目录整体搬到 dest。返回残留说明（空串 = 干净搬完）。

    抛异常 = **数据没到位**，调用方必须当作归档失败处理。
    正常返回 = 数据已在 dest，台账必须写，哪怕带着残留说明。
    """
    try:
        os.rename(src, dest)                 # 同一挂载点上是原子的，最快路径
        return ""
    except OSError:
        pass
    # rename 走不通（被占用/跨设备）：先完整复制，确认到位，再删源
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return _rmtree_retry(src)


def eligibility(company: str) -> dict:
    """能不能归档 + 不能的原因 + 卡住的产品清单（最多带 20 条给前端展示）。"""
    if scanner.is_archived(company):
        return {"ok": False, "reason": "该公司已经归档", "blockers": [], "counts": {}}
    prods, recs, counts = review.count_products(company)
    if not prods:
        return {"ok": False, "reason": "该公司没有产品，不能归档", "blockers": [], "counts": counts}
    blockers = []
    for p in prods:
        st = review.status_of(recs.get(p["path"]))
        if st != review.PASSED:
            blockers.append({"path": p["path"], "name": p["name"], "status": st,
                             "note": (recs.get(p["path"]) or {}).get("note", "")})
    if blockers:
        parts = []
        if counts[review.UNCHECKED]:
            parts.append(f"{counts[review.UNCHECKED]} 个未检")
        if counts[review.PROBLEM]:
            parts.append(f"{counts[review.PROBLEM]} 个有问题")
        return {"ok": False, "reason": "还有 " + "、".join(parts) + "，全部通过后才能归档",
                "blockers": blockers[:20], "blocker_total": len(blockers), "counts": counts}
    return {"ok": True, "reason": "", "blockers": [], "counts": counts}


def archive(company: str, target_dir: str = "") -> dict:
    """把公司文件夹整体移到归档路径。移动成功后才写台账——
    反过来写会出现"台账说归档了、文件还在原地"的错位状态。"""
    src = paths.data_dir() / company
    if "/" in company or "\\" in company or company in (".", "..") or not src.is_dir():
        raise ArchiveError("公司目录不存在")

    tdir = Path(target_dir.strip()) if target_dir.strip() else paths.archive_dir()
    try:
        tdir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise ArchiveError(f"归档路径不可用: {e}")
    dest = tdir / company

    # ---- 残局补完 ----
    # 上一次归档搬到一半：数据已在归档区、源目录只剩空壳、台账没写。
    # 用户此刻点「归档」，想要的就是把这件事做完；直接回一句"目标目录已存在"
    # 会让这家公司永远卡住 —— 它既进不了台账，又因为源目录还在而赖在审核列表上。
    # 只在**源目录确实没有任何文件**且**台账里也确实没有它**时才认定是残局；
    # 两份都有真数据时绝不自作主张合并，那是要人来判的。
    #
    # ⚠ **这一段必须排在 eligibility() 之前。** 残局里的源目录已经是空壳，
    # 门槛检查会以"该公司没有产品，不能归档"把它拦下 —— 于是这家公司永远补不完。
    # （实测：把它放在门槛之后，「杭州微影传感科技有限公司」照样归档不掉。
    #  自检里当时能过是假阳性：内存索引还缓存着搬走前的产品数，绕过了门槛。）
    if dest.is_dir() and not store.ledger_entry(company) and not _has_files(src):
        note = _rmtree_retry(src)
        # 此处必须现场数一遍 dest：源目录已经空了，内存索引里那份是 0 个产品，
        # 拿它写台账会记下一个假的 total。这是**唯一**允许在请求里走目录树的地方，
        # 因为它一家公司只会发生一次。
        total = sum(1 for _dp, _dns, fns in os.walk(dest) if "info.json" in fns)
        recs = store.load_records(company)
        counts = {s: 0 for s in review.STATUSES}
        for rec in recs.values():
            counts[review.status_of(rec)] += 1
        store.add_ledger({"company": company, "target": str(dest),
                          "total": total, "counts": counts})
        store.save_config({"archive_dir": str(tdir)})
        scanner.remove_company(company)
        scanner.scan_company(company)
        return {"company": company, "target": str(dest), "total": total,
                "note": "上一次归档只差最后一步（源目录没删净、台账没写），已补完。"
                        + (f" {note}" if note else "")}

    # ---- 正常归档 ----
    elig = eligibility(company)
    if not elig["ok"]:
        raise ArchiveError(elig["reason"])
    if dest.exists():
        # 走到这里说明不是残局：要么台账里已有它，要么源目录里还有真文件。
        # 两份都有数据时绝不自作主张合并或覆盖，交给人判断。
        raise ArchiveError(
            f"归档路径下已存在同名文件夹，未做任何改动：{dest}"
            "（两边都有数据，请手工核对后再决定保留哪一份）")

    prods, _recs, counts = review.count_products(company)
    try:
        note = _move_company(src, dest)
    except Exception as e:
        # 抛异常 = 数据没到位。把可能只复制了一半的目标清掉，源目录原样不动，
        # 让这次归档干净地"什么都没发生"，用户重试才不会撞上半份残留。
        if dest.exists():
            try:
                shutil.rmtree(dest)
            except OSError:
                pass
        raise ArchiveError(f"归档失败，数据仍在原处：{e}")

    # 走到这里数据一定已经在 dest —— 台账必须写，哪怕源目录没删干净。
    # 反过来（因为删不掉空壳就不写台账）正是这次事故的成因。
    store.add_ledger({"company": company, "target": str(dest),
                      "total": len(prods), "counts": counts})
    store.save_config({"archive_dir": str(tdir)})
    scanner.remove_company(company)      # 根路径变了，旧索引作废
    scanner.scan_company(company)        # 立刻按归档路径重扫，复查页秒开
    out = {"company": company, "target": str(dest), "total": len(prods)}
    if note:
        out["note"] = note               # 归档已生效，只是提醒源目录有壳
    return out


def revoke(company: str) -> dict:
    """撤回归档：搬回库内并删台账——这是修改已归档数据的唯一正规入口。"""
    entry = store.ledger_entry(company)
    if not entry:
        raise ArchiveError("归档台账里没有这家公司")
    src = Path(entry.get("target", ""))
    if not src.is_dir():
        raise ArchiveError(f"归档路径下找不到该公司文件夹：{entry.get('target')}")
    dest = paths.data_dir() / company
    if dest.exists():
        raise ArchiveError("库内已存在同名文件夹，无法撤回（请先处理重名）")
    shutil.move(str(src), str(dest))
    store.remove_ledger(company)
    scanner.remove_company(company)
    scanner.scan_company(company)
    return {"company": company, "back_to": str(dest)}


def ledger() -> list:
    """台账 + 每条的实时校验：目标目录还在不在、是否与库内同名文件夹冲突。"""
    out = []
    for e in store.load_ledger():
        c = e.get("company", "")
        t = Path(e.get("target", ""))
        # 库内又出现同名文件夹 = 两份数据各自漂移，必须让人看见。
        # 但只剩空目录的壳不算漂移（归档时 rmtree 被 Windows 句柄挡住的残渣），
        # 报成冲突会让人以为数据有两份、白跑一趟去核对。判空口径与 scanner 一致。
        back = (paths.data_dir() / c) if c else None
        out.append({**e,
                    "target_exists": t.is_dir(),
                    "conflict": bool(back and back.is_dir() and scanner._has_any_file(back))})
    # 按归档时间倒序。add_ledger 本来就把新条目插在最前，但迁移进来的历史条目
    # 沿用旧平台的顺序，两批混在一起就没有次序可言了；没时间戳的沉到最后。
    out.sort(key=lambda e: e.get("time") or "", reverse=True)
    return out
