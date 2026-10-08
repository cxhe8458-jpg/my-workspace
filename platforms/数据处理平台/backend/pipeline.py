# -*- coding: utf-8 -*-
"""流程编排：② 映射 → ③ 分类+清洗（合一） → ④ 复查，逐步校验、不通过不进入下一步。
路径配置持久化在 data/pipeline.json，界面可改可存。

2026-07 改版要点：
- ③④ 合并为一步（consolidate），取消"二次分类"中间目录，少一整份全量副本。
- ③ 按公司增量：只处理映射输出下 新增/有更新 的公司，输出目录固定不再带日期后缀。
  修复了"加一家公司却把已完成的全部重新复制清洗一遍"的重复产出 bug。
- ④ 复查默认只核对本次处理的公司，基准取映射输出。
- 人工映射结果直接回写映射输出，原先独立的"人工映射分类合并链"已取消。
"""
import os, json, time, threading
from .paths import DATA_DIR, normalize_path
from . import mapping, consolidate, audit, idmap, history, registry

CFG_PATH = os.path.join(DATA_DIR, "pipeline.json")
_lock = threading.Lock()

CFG_VERSION = 5
BASE = "/mnt/d/自动化数据处理"
DEFAULTS = {
    "input":        f"{BASE}/公司产品数据",
    "mapping_out":  f"{BASE}/公司产品数据_产品映射",
    "unmatched":    f"{BASE}/公司产品数据_未匹配",
    "process":      f"{BASE}/公司产品数据_产品映射_过程记录",
    "clean_out":    f"{BASE}/公司产品数据_清洗后",
    "final_out":    f"{BASE}/公司产品数据_最终数据",
    "company_id":   f"{BASE}/公司产品数据_id表/公司id.json",
    "product_id":   f"{BASE}/公司产品数据_id表/产品id.json",
    "model_id": "", "use_ai": True, "threads": 1,
    # AI 研判的**产品级**并发。threads 是公司级的，管本地文件遍历；这个管 LLM 调用。
    # 实测单次研判 11~20 秒，串行时一家 2611 个产品的公司要跑 7 小时；
    # 并发 8 之后同样的量约 50 分钟。见 mapping._ai_batch 的注释。
    "ai_workers": 8,
    "remap_all": False, "reclean_all": False,
    # 额外统计来源：只参与统计分析，不进入任何处理流程（其他同事整理好的历史数据）
    "extra_sources": ["/mnt/d/公司数据备份/待分类/json",
                      "/mnt/d/公司数据备份/待分类/pdf"],
}
PATH_KEYS = ["input", "mapping_out", "unmatched", "process", "clean_out",
             "final_out", "company_id", "product_id"]
OBSOLETE = ("classify_out", "manual_out", "manual_new", "manual_merged")


def _migrate(cfg):
    """v1→v2：删除已取消的目录配置，清洗输出改为新默认目录。
    v2→v3：补上 id 映射相关的三个路径默认值。
    v3→v4：补上额外统计来源 extra_sources。
    v4→v5：补上 AI 研判的产品级并发 ai_workers。"""
    if cfg.get("_v") == CFG_VERSION:
        return cfg, False
    for k in OBSOLETE:
        cfg.pop(k, None)
    old = cfg.get("clean_out") or ""
    if "二次分类" in old or not old:
        cfg["clean_out"] = DEFAULTS["clean_out"]
    for k in ("final_out", "company_id", "product_id"):
        if not cfg.get(k):
            cfg[k] = DEFAULTS[k]
    if not isinstance(cfg.get("extra_sources"), list):
        cfg["extra_sources"] = list(DEFAULTS["extra_sources"])
    try:
        cfg["ai_workers"] = max(1, min(32, int(cfg.get("ai_workers") or DEFAULTS["ai_workers"])))
    except (TypeError, ValueError):
        cfg["ai_workers"] = DEFAULTS["ai_workers"]
    cfg["_v"] = CFG_VERSION
    return cfg, True


def load_config():
    cfg = dict(DEFAULTS)
    changed = False
    if os.path.exists(CFG_PATH):
        try:
            cfg.update(json.load(open(CFG_PATH, encoding="utf-8")))
        except Exception:
            pass
    cfg, changed = _migrate(cfg)
    if changed:
        _write(cfg)
    return cfg


def _write(cfg):
    tmp = CFG_PATH + ".tmp"
    json.dump(cfg, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, CFG_PATH)


