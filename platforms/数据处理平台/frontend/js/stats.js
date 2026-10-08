/* 统计页 v3（预览已确认）。

   v2 → v3 的改动：
   · 状态语义重构 —— R1 概览带 / R2 全景分布固定为全局口径；公司维度集中到 R3「公司透视」。
   · 占比双标注 —— drawBars 新增 pct 选项（common.js，默认 false 全站兼容），
     统计页所有单系列条图行尾显示「数量 · 占比」，满足"数量与占比同时可见"。
   · hero 构成条 —— 全部产品的部分-整体堆叠（Top6 + 「其他」折叠，图例常开），
     是页面上唯一的彩色全景，一眼看懂全局。
   · 公司透视（R3）—— select 切换查看单家完整分布；
     对比抽屉勾选 2-4 家并排比较一级构成（跨行同色可比，数据表兜底）。
   · 其余（锚点导航 / 骨架屏 / 扫描任务 / 导出 / AI 分析）沿用 v2。

   API 契约与 /api/stats/dist、/api/stats 完全兼容，后端零改动。 */

let curDays = parseInt(localStorage.getItem("stats:days") || "7");
let curMode = localStorage.getItem("stats:mode") || "week";
let curStats = null, DIST = null, dCompany = "", dCat1 = "";
// R3 公司透视里选中的一级分类（右栏下钻用）。不持久化：它依附于当前选中的公司，
// 换公司就该清掉，存下来反而会在下次打开时显示一个与当前公司无关的大类。
let cmpCat1 = "";

const SERIES = [
  { k: "mapped",    l: "规则命中", c: "var(--d1)" },
  { k: "ai",        l: "AI 研判",  c: "var(--d3)" },
  { k: "unmatched", l: "未匹配",   c: "var(--d4)" },
];

/* 横向堆叠条：段间留 2px 表面色间隙，零值段不画（画一条 3px 会让人以为「还有一点点」） */
function drawStacked(box, rows, series, labelKey) {
  if (!rows.length) { box.innerHTML = `<p class="hint">这个周期还没有数据。</p>`; return; }
  const max = Math.max(...rows.map(r => series.reduce((s, x) => s + (r[x.k] || 0), 0)), 1);
  box.className = "bars";
  box.innerHTML = rows.map((r, i) => {
    const tot = series.reduce((s, x) => s + (r[x.k] || 0), 0);
    const segs = series.filter(x => (r[x.k] || 0) > 0).map(x =>
      `<div class="bf" style="width:${(r[x.k] * 100 / max).toFixed(2)}%;background:${x.c}"
            data-s="${x.k}" data-row="${i}"></div>`).join("");
    return `<div class="brow" data-i="${i}">
      <div class="bl" title="${esc(r[labelKey])}">${esc(r[labelKey])}</div>
      <div class="bt">${segs}<span class="bv">${fmt(tot)}</span></div></div>`;
  }).join("");
  box.querySelectorAll(".brow").forEach(row => {
    const r = rows[+row.dataset.i];
    row.onmouseenter = () => { box.classList.add("hov"); row.classList.add("on"); };
    row.onmouseleave = () => { box.classList.remove("hov"); row.classList.remove("on"); hideTip(); };
    row.onmousemove = e => {
      const seg = e.target.closest(".bf");
      const tot = series.reduce((s, x) => s + (r[x.k] || 0), 0);
      if (seg) {
        const s = series.find(x => x.k === seg.dataset.s);
        showTip(e, `${r[labelKey]} · ${s.l}`, `${fmt(r[s.k])} 个 · 占该公司 ${(r[s.k] * 100 / tot).toFixed(1)}%`);
      } else {
        showTip(e, r[labelKey], series.map(x => `${x.l} ${fmt(r[x.k] || 0)}`).join("  ·  ") + `  合计 ${fmt(tot)}`);
      }
    };
  });
}

/* ── 骨架屏：数据没到之前先占位，到了才换成真值 ─────────────────────────── */
function skel(n) {
  return Array.from({ length: n }, () =>
    `<div class="kpi skel"><div class="skl s1"></div><div class="skl s2"></div></div>`).join("");
}

/* ── R1 全局概览带（固定全局口径，公司维度在 R3） ───────────────────────── */
const RANGE_LAB = { 7: "近 7 天", 30: "近 30 天", 0: "全部" };

function renderOvKpi() {
  const ov = document.getElementById("ov-kpis");
  const distTime = document.getElementById("ov-dist-time");
  const pend = document.getElementById("ov-dist-pend");
  if (!DIST) {
    distTime.textContent = "—"; pend.textContent = "";
    ov.innerHTML = `<div class="kpi" style="grid-column:1/-1;display:flex;align-items:center;gap:10px">
      <div class="kk" style="margin:0">还没有扫描过类别分布 —— 在下方点「重新扫描」生成快照（首次全量约两分钟）</div></div>`;
    return;
  }
  distTime.textContent = DIST.scanned_at || "—";
  pend.textContent = DPENDING ? `${DPENDING} 家公司在快照后有改动，尚未算入` : "";
  const tx = DIST.taxonomy || {};
  const cov = (n, all) => all ? `${n}<small> / ${all}</small>` : n;
  const c1p = tx.cat1 ? Math.round(DIST.overall_cat1.length * 100 / tx.cat1) : 0;
  const c2n = Object.values(DIST.cat2_by_cat1 || {}).reduce((s, arr) => s + arr.length, 0);
  const c2p = tx.cat2 ? Math.round(c2n * 100 / tx.cat2) : 0;
  // 主指标 = 映射输出的产品数（流水线真正产出、进下游 ③④⑤ 的库），
  // 待分类额外来源降为次要信息 —— 它不进流水线、只参与统计。
  const mainSrc = (DIST.sources || []).find(s => s.label === "映射输出");
  const extra = (DIST.sources || []).filter(s => s.label !== "映射输出")
    .reduce((s, x) => s + (x.products || 0), 0);
  // 公司数同样要把来源拆开。它是三个来源直接相加（已核对零重叠），而且**映射输出是累积的**
  // ——源目录处理完被挪走后，公司仍留在这个数里。只给一个总数，用户对着输入目录一数就对不上，
  // 只能怀疑平台算错了（实测被问过一次：输入目录 131 家、这里显示 150 家）。
  const mainCo = mainSrc ? (mainSrc.companies || 0) : DIST.companies.length;
  const extraCo = (DIST.sources || []).filter(s => s.label !== "映射输出")
    .reduce((s, x) => s + (x.companies || 0), 0);
  ov.innerHTML = `
    <div class="kpi main"><div class="kv">${fmt(mainSrc ? mainSrc.products : DIST.total)}<small>个</small></div>
      <div class="kk">映射输出 · 已分类产品</div>
      <div class="knote">另含待分类 ${fmt(extra)} 个 · 合计 ${fmt(DIST.total)}</div></div>
    <div class="kpi"><div class="kv">${cov(DIST.overall_cat1.length, tx.cat1)}<small>类</small></div><div class="kk">覆盖一级分类${tx.cat1 ? "（分母=标准体系）" : ""}</div>
      <div class="kbar"><i style="width:${c1p}%"></i></div></div>
    <div class="kpi"><div class="kv">${cov(c2n, tx.cat2)}<small>类</small></div><div class="kk">覆盖二级分类${tx.cat2 ? "（分母=标准体系）" : ""}</div>
      <div class="kbar"><i style="width:${c2p}%"></i></div></div>
    <div class="kpi"><div class="kv">${DIST.companies.length}</div><div class="kk">公司数</div>
      <div class="knote">映射输出 ${fmt(mainCo)} 家 · 待分类 ${fmt(extraCo)} 家</div></div>`;
}

