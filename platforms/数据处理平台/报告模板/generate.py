# -*- coding: utf-8 -*-
"""三份 HTML 报告模板的生成器 —— 样图阶段，先看效果再决定要不要并进 htmlreport.py。

用法：python3 报告模板/generate.py       （在平台根目录跑）

三份的分工（角度不同，不是同一份换皮）：
  A 分类覆盖  —— 分母是标准分类体系 160 个二级分类，回答"哪里还是空的"
  B 公司覆盖  —— 232 家公司的集中度与品类宽度，回答"数据靠谁撑着"
  C 周期进展  —— 按周(六→五)的推进与质量趋势，回答"这周干了什么、质量在往哪走"

设计语言沿用 frontend_v3 那套（Tufte/编辑部）：零卡片、细横线分区、
等宽小字大字距做标签、衬线只给标题和大数字。所有图都是内联 SVG/CSS，零外部依赖。
"""
import json, os, sys, time, collections
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "报告模板")
sys.path.insert(0, ROOT)


# ══════════════════════════════════════════════════════════════════════════
#  共用样式
# ══════════════════════════════════════════════════════════════════════════
CSS = """
:root{
  --paper:#fff; --paper-hi:#f7f9fc;
  --ink:#051c2c; --ink-md:#42566a; --ink-lo:#8595a6;
  --line:#dbe2ea; --line-lo:#eef1f6;
  --blue:#2251ff; --blue-lo:#7d9bff;
  --ok:#0e7c55; --warn:#a05a00; --err:#c0332b; --ai:#6a4fbf;
  --d1:#2A78D6; --d2:#EB6834; --d3:#1BAF7A; --d4:#EDA100;
  --d5:#E87BA4; --d6:#008300; --d7:#4A3AA7; --d8:#E34948;
  /* 衬线只给标题和大数字；密集小字走无衬线（Windows 上只有 SimSun，小字会发虚） */
  --serif:"Source Han Serif SC","Noto Serif SC","Songti SC","SimSun",Georgia,serif;
  --sans:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",system-ui,sans-serif;
  --mono:"Cascadia Mono","JetBrains Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--paper);color:var(--ink);font-family:var(--sans);
  font-size:15px;line-height:1.7;-webkit-font-smoothing:antialiased}
.page{max-width:1120px;margin:0 auto;padding:0 28px}
.page > *{max-width:720px}
.page > .full,.page > .figure,.page > .tblbox{max-width:none}
.band{padding:42px 0 48px;border-top:1px solid var(--line-lo)}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}

.kicker{font-family:var(--mono);font-size:11px;letter-spacing:.26em;color:var(--blue);
  text-transform:uppercase;margin-bottom:13px}
h1{font-family:var(--serif);font-size:clamp(34px,4.6vw,50px);font-weight:700;
  line-height:1.1;letter-spacing:-.01em;margin:0 0 16px}
h2{font-family:var(--serif);font-size:29px;font-weight:700;line-height:1.28;margin:0 0 14px}
h3{font-family:var(--serif);font-size:18px;font-weight:700;margin:26px 0 10px}
p{margin:0 0 13px;color:var(--ink-md)}
.lede{font-family:var(--serif);font-size:19px;line-height:1.62;color:var(--ink-md);margin-bottom:20px}
.note{font-size:13.5px;color:var(--ink-lo);line-height:1.62}
b,strong{font-weight:700;color:var(--ink)}
.hd{padding:52px 0 30px}
.meta{display:flex;flex-wrap:wrap;gap:0 26px;padding-top:13px;border-top:1px solid var(--line);
  font-family:var(--mono);font-size:11.5px;color:var(--ink-lo)}
.meta b{color:var(--ink);font-weight:400}

.metrics{display:grid;gap:0 24px;margin-top:26px}
.metric{border-top:1px solid var(--line);padding-top:9px;min-width:0}
.metric .lab{font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;color:var(--ink-lo);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.metric .val{font-family:var(--serif);font-size:33px;font-weight:700;line-height:1.15;margin-top:4px;
  font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.metric .val small{font-size:13px;font-weight:400;color:var(--ink-lo);margin-left:3px}
.metric .sub{font-family:var(--mono);font-size:10.5px;color:var(--ink-lo);margin-top:2px}
.metric.acc .val{color:var(--blue)} .metric.warn .val{color:var(--warn)}
.metric.ok .val{color:var(--ok)} .metric.ai .val{color:var(--ai)}

.figure{border-top:1px solid var(--line);padding:14px 0 6px;margin:32px 0 0}
.figure .cap{display:flex;justify-content:space-between;align-items:baseline;gap:16px;margin-bottom:6px}
.figure .cap .t{font-family:var(--serif);font-size:16px;font-weight:700}
.figure .cap .n{font-family:var(--mono);font-size:11px;color:var(--ink-lo)}
.figure .sub{font-size:13px;color:var(--ink-lo);margin-bottom:16px;line-height:1.55}

.bars{display:grid;grid-template-columns:max-content 1fr max-content max-content;
  gap:8px 13px;align-items:center}
.bars .nm{font-size:14px;white-space:nowrap}
.bars .tr{height:10px;background:var(--line-lo);position:relative}
.bars .tr i{position:absolute;left:0;top:0;bottom:0;background:var(--ink)}
.bars .vn{font-family:var(--mono);font-size:12px;text-align:right}
.bars .pc{font-family:var(--mono);font-size:11px;text-align:right;color:var(--ink-lo);min-width:44px}
.bars.np{grid-template-columns:max-content 1fr max-content}

.stack{display:flex;height:28px;width:100%}
.stack i{display:block;height:100%;box-shadow:inset -1px 0 0 #fff}
.stack i:last-child{box-shadow:none}
.legend{display:flex;flex-wrap:wrap;gap:5px 18px;margin-top:12px}
.legend .li{display:flex;align-items:center;gap:7px;font-size:13px;color:var(--ink-md)}
.legend .sw{width:9px;height:9px;flex:0 0 auto}
.legend .n{font-family:var(--mono);font-size:11px;color:var(--ink-lo)}

table{width:100%;border-collapse:collapse;margin-top:4px}
th{font-family:var(--mono);font-size:10.5px;font-weight:400;letter-spacing:.08em;text-align:left;
  color:var(--ink-lo);padding:7px 12px 7px 0;border-bottom:1px solid var(--line);
  text-transform:uppercase;white-space:nowrap}
td{padding:7px 12px 7px 0;border-bottom:1px solid var(--line-lo);color:var(--ink-md);
  font-size:14px;vertical-align:baseline}
td.n,th.n{text-align:right;font-family:var(--mono);font-size:12px;font-variant-numeric:tabular-nums}
td.k{color:var(--ink)}
tr:hover td{background:var(--paper-hi)}
.tblbox{overflow-x:auto}

.aside{margin:28px 0;padding:14px 0 15px;border-top:1px solid var(--line);
  border-bottom:1px solid var(--line);font-size:14.5px;line-height:1.65;color:var(--ink-md)}
.aside .h{font-family:var(--mono);font-size:10.5px;letter-spacing:.14em;color:var(--blue);
  text-transform:uppercase;margin-bottom:6px}
.aside.warn .h{color:var(--warn)}

.tag{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:11px;
  color:var(--ink-lo);white-space:nowrap}
.tag::before{content:"";width:6px;height:6px;background:var(--ink-lo)}
.tag.ok{color:var(--ok)} .tag.ok::before{background:var(--ok)}
.tag.warn{color:var(--warn)} .tag.warn::before{background:var(--warn)}
.tag.err{color:var(--err)} .tag.err::before{background:var(--err)}

.foot{border-top:1px solid var(--line);margin-top:40px;padding:18px 0 56px;
  font-family:var(--mono);font-size:11px;color:var(--ink-lo)}
@media print{.band{break-inside:avoid}}
"""