def save_config(new):
    with _lock:
        cfg = load_config()
        for k in PATH_KEYS:
            if k in new and new[k]:
                cfg[k] = normalize_path(new[k])
        for k in ("model_id", "use_ai", "threads", "ai_workers", "remap_all", "reclean_all"):
            if k in new:
                cfg[k] = new[k]
        if isinstance(new.get("extra_sources"), list):
            cfg["extra_sources"] = [normalize_path(x) for x in new["extra_sources"] if str(x).strip()]
        cfg["threads"] = max(1, min(5, int(cfg.get("threads") or 1)))
        cfg["ai_workers"] = max(1, min(32, int(cfg.get("ai_workers") or DEFAULTS["ai_workers"])))
        for k in OBSOLETE:
            cfg.pop(k, None)
        cfg["_v"] = CFG_VERSION
        _write(cfg)
        return cfg


# ---------------- 步骤封装 ----------------
def _step_mapping(job, cfg, model_cfg, names):
    r = mapping.run_mapping(job, cfg["input"], cfg["mapping_out"], names,
                            model_cfg, cfg["use_ai"], threads=cfg["threads"],
                            unmatched_root=cfg["unmatched"], process_root=cfg["process"],
                            ai_workers=cfg.get("ai_workers"))
    return {"companies": len(r["companies"]),
            "total": sum(c["total"] for c in r["companies"]),
            "mapped": sum(c["mapped"] for c in r["companies"]),
            "manual": sum(c.get("manual", 0) for c in r["companies"]),
            "ai": sum(c["ai"] for c in r["companies"]),
            "unmatched": sum(c["unmatched"] for c in r["companies"]),
            "verify_ok": all(c["verify_ok"] for c in r["companies"]),
            "detail": r}


def _step_consolidate(job, cfg, scope=None):
    """scope=None 表示"按增量自动选"；给了列表就只处理这几家。
    连续执行/一键全流程 现在都能限定公司，这里必须把范围传下去，
    否则界面上勾了 3 家、实际还是把所有待处理公司都跑了。"""
    job.stats.clear()
    return consolidate.run_consolidate(job, cfg["mapping_out"], cfg["clean_out"],
                                       companies=scope or None, apply=True,
                                       force=bool(cfg.get("reclean_all")))


def _step_audit(job, cfg, scope):
    job.stats.clear()
    return audit.run_audit(job, cfg["clean_out"], cfg["mapping_out"], companies=scope)


def _step_idmap(job, cfg, scope=None):
    job.stats.clear()
    return idmap.run_idmap(job, cfg["clean_out"], cfg["final_out"],
                           cfg["company_id"], cfg["product_id"], apply=True,
                           companies=scope)


def _idmap_ready(cfg):
    """id 表齐备才做 id 映射；缺表不算错误，跳过并说明。"""
    for k, label in (("company_id", "公司id.json"), ("product_id", "产品id.json")):
        if not os.path.isfile(cfg.get(k) or ""):
            return False, f"{label} 不存在（{cfg.get(k) or '未配置'}）"
    return True, ""


def _pick_companies(job, cfg, model_cfg):
    """映射阶段要处理的公司（增量：跳过已完成，除非 remap_all）。"""
    comps = mapping.scan_companies(cfg["input"])
    if comps is None:
        raise ValueError(f"输入路径不存在: {cfg['input']}")
    if not comps:
        raise ValueError(f"输入路径下未识别到产品数据（叶子目录需含 json/pdf/图片）: {cfg['input']}")
    with_st = registry.status_of(comps)
    if cfg.get("remap_all"):
        names = [c["name"] for c in with_st]
        job.log(f"识别到 {len(names)} 家公司（已选择全部重做），线程数 {cfg['threads']}，"
                f"AI研判 {'开' if cfg['use_ai'] and model_cfg else '关'}")
    else:
        names = [c["name"] for c in with_st if c["status"] != "已完成"]
        skipped = [c["name"] for c in with_st if c["status"] == "已完成"]
        job.log(f"识别到 {len(with_st)} 家公司：新增/有更新 {len(names)} 家，"
                f"已完成跳过 {len(skipped)} 家（映射记忆）；线程数 {cfg['threads']}，"
                f"AI研判 {'开' if cfg['use_ai'] and model_cfg else '关'}")
        if skipped:
            job.log("  跳过（已完成）：" + "、".join(skipped[:20]) + ("…" if len(skipped) > 20 else ""))
    return names, len(comps)


