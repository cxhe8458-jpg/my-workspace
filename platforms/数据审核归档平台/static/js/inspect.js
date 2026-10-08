/* 审核页：公司列表 → 产品树 → 产品详情 → 标记 */
"use strict";

const STAGES = ["未审核", "审核中", "可归档", "已归档"];

/* ---------- 公司列表 ---------- */
/* 首扫期间的轮询：**必须等上一次回来再排下一次**，并逐步退避。
   原来是固定 setTimeout(loadCompanies, 2500)，而首扫期间这个接口本身要花
   一两秒，于是请求首尾相接堆在一起，和后台扫描抢同一个 /mnt/d，越轮询越慢，
   界面看着就是卡死。另外原写法没接 catch，一次失败就是个未处理的 rejection。*/
let _pollTimer = null, _pollWait = 2500;

async function loadCompanies() {
  S.companies = await api("/api/companies");
  const notReady = S.companies.filter(c => !c.ready).length;
  const banner = $("#scan-banner");
  banner.style.display = notReady ? "block" : "none";
  if (notReady) {
    banner.textContent = `⏳ 正在后台扫描产品数据，已就绪 `
      + `${S.companies.length - notReady}/${S.companies.length} 家，已就绪的可直接进入…`;
  }
  renderCompanies();
  clearTimeout(_pollTimer);
  if (notReady) {
    _pollTimer = setTimeout(() => loadCompanies().catch(() => {}), _pollWait);
    _pollWait = Math.min(_pollWait * 1.5, 15000);
  } else {
    _pollWait = 2500;
  }
}

/* 组内排序，审核页与归档页共用一份，别各排各的。
   已归档按归档时间**倒序**：刚归档的排最前，否则按公司名排要翻半天才找得到。
   时间格式固定 YYYY-MM-DD HH:MM:SS，直接按字符串比即可；
   没有时间戳的（旧平台迁移进来的历史条目）沉到最后。 */
function sortStageGroups(g) {
  g["审核中"]?.sort((a, b) => b.coverage - a.coverage);   // 快检完的排最前
  g["已归档"]?.sort((a, b) => (b.archive_time || "").localeCompare(a.archive_time || ""));
  return g;
}

function groupByStage(list) {
  const g = Object.fromEntries(STAGES.map(s => [s, []]));
  list.forEach(c => (g[c.stage] || g["未审核"]).push(c));
  return sortStageGroups(g);
}

function renderCompanies() {
  const g = groupByStage(S.companies);
  const stage = g[S.stage]?.length ? S.stage : (STAGES.find(s => g[s].length) || "未审核");
  S.stage = stage;
  $("#stage-tabs").innerHTML = STAGES.map(s =>
    `<button data-s="${s}" class="${s === stage ? "active" : ""}">${s} <b>${g[s].length}</b></button>`).join("");
  $$("#stage-tabs button").forEach(b => b.onclick = () => {
    S.stage = b.dataset.s; localStorage.setItem("stage", S.stage); renderCompanies();
  });
  $("#company-list").innerHTML = g[stage].map(companyCard).join("")
    || `<div class="empty" style="padding:16px">该阶段暂无公司</div>`;
  $$("#company-list .company-card[data-c]").forEach(el => el.onclick = () => openCompany(el.dataset.c));
}

function companyCard(c) {
  if (!c.ready) return `<div class="company-card" style="opacity:.6"><h3>${esc(c.name)}</h3>
    <div class="meta">⏳ 后台扫描中…</div></div>`;
  const k = c.counts, t = c.total || 1;
  return `<div class="company-card ${c.archived ? "archived" : ""}" data-c="${esc(c.name)}">
    <h3>${c.archived ? "📦 " : ""}${esc(c.name)}</h3>
    <div class="progress">
      <div class="seg-pass" style="width:${k.通过 / t * 100}%"></div>
      <div class="seg-prob" style="width:${k.有问题 / t * 100}%"></div>
    </div>
    <div class="meta">
      <span>共 ${c.total}</span>
      <span>覆盖 <b>${c.coverage}%</b></span>
      <span style="color:var(--green)">通过 ${k.通过}</span>
      <span style="color:var(--red)">有问题 ${k.有问题}</span>
      <span>未检 ${k.未检}</span>
    </div>
    <div class="meta">
      ${c.no_html ? `<span style="color:var(--amber)">⚠ ${c.no_html} 个缺 HTML</span>` : `<span style="color:var(--green)">HTML 齐全</span>`}
      ${c.archived ? `<span>归档于 ${esc(c.archive_time || "")}</span>` : ""}
    </div>
    ${c.archived ? `<div class="meta ellipsis" title="${esc(c.archive_target || "")}">→ ${esc(c.archive_target || "")}</div>` : ""}
  </div>`;
}

