# -*- coding: utf-8 -*-
"""③ 分类 + 清洗（合一，按公司增量）——替代原先的 classify3(全量) + clean(全量新目录)。

原先的问题：映射是增量的，但分类会 rmtree 清空类别目录全量重建、清洗遇到已存在的输出
目录就追加时间戳另起一份全量副本。结果每加一家公司就产出一整份全量副本（实测 6 份 ≈1.7GB）。

现在：
- 增量单位 = 公司。只处理"新增/有更新"的公司（指纹见 registry.clean_status），
  其余公司在输出目录里原样保留、绝不重复复制。
- 输出目录固定（不带日期后缀），可反复写入。
- 分类与清洗一次完成：判定类别 → 直接复制到 <输出>/<类别X>/<公司>/… → 就地清洗该副本。
  省掉了原先"二次分类"那一整份中间副本。

分类判定的**权威口径在 classify3.py**，一句话是「参数载在哪」而不是「目录里有什么文件」：
有原始 PDF 或参数图 → 类别3；压根没有 json → 类别3；否则按 json 参数 非空/空 分 类别1/类别2。
**产品展示图（主图/图片_N/产品图_N）不参与判定**——它不承载参数。
（这段注释一度还停留在旧口径「有 PDF 或图片 → 类别3」，照着它理解会理解错，2026-08-04 更正。）

**html 源文件全程原样保留**：不参与分类判定、不被清洗触碰、跟着整目录复制走。
每家公司清洗完会核对 html 数量（源 vs 落地），对不上就不写清洗记忆、下次重做。

输出结构：<输出>/<类别X>/<公司>/<一级分类>/<二级分类>/<产品>/{<产品>.json, <产品>.pdf, <原名>.html}
"""
import os, shutil, time
from collections import Counter
from . import history, registry
from .classify3 import CAT1, CAT2, CAT3, classify_product, IMG_EXT
from .clean import process_product
from . import products as P

CATS = (CAT1, CAT2, CAT3)


def _products_of(croot):
    """公司目录下的产品叶子。口径见 products.py —— 不再要求必须有 .json，
    否则"只有图片和 PDF"的产品会被整个跳过（既不分类也不清洗）。"""
    return sorted(P.iter_products(croot), key=lambda x: x[1])


def _purge_company(clean_out, company):
    """删除该公司在输出目录三个类别下的旧产物（只删这一家，不动其他公司）。"""
    for cat in CATS:
        p = os.path.join(clean_out, cat, company)
        if os.path.isdir(p):
            shutil.rmtree(p)


def _company_output_dirs(clean_out, company):
    return [os.path.join(clean_out, cat, company) for cat in CATS]


def _count_output(clean_out, company):
    n = 0
    for d in _company_output_dirs(clean_out, company):
        if not os.path.isdir(d):
            continue
        for dp, dns, fns in P.walk(d):
            if P.is_product_dir(dns, fns):
                n += 1
    return n


def _count_output_html(clean_out, company):
    """该公司落地产物里的 html 总数（跨三个类别累加）。"""
    return sum(P.count_html(d) for d in _company_output_dirs(clean_out, company)
               if os.path.isdir(d))


def _residual(clean_out, company):
    """清洗后不应残留的文件：info.json / description.txt / 图片 / 临时文件。
    这四项任一非零都会让 verify_ok 为假（它们确凿说明清洗没做干净）。"""
    r = {"info_left": 0, "desc_left": 0, "img_left": 0, "tmp_left": 0}
    for d in _company_output_dirs(clean_out, company):
        if not os.path.isdir(d):
            continue
        for dp, _d, fns in P.walk(d):
            for f in fns:
                if f == "info.json": r["info_left"] += 1
                elif f == "description.txt": r["desc_left"] += 1
                elif f.lower().endswith(IMG_EXT): r["img_left"] += 1
                elif f.startswith(".__"): r["tmp_left"] += 1
    return r


# 交付物里**应该**出现的文件：<产品>.json / <产品>.pdf / <产品>_N.pdf / 原样保留的 html
DELIVER_EXT = P.JSON_EXT + P.DOC_EXT + P.HTML_EXT