function renderSKpi() {
  const s = curStats;
  if (!s) return;
  document.getElementById("ov-range-lab").textContent =
    s.range_label || RANGE_LAB[curDays] || "全部";
  document.getElementById("ov-stats-time").textContent = s.generated || "—";
  document.getElementById("s-kpis").innerHTML = `
    <div class="kpi"><div class="kv">${fmt(s.total)}<small>个</small></div><div class="kk">周期内产品总数</div></div>
    <div class="kpi ok"><div class="kv">${s.match_rate}%</div><div class="kk">映射成功率（规则 + AI）</div></div>
    <div class="kpi ${s.unmatched ? "warn" : ""}"><div class="kv">${fmt(s.unmatched)}</div><div class="kk">未匹配</div></div>
    <div class="kpi ai"><div class="kv">${fmt(s.ai)}</div><div class="kk">AI 研判归类</div></div>
    <div class="kpi"><div class="kv">${fmt(s.companies)}</div><div class="kk">映射公司数</div></div>
    <div class="kpi"><div class="kv">${fmt(s.manual_total)}</div><div class="kk">人工重匹配次数</div></div>`;
}

/* ── R2 hero：全部产品的构成（部分-整体堆叠，Top6 + 其他折叠） ──────────── */
const HERO_COLORS = ["var(--d1)", "var(--d2)", "var(--d3)", "var(--d4)", "var(--d5)", "var(--d6)"];
/* Top6 + 「其他」折叠。分类色位上限 7（dataviz 规矩），超出的一律并进「其他」，
   绝不循环取色 —— 循环会让两个不相干的分类拿到同一个颜色。 */
function segsOf(cats) {
  const top = (cats || []).slice(0, 6).map(c => ({ name: c.name, n: c.n }));
  const rest = (cats || []).slice(6).reduce((s, c) => s + c.n, 0);
  if (rest > 0) top.push({ name: "其他", n: rest, other: true });
  return top;
}
function heroSegs() { return segsOf(DIST.overall_cat1); }

/* R2「全部产品的构成」与 R4「本周期的构成」共用这一个渲染器 —— 同一种问题就该是
   同一种图形。但**两者口径完全不同**（实时扫描 vs 映射记忆快照），所以 100% 的基数
   和那句说明必须由调用方给，这里绝不自己猜。
   root = .hero 容器；条、基数行、图例按类名从它下面找，不依赖具体 id。 */
function paintHero(root, cats, tot, baseText) {
  const segs = segsOf(cats);
  const bar = root.querySelector(".hero-bar");
  const lg = root.querySelector(".lg");
  const base = root.querySelector(".hero-total");
  base.textContent = baseText || "";
  if (!segs.length || !tot) {
    bar.innerHTML = "";
    lg.innerHTML = `<span class="dash">这个周期没有已分类的产品。</span>`;
    return;
  }
  const pct = n => (n * 100 / tot).toFixed(1);
  bar.innerHTML = segs.map((s, i) => {
    const w = s.n * 100 / tot;
    const label = w >= 7.5 ? `${esc(s.name)} ${pct(s.n)}%` : "";
    return `<div class="hseg" data-i="${i}" style="flex-grow:${(w * 100).toFixed(2)};background:${s.other ? "#9aa5b1" : HERO_COLORS[i]}"
      title="${esc(s.name)}：${fmt(s.n)} 个（${pct(s.n)}%）">${label}</div>`;
  }).join("");
  bar.querySelectorAll(".hseg").forEach(seg => {
    const s = segs[+seg.dataset.i];
    // ⚠ hov 要加在 .hero 容器上，不是加在条上：CSS 写的是 `.hero.hov .hseg`。
    // 以前加在 #hero-bar（class 是 hero-bar）上，`.hero.hov` 永远选不中它 ——
    // 于是"悬停时其余段变暗"这个效果**从来没生效过**，只剩 tooltip 在动。
    seg.onmouseenter = () => { root.classList.add("hov"); seg.classList.add("on"); };
    seg.onmouseleave = () => { root.classList.remove("hov"); seg.classList.remove("on"); hideTip(); };
    seg.onmousemove = e => showTip(e, s.name, `${fmt(s.n)} 个 · 占比 ${pct(s.n)}%`);
  });
  lg.innerHTML = segs.map((s, i) =>
    `<span><i style="background:${s.other ? "#9aa5b1" : HERO_COLORS[i]}"></i>${esc(s.name)}
     <b>${fmt(s.n)}</b> · ${pct(s.n)}%</span>`).join("");
}

function renderHero() {
  const extra = (DIST.sources || []).filter(s => s.label !== "映射输出")
    .reduce((s, x) => s + (x.products || 0), 0);
  document.getElementById("hero-total").textContent = fmt(DIST.total);
  paintHero(document.getElementById("hero"), DIST.overall_cat1, DIST.total,
    `100% = ${fmt(DIST.total)} 个产品（含待分类额外来源 ${fmt(extra)} 个）`);
}

/* ── R2 全景分布：一级明细（数量+占比）/ 二级下钻（全局口径 + dCat1） ───── */
function renderDist() {
  const d = {
    total: DIST.total,
    cat1: DIST.overall_cat1 || [],
    cat2: Object.entries(DIST.cat2_by_cat1 || {}).flatMap(([c1, arr]) =>
      arr.map(x => ({ name: `${c1}/${x.name}`, n: x.n }))).sort((a, b) => b.n - a.n),
  };
  drawBars(document.getElementById("d-cat1"), d.cat1, {
    pct: true, clickable: true, empty: "这个范围下还没有已分类的产品。",
    onPick: name => { dCat1 = name; renderDist(); },
  });
  document.getElementById("d-cat1-t").innerHTML =
    dataTable(["一级分类", "产品数", "占比"], d.cat1.map(x => [x.name, fmt(x.n), (x.n * 100 / d.total).toFixed(1) + "%"]));

  let rows, title, sub;
  if (dCat1) {
    rows = d.cat2.filter(x => x.name.startsWith(dCat1 + "/"))
                 .map(x => ({ name: x.name.slice(dCat1.length + 1), n: x.n }));
    title = `${dCat1} 下的二级分类`;
    sub = `${rows.length} 个二级分类，合计 ${fmt(rows.reduce((s, x) => s + x.n, 0))} 个产品`;
    document.getElementById("d-crumb").innerHTML =
      `<button id="d-back">全部一级分类</button>${ic("chev", 13)}<b style="color:var(--ink)">${esc(dCat1)}</b>`;
    document.getElementById("d-back").onclick = () => { dCat1 = ""; renderDist(); };
  } else {
    rows = d.cat2.slice(0, 20);
    title = "二级分类分布（全部大类 Top 20）";
    sub = "点左边任一大类，这里只看那个大类下的二级分布";
    document.getElementById("d-crumb").innerHTML = "";
  }
  document.getElementById("d-cat2-title").textContent = title;
  document.getElementById("d-cat2-sub").textContent = sub;
  // 单系列比大小一律用同一个蓝、不放图例（标题已说明它是什么）。
  drawBars(document.getElementById("d-cat2"), rows, { pct: true, empty: "这个大类下还没有产品。" });
  document.getElementById("d-cat2-t").innerHTML =
    dataTable(["二级分类", "产品数", "占比"], rows.map(x => [x.name, fmt(x.n), (x.n * 100 / d.total).toFixed(1) + "%"]));
}