/* ---------- 进入公司 ---------- */
async function openCompany(name, keepCurrent) {
  const data = await api("/api/company/" + encodeURIComponent(name));
  if (S.company !== name) S.expanded = new Set();
  S.company = name;
  S.products = data.products;
  S.summary = data.summary;
  S.eligibility = data.eligibility;
  S.readonly = !!data.archived;
  $("#btn-add-product").style.display = S.readonly ? "none" : "";
  $("#btn-pass-all").style.display = S.readonly ? "none" : "";
  $("#company-page").style.display = "none";
  $("#scan-banner").style.display = "none";
  $("#workspace").classList.add("active");
  $("#workspace").classList.toggle("compare", cmpOn);
  $("#side-company").textContent = name;
  $("#side-company").title = name;
  renderTree();
  if (!keepCurrent) {
    S.current = null; S.prod = null;
    $("#detail-body").style.display = "none";
    document.body.classList.remove("dock-on");
    $("#detail-empty").style.display = "block";
  } else if (S.current && S.products.some(p => p.path === S.current)) {
    openProduct(S.current);
  }
}

function backToCompanies() {
  S.company = null; S.current = null; S.prod = null;
  $("#workspace").classList.remove("active");
  $("#company-page").style.display = "";
  document.body.classList.remove("dock-on");
  loadCompanies();
}

/* ---------- 左侧树 ---------- */
const catKey = p => p.categories.join(" / ") || "（未分类）";

function matchFilter(p) {
  if (!S.filter) return true;
  if (S.filter === "no-html") return !p.has_html;
  return p.status === S.filter;
}

function renderTree() {
  const groups = new Map();
  S.products.filter(matchFilter).forEach(p => {
    const k = catKey(p);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(p);
  });
  if (!groups.size) { $("#tree").innerHTML = `<div class="empty" style="padding:10px">没有符合筛选的产品</div>`; return; }
  let html = "";
  for (const [cat, items] of groups) {
    const open = S.expanded.has(cat);
    const done = items.filter(p => p.status !== "未检").length;
    html += `<div class="tree-cat" data-cat="${esc(cat)}">
        <span>${open ? "▾" : "▸"}</span><span class="cat-nm" title="${esc(cat)}">${esc(cat)}</span>
        <span class="cat-cnt">${done}/${items.length}</span></div>`;
    if (open) html += items.map(p => `<div class="tree-item ${p.path === S.current ? "selected" : ""}" data-p="${esc(p.path)}">
        <span class="dot ${p.status}"></span>
        <span class="nm" title="${esc(p.name)}">${esc(p.name)}</span>
        ${p.has_html ? "" : `<span class="flag-html" title="缺少网页源文件">无H</span>`}
      </div>`).join("");
  }
  $("#tree").innerHTML = html;
  $$("#tree .tree-cat").forEach(el => el.onclick = () => {
    const c = el.dataset.cat;
    S.expanded.has(c) ? S.expanded.delete(c) : S.expanded.add(c);
    renderTree();
  });
  $$("#tree .tree-item").forEach(el => el.onclick = () => openProduct(el.dataset.p));
}

/* ---------- 产品详情 ---------- */
async function openProduct(path) {
  const isSwitch = S.current !== path;
  const data = await api("/api/product?path=" + encodeURIComponent(path));
  S.current = path; S.prod = data.product; S.record = data.record;
  S.readonly = !!data.archived;
  const p = S.products.find(x => x.path === path);
  if (p) {
    p.status = data.record.status || "未检";
    p.has_html = data.product.has_html;
    S.expanded.add(catKey(p));
  }
  renderTree();
  renderDetail(data.product, data.record, data.info);
  if (isSwitch) updateComparePane();
  $("#detail-empty").style.display = "none";
  $("#detail-body").style.display = "block";
  document.body.classList.add("dock-on");
  if (isSwitch) {
    $("#detail").scrollIntoView({ behavior: "smooth", block: "start" });
    document.querySelector("#tree .tree-item.selected")?.scrollIntoView({ block: "nearest" });
  }
}