def _strays(clean_out, company):
    """交付物里出现的**计划外文件**：{扩展名: 个数}, [样例路径]。

    只报告、不删除、不拦闸门。理由：删未知文件有丢数据的风险，而拦下来又会把整家公司
    卡在"需重跑"上 —— 但**完全不提**是最糟的：这次爬虫缓存目录被当成产品带进来一批
    `_cent_text.txt`，日志里一个字都没有，用户是打开文件夹才发现的。
    出现计划外文件，几乎总说明上游混进了不该进流程的东西，值得看一眼。
    """
    cnt, sample = {}, []
    for d in _company_output_dirs(clean_out, company):
        if not os.path.isdir(d):
            continue
        for dp, _d, fns in P.walk(d):
            for f in P.real_files(fns):
                if f.lower().endswith(DELIVER_EXT):
                    continue
                ext = (os.path.splitext(f)[1] or "（无扩展名）").lower()
                cnt[ext] = cnt.get(ext, 0) + 1
                if len(sample) < 5:
                    sample.append(os.path.join(os.path.basename(dp), f))
    return cnt, sample


def _product_fp(src, names):
    """单个产品的指纹：(文件数, 该目录下文件的最大 mtime)。
    产品叶子目录里只有文件、没有子目录，所以这点开销可以忽略。"""
    mt = 0.0
    n = 0
    for f in names:
        fp = os.path.join(src, f)
        try:
            if os.path.isfile(fp):
                n += 1
                mt = max(mt, os.path.getmtime(fp))
        except OSError:
            pass
    return n, round(mt, 3)


def _drop_product(clean_out, company, rel):
    """删掉某个产品在输出目录下的副本（跨三个类别找，因为它可能换过类别）。"""
    for cat in CATS:
        d = os.path.join(clean_out, cat, company, rel)
        if os.path.isdir(d):
            shutil.rmtree(d)
            # 顺手收掉因此变空的分类目录，别留一堆空壳
            p = os.path.dirname(d)
            while os.path.normpath(p) != os.path.normpath(os.path.join(clean_out, cat, company)):
                try:
                    if os.path.isdir(p) and not os.listdir(p):
                        os.rmdir(p)
                        p = os.path.dirname(p)
                        continue
                except OSError:
                    pass
                break


