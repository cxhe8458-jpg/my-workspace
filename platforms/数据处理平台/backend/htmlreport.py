# -*- coding: utf-8 -*-
"""自包含 HTML 报告 —— 导出一个双击就能看的文件，交互和统计页完全一致。

为什么不是 Excel：用户要的是"和页面展示的一样，可以筛选、图表实时变化"。
Excel 里做到这一点只有数据透视图一条路，而 openpyxl 生成不了可靠的透视表/切片器，
只能靠维护一个二进制模板；更要命的是透视图里没法守住本项目的图表约束
（横向条形、绝不饼图/双轴、7 色上限）—— 领导自己一拖就能拖出个饼图。

HTML 报告没有这些问题：筛选、下钻、联动都是我们自己的代码，色板和图形规则原样带过去。
唯一的代价是它不是 Excel，所以 xlsx 导出仍然保留，两个入口并存。

硬约束：**零外部请求**。样式、脚本、数据全部内联，断网也能打开。
"""
import os, json, time, html

from .paths import DATA_DIR

EXPORT_DIR = os.path.join(DATA_DIR, "exports")

# 与前端 --d1…--d8 / report.PALETTE 同一套（dataviz 已验证，固定顺序不循环取色）
PALETTE = ["#2A78D6", "#EB6834", "#1BAF7A", "#EDA100",
           "#E87BA4", "#008300", "#4A3AA7", "#E34948"]

# 注意：这段 CSS 里有大量 `%`（100% / 40% …），所以**不能用 % 格式化**，
# 只能用占位符替换 —— 曾经用 `CSS % PALETTE[0]` 直接把整个后端启动搞挂了。
CSS = """
:root{--bg:#eff1f0;--panel:#fff;--panel2:#f6f7f6;--ink:#14181d;--ink2:#39424c;
 --muted:#5c6672;--faint:#8a94a0;--line:#dfe3e1;--line2:#eceeed;--ok:#0e7c55;--d1:__D1__;
 --font:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",system-ui,sans-serif;
 --mono:"Cascadia Mono",Consolas,ui-monospace,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font-family:var(--font);font-size:14px;line-height:1.6}
.wrap{max-width:1240px;margin:0 auto;padding:28px 24px 70px}
h1{font-size:22px;font-weight:650;letter-spacing:-.01em}
h2{font-size:14px;font-weight:650;margin-bottom:4px}
.sub{color:var(--muted);font-size:13px;margin-top:4px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin-bottom:16px}
.desc{color:var(--muted);font-size:12.5px;margin-bottom:14px}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px}
select{background:var(--panel);border:1px solid var(--line);border-radius:6px;color:var(--ink);
 padding:7px 11px;font-size:13px;font-family:inherit;max-width:340px}
button{background:transparent;border:1px solid var(--line);border-radius:6px;color:var(--ink2);
 padding:5px 12px;font-size:12.5px;font-family:inherit;cursor:pointer}
button:hover{background:var(--panel2)}
button.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:6px 0 4px}
.stat{background:var(--panel2);border:1px solid var(--line);border-radius:6px;padding:13px 15px}
.stat .v{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:23px;font-weight:650;line-height:1.2}
.stat .k{color:var(--muted);font-size:12px;margin-top:2px}
.stat .v small{font-size:14px;color:var(--muted);font-weight:500}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(440px,1fr));gap:16px}
.ct{font-size:13.5px;font-weight:650;margin:0 0 3px}
.cs{font-size:12px;color:var(--muted);margin-bottom:10px}
.bars{display:flex;flex-direction:column;gap:3px}
.brow{display:flex;align-items:center;gap:10px;padding:2px 0;border-radius:4px;transition:opacity .16s}
.brow.clk{cursor:pointer}
.bars.hov .brow{opacity:.4}
.bars.hov .brow.on{opacity:1}
.bl{width:180px;flex-shrink:0;text-align:right;font-size:12.5px;color:var(--ink2);
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bt{flex:1;display:flex;align-items:center;gap:7px;min-width:0}
.bf{height:15px;border-radius:0 4px 4px 0;min-width:2px;background:var(--d1);transition:width .45s cubic-bezier(.22,.61,.36,1)}
.bv{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:12px;color:var(--muted);white-space:nowrap}
.crumb{display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--muted);margin-bottom:10px;min-height:22px}
.crumb button{border:0;background:none;color:#23408e;padding:0}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-weight:500;font-size:11.5px;color:var(--faint);padding:0 10px 8px;
 border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--line2)}
td.n,th.n{font-family:var(--mono);font-variant-numeric:tabular-nums;text-align:right}
details{margin-top:12px}
summary{cursor:pointer;font-size:12.5px;color:var(--muted);list-style:none}
summary::-webkit-details-marker{display:none}
summary:hover{color:var(--ink)}
.box{max-height:260px;overflow:auto;border:1px solid var(--line);border-radius:6px;margin-top:8px}
.ai{white-space:normal;line-height:1.75;color:var(--ink2)}
.ai h3{margin:14px 0 6px;color:var(--ink);font-size:13.5px}
.foot{color:var(--faint);font-size:12px;margin-top:24px;text-align:center}
@media print{body{background:#fff}.card{break-inside:avoid}.bar{display:none}}
""".replace("__D1__", PALETTE[0])