function renderDetail(p, rec, info) {
  const status = rec.status || "未检";
  const ro = S.readonly;
  $("#prod-head").innerHTML = `
    <div class="info">
      <div class="crumb">${esc(p.categories.join(" / "))}</div>
      <h2>${esc(p.name)} <span class="badge ${status}">${status}</span>
        <span class="badge ${p.has_html ? "ok" : "no"}" title="${p.has_html ? esc(p.htmls.join("、")) : "目录里没有 .html 文件"}">HTML ${p.has_html ? "✓" : "✗"}</span>
        ${ro ? `<span class="badge">📦 已归档 · 只读</span>` : ""}</h2>
      <div style="margin-top:6px">
        ${p.url ? `<a href="${esc(p.url)}" target="_blank">🔗 打开官网页面对照 →</a>` : `<span class="muted">⚠ 无页面URL</span>`}
        ${ro ? "" : `<button class="btn small" style="margin-left:8px" onclick="editField('页面URL')" title="修正页面URL">✏️</button>`}
      </div>
      ${rec.note ? `<div style="margin-top:6px;color:var(--red)">问题说明：${esc(rec.note)}</div>` : ""}
      ${p.info_error ? `<div style="color:var(--red)">info.json 解析失败：${esc(p.info_error)}</div>` : ""}
    </div>
    <div style="flex-shrink:0;display:flex;gap:6px">
      <button class="btn small ${cmpOn ? "primary" : ""}" id="btn-compare" onclick="toggleCompareBtn()">⇄ 对比模式</button>
      ${ro ? "" : `<button class="btn small danger" onclick="deleteProduct(this)" title="该产品不属于本公司（误提取）时使用，20 秒内可撤销">🗑 删除产品</button>`}
    </div>`;

  if (ro) {
    ["#pa-main", "#pa-pimg", "#pa-pdf", "#pa-params"].forEach(s => $(s).innerHTML = `<span class="muted">只读</span>`);
  } else {
    $("#pa-params").innerHTML = `<span class="muted">参数只读展示，与官网逐行核对</span>`;
    $("#pa-main").innerHTML = `<button class="btn small" onclick="uploadFile('main','')">＋ 上传主图（可粘贴）</button>`;
    $("#pa-pimg").innerHTML = `<button class="btn small" onclick="uploadFile('param','')">＋ 上传参数图（可粘贴）</button>`;
    $("#pa-pdf").innerHTML = `<button class="btn small" onclick="uploadFile('pdf','')">＋ 上传PDF</button>
      <button class="btn small" onclick="editField('手册URL')">✏️ 手册URL</button>`;
  }
  renderInfo(info);
  renderParams(p);
  renderImgs("#prod-mainimgs", p, p.main_images);
  renderImgs("#prod-paramimgs", p, p.param_images);
  renderPdf(p);
  renderHtml(p);
  renderMarkDock(status, rec);
  $("#prod-history").innerHTML = (rec.history || []).slice().reverse()
    .map(h => `<li>${esc(h.time)} — ${esc(h.event)}</li>`).join("") || `<li class="empty">暂无记录</li>`;
}

/* ---------- 产品信息：info.json 里参数/图片/PDF 之外的字段 ---------- */
/* 产品描述、产品应用、产品特点这些正文字段占比 27%~85%，过去一个都没展示过。
   渲染原则：**原样呈现，不加工数据**——只做「；」分条与长文折叠这类纯排版处理，
   折叠是视觉上的，展开后仍是全文，绝不截断。字段顺序与是否为空由后端给定
   （scanner.INFO_ORDER），前端不再自己判断哪些字段该露面。 */

const INFO_LONG = 200;        // 超过这个字数就先折叠，免得一段描述把整个面板撑爆

/* 爬虫普遍用「；」串接多条特性/应用，拆成条目比一整段好逐条核对。
   ⚠ **只按「；」拆，不要把换行也算进去**（实测过）：
   - 产品特性里 65/131 条含换行，其中绝大多数**自己已经带了「•」项目符号**，
     再套一层 <li> 就成了双重项目符号；
   - 产品描述的换行是散文分段，拆成条目会把一段话打散。
   换行交给 .info-text 的 white-space:pre-wrap 原样保留即可，那才是忠实呈现。 */