HEAD = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title><style>%s</style></head><body>"""


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def fm(n):
    return f"{n:,}"


def metrics(items, cols=None):
    """items: [(标签, 值, 单位, 副标, 类名)]"""
    cols = cols or len(items)
    out = [f'<div class="metrics full" style="grid-template-columns:repeat({cols},1fr)">']
    for lab, val, unit, sub, cls in items:
        out.append(f'<div class="metric {cls}"><div class="lab">{lab}</div>'
                   f'<div class="val">{val}<small>{unit}</small></div>'
                   f'<div class="sub">{sub}</div></div>')
    return "".join(out) + "</div>"


def bars(rows, total=None, color="var(--ink)", pct=True, unit="", scale=None):
    """rows: [(名称, 数值)] —— 横向条。

    pct=True  值是绝对数量，右侧补一列"占全体的百分比"
    pct=False 值本身就是百分比/比率，不能再算占比（算了就是拿百分比去除以百分比之和，
              会得出 9.2% 这种没有意义的数）—— 这时只显示值本身。
    scale     条长的归一基准，默认取最大值；给 100 就是"按百分比铺满"
    """
    if not rows:
        return ""
    mx = scale or max(r[1] for r in rows) or 1
    out = [f'<div class="bars{"" if pct else " np"}">']
    tot = (total or sum(r[1] for r in rows) or 1) if pct else 1
    for nm, n in rows:
        out.append(f'<span class="nm">{esc(nm)}</span>'
                   f'<span class="tr"><i style="width:{min(n/mx*100,100):.2f}%;background:{color}"></i></span>'
                   f'<span class="vn">{fm(n)}{unit}</span>'
                   + (f'<span class="pc">{n/tot*100:.1f}%</span>' if pct else ""))
    return "".join(out) + "</div>"


PAL = ["var(--d1)", "var(--d2)", "var(--d3)", "var(--d4)",
       "var(--d5)", "var(--d6)", "var(--d7)", "var(--d8)"]


def stack(rows, keep=6):
    """部分-整体堆叠条；超过 keep+1 项，尾部折叠成「其他」"""
    s = sorted(rows, key=lambda x: -x[1])
    if len(s) > keep + 1:
        s = s[:keep] + [(f"其他（{len(s)-keep} 类）", sum(x[1] for x in s[keep:]))]
    tot = sum(x[1] for x in s) or 1
    bar = "".join(f'<i style="width:{n/tot*100:.2f}%;background:{PAL[i%8]}" title="{esc(nm)} {fm(n)}"></i>'
                  for i, (nm, n) in enumerate(s))
    lg = "".join(f'<span class="li"><span class="sw" style="background:{PAL[i%8]}"></span>'
                 f'{esc(nm)} <span class="n">{fm(n)} · {n/tot*100:.1f}%</span></span>'
                 for i, (nm, n) in enumerate(s))
    return f'<div class="stack">{bar}</div><div class="legend">{lg}</div>'


def figure(title, note, body, sub=""):
    s = f'<div class="sub">{sub}</div>' if sub else ""
    return (f'<div class="figure"><div class="cap"><span class="t">{title}</span>'
            f'<span class="n">{note}</span></div>{s}{body}</div>')


# ══════════════════════════════════════════════════════════════════════════
#  载入数据
# ══════════════════════════════════════════════════════════════════════════
def load():
    from backend import stats as S, registry, pipeline
    cfg = pipeline.load_config()
    d = S.load_dist_cache()
    if not d:
        raise SystemExit("没有分布缓存，先在页面上跑一次「扫描类别分布」")
    tax = json.load(open(os.path.join(ROOT, "config", "company_categories.json"),
                         encoding="utf-8"))["categories"]
    return d, registry.load(), {k: list(v["children"]) for k, v in tax.items()}


def week_start(d):
    return (d - timedelta(days=(d.weekday() - 5) % 7)).replace(
        hour=0, minute=0, second=0, microsecond=0)


# ══════════════════════════════════════════════════════════════════════════
#  模板 A —— 分类覆盖：分母是标准分类体系，回答"哪里还是空的"
# ══════════════════════════════════════════════════════════════════════════
def report_a(dist, reg, std):
    have = collections.Counter()
    for c1, lst in dist["cat2_by_cat1"].items():
        for x in lst:
            have[f"{c1}/{x['name']}"] += x["n"]

    tot2 = sum(len(v) for v in std.values())
    cov2 = sum(1 for c1, ch in std.items() for c2 in ch if have.get(f"{c1}/{c2}", 0))
    empty = [(c1, c2) for c1, ch in std.items() for c2 in ch if not have.get(f"{c1}/{c2}", 0)]
    nonstd = [k for k in have
              if k.split("/")[0] not in std or k.split("/", 1)[1] not in std.get(k.split("/")[0], [])]
    per1 = {c1: (sum(1 for c2 in ch if have.get(f"{c1}/{c2}", 0)), len(ch),
                 sum(have.get(f"{c1}/{c2}", 0) for c2 in ch)) for c1, ch in std.items()}
    order = sorted(std, key=lambda c: -per1[c][2])

    # 覆盖矩阵：一行一个大类，一格一个二级分类。深浅＝产品数（对数分档），空白格描虚线。
    mx = max(have.values()) or 1
    def cell(c1, c2):
        n = have.get(f"{c1}/{c2}", 0)
        if not n:
            return (f'<i class="cx e" title="{esc(c1)}/{esc(c2)} —— 尚无产品"></i>')
        import math
        lv = min(4, int(math.log(n + 1) / math.log(mx + 1) * 5))
        return f'<i class="cx l{lv}" title="{esc(c1)}/{esc(c2)} {fm(n)} 个"></i>'

    rows = []
    for c1 in order:
        n, t, p = per1[c1]
        gap = "" if n == t else f'<span class="gp">缺 {t-n}</span>'
        rows.append(
            f'<div class="mrow"><span class="mn">{esc(c1)}</span>'
            f'<span class="mc">{"".join(cell(c1,c2) for c2 in std[c1])}</span>'
            f'<span class="mv mono">{n}/{t}</span>{gap}'
            f'<span class="mp mono">{fm(p)}</span></div>')
    matrix = f'<div class="matrix full">{"".join(rows)}</div>'

    covbars = bars([(c1, round(per1[c1][0] * 100 / per1[c1][1])) for c1 in
                    sorted(std, key=lambda c: (per1[c][0] / per1[c][1], per1[c][2]))],
                   color="var(--blue)", pct=False, unit="%", scale=100)

    etbl = "".join(
        f'<tr><td class="k">{esc(c1)}</td><td>{esc(c2)}</td>'
        f'<td class="n">{fm(per1[c1][2])}</td>'
        f'<td><span class="tag {"warn" if per1[c1][2] < 200 else ""}">'
        f'{"整类都薄" if per1[c1][2] < 200 else "该类有量，独缺此项"}</span></td></tr>'
        for c1, c2 in sorted(empty, key=lambda x: per1[x[0]][2]))

    warn = ""
    if nonstd:
        li = "、".join(f"<b>{esc(k)}</b>" for k in nonstd[:5])
        warn = (f'<div class="aside warn"><div class="h">命名不一致</div>'
                f'有 {len(nonstd)} 个分类出现在数据里、但不在标准体系中：{li}。'
                f'多半是同义词被当成了新类（例如「成像/虚拟现实」与标准的'
                f'「成像/虚拟现实增强现实」）。这类要么并回标准类，要么正式纳入体系，'
                f'留着会让覆盖率既算不准也对不齐。</div>')

    body = f"""<header class="hd"><div class="page">
  <div class="kicker">数据处理平台 / 覆盖报告</div>
  <h1>标准分类体系的覆盖情况</h1>
  <p class="lede">标准体系共 <b>{len(std)}</b> 个一级分类、<b>{tot2}</b> 个二级分类。
    当前数据覆盖到其中 <b>{cov2}</b> 个二级分类，覆盖率 <b>{cov2/tot2*100:.1f}%</b>，
    还有 <b>{len(empty)}</b> 个二级分类一个产品都没有。</p>
  <div class="meta"><span>生成于 <b>{time.strftime('%Y-%m-%d %H:%M')}</b></span>
    <span>扫描于 <b>{dist.get('scanned_at','—')}</b></span>
    <span>口径 <b>实时扫描目录树</b></span></div>