JS = r"""
const D = __DATA__;
let comp = "", cat1 = "";
const fmt = n => (n||0).toLocaleString("en-US");
const esc = s => String(s??"").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

function scope(){
  if (comp && D.by_company[comp]) {
    const b = D.by_company[comp];
    return {total:b.total, cat1:b.cat1, cat2:b.cat2, label:comp};
  }
  const flat = [];
  Object.entries(D.cat2_by_cat1).forEach(([c1,arr]) => arr.forEach(x => flat.push({name:c1+"/"+x.name, n:x.n})));
  flat.sort((a,b)=>b.n-a.n);
  return {total:D.total, cat1:D.overall_cat1, cat2:flat, label:"全部公司"};
}

/* 单系列比大小一律一个蓝、不放图例（标题已说明它是什么）—— 与平台内的规则一致 */
function bars(box, items, {clickable=false, onPick=null, empty="这个范围下还没有产品。"}={}){
  if(!items.length){ box.innerHTML = '<p class="cs">'+empty+'</p>'; return; }
  const m = Math.max(...items.map(x=>x.n),1), tot = items.reduce((s,x)=>s+x.n,0);
  box.className = "bars";
  box.innerHTML = items.map((x,i)=>`
    <div class="brow${clickable?" clk":""}" data-i="${i}">
      <div class="bl" title="${esc(x.name)}">${esc(x.name)}</div>
      <div class="bt"><div class="bf" style="width:${Math.max(x.n*100/m,.6)}%"></div>
        <span class="bv">${fmt(x.n)}　${(x.n*100/tot).toFixed(1)}%</span></div>
    </div>`).join("");
  box.querySelectorAll(".brow").forEach(row=>{
    const x = items[+row.dataset.i];
    row.onmouseenter = ()=>{box.classList.add("hov");row.classList.add("on");};
    row.onmouseleave = ()=>{box.classList.remove("hov");row.classList.remove("on");};
    if(clickable&&onPick) row.onclick = ()=>onPick(x.name);
  });
}

function table(headers, rows, title){
  if(!rows.length) return "";
  return `<details><summary>查看${esc(title)}数据表</summary><div class="box"><table>
    <thead><tr>${headers.map((h,i)=>`<th${i?' class="n"':""}>${esc(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(r=>`<tr>${r.map((c,i)=>`<td${i?' class="n"':""}>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div></details>`;
}

function render(){
  const d = scope();
  const cov = (n,all)=> all ? `${n}<small> / ${all}</small>` : n;
  document.getElementById("kpi").innerHTML = `
    <div class="stat"><div class="v">${fmt(d.total)}</div><div class="k">${esc(d.label)} · 已分类产品</div></div>
    <div class="stat"><div class="v">${cov(d.cat1.length, D.taxonomy.cat1)}</div><div class="k">覆盖的一级分类（分母＝标准体系）</div></div>
    <div class="stat"><div class="v">${cov(d.cat2.length, D.taxonomy.cat2)}</div><div class="k">覆盖的二级分类（分母＝标准体系）</div></div>
    ${comp?"":`<div class="stat"><div class="v">${D.companies.length}</div><div class="k">公司数</div></div>`}`;

  bars(document.getElementById("c1"), d.cat1, {clickable:true, onPick:n=>{cat1=n;render();}});
  document.getElementById("c1t").innerHTML = table(["一级分类","产品数"], d.cat1.map(x=>[x.name,fmt(x.n)]), "一级分类");

  let rows, title, sub;
  if(cat1){
    rows = d.cat2.filter(x=>x.name.startsWith(cat1+"/")).map(x=>({name:x.name.slice(cat1.length+1),n:x.n}));
    title = cat1+" 下的二级分类";
    sub = `${d.label} · ${rows.length} 个二级分类，合计 ${fmt(rows.reduce((s,x)=>s+x.n,0))} 个产品`;
    document.getElementById("crumb").innerHTML = `<button onclick="cat1='';render()">← 全部一级分类</button> / <b>${esc(cat1)}</b>`;
  } else {
    rows = d.cat2.slice(0,20);
    title = "二级分类分布（全部大类 Top 20）";
    sub = `${d.label} · 点左边任一大类，这里只看那个大类下的二级分布`;
    document.getElementById("crumb").innerHTML = "";
  }
  document.getElementById("c2ttl").textContent = title;
  document.getElementById("c2sub").textContent = sub;
  bars(document.getElementById("c2"), rows, {empty:"这个大类下还没有产品。"});
  document.getElementById("c2t").innerHTML = table(["二级分类","产品数"], rows.map(x=>[x.name,fmt(x.n)]), "二级分类");

  // 公司排行：选中某家时高亮它的位置
  const top = D.companies.map(c=>({name:c,n:D.by_company[c].total}));
  bars(document.getElementById("cc"), top.slice(0,20), {clickable:true, onPick:n=>{
    comp = (comp===n?"":n); cat1=""; document.getElementById("sel").value=comp; render();}});
  document.getElementById("cct").innerHTML = table(["公司","产品数","主力品类"],
    top.map(c=>[c.name, fmt(c.n), D.by_company[c.name].cat1.slice(0,3).map(x=>`${x.name}(${x.n})`).join("、")]), "公司明细");
}

document.getElementById("sel").onchange = e => { comp = e.target.value; cat1=""; render(); };
render();
"""


