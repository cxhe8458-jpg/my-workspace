# -*- coding: utf-8 -*-
"""页面4：数据清洗（移植自 product-data-pipeline/consolidate.py，已修复全部已知坑）
每个产品折叠为 <父文件夹名>.json + <父文件夹名>.pdf：
  操作1: description.txt 并入 json 的「产品描述」→ 删 txt → info.json 改名 <父名>.json
  操作2: 图片(主图*→其他产品图→参数图*)各自转 PDF 后前置合并到说明书 PDF 最前面；
         多 PDF 时图片并入第一个，其余改名 <父名>_N.pdf；生成后删除原图片和已并入的原 PDF。
已修复的坑：
  - /mnt/d NTFS 大小写不敏感：原 PDF 名与 <父名>.pdf 仅大小写不同也是同一文件，
    删除守卫用小写绝对路径比较，防止把刚合并好的 PDF 自己删掉。
  - SVG 伪装 .jpg → cairosvg 转 PDF 合入，不丢弃。
  - 无效/损坏图片 → 跳过该图不中断；全部无效 → 不生成 0 页 PDF。
  - 损坏 PDF → strict=False / pikepdf 修复后合并。
保护原始数据：落地时必须指定输出目录，先整体复制到输出目录再清洗副本，输入目录零改动。

⚠ **html 源文件全程不动**：不并入 PDF、不改名、不删除，原样留在产品目录里作为交付物的一部分。
清洗只认图片与 PDF，html 既不在 `order_images` 里也不在 `pdfs` 里，两处删除循环都碰不到它。
一个「只有 html」的产品会走 `not images and not pdfs` 提前返回，html 完好无损。
"""
import os, re, io, json, shutil
from pypdf import PdfWriter, PdfReader
from . import history
from . import products as P
# 页数统计只留一份实现（两边各写一份迟早分叉，而这一份里藏着不能省的 close()）。
# ④ 的 audit 是页数会计的主场，所以口径放在那里；它模块级只依赖 history/products，
# 不会与 ③ 这条链构成循环导入。
from .audit import pdf_pages

# 口径统一到 products.IMG_EXT。以前这里少了 .gif/.svg，而 classify3 的残留检查含 .gif ——
# 一个 .gif 不会被合并却会被记成残留，verify_ok 永假、清洗记忆永远不写，这家公司
# 每次重跑都白跑（旧缺陷 #3）。两种格式 img_to_pdf_bytes 都能转：
# .svg 走 cairosvg 分支，.gif 走 img2pdf 失败后的 PIL 兜底（取首帧）。
IMG_EXT = P.IMG_EXT


