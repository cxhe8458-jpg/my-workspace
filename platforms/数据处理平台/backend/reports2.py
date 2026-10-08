# -*- coding: utf-8 -*-
"""第二代 HTML 报告 —— 公司覆盖 / 周期进展。

和 htmlreport.py 的关系：那份是「分布报告」，带公司下钻联动，仍是默认模板，一行没动。
这里是**新增**的两个角度，共用同一条导出管线（同样零外部请求、同样自包含）：

  company —— 公司覆盖：232 家公司的集中度与品类宽度。回答"数据靠谁撑着"。
             数据源＝实时扫描目录树（需要 dist），另取映射记忆里的时间戳补"最后映射"。
  period  —— 周期进展：按周(六→五)的推进与质量趋势。回答"这周干了什么、质量往哪走"。
             数据源＝**只有映射记忆**，不需要扫描目录树 —— 所以这条路径跳过那个几分钟的
             全树 walk，导出是秒级的。周报要每周五发，不能每次都等六分钟。

排版沿用 frontend_v3 那套（细横线分区、等宽小字大字距做标签、衬线只给标题和大数字）。
图表色板与状态色语义原样沿用 htmlreport.PALETTE，一个色值都没改。

硬约束同样是**零外部请求**：样式、SVG、数据全部内联。
"""
import os, json, time, html, collections
from datetime import datetime, timedelta

from .paths import DATA_DIR
from .htmlreport import EXPORT_DIR, PALETTE, _md

# 与 frontend_v3 样图一致；__D1__ 这类占位符在末尾统一替换（CSS 里 % 太多，不能用 % 格式化）
CSS = """
:root{
 --paper:#fff;--paper-hi:#f7f9fc;--ink:#051c2c;--ink-md:#42566a;--ink-lo:#8595a6;
 --line:#dbe2ea;--line-lo:#eef1f6;--blue:#2251ff;
 --ok:#0e7c55;--warn:#a05a00;--err:#c0332b;--ai:#6a4fbf;
 --serif:"Source Han Serif SC","Noto Serif SC","Songti SC","SimSun",Georgia,serif;
 --sans:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",system-ui,sans-serif;
 --mono:"Cascadia Mono","JetBrains Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--paper);color:var(--ink);font-family:var(--sans);font-size:15px;
 line-height:1.7;-webkit-font-smoothing:antialiased}
.page{max-width:1120px;margin:0 auto;padding:0 28px}
.page>*{max-width:720px}
.page>.full,.page>.figure,.page>.tblbox{max-width:none}
.band{padding:42px 0 48px;border-top:1px solid var(--line-lo)}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.kicker{font-family:var(--mono);font-size:11px;letter-spacing:.26em;color:var(--blue);
 text-transform:uppercase;margin-bottom:13px}
h1{font-family:var(--serif);font-size:clamp(34px,4.6vw,50px);font-weight:700;line-height:1.1;
 letter-spacing:-.01em;margin:0 0 16px}
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
.metric.acc .val{color:var(--blue)}.metric.warn .val{color:var(--warn)}
.metric.ok .val{color:var(--ok)}.metric.ai .val{color:var(--ai)}
.figure{border-top:1px solid var(--line);padding:14px 0 6px;margin:32px 0 0}
.figure .cap{display:flex;justify-content:space-between;align-items:baseline;gap:16px;margin-bottom:6px}
.figure .cap .t{font-family:var(--serif);font-size:16px;font-weight:700}
.figure .cap .n{font-family:var(--mono);font-size:11px;color:var(--ink-lo)}
.figure .sub{font-size:13px;color:var(--ink-lo);margin-bottom:16px;line-height:1.55}
.bars{display:grid;grid-template-columns:max-content 1fr max-content max-content;gap:8px 13px;
 align-items:center}
.bars.np{grid-template-columns:max-content 1fr max-content}
.bars .nm{font-size:14px;white-space:nowrap}
.bars .tr{height:10px;background:var(--line-lo);position:relative}
.bars .tr i{position:absolute;left:0;top:0;bottom:0;background:var(--ink)}
.bars .vn{font-family:var(--mono);font-size:12px;text-align:right}
.bars .pc{font-family:var(--mono);font-size:11px;text-align:right;color:var(--ink-lo);min-width:44px}
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
.tag{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:11px;
 color:var(--ink-lo);white-space:nowrap}
.tag::before{content:"";width:6px;height:6px;background:var(--ink-lo)}
.tag.ok{color:var(--ok)}.tag.ok::before{background:var(--ok)}
.tag.warn{color:var(--warn)}.tag.warn::before{background:var(--warn)}
.tag.err{color:var(--err)}.tag.err::before{background:var(--err)}
.svg{width:100%;height:auto;margin-top:8px}
.svg .ax{font-family:var(--mono);font-size:9.5px;fill:var(--ink-lo)}
.svg .lb{font-family:var(--mono);font-size:10px;fill:var(--blue)}
.svg .vl{font-family:var(--mono);font-size:10px;fill:var(--ink-md)}
/* 未匹配率的点会落到实心蓝柱上，橙字压蓝底看不清 —— 描一圈白边再填色 */
.svg .ul{font-family:var(--mono);font-size:9.5px;fill:var(--warn);
 paint-order:stroke fill;stroke:#fff;stroke-width:3px;stroke-linejoin:round}
.hist{display:grid;gap:5px 0}
.hb{display:grid;grid-template-columns:26px 1fr 56px;gap:0 12px;align-items:center}
.hn{font-family:var(--mono);font-size:11.5px;color:var(--ink-lo);text-align:right}
.ht{height:12px;background:var(--line-lo);position:relative}
.ht i{position:absolute;left:0;top:0;bottom:0;background:var(--ink)}
.hv{font-family:var(--mono);font-size:11.5px;color:var(--ink-lo)}
.ai-sec{font-size:15px;line-height:1.75;color:var(--ink-md)}
.ai-sec h2,.ai-sec h3{font-family:var(--serif)}
.ai-sec ul,.ai-sec ol{margin:0 0 14px 22px}
.foot{border-top:1px solid var(--line);margin-top:40px;padding:18px 0 56px;
 font-family:var(--mono);font-size:11px;color:var(--ink-lo)}
@media print{.band{break-inside:avoid}}
"""


