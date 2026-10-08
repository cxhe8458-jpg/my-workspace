# -*- coding: utf-8 -*-
"""按公司彻底移除：把一家「抓错了」的公司从平台里清干净。

用途：爬取阶段抓错了公司（抓成了同名的另一家、或整批数据本身作废），它已经跑过
②③④⑤ 并在四棵输出树和五份记忆里都留了痕。手工删要在六个地方各找一遍，漏一处
就会以幽灵的形态回到总览上 —— 比如只删了目录没删记忆，下次「重新扫描」它照样冒出来；
只删了记忆没删目录，`registry.clean_status` 一 walk 又把它捞回来。

**删什么**（2026-08-04 用户拍板）：

    删  data/ 五份记忆里该公司的键 + 类别分布分片 + 总览缓存行
    删  映射输出 / 未匹配 / 清洗后(三个类别) / 最终数据(按公司id) / 过程记录
    删  输入目录 <input>/<公司>/          ← **仅当显式传 with_input=True**

`with_input` 默认关闭，因为两种场景的正确答案相反：

- **抓错了但还想留一手** → 关。删掉平台产出、留着源数据，随时能重新跑回来。
  代价是只要源目录还在，下次跑 ② 这家公司就会整个回来，界面上必须把这句话说清楚。
- **这家公司压根不在抓取名单里** → 开。它的原始数据本身就是误抓的产物，留着没有意义，
  而且不删的话它会在每一次映射里反复回来。

开 `with_input` 是对 CLAUDE.md 第十四节第 1 条「原始数据零改动」的**用户显式授权的例外**，
不是默认行为。所以它在接口上是一个必须主动传的参数、在界面上是一个默认不勾的复选框，
并且预览里把这一条单独标红 —— 绝不能让它跟着别的选项被顺手带上。

顺带一个实际收益：**从 WSL 删能绕开 Windows 资源管理器的 ACL 报错**
（「你需要来自 XXX 的权限」）。那个报错通常不是文件被锁，而是 WSL 写下的目录在 NTFS 上
属主对不上，资源管理器的删除流程会卡在属主校验；WSL 侧 `shutil.rmtree` 不走那套校验。
真被句柄占住的情况长这样：目录无法 rename（`Permission denied`）但内容照样可读可写 ——
那种只能先关掉占着它的窗口/程序。

两段式：`preview()` 只看不动，把每一处将被删除的路径与产品数如实列出；`execute()` 才动手。
前端强制先预览再确认，因为这是全平台唯一一个会成片删除数据的入口。
"""
import os, json, shutil, time, threading

from .paths import DATA_DIR
from .classify3 import CAT1, CAT2, CAT3
from . import products as P

CATS = (CAT1, CAT2, CAT3)
_lock = threading.Lock()

# 以公司目录名为键的记忆文件。与 variants.REG_FILES 同一份清单 —— 那边是合并同名变体时
# 摘记录，这边是整家移除，漏掉任何一份都会留下幽灵状态。
REG_FILES = (
    ("mapped_companies.json",   "② 映射记忆"),
    ("cleaned_companies.json",  "③ 清洗记忆"),
    ("audited_companies.json",  "④ 复查记忆"),
    ("idmapped_companies.json", "⑤ id映射记忆"),
    ("shelved_products.json",   "搁置记录"),
)


def _count(path):
    """目录下的产品数（不存在返回 None，用于区分"没有"和"有但是空的"）。"""
    if not os.path.isdir(path):
        return None
    return P.count_products(path)


def _company_id(cfg, company):
    """这家公司在最终数据里的目录名（公司id）。查不到就返回原名 —— 与 idmap 写盘同一口径。

    必须走 idmap.lookup()：别名表、去尾部消歧后缀两种命中方式都要覆盖，否则最终数据
    那一棵树的清理会整个打空（这正是当年丢 16 个产品的三个缺陷之一）。
    """
    try:
        from . import idmap
        comp_map, _ = idmap.load_id_table(cfg.get("company_id") or "", "公司id.json")
        cid, _how = idmap.lookup(comp_map, company, idmap.load_aliases()["company"])
        return cid or company
    except Exception:
        return company