/* ── R3 公司透视：切换查看单家公司完整分布 ──────────────────────────────── */
function renderCompany() {
  const sel = document.getElementById("cmp-sel");
  /* ⚠ 每次都按当前 DIST 重建选项。以前这里是 `if (sel.options.length <= 1)` ——
     只在第一次填充，**之后永不重建**：移除一家公司之后它照样留在下拉里，
     选中还会报错找不到。110 家公司重建一次是毫秒级，没有省这一下的必要。 */
  if (DIST) {
    sel.innerHTML = `<option value="">选择一家公司…</option>` +
      DIST.companies.map(c => `<option value="${esc(c)}">${esc(c)}（${DIST.by_company[c].total}）</option>`).join("");
    if (dCompany && DIST.companies.includes(dCompany)) sel.value = dCompany;
    else { dCompany = ""; sel.value = ""; }
    // 对比抽屉的勾选集合同理：已经不存在的公司要摘掉，否则对比图会画出空行
    let dropped = false;
    [...cmpSel].forEach(c => { if (!DIST.companies.includes(c)) { cmpSel.delete(c); dropped = true; } });
    if (dropped && document.getElementById("cmp-drawer").classList.contains("open")) {
      renderCmpList(document.getElementById("cmp-q").value || "");
      renderCmpBody();
    }
  }
  const b = dCompany ? DIST.by_company[dCompany] : null;
  document.getElementById("cmp-desc").textContent = b
    ? `正在查看：${dCompany} —— 以下所有图表的范围都是这一家公司`
    : "从下拉选择一家公司，查看它的完整产品分布；或点「对比公司」并排比较 2-4 家。";
  if (!b) {
    document.getElementById("cmp-kpis").innerHTML = "";
    ["cmp-cat1", "cmp-cat2"].forEach(id =>
      document.getElementById(id).innerHTML = `<p class="hint">还没有选择公司。</p>`);
    ["cmp-cat1-t", "cmp-cat2-t"].forEach(id => document.getElementById(id).innerHTML = "");
    return;
  }
  const top3 = b.cat1.slice(0, 3).map(x => `${x.name} ${fmt(x.n)}`).join("、");
  document.getElementById("cmp-kpis").innerHTML = `
    <div class="cmp-kpi"><div class="v">${fmt(b.total)}<small> 个</small></div><div class="k">产品总数</div></div>
    <div class="cmp-kpi"><div class="v">${b.cat1.length}<small> / ${DIST.taxonomy.cat1}</small></div><div class="k">覆盖一级分类</div></div>
    <div class="cmp-kpi"><div class="v">${b.cat2.length}<small> / ${DIST.taxonomy.cat2}</small></div><div class="k">覆盖二级分类</div></div>
    <div class="cmp-kpi"><div class="v top3" title="${esc(top3)}">${esc(top3)}</div><div class="k">主力品类（前三）</div></div>`;
  // 选中的一级分类如果不在这家公司里（换了公司、或该公司没有这个大类），立即作废 ——
  // 否则右侧会显示上一家公司留下的那个大类，标题却写着当前公司，看着像数据错乱。
  if (cmpCat1 && !b.cat1.some(x => x.name === cmpCat1)) cmpCat1 = "";

  drawBars(document.getElementById("cmp-cat1"), b.cat1, {
    pct: true, clickable: true, empty: "这家公司还没有已分类产品。",
    onPick: name => { cmpCat1 = (cmpCat1 === name) ? "" : name; renderCompany(); },
  });
  document.getElementById("cmp-cat1-t").innerHTML =
    dataTable(["一级分类", "产品数", "占该公司"], b.cat1.map(x => [x.name, fmt(x.n), (x.n * 100 / b.total).toFixed(1) + "%"]));

  /* 右侧：选了一级就只看它的二级构成（与 R2 的下钻同一套交互与措辞）。
     没选时给**全部**二级、不再截 Top 10 —— 截断在这里没有意义：这一栏的分母
     本来就只有一家公司，条目数天然有限，而截断会让"合计"对不上公司总数。 */
  let rows, title, sub, base;
  if (cmpCat1) {
    rows = b.cat2.filter(x => x.name.startsWith(cmpCat1 + "/"))
                 .map(x => ({ name: x.name.slice(cmpCat1.length + 1), n: x.n }))
                 .sort((x, y) => y.n - x.n);
    base = rows.reduce((s, x) => s + x.n, 0);
    title = `${cmpCat1} 下的二级分类`;
    sub = `${rows.length} 个二级分类，合计 ${fmt(base)} 个产品 · 占该公司 ${(base * 100 / b.total).toFixed(1)}%`;
    document.getElementById("cmp-crumb").innerHTML =
      `<button id="cmp-back">该公司全部二级分类</button>${ic("chev", 13)}<b style="color:var(--ink)">${esc(cmpCat1)}</b>`;
    document.getElementById("cmp-back").onclick = () => { cmpCat1 = ""; renderCompany(); };
  } else {
    rows = [...b.cat2].sort((x, y) => y.n - x.n)
                      .map(x => ({ name: x.name.replace("/", " / "), n: x.n }));
    base = b.total;
    title = "二级分类分布（全部大类）";
    sub = `该公司 ${b.cat2.length} 个二级分类 · 点左边任一大类，这里只看那个大类`;
    document.getElementById("cmp-crumb").innerHTML = "";
  }
  document.getElementById("cmp-cat2-title").textContent = title;
  document.getElementById("cmp-cat2-sub").textContent = sub;
  drawBars(document.getElementById("cmp-cat2"), rows, { pct: true, empty: "这家公司还没有二级分类数据。" });
  document.getElementById("cmp-cat2-t").innerHTML =
    dataTable(["二级分类", "产品数", cmpCat1 ? "占该大类" : "占该公司"],
      rows.map(x => [x.name, fmt(x.n), (x.n * 100 / (base || 1)).toFixed(1) + "%"]));
}