def _payload(dist, companies):
    """只带报告要用的字段，别把整个 dist 塞进去（文件会大一倍）。"""
    sel = [c for c in dist["companies"] if not companies or c in companies]
    by = {c: dist["by_company"][c] for c in sel}
    from collections import Counter
    c1, c2 = Counter(), Counter()
    for c in sel:
        for x in by[c]["cat1"]:
            c1[x["name"]] += x["n"]
        for x in by[c]["cat2"]:
            c2[x["name"]] += x["n"]
    by1 = {}
    for key, v in c2.items():
        a, _, b = key.partition("/")
        by1.setdefault(a, []).append({"name": b, "n": v})
    for k in by1:
        by1[k].sort(key=lambda x: -x["n"])
    return {
        "total": sum(by[c]["total"] for c in sel),
        "companies": sorted(sel, key=lambda c: -by[c]["total"]),
        "by_company": by,
        "overall_cat1": [{"name": k, "n": v} for k, v in c1.most_common()],
        "cat2_by_cat1": by1,
        "taxonomy": dist.get("taxonomy") or {},
    }


def _md(text):
    """极简 Markdown → HTML（AI 分析用的就是标题 + 段落 + 列表 + 粗体）。"""
    out, lines = [], str(text or "").split("\n")
    for ln in lines:
        e = html.escape(ln)
        e = e.replace("**", "\x00")
        parts = e.split("\x00")
        e = "".join(p if i % 2 == 0 else f"<b>{p}</b>" for i, p in enumerate(parts))
        if ln.startswith("### "):
            out.append(f"<h3>{e[4:]}</h3>")
        elif ln.startswith("## "):
            out.append(f"<h3>{e[3:]}</h3>")
        elif ln.startswith("# "):
            out.append(f"<h3>{e[2:]}</h3>")
        elif ln.startswith(("- ", "* ")):
            out.append(f"<div>· {e[2:]}</div>")
        elif ln.strip():
            out.append(f"<p>{e}</p>")
    return "\n".join(out)