</div></header>

<section class="band" style="border-top:0"><div class="page">
{metrics([
  ("二级分类覆盖", f"{cov2}", f"/{tot2}", f"{cov2/tot2*100:.1f}%", "acc"),
  ("一级分类覆盖", f"{sum(1 for c in std if per1[c][2])}", f"/{len(std)}", "", "ok"),
  ("空白二级分类", f"{len(empty)}", "个", "一个产品都没有", "warn" if empty else ""),
  ("产品总数", fm(dist['total']), "个", "", ""),
  ("公司数", fm(len(dist['companies'])), "家", "", ""),
  ("非标分类", f"{len(nonstd)}", "个", "不在体系内", "warn" if nonstd else ""),
], 6)}

{figure("覆盖矩阵", f"{tot2} 个二级分类", matrix,
        "一行一个一级分类，一格一个二级分类。格子深浅＝产品数量（对数分档），"
        "虚线空格＝该二级分类尚无产品。鼠标悬停看具体数字。")}
</div></section>

<section class="band"><div class="page">
  <div class="kicker">01 / 缺口</div>
  <h2>缺口集中在哪</h2>
  <p class="note">覆盖率高不等于均匀。下面按覆盖百分比<b>升序</b>排 ——
     排在最上面的才是真正需要补数据的方向。</p>
  {figure("各一级分类的二级覆盖率", "百分比", covbars)}

  {warn}

  <h3>完全空白的 {len(empty)} 个二级分类</h3>
  <p class="note">按所属一级分类的产品量升序 —— 排在前面的是"整类都薄"，
     排在后面的是"这一类明明有量，偏偏缺这一项"，后者更值得优先补。</p>
  <div class="tblbox"><table>
    <thead><tr><th>一级分类</th><th>空白的二级分类</th><th class="n">该一级分类产品数</th><th>判断</th></tr></thead>
    <tbody>{etbl}</tbody></table></div>
