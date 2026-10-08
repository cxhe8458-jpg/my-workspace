# -*- coding: utf-8 -*-
"""从上一代平台迁移审核记录与交付台账。

    python3 tools/migrate.py            # 只出对账报告，不写任何东西
    python3 tools/migrate.py --apply    # 确认无误后真正写入

状态映射（已与使用者确认）：
    通过 / 人工修改      → 通过
    有错误 / 待复检      → 有问题（旧错误原因文字并入备注）
    未检                → 未检
交付台账 58 家 → 归档台账；旧历史原样保留。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.infra import paths, store                      # noqa: E402

OLD_ROOT = Path("/mnt/d/公司数据自动化检验平台/检验数据")
OLD_RECORDS = OLD_ROOT / "records"
OLD_LEDGER = OLD_ROOT / "knowledge/交付记录.json"

MAP = {"通过": "通过", "人工修改": "通过", "有错误": "有问题", "待复检": "有问题", "未检": "未检"}
# 单元测试留下的假记录，不是业务数据
SKIP_COMPANIES = {"__UT__"}


def _strip_suffix(s: str) -> str:
    """归档后有人把文件夹尾部的 _ / _1 去掉了，靠这个把台账和实际目录对上。"""
    return re.sub(r"[_\s]+\d*$", "", s).strip()


def repair_target(company: str, target: str):
    """返回 (实际路径 or "", 修复说明)。目录真找不到就保留原值，历史记录不能丢。"""
    t = Path(target) if target else None
    if t and t.is_dir():
        return str(t), ""
    arc = paths.archive_dir()
    if not arc.is_dir():
        return target, "归档根目录不存在"
    same = arc / company
    if same.is_dir():
        return str(same), "按公司名重新定位"
    want = _strip_suffix(company)
    for x in sorted(arc.iterdir()):
        if x.is_dir() and _strip_suffix(x.name) == want:
            return str(x), f"归档后被改名：{company} → {x.name}"
    return target, "目录已不在原处（记录保留）"


def _reason_text(rec: dict) -> str:
    """把旧的结构化错误（类别 + 原因数组 + 备注）压成一行文字，塞进新的 note。"""
    bits = []
    for e in rec.get("errors") or []:
        cat = e.get("category", "")
        reasons = "；".join(e.get("reasons") or [])
        note = e.get("note", "")
        bits.append("".join([cat, f"（{reasons}）" if reasons else "",
                             f" {note}" if note else ""]).strip())
    if not bits:
        for e in reversed(rec.get("error_log") or []):
            if e.get("reasons"):
                bits.append(";".join(e["reasons"]))
                break
    return "、".join(b for b in bits if b)


def convert():
    companies, stats = {}, {"公司": 0, "产品": 0, "通过": 0, "有问题": 0, "未检": 0, "未识别": 0}
    if not OLD_RECORDS.is_dir():
        print(f"⚠ 找不到旧记录目录：{OLD_RECORDS}")
        # 四个返回值必须和正常路径一致，否则调用方解包直接 ValueError
        return companies, stats, [], []
    skipped = []
    for f in sorted(OLD_RECORDS.glob("*.json")):
        if f.stem in SKIP_COMPANIES:
            skipped.append(f.stem)
            continue
        try:
            old = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"⚠ 跳过 {f.name}（解析失败: {e}）")
            continue
        if not isinstance(old, dict) or not old:
            continue
        out = {}
        for rel, rec in old.items():
            if not isinstance(rec, dict):
                continue
            src = rec.get("status", "未检")
            new = MAP.get(src)
            if new is None:
                stats["未识别"] += 1
                new = "未检"
            note = _reason_text(rec) if new == "有问题" else ""
            if new == "有问题" and not note:
                note = f"（迁移自旧状态「{src}」，原因未记录）"
            out[rel] = {"status": new, "note": note,
                        "html": None,                     # 首次打开产品时按磁盘补上
                        "updated": rec.get("updated", ""),
                        "history": (rec.get("history") or []) +
                                   [{"time": store.now(), "event": f"从旧平台迁移（原状态：{src}）"}]}
            stats[new] += 1
            stats["产品"] += 1
        if out:
            companies[f.stem] = out
            stats["公司"] += 1

    ledger, repairs = [], []
    if OLD_LEDGER.is_file():
        try:
            for e in json.loads(OLD_LEDGER.read_text(encoding="utf-8")):
                c = e.get("company", "")
                target, why = repair_target(c, e.get("target", ""))
                if why:
                    repairs.append((c, target, why))
                ledger.append({"company": c, "target": target,
                               "total": e.get("total", 0),
                               "counts": {"未检": 0, "通过": e.get("total", 0), "有问题": 0},
                               "time": e.get("time", ""), "migrated": True})
        except Exception as e:
            print(f"⚠ 交付台账解析失败: {e}")
    stats["跳过的测试记录"] = skipped
    return companies, stats, ledger, repairs


def report(companies, stats, ledger, repairs):
    print("=" * 66)
    print("迁移对账报告（当前为试运行，未写入任何数据）")
    print("=" * 66)
    print(f"  公司 {stats['公司']} 家 / 产品 {stats['产品']} 条")
    print(f"    通过   {stats['通过']}")
    print(f"    有问题 {stats['有问题']}")
    print(f"    未检   {stats['未检']}")
    if stats["未识别"]:
        print(f"    ⚠ 无法识别的旧状态 {stats['未识别']} 条，按「未检」处理")
    print(f"  归档台账 {len(ledger)} 家")

    if stats.get("跳过的测试记录"):
        print(f"  已跳过测试残留记录：{'、'.join(stats['跳过的测试记录'])}")

    renamed = [r for r in repairs if "改名" in r[2] or "重新定位" in r[2]]
    lost = [r for r in repairs if r not in renamed]
    if renamed:
        print(f"  ✔ 归档目录被改名、已自动对上：{len(renamed)} 家")
        for c, t, why in renamed:
            print(f"      - {why}")
    if lost:
        print(f"  ⚠ 归档目录已不在原处：{len(lost)} 家（台账记录保留，只是点不开）")
        print(f"      {'、'.join(c for c, _t, _w in lost)}")
        print("      → 这些多半被下游流程搬走或改名了，不影响新平台使用")

    data_dir = paths.data_dir()
    conflicts = [e["company"] for e in ledger
                 if e["company"] and (data_dir / e["company"]).is_dir()]
    if conflicts:
        print(f"  ⚠ 台账说已归档、但库内又有同名文件夹（两份数据各自漂移）：{len(conflicts)} 家")
        for c in conflicts:
            print(f"      - {c}")
        print("    → 新平台以库内那份为准（当作未归档），台账条目保留并在归档页标红")

    existing = list(paths.RECORDS_DIR.glob("*.json"))
    if existing:
        print(f"  ⚠ 新平台已有 {len(existing)} 个记录文件，--apply 会按公司整份覆盖")
    print("=" * 66)
    print("确认无误后执行：python3 tools/migrate.py --apply")


def apply(companies, ledger):
    paths.ensure_dirs()
    for company, recs in companies.items():
        store.write_json(paths.RECORDS_DIR / f"{company}.json", recs)
    store.write_json(paths.LEDGER_FILE, ledger)
    print(f"✔ 已写入 {len(companies)} 家公司的审核记录 → {paths.RECORDS_DIR}")
    print(f"✔ 已写入归档台账 {len(ledger)} 条 → {paths.LEDGER_FILE}")


if __name__ == "__main__":
    c, s, l, rep = convert()
    if "--apply" in sys.argv:
        apply(c, l)
    else:
        report(c, s, l, rep)