def _do_company(job, mapping_out, clean_out, company, stats, prog, force=False):
    """处理一家公司：**逐产品增量** 分类 → 复制 → 就地清洗 → 校验。返回汇总 dict。

    以前是整家 rmtree 再全量重做：人工匹配加了 1 个产品，247 个产品的公司就得重跑 247 遍、
    重新合并 247 份 PDF。现在按产品指纹（文件数 + 最大 mtime）比对，只动真正变过的产品；
    换过类别的会先删掉旧类别下的副本，源里删掉的产品也会同步删除。
    force=True 时忽略产品记忆，整家重做。
    """
    croot = os.path.join(mapping_out, company)
    prods = _products_of(croot)
    if not prods:
        job.log(f"── {company}：映射输出下无产品，跳过")
        return None
    fp_n, fp_mt = registry.fingerprint(mapping_out, company)
    prev = {} if force else registry.clean_products(company)
    if force:
        _purge_company(clean_out, company)

    dist = Counter()
    errs = []
    cur = {}
    failed = set()          # 本轮处理时抛异常的产品（见下面 gone 的计算）
    n_html_src = 0          # 源侧 html 总数，落地后拿它对账
    n_new = n_skip = 0

    for src, rel in prods:
        if job.cancelled:
            return None
        names = os.listdir(src)
        cat = classify_product(src, names) or CAT2
        dist[cat] += 1
        n_html_src += len(P.html_files(names))
        fp = _product_fp(src, names)
        dst = os.path.join(clean_out, cat, company, rel)
        old = prev.get(rel)
        # 沿用条件：类别没变、指纹没变、输出还在。三者缺一都要重做。
        if old and old[0] == cat and tuple(old[1:3]) == fp and os.path.isdir(dst):
            cur[rel] = [cat, fp[0], fp[1]]
            n_skip += 1
            prog[1][0] += 1
            job.step(prog[1][0])
            continue
        job.set_current(f"{company} / {rel}")
        try:
            _drop_product(clean_out, company, rel)    # 换类别/内容变了，先清掉旧副本
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copytree(src, dst)
            process_product(dst, True, stats, job.log)   # 就地清洗副本
            job.bump(cat)
            cur[rel] = [cat, fp[0], fp[1]]
            n_new += 1
        except Exception as e:
            # ⚠ 出错的产品**不进 cur、但要记进 failed**。
            # 以前只是不进 cur，于是它会被下面的 gone 算成"源里已不存在"，
            # `_drop_product` 连上一轮那份完好的副本一起删掉 —— 在目录被 Windows 句柄
            # 短暂占住时就会触发，那一轮的交付是残的（旧缺陷 #6）。
            # errs 非空会让 verify_ok=False、不写清洗记忆，下一轮自然重做，不需要靠删来纠正。
            failed.add(rel)
            stats["errors"] += 1
            errs.append(f"{company}/{rel}: {e!r}")
            job.log(f"  ✗ 错误 {company}/{rel}: {e!r}（保留上一轮的副本，本次不写清洗记忆）")
        prog[1][0] += 1
        job.step(prog[1][0])

    # 源里已经没有的产品，把输出里的残留一并删掉（不然复查会多出一堆孤儿）。
    #
    # ⚠ 以**输出目录的实际内容**为准，不能只看上次的记忆 `prev`：
    # 旧版记忆没有 products_map（prev 是空的），于是一个孤儿都找不出来，而校验又要求
    # 「落地数 = 分类数」—— 孤儿让校验永远过不去，不写清洗记忆，这家公司就永远停在
    # 「需重跑」，重跑多少次都一样。实测「英福康」247 个产品的输出里多了 1 个孤儿，
    # 这家公司因此卡了 6 天，每次重跑都白跑。
    have = set()
    for cat in CATS:
        have |= {rel for _dp, rel in P.iter_products(os.path.join(clean_out, cat, company))}
    # failed 要从 gone 里排除：它们在源里明明还在，只是这一轮没处理成功。
    gone = sorted((have | set(prev)) - set(cur) - failed)
    for rel in gone:
        _drop_product(clean_out, company, rel)
    if gone:
        job.log(f"  清掉 {len(gone)} 个孤儿产品（源里已不存在）：" +
                "、".join(gone[:5]) + ("…" if len(gone) > 5 else ""))

    got = _count_output(clean_out, company)
    resid = _residual(clean_out, company)
    # html 保全：清洗不碰 html，落地数必须与源数相等。少了就是复制或清洗环节吃掉了它，
    # 这时候绝不能写清洗记忆 —— 否则这家公司会被判为"已完成"，丢掉的 html 再也不会被补回来。
    got_html = _count_output_html(clean_out, company)
    html_ok = (got_html == n_html_src)
    verify_ok = ((got == len(prods)) and not errs
                 and all(v == 0 for v in resid.values()) and html_ok)
    job.log(f"── {company}：产品 {len(prods)}（{CAT1}={dist[CAT1]} {CAT2}={dist[CAT2]} "
            f"{CAT3}={dist[CAT3]}），本次处理 {n_new}、沿用 {n_skip}"
            + (f"、删除 {len(gone)}" if gone else "")
            + (f"、出错保留 {len(failed)}" if failed else "")
            + f"，落地 {got}，残留 "
            f"info={resid['info_left']}/desc={resid['desc_left']}/图片={resid['img_left']}，"
            + (f"html {got_html}/{n_html_src}{'' if html_ok else ' ✗少了!'}，" if n_html_src else "")
            + f"校验 {'✓通过' if verify_ok else '✗不一致!'}")
    if not html_ok:
        job.log(f"  ✗✗ {company}：html 源文件对不上（源 {n_html_src} 份，落地 {got_html} 份）——"
                f"不写清洗记忆，下次会重做这家")
    # 计划外文件：只提醒，不拦。交付物里本该只有 <产品>.json / <产品>.pdf / html。
    stray_cnt, stray_eg = _strays(clean_out, company)
    if stray_cnt:
        job.log(f"  ⚠ {company}：交付物里有 {sum(stray_cnt.values())} 个计划外文件 —— "
                + "、".join(f"{k} × {v}" for k, v in sorted(stray_cnt.items())))
        job.log(f"     例如：{'、'.join(stray_eg)}")
        job.log(f"     交付物本应只有 <产品>.json / <产品>.pdf / html。"
                f"多半是上游混进了不该进流程的目录（如爬虫缓存），建议核对来源。")
    if verify_ok:
        registry.update_clean(company, fp_n, fp_mt,
                              {"dist": {k: dist[k] for k in CATS}, "output": got},
                              products_map=cur)
    return {"company": company, "products": len(prods), "output": got,
            "processed": n_new, "reused": n_skip, "removed": len(gone),
            "failed": len(failed), "html_src": n_html_src, "html_out": got_html,
            "dist": {k: dist[k] for k in CATS}, "verify_ok": verify_ok,
            "errors": errs[:10]}