def _targets(cfg, company, with_input=False):
    """列出该公司在磁盘上的全部落点。返回 ([{key, label, path, products, raw}], cid)。

    raw=True 标记「这是原始爬取数据」—— 界面据此把它单独标红，别跟平台产出混在一起读。
    """
    cid = _company_id(cfg, company)
    out = []
    if with_input:
        # 排在最前：它是唯一不可再生的一项，要第一眼就看见
        out.append({"key": "input", "label": "① 输入目录（原始爬取数据）", "raw": True,
                    "path": os.path.join(cfg.get("input") or "", company)})
    out += [
        {"key": "mapping_out", "label": "② 映射输出",
         "path": os.path.join(cfg.get("mapping_out") or "", company)},
        {"key": "unmatched", "label": "未匹配",
         "path": os.path.join(cfg.get("unmatched") or "", company)},
    ]
    for cat in CATS:
        out.append({"key": "clean_out", "label": f"③ 清洗后 · {cat}",
                    "path": os.path.join(cfg.get("clean_out") or "", cat, company)})
    for cat in CATS:
        out.append({"key": "final_out", "label": f"⑤ 最终数据 · {cat}",
                    "path": os.path.join(cfg.get("final_out") or "", cat, cid)})
    out.append({"key": "process", "label": "过程记录（映射报告/研判/人工记录/落地清单）",
                "path": os.path.join(cfg.get("process") or "", company)})
    for t in out:
        t.setdefault("raw", False)
        t["exists"] = os.path.isdir(t["path"])
        t["products"] = _count(t["path"]) or 0
        t["locked"] = t["exists"] and not _can_remove(t["path"])
    return [t for t in out if t["exists"]], cid


def _can_remove(path):
    """这个目录现在删得掉吗 —— 用 rename 探测有没有被句柄占住。

    /mnt/d 上真正删不掉的原因几乎只有一个：Windows 侧有进程开着它（多半是资源管理器
    正停在这个文件夹里）。那种情况下目录**无法 rename 但内容照样可读可写**，
    所以只能靠 rename 探。探到了就在预览里提前说清楚，别等删到一半才报错。

    注意与 Windows 资源管理器那个「你需要来自 XXX 的权限」区分开：那个是 NTFS 属主
    校验，不是占用，WSL 侧照样删得掉 —— 这里探测的是真占用。
    """
    tmp = path + ".__rmprobe"
    try:
        os.rename(path, tmp)
        os.rename(tmp, path)
        return True
    except OSError:
        return False


def _regs_hit(company):
    """五份记忆里哪几份有这家公司。"""
    hits = []
    for fn, label in REG_FILES:
        p = os.path.join(DATA_DIR, fn)
        if not os.path.isfile(p):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and company in d:
            n = len(d[company]) if isinstance(d[company], dict) else 1
            hits.append({"file": fn, "label": label, "items": n})
    return hits


def preview(cfg, company, with_input=False):
    """只看不动：这家公司会被删掉哪些目录、多少产品、命中哪几份记忆。"""
    company = (company or "").strip()
    if not company:
        raise ValueError("请指定公司名")
    targets, cid = _targets(cfg, company, with_input)
    regs = _regs_hit(company)
    in_path = os.path.join(cfg.get("input") or "", company)
    in_input = os.path.isdir(in_path)
    if not targets and not regs:
        raise ValueError(
            f"平台里没有「{company}」的任何产出或记录"
            + ("（它只在输入目录里 —— 勾上「同时删除输入目录」即可一并清掉）"
               if in_input else "，请确认公司名是否正确"))
    locked = [t for t in targets if t.get("locked")]
    return {
        "company": company, "company_id": cid,
        "targets": targets,
        "products": sum(t["products"] for t in targets),
        "registries": regs,
        "with_input": bool(with_input),
        # 没勾时输入目录单独报告：用户需要知道"删完之后源头还在，下次跑映射它会整个回来"
        "input_kept": in_input and not with_input,
        "input_path": in_path if in_input else "",
        # 被句柄占住的目录提前报出来，别等删到一半才失败
        "locked": [{"label": t["label"], "path": t["path"]} for t in locked],
    }


