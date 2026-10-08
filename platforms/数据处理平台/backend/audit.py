# -*- coding: utf-8 -*-
"""④ 数据复查（审计）：逐产品核对清洗后有没有比清洗前少东西。

四项，目标全为 0：
  unmatched     原始侧的产品在清洗侧找不到对应
  pdf_missing   原有 PDF/图片，清洗后一个 PDF 都没有（疑似合并后被误删，最重）
  page_loss     清洗后 PDF 总页数 < 原始(图片数 + 原PDF总页数)
  html_missing  原始侧的 html 源文件在清洗侧少了（2026-08-04 新增）

匹配键：相对路径优先（精确）；路径不在时回退 (公司, 产品叶子名)。绝不用页面URL。

**html 单独成一项、且与页数解耦**：html 不并入 PDF，所以它的丢失不会体现在页数上；
而「只有 html」的产品 exp 恰好是 0，页数那条路径根本不会检查到它 —— 它反倒是最需要
这道检查的一类。所以 html 校验在 exp==0 时也照跑。

注：曾有一个 run_id_rename()（旧的「公司名→id 就地替换」全量入口）连同 _norm /
STRUCT_DIRS / SKIP / _is_struct_dir 一组辅助挂在本模块末尾，0 处引用、无路由，
照着它理解 ⑤ 会理解错（⑤ 的真实实现在 idmap.py）。已删除。
"""
import os, time, logging, warnings, threading
from concurrent.futures import ThreadPoolExecutor
from pypdf import PdfReader
from . import history
from . import products as P

logging.getLogger("pypdf").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

# 口径统一到 products.IMG_EXT（含 .gif/.svg）。必须与 clean 合并时用的口径一致：
# clean 会把哪些文件并进 PDF，这里就得按哪些文件算 exp，否则 exp 与 got 天然对不上。
IMG_EXT = P.IMG_EXT


def pdf_pages(p):
    """PDF 页数，读不出来返回 None。**页数统计的唯一口径**（clean.py 也从这里取）。

    ⚠ 必须自己持有文件句柄并显式 close，别写回 `PdfReader(p).pages` 那种一行版本：
    pypdf 的对象图里有引用环，靠引用计数回收不掉，要等分代 GC 才释放。而分代 GC 是按
    **容器分配个数**触发的、对字节数一无所知 —— 几个几十 MB 的 PDF 被环困住，
    一次 GC 都不会因此提前发生。规模摆在这里：清洗后一侧 11,547 个 PDF / 13 GB，
    单个最大 53 MB，`run_audit` 还开 16 线程同时读，清洗前那侧同样要读一遍。

    实测（60 个 PDF / 51 MB）：一行版滞留 52 MB（几乎等于文件总字节），
    本版滞留 0 MB。2026-08-06 两次生产任务就是被它拖到
    `OSError: [Errno 12] Cannot allocate memory` —— 崩点分别在 audit 的 os.listdir
    和 mapping 的 shutil.copytree，都只是"最后一根稻草"，真凶在这里。
    """
    r = None
    try:
        with open(p, "rb") as fh:
            r = PdfReader(fh, strict=False)
            return len(r.pages)
    except Exception:
        return None
    finally:
        if r is not None:
            try:
                r.close()
            except Exception:
                pass


_pages = pdf_pages          # 模块内旧名


def _exp_of(dp, fns):
    imgs = [f for f in fns if f.lower().endswith(IMG_EXT)]
    pg = 0
    for f in fns:
        if f.lower().endswith(".pdf"):
            pg += (_pages(os.path.join(dp, f)) or 0)
    return len(imgs) + pg, len(imgs), pg


# 精确复核要真的去解码图片（复用清洗器的转换器），所以串行化：这条路径是冷的
# （只在本该报错的少数产品上跑），但别让 16 个核页线程同时进 cairosvg/img2pdf。
_precise_lock = threading.Lock()


