/* ==========================================================================
   公司信息采集平台 —— 前端
   --------------------------------------------------------------------------
   两块：采集（配置 + 抓取）与人工审核（逐字段核对 + 修正 + 结论）。
   审核状态一律以服务端为准（落盘在 review_state.json），前端不自己攒状态——
   刷新页面、换台电脑接着审，看到的都得是同一份。
   ========================================================================== */
"use strict";

const $ = id => document.getElementById(id);
const $$ = sel => [...document.querySelectorAll(sel)];
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const S = {            /* 全局状态，只此一份 */
  view: "collect",
  items: [], counts: {}, filter: "",
  key: null, item: null, meta: null,
  collapsed: new Set(["系统字段"]),   // 系统字段默认收起：人不需要核对雪花ID和时间戳
  expandedVals: new Set(),
  sel: new Set(JSON.parse(localStorage.getItem("rvSel") || "[]")),   // 勾选要导出的公司
};

/* ---------- 基础件 ---------- */
let _toastTimer = null;
function toast(msg, ms = 2600) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => t.classList.remove("show"), ms);
}

function showMsg(id, type, text) { const m = $(id); m.className = "msg " + type; m.textContent = text; }
function hideMsg(id) { $(id).className = "msg"; }

async function api(url, opts = {}) {
  if (opts.json) {
    opts.body = JSON.stringify(opts.json);
    opts.headers = { "Content-Type": "application/json" };
    delete opts.json;
  }
  const r = await fetch(url, opts);
  if (!r.ok) {
    let msg = "HTTP " + r.status;
    try { const j = await r.json(); if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (e) { }
    throw new Error(msg);
  }
  return (r.headers.get("content-type") || "").includes("json") ? r.json() : r.text();
}

async function busy(btn, fn) {
  if (!btn || btn.disabled) return;
  const old = btn.textContent;
  btn.disabled = true;
  try { return await fn(); }
  finally { btn.disabled = false; btn.textContent = old; }
}

/* ---------- 分段控件：滑块跟着当前按钮走 ---------- */
function initSeg(seg, onPick) {
  const thumb = seg.querySelector(".thumb");
  const btns = [...seg.querySelectorAll("button")];
  const move = () => {
    const on = seg.querySelector("button.active") || btns[0];
    thumb.style.width = on.offsetWidth + "px";
    thumb.style.transform = `translateX(${on.offsetLeft - btns[0].offsetLeft}px)`;
  };
  btns.forEach(b => b.onclick = () => {
    btns.forEach(x => x.classList.toggle("active", x === b));
    move();
    onPick(b);
  });
  // 字体尚未加载完时按钮宽度会变，两次校正避免滑块错位
  move();
  requestAnimationFrame(move);
  window.addEventListener("resize", move);
  return move;
}

/* ==========================================================================
   视图切换
   ========================================================================== */
initSeg($("nav"), b => switchView(b.dataset.view));

function switchView(name) {
  S.view = name;
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  $("view-" + name).classList.add("active");
  if (name === "review") loadReview();
}

$("btn-goto-review").onclick = () => {
  const b = document.querySelector('#nav button[data-view="review"]');
  b.click();
};

/* ==========================================================================
   采集：配置
   ========================================================================== */
function dbPayload() {
  return {
    host: $("db-host").value.trim() || "127.0.0.1",
    port: parseInt($("db-port").value || "3306", 10),
    database: $("db-database").value.trim(),
    username: $("db-username").value.trim(),
    password: $("db-password").value,
    table: $("db-table").value.trim() || "company",
  };
}

async function loadConfig() {
  try {
    const c = await api("/api/config");
    $("db-host").value = c.db.host;
    $("db-port").value = c.db.port;
    $("db-database").value = c.db.database;
    $("db-username").value = c.db.username;
    $("db-password").value = c.db.password;   // 已脱敏为 ******
    $("db-table").value = c.db.table;
    showAiConfig(c.ai);
  } catch (e) { /* 配置读不到不影响使用 */ }
}

/* 保存整份配置。db 与 ai 两张卡片共用，谁也别把对方的段冲掉 */
async function saveConfig(includeAi) {
  return api("/api/save-config", {
    method: "POST", json: {
      db: dbPayload(),
      auth: { username: "admin", password: "******" },   // 占位：后端会保留旧口令
      defaults: { updatedBy: "admin", isEnabled: 1, isRecommended: 0, accessCount: 0, checkStatus: 0, type: null, industryId: 0 },
      ai: includeAi ? aiPayload() : null,                // null = 后端原样保留旧的 ai 段
    }
  });
}

$("btn-save").onclick = e => busy(e.target, async () => {
  try {
    const j = await saveConfig(true);
    showMsg("db-msg", "ok", j.message || "已保存");
    loadConfig();
  } catch (err) { showMsg("db-msg", "err", "保存失败：" + err.message); }
});

/* ---------- AI 模型接口 ---------- */
let AI_PRESETS = [];

function aiPayload() {
  return {
    provider: $("ai-provider").value,
    api_url: $("ai-url").value.trim(),
    api_key: $("ai-key").value,
    model: $("ai-model").value.trim(),
    max_tokens: parseInt($("ai-maxtokens").value || "8192", 10),
    temperature: parseFloat($("ai-temp").value || "0"),
    timeout: parseInt($("ai-timeout").value || "180", 10),
    reasoning_effort: $("ai-reasoning").value,
    json_mode: $("ai-jsonmode").value === "1",
  };
}

function fillModelList(models) {
  $("ai-model-list").innerHTML = (models || []).map(m => `<option value="${esc(m)}">`).join("");
}

async function loadPresets() {
  try {
    const j = await api("/api/ai/presets");
    AI_PRESETS = j.presets || [];
  } catch (e) { return; }
  $("ai-provider").innerHTML = AI_PRESETS.map(p =>
    `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
  $("ai-provider").onchange = () => {
    const p = AI_PRESETS.find(x => x.id === $("ai-provider").value);
    if (!p) return;
    // 选了预设就把地址填好；「自定义」不覆盖已填的地址
    if (p.api_url) $("ai-url").value = p.api_url;
    fillModelList(p.models);
    if (p.models && p.models.length) $("ai-model").value = p.models[0];
    $("ai-note").textContent = p.note || "";
  };
}

function showAiConfig(ai) {
  if (!ai) return;
  $("ai-provider").value = ai.provider || "deepseek";
  $("ai-url").value = ai.api_url || "";
  $("ai-key").value = ai.api_key || "";       // 已脱敏为 ******
  $("ai-model").value = ai.model || "";
  $("ai-maxtokens").value = ai.max_tokens ?? 8192;
  $("ai-temp").value = ai.temperature ?? 0;
  $("ai-timeout").value = ai.timeout ?? 180;
  $("ai-reasoning").value = ai.reasoning_effort ?? "none";
  $("ai-jsonmode").value = ai.json_mode === false ? "0" : "1";
  const p = AI_PRESETS.find(x => x.id === (ai.provider || ""));
  if (p) { fillModelList(p.models); $("ai-note").textContent = p.note || ""; }
}

$("btn-ai-save").onclick = e => busy(e.target, async () => {
  try {
    const j = await saveConfig(true);
    showMsg("ai-msg", "ok", (j.message || "已保存") + " —— 下一次抓取立刻用新配置，不用重启服务");
    toast("✔ AI 配置已保存");
    loadConfig();
  } catch (err) { showMsg("ai-msg", "err", "保存失败：" + err.message); }
});

$("btn-ai-test").onclick = e => busy(e.target, async () => {
  e.target.textContent = "测试中…";
  hideMsg("ai-msg");
  try {
    const j = await api("/api/test-ai", { method: "POST", json: aiPayload() });
    showMsg("ai-msg", j.ok ? "ok" : "err", j.message);
    toast(j.ok ? "✔ " + j.message : "✘ 测试未通过", 6000);
  } catch (err) { showMsg("ai-msg", "err", "请求失败：" + err.message); }
});

$("btn-ai-models").onclick = e => busy(e.target, async () => {
  e.target.textContent = "拉取中…";
  try {
    const j = await api("/api/ai/models", { method: "POST", json: aiPayload() });
    if (j.models && j.models.length) fillModelList(j.models);
    showMsg("ai-msg", j.ok ? "ok" : "err",
      j.message + (j.models && j.models.length ? "：" + j.models.join("、") : ""));
  } catch (err) { showMsg("ai-msg", "err", "拉取失败：" + err.message); }
});

$("btn-test").onclick = e => busy(e.target, async () => {
  hideMsg("db-msg");
  e.target.textContent = "连接中…";
  try {
    const j = await api("/api/test-db", { method: "POST", json: dbPayload() });
    showMsg("db-msg", j.ok ? "ok" : "err", j.message);
  } catch (err) { showMsg("db-msg", "err", "请求失败：" + err.message); }
});

/* ==========================================================================
   采集：名单导入与抓取
   ========================================================================== */
$("btn-import").onclick = () => $("file-input").click();

$("file-input").onchange = async e => {
  const file = e.target.files[0];
  if (!file) return;
  $("import-state").textContent = "正在解析 " + file.name + " …";
  const fd = new FormData();
  fd.append("file", file);
  try {
    const j = await api("/api/import-list", { method: "POST", body: fd });
    if (!j.ok) { $("import-state").textContent = j.message; showMsg("crawl-msg", "err", j.message); return; }
    $("urls").value = j.items.map(it => it.name ? it.url + "," + it.name : it.url).join("\n");
    $("import-state").textContent = `已识别 ${j.count} 条网址并填入下方，确认后点「开始抓取」`;
    showMsg("crawl-msg", "ok", j.message);
  } catch (err) {
    $("import-state").textContent = "上传失败：" + err.message;
  } finally { e.target.value = ""; }
};

let pollTimer = null;

$("btn-crawl").onclick = async () => {
  const urls = $("urls").value.split("\n").map(s => s.trim()).filter(Boolean)
    .map(l => l.split(/[,，]/)[0].trim()).filter(Boolean);
  if (!urls.length) return showMsg("crawl-msg", "err", "请先输入至少一个网址");
  hideMsg("crawl-msg");
  $("btn-crawl").disabled = true;
  $("progress").style.display = "block";
  try {
    const j = await api("/api/crawl", { method: "POST", json: { urls } });
    if (!j.ok) {
      showMsg("crawl-msg", "err", j.message);
      $("btn-crawl").disabled = false; $("progress").style.display = "none";
      return;
    }
    showMsg("crawl-msg", "info", j.message);
    $("crawl-state").textContent = "抓取中…";
    $("top-stat").textContent = "抓取中…";
    pollTimer = setInterval(poll, 1500);
  } catch (err) {
    showMsg("crawl-msg", "err", "请求失败：" + err.message);
    $("btn-crawl").disabled = false; $("progress").style.display = "none";
  }
};

async function poll() {
  let s;
  try { s = await api("/api/crawl/status"); } catch (e) { return; }
  if (s.total) {
    const pct = s.status === "done" ? 100 : Math.min(95, Math.round(s.done / s.total * 100));
    $("progress-bar").style.width = pct + "%";
  }
  if (s.status === "running") { $("crawl-state").textContent = s.stage || "抓取中…"; return; }
  clearInterval(pollTimer);
  $("btn-crawl").disabled = false;
  $("progress-bar").style.width = "100%";
  $("crawl-state").textContent = s.status === "done" ? "完成" : "出错";
  showMsg("crawl-msg", s.status === "done" ? "ok" : "err", s.message);
  if (s.failed && s.failed.length) {
    showMsg("crawl-msg", "err", s.message + "\n失败：" + s.failed.map(f => f.url).join("、"));
  }
  loadReview();
}

/* ==========================================================================
   人工审核
   ========================================================================== */
initSeg($("rv-seg"), b => { S.filter = b.dataset.f; renderList(); });

function saveSel() { localStorage.setItem("rvSel", JSON.stringify([...S.sel])); }

/* 勾选了多少家还在清单里的公司（重抓/清空后勾选会失效，这里按当前清单裁掉） */
function selCount() { return [...S.sel].filter(k => S.items.some(i => i.key === k)).length; }

function updateSelButton() {
  const n = selCount();
  const b = $("btn-rv-export-sel");
  b.textContent = `导出所选（${n} 家）`;
  b.disabled = !n;
}

async function loadReview() {
  let j;
  try { j = await api("/api/review/list"); } catch (e) { return toast("加载审核清单失败：" + e.message); }
  S.items = j.items || [];
  S.counts = j.counts || {};
  // 清单里已经不存在的勾选 key 清掉，免得「导出所选」数字虚高
  S.sel = new Set([...S.sel].filter(k => S.items.some(i => i.key === k)));
  saveSel();
  renderCounts();
  updateSelButton();
  renderList();
  // 当前选中的公司还在清单里就保持不动，避免标记完一条视图跳走
  if (S.key && S.items.some(i => i.key === S.key)) openItem(S.key, true);
  else if (!S.items.length) { $("rv-body").style.display = "none"; $("rv-empty").style.display = "block"; }
}

function renderCounts() {
  const c = S.counts;
  $("c-total").textContent = `共 ${c.total || 0} 家`;
  $("c-un").textContent = `未检 ${c["未检"] || 0}`;
  $("c-pass").textContent = `通过 ${c["通过"] || 0}`;
  $("c-prob").textContent = `有问题 ${c["有问题"] || 0}`;
  const passed = c["通过"] || 0;
  $("btn-write").textContent = `写入数据库（${passed} 家）`;
  $("btn-write").disabled = !(passed > 0);
  $("btn-rv-export").textContent = `导出已通过（${passed} 家）`;
  $("btn-rv-export").disabled = !(passed > 0);
  $("btn-rv-export-all").disabled = !(c.total > 0);
  $("top-stat").textContent = c.total
    ? `待审 ${c["未检"] || 0} / 共 ${c.total}` : "未抓取";
}

function visibleItems() {
  return S.filter ? S.items.filter(i => i.status === S.filter) : S.items;
}

function renderList() {
  const list = visibleItems();
  if (!list.length) {
    $("rv-items").innerHTML = `<div class="empty">${S.items.length ? "该筛选下没有公司" : "还没有数据"}</div>`;
    return;
  }
  $("rv-items").innerHTML = list.map(it => `
    <div class="rv-item ${it.key === S.key ? "on" : ""}" data-k="${esc(it.key)}">
      <input type="checkbox" class="sel" title="勾选后可用「导出所选」" ${S.sel.has(it.key) ? "checked" : ""}>
      <span class="sdot ${esc(it.status)}"></span>
      <span class="nm ellipsis" title="${esc(it.name)}">${esc(it.name)}</span>
      ${it.edited ? `<span class="tag">改${it.edited}</span>` : ""}
      ${it.about_fallback ? `<span class="tag" title="没找到「关于我们」，字段全部来自首页">首页</span>` : ""}
    </div>`).join("");
  $("rv-items").querySelectorAll(".rv-item").forEach(el => {
    el.onclick = () => openItem(el.dataset.k);
    const cb = el.querySelector("input.sel");
    cb.onclick = e => e.stopPropagation();          // 点勾选框别触发打开公司
    cb.onchange = () => {
      cb.checked ? S.sel.add(el.dataset.k) : S.sel.delete(el.dataset.k);
      saveSel();
      updateSelButton();
    };
  });
}

async function openItem(key, keepScroll) {
  let j;
  try { j = await api("/api/review/item?key=" + encodeURIComponent(key)); }
  catch (e) { return toast("打开失败：" + e.message); }
  S.key = key; S.item = j.item; S.meta = j.meta;
  S.expandedVals.clear();
  $("rv-empty").style.display = "none";
  $("rv-body").style.display = "block";
  renderList();
  renderDetail();
  // 面板开着就自动跟到这家公司的同类页面——不用每换一家都重新点开
  if (CMP.open) cmpShow(CMP.kind);
  if (!keepScroll) window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------- 头部：公司 + 证据入口 ---------- */
function renderDetail() {
  const it = S.item, ev = it.evidence || {}, rv = it.review || {}, rec = it.record || {};
  const st = rv.status || "未检";
  const site = rec.website || ev.site || "";

  const chip = (kind, label, has) => has
    ? `<span class="chip" onclick="openEvidence('${kind}','${esc(label)}')">${esc(label)}</span>`
    : `<span class="chip off" title="抓取时没拿到这个页面">${esc(label)} ✕</span>`;

  const logoImg = ev.logo
    ? `<img class="logo" src="/api/evidence?key=${encodeURIComponent(it.key)}&kind=logo&v=${encodeURIComponent(ev.logo)}" alt="logo"
          title="抓取时下载的 logo（灰色棋盘=透明底）。点击看原图" onclick="openLogo()">`
    : `<div class="logo none">抓取时<br>没找到 logo</div>`;
  const logoActions = ev.logo
    ? `<div class="logo-actions">
         <a class="btn" href="/api/logo-download?key=${encodeURIComponent(it.key)}"
            title="下载 logo，文件名就是这家公司的记录 id">⬇ 下载</a>
         <button class="btn small danger" onclick="delLogo('${esc(it.key)}')"
            title="抓到的 logo 不对时删除，再点下方粘贴框 Ctrl+V 换一张">删除</button>
       </div>`
    : "";

  $("rv-head").innerHTML = `
    <div class="logo-box">
      ${logoImg}
      ${logoActions}
      <div class="logo-paste" id="logo-paste" contenteditable="true"
           title="点击此框后 Ctrl+V 粘贴（复制好的）新 logo；或点「浏览文件」选择本地图片">点击后 Ctrl+V 粘贴新 logo</div>
      <button class="btn small ghost" onclick="$('logo-file').click()" title="或选择本地图片文件">浏览文件</button>
      <input type="file" id="logo-file" accept="image/*" hidden>
    </div>
    <div style="flex:1;min-width:0">
      <h2>${esc(it.name)} <span class="badge ${st}">${st}</span></h2>
      <div class="muted" style="margin-top:3px">
        ${site ? `<a href="${esc(site)}" target="_blank" rel="noopener">${esc(site)}</a>` : "（无官网地址）"}
      </div>
      ${rv.note ? `<div style="margin-top:7px;color:var(--red);font-size:13px">问题说明：${esc(rv.note)}</div>` : ""}
      <div class="chips">
        ${chip("home", "首页原文", ev.home_html)}
        ${chip("about", "关于页原文", ev.about_html)}
        ${chip("contact", "联系页原文", ev.contact_html)}
        ${ev.about_url ? `<a class="chip" href="${esc(ev.about_url)}" target="_blank" rel="noopener">关于页链接 ↗</a>` : ""}
        ${ev.contact_url ? `<a class="chip" href="${esc(ev.contact_url)}" target="_blank" rel="noopener">联系页链接 ↗</a>` : ""}
      </div>
      <div class="chips">
        ${ev.about_fallback ? `<span class="chip warn">没找到「关于我们」，25 个字段其实全部取自首页 —— 概述/分类要重点看</span>` : ""}
        ${ev.contact_missing ? `<span class="chip warn">没抓到「联系我们」，联系方式只能来自关于页</span>` : ""}
        ${ev.url_corrected_from ? `<span class="chip warn">网址协议被自动纠正：${esc(ev.url_corrected_from)} → ${esc(ev.url)}</span>` : ""}
        ${(ev.products && ev.products.length) ? `<span class="chip">首页产品大类：${esc(ev.products.slice(0, 6).join("、"))}</span>` : ""}
      </div>
    </div>`;

  renderFields();
  renderRaw();
  $("rv-note").classList.remove("show");
  $("rv-note-text").value = rv.note || "";
  $("rv-dock-tip").textContent = st === "未检"
    ? "核对完成后给出结论 —— 只有「通过」的才会入库"
    : `当前结论：${st}（可以改）`;
}

/* ---------- 字段表 ---------- */
function valueHtml(field, v) {
  if (v === null || v === undefined || v === "") return `<span class="empty-val">（空）</span>`;
  const text = String(v);
  const long = text.length > 150;
  const open = S.expandedVals.has(field);
  return `<div class="vtext${long && !open ? " clamp" : ""}">${esc(text)}</div>`
    + (long ? `<span class="more" onclick="toggleVal('${esc(field)}')">${open ? "收起" : `展开全文（${text.length} 字）`}</span>` : "");
}

window.toggleVal = f => {
  S.expandedVals.has(f) ? S.expandedVals.delete(f) : S.expandedVals.add(f);
  renderFields();
};

function renderFields() {
  const it = S.item, meta = S.meta, rec = it.record || {}, rv = it.review || {};
  const marks = rv.fields || {}, edits = rv.edits || {};
  const risky = new Set(meta.risky), editable = new Set(meta.editable);

  $("rv-fields").innerHTML = meta.groups.map(([group, fields]) => {
    const collapsed = S.collapsed.has(group);
    const rows = fields.map(f => {
      const mark = marks[f] || "";
      const ed = edits[f];
      return `<div class="frow ${mark}" data-f="${esc(f)}">
        <div class="fname">${esc(meta.cn[f] || f)}${risky.has(f) ? `<span class="risk">重点</span>` : ""}
          <span class="k">${esc(f)}</span></div>
        <div class="fval">${valueHtml(f, rec[f])}${ed ? `<span class="was">已修正 · 抓取原值：${esc(ed.from ?? "（空）")} <a href="javascript:void 0" onclick="revertField('${esc(f)}')">还原</a></span>` : ""}</div>
        <div class="facts">
          <button class="${mark === "ok" ? "on-ok" : ""}" title="标记已核对" onclick="markField('${esc(f)}','ok')">✓</button>
          <button class="${mark === "problem" ? "on-problem" : ""}" title="标记存疑" onclick="markField('${esc(f)}','problem')">!</button>
          ${editable.has(f) ? `<button title="修正这个值" onclick="editField('${esc(f)}')">✎</button>` : ""}
        </div>
      </div>`;
    }).join("");
    return `<div class="fgroup card glass ${collapsed ? "collapsed" : ""}" data-g="${esc(group)}">
      <h3><span class="arrow">▾</span>${esc(group)}
        <span class="muted" style="font-weight:400">${fields.length} 项</span></h3>
      <div class="frows">${rows}</div></div>`;
  }).join("");

  $("rv-fields").querySelectorAll(".fgroup > h3").forEach(h => h.onclick = () => {
    const g = h.parentElement.dataset.g;
    S.collapsed.has(g) ? S.collapsed.delete(g) : S.collapsed.add(g);
    renderFields();
  });
}

window.markField = async (field, action) => {
  const cur = (S.item.review.fields || {})[field];
  try {
    const j = await api("/api/review/field", {
      method: "POST",
      json: { key: S.key, field, action: cur === action ? "clear" : action },
    });
    S.item.review = j.review;
    renderFields();
  } catch (e) { toast("标记失败：" + e.message); }
};

window.editField = field => {
  const row = document.querySelector(`.frow[data-f="${CSS.escape(field)}"] .fval`);
  if (!row || row.querySelector(".fedit")) return;
  const cur = S.item.record[field];
  row.innerHTML = `<div class="fedit" style="flex-direction:column;width:100%">
      <textarea id="ed-input">${esc(cur ?? "")}</textarea>
      <div style="display:flex;gap:8px">
        <button class="btn small primary" id="ed-save">保存</button>
        <button class="btn small ghost" id="ed-cancel">取消</button>
        <span class="muted" style="align-self:center">原值会保留，随时可还原</span>
      </div></div>`;
  const ta = $("ed-input");
  ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
  $("ed-cancel").onclick = () => renderFields();
  $("ed-save").onclick = e => busy(e.target, async () => {
    try {
      const j = await api("/api/review/edit", { method: "POST", json: { key: S.key, field, value: ta.value } });
      S.item.record = j.record; S.item.review = j.review; S.item.name = j.name;
      renderFields();
      toast("已修正并留痕");
      loadReview();
    } catch (err) { toast("修改失败：" + err.message); }
  });
};

window.revertField = async field => {
  try {
    const j = await api("/api/review/revert", { method: "POST", json: { key: S.key, field } });
    S.item.record = j.record; S.item.review = j.review; S.item.name = j.name;
    renderFields();
    toast("已还原为抓取原值");
    loadReview();
  } catch (e) { toast("还原失败：" + e.message); }
};

/* ---------- 原始 25 字段（不入库但决定分类对不对，必须看得见） ---------- */
function renderRaw() {
  const raw = S.item.raw || {}, meta = S.meta;
  const keyset = new Set(meta.raw_key_fields);
  const rows = meta.raw_order.filter(k => k in raw).map(k => {
    const v = raw[k];
    return `<tr class="${keyset.has(k) ? "key" : ""}">
      <th>${esc(meta.raw_cn[k] || k)}</th>
      <td>${v === null || v === undefined || v === "" ? `<span class="muted">（空）</span>` : esc(String(v))}</td></tr>`;
  }).join("");
  $("rv-raw").innerHTML = `<h2><span class="n">原</span>抓取原始字段
      <span class="muted" style="font-weight:400">25 项 · 不入库，但「属性 / 一级分类 / 二级分类」是必填项，错了要打回</span></h2>
    <table class="raw"><tbody>${rows}</tbody></table>`;
}

/* ---------- 原网页对比面板（并排常驻） ---------- */
/* 设计要点，都是"审核时对着原网页改字段"这一条推出来的：
   1. **不是弹窗**：没有遮罩，点左侧任何地方都不会关掉它——否则每改一个字段就要重开一次；
   2. **切公司自动跟随**：换一家公司，面板自动换成那家的同类页面，不用手动重开；
   3. 宽度可拖、开关状态与宽度都记在 localStorage，下次进来还是上次那样。 */
const CMP = {
  open: localStorage.getItem("cmpOpen") === "1",
  kind: localStorage.getItem("cmpKind") || "home",
  width: parseInt(localStorage.getItem("cmpWidth") || "620", 10),
};
const CMP_KIND_CN = { home: "首页", about: "关于页", contact: "联系页" };
const CMP_FIELD = { home: "home_html", about: "about_html", contact: "contact_html" };

function cmpApplyWidth(w) {
  CMP.width = Math.max(360, Math.min(w, Math.round(innerWidth * 0.72)));
  document.documentElement.style.setProperty("--cmp-w", CMP.width + "px");
  localStorage.setItem("cmpWidth", String(CMP.width));
}

/* 面板里显示当前公司的某份存档；kind 缺省沿用上次看的那类 */
function cmpShow(kind) {
  if (!S.item) return;
  const ev = S.item.evidence || {};
  const avail = Object.keys(CMP_FIELD).filter(k => ev[CMP_FIELD[k]]);
  if (!avail.length) {
    CMP.open = false;
    document.body.classList.remove("cmp-on");
    localStorage.setItem("cmpOpen", "0");
    return toast("这家公司抓取时没留下任何网页存档");
  }
  // 想看的那类没有就退到有的那类，并说清楚，别让人对着空白猜
  let k = kind || CMP.kind;
  let fellback = "";
  if (!ev[CMP_FIELD[k]]) { fellback = CMP_KIND_CN[k]; k = avail[0]; }
  CMP.kind = k;
  localStorage.setItem("cmpKind", k);

  CMP.open = true;
  document.body.classList.add("cmp-on");
  localStorage.setItem("cmpOpen", "1");
  cmpApplyWidth(CMP.width);

  const url = `/api/evidence?key=${encodeURIComponent(S.key)}&kind=${k}`;
  const frame = $("cmp-frame");
  if (frame.dataset.src !== url) { frame.src = url; frame.dataset.src = url; }
  $("cmp-open").href = url;
  $("cmp-sub").textContent = S.item.name;
  $("cmp-sub").title = S.item.name;

  // 页签：没有存档的那类置灰但仍显示，让人知道"这家就是没抓到"
  $$("#cmp-tabs button").forEach(b => {
    const has = !!ev[CMP_FIELD[b.dataset.k]];
    b.disabled = !has;
    b.classList.toggle("active", b.dataset.k === k);
    b.title = has ? `查看${CMP_KIND_CN[b.dataset.k]}存档` : "抓取时没拿到这个页面";
  });
  cmpMoveThumb();

  const tips = [];
  if (fellback) tips.push(`这家没有${fellback}存档，已切到${CMP_KIND_CN[k]}`);
  if (k === "about" && ev.about_fallback) tips.push("这份「关于页」其实是首页——抓取时没找到关于我们栏目");
  const tip = $("cmp-tip");
  tip.textContent = tips.join("；");
  tip.classList.toggle("show", !!tips.length);
}

function cmpClose() {
  CMP.open = false;
  document.body.classList.remove("cmp-on");
  localStorage.setItem("cmpOpen", "0");
  const f = $("cmp-frame");
  f.src = "about:blank"; f.dataset.src = "";
}

window.openEvidence = kind => cmpShow(kind);
window.openLogo = () => {
  if (S.key) window.open(`/api/evidence?key=${encodeURIComponent(S.key)}&kind=logo&v=${encodeURIComponent((S.item?.evidence||{}).logo||"")}`, "_blank", "noopener");
};

/* ---------- logo 替换：删掉错图 → 粘贴新图 ---------- */
window.delLogo = async key => {
  if (!confirm("确定删除这家公司的 logo 吗？删除后可点击粘贴框 Ctrl+V 粘贴一张新的。")) return;
  try {
    const j = await api("/api/logo-delete", { method: "POST", json: { key } });
    toast("✔ logo 已删除", 3000);
    if (S.key === key) await openItem(key, true);
    else await loadReview();
  } catch (err) {
    toast("✘ " + err.message, 5000);
  }
};

async function uploadLogo(file) {
  const key = S.key;
  if (!key || !file) return;
  const box = $("logo-paste");
  if (box) box.textContent = "上传中…";
  const fd = new FormData();
  fd.append("key", key);
  fd.append("file", file);
  try {
    const r = await fetch("/api/logo-upload", { method: "POST", body: fd });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || j.message || ("HTTP " + r.status));
    }
    toast("✔ logo 已替换", 3000);
    await openItem(key, true);          // 重新拉取该条，刷新 logo 展示
  } catch (err) {
    toast("✘ 上传失败：" + err.message, 6000);
    if (box) box.textContent = "点击后 Ctrl+V 粘贴新 logo";
  }
}

// 全局委托：粘贴框聚焦时接收 Ctrl+V 的图片（contenteditable 框，删后重建也生效）
document.addEventListener("paste", e => {
  const box = document.activeElement && document.activeElement.closest
    ? document.activeElement.closest("#logo-paste")
    : null;
  if (!box) return;
  const items = (e.clipboardData || {}).items || [];
  for (const it of items) {
    if (it.type && it.type.startsWith("image/")) {
      const f = it.getAsFile();
      if (f) { e.preventDefault(); uploadLogo(f); return; }
    }
  }
});

// 全局委托：「浏览文件」fallback，文件框被重建也生效；允许连续选同一个文件
document.addEventListener("change", e => {
  if (e.target && e.target.id === "logo-file" && e.target.files && e.target.files[0]) {
    uploadLogo(e.target.files[0]);
    e.target.value = "";
  }
});

/* 页签滑块：seg 的通用 initSeg 依赖 click 改 active，这里 active 由 cmpShow 决定，所以单独挪 */
function cmpMoveThumb() {
  const seg = $("cmp-tabs"), thumb = seg.querySelector(".thumb");
  const on = seg.querySelector("button.active") || seg.querySelector("button");
  const first = seg.querySelector("button");
  thumb.style.width = on.offsetWidth + "px";
  thumb.style.transform = `translateX(${on.offsetLeft - first.offsetLeft}px)`;
}
$$("#cmp-tabs button").forEach(b => b.onclick = () => { if (!b.disabled) cmpShow(b.dataset.k); });
$("cmp-close").onclick = cmpClose;

/* 左边缘拖动改宽度 */
(function () {
  const grip = $("cmp-grip");
  let on = false;
  grip.addEventListener("pointerdown", e => {
    on = true; grip.setPointerCapture(e.pointerId);
    grip.classList.add("dragging");
    document.body.classList.add("cmp-resizing");
    e.preventDefault();
  });
  grip.addEventListener("pointermove", e => { if (on) cmpApplyWidth(innerWidth - e.clientX); });
  const end = () => {
    if (!on) return;
    on = false; grip.classList.remove("dragging");
    document.body.classList.remove("cmp-resizing");
  };
  grip.addEventListener("pointerup", end);
  grip.addEventListener("pointercancel", end);
})();

cmpApplyWidth(CMP.width);

/* ---------- 结论 ---------- */
$("btn-problem").onclick = () => {
  const box = $("rv-note");
  if (!box.classList.contains("show")) { box.classList.add("show"); $("rv-note-text").focus(); return; }
  const note = $("rv-note-text").value.trim();
  if (!note) return toast("请先写清问题在哪");
  submitMark("有问题", note);
};
$("btn-pass").onclick = () => submitMark("通过", "");

async function submitMark(status, note) {
  if (!S.key) return;
  try {
    await api("/api/review/mark", { method: "POST", json: { key: S.key, status, note } });
  } catch (e) { return toast("标记失败：" + e.message); }
  // 标记完自动跳到下一个未检的，一路审下去不用回头点列表
  const list = S.items;
  const idx = list.findIndex(i => i.key === S.key);
  const next = list.slice(idx + 1).find(i => i.status === "未检")
    || list.find(i => i.status === "未检" && i.key !== S.key);
  const cur = S.key;
  await loadReview();
  if (next && next.key !== cur) { await openItem(next.key); toast(`已标记「${status}」→ 下一家：${next.name}`); }
  else { await openItem(cur, true); toast(`已标记「${status}」，没有下一个未检的了`); }
}

/* ---------- 工具条 ---------- */
$("btn-rv-refresh").onclick = e => busy(e.target, loadReview);

$("btn-rv-clear").onclick = async () => {
  if (!confirm("确认清空整个审核清单吗？\n\n抓取产物（runs/ 下的 HTML、Excel）不受影响，但所有人工核对结论会一并清掉。")) return;
  try {
    await api("/api/review/clear", { method: "POST" });
    S.key = null; S.item = null;
    S.sel.clear(); saveSel(); updateSelButton();
    $("rv-body").style.display = "none"; $("rv-empty").style.display = "block";
    await loadReview();
    toast("审核清单已清空");
  } catch (e) { toast("清空失败：" + e.message); }
};

$("btn-write").onclick = async e => {
  const n = S.counts["通过"] || 0;
  const un = S.counts["未检"] || 0, pb = S.counts["有问题"] || 0;
  let warn = `确认把 ${n} 家「已通过」的公司写入数据库吗？\n\n写入方式：仅新增（INSERT），按官网去重，绝不覆盖或删除已有数据。`;
  if (un || pb) warn += `\n\n不会写入：未检 ${un} 家、有问题 ${pb} 家。`;
  if (!confirm(warn)) return;
  await busy(e.target, async () => {
    try {
      const j = await api("/api/write-db", { method: "POST" });
      const detail = j.errors && j.errors.length ? "\n\n失败明细：\n" + j.errors.join("\n") : "";
      showMsg("write-msg", j.ok ? "ok" : "err", (j.message || "") + detail);
      toast(j.message || "完成", 5000);
    } catch (err) { toast("写入失败：" + err.message, 5000); }
  });
};

/* 导出：两处按钮共用，只差 scope。
   导出的一律是审核清单里的**当前值**——人工修正过的字段会体现在里面。
   连不上 MySQL 时这就是唯一交付路径，所以前三列专门交代审核结论。 */
/* ---------- 导出字段选择 ---------- */
/* 默认排除 8 个系统维护字段（后端 EXPORT_DEFAULT_SKIP），用户可在弹层里改；
   选择存在 localStorage，对所有导出按钮（所选/已通过/全部）一致生效。 */
let EXP_META = null;          // {columns, cn, skip}，从 /api/export/fields 拉
let expFields = null;         // 当前勾选的字段名数组，null=还没初始化

async function loadExpMeta() { EXP_META = await api("/api/export/fields"); }

function expFieldsInit() {
  if (!EXP_META || expFields !== null) return;
  const saved = localStorage.getItem("rvExpFields");
  expFields = saved
    ? JSON.parse(saved).filter(f => EXP_META.columns.includes(f))
    : EXP_META.columns.filter(f => !EXP_META.skip.includes(f));
  if (!expFields.length) expFields = EXP_META.columns.filter(f => !EXP_META.skip.includes(f));
}

function expSave() {
  expFields = EXP_META.columns.filter(c => expFields.includes(c));   // 按列顺序还原
  localStorage.setItem("rvExpFields", JSON.stringify(expFields));
  updateExpBtn();
}

function updateExpBtn() {
  if (!expFields || !EXP_META) return;
  $("btn-rv-fields").textContent = `导出字段（${expFields.length}/${EXP_META.columns.length}）`;
}

$("btn-rv-fields").onclick = () => {
  if (!EXP_META) return toast("字段清单还没加载好，稍后再点", 3000);
  expFieldsInit();
  $("fmodal-list").innerHTML = EXP_META.columns.map(c => `
    <label class="fmodal-item ${EXP_META.skip.includes(c) ? "is-skip" : ""}">
      <input type="checkbox" data-f="${esc(c)}" ${expFields.includes(c) ? "checked" : ""}>
      <span class="nm">${esc(EXP_META.cn[c] || c)}</span><span class="k">${esc(c)}</span>
    </label>`).join("");
  $("fmodal-list").querySelectorAll("input[type=checkbox]").forEach(cb => cb.onchange = () => {
    const f = cb.dataset.f;
    const i = expFields.indexOf(f);
    if (cb.checked) { if (i < 0) expFields.push(f); }      // 已在列表就别重复加
    else if (i >= 0) expFields.splice(i, 1);               // 不在列表就别乱删（splice(-1) 会删最后一个）
  });
  $("fmodal").style.display = "block";
};
function closeFieldsModal() { $("fmodal").style.display = "none"; }
$("fmodal-cancel").onclick = closeFieldsModal;
$("fmodal-mask").onclick = closeFieldsModal;
$("fmodal-save").onclick = () => { expSave(); closeFieldsModal(); toast(`✔ 已设为导出 ${expFields.length} 个字段`, 3000); };
$("fmodal-all").onclick = () => {
  $("fmodal-list").querySelectorAll("input[type=checkbox]").forEach(cb => { cb.checked = true; });
  expFields = [...EXP_META.columns];
};
$("fmodal-default").onclick = () => {
  const def = EXP_META.columns.filter(c => !EXP_META.skip.includes(c));
  $("fmodal-list").querySelectorAll("input[type=checkbox]").forEach(cb => {
    cb.checked = def.includes(cb.dataset.f);
  });
  expFields = [...def];
};

async function doExport(scope, btn, keys) {
  expFieldsInit();
  await busy(btn, async () => {
    btn.textContent = "导出中…";
    try {
      const url = "/api/export?scope=" + scope
        + (keys && keys.length ? "&keys=" + encodeURIComponent(keys.join(",")) : "")
        + (expFields ? "&fields=" + encodeURIComponent(expFields.join(",")) : "")
        + (expLogosOn() ? "&with_logos=1" : "");
      const r = await fetch(url);
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        toast("✘ " + (j.message || "导出失败"), 6000);
        return showMsg("write-msg", "err", j.message || "导出失败");
      }
      const blob = await r.blob();
      const cd = r.headers.get("Content-Disposition") || "";
      const m = cd.match(/filename\*=UTF-8''([^;]+)/);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = m ? decodeURIComponent(m[1]) : "公司信息.xlsx";
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(a.href);
      let tip = keys && keys.length
        ? `✔ 已导出所选 ${keys.length} 家（含人工修正后的值）`
        : scope === "passed"
          ? `✔ 已导出「通过」的 ${S.counts["通过"] || 0} 家（含人工修正后的值，可直接人工导库）`
          : "✔ 已导出全部（首列审核状态，未检与有问题的也在里面）";
      if (expLogosOn()) tip += "（logo 已一并打包进压缩包）";
      toast(tip, 5000);
      showMsg("write-msg", "ok", tip);
    } catch (err) {
      toast("✘ 导出失败：" + err.message, 6000);
      showMsg("write-msg", "err", "导出失败：" + err.message);
    }
  });
}

$("btn-export").onclick = e => doExport("all", e.target);
$("btn-rv-export").onclick = e => doExport("passed", e.target);
$("btn-rv-export-all").onclick = e => doExport("all", e.target);

/* 「同时导出 logo」开关：勾选后导出的是 zip（Excel + logos 文件夹，解压后同一目录）。
   选择存 localStorage，对所有导出按钮（所选/已通过/全部）一致生效。 */
function expLogosOn() { return localStorage.getItem("rvExpLogos") === "1"; }
function syncExpLogos() { const el = $("exp-logos"); if (el) el.checked = expLogosOn(); }
$("exp-logos").onchange = e => localStorage.setItem("rvExpLogos", e.target.checked ? "1" : "0");
syncExpLogos();

/* 勾选导出：所选里只要有一家不是「通过」就先把话说清楚——
   那行会照常导出并在「审核状态」列标明，避免人拿了表才发现混着没审完的。 */
$("btn-rv-export-sel").onclick = e => {
  const keys = [...S.sel].filter(k => S.items.some(i => i.key === k));
  if (!keys.length) return toast("先在左侧清单里勾选要导出的公司");
  const sel = S.items.filter(i => keys.includes(i.key));
  const un = sel.filter(i => i.status === "未检").length;
  const pb = sel.filter(i => i.status === "有问题").length;
  if (un || pb) {
    if (!confirm(`所选 ${sel.length} 家中：未检 ${un} 家、有问题 ${pb} 家。\n\n`
      + "这些行会照常导出，并在「审核状态」列标明。确定一起导出吗？")) return;
  }
  doExport("", e.target, keys);
};

/* ---------- 键盘：审核页逐条快审 ---------- */
document.addEventListener("keydown", e => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const t = e.target;
  if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) {
    if (e.key === "Escape") t.blur();
    return;
  }
  if (e.key === "Escape" && $("fmodal").style.display !== "none") return closeFieldsModal();
  if (S.view !== "review" || !S.key) return;
  if (e.key.toLowerCase() === "w") {            // W：开/关原网页对比
    e.preventDefault();
    return CMP.open ? cmpClose() : cmpShow(CMP.kind);
  }
  const list = visibleItems();
  const i = list.findIndex(x => x.key === S.key);
  if (e.key.toLowerCase() === "a") { e.preventDefault(); $("btn-pass").click(); }
  else if (e.key.toLowerCase() === "d") { e.preventDefault(); $("btn-problem").click(); }
  else if (e.key.toLowerCase() === "j") { e.preventDefault(); if (list[i + 1]) openItem(list[i + 1].key); }
  else if (e.key.toLowerCase() === "k") { e.preventDefault(); if (list[i - 1]) openItem(list[i - 1].key); }
});

/* ---------- 启动 ---------- */
loadPresets().then(loadConfig);   // 先有预设表，回填配置时才填得出备注与候选模型
loadReview();
loadExpMeta().then(() => { expFieldsInit(); updateExpBtn(); });   // 导出字段选择

// 哨兵：模块完整执行到最后一行才置位。中途抛异常的话它就是 undefined，
// index.html 里的自检脚本据此判断"脚本半路夭折"并弹横幅——
// 这种故障最阴：页面照常渲染，按钮却一个都不响应。
window.__APP_READY__ = true;