def execute(job, cfg, company, with_input=False):
    """真正删除。先删目录再清记忆 —— 反过来的话，中途失败会留下"记忆没了但目录还在"，
    下次扫描又把这家公司整个捞回来，用户以为没删掉。"""
    company = (company or "").strip()
    if not company:
        raise ValueError("请指定公司名")
    pv = preview(cfg, company, with_input)
    targets = pv["targets"]
    job.log(f"移除公司「{company}」（公司id: {pv['company_id']}）")
    job.log(f"共 {len(targets)} 处目录 / {pv['products']} 个产品，"
            f"另清 {len(pv['registries'])} 份记忆")
    if with_input:
        job.log("⚠ 本次**包含输入目录**：原始爬取数据也会被删除，不可恢复。")
    job.step(0, len(targets) + 2)

    removed, failed = [], []
    for i, t in enumerate(targets, 1):
        job.set_current(f"{t['label']} · {t['path']}")
        try:
            shutil.rmtree(t["path"])
            removed.append(t)
            job.log(f"  ✓ 已删 {t['label']}：{t['path']}（{t['products']} 个产品）"
                    + ("  ← 原始数据" if t.get("raw") else ""))
        except OSError as e:
            # /mnt/d 上的目录可能被 Windows 侧句柄占住（用户在资源管理器里打开了它）。
            # 不重试、不强删，如实报告让用户关掉窗口再来一次。
            failed.append({**t, "error": str(e)})
            job.log(f"  ✗ 删不掉 {t['label']}：{e}")
            job.log(f"     多半是这个目录正开在资源管理器里，关掉那个窗口再重试一次即可。")
        job.step(i)

    # ---- 记忆 ----
    job.set_current("清理处理记忆 …")
    dropped = []
    with _lock:
        for fn, label in REG_FILES:
            p = os.path.join(DATA_DIR, fn)
            if not os.path.isfile(p):
                continue
            try:
                d = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(d, dict) or company not in d:
                continue
            d.pop(company, None)
            tmp = p + ".tmp"
            json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, p)
            dropped.append(label)
    if dropped:
        job.log("  ✓ 已清记忆：" + "、".join(dropped))
    job.step(len(targets) + 1)

    # ---- 派生缓存：分布分片 + 总览行 ----
    job.set_current("刷新缓存 …")
    try:
        from . import stats as _stats
        _stats.drop_company_shard(company)
        job.log("  ✓ 已从类别分布分片里移除")
    except Exception as e:
        job.log(f"  ⚠ 类别分布分片清理失败（下次全量重扫会自愈）：{e!r}")
    try:
        from . import overview as _ov
        _ov.drop_rows([company])
        job.log("  ✓ 已从总览快照里移除")
    except Exception as e:
        job.log(f"  ⚠ 总览快照清理失败（点一次「重新扫描」即可）：{e!r}")
    job.step(len(targets) + 2)

    ok = not failed
    from . import history
    history.add("purge", f"移除公司 {company}" + ("（含原始数据）" if with_input else ""),
                [company],
                {"目录": len(removed), "产品": sum(t["products"] for t in removed),
                 "记忆": len(dropped), "失败": len(failed),
                 "含输入目录": "是" if with_input else "否"},
                {"输入目录": pv["input_path"] or "—"}, ok)
    job.log(("✓ 移除完成" if ok else f"⚠ 移除完成，但有 {len(failed)} 处删不掉")
            + f"：{len(removed)} 处目录 / {sum(t['products'] for t in removed)} 个产品")
    if with_input:
        gone = not os.path.isdir(pv["input_path"] or "x")
        job.log(("✓ 输入目录（原始爬取数据）已一并删除，这家公司不会再回来。" if gone else
                 "⚠ 输入目录没删掉 —— 它还在，下次跑 ② 映射这家公司会整个回来。"))
    elif pv["input_kept"]:
        job.log(f"ℹ 输入目录里这家公司**没有动**：{pv['input_path']}")
        job.log("  只要它还在输入目录里，下次跑 ② 映射这家公司就会整个回来 —— "
                "要连原始数据一起清掉，请勾选「同时删除输入目录」重跑一次。")
    return {"company": company, "removed": removed, "failed": failed,
            "registries": dropped, "products": sum(t["products"] for t in removed),
            "with_input": bool(with_input),
            "input_kept": pv["input_kept"], "input_path": pv["input_path"],
            "ok": ok, "time": time.strftime("%Y-%m-%d %H:%M:%S")}