def _exp_readable(dp, fns):
    """精确版 exp：**按清洗器的口径**重算这个产品本该产出多少页。返回 (exp, [坏文件名])。

    ⚠ 为什么必须精确复核：`_exp_of` 只按扩展名数，而清洗器对转不了的图片是**跳过不产出**的
    （`clean.img_to_pdf_bytes` 返回 None）。两处口径不一致 → 一个坏源文件就让这家公司
    **永远**过不了这道闸门，重跑多少次都一样：
      · 「广州标旗光电」的 `主图.png` 其实是爬虫存下来的 HTML 错误页 → `exp=1/got=0`
        → 报 `pdf_missing`（界面显示成"疑似误删"），208 个产品的公司一直挂着"有损失"；
      · 3 张图里坏 1 张（截断的 jpeg，文件头合法、内容损坏）→ 报永久 page_loss。

    判据**直接复用 `clean.img_to_pdf_bytes`**，不另写一套文件头识别 —— 与"IMG_EXT 只有一处
    口径"同理：两处各写一份迟早分叉，而分叉的表现就是"某些文件审计说能转、清洗说转不了"。
    它转不了的文件同样返回 None，两边从此完全一致。

    只在**本该报错**的产品上调用（`check()` 里那条错误分支），失败是少数，成本可忽略；
    原 PDF 的页数照旧由 `_pages()` 数（读不出算 0），所以"原 PDF 整体被删/被损坏"
    这类最重的问题**不受影响**。
    """
    from .clean import img_to_pdf_bytes      # 局部导入：clean 模块级依赖本模块的 pdf_pages
    # dummy 是一次性计数器：img_to_pdf_bytes 会把 svg / 坏图记到传入的 stats 上，
    # 直接给 {} 会 KeyError。这里只要它的返回值（转得出来 / 转不出来），计数丢弃。
    broken, n_img, pg, dummy = [], 0, 0, {"svg": 0, "bad_img": 0}
    with _precise_lock:
        for f in fns:
            lf = f.lower()
            if lf.endswith(IMG_EXT):
                if img_to_pdf_bytes(os.path.join(dp, f), dummy) is not None:
                    n_img += 1
                else:
                    broken.append(f)
            elif lf.endswith(".pdf"):
                pg += (_pages(os.path.join(dp, f)) or 0)
    return n_img + pg, broken


def _got_of(dp, fns):
    g = 0
    for f in fns:
        if f.lower().endswith(".pdf"):
            g += (_pages(os.path.join(dp, f)) or 0)
    return g


def _product_dirs(root, only_dirs=None):
    """{相对路径: (绝对路径, 文件名列表)}。only_dirs 非空时只扫 root 下这些子目录。

    only_dirs 的元素可以带层级（如 `类别3_有pdf或参数图/某公司`）—— 增量复查靠这个
    把「只核这几家」直接压进 walk 的起点。不存在的子目录静默跳过（一家公司不一定
    三个类别都有）。返回的键始终是相对 root 的完整路径，与全量扫描时一致。

    **文件名一并带出来，别让调用方再 listdir 一遍**：/mnt/d 走 drvfs，每个目录项约 6.8ms，
    两侧各一万多个产品目录，重复列一轮就是分钟级的白等。产品目录按定义是叶子
    （`is_product_dir` 要求 dns 为空），所以这里的 fns 与 `os.listdir(dp)` 等价。
    """
    out = {}
    roots = [os.path.join(root, d) for d in only_dirs] if only_dirs else [root]
    for r in roots:
        if not os.path.isdir(r):
            continue
        for dp, dns, fns in P.walk(r):
            if P.is_product_dir(dns, fns):
                out[os.path.relpath(dp, root)] = (dp, fns)
    return out


def _canonical_cats(root):
    """清洗后目录只认这三个标准类别目录；有任何一个存在就只扫它们，
    避免旧口径遗留的类别目录（如改名前的 类别3_有pdf和图片）混进比对，把产品数算重。
    一个都没有时退回全量扫描，保证指向非标准目录时仍可用。"""
    from .classify3 import CAT1, CAT2, CAT3
    present = [c for c in (CAT1, CAT2, CAT3) if os.path.isdir(os.path.join(root, c))]
    return present or None