</div></section>

<footer class="foot"><div class="page">分类覆盖报告 · 自包含单文件，无外部依赖 ·
  分母为 config/company_categories.json 定义的标准分类体系</div></footer>"""

    extra = """
.matrix{margin-top:4px}
.mrow{display:grid;grid-template-columns:150px 1fr 52px 54px 66px;gap:0 12px;align-items:center;
  padding:5px 0;border-bottom:1px solid var(--line-lo)}
.mn{font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mc{display:flex;flex-wrap:wrap;gap:3px}
.cx{width:13px;height:13px;display:block}
.cx.l0{background:#dde5ff}.cx.l1{background:#b3c5ff}.cx.l2{background:#7d9bff}
.cx.l3{background:#4a70f5}.cx.l4{background:#2251ff}
.cx.e{background:0;border:1px dashed #c0392b80}
.mv{font-size:11.5px;color:var(--ink-lo);text-align:right}
.gp{font-family:var(--mono);font-size:10.5px;color:var(--warn)}
.mp{font-size:11.5px;text-align:right;color:var(--ink)}
"""
    return HEAD % ("分类覆盖报告", CSS + extra) + body + "</body></html>"


# ══════════════════════════════════════════════════════════════════════════
#  模板 B —— 公司覆盖：232 家公司的集中度与品类宽度，回答"数据靠谁撑着"
# ══════════════════════════════════════════════════════════════════════════
def report_b(dist, reg, std):
    bc, comps, tot = dist["by_company"], dist["companies"], dist["total"]
    srt = sorted(comps, key=lambda c: -bc[c]["total"])

    cum, pts, p50, p80 = 0, [], None, None
    for i, c in enumerate(srt, 1):
        cum += bc[c]["total"]
        pts.append((i, cum / tot * 100))
        if p50 is None and cum >= tot * .5:
            p50 = i
        if p80 is None and cum >= tot * .8:
            p80 = i

    # 帕累托曲线（内联 SVG，无库）
    W, H, PADL, PADB = 760, 240, 40, 26
    def px(i):
        return PADL + (i - 1) / max(1, len(srt) - 1) * (W - PADL - 8)
    def py(v):
        return H - PADB - v / 100 * (H - PADB - 10)
    path = "M" + " L".join(f"{px(i):.1f},{py(v):.1f}" for i, v in pts)
    grid = "".join(
        f'<line x1="{PADL}" y1="{py(v):.1f}" x2="{W-8}" y2="{py(v):.1f}" stroke="var(--line-lo)"/>'
        f'<text x="{PADL-7}" y="{py(v)+3.5:.1f}" text-anchor="end" class="ax">{v}%</text>'
        for v in (0, 25, 50, 75, 100))
    marks = "".join(
        f'<line x1="{px(n):.1f}" y1="{py(0):.1f}" x2="{px(n):.1f}" y2="{py(v):.1f}" '
        f'stroke="var(--blue)" stroke-dasharray="3 3"/>'
        f'<circle cx="{px(n):.1f}" cy="{py(v):.1f}" r="3.5" fill="var(--blue)"/>'
        f'<text x="{px(n)+7:.1f}" y="{py(v)-7:.1f}" class="lb">前 {n} 家 · {v}%</text>'
        for n, v in ((p50, 50), (p80, 80)))
    pareto = f'''<svg viewBox="0 0 {W} {H}" class="svg full" role="img"
      aria-label="公司产品量帕累托曲线">{grid}
      <path d="{path}" fill="none" stroke="var(--ink)" stroke-width="1.6"/>{marks}
      <text x="{PADL}" y="{H-8}" class="ax">产品量最大的公司 →</text>
      <text x="{W-8}" y="{H-8}" text-anchor="end" class="ax">第 {len(srt)} 家</text></svg>'''

    width = collections.Counter(len(bc[c]["cat1"]) for c in comps)
    wmax = max(width.values())
    hist = "".join(
        f'<div class="hb"><span class="hn mono">{k}</span>'
        f'<span class="ht"><i style="width:{width[k]/wmax*100:.1f}%"></i></span>'
        f'<span class="hv mono">{width[k]} 家</span></div>'
        for k in sorted(width))

    mono = [c for c in comps if bc[c]["cat1"] and bc[c]["cat1"][0]["n"] / bc[c]["total"] >= .9]
    small = [c for c in comps if bc[c]["total"] < 10]
    src = collections.Counter(bc[c]["source"] for c in comps)

    trows = []
    for c in srt[:25]:
        b = bc[c]
        top = b["cat1"][0] if b["cat1"] else {"name": "—", "n": 0}
        conc = top["n"] / b["total"] * 100
        tag = ("ok", "专精") if conc >= 90 else (("", "偏重") if conc >= 60 else ("warn", "分散"))
        t = reg.get(c, {}).get("time", "")[:10]
        trows.append(
            f'<tr><td class="k">{esc(c)}</td><td class="n">{fm(b["total"])}</td>'
            f'<td class="n">{len(b["cat1"])}</td><td>{esc(top["name"])}</td>'
            f'<td class="n">{conc:.0f}%</td>'
            f'<td><span class="tag {tag[0]}">{tag[1]}</span></td>'
            f'<td class="n" style="color:var(--ink-lo)">{t or "—"}</td></tr>')

    srcrows = "".join(
        f'<tr><td class="k">{esc(s["label"])}</td><td class="n">{fm(s["companies"])}</td>'
        f'<td class="n">{fm(s["products"])}</td>'
        f'<td class="mono" style="font-size:11px;color:var(--ink-lo)">{esc(s["path"])}</td></tr>'
        for s in dist.get("sources", []) if s["products"])

    body = f"""<header class="hd"><div class="page">
  <div class="kicker">数据处理平台 / 覆盖报告</div>
  <h1>公司维度的数据分布</h1>
  <p class="lede">共 <b>{len(comps)}</b> 家公司、<b>{fm(tot)}</b> 个产品。
    分布高度不均：产品量最大的 <b>{p50}</b> 家（占公司数 {p50/len(comps)*100:.0f}%）
    就贡献了一半产品，前 <b>{p80}</b> 家贡献 80%。</p>
  <div class="meta"><span>生成于 <b>{time.strftime('%Y-%m-%d %H:%M')}</b></span>
    <span>扫描于 <b>{dist.get('scanned_at','—')}</b></span>
    <span>口径 <b>实时扫描目录树</b></span></div>
</div></header>

<section class="band" style="border-top:0"><div class="page">
{metrics([
  ("公司总数", fm(len(comps)), "家", "", ""),
  ("产品总数", fm(tot), "个", "", ""),
  ("撑起一半产品", f"{p50}", "家", f"占公司数 {p50/len(comps)*100:.0f}%", "acc"),
  ("高度专精", f"{len(mono)}", "家", "单一大类≥90%", ""),
  ("长尾公司", f"{len(small)}", "家", "不足 10 个产品", "warn"),
  ("平均每家", f"{tot//len(comps)}", "个", f"中位数 {sorted(bc[c]['total'] for c in comps)[len(comps)//2]}", ""),
], 6)}

{figure("产品量的累计集中度", "帕累托", pareto,
        "横轴是按产品量降序排的公司，纵轴是累计产品占比。曲线越靠左上，说明数据越依赖少数几家。")}

<div class="aside"><div class="h">怎么读这条曲线</div>
  {p50} 家撑起一半、{p80} 家撑起八成，意味着<b>数据质量的风险是集中的</b> ——
  这 {p80} 家里任何一家抓错、结构判错，影响面都远大于长尾那 {len(small)} 家小公司加起来。
  复核资源应该按这条曲线分配，而不是按公司数平均分。</div>
</div></section>

<section class="band"><div class="page">
  <div class="kicker">01 / 品类宽度</div>
  <h2>每家公司横跨几个一级分类</h2>
  <p class="note">宽度＝这家公司的产品落在几个不同的一级分类里。
    宽度 1 意味着纯单品类厂商，宽度 ≥6 通常是综合代理商或平台型企业。</p>
  {figure("品类宽度分布", f"{len(comps)} 家公司", f'<div class="hist">{hist}</div>')}
  <p class="note"><b>{len(mono)}</b> 家（{len(mono)/len(comps)*100:.0f}%）高度专精：
    单一一级分类占了它 90% 以上的产品。这类公司的分类映射最不容易出错，
    而宽度大的公司才是人工复核该盯的地方。</p>
</div></section>

<section class="band"><div class="page">
  <div class="kicker">02 / 明细</div>
  <h2>产品量前 25 家</h2>
  <div class="tblbox"><table>
    <thead><tr><th>公司</th><th class="n">产品数</th><th class="n">一级分类数</th>
      <th>主力品类</th><th class="n">集中度</th><th>形态</th><th>最后映射</th></tr></thead>
    <tbody>{"".join(trows)}</tbody></table></div>
</div></section>

<section class="band"><div class="page">
  <div class="kicker">03 / 来源</div>
  <h2>数据来自哪里</h2>
  <div class="tblbox"><table>
    <thead><tr><th>来源</th><th class="n">公司数</th><th class="n">产品数</th><th>路径</th></tr></thead>
    <tbody>{srcrows}</tbody></table></div>
  <p class="note" style="margin-top:12px">「待分类」的两个来源尚未进入映射输出，
    它们的 {fm(dist.get('extra_total',0))} 个产品计入总量但还没有分类结果。</p>
</div></section>

<footer class="foot"><div class="page">公司覆盖报告 · 自包含单文件，无外部依赖</div></footer>"""

    extra = """
.svg{width:100%;height:auto;margin-top:8px}
.svg .ax{font-family:var(--mono);font-size:9.5px;fill:var(--ink-lo)}
.svg .lb{font-family:var(--mono);font-size:10px;fill:var(--blue)}
.hist{display:grid;gap:5px 0}
.hb{display:grid;grid-template-columns:26px 1fr 56px;gap:0 12px;align-items:center}
.hn{font-size:11.5px;color:var(--ink-lo);text-align:right}
.ht{height:12px;background:var(--line-lo);position:relative}
.ht i{position:absolute;left:0;top:0;bottom:0;background:var(--ink)}
.hv{font-size:11.5px;color:var(--ink-lo)}
"""
    return HEAD % ("公司覆盖报告", CSS + extra) + body + "</body></html>"


# ══════════════════════════════════════════════════════════════════════════
#  模板 C —— 周期进展：按周(六→五)推进与质量趋势，就是每周五要发的那份
# ══════════════════════════════════════════════════════════════════════════
def report_c(dist, reg, std):
    byw = collections.defaultdict(lambda: {"co": 0, "p": 0, "un": 0, "ai": 0, "mp": 0,
                                           "c1": collections.Counter(), "list": []})
    for k, v in reg.items():
        try:
            d = datetime.strptime(v["time"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        w = byw[week_start(d).date()]
        w["co"] += 1
        w["p"] += v.get("total", 0)
        w["un"] += v.get("unmatched", 0)
        w["ai"] += v.get("ai", 0)
        w["mp"] += v.get("mapped", 0)
        for kk, n in (v.get("dist") or {}).items():
            w["c1"][kk.split("/")[0]] += n
        w["list"].append((k, v))

    weeks = sorted(byw)[-8:]
    cur, prev = weeks[-1], (weeks[-2] if len(weeks) > 1 else None)
    C, P = byw[cur], (byw[prev] if prev else None)
    rate = lambda w: (w["mp"] + w["ai"]) / w["p"] * 100 if w["p"] else 0
    unr = lambda w: w["un"] / w["p"] * 100 if w["p"] else 0

    def delta(now, before, unit="", inv=False):
        if before is None:
            return ""
        d = now - before
        if abs(d) < 1e-9:
            return "与上周持平"
        good = (d < 0) if inv else (d > 0)
        return f'<span style="color:{"var(--ok)" if good else "var(--warn)"}">' \
               f'{"+" if d>0 else ""}{d:,.1f}{unit} 对比上周</span>'.replace(".0", "")

    # 周趋势：产品量柱 + 未匹配率折线
    W, H, PADL, PADB, PADT = 760, 250, 42, 34, 14
    pmax = max(byw[w]["p"] for w in weeks) or 1
    umax = max(max(unr(byw[w]) for w in weeks), 1) * 1.25
    bw = (W - PADL - 14) / len(weeks)
    bars_svg, dots = [], []
    for i, w in enumerate(weeks):
        d = byw[w]
        x = PADL + i * bw
        h = d["p"] / pmax * (H - PADB - PADT)
        cu = (w == cur)
        bars_svg.append(
            f'<rect x="{x+bw*.18:.1f}" y="{H-PADB-h:.1f}" width="{bw*.64:.1f}" height="{h:.1f}" '
            f'fill="{"var(--blue)" if cu else "#c8d3e4"}"><title>{w} 起 · {fm(d["p"])} 个产品 · {d["co"]} 家</title></rect>'
            f'<text x="{x+bw/2:.1f}" y="{H-PADB+13:.1f}" text-anchor="middle" class="ax">{w.strftime("%m-%d")}</text>'
            f'<text x="{x+bw/2:.1f}" y="{H-PADB-h-5:.1f}" text-anchor="middle" class="vl">{fm(d["p"])}</text>')
        uy = PADT + (1 - unr(d) / umax) * (H - PADB - PADT)
        dots.append((x + bw / 2, uy, unr(d), w))
    line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in dots)
    dotsvg = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="#fff" stroke="var(--warn)" stroke-width="1.6">'
        f'<title>{w} 起 · 未匹配率 {v:.1f}%</title></circle>'
        f'<text x="{x:.1f}" y="{y-8:.1f}" text-anchor="middle" class="ul">{v:.1f}%</text>'
        for x, y, v, w in dots)
    trend = f'''<svg viewBox="0 0 {W} {H}" class="svg full" role="img" aria-label="按周产品量与未匹配率趋势">
      {"".join(bars_svg)}
      <path d="{line}" fill="none" stroke="var(--warn)" stroke-width="1.5"/>{dotsvg}
      <line x1="{PADL-6}" y1="{H-PADB}" x2="{W-8}" y2="{H-PADB}" stroke="var(--line)"/></svg>'''

    wrows = "".join(
        f'<tr{" style=\"background:var(--paper-hi)\"" if w == cur else ""}>'
        f'<td class="k mono">{w} 起</td><td class="n">{byw[w]["co"]}</td>'
        f'<td class="n">{fm(byw[w]["p"])}</td><td class="n">{fm(byw[w]["mp"])}</td>'
        f'<td class="n">{fm(byw[w]["ai"])}</td><td class="n">{fm(byw[w]["un"])}</td>'
        f'<td class="n">{rate(byw[w]):.1f}%</td>'
        f'<td><span class="tag {"ok" if unr(byw[w])<3 else ("warn" if unr(byw[w])<8 else "err")}">'
        f'{unr(byw[w]):.1f}%</span></td></tr>'
        for w in reversed(weeks))

    top = sorted(C["list"], key=lambda x: -x[1].get("total", 0))[:20]
    crows = "".join(
        f'<tr><td class="k">{esc(k)}</td><td class="n">{fm(v.get("total",0))}</td>'
        f'<td class="n">{fm(v.get("mapped",0))}</td><td class="n">{fm(v.get("ai",0))}</td>'
        f'<td class="n">{fm(v.get("unmatched",0)) if v.get("unmatched") else "—"}</td>'
        f'<td class="n" style="color:var(--ink-lo)">{v.get("time","")[5:16]}</td></tr>'
        for k, v in top)

    body = f"""<header class="hd"><div class="page">
  <div class="kicker">数据处理平台 / 周期报告</div>
  <h1>本周期进展</h1>
  <p class="lede">{cur} 起至本周五，共 <b>{C['co']}</b> 家公司完成映射，
    产出 <b>{fm(C['p'])}</b> 个产品，规则与 AI 合计命中 <b>{rate(C):.1f}%</b>，
    未匹配率 <b>{unr(C):.1f}%</b>。</p>
  <div class="meta"><span>周期 <b>{cur} 六 → {(cur+timedelta(days=6))} 五</b></span>
    <span>生成于 <b>{time.strftime('%Y-%m-%d %H:%M')}</b></span>
    <span>口径 <b>映射记忆快照</b></span></div>
</div></header>

<section class="band" style="border-top:0"><div class="page">
{metrics([
  ("本周公司", f"{C['co']}", "家", delta(C['co'], P and P['co'], " 家"), "acc"),
  ("本周产品", fm(C['p']), "个", delta(C['p'], P and P['p'], ""), ""),
  ("映射成功率", f"{rate(C):.1f}", "%", delta(rate(C), P and rate(P), "pt"), "ok"),
  ("AI 研判", fm(C['ai']), "个", f"占 {C['ai']/C['p']*100:.1f}%" if C['p'] else "", "ai"),
  ("未匹配", fm(C['un']), "个", delta(unr(C), P and unr(P), "pt", inv=True), "warn" if C['un'] else ""),
  ("累计公司", fm(len(reg)), "家", "映射记忆总量", ""),
], 6)}

{figure("按周推进与质量趋势", f"最近 {len(weeks)} 周", trend,
        "蓝柱＝该周映射的产品数（当前周高亮）；橙线＝该周的未匹配率。"
        "两条线要一起看：产量涨而未匹配率没跟着涨，才说明产能是健康的。")}
</div></section>

<section class="band"><div class="page">
  <div class="kicker">01 / 构成</div>
  <h2>本周期处理的是什么</h2>
  {figure("本周映射产品的一级分类构成", f"{fm(sum(C['c1'].values()))} 个已分类",
          stack(list(C['c1'].items())))}
</div></section>

<section class="band"><div class="page">
  <div class="kicker">02 / 逐周</div>
  <h2>逐周对照</h2>
  <div class="tblbox"><table>
    <thead><tr><th>周期（六起）</th><th class="n">公司</th><th class="n">产品</th>
      <th class="n">规则映射</th><th class="n">AI 研判</th><th class="n">未匹配</th>
      <th class="n">成功率</th><th>未匹配率</th></tr></thead>
    <tbody>{wrows}</tbody></table></div>
</div></section>

<section class="band"><div class="page">
  <div class="kicker">03 / 本周明细</div>
  <h2>本周期的 {C['co']} 家公司</h2>
  <p class="note">按产品数排序，只列前 20 家。</p>
  <div class="tblbox"><table>
    <thead><tr><th>公司</th><th class="n">产品数</th><th class="n">规则映射</th>
      <th class="n">AI 研判</th><th class="n">未匹配</th><th>完成时间</th></tr></thead>
    <tbody>{crows}</tbody></table></div>
</div></section>

<footer class="foot"><div class="page">周期进展报告 · 周界为周六 00:00 → 周五 23:59 ·
  一家公司整批计入它最后一次映射的时间</div></footer>"""

    extra = """
.svg{width:100%;height:auto;margin-top:8px}
.svg .ax{font-family:var(--mono);font-size:9.5px;fill:var(--ink-lo)}
.svg .vl{font-family:var(--mono);font-size:10px;fill:var(--ink-md)}
/* 未匹配率的点会落到柱子上（当前周的柱是实心蓝），橙字压蓝底基本看不清。
   给文字描一圈白边再填色 —— paint-order 让描边先画、文字盖在上面。 */
.svg .ul{font-family:var(--mono);font-size:9.5px;fill:var(--warn);
  paint-order:stroke fill;stroke:#fff;stroke-width:3px;stroke-linejoin:round}
"""
    return HEAD % ("周期进展报告", CSS + extra) + body + "</body></html>"


# ══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    dist, reg, std = load()
    for name, fn in (("模板A_分类覆盖.html", report_a),
                     ("模板B_公司覆盖.html", report_b),
                     ("模板C_周期进展.html", report_c)):
        p = os.path.join(OUT, name)
        html = fn(dist, reg, std)
        open(p, "w", encoding="utf-8").write(html)
        print(f"  ✓ {name}  {len(html)/1024:.0f} KB")