function infoSegments(text) {
  const parts = String(text).split(/；+/).map(x => x.trim()).filter(Boolean);
  return parts.length > 1 ? parts : null;
}

function infoValueHtml(v) {
  if (Array.isArray(v)) {
    return `<ul class="info-seg">` + v.map(x =>
      `<li>${esc(x && typeof x === "object" ? JSON.stringify(x, null, 1) : x)}</li>`).join("") + `</ul>`;
  }
  if (v && typeof v === "object") return kvTable(v);      // 如「产品参数」这类子字典
  const text = String(v);
  const segs = infoSegments(text);
  const body = segs
    ? `<ul class="info-seg">` + segs.map(x => `<li>${esc(x)}</li>`).join("") + `</ul>`
    : `<div class="info-text">${esc(text)}</div>`;
  if (text.length <= INFO_LONG) return body;
  return `<div class="info-long clamped">${body}`
    + `<button class="info-more" data-n="${text.length}" onclick="toggleInfoLong(this)">展开全文（${text.length} 字）</button></div>`;
}

window.toggleInfoLong = btn => {
  const clamped = btn.parentElement.classList.toggle("clamped");
  btn.textContent = clamped ? `展开全文（${btn.dataset.n} 字）` : "收起";
};

function renderInfo(info) {
  const box = $("#prod-info");
  $("#pa-info").innerHTML = S.readonly ? `<span class="muted">只读</span>`
    : `<span class="muted">只读展示，与官网正文逐段核对</span>`;
  const fields = (info && info.fields) || [];
  const missing = (info && info.missing) || [];
  let html = "";
  if (!fields.length) {
    html = `<div class="empty">info.json 里除参数、图片、PDF、URL 外没有其他字段</div>`;
  } else {
    const emptyN = fields.filter(f => f.empty).length;
    html = `<div class="muted" style="margin-bottom:8px">共 ${fields.length} 个字段`
      + `${emptyN ? `（其中 <b>${emptyN}</b> 个为空）` : ""} —— 重点核对：描述/特性/应用是否张冠李戴、`
      + `有无截断、是否混进了别的产品或整站通用的宣传文案</div>`
      + `<div class="info-list">` + fields.map(f => `<div class="info-row${f.empty ? " is-empty" : ""}">
        <div class="info-key">${esc(f.key)}</div>
        <div class="info-val">${f.empty
          ? `<span class="info-empty">字段存在但为空 —— 请核对官网是否确实没有这项</span>`
          : infoValueHtml(f.value)}</div>
      </div>`).join("") + `</div>`;
  }
  if (missing.length) {
    html += `<div class="info-missing">info.json 里<b>没有</b> ${missing.map(esc).join("、")} `
      + `字段（该站点可能整站都没采这一项）—— 若官网其实写了，属于漏采</div>`;
  }
  box.innerHTML = html;
}

/* ---------- 参数渲染 ---------- */
const KV_HINT = `<div class="kv-hint">💡 逐行核对时可<b>点击任意一行</b>标记「已核对」（仅本次浏览的视觉辅助，不写入数据）
  　<span id="kv-counter"></span></div>`;

function kvTable(obj) {
  return `<div class="table-wrap"><table class="kv"><tbody>` +
    Object.entries(obj).map(([k, v]) =>
      `<tr><th>${esc(k)}</th><td>${esc(typeof v === "object" ? JSON.stringify(v, null, 1) : v)}</td></tr>`).join("") +
    `</tbody></table></div>`;
}

/* 记录式参数表（company-product-records 新格式）：
   [{group,name,symbol,value,unit,conditions}, ...] —— 每个型号一张表，六列；行级打勾复用 kv 的交互 */
function recTable(recs) {
  const hasGroup = recs.some(r => r.group);
  const cell = v => (v === null || v === undefined || v === "") ? `<span class="rec-null">—</span>` : esc(String(v));
  let h = `<div class="table-wrap"><table class="kv kv-rec"><thead><tr>`
    + (hasGroup ? "<th>组</th>" : "") + "<th>名称</th><th>符号</th><th>值</th><th>单位</th><th>条件</th></tr></thead><tbody>";
  recs.forEach(r => {
    h += "<tr>" + (hasGroup ? `<td>${cell(r.group)}</td>` : "")
      + `<td>${cell(r.name)}</td><td>${cell(r.symbol)}</td><td class="v">${cell(r.value)}</td>`
      + `<td>${cell(r.unit)}</td><td>${cell(r.conditions)}</td></tr>`;
  });
  return h + "</tbody></table></div>";
}