def natkey(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def img_to_pdf_bytes(path, stats):
    try:
        with open(path, "rb") as f:
            head = f.read(300)
        low = head.lower()
        if b"<svg" in low or head.lstrip().startswith(b"<?xml"):
            import cairosvg
            stats["svg"] += 1
            return cairosvg.svg2pdf(url=path)
    except Exception:
        pass
    try:
        import img2pdf
        return img2pdf.convert(path)
    except Exception:
        try:
            from PIL import Image
            im = Image.open(path).convert("RGB")
            buf = io.BytesIO(); im.save(buf, "PDF")
            return buf.getvalue()
        except Exception:
            stats["bad_img"] += 1
            return None


def order_images(files):
    imgs = [f for f in files if f.lower().endswith(IMG_EXT)]
    param = sorted([f for f in imgs if "参数" in f], key=natkey)
    prod = [f for f in imgs if "参数" not in f]
    main = sorted([f for f in prod if f.startswith("主图")], key=natkey)
    other = sorted([f for f in prod if not f.startswith("主图")], key=natkey)
    return main + other + param        # 主图 → 其他产品图 → 参数图


def _append_pdf(writer, path):
    try:
        writer.append(path)
        return True
    except Exception:
        pass
    try:
        writer.append(PdfReader(path, strict=False))
        return True
    except Exception:
        pass
    try:  # 破损 PDF：pikepdf 修复后再并
        import pikepdf
        buf = io.BytesIO()
        with pikepdf.open(path) as pk:
            pk.save(buf)
        buf.seek(0)
        writer.append(PdfReader(buf, strict=False))
        return True
    except Exception:
        return False


def process_product(d, apply, stats, log):
    parent = os.path.basename(d)
    fns = os.listdir(d)
    # html 源文件：只清点、不处理。计数进 stats 是为了让"html 有没有跟过来"
    # 在任务日志里直接看得见，而不是等 ④ 复查才发现少了。
    n_html = len(P.html_files(fns))
    if n_html:
        stats["html"] = stats.get("html", 0) + n_html
    # ---- 操作1: json ----
    js = [f for f in fns if f.lower().endswith(".json")]
    ip = os.path.join(d, "info.json") if "info.json" in fns else (os.path.join(d, js[0]) if js else None)
    if ip and os.path.exists(ip):
        try:
            info = json.load(open(ip, encoding="utf-8"))
        except Exception:
            info = {}
        dp_txt = os.path.join(d, "description.txt")
        if os.path.exists(dp_txt):
            info["产品描述"] = open(dp_txt, encoding="utf-8").read()
            if apply:
                os.remove(dp_txt)
            stats["desc_merged"] += 1
        jname = os.path.join(d, parent + ".json")
        if apply:
            json.dump(info, open(jname, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            if os.path.abspath(jname).lower() != os.path.abspath(ip).lower():
                os.remove(ip)
        if os.path.basename(ip) != parent + ".json":
            stats["json_renamed"] += 1
    # ---- 操作2: pdf ----
    fns = os.listdir(d)
    images = order_images(fns)
    # 注意：不排除与父文件夹同名的 PDF —— 它是待合并的原件（先读入内存写 tmp，最后才替换），
    # 排除它会导致其页面丢失。重跑已清洗目录时会原样重建 <父名>.pdf，内容幂等。
    pdfs = sorted([f for f in fns if f.lower().endswith(".pdf")
                   and not re.fullmatch(re.escape(parent) + r"_\d+\.pdf", f, re.I)], key=natkey)
    if not images and not pdfs:
        stats["no_media"] += 1
        return
    if not apply:
        stats["merge_planned"] += 1
        return
    tmp = os.path.join(d, ".__clean_tmp.pdf")
    w = PdfWriter()
    for img in images:
        b = img_to_pdf_bytes(os.path.join(d, img), stats)
        if b is None:
            log(f"  ⚠ 无效图片已跳过: {os.path.join(parent, img)}")
            continue
        w.append(io.BytesIO(b))
    if pdfs:
        if not _append_pdf(w, os.path.join(d, pdfs[0])):
            log(f"  ⚠ PDF 无法读取(已保留原件): {os.path.join(parent, pdfs[0])}")
            stats["bad_pdf"] += 1
            p0 = os.path.join(d, pdfs[0])
            # 损坏原件与最终名同名时先改名保留，防止被合并产物覆盖
            if os.path.abspath(p0).lower() == os.path.abspath(os.path.join(d, parent + ".pdf")).lower():
                os.rename(p0, os.path.join(d, parent + "_损坏原件.pdf"))
            pdfs = []  # 原 PDF 保留不删，图片单独成 PDF
    with open(tmp, "wb") as f:
        w.write(f)
    # 合并结果是本目录里最大的一份数据（整本说明书 + 全部图片）。writer 与 reader 都要
    # 显式关掉：pypdf 的引用环让它们靠引用计数回收不掉，会一路堆到分代 GC 才释放，
    # 而清洗是几千个产品连着跑的。页数统计走 audit.pdf_pages（唯一口径，见那里的长注释）。
    w.close()
    n = pdf_pages(tmp) or 0
    if n < 1:
        os.remove(tmp)
        for img in images:  # 全部图片无效：删无效图，只留 json
            p = os.path.join(d, img)
            if os.path.exists(p):
                os.remove(p)
        return
    for k, extra in enumerate(pdfs[1:], start=1):
        os.rename(os.path.join(d, extra), os.path.join(d, f"{parent}_{k}.pdf"))
    final = os.path.join(d, parent + ".pdf")
    if os.path.exists(final) and os.path.abspath(final).lower() != os.path.abspath(tmp).lower():
        os.remove(final)
    os.rename(tmp, final)
    for img in images:
        p = os.path.join(d, img)
        if os.path.exists(p):
            os.remove(p)
    if pdfs:
        p0 = os.path.join(d, pdfs[0])
        # ★大小写守卫：原 PDF 名 = <父名>.pdf（含仅大小写不同）时禁止删除，否则会删掉刚生成的合并 PDF
        if os.path.exists(p0) and os.path.abspath(p0).lower() != os.path.abspath(final).lower():
            os.remove(p0)
    stats["merged"] += 1
    stats["pages"] += n


# 注：曾有 run_clean() / find_products() / _copytree_progress() 一组旧的「全量清洗」入口，
# 已无任何调用方（清洗现在由 consolidate 逐产品调 process_product 完成），其 apply 参数
# 同样会被误读成本模块支持 dry-run。已删除。