# ---------------- 一键全流程 ----------------
def run_pipeline(job, cfg, model_cfg, companies=None):
    """companies=None 沿用原来的"自动挑新增/有更新"；给了列表就只跑这几家。"""
    steps = {}
    t0 = time.time()

    # ---------- ② 映射 ----------
    job.log("━━━━━━ 步骤② 产品数据映射 ━━━━━━")
    if companies:
        avail = mapping.scan_companies(cfg["input"]) or []
        have = {c["name"] for c in avail}
        names = [c for c in companies if c in have]
        n_all = len(avail)
        skipped = [c for c in companies if c not in have]
        job.log(f"按指定范围执行：{len(companies)} 家，其中 {len(names)} 家在输入目录里需要映射"
                + (f"；另 {len(skipped)} 家只在映射输出里，从 ③ 开始跑" if skipped else ""))
    else:
        names, n_all = _pick_companies(job, cfg, model_cfg)
    touched = set(names) | set(companies or [])   # 本次碰过的公司，用于跑完就地刷新总览（含中途中止的情形）
    if names:
        steps["mapping"] = _step_mapping(job, cfg, model_cfg, names)
        steps["mapping"]["skipped"] = n_all - len(names)
    else:
        job.log("没有新增公司需要映射；后续步骤仍按增量检查映射输出。")
        steps["mapping"] = {"companies": 0, "total": 0, "mapped": 0, "manual": 0, "ai": 0,
                            "unmatched": 0, "verify_ok": True, "skipped": n_all,
                            "detail": {"companies": []}}
    if job.cancelled:
        return {"steps": steps, "ok": False, "aborted": "已取消",
                "touched": sorted(touched)}
    if not steps["mapping"]["verify_ok"]:
        job.log("✗ 映射完整性校验未通过，流程中止（不进入下一步）")
        return {"steps": steps, "ok": False, "aborted": "映射校验失败",
                "touched": sorted(touched)}

    # ---------- ③ 分类 + 清洗（合一，按公司增量） ----------
    job.log("━━━━━━ 步骤③ 分类 + 清洗（按公司增量，不重复处理已完成公司） ━━━━━━")
    r3 = _step_consolidate(job, cfg, companies)
    steps["consolidate"] = {k: r3.get(k) for k in
                            ("total", "dist", "stats", "verify_ok", "skipped", "clean_out")}
    steps["consolidate"]["companies"] = len(r3["companies"])
    touched |= {s_["company"] for s_ in r3["companies"]}
    if job.cancelled:
        return {"steps": steps, "ok": False, "aborted": "已取消",
                "touched": sorted(touched)}
    if not r3["verify_ok"] and r3["companies"]:
        job.log("✗ 分类+清洗校验未通过，流程中止")
        return {"steps": steps, "ok": False, "aborted": "分类清洗校验失败",
                "touched": sorted(touched)}

    # ---------- ④ 复查（只核对本次处理的公司） ----------
    scope = [s["company"] for s in r3["companies"]]
    if not scope:
        job.log("━━━━━━ 步骤④ 数据复查 ━━━━━━")
        job.log("本次没有公司被重新清洗，跳过复查（如需全量复查请到复查页手动执行）")
        steps["audit"] = {"checked": 0, "loss_count": 0, "unmatched_count": 0,
                          "pdf_missing_count": 0, "ok": True, "losses": [], "skipped": True}
        ok = True
    else:
        job.log(f"━━━━━━ 步骤④ 数据复查（{len(scope)} 家公司，损失必须=0） ━━━━━━")
        r4 = _step_audit(job, cfg, scope)
        steps["audit"] = {"checked": r4["checked"], "loss_count": r4["loss_count"],
                          "unmatched_count": r4["unmatched_count"],
                          "pdf_missing_count": r4["pdf_missing_count"],
                          "ok": r4["ok"], "losses": r4["losses"][:50]}
        ok = r4["ok"]
    # ---------- ⑤ id 映射（公司名→公司id，一级/二级→分类id） ----------
    if not ok:
        job.log("审计未通过，跳过 id 映射（不产出有损的交付物）")
    elif not scope:
        job.log("本次无公司需要重新映射 id，跳过")
    else:
        ready, why = _idmap_ready(cfg)
        if not ready:
            job.log(f"━━━━━━ 步骤⑤ id 映射 ━━━━━━")
            job.log(f"跳过：{why}。补好 id 表后可在数据处理页单独执行。")
            steps["idmap"] = {"skipped": True, "reason": why}
        else:
            job.log(f"━━━━━━ 步骤⑤ id 映射（{len(scope)} 家公司） ━━━━━━")
            r5 = _step_idmap(job, cfg, scope)
            steps["idmap"] = {k: r5[k] for k in
                              ("total", "done", "output", "verify_ok", "out_root",
                               "unresolved_companies", "unresolved_categories",
                               "fallbacks", "collisions")}
            ok = ok and r5["verify_ok"]

    job.log(("✓ 全流程完成，审计通过（损失=0）" if ok else "✗ 存在问题，请到复查页查看并按修复闭环处理")
            + f"，耗时 {int(time.time() - t0)}s")
    history.add("pipeline", f"一键全流程（映射 {len(names)} 家 / 清洗 {len(scope)} 家）",
                names or scope,
                {"映射": f"{steps['mapping']['mapped']}规则+{steps['mapping']['ai']}AI",
                 "未匹配": steps["mapping"]["unmatched"],
                 "清洗产品": steps["consolidate"].get("total", 0),
                 "合并PDF": (steps["consolidate"].get("stats") or {}).get("merged", 0),
                 "审计损失": steps["audit"]["loss_count"],
                 "id映射": (steps.get("idmap") or {}).get("done", "跳过")},
                {"输入": cfg["input"], "映射": cfg["mapping_out"],
                 "清洗后": cfg["clean_out"], "最终数据": cfg["final_out"]}, ok)
    return {"steps": steps, "ok": ok, "clean_out": cfg["clean_out"],
            "final_out": cfg["final_out"],
            "touched": sorted(touched)}