/* 参数块跳转导航（nested / records 共用）：chips + 全部收起 */
function wireParamNav(nav, chips) {
  nav.innerHTML = chips.map(([id, label]) => `<button data-go="${id}" title="${esc(label)}">${esc(label)}</button>`).join("")
    + `<button id="pn-toggle" style="margin-left:auto">全部收起</button>`;
  $$("#param-nav button[data-go]").forEach(c => c.onclick = () => {
    const el = document.getElementById(c.dataset.go);
    el.classList.remove("collapsed");
    el.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  $("#pn-toggle").onclick = e => {
    const blocks = $$("#prod-params .model-block");
    const collapse = !blocks[0].classList.contains("collapsed");
    blocks.forEach(b => b.classList.toggle("collapsed", collapse));
    e.target.textContent = collapse ? "全部展开" : "全部收起";
  };
}

function renderParams(p) {
  const box = $("#prod-params"), nav = $("#param-nav");
  nav.innerHTML = "";
  const params = p.params || {};
  if (p.param_type === "empty") {
    box.innerHTML = `<div class="empty">参数信息为空 {} —— 请核对官网是否确实无参数（漏采是重点检查项）</div>`;
    return;
  }
  if (p.param_type === "images") {
    // 参数图清单有两种写法：{参数图:[...]}，或整个「参数信息」就是个数组（凯普林等站点）
    const imgs = Array.isArray(params) ? params : (params["参数图"] || []);
    box.innerHTML = `<div class="empty">该产品参数以图片呈现，见下方「参数图」（共 ${imgs.length} 张，请与官网逐张核对）</div>`;
    return;
  }
  if (p.param_type === "records") {
    // 记录式（新格式，2026-09 起）：{型号名或表名: [{group,name,symbol,value,unit,conditions}, ...]}
    // 混杂其中的扁平键值仍并入「普通参数」块（老形态兼容）
    const models = [], flat = {};
    for (const [k, v] of Object.entries(params)) {
      if (k === "参数图") continue;
      if (Array.isArray(v) && v.length && v.every(x => x && typeof x === "object" && "name" in x && "value" in x)) models.push([k, v]);
      else flat[k] = Array.isArray(v) ? v.join("；") : v;
    }
    const fN = Object.keys(flat).length;
    const nRec = models.reduce((s, [, v]) => s + v.length, 0);
    let html = `<div class="muted" style="margin-bottom:8px">记录式参数：<b>${models.length}</b> 个型号 / 共 <b>${nRec}</b> 条记录`
      + `${fN ? ` + 普通参数 ${fN} 项` : ""} —— 重点核对：名称 / 符号 / 值 / 单位 / 条件 是否与官网逐格一致，有无漏行、串行</div>` + KV_HINT;
    if (fN) html += `<div class="model-block" id="mb-flat"><div class="model-name"><span class="mb-arrow">▾</span>普通参数<span class="mb-count">${fN} 项</span></div>${kvTable(flat)}</div>`;
    models.forEach(([key, recs], i) => {
      html += `<div class="model-block" id="mb-${i}"><div class="model-name"><span class="mb-arrow">▾</span>${esc(key)}<span class="mb-count">${recs.length} 条</span></div>${recTable(recs)}</div>`;
    });
    box.innerHTML = html;
    if (models.length >= 2) {
      wireParamNav(nav, (fN ? [["mb-flat", "普通参数"]] : []).concat(models.map(([k], i) => [`mb-${i}`, k])));
    }
    bindParamRows();
    return;
  }
  if (p.param_type === "nested") {
    // 「参数图」是图片引用键而非参数项，剥离后再渲染
    const flat = {}, nested = {};
    for (const [k, v] of Object.entries(params)) {
      if (k === "参数图") continue;
      if (v && typeof v === "object" && !Array.isArray(v)) nested[k] = v;
      else flat[k] = Array.isArray(v) ? v.join("；") : v;
    }
    const nN = Object.keys(nested).length, fN = Object.keys(flat).length;
    let html = `<div class="muted" style="margin-bottom:8px">${fN ? `普通参数 ${fN} 项` : ""}${fN && nN ? " + " : ""}${nN ? `${nN} 个子表（多型号或参数分组）` : ""} —— 重点核对：与官网一致？有无漏提/串行？</div>` + KV_HINT;
    if (fN) html += `<div class="model-block" id="mb-flat"><div class="model-name"><span class="mb-arrow">▾</span>普通参数<span class="mb-count">${fN} 项</span></div>${kvTable(flat)}</div>`;
    Object.entries(nested).forEach(([key, vals], i) => {
      html += `<div class="model-block" id="mb-${i}"><div class="model-name"><span class="mb-arrow">▾</span>${esc(key)}<span class="mb-count">${Object.keys(vals).length} 项</span></div>${kvTable(vals)}</div>`;
    });
    box.innerHTML = html;
    if (nN >= 2) {
      wireParamNav(nav, (fN ? [["mb-flat", "普通参数"]] : [])
        .concat(Object.keys(nested).map((k, i) => [`mb-${i}`, k])));
    }
    bindParamRows();
    return;
  }
  const flat = Object.fromEntries(Object.entries(params).filter(([k]) => k !== "参数图"));
  box.innerHTML = `<div class="muted" style="margin-bottom:8px">扁平键值参数（${Object.keys(flat).length} 项）—— 重点核对：有无截断、上下标是否正确、单位与官网一致</div>`
    + KV_HINT + kvTable(flat);
  bindParamRows();
}

/* 行级打勾纯属浏览辅助，切换产品即重置，绝不写入数据 */
function bindParamRows() {
  $$("#prod-params .model-block > .model-name").forEach(h =>
    h.onclick = () => h.parentElement.classList.toggle("collapsed"));
  const rows = $$("#prod-params table.kv tbody tr");
  const counter = $("#kv-counter");
  const sync = () => {
    if (!counter) return;
    const done = $$("#prod-params table.kv tbody tr.kv-done").length;
    counter.innerHTML = done ? `已核对 <b>${done}</b> / ${rows.length} 行` : "";
  };
  rows.forEach(tr => tr.onclick = () => { tr.classList.toggle("kv-done"); sync(); });
  sync();
}

/* ---------- PDF / HTML ---------- */
function renderPdf(p) {
  const box = $("#prod-pdf");
  if (!p.pdfs.length) {
    box.innerHTML = `<div class="empty">无 PDF 说明书${p.manual_url
      ? `（info.json 记了手册URL但本地无文件：<a href="${esc(p.manual_url)}" target="_blank">${esc(p.manual_url)}</a>）`
      : "（手册URL 也为空 —— 请核对官网是否有手册）"}</div>`;
    return;
  }
  box.innerHTML = p.pdfs.map(f => {
    const url = fileUrl(p.path + "/" + f);
    return `<div style="margin-bottom:6px;display:flex;gap:10px;align-items:center">
      <a href="${url}" target="_blank">📄 ${esc(f)}（新窗口打开）</a>
      ${S.readonly ? "" : `<button class="btn small" onclick="uploadFile('pdf','${esc(f)}')">替换</button>
      <button class="btn small" onclick="deleteFile('${esc(f)}')">删除</button>`}
    </div><iframe class="pdf" src="${url}"></iframe>`;
  }).join("");
}

function renderHtml(p) {
  const box = $("#prod-html");
  if (!p.htmls.length) {
    box.innerHTML = `<div class="empty" style="color:var(--red)">✗ 该产品目录下<b>没有网页源文件</b>（.html）—— 仅作提示，不影响标记与归档</div>`;
    return;
  }
  box.innerHTML = `<div style="color:var(--green);margin-bottom:6px">✓ 检测到 ${p.htmls.length} 个网页源文件</div>` +
    p.htmls.map(f => `<div style="margin-bottom:4px"><a href="${fileUrl(p.path + "/" + f)}" target="_blank">📄 ${esc(f)}</a></div>`).join("");
}

/* ---------- 标记栏 ---------- */
function renderMarkDock(status, rec) {
  const box = $("#mark-dock");
  if (S.readonly) {
    box.innerHTML = `<div class="dock-bar"><div class="dock-info"><span class="dock-title">📦 只读复查</span>
      —— 该公司已归档。要修改请到「归档」页点「撤回归档」，数据搬回后即可正常审核。</div></div>`;
    return;
  }
  box.innerHTML = `
    <div id="problem-form">
      <div class="muted" style="margin-bottom:6px">写清问题在哪（会记入审核历史，也会显示在产品页顶部）</div>
      <textarea id="problem-note" placeholder="例：参数表缺第 3 个型号；主图是别家产品；PDF 打不开…">${esc(rec.note || "")}</textarea>
      <div class="row" style="justify-content:flex-start;margin:10px 0 12px">
        <button class="btn danger" id="btn-problem-save">✘ 确认标记有问题</button>
        <button class="btn" id="btn-problem-cancel">取消</button>
      </div>
    </div>
    <div class="dock-bar">
      <div class="dock-info"><span class="dock-title">审核标记</span> —— 标记后自动跳到下一个未检产品</div>
      <div class="mark-actions">
        <button class="btn green" id="btn-pass">✔ 通过<kbd>A</kbd></button>
        <button class="btn danger" id="btn-problem">✘ 有问题<kbd>D</kbd></button>
      </div>
    </div>`;
  $("#btn-pass").onclick = e => busy(e.target, () => submitMark("通过", ""));
  $("#btn-problem").onclick = () => {
    const f = $("#problem-form");
    f.classList.toggle("show");
    if (f.classList.contains("show")) $("#problem-note").focus();
  };
  $("#btn-problem-cancel").onclick = () => $("#problem-form").classList.remove("show");
  $("#btn-problem-save").onclick = e => {
    const note = $("#problem-note").value.trim();
    if (!note) return toast("请先写清问题在哪");
    busy(e.target, () => submitMark("有问题", note));
  };
  observeDock();
}

/* 脱离内容底边时才加阴影 */
function observeDock() {
  const dock = $("#mark-dock");
  if (!dock || dock._ob) return;
  const s = document.createElement("div");
  s.style.cssText = "height:1px";
  dock.after(s);
  dock._ob = new IntersectionObserver(([e]) => dock.classList.toggle("floating", !e.isIntersecting));
  dock._ob.observe(s);
}

async function submitMark(status, note) {
  try {
    const r = await api("/api/mark", { method: "POST", json: { path: S.current, status, note } });
    S.summary = r.summary;
    const p = S.products.find(x => x.path === S.current);
    if (p) { p.status = status; p.note = note; }
    $("#problem-form")?.classList.remove("show");
    if (r.next) {
      toast(`${status === "通过" ? "✔ 已通过" : "✘ 已标记有问题"} → 跳到下一个未检产品`, 2200);
      await openProduct(r.next);
    } else {
      toast(`${status === "通过" ? "✔ 已通过" : "✘ 已标记有问题"}　🎉 该公司已无未检产品`, 3500);
      await openProduct(S.current);
    }
    renderTree();
  } catch (e) { toast("✘ 标记失败: " + e.message, 6000); }
}

/* ---------- 一键通过 ---------- */
async function passAll(btn) {
  const un = S.products.filter(p => p.status === "未检").length;
  const prob = S.products.filter(p => p.status === "有问题").length;
  if (!un) return toast("该公司没有未检产品");
  const ok = await confirmModal("一键通过",
    `<p>将把 <b>${S.company}</b> 下的 <b style="color:var(--blue);font-size:16px">${un}</b> 个<b>未检</b>产品一次性标记为「通过」。</p>
     ${prob ? `<p style="color:var(--red)">已标「有问题」的 ${prob} 个<b>不受影响</b>，仍需你逐个处理后才能归档。</p>` : ""}
     <p class="muted">每个产品都会写入一条审核历史，可追溯。此操作不可批量撤销。</p>`, `确认通过 ${un} 个`);
  if (!ok) return;
  await busy(btn, async () => {
    try {
      const r = await api("/api/company/pass_all", { method: "POST", json: { company: S.company } });
      toast(`✔ 已通过 ${r.passed} 个产品` + (r.skipped_problem ? `（${r.skipped_problem} 个有问题的未动）` : ""), 5000);
      await openCompany(S.company, true);
    } catch (e) { toast("✘ 一键通过失败: " + e.message, 7000); }
  });
}