def _e(s):
    return html.escape(str(s))


def _f(n):
    return f"{n:,}"


def _metrics(items, cols=None):
    cols = cols or len(items)
    out = [f'<div class="metrics full" style="grid-template-columns:repeat({cols},1fr)">']
    for lab, val, unit, sub, cls in items:
        out.append(f'<div class="metric {cls}"><div class="lab">{lab}</div>'
                   f'<div class="val">{val}<small>{unit}</small></div>'
                   f'<div class="sub">{sub}</div></div>')
    return "".join(out) + "</div>"


def _bars(rows, total=None, color="var(--ink)", pct=True, unit="", scale=None):
    """pct=False 用于「值本身就是百分比」的场景 —— 那时不能再算占比，
    否则就是拿百分比去除以百分比之和，得出的数没有意义。"""
    if not rows:
        return ""
    mx = scale or max(r[1] for r in rows) or 1
    tot = (total or sum(r[1] for r in rows) or 1) if pct else 1
    out = [f'<div class="bars{"" if pct else " np"}">']
    for nm, n in rows:
        out.append(f'<span class="nm">{_e(nm)}</span>'
                   f'<span class="tr"><i style="width:{min(n/mx*100,100):.2f}%;background:{color}"></i></span>'
                   f'<span class="vn">{_f(n)}{unit}</span>'
                   + (f'<span class="pc">{n/tot*100:.1f}%</span>' if pct else ""))
    return "".join(out) + "</div>"


def _stack(rows, keep=6):
    s = sorted(rows, key=lambda x: -x[1])
    if len(s) > keep + 1:
        s = s[:keep] + [(f"其他（{len(s)-keep} 类）", sum(x[1] for x in s[keep:]))]
    tot = sum(x[1] for x in s) or 1
    bar = "".join(f'<i style="width:{n/tot*100:.2f}%;background:{PALETTE[i%8]}" '
                  f'title="{_e(nm)} {_f(n)}"></i>' for i, (nm, n) in enumerate(s))
    lg = "".join(f'<span class="li"><span class="sw" style="background:{PALETTE[i%8]}"></span>'
                 f'{_e(nm)} <span class="n">{_f(n)} · {n/tot*100:.1f}%</span></span>'
                 for i, (nm, n) in enumerate(s))
    return f'<div class="stack">{bar}</div><div class="legend">{lg}</div>'


def _figure(title, note, body, sub=""):
    s = f'<div class="sub">{sub}</div>' if sub else ""
    return (f'<div class="figure"><div class="cap"><span class="t">{title}</span>'
            f'<span class="n">{note}</span></div>{s}{body}</div>')