def run_audit(job, cleaned_root, pristine_root, companies=None):
    """核对清洗后 vs 清洗前，损失必须为 0。

    pristine_root = 映射输出（它才是真正的"清洗前状态"，含全部原始图片与 PDF）。
    清洗后目录多一层 类别X 前缀（<类别X>/<公司>/…），比对前剥掉该层再与映射输出对齐。
    companies 非空时只核对这些公司（增量复查）；为空则全量。

    第四个桶 `bad_source` 是**信息**，不参与 `ok`：源文件不是有效图片时清洗器本来就转不了
    （见 _exp_readable）。它只出现在日志与结果里，不进 per_company，所以总览的 ④ 列
    与"四项均为 0"的语义都不变。
    """
    if not os.path.isdir(cleaned_root):
        raise ValueError(f"清洗后目录不存在: {cleaned_root}")
    if not os.path.isdir(pristine_root):
        raise ValueError(f"清洗前（映射输出）目录不存在: {pristine_root}")
    scope = set(companies or [])
    cats = _canonical_cats(cleaned_root)
    if cats:
        job.log(f"清洗后目录只扫描标准类别：{'、'.join(cats)}")

    # ⚠ **范围必须压进 walk 的起点，不能"先全量扫完再按公司过滤"。**
    # 这两棵树各有一万多个目录，/mnt/d 上走一趟各约 90 秒。以前是全量扫两棵树、
    # 扫完才 `if r.split(os.sep)[0] in scope` 筛掉 —— 于是**复查 1 家公司和复查全部
    # 一样慢**（实测索引阶段静默 5 分钟以上，界面上完全看不出在干什么，像卡死）。
    # 增量的范围必须一路跟到最底层，这与 ⑤ idmap「校验范围要跟着增量范围走」同理。
    if scope:
        pris_subs = sorted(scope)
        # 清洗后目录多一层 类别X 前缀，所以要 类别 × 公司 笛卡尔积；不存在的自动跳过
        clean_subs = ([os.path.join(c, comp) for c in cats for comp in sorted(scope)]
                      if cats else None)
        job.log(f"增量复查范围：{len(scope)} 家公司 —— 只遍历这几家的子树，不扫全树")
    else:
        pris_subs, clean_subs = None, cats

    t0 = time.time()
    job.log("索引清洗前（映射输出）产品目录 …")
    pris = _product_dirs(pristine_root, only_dirs=pris_subs)
    job.log(f"  清洗前 {len(pris)} 个产品，耗时 {time.time() - t0:.0f}s")

    t1 = time.time()
    job.log("索引清洗后产品目录 …")
    clean_raw = _product_dirs(cleaned_root, only_dirs=clean_subs)
    job.log(f"  清洗后 {len(clean_raw)} 个产品，耗时 {time.time() - t1:.0f}s")

    # 剥掉清洗后目录的 类别X 层，使其相对路径与映射输出一致
    clean = {}
    for rel, ent in clean_raw.items():
        parts = rel.split(os.sep)
        clean[os.sep.join(parts[1:]) if len(parts) > 1 else rel] = ent
    # 兜底再筛一次：`cats` 为空（非标准目录结构）时上面压不下去，仍要保证范围正确
    if scope:
        pris = {r: d for r, d in pris.items() if r.split(os.sep)[0] in scope}
        clean = {r: d for r, d in clean.items() if r.split(os.sep)[0] in scope}
    job.log(f"清洗前 {len(pris)} 个产品，清洗后 {len(clean)} 个产品")
    # 回退索引：(公司, 叶子名) → 清洗后目录列表
    leaf_idx = {}
    for rel, ent in clean.items():
        parts = rel.split(os.sep)
        leaf_idx.setdefault((parts[0], parts[-1]), []).append(ent)

    tasks = []
    unmatched = []
    for rel, (pdp, pfns) in pris.items():
        cent = clean.get(rel)
        matched_by = "路径"
        if not cent:
            parts = rel.split(os.sep)
            cands = leaf_idx.get((parts[0], parts[-1])) or leaf_idx.get((parts[0].split(os.sep)[0], parts[-1]))
            if not cands:
                # 公司层可能被替换为 id：按叶子名全局唯一才回退
                allc = [d for (comp, leaf), ds in leaf_idx.items() if leaf == parts[-1] for d in ds]
                cands = allc if len(allc) == 1 else None
            if cands and len(cands) == 1:
                cent = cands[0]; matched_by = "公司+产品名"
            else:
                unmatched.append(rel)
                continue
        cdp, cfns = cent
        tasks.append((rel, pdp, pfns, cdp, cfns, matched_by))
    job.log(f"可核对 {len(tasks)} 个，原始侧无法在清洗侧找到对应 {len(unmatched)} 个")
    job.step(0, len(tasks))
    losses = []
    done = 0

    pdf_missing = []          # 重点：原有 PDF/图片，清洗后却一个 PDF 都没有（疑似合并后被误删）
    html_missing = []         # html 源文件在清洗侧少了
    bad_source = []           # 源文件不是有效图片（清洗器同样转不了）—— 信息，不计入闸门

    def check(t):
        """返回该产品的问题列表（可能同时有页数损失和 html 丢失，所以是 list 不是单值）。

        两侧的文件名都在上面那趟 walk 里拿到了，这里**一次 listdir 都不做** ——
        这个函数会跑一万多遍，而 /mnt/d 每个目录项约 6.8ms，重列一轮就是几分钟。
        """
        rel, pdp, pfns, cdp, cfns, mb = t
        out = []

        # ---- html 保全：与页数无关，独立判定 ----
        # 必须在 exp==0 的分支之前做：「只有 html」的产品 exp 恰好为 0，
        # 而它正是最需要这道检查的一类。
        want = {h.lower() for h in P.html_files(pfns)}
        if want:
            have = {h.lower() for h in P.html_files(cfns)}
            lost = sorted(want - have)
            if lost:
                out.append({"kind": "html_missing", "rel": rel,
                            "exp": len(want), "got": len(have), "lost": lost[:10],
                            "pristine": pdp, "cleaned": cdp, "matched_by": mb})

        exp, ni, npg = _exp_of(pdp, pfns)
        if exp == 0:
            return out
        got = _got_of(cdp, cfns)
        has_pdf = any(f.lower().endswith(".pdf") for f in cfns)
        if (not has_pdf) or got < exp:
            # 本该报错了 —— 先把 exp 换成**精确版**再定论：`_exp_of` 只按扩展名数图片，
            # 而清洗器对转不了的源图片是跳过不产出的，两处口径不一致会让坏源文件变成
            # 永久损失（见 _exp_readable）。只在这条错误分支上才真正试转一次，
            # 失败是少数，成本约等于零。
            exp2, broken = _exp_readable(pdp, pfns)
            if broken:
                out.append({"kind": "bad_source", "rel": rel, "exp": exp2, "got": got,
                            "exp_raw": exp, "broken": broken[:10],
                            "pristine": pdp, "cleaned": cdp, "matched_by": mb})
            if not has_pdf and exp2 > 0:
                out.append({"kind": "pdf_missing", "rel": rel, "exp": exp2, "got": 0,
                            "imgs": ni, "pdf_pages": npg,
                            "pristine": pdp, "cleaned": cdp, "matched_by": mb})
            elif got < exp2:
                out.append({"kind": "page_loss", "rel": rel, "exp": exp2, "got": got,
                            "imgs": ni, "pdf_pages": npg,
                            "pristine": pdp, "cleaned": cdp, "matched_by": mb})
        return out

    with ThreadPoolExecutor(max_workers=16) as ex:
        for t, rs in zip(tasks, ex.map(check, tasks)):
            done += 1
            job.step(done)
            job.set_current(t[0])
            for r in (rs or []):
                if r["kind"] == "pdf_missing":
                    pdf_missing.append(r)
                    job.bump("PDF缺失")
                    job.log(f"  ✗✗ PDF整体缺失(疑似误删) 原图片{r['imgs']}+原PDF页{r['pdf_pages']}  {r['rel']}")
                elif r["kind"] == "html_missing":
                    html_missing.append(r)
                    job.bump("html缺失")
                    job.log(f"  ✗✗ html源文件缺失 {r['got']}/{r['exp']}"
                            f"（缺 {'、'.join(r['lost'])}）  {r['rel']}")
                elif r["kind"] == "bad_source":
                    # **不计入闸门**：源文件本身不是图片（清洗器同样转不了、已跳过），
                    # 交付里少这一页不是清洗弄丢的。但也绝不能一声不吭 ——
                    # 它是"爬虫把错误页存成了 .png"的信号，得让用户去源头修。
                    bad_source.append(r)
                    job.bump("源文件损坏")
                    job.log(f"  ℹ 源文件不是有效图片，已跳过（交付里没有这一页）"
                            f"：{'、'.join(r['broken'])}  {r['rel']}")
                else:
                    losses.append(r)
                    job.bump("页数损失")
                    job.log(f"  ✗ 页数损失 exp={r['exp']} got={r['got']}  {r['rel']}")
            if not rs and done % 25 == 0:
                job.set_stat("已核对", done)
            if job.cancelled:
                break
    job.set_stat("已核对", done)
    ok = not losses and not pdf_missing and not unmatched and not html_missing

    # 按公司留档，供总览的「④ 复查」列使用。相对路径的第一段就是公司名
    # （清洗后目录的 类别X 层在上面已经剥掉，两侧都对齐到 公司/一级/二级/产品）。
    per_company = {}
    for rel in pris:
        per_company.setdefault(rel.split(os.sep)[0],
                               {"checked": 0, "losses": 0, "pdf_missing": 0,
                                "unmatched": 0, "html_missing": 0})
    for t in tasks:
        rel = t[0]
        per_company[rel.split(os.sep)[0]]["checked"] += 1
    for bucket, key in ((losses, "losses"), (pdf_missing, "pdf_missing"),
                        (html_missing, "html_missing")):
        for r in bucket:
            c = r["rel"].split(os.sep)[0]
            if c in per_company:
                per_company[c][key] += 1
    for rel in unmatched:
        c = rel.split(os.sep)[0]
        if c in per_company:
            per_company[c]["unmatched"] += 1
    # 明细也按公司留一份（每类最多 20 条）：总览的 ④ 状态格点开就要看这个。
    # 只存计数的话，用户看到"有损失"却不知道损失在哪，只能重跑一次全量复查才查得到。
    for bucket, key in ((losses, "loss_items"), (pdf_missing, "pdf_missing_items"),
                        (html_missing, "html_missing_items")):
        for r in bucket:
            c = r["rel"].split(os.sep)[0]
            if c in per_company:
                per_company[c].setdefault(key, [])
                if len(per_company[c][key]) < 20:
                    per_company[c][key].append(r)
    for rel in unmatched:
        c = rel.split(os.sep)[0]
        if c in per_company:
            per_company[c].setdefault("unmatched_items", [])
            if len(per_company[c]["unmatched_items"]) < 20:
                per_company[c]["unmatched_items"].append(rel)
    for r in per_company.values():
        r["ok"] = not (r["losses"] or r["pdf_missing"] or r["unmatched"] or r["html_missing"])
    # 被取消的任务没核完，结论不作数，不写记忆。
    # overview 依赖 pipeline，pipeline 又依赖本模块，所以在函数内导入避开循环引用。
    if not job.cancelled:
        from . import overview
        overview.record_audit(pristine_root, per_company)

    # 文件名沿用上面那趟 walk 的结果。这里曾经是 `os.listdir(d) for d in pris.values()`，
    # 把清洗前每一个产品目录**又列了一遍**，只为算这一个日志数字：全量复查一万多个目录
    # ≈ 90 秒纯等待，而且 2026-08-06 那次 ENOMEM 正是崩在这一行上。
    n_html_pris = sum(len(P.html_files(fns)) for _dp, fns in pris.values())
    job.log(f"审计完成：产品 清洗前{len(pris)}/清洗后{len(clean)}，核对 {len(tasks)}，"
            f"缺失产品 {len(unmatched)}，PDF整体缺失 {len(pdf_missing)}，"
            f"页数损失 {len(losses)}，html缺失 {len(html_missing)}（四项目标均为 0）")
    job.log(f"  html 源文件：清洗前共 {n_html_pris} 份"
            + ("，全部完好" if not html_missing else
               f"，其中 {len(html_missing)} 个产品的 html 在清洗侧缺失"))
    if bad_source:
        job.log(f"  ℹ 另有 {len(bad_source)} 个产品的源图片不是有效图片（扩展名与实际格式"
                f"不符，清洗器转不了、交付里没有这一页）—— 不影响结论，"
                f"但建议回源头修爬取：{'、'.join(r['rel'] for r in bad_source[:3])}"
                + ("…" if len(bad_source) > 3 else ""))
    history.add("audit", f"数据复查 × {len(tasks)} 产品"
                + (f"（{len(scope)} 家公司）" if scope else "（全量）"),
                sorted(scope)[:100],
                {"核对": len(tasks), "缺失产品": len(unmatched),
                 "PDF缺失": len(pdf_missing), "页数损失": len(losses),
                 "html缺失": len(html_missing), "源文件损坏": len(bad_source)},
                {"清洗后": cleaned_root, "清洗前": pristine_root}, ok)
    return {"checked": len(tasks),
            "companies": sorted(per_company),
            "count_pristine": len(pris), "count_cleaned": len(clean),
            "losses": losses[:500], "loss_count": len(losses),
            "pdf_missing": pdf_missing[:500], "pdf_missing_count": len(pdf_missing),
            "html_missing": html_missing[:500], "html_missing_count": len(html_missing),
            "html_pristine": n_html_pris,
            "unmatched": unmatched[:200], "unmatched_count": len(unmatched),
            "source_broken": bad_source[:500], "source_broken_count": len(bad_source),
            "ok": ok}