/* ── 对比抽屉：勾选 2-4 家并排比较一级构成 ─────────────────────────────── */
const CMP_MAX = 4;
let cmpSel = new Set();
function openCompare() {
  renderCmpList("");
  document.getElementById("cmp-drawer").classList.add("open");
  document.getElementById("cmp-mask").classList.add("open");
  renderCmpBody();
  document.getElementById("cmp-q").focus();
}
function closeCompare() {
  document.getElementById("cmp-drawer").classList.remove("open");
  document.getElementById("cmp-mask").classList.remove("open");
  document.getElementById("cmp-open").focus();
}
function renderCmpList(q) {
  const list = DIST.companies.filter(c => !q || c.toLowerCase().includes(q.toLowerCase()));
  document.getElementById("cmp-list").innerHTML = list.map(c =>
    `<label><input type="checkbox" value="${esc(c)}"${cmpSel.has(c) ? " checked" : ""}>
      <span>${esc(c)}</span><small>${fmt(DIST.by_company[c].total)}</small></label>`).join("");
}
function renderCmpBody() {
  const body = document.getElementById("cmp-body");
  const n = cmpSel.size;
  document.getElementById("cmp-sel-n").textContent = `已选 ${n} / ${CMP_MAX} 家`;
  if (n < 2) {
    body.innerHTML = `<p class="cmp-hint" style="margin-top:4px">再勾选 ${2 - n} 家即可开始对比（最多 ${CMP_MAX} 家）。</p>`;
    return;
  }
  const segs = heroSegs();          // 全局 Top6 + 其他 —— 跨公司同色可比
  const rows = [...cmpSel].map(c => {
    const b = DIST.by_company[c];
    const m = {};
    b.cat1.forEach(x => m[x.name] = x.n);
    return { c, total: b.total, m };
  });
  const max = Math.max(...rows.map(r => r.total), 1);
  body.innerHTML = `<div class="cmp">
    <div class="cmp-chart">
      ${rows.map(r => {
        const parts = segs.map((s, i) => {
          const n = r.m[s.name] || 0;
          if (!n) return "";
          return `<div class="cs" data-r="${esc(r.c)}" data-s="${i}"
            style="width:${(n * 100 / max).toFixed(2)}%;background:${s.other ? "#9aa5b1" : HERO_COLORS[i]}"
            title="${esc(s.name)}：${fmt(n)}"></div>`;
        }).join("");
        return `<div class="cmp-row" data-r="${esc(r.c)}">
          <div class="cl">${esc(r.c)}</div><div class="cb">${parts}</div><div class="cv">${fmt(r.total)}</div></div>`;
      }).join("")}
    </div>
    <div class="lg">${segs.map((s, i) =>
      `<span><i style="background:${s.other ? "#9aa5b1" : HERO_COLORS[i]}"></i>${esc(s.name)} · ${fmt(s.n)} · ${(s.n * 100 / DIST.total).toFixed(1)}%</span>`).join("")}</div>
    ${dataTable(["公司", ...segs.map(s => s.name), "合计"],
      rows.map(r => [r.c, ...segs.map(s => fmt(r.m[s.name] || 0)), fmt(r.total)]), "对比明细")}
  </div>`;
  body.querySelectorAll(".cmp-row").forEach(row => {
    const b = DIST.by_company[row.dataset.r];
    row.onmouseenter = () => { body.querySelector(".cmp").classList.add("hov"); row.classList.add("on"); };
    row.onmouseleave = () => { body.querySelector(".cmp").classList.remove("hov"); row.classList.remove("on"); hideTip(); };
    row.onmousemove = e => {
      const s = e.target.closest(".cs");
      if (s) {
        const seg = segs[+s.dataset.s];
        const n = b.cat1.find(x => x.name === seg.name)?.n || 0;
        showTip(e, `${row.dataset.r} · ${seg.name}`, `${fmt(n)} 个 · 占该公司 ${(n * 100 / b.total).toFixed(1)}%`);
      } else {
        showTip(e, row.dataset.r, `共 ${fmt(b.total)} 个产品`);
      }
    };
  });
}
document.getElementById("cmp-open").onclick = openCompare;
document.getElementById("cmp-close").onclick = closeCompare;
document.getElementById("cmp-mask").onclick = closeCompare;
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && document.getElementById("cmp-drawer").classList.contains("open")) closeCompare();
});
document.getElementById("cmp-list").addEventListener("change", e => {
  const c = e.target.value;
  if (e.target.checked) {
    if (cmpSel.size >= CMP_MAX) { e.target.checked = false; return; }
    cmpSel.add(c);
  } else cmpSel.delete(c);
  renderCmpBody();
});
let cmpDeb = null;
document.getElementById("cmp-q").addEventListener("input", e => {
  clearTimeout(cmpDeb);
  cmpDeb = setTimeout(() => renderCmpList(e.target.value), 200);
});
document.getElementById("cmp-sel").onchange = e => {
  dCompany = e.target.value;
  cmpCat1 = "";
  localStorage.setItem("stats:company", dCompany);
  renderCompany();
};

/* ── 类别分布：服务端缓存 + 后台扫描任务 ──────────────────────────────────
   这一步要全树 walk 一万多个产品（实测 94 秒）。以前是同步 fetch 等着，
   **一切换页面浏览器就把请求 abort 掉，结果连服务端都没地方存**，回来从零再扫 ——
   这是"刷新统计一切页面就中断"的全部原因。现在与总览同一套：
   GET 只读缓存（秒开），扫描走 POST 起后台任务，resumeJob 负责切页面回来续上。 */
function distEmpty(msg, sub = "") {
  document.getElementById("d-gen").textContent = msg;
  ["d-cat1", "d-cat2", "hero-bar"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = `<div class="empty">${esc(sub || msg)}</div>`;
  });
}

let DPENDING = 0;      // 快照之后有多少家公司变过（后端按分片记的脏标）

/* 读缓存。ready:false 就引导去扫，不自己偷偷开扫（那又会变成一次没人知道的两分钟）。 */
async function loadDist() {
  try {
    const d = await api("/api/stats/dist");
    DPENDING = d.pending || 0;
    if (!d.ready) {
      DIST = null;
      distEmpty("还没有扫描过类别分布", (d.reason || "") + " 扫描在后台跑，可以切页面去干别的。");
      drawScanBtns();
      renderOvKpi();
      return null;
    }
    DIST = d;
    paintDist();
    drawScanBtns();
    renderOvKpi();
    return d;
  } catch (e) {
    distEmpty("", "读不到类别分布：" + e.message);
    return null;
  }
}

/* 两个按钮的文案要说清楚代价差别：增量约 15 秒、全量约两分钟。
   有公司变过时把家数写在按钮上 —— 否则没人知道该不该点。 */
function drawScanBtns() {
  const inc = document.getElementById("d-reload");
  const full = document.getElementById("d-full");
  inc.textContent = DPENDING ? `重新扫描（${DPENDING} 家有改动）` : "重新扫描";
  inc.title = "只重扫上次快照之后变过的公司，约十几秒";
  full.title = "整棵重走，约两分钟。只有在资源管理器里手工挪过目录时才需要"
             + "——那种改动平台无从知道";
}

/* 起一次后台扫描。box 里显示进度与日志，跑完自动渲染。 */
async function scanDist(full = false) {
  if (full) {
    const ok = await confirmBox("全量重扫", `
      <p>整棵重走映射输出与额外来源，约 <b>两分钟</b>。</p>
      <div class="note" style="margin-top:10px">
        平常不需要——平台自己知道哪家公司变过，「重新扫描」只走那几家，十几秒就好。<br>
        <b>只有你在资源管理器里手工挪动/删除过目录时才用这个</b>，那种改动平台无从知道。
      </div>`, { okText: "全量重扫" });
    if (!ok) return;
  }
  const btns = ["d-reload", "d-full"].map(id => document.getElementById(id));
  btns.forEach(b => b.disabled = true);
  const box = document.getElementById("d-jobbox");
  try {
    const r = await api(`/api/stats/dist/scan?full=${full ? "true" : "false"}`, { method: "POST" });
    const ui = liveUI(box, { cancel: () => api(`/api/jobs/${r.job_id}/cancel`, { method: "POST" }) });
    ui.open();
    document.getElementById("d-gen").textContent = "正在后台扫描…（可以切到别的页面，回来会自动接上）";
    pollJob(r.job_id, ui, async j => {
      btns.forEach(b => b.disabled = false);
      if (j.status === "done") {
        await loadDist();
        box.style.display = "none";
        const R = j.result || {};
        if (R.rescanned !== undefined)
          toast(R.full ? `全量重扫完成：${fmt(R.total)} 个产品`
                       : `增量完成：重扫 ${R.rescanned} 家、沿用 ${R.reused} 家`, "ok");
      }
    });
  } catch (e) { btns.forEach(b => b.disabled = false); toast(e.message, "err"); }
}

function paintDist() {
  /* 数据一律来自服务端快照，所以芯片说的不是"实时还是缓存"，而是**这份快照有多旧**。
     颜色必须跟着含义走（别把陈旧快照配成绿底）。 */
  const live = document.getElementById("d-live");
  const age = (Date.now() - new Date((DIST.scanned_at || "").replace(/-/g, "/"))) / 3600000;
  const old = !(age >= 0) ? false : age > 24;
  live.className = "chip " + (old ? "warn" : "ok");
  live.innerHTML = old
    ? MK.stale + `${Math.round(age / 24)} 天前的快照`
    : MK.done + (age >= 1 ? `${Math.round(age)} 小时前扫描` : "刚扫描过");
  const mainS = (DIST.sources || []).find(s => s.label === "映射输出");
  const otherS = (DIST.sources || []).filter(s => s.label !== "映射输出");
  document.getElementById("d-gen").innerHTML =
    `扫描于 <span class="mono">${esc(DIST.scanned_at)}</span> · 映射输出 <b>${fmt(mainS ? mainS.products : 0)}</b> 个产品`
    + otherS.map(s => ` · ${esc(s.label)} ${fmt(s.products)}`).join("")
    + ` · 合计 <b>${fmt(DIST.total)}</b> / <b>${DIST.companies.length}</b> 家公司`
    + (DPENDING ? ` <span style="color:var(--warn)">· 之后有 <b>${DPENDING}</b> 家公司变过，
         这份快照还没算上它们</span>` : "");
  renderDist();
  renderHero();
  renderCompany();
  exPeriodDraw(true);
}

document.getElementById("d-reload").onclick = () => scanDist(false);
document.getElementById("d-full").onclick = () => scanDist(true);

/* ── 快照口径：映射构成与趋势 ─────────────────────────────────────────── */
async function load() {
  let s;
  try { s = curStats = await api(`/api/stats?mode=${curMode}&days=${curDays}`); }
  catch (e) {
    document.getElementById("s-gen").innerHTML =
      `${chip("err", "warn", "读不到映射记忆")} ${esc(e.message)}`;
    return;
  }
  document.getElementById("s-gen").innerHTML =
    `生成于 <span class="mono">${esc(s.generated)}</span>，`
    + `<b>${esc(s.range_label || "")}</b> 内完成映射的 ${s.companies} 家公司`
    + (s.range_start ? ` <span class="mono" style="color:var(--faint)">（${esc(s.range_start)}`
        + `${s.range_end ? " ~ " + esc(s.range_end) : " 起"}）</span>` : "");
  renderSKpi();
  exPeriodDraw(true);          // 导出面板跟着周期重新标记/勾选

  // 本周期构成条（与 R2 同形态、不同口径）。
  // ⚠ 分母用**已分类数**而不是 s.total：s.total 含未匹配，拿它做分母各段之和只有 94%，
  // 部分-整体的条会填不满一格 —— 而"填满"正是这种图唯一的语义。
  const pTot = (s.cat1_dist || []).reduce((a, x) => a + x.n, 0);
  document.getElementById("p-hero-total").textContent = fmt(pTot);
  paintHero(document.getElementById("p-hero"), s.cat1_dist, pTot,
    `100% = ${fmt(pTot)} 个已分类产品`
    + (s.unmatched ? `（另有 ${fmt(s.unmatched)} 个未匹配未计入）` : "")
    + ` · 周期内 ${s.companies} 家公司`);

  drawBars(document.getElementById("c-cat1"), s.cat1_dist, { pct: true, empty: "这个周期没有映射记录。" });
  document.getElementById("c-cat1-t").innerHTML =
    // 两个折叠都叫「查看数据表」的话，用户点开之前不知道是哪个，所以各自带名。
    // 占比一律以**已分类数**为分母，与上面的条形图、构成条同基数 —— 以前这里用的是
    // s.total（含未匹配），而 drawBars 内部用的是 sum(items)，同一个分类在图上和表里
    // 显示的占比对不上。
    dataTable(["一级分类", "产品数", "占已分类"], s.cat1_dist.map(x => [x.name, fmt(x.n), (x.n * 100 / (pTot || 1)).toFixed(1) + "%"]), "一级分类") +
    dataTable(["二级分类 Top15", "产品数", "占已分类"], s.cat2_top.map(x => [x.name, fmt(x.n), (x.n * 100 / (pTot || 1)).toFixed(1) + "%"]), "二级分类 Top15");

  document.getElementById("c-comp-lg").innerHTML = SERIES.map(x =>
    `<span><i style="background:${x.c}"></i>${x.l}</span>`).join("");
  drawStacked(document.getElementById("c-comp"), s.per_company, SERIES, "company");
  document.getElementById("c-comp-t").innerHTML =
    dataTable(["公司", "产品", "规则命中", "AI研判", "未匹配", "映射时间"],
      s.per_company.map(c => [c.company, fmt(c.total), fmt(c.mapped), fmt(c.ai), fmt(c.unmatched), c.time]));

  // 未匹配率是 rate，求和没有业务意义 —— 不给它占比列
  drawBars(document.getElementById("c-unrank"),
    s.unmatched_rank.map(x => ({ name: x.company, n: x.rate })),
    { color: "var(--d4)", unit: "%", max: 100, empty: "这个周期没有未匹配的产品。" });
  document.getElementById("c-unrank-t").innerHTML =
    dataTable(["公司", "产品数", "未匹配数", "未匹配率 %"],
      s.unmatched_rank.map(x => [x.company, fmt(x.total), fmt(x.unmatched), x.rate]));

  drawBars(document.getElementById("c-manual"), s.manual_cat,
    { pct: true, unit: "次", empty: "这个周期没有人工重匹配 —— 规则和 AI 把活都干了。" });
  document.getElementById("c-manual-t").innerHTML =
    dataTable(["分类（一级 / 二级）", "人工重匹配次数"], s.manual_cat.map(x => [x.name, fmt(x.n)]));

  document.getElementById("c-custom").innerHTML = s.custom_categories.length
    ? `<div class="tablebox"><table>
       <thead><tr><th>一级</th><th>二级</th><th>类型</th><th>定义</th><th class="n">周期内命中</th></tr></thead>
       <tbody>${s.custom_categories.map(c => `<tr>
         <td>${esc(c.category1)}${c.new_cat1 ? " " + chip("warn", "stale", "新一级") : ""}</td>
         <td>${esc(c.category2)} ${chip("ai", "done", "新增")}</td>
         <td>${c.new_cat1 ? "全新一级 + 二级" : "已有一级下新增二级"}</td>
         <td style="max-width:420px">${esc(c.definition)}</td><td class="n">${fmt(c.count)}</td></tr>`).join("")}</tbody>
       </table></div>`
    : `<p class="hint">还没有人工新增过分类——说明标准分类体系目前够用。</p>`;
}

/* 周期选择：本周=固定周界（周六 00:00 → 周五 23:59，不滑动）；自定义=近 N 天滑动窗口。
   两者口径不同，标签一律用后端回的 range_label，避免前后端各写一份说法。 */
(function rangeSeg() {
  const seg = document.getElementById("s-range");
  const box = document.getElementById("s-custom");
  const inp = document.getElementById("s-days");
  const btns = [...seg.querySelectorAll("button")];

  function paint() {
    const key = curMode === "days" && curDays !== 30 ? "custom" : curMode;
    btns.forEach(b => b.classList.toggle("on",
      b.dataset.m === key && (b.dataset.m !== "days" || parseInt(b.dataset.d) === curDays)));
    box.style.display = key === "custom" ? "" : "none";
    if (key === "custom") inp.value = curDays;
  }

  function apply(mode, days) {
    curMode = mode; curDays = days;
    localStorage.setItem("stats:mode", mode);
    localStorage.setItem("stats:days", days);
    paint();
    load();
  }

  btns.forEach(b => {
    b.onclick = () => {
      const m = b.dataset.m;
      if (m === "custom") apply("days", Math.max(1, parseInt(inp.value) || 7));
      else if (m === "days") apply("days", parseInt(b.dataset.d));
      else apply(m, curDays);
    };
  });
  inp.onchange = () => apply("days", Math.max(1, Math.min(3650, parseInt(inp.value) || 7)));
  paint();
})();

/* ── 分区锚点导航：scrollspy ──────────────────────────────────────────── */
(function secnav() {
  const SECS = ["sec-kpi", "sec-dist", "sec-company", "sec-trend", "sec-out", "sec-purge"];
  const links = [...document.querySelectorAll("#secnav a")];
  let timer = null;
  function spy() {
    const y = window.scrollY + 96;
    let cur = SECS[0];
    for (const id of SECS) {
      const el = document.getElementById(id);
      if (el && el.offsetTop <= y) cur = id;
    }
    links.forEach(a => a.classList.toggle("on", a.getAttribute("href") === "#" + cur));
  }
  window.addEventListener("scroll", () => {
    if (timer) return;
    timer = setTimeout(() => { timer = null; spy(); }, 60);
  }, { passive: true });
  spy();
})();

/* ── AI 分析 ──────────────────────────────────────────────────────────── */
(() => {
  const last = localStorage.getItem("stats:analysis");
  if (last) {
    const out = document.getElementById("a-out");
    out.style.display = "";
    out.innerHTML = `<p class="hint" style="margin:0 0 8px">上次的分析结果</p>` + mdRender(last);
  }
})();

async function fillModels(id, blank) {
  const d = await api("/api/models");
  document.getElementById(id).innerHTML = (blank ? `<option value="">${blank}</option>` : "") +
    (d.models.length ? d.models.map(m => `<option value="${m.id}">${esc(m.name)}（${esc(m.model)}）</option>`).join("")
                     : (blank ? "" : `<option value="">还没有任务模型 — 先到设置页建一个</option>`));
}

document.getElementById("a-run").onclick = async () => {
  const mid = document.getElementById("a-model").value;
  if (!mid) return toast("先到设置页创建一个任务模型", "err");
  const btn = document.getElementById("a-run"), out = document.getElementById("a-out");
  btn.disabled = true; btn.textContent = "分析中…";
  out.style.display = ""; out.innerHTML = `<p class="hint">正在让模型读统计结果，稍等…</p>`;
  try {
    const r = await api("/api/stats/analyze", { method: "POST", body: { days: curDays, mode: curMode, model_id: mid } });
    out.innerHTML = mdRender(r.analysis || "模型没有返回内容。");
    if (r.analysis) localStorage.setItem("stats:analysis", r.analysis);
  } catch (e) { out.innerHTML = `<p>${chip("err", "warn", "分析失败")} ${esc(e.message)}</p>`; }
  btn.disabled = false; btn.textContent = "分析当前周期";
};

/* ── 导出 Excel ───────────────────────────────────────────────────────── */
/* 周期内的公司集合；null = 不按周期筛（勾了"不限周期"或当前口径就是"全部"）。
   名单来自 /api/stats 的 companies_in_range，跟顶部周期选择器同源。 */
function exPeriodSet() {
  if (document.getElementById("ex-nolimit").checked) return null;
  if (!curStats || curStats.range_mode === "all") return null;
  return new Set(curStats.companies_in_range || []);
}

function exPeriodDraw(reselect) {
  const inr = exPeriodSet();
  document.getElementById("ex-range").textContent =
    inr ? ((curStats && curStats.range_label) || "—") : "全部（不限周期）";
  const cnt = document.getElementById("ex-rangecnt");
  cnt.textContent = (inr && DIST)
    ? ` · 周期内 ${inr.size} 家，其中 ${DIST.companies.filter(c => inr.has(c)).length} 家有扫描数据`
    : "";
  if (reselect) exRender();
}

function exRender() {
  if (!DIST) return;
  const inr = exPeriodSet();
  document.querySelector("#ex-tbl tbody").innerHTML = DIST.companies.map(c => {
    const b = DIST.by_company[c];
    const top = b.cat1.slice(0, 3).map(x => `${x.name}（${x.n}）`).join("、");
    const isMain = (b.source || "").indexOf("映射输出") >= 0;
    const hit = !inr || inr.has(c);
    return `<tr${hit ? "" : ' style="opacity:.5"'}>
      <td><input type="checkbox" class="ck-ex" data-c="${esc(c)}" data-main="${isMain}"
                 data-inr="${hit}"${hit ? " checked" : ""}></td>
      <td>${esc(c)}</td><td class="n">${fmt(b.total)}</td>
      <td>${isMain ? chip("ok", "done", b.source || "") : chip("idle", "idle", b.source || "")}</td>
      <td style="max-width:420px">${esc(top)}</td></tr>`;
  }).join("");
  document.querySelectorAll(".ck-ex").forEach(x => x.addEventListener("change", exSum));
  exSum();
}
function exSum() {
  const sel = [...document.querySelectorAll(".ck-ex:checked")];
  const n = sel.reduce((s, x) => s + DIST.by_company[x.dataset.c].total, 0);
  document.getElementById("ex-sum").innerHTML =
    `已选 <b>${sel.length}</b> / ${DIST.companies.length} 家 · 合计 <b>${fmt(n)}</b> 个产品`;
}
document.getElementById("ex-all").onclick = () => {
  document.querySelectorAll(".ck-ex").forEach(x => x.checked = true); exSum(); };
document.getElementById("ex-none").onclick = () => {
  document.querySelectorAll(".ck-ex").forEach(x => x.checked = false); exSum(); };
document.getElementById("ex-main").onclick = () => {
  document.querySelectorAll(".ck-ex").forEach(x => x.checked = x.dataset.main === "true"); exSum(); };
document.getElementById("ex-period").onclick = () => {
  document.querySelectorAll(".ck-ex").forEach(x => x.checked = x.dataset.inr === "true"); exSum(); };
document.getElementById("ex-nolimit").onchange = () => exPeriodDraw(true);

/* 两种格式并存：HTML 给"要能筛、图表要跟着变"的汇报场景，
   Excel 给"领导想自己拉数据"的场景。默认 HTML —— 它才是"和页面一样"的那个。 */
let exFmt = localStorage.getItem("stats:exfmt") === "xlsx" ? "xlsx" : "html";
let exTpl = localStorage.getItem("stats:extpl") || "classic";

/* 三个模板回答的是不同问题，所以提示语按模板给，而不是笼统写"导出报告"。
   period 那份只读映射记忆，不做全树扫描 —— 这是它和另外两份最大的差别，得说清楚。 */
const TPL_HINT = {
  classic: "<b>分布报告</b>：公司筛选、一级分类下钻、图表联动都在，和本页一致。要把数据交给别人自己翻，选这个。",
  company: "<b>公司覆盖</b>：帕累托集中度曲线 + 品类宽度分布 + 前 25 家明细。回答「数据靠谁撑着、复核资源该往哪投」。静态图，无下钻。",
  period: "<b>周期进展</b>：按周(六→五)的产量柱 + 未匹配率折线 + 逐周对照。就是每周五要发的那份。<b>只读映射记忆，跳过全树扫描，几秒就能出</b>。",
};
function exFmtDraw() {
  document.querySelectorAll("#ex-fmt button").forEach(b =>
    b.classList.toggle("on", b.dataset.f === exFmt));
  document.querySelectorAll("#ex-tpl button").forEach(b =>
    b.classList.toggle("on", b.dataset.t === exTpl));
  // Excel 只有一种版式，模板选择器对它没有意义
  document.getElementById("ex-tplbox").style.display = exFmt === "html" ? "" : "none";
  document.getElementById("ex-fmthint").innerHTML = exFmt === "html"
    ? TPL_HINT[exTpl] + " 零外部请求，断网也能看，发给别人也不会失效。"
    : "六个工作表：总览、公司明细、一级分类分布、二级分类分布、公司×一级分类交叉表。图表是静态的，<b>不会跟着筛选变</b>——需要联动请选 HTML。";
}
document.getElementById("ex-fmt").onclick = e => {
  const b = e.target.closest("button"); if (!b) return;
  exFmt = b.dataset.f; localStorage.setItem("stats:exfmt", exFmt); exFmtDraw();
};
document.getElementById("ex-tpl").onclick = e => {
  const b = e.target.closest("button"); if (!b) return;
  exTpl = b.dataset.t; localStorage.setItem("stats:extpl", exTpl); exFmtDraw();
};
exFmtDraw();

document.getElementById("ex-run").onclick = async () => {
  const sel = [...document.querySelectorAll(".ck-ex:checked")].map(x => x.dataset.c);
  // 周期进展报告的范围由周期决定，不看这里的公司勾选，所以不拦
  if (!sel.length && exTpl !== "period") return toast("先勾选至少一家公司", "err");
  const btn = document.getElementById("ex-run");
  if (btn.disabled) return;
  btn.disabled = true;                    // 先置灰再发请求
  document.getElementById("ex-done").style.display = "none";
  try {
    const nolimit = document.getElementById("ex-nolimit").checked;
    const r = await api("/api/stats/export", { method: "POST", body: {
      companies: sel.length === DIST.companies.length ? [] : sel,
      model_id: document.getElementById("ex-model").value,
      format: exFmt, template: exTpl,
      // 周期与页面顶部选择器同源；勾了"不限周期"就退回 all
      mode: nolimit ? "all" : curMode, days: curDays } });
    const ui = liveUI(document.getElementById("ex-box"),
      { cancel: () => api(`/api/jobs/${r.job_id}/cancel`, { method: "POST" }) });
    pollJob(r.job_id, ui, j => {
      btn.disabled = false;
      if (j.status === "done" && j.result) exDone(r.job_id, j.result);
    });
  } catch (e) { btn.disabled = false; toast(e.message, "err"); }
};

function exDone(jid, res) {
  const box = document.getElementById("ex-done");
  box.innerHTML = `<div class="btnrow" style="margin:0">
    ${chip("ok", "done", "导出完成")}
    <b>${esc(res.name)}</b>
    <span class="hint" style="margin:0">${Math.round(res.size / 1024)} KB · ${res.companies} 家公司 · ${res.has_analysis ? "含 AI 分析" : "未含 AI 分析"}</span>
    <a href="/api/stats/export/${jid}" download><button class="small">${ic("download", 13)}下载</button></a>
    ${res.format === "html" ? `<a href="/api/stats/export/${jid}" target="_blank" rel="noopener"><button class="small ghost">${ic("external", 13)}先看看</button></a>` : ""}</div>`;
  box.style.display = "";
  if (res.analysis) {
    const d = document.createElement("details");
    d.className = "dt";
    d.innerHTML = `<summary>预览 AI 分析</summary><div class="aibox">${mdRender(res.analysis)}</div>`;
    box.appendChild(d);
  }
}

/* ── 移除公司：抓错公司时的清理出口 ──────────────────────────────────────
   两段式，且**预览是强制的**：这是全平台唯一一个会成片删除产出数据的入口，
   不给"删之前看清楚删的是哪几个目录、多少产品"的机会是不行的。
   确认框要求一字不差地输入公司名 —— 下拉选错一项和手打错一个字，代价差得很远。

   公司清单取自总览快照（/api/overview），不是 DIST：DIST 只有"有已分类产品"的公司，
   而抓错的公司很可能整批都没匹配上、只躺在 _未匹配 里，那才是最需要删的一类。 */
let PGPREV = null, PGROWS = [], pgSel = "", pgQ = "";

async function pgFill() {
  const list = document.getElementById("pg-list");
  try {
    const d = await api("/api/overview");
    if (!d.ready || !(d.rows || []).length) {
      PGROWS = [];
      list.innerHTML = `<div class="empty">总览还没有快照 —— 先到总览点一次「重新扫描」</div>`;
      return;
    }
    PGROWS = [...d.rows].sort((a, b) => a.name.localeCompare(b.name, "zh"));
    document.getElementById("pg-badge").innerHTML = MK.warn + "不可撤销";
  } catch (e) {
    PGROWS = [];
    list.innerHTML = `<div class="empty">读不到公司列表：${esc(e.message)}</div>`;
    return;
  }
  // 选中的公司如果已经不在了（比如刚被删掉），选择状态要跟着清掉
  if (pgSel && !PGROWS.some(r => r.name === pgSel)) pgSel = "";
  pgDrawList();
}

/* 搜索是子串匹配，不做分词：公司名里「深圳」「光电」「有限」这种片段最常用来定位，
   前缀匹配反而找不到。96 家公司全量过滤是毫秒级，不需要防抖之外的任何优化。 */
function pgDrawList() {
  const list = document.getElementById("pg-list");
  const q = pgQ.trim().toLowerCase();
  const rs = q ? PGROWS.filter(r => r.name.toLowerCase().includes(q)) : PGROWS;
  document.getElementById("pg-count").innerHTML = PGROWS.length
    ? (pgSel ? `已选 <b>${esc(pgSel)}</b>`
             : `${rs.length} / ${PGROWS.length} 家 · 点一条选中`)
    : "";
  document.getElementById("pg-check").disabled = !pgSel;
  if (!rs.length) {
    list.innerHTML = `<div class="empty">没有匹配「${esc(pgQ)}」的公司</div>`;
    return;
  }
  list.innerHTML = rs.map(r => `
    <button type="button" role="option" aria-selected="${r.name === pgSel}"
            class="${r.name === pgSel ? "on" : ""}" data-c="${esc(r.name)}">
      <span class="nm">${esc(r.name)}</span>
      <span class="n">${fmt(r.products || 0)} 个产品</span>
    </button>`).join("");
  list.querySelectorAll("button").forEach(b => b.onclick = () => {
    // 再点一次＝取消选中；换公司必须让上一份预览立即作废
    pgSel = (pgSel === b.dataset.c) ? "" : b.dataset.c;
    pgInvalidate();
    pgDrawList();
  });
}

const pgWithInput = () => document.getElementById("pg-input").checked;

async function pgCheck() {
  const name = pgSel;
  const box = document.getElementById("pg-preview");
  if (!name) { PGPREV = null; box.innerHTML = ""; return toast("先选一家公司", "err"); }
  const btn = document.getElementById("pg-check");
  btn.disabled = true;
  box.innerHTML = `<div class="empty">正在清点这家公司在各棵树里的产物…</div>`;
  try {
    PGPREV = await api(`/api/company/purge/preview?company=${encodeURIComponent(name)}`
                       + `&with_input=${pgWithInput()}`);
  } catch (e) {
    PGPREV = null;
    box.innerHTML = `<div class="note err">${esc(e.message)}</div>`;
    btn.disabled = false;
    return;
  }
  btn.disabled = false;
  pgDraw();
}

function pgDraw() {
  const d = PGPREV, box = document.getElementById("pg-preview");
  const rows = (d.targets || []).map(t => `
    <div class="pg-row${t.raw ? " raw" : ""}${t.locked ? " locked" : ""}">
      <div class="lb">${esc(t.label)}</div>
      <div class="pa">${esc(t.path)}</div>
      ${t.locked ? `<div class="lk">${MK.warn} 被占用</div>` : ""}
      <div class="nn">${t.products ? fmt(t.products) + " 个产品" : "—"}</div>
    </div>`).join("");
  const regs = (d.registries || []).map(r =>
    `<span class="lstat"><b>${fmt(r.items)}</b>${esc(r.label)}</span>`).join("");
  box.innerHTML = `
    <div class="pg-sum">
      <span>将删除 <b>${fmt(d.products)}</b> 个产品，分布在 <b>${d.targets.length}</b> 处目录</span>
      ${d.company_id && d.company_id !== d.company
        ? `<span class="hint" style="margin:0">最终数据里的目录名（公司id）：<code>${esc(d.company_id)}</code></span>` : ""}
    </div>
    <div class="pg-list">${rows || `<div class="hint">磁盘上没有产物目录，只需清记忆。</div>`}</div>
    ${regs ? `<div class="livestats" style="margin-top:10px">${regs}</div>` : ""}
    ${(d.locked || []).length ? `
      <div class="note err" style="margin-top:10px">${MK.warn}
        <b>有 ${d.locked.length} 处目录正被占用</b>，现在删会失败：<br>
        ${d.locked.map(l => `<code>${esc(l.path)}</code>`).join("<br>")}<br>
        多半是资源管理器正停在这个文件夹里 —— 关掉那个窗口，再点一次「查看将删除什么」。</div>` : ""}
    ${d.with_input ? `
      <div class="note err" style="margin-top:12px;border-left:3px solid var(--err)">
        ${MK.warn} <b>本次包含原始爬取数据</b>：<code>${esc(d.input_path || "—")}</code><br>
        删掉之后这家公司彻底消失，<b>不可恢复、平台不做备份</b>，也无法再重新跑回来。</div>`
      : d.input_kept ? `
      <div class="pg-keep"><b>不会删</b>：输入目录 <code>${esc(d.input_path)}</code><br>
        <b>注意：它还在，下次跑 ② 映射这家公司就会整个回来。</b>
        要连原始数据一起清掉，勾上「同时删除输入目录」再看一次。</div>`
      : `<div class="pg-keep"><b>输入目录里已经没有这家公司</b> —— 删完就彻底干净了。</div>`}
    <div class="pg-confirm">
      <label class="fl" for="pg-typed">确认删除请一字不差地输入公司名：<code>${esc(d.company)}</code></label>
      <div class="row" style="margin-top:6px">
        <div><input type="text" id="pg-typed" placeholder="输入公司名以解锁下面的按钮" autocomplete="off"></div>
        <div style="align-self:flex-end;max-width:200px">
          <button class="danger" id="pg-go" disabled>永久删除这家公司</button></div>
      </div>
    </div>`;
  const typed = document.getElementById("pg-typed");
  const go = document.getElementById("pg-go");
  typed.oninput = () => { go.disabled = typed.value.trim() !== d.company; };
  typed.onkeydown = e => { if (e.key === "Enter" && !go.disabled) go.click(); };
  go.onclick = pgRun;
}

async function pgRun() {
  const d = PGPREV;
  const ok = await confirmBox(
    d.with_input ? "永久删除这家公司（含原始数据）" : "永久删除这家公司", `
    <p>把 <b>${esc(d.company)}</b> 的 <b>${fmt(d.products)}</b> 个产品
       从 <b>${d.targets.length}</b> 处目录里删除，并清掉 ${d.registries.length} 份处理记忆。</p>
    <div class="note" style="margin-top:10px;border-left:3px solid var(--err)">
      <b>删除不可撤销</b>，平台不做备份。<br>
      ${d.with_input
        ? `<b style="color:var(--err)">本次连原始爬取数据一起删</b>：
           <code>${esc(d.input_path || "—")}</code><br>
           删完这家公司彻底消失，<b>再也跑不回来了</b>。`
        : d.input_kept
        ? `输入目录 <code>${esc(d.input_path)}</code> <b>不会被删</b>——
           只要它还在，下次映射这家公司就会整个回来。`
        : `输入目录里已经没有这家公司，删完即彻底移除。`}
    </div>`, { okText: d.with_input ? "确认删除（含原始数据）" : "确认删除", danger: true });
  if (!ok) return;
  const go = document.getElementById("pg-go");
  go.disabled = true;
  try {
    const r = await api("/api/company/purge", { method: "POST",
      body: { company: d.company, confirm: d.company, with_input: !!d.with_input } });
    const ui = liveUI(document.getElementById("pg-box"),
      { cancel: () => api(`/api/jobs/${r.job_id}/cancel`, { method: "POST" }) });
    ui.open();
    pollJob(r.job_id, ui, async j => {
      if (j.status !== "done") { go.disabled = false; return; }
      const res = j.result || {};
      toast(`已移除「${d.company}」：${fmt(res.products || 0)} 个产品、${(res.registries || []).length} 份记忆`
            + (res.with_input ? "，含原始爬取数据" : "")
            + ((res.failed || []).length ? `（${res.failed.length} 处删不掉，见日志）` : ""),
            (res.failed || []).length ? "err" : "ok");
      PGPREV = null;
      document.getElementById("pg-preview").innerHTML = "";
      document.getElementById("pg-input").checked = false;   // 危险选项不留状态
      pgSel = "";
      /* 这家公司已经不在了 —— **每一处引用它的地方都要刷**，漏一处它就以幽灵的形态
         留在界面上，用户以为没删干净：
           pgFill()  移除公司的候选列表
           loadDist() 类别分布快照 → 连带 hero / 一级二级 / 公司透视 / 导出表
           load()     映射构成与趋势（R4，读的是映射记忆，与 dist 是两套口径、两个接口）
           refreshBadge() 导航待办徽标 */
      await pgFill();
      await loadDist();
      await load().catch(() => {});
      refreshBadge();
    });
  } catch (e) { toast(e.message, "err"); go.disabled = false; }
}

document.getElementById("pg-check").onclick = pgCheck;
/* 换公司、或改「是否连原始数据一起删」，上一份预览立即作废 ——
   这两样都改变删除范围，绝不能让「看的是 A、删的是 B」有机可乘。 */
function pgInvalidate() {
  PGPREV = null;
  document.getElementById("pg-preview").innerHTML = "";
}
document.getElementById("pg-input").onchange = pgInvalidate;
let pgDeb = null;
document.getElementById("pg-q").addEventListener("input", e => {
  pgQ = e.target.value;
  clearTimeout(pgDeb);
  pgDeb = setTimeout(pgDrawList, 120);
});
/* 搜索框回车：结果只剩一条时直接选中它 —— 打完字还要再挪去点一下很别扭 */
document.getElementById("pg-q").addEventListener("keydown", e => {
  if (e.key !== "Enter") return;
  const q = pgQ.trim().toLowerCase();
  const rs = q ? PGROWS.filter(r => r.name.toLowerCase().includes(q)) : PGROWS;
  if (rs.length === 1) { pgSel = rs[0].name; pgInvalidate(); pgDrawList(); }
});

/* ── 启动 ─────────────────────────────────────────────────────────────── */
document.getElementById("ov-kpis").innerHTML = skel(4);   // 实时段骨架，DIST 到了才换真值
document.getElementById("s-kpis").innerHTML = skel(6);    // 快照段骨架
dCompany = localStorage.getItem("stats:company") || "";
load();
fillModels("a-model");
fillModels("ex-model", "不做 AI 分析，只导表");
loadDist();
pgFill();
resumeJob("purge", document.getElementById("pg-box"), j => {
  if (j.status === "done") { pgFill(); loadDist(); refreshBadge(); }
});
/* 切页面回来继续接上正在跑的扫描 */
resumeJob("dist", document.getElementById("d-jobbox"), async j => {
  document.getElementById("d-reload").disabled = j.status === "running" || j.status === "queued";
  if (j.status === "done") { await loadDist(); document.getElementById("d-jobbox").style.display = "none"; }
});
resumeJob("export", document.getElementById("ex-box"), j => {
  if (j.status === "done" && j.result) exDone(j.id, j.result);
});