def _ai_section(analysis):
    if not analysis:
        return ""
    return f"""<section class="band"><div class="page">
  <div class="kicker">AI 分析</div><h2>模型解读</h2>
  <p class="note">由任务模型基于本报告的统计数据生成。分母（标准分类体系规模）由代码算好喂给模型，
     不让它自己推断。</p>
  <div class="ai-sec">{_md(analysis)}</div></div></section>"""


def _doc(title, body):
    return (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_e(title)}</title><style>{CSS}</style></head><body>{body}</body></html>')


def _week_start(d):
    return (d - timedelta(days=(d.weekday() - 5) % 7)).replace(
        hour=0, minute=0, second=0, microsecond=0)


# ══════════════════════════════════════════════════════════════════════════
#  公司覆盖
# ══════════════════════════════════════════════════════════════════════════
def build_company(dist, companies, reg, analysis="", note=""):
    bc = dist["by_company"]
    comps = [c for c in dist["companies"] if not companies or c in companies]
    tot = sum(bc[c]["total"] for c in comps)
    if not comps or not tot:
        raise ValueError("没有可导出的公司")
    srt = sorted(comps, key=lambda c: -bc[c]["total"])

    cum, pts, p50, p80 = 0, [], None, None
    for i, c in enumerate(srt, 1):
        cum += bc[c]["total"]
        pts.append((i, cum / tot * 100))
        if p50 is None and cum >= tot * .5:
            p50 = i
        if p80 is None and cum >= tot * .8:
            p80 = i
    p50, p80 = p50 or len(srt), p80 or len(srt)

    W, H, PADL, PADB = 760, 240, 40, 26
    n_1 = max(1, len(srt) - 1)
    px = lambda i: PADL + (i - 1) / n_1 * (W - PADL - 8)
    py = lambda v: H - PADB - v / 100 * (H - PADB - 10)
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
    pareto = (f'<svg viewBox="0 0 {W} {H}" class="svg full" role="img" '
              f'aria-label="公司产品量帕累托曲线">{grid}'
              f'<path d="{path}" fill="none" stroke="var(--ink)" stroke-width="1.6"/>{marks}'
              f'<text x="{PADL}" y="{H-8}" class="ax">产品量最大的公司 →</text>'
              f'<text x="{W-8}" y="{H-8}" text-anchor="end" class="ax">第 {len(srt)} 家</text></svg>')

    width = collections.Counter(len(bc[c]["cat1"]) for c in comps)
    wmax = max(width.values())
    hist = "".join(f'<div class="hb"><span class="hn">{k}</span>'
                   f'<span class="ht"><i style="width:{width[k]/wmax*100:.1f}%"></i></span>'
                   f'<span class="hv">{width[k]} 家</span></div>' for k in sorted(width))

    mono = [c for c in comps if bc[c]["cat1"] and bc[c]["cat1"][0]["n"] / bc[c]["total"] >= .9]
    small = [c for c in comps if bc[c]["total"] < 10]
    med = sorted(bc[c]["total"] for c in comps)[len(comps) // 2]

    trows = []
    for c in srt[:25]:
        b = bc[c]
        top = b["cat1"][0] if b["cat1"] else {"name": "—", "n": 0}
        conc = top["n"] / b["total"] * 100
        tg = ("ok", "专精") if conc >= 90 else (("", "偏重") if conc >= 60 else ("warn", "分散"))
        trows.append(f'<tr><td class="k">{_e(c)}</td><td class="n">{_f(b["total"])}</td>'
                     f'<td class="n">{len(b["cat1"])}</td><td>{_e(top["name"])}</td>'
                     f'<td class="n">{conc:.0f}%</td>'
                     f'<td><span class="tag {tg[0]}">{tg[1]}</span></td>'
                     f'<td class="n" style="color:var(--ink-lo)">'
                     f'{(reg.get(c) or {}).get("time","")[:10] or "—"}</td></tr>')

    srcrows = "".join(
        f'<tr><td class="k">{_e(s["label"])}</td><td class="n">{_f(s["companies"])}</td>'
        f'<td class="n">{_f(s["products"])}</td>'
        f'<td class="mono" style="font-size:11px;color:var(--ink-lo)">{_e(s["path"])}</td></tr>'
        for s in dist.get("sources", []) if s["products"])

    body = f"""<header class="hd"><div class="page">
  <div class="kicker">数据处理平台 / 覆盖报告</div>
  <h1>公司维度的数据分布</h1>
  <p class="lede">共 <b>{_f(len(comps))}</b> 家公司、<b>{_f(tot)}</b> 个产品。
    分布高度不均：产品量最大的 <b>{p50}</b> 家（占公司数 {p50/len(comps)*100:.0f}%）
    就贡献了一半产品，前 <b>{p80}</b> 家贡献 80%。</p>
  <div class="meta"><span>生成于 <b>{time.strftime('%Y-%m-%d %H:%M')}</b></span>
    <span>扫描于 <b>{_e(dist.get('scanned_at','—'))}</b></span>
    <span>口径 <b>实时扫描目录树</b></span></div>
  {f'<p class="note" style="margin-top:10px">{_e(note)}</p>' if note else ''}
</div></header>

<section class="band" style="border-top:0"><div class="page">
{_metrics([
  ("公司总数", _f(len(comps)), "家", "", ""),
  ("产品总数", _f(tot), "个", "", ""),
  ("撑起一半产品", f"{p50}", "家", f"占公司数 {p50/len(comps)*100:.0f}%", "acc"),
  ("高度专精", f"{len(mono)}", "家", "单一大类≥90%", ""),
  ("长尾公司", f"{len(small)}", "家", "不足 10 个产品", "warn" if small else ""),
  ("平均每家", f"{tot//len(comps)}", "个", f"中位数 {med}", ""),
], 6)}
{_figure("产品量的累计集中度", "帕累托", pareto,
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
    宽度 1 是纯单品类厂商，宽度 ≥6 通常是综合代理商或平台型企业。</p>
  {_figure("品类宽度分布", f"{len(comps)} 家公司", f'<div class="hist">{hist}</div>')}
  <p class="note"><b>{len(mono)}</b> 家（{len(mono)/len(comps)*100:.0f}%）高度专精：
    单一一级分类占了它 90% 以上的产品。这类公司的分类映射最不容易出错，
    宽度大的公司才是人工复核该盯的地方。</p>
</div></section>

<section class="band"><div class="page">
  <div class="kicker">02 / 明细</div>
  <h2>产品量前 {min(25,len(srt))} 家</h2>
  <div class="tblbox"><table>
    <thead><tr><th>公司</th><th class="n">产品数</th><th class="n">一级分类数</th>
      <th>主力品类</th><th class="n">集中度</th><th>形态</th><th>最后映射</th></tr></thead>
    <tbody>{"".join(trows)}</tbody></table></div>
</div></section>

<section class="band"><div class="page">
  <div class="kicker">03 / 来源</div><h2>数据来自哪里</h2>
  <div class="tblbox"><table>
    <thead><tr><th>来源</th><th class="n">公司数</th><th class="n">产品数</th><th>路径</th></tr></thead>
    <tbody>{srcrows}</tbody></table></div>
</div></section>
{_ai_section(analysis)}
<footer class="foot"><div class="page">公司覆盖报告 · 自包含单文件，无外部依赖</div></footer>"""
    return _doc("公司覆盖报告", body)


# ══════════════════════════════════════════════════════════════════════════
#  周期进展
# ══════════════════════════════════════════════════════════════════════════
def build_period(reg, range_label="", analysis="", note=""):
    byw = collections.defaultdict(lambda: {"co": 0, "p": 0, "un": 0, "ai": 0, "mp": 0,
                                           "c1": collections.Counter(), "list": []})
    for k, v in reg.items():
        try:
            d = datetime.strptime(v["time"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        w = byw[_week_start(d).date()]
        w["co"] += 1
        w["p"] += v.get("total", 0)
        w["un"] += v.get("unmatched", 0)
        w["ai"] += v.get("ai", 0)
        w["mp"] += v.get("mapped", 0)
        for kk, n in (v.get("dist") or {}).items():
            w["c1"][kk.split("/")[0]] += n
        w["list"].append((k, v))
    if not byw:
        raise ValueError("映射记忆里没有任何带时间戳的记录")

    weeks = sorted(byw)[-8:]
    cur = weeks[-1]
    prev = weeks[-2] if len(weeks) > 1 else None
    C = byw[cur]
    P = byw[prev] if prev else None
    rate = lambda w: (w["mp"] + w["ai"]) / w["p"] * 100 if w["p"] else 0
    unr = lambda w: w["un"] / w["p"] * 100 if w["p"] else 0

    def delta(now, before, unit="", inv=False):
        """inv=True 表示"降了才是好事"（未匹配率就是这种）。"""
        if before is None:
            return ""
        d = now - before
        if abs(d) < 1e-9:
            return "与上周持平"
        good = (d < 0) if inv else (d > 0)
        txt = f'{"+" if d>0 else ""}{d:,.1f}{unit}'.replace(".0", "")
        return f'<span style="color:{"var(--ok)" if good else "var(--warn)"}">{txt} 对比上周</span>'

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
            f'fill="{"var(--blue)" if cu else "#c8d3e4"}">'
            f'<title>{w} 起 · {_f(d["p"])} 个产品 · {d["co"]} 家</title></rect>'
            f'<text x="{x+bw/2:.1f}" y="{H-PADB+13:.1f}" text-anchor="middle" class="ax">'
            f'{w.strftime("%m-%d")}</text>'
            f'<text x="{x+bw/2:.1f}" y="{H-PADB-h-5:.1f}" text-anchor="middle" class="vl">'
            f'{_f(d["p"])}</text>')
        dots.append((x + bw / 2, PADT + (1 - unr(d) / umax) * (H - PADB - PADT), unr(d), w))
    line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in dots)
    dotsvg = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="#fff" stroke="var(--warn)" '
        f'stroke-width="1.6"><title>{w} 起 · 未匹配率 {v:.1f}%</title></circle>'
        f'<text x="{x:.1f}" y="{y-8:.1f}" text-anchor="middle" class="ul">{v:.1f}%</text>'
        for x, y, v, w in dots)
    trend = (f'<svg viewBox="0 0 {W} {H}" class="svg full" role="img" '
             f'aria-label="按周产品量与未匹配率趋势">{"".join(bars_svg)}'
             f'<path d="{line}" fill="none" stroke="var(--warn)" stroke-width="1.5"/>{dotsvg}'
             f'<line x1="{PADL-6}" y1="{H-PADB}" x2="{W-8}" y2="{H-PADB}" '
             f'stroke="var(--line)"/></svg>')

    def _wrow(w):
        d = byw[w]
        hi = ' style="background:var(--paper-hi)"' if w == cur else ""
        u = unr(d)
        tg = "ok" if u < 3 else ("warn" if u < 8 else "err")
        return (f'<tr{hi}><td class="k mono">{w} 起</td><td class="n">{d["co"]}</td>'
                f'<td class="n">{_f(d["p"])}</td><td class="n">{_f(d["mp"])}</td>'
                f'<td class="n">{_f(d["ai"])}</td><td class="n">{_f(d["un"])}</td>'
                f'<td class="n">{rate(d):.1f}%</td>'
                f'<td><span class="tag {tg}">{u:.1f}%</span></td></tr>')

    wrows = "".join(_wrow(w) for w in reversed(weeks))

    top = sorted(C["list"], key=lambda x: -x[1].get("total", 0))[:20]
    crows = "".join(
        f'<tr><td class="k">{_e(k)}</td><td class="n">{_f(v.get("total",0))}</td>'
        f'<td class="n">{_f(v.get("mapped",0))}</td><td class="n">{_f(v.get("ai",0))}</td>'
        f'<td class="n">{_f(v.get("unmatched",0)) if v.get("unmatched") else "—"}</td>'
        f'<td class="n" style="color:var(--ink-lo)">{_e(v.get("time","")[5:16])}</td></tr>'
        for k, v in top)

    body = f"""<header class="hd"><div class="page">
  <div class="kicker">数据处理平台 / 周期报告</div>
  <h1>本周期进展</h1>
  <p class="lede">{cur} 起至本周五，共 <b>{C['co']}</b> 家公司完成映射，
    产出 <b>{_f(C['p'])}</b> 个产品，规则与 AI 合计命中 <b>{rate(C):.1f}%</b>，
    未匹配率 <b>{unr(C):.1f}%</b>。</p>
  <div class="meta"><span>周期 <b>{cur} 六 → {cur+timedelta(days=6)} 五</b></span>
    <span>生成于 <b>{time.strftime('%Y-%m-%d %H:%M')}</b></span>
    <span>口径 <b>映射记忆快照</b></span></div>
  {f'<p class="note" style="margin-top:10px">{_e(note)}</p>' if note else ''}
</div></header>

<section class="band" style="border-top:0"><div class="page">
{_metrics([
  ("本周公司", f"{C['co']}", "家", delta(C['co'], P and P['co'], " 家"), "acc"),
  ("本周产品", _f(C['p']), "个", delta(C['p'], P and P['p'], ""), ""),
  ("映射成功率", f"{rate(C):.1f}", "%", delta(rate(C), P and rate(P), "pt"), "ok"),
  ("AI 研判", _f(C['ai']), "个", f"占 {C['ai']/C['p']*100:.1f}%" if C['p'] else "", "ai"),
  ("未匹配", _f(C['un']), "个", delta(unr(C), P and unr(P), "pt", inv=True),
   "warn" if C['un'] else ""),
  ("累计公司", _f(len(reg)), "家", "映射记忆总量", ""),
], 6)}
{_figure("按周推进与质量趋势", f"最近 {len(weeks)} 周", trend,
         "蓝柱＝该周映射的产品数（当前周高亮）；橙线＝该周的未匹配率。"
         "两条要一起看：产量涨而未匹配率没跟着涨，才说明产能是健康的。")}
</div></section>

<section class="band"><div class="page">
  <div class="kicker">01 / 构成</div><h2>本周期处理的是什么</h2>
  {_figure("本周映射产品的一级分类构成", f"{_f(sum(C['c1'].values()))} 个已分类",
           _stack(list(C['c1'].items())))}
</div></section>

<section class="band"><div class="page">
  <div class="kicker">02 / 逐周</div><h2>逐周对照</h2>
  <div class="tblbox"><table>
    <thead><tr><th>周期（六起）</th><th class="n">公司</th><th class="n">产品</th>
      <th class="n">规则映射</th><th class="n">AI 研判</th><th class="n">未匹配</th>
      <th class="n">成功率</th><th>未匹配率</th></tr></thead>
    <tbody>{wrows}</tbody></table></div>
</div></section>

<section class="band"><div class="page">
  <div class="kicker">03 / 本周明细</div><h2>本周期的 {C['co']} 家公司</h2>
  <p class="note">按产品数排序，只列前 {len(top)} 家。</p>
  <div class="tblbox"><table>
    <thead><tr><th>公司</th><th class="n">产品数</th><th class="n">规则映射</th>
      <th class="n">AI 研判</th><th class="n">未匹配</th><th>完成时间</th></tr></thead>
    <tbody>{crows}</tbody></table></div>
</div></section>
{_ai_section(analysis)}
<footer class="foot"><div class="page">周期进展报告 · 周界为周六 00:00 → 周五 23:59 ·
  一家公司整批计入它最后一次映射的时间</div></footer>"""
    return _doc("周期进展报告", body)