def build_html(dist, companies, analysis="", note=""):
    data = _payload(dist, companies)
    sel_n = len(data["companies"])
    when = time.strftime("%Y-%m-%d %H:%M")
    body = f"""<div class="wrap">
  <h1>产品数据分布报告</h1>
  <p class="sub">生成于 <span class="mono">{when}</span> · 覆盖 <b>{sel_n}</b> 家公司 /
     <b>{data['total']:,}</b> 个产品{('　·　' + html.escape(note)) if note else ''}</p>
  <p class="sub">这份报告是自包含的：断网、换电脑、发给别人都能直接打开，筛选和下钻都还在。</p>

  <div class="card" style="margin-top:18px">
    <div class="bar">
      <select id="sel">
        <option value="">全部公司（合计）</option>
        {''.join(f'<option value="{html.escape(c)}">{html.escape(c)}（{data["by_company"][c]["total"]}）</option>' for c in data["companies"])}
      </select>
      <span class="cs">选一家公司，下面所有图表和数字都跟着变</span>
    </div>
    <div class="stats" id="kpi"></div>
  </div>

  <div class="grid">
    <div class="card">
      <div class="ct">一级分类分布</div>
      <div class="cs">点任一条可以下钻到它的二级分类</div>
      <div id="c1"></div><div id="c1t"></div>
    </div>
    <div class="card">
      <div class="ct" id="c2ttl">二级分类分布</div>
      <div class="cs" id="c2sub"></div>
      <div class="crumb" id="crumb"></div>
      <div id="c2"></div><div id="c2t"></div>
    </div>
  </div>

  <div class="card">
    <div class="ct">各公司产品量（Top 20）</div>
    <div class="cs">点任一条＝只看那家公司；再点一次取消</div>
    <div id="cc"></div><div id="cct"></div>
  </div>

  {f'<div class="card"><h2>AI 分析</h2><p class="desc">由任务模型基于上面这份统计数据生成，分母（标准分类体系规模）由代码算好喂给模型，不让它自己编。</p><div class="ai">{_md(analysis)}</div></div>' if analysis else ''}

  <p class="foot">自动化数据处理平台 · 自包含报告，无外部依赖</p>
</div>"""
    js = JS.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>产品数据分布报告 {when}</title>
<style>{CSS}</style></head>
<body>{body}
<script>{js}</script>
</body></html>"""


def run_export_html(job, dist, companies, model_cfg=None, note=""):
    """后台任务：可选调 LLM 生成分析 → 生成自包含 HTML → 返回文件路径。"""
    from . import llm as llmmod
    from .report import ANALYZE_PROMPT, _payload_for_ai
    os.makedirs(EXPORT_DIR, exist_ok=True)
    sel_n = len([c for c in dist["companies"] if not companies or c in companies])
    job.step(0, 3)
    job.log(f"统计范围：{'全部' if not companies else f'所选 {sel_n} 家'}公司")

    analysis = ""
    if model_cfg:
        job.set_current("正在调用任务模型生成分析 …")
        job.log("调用 AI 生成分析（可能需要 10-60 秒）…")
        text, err = llmmod.chat(model_cfg, ANALYZE_PROMPT + json.dumps(
            _payload_for_ai(dist, companies), ensure_ascii=False, indent=1))
        if err:
            job.log(f"⚠ AI 分析调用失败，将只导出图表：{err}")
        else:
            analysis = text or ""
            job.log(f"AI 分析完成（{len(analysis)} 字）")
    job.step(1)

    job.set_current("正在生成 HTML 报告 …")
    doc = build_html(dist, companies, analysis, note)
    job.step(2)

    name = f"产品数据分布报告_{time.strftime('%Y%m%d_%H%M%S')}.html"
    path = os.path.join(EXPORT_DIR, f"{job.id}.html")
    open(path, "w", encoding="utf-8").write(doc)
    job.step(3)
    size = os.path.getsize(path)
    job.log(f"✓ 导出完成：{name}（{size // 1024} KB，自包含、零外部请求）")
    return {"file": path, "name": name, "companies": sel_n, "format": "html",
            "has_analysis": bool(analysis), "analysis": analysis, "size": size}