def run_consolidate(job, mapping_out, clean_out, companies=None, apply=True, force=False):
    """按公司增量执行 分类+清洗。

    companies=None → 自动选取映射输出下状态为 新增/有更新 的公司；
    force=True     → 忽略清洗记忆，把 companies（或全部公司）整体重做。
    apply=False    → 预演：只做分类统计，不写盘。
    """
    if not os.path.isdir(mapping_out):
        raise ValueError(f"映射输出目录不存在: {mapping_out}")

    # ---- 判定各公司的清洗状态 ----
    # ⚠ 这一整段以前是**不分青红皂白先跑一次全树 `clean_status()`**：对该公司逐家 walk +
    #   getmtime（实测映射输出 419 家公司 / 44,636 个目录 / 146,754 个文件，**纯计数**就要
    #   339 秒，加上 stat 是十几分钟），而这十几分钟里一行日志都没有、进度条停在 0/0，
    #   当前条目空白 —— 界面上就是"③ 卡死了"。实测历史日志：③ 的标题行到第一行内容
    #   **静默 902 秒**。而点名执行时根本不需要知道没被点到的公司是什么状态。
    #   所以：点名 → 只 walk 这几家（clean_status_one，单家约 1 秒）；
    #         自动按增量挑 → 不得不全扫，但先把话说出来、并让当前条目一直有字。
    if companies:
        status = []
        n_named = len(companies)
        reg = registry.load_clean()      # 读一次，逐家传进去（别每家重读 4.8 MB 的记忆文件）
        job.log(f"点名 {n_named} 家公司，逐家判定清洗状态（只 walk 这几家，不扫全树）…")
        for i, c in enumerate(companies, 1):
            if job.cancelled:
                break
            job.set_current(f"判定清洗状态 {i}/{n_named} · {c}")
            s = registry.clean_status_one(mapping_out, clean_out, c, reg)
            if s:
                status.append(s)
        by_name = {s["name"]: s for s in status}
        todo = [c for c in companies if c in by_name]
        for m in companies:
            if m not in by_name:
                job.log(f"⚠ 映射输出下找不到公司（或它下面没有产品），已跳过: {m}")
        if not force:
            skipped_done = [c for c in todo if by_name[c]["status"] == "已完成"]
            todo = [c for c in todo if by_name[c]["status"] != "已完成"]
            if skipped_done:
                job.log(f"跳过已完成（清洗记忆）{len(skipped_done)} 家："
                        + "、".join(skipped_done[:20]) + ("…" if len(skipped_done) > 20 else ""))
    else:
        job.log("未点名公司 → 按增量自动挑，先扫一遍全部公司的清洗状态。"
                "这一步要遍历整个映射输出（几万个目录），**期间不会有新日志**，属正常，请等它走完。")
        job.set_current("正在扫描全部公司的清洗状态（全树 walk，请稍候）…")
        status = registry.clean_status(mapping_out, clean_out)
        todo = [s["name"] for s in status] if force else \
               [s["name"] for s in status if s["status"] != "已完成"]
        done = [s["name"] for s in status if s["status"] == "已完成"]
        if done and not force:
            job.log(f"共 {len(status)} 家公司，已完成跳过 {len(done)} 家（增量），"
                    f"本次处理 {len(todo)} 家")

    if not todo:
        job.log("✓ 没有需要处理的公司（全部已是最新，未做任何重复复制）")
        return {"companies": [], "todo": [], "total": 0, "skipped": len(status),
                "verify_ok": True, "clean_out": clean_out, "stats": {}}

    # ---- 预演：只统计分类分布 ----
    if not apply:
        dist = Counter()
        n = 0
        for comp in todo:
            for src, rel in _products_of(os.path.join(mapping_out, comp)):
                dist[classify_product(src, os.listdir(src)) or CAT2] += 1
                n += 1
        job.log(f"预演：{len(todo)} 家公司 / {n} 个产品 —— "
                f"{CAT1}={dist[CAT1]} {CAT2}={dist[CAT2]} {CAT3}={dist[CAT3]}（未写盘）")
        return {"companies": [], "todo": todo, "total": n,
                "dist": {k: dist[k] for k in CATS}, "applied": False,
                "verify_ok": True, "clean_out": clean_out, "stats": {}}

    os.makedirs(clean_out, exist_ok=True)
    # 清点产品数还要再 walk 一遍（只走本次要处理的这几家）。全量时它与上面那次 walk 同量级，
    # 所以同样先出声、并逐家推进当前条目 —— 静默的几十秒和卡死在界面上没有区别。
    n_todo = len(todo)
    job.log(f"正在清点待处理公司的产品数（{n_todo} 家）…")
    total = 0
    for i, c in enumerate(todo, 1):
        job.set_current(f"清点产品数 {i}/{n_todo} · {c}")
        total += len(_products_of(os.path.join(mapping_out, c)))
        if i % 20 == 0:
            job.log(f"  · 已清点 {i}/{n_todo} 家，累计 {total} 个产品")
    job.step(0, total)
    job.log(f"分类+清洗 {len(todo)} 家公司 / {total} 个产品 → {clean_out}")
    job.log("处理：" + "、".join(todo[:20]) + ("…" if len(todo) > 20 else ""))

    stats = {"products": total, "merged": 0, "pages": 0, "json_renamed": 0,
             "desc_merged": 0, "svg": 0, "bad_img": 0, "bad_pdf": 0,
             "no_media": 0, "merge_planned": 0, "errors": 0, "html": 0}
    LIVE = {"merged": "已合并PDF", "json_renamed": "json重命名", "desc_merged": "描述并入",
            "svg": "SVG转换", "bad_img": "无效图跳过", "bad_pdf": "损坏PDF保留",
            "html": "html保留", "errors": "错误"}
    prog = (None, [0])
    summary = []
    t0 = time.time()
    for comp in todo:
        if job.cancelled:
            break
        s = _do_company(job, mapping_out, clean_out, comp, stats, prog, force=force)
        if s:
            summary.append(s)
        for k, label in LIVE.items():
            if stats[k]:
                job.set_stat(label, stats[k])

    dist_all = Counter()
    for s in summary:
        for k, v in s["dist"].items():
            dist_all[k] += v
    verify_ok = bool(summary) and all(s["verify_ok"] for s in summary) and not job.cancelled
    n_proc = sum(s.get("processed", s["products"]) for s in summary)
    n_reuse = sum(s.get("reused", 0) for s in summary)
    n_html_src = sum(s.get("html_src", 0) for s in summary)
    n_html_out = sum(s.get("html_out", 0) for s in summary)
    job.log(f"✓ 分类+清洗完成：{len(summary)} 家公司 / {sum(s['products'] for s in summary)} 个产品"
            f"（实际处理 {n_proc}，沿用未变的 {n_reuse}），"
            f"合并PDF {stats['merged']}，"
            + (f"html {n_html_out}/{n_html_src} 份"
               + ("全部保留，" if n_html_out == n_html_src else " ✗有缺失!，") if n_html_src else "")
            + f"耗时 {int(time.time() - t0)}s，"
            f"总校验 {'✓通过' if verify_ok else '✗有公司未通过'}")
    if summary:
        history.add("consolidate", f"分类+清洗 × {len(summary)} 家公司",
                    [s["company"] for s in summary],
                    {"产品": sum(s["products"] for s in summary),
                     "合并PDF": stats["merged"], CAT1: dist_all[CAT1],
                     CAT2: dist_all[CAT2], CAT3: dist_all[CAT3],
                     "html": f"{n_html_out}/{n_html_src}",
                     "错误": stats["errors"]},
                    {"映射输出": mapping_out, "清洗输出": clean_out}, verify_ok)
    return {"companies": summary, "todo": todo,
            "total": sum(s["products"] for s in summary),
            "dist": {k: dist_all[k] for k in CATS}, "stats": stats,
            "html_src": n_html_src, "html_out": n_html_out,
            "applied": True, "verify_ok": verify_ok, "clean_out": clean_out,
            "skipped": len(status) - len(todo)}