# ══════════════════════════════════════════════════════════════════════════
#  导出管线
# ══════════════════════════════════════════════════════════════════════════
TITLES = {"company": "公司覆盖报告", "period": "周期进展报告"}


def run_export(job, template, model_cfg=None, note="", *,
               dist=None, companies=None, reg=None, ai_payload=None):
    """生成并落盘。template ∈ {"company","period"}。"""
    from . import llm as llmmod
    from .report import ANALYZE_PROMPT
    os.makedirs(EXPORT_DIR, exist_ok=True)
    job.step(0, 3)

    analysis = ""
    if model_cfg and ai_payload is not None:
        job.set_current("正在调用任务模型生成分析 …")
        job.log("调用 AI 生成分析（可能需要 10-60 秒）…")
        text, err = llmmod.chat(model_cfg, ANALYZE_PROMPT + json.dumps(
            ai_payload, ensure_ascii=False, indent=1))
        if err:
            job.log(f"⚠ AI 分析调用失败，将只导出图表：{err}")
        else:
            analysis = text or ""
            job.log(f"AI 分析完成（{len(analysis)} 字）")
    elif model_cfg:
        job.log("本模板未接 AI 分析，只导出图表")
    job.step(1)

    job.set_current("正在生成 HTML 报告 …")
    if template == "company":
        doc = build_company(dist, companies or [], reg or {}, analysis, note)
    else:
        doc = build_period(reg or {}, analysis=analysis, note=note)
    job.step(2)

    title = TITLES.get(template, "统计报告")
    name = f"{title}_{time.strftime('%Y%m%d_%H%M%S')}.html"
    path = os.path.join(EXPORT_DIR, f"{job.id}.html")
    open(path, "w", encoding="utf-8").write(doc)
    job.step(3)
    size = os.path.getsize(path)
    job.log(f"✓ 导出完成：{name}（{size // 1024} KB，自包含、零外部请求）")
    return {"file": path, "name": name, "format": "html", "template": template,
            "has_analysis": bool(analysis), "analysis": analysis, "size": size}