# ---------------- 数据处理页（可单步） ----------------
def run_process(job, cfg, model_cfg, companies, do_map=True, do_clean=True,
                do_audit=False, do_idmap=False):
    """映射 / 分类+清洗 / 复查 / id映射 按勾选执行，逐步校验、不通过即中止。"""
    steps = {}
    touched = set(companies) if do_map else set()
    if do_map:
        job.log("━━━━━━ 映射 ━━━━━━")
        if not companies:
            raise ValueError("请至少选择一家公司")
        steps["mapping"] = _step_mapping(job, cfg, model_cfg, companies)
        if job.cancelled:
            return {"steps": steps, "ok": False, "aborted": "已取消",
                "touched": sorted(touched)}
        if not steps["mapping"]["verify_ok"]:
            job.log("✗ 映射完整性校验未通过，中止")
            return {"steps": steps, "ok": False, "aborted": "映射校验失败",
                "touched": sorted(touched)}

    scope = []
    if do_clean:
        job.log("━━━━━━ 分类 + 清洗（按公司增量） ━━━━━━")
        r3 = _step_consolidate(job, cfg, companies)
        steps["consolidate"] = {k: r3.get(k) for k in
                                ("total", "dist", "stats", "verify_ok", "skipped", "clean_out")}
        steps["consolidate"]["companies"] = len(r3["companies"])
        scope = [s["company"] for s in r3["companies"]]
        touched |= set(scope)
        if job.cancelled:
            return {"steps": steps, "ok": False, "aborted": "已取消",
                "touched": sorted(touched)}
        if not r3["verify_ok"] and r3["companies"]:
            job.log("✗ 分类+清洗校验未通过，中止")
            return {"steps": steps, "ok": False, "aborted": "分类清洗校验失败",
                "touched": sorted(touched)}

    if do_audit:
        # scope 只有跑过 ③清洗 才有值。以前这里写的是 `if do_audit and scope`，
        # 于是「勾了④、没勾③」时复查一步都不跑，而任务标题照样写着「→复查」——
        # 用户以为核过了。现在退回用本次选中的公司；一家都没选就全量。
        a_scope = scope or [c for c in (companies or []) if c] or None
        job.log("━━━━━━ 复查（%s） ━━━━━━" %
                (f"{len(a_scope)} 家公司" if a_scope else "全量"))
        if not scope:
            job.log("本次没有公司被重新清洗，改为按所选范围核对既有的清洗产物。")
        r4 = _step_audit(job, cfg, a_scope)
        touched |= set(a_scope or [])
        steps["audit"] = {"checked": r4["checked"], "loss_count": r4["loss_count"],
                          "unmatched_count": r4["unmatched_count"],
                          "pdf_missing_count": r4["pdf_missing_count"],
                          "ok": r4["ok"], "losses": r4["losses"][:50]}
        if not r4["ok"]:
            job.log("✗ 复查发现损失，跳过 id 映射（不产出有损的交付物）")
            do_idmap = False

    if do_idmap:
        ready, why = _idmap_ready(cfg)
        if not ready:
            raise ValueError(f"无法执行 id 映射：{why}")
        job.log("━━━━━━ id 映射 ━━━━━━"
                + (f"（{len(scope)} 家公司）" if scope else "（全量）"))
        r5 = _step_idmap(job, cfg, scope or None)
        steps["idmap"] = {k: r5[k] for k in
                          ("total", "done", "output", "verify_ok", "out_root",
                           "unresolved_companies", "unresolved_categories",
                           "fallbacks", "collisions")}

    ok = all(s.get("verify_ok", s.get("ok", True)) for s in steps.values())
    job.log("✓ 数据处理完成：" + "、".join(steps.keys()))
    history.add("process", "数据处理（" + "→".join(steps.keys()) + "）",
                companies if do_map else scope,
                {k: (v.get("total") or v.get("done")
                     or (v.get("stats") or {}).get("merged", ""))
                 for k, v in steps.items()},
                {"输入": cfg["input"], "清洗输出": cfg["clean_out"] if do_clean else "",
                 "最终数据": cfg["final_out"] if do_idmap else ""}, ok)
    return {"steps": steps, "ok": ok, "clean_out": cfg["clean_out"] if do_clean else "",
            "final_out": cfg["final_out"] if do_idmap else "",
            "touched": sorted(touched)}
