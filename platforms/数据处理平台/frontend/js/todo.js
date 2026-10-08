/* 待办工作台：人工匹配 + AI 研判复核。

   这两件事以前是两个页面，但交互是同构的：选公司 → 挑一条 → 定分类 → 提交，
   而且两边都必须同时做两件事（结果回写映射输出 + 关键词归档进规则库），
   所以合成一个双栏工作台：左边下钻列表，右边处理表单。选中即编辑，不用再往下滚。 */

let TAB = new URLSearchParams(location.search).get("tab") || localStorage.getItem("todo:tab") || "manual";
if (!["manual", "review"].includes(TAB)) TAB = "manual";
/* 只消费一次：用完置空，否则返回键会被无限弹回同一家公司（见 loadCompanies） */
let pendingPre = new URLSearchParams(location.search).get("company") || "";

let CATS = [], comps = [], prods = [], company = "", cur = null, q = "";
let onlyPending = localStorage.getItem("todo:onlyPending") !== "";
/* 搁置的产品默认藏起来（它们已经不是待办了），需要回看时再打开 */
let showShelved = localStorage.getItem("todo:showShelved") === "1";
/* 多选模式：一家公司里几十个归不了类的产品，逐个点「搁置」再逐个确认实在太慢。
   进入多选后点条目＝勾选，不再打开右栏详情。 */
let selMode = false, sel = new Set();

/* 注意：listCompanies 一律返回**未过滤**的全量数据。
   过滤（只看还有待复核的）必须放在渲染层 —— 以前它写在 fetcher 里，而 fetcher 被包在
   cached() 里，切开关时命中缓存直接返回旧结果、fetcher 根本不执行，那个复选框是死的。 */
const T = {
  manual: {
    name: "人工匹配", icon: "hand",
    listCompanies: () => api("/api/manual/companies").then(d => d.companies.map(c => ({ ...c, n: c.products }))),
    listProducts: c => api("/api/manual/products?company=" + encodeURIComponent(c)).then(d => d.products),
    countLabel: "个未匹配",
  },
  review: {
    name: "AI 研判复核", icon: "bot",
    listCompanies: () => api("/api/review/companies").then(d =>
      d.companies.map(c => ({ ...c, n: c.pending }))),
    listProducts: c => api("/api/review/products?company=" + encodeURIComponent(c)).then(d => d.products),
    countLabel: "条待复核",
  },
};

/* 当前 tab 下要显示的公司（渲染时才过滤） */
function visibleComps() {
  return TAB === "review" && onlyPending ? comps.filter(c => c.pending > 0) : comps;
}

/* ── 分类选择器（两个 tab 共用）────────────────────────────────────────── */
function catPicker(pfx) {
  return `
    <div class="row">
      <div><label class="fl">一级分类</label>
        <select id="${pfx}-c1"><option value="">选择一级分类</option></select></div>
      <div><label class="fl">二级分类</label>
        <select id="${pfx}-c2"><option value="">选择二级分类</option></select></div>
    </div>
    <div id="${pfx}-def" class="note" style="display:none"></div>
    <details class="drawer" id="${pfx}-newwrap" style="margin-top:14px">
      <summary><span class="chev"></span><b>现有分类都不合适？新增一个</b>
        <span class="cv">新分类只进自定义表，不写回标准分类树</span></summary>
      <div class="body">
        <div class="row">
          <div><label class="fl">新增方式</label>
            <select id="${pfx}-nmode">
              <option value="cat2">在已有一级分类下新增二级</option>
              <option value="cat1">全新的一级 + 二级</option>
            </select></div>
          <div><label class="fl">一级分类</label>
            <select id="${pfx}-n1sel"></select>
            <input type="text" id="${pfx}-n1inp" placeholder="新一级分类名称" style="display:none"></div>
          <div><label class="fl">新二级分类名称</label><input type="text" id="${pfx}-n2inp" placeholder="例：光学延迟器件"></div>
        </div>
        <div id="${pfx}-d1wrap" style="display:none">
          <label class="fl">新一级分类的定义（必填）</label>
          <textarea id="${pfx}-d1" placeholder="这个一级分类涵盖哪些产品、判定依据是什么……"></textarea>
        </div>
        <label class="fl">新二级分类的定义（必填）</label>
        <textarea id="${pfx}-d2" placeholder="这个二级分类是什么、典型产品有哪些、怎么判定……"></textarea>
        <div class="btnrow"><button id="${pfx}-nsubmit">按新增分类提交</button></div>
      </div>
    </details>`;
}

function bindCatPicker(pfx) {
  const c1 = document.getElementById(pfx + "-c1"), c2 = document.getElementById(pfx + "-c2");
  const fill1 = sel => sel.innerHTML = (sel === c1 ? `<option value="">选择一级分类</option>` : "") +
    CATS.map(c => `<option value="${esc(c.name)}" title="${esc(c.def)}">${esc(c.name)}${c.new ? "（新增）" : ""}</option>`).join("");
  fill1(c1);
  fill1(document.getElementById(pfx + "-n1sel"));
  c1.onchange = () => {
    const c = CATS.find(x => x.name === c1.value);
    c2.innerHTML = `<option value="">选择二级分类</option>` + (c ? c.children.map(k =>
      `<option value="${esc(k.name)}" title="${esc(k.def)}">${esc(k.name)}${k.new ? "（新增）" : ""}</option>`).join("") : "");
    showDef(pfx);
  };
  c2.onchange = () => showDef(pfx);
  document.getElementById(pfx + "-nmode").onchange = e => {
    const n1 = e.target.value === "cat1";
    document.getElementById(pfx + "-n1sel").style.display = n1 ? "none" : "";
    document.getElementById(pfx + "-n1inp").style.display = n1 ? "" : "none";
    document.getElementById(pfx + "-d1wrap").style.display = n1 ? "" : "none";
  };
}

/* 分类定义就摆在选择器下面 —— 判断分类对不对，靠的就是定义 */
function showDef(pfx) {
  const c1 = document.getElementById(pfx + "-c1").value;
  const c2 = document.getElementById(pfx + "-c2").value;
  const c = CATS.find(x => x.name === c1);
  const k = c && c.children.find(x => x.name === c2);
  const box = document.getElementById(pfx + "-def");
  let html = "";
  if (c) html += `<div><b>${esc(c.name)}</b>：${esc(c.def || "（还没有定义）")}</div>`;
  if (k) html += `<div style="margin-top:5px"><b>${esc(k.name)}</b>：${esc(k.def || "（还没有定义）")}</div>`;
  box.innerHTML = html;
  box.style.display = html ? "" : "none";
}

function readNew(pfx) {
  const n1 = document.getElementById(pfx + "-nmode").value === "cat1";
  return {
    category1: n1 ? document.getElementById(pfx + "-n1inp").value.trim()
                  : document.getElementById(pfx + "-n1sel").value,
    category2: document.getElementById(pfx + "-n2inp").value.trim(),
    def1: document.getElementById(pfx + "-d1").value.trim(),
    def2: document.getElementById(pfx + "-d2").value.trim(),
    needDef1: n1,
  };
}
function validNew(v) {
  if (!v.category1) return "先填写或选择一级分类";
  if (!v.category2) return "填写新二级分类的名称";
  if (v.needDef1 && !v.def1) return "新增一级分类必须写定义";
  if (!v.def2) return "新增二级分类必须写定义";
  return "";
}

/* ── 左栏 ─────────────────────────────────────────────────────────────── */
function drawTabs() {
  document.getElementById("tb-tabs").innerHTML = Object.entries(T).map(([k, t]) =>
    `<button data-t="${k}" class="${k === TAB ? "on" : ""}">${t.name}</button>`).join("");
}

function drawCrumb() {
  const el = document.getElementById("tb-crumb");
  el.innerHTML = company
    ? `<button id="tb-back">全部公司</button>${ic("chev", 13)}<b style="color:var(--ink)">${esc(company)}</b>`
    : `<b style="color:var(--ink)">按公司选</b>`;
  const b = document.getElementById("tb-back");
  if (b) b.onclick = () => { company = ""; cur = null; q = ""; document.getElementById("tb-q").value = ""; loadCompanies(); };

  /* 批量确认只在 review 的产品列表里出现 */
  const act = document.getElementById("tb-listact");
  if (TAB === "review" && company) {
    act.innerHTML = `<button class="ghost small" id="tb-batch">${ic("check", 13)}一键确认全部待复核</button>`;
    document.getElementById("tb-batch").onclick = batchConfirm;
  } else if (TAB === "review") {
    act.innerHTML = `<label class="ck" style="font-size:12.5px;color:var(--muted)">
      <input type="checkbox" id="tb-onlyp" ${onlyPending ? "checked" : ""}> 只看还有待复核的</label>`;
    // 纯显示层的过滤，不重新拉数据（数据是全量的，见 T.review.listCompanies 的注释）
    document.getElementById("tb-onlyp").onchange = e => {
      onlyPending = e.target.checked;
      localStorage.setItem("todo:onlyPending", onlyPending ? "1" : "");
      drawList(); drawCount();
    };
  } else if (TAB === "manual" && company) {
    const n = prods.filter(p => p.shelved).length;
    act.innerHTML =
      `<button class="ghost small" id="tb-selmode">${ic(selMode ? "x" : "check", 13)}${
        selMode ? "退出多选" : "多选"}</button>`
      + (n ? ` <label class="ck" style="font-size:12.5px;color:var(--muted)">
          <input type="checkbox" id="tb-shelf" ${showShelved ? "checked" : ""}> 显示已搁置的 ${n} 个</label>` : "");
    document.getElementById("tb-selmode").onclick = () => {
      selMode = !selMode; sel.clear(); drawCrumb(); drawList(); clearDetail();
    };
    const el = document.getElementById("tb-shelf");
    if (el) el.onchange = e => {
      showShelved = e.target.checked;
      localStorage.setItem("todo:showShelved", showShelved ? "1" : "");
      drawList();
    };
  } else act.innerHTML = "";
  drawSelBar();
}

/* 多选模式下的批量操作条。数量为 0 时按钮禁用而不是隐藏 ——
   隐藏会让操作条高度跳动，勾选过程中很晃眼。 */
function drawSelBar() {
  const bar = document.getElementById("tb-selbar");
  if (!bar) return;
  if (!(selMode && TAB === "manual" && company)) { bar.innerHTML = ""; bar.hidden = true; return; }
  const vis = visibleProds();
  const picked = vis.filter(p => sel.has(p.rel));
  const toShelve = picked.filter(p => !p.shelved).length;
  const toFree = picked.filter(p => p.shelved).length;
  bar.hidden = false;
  bar.innerHTML = `
    <label class="ck" style="font-size:12.5px">
      <input type="checkbox" id="sb-all" ${vis.length && picked.length === vis.length ? "checked" : ""}>
      全选当前 ${vis.length} 条</label>
    <span class="fcount">已选 <b>${picked.length}</b></span>
    <button class="ghost small" id="sb-shelve" ${toShelve ? "" : "disabled"}>${ic("box", 12)}搁置选中的 ${toShelve} 个</button>
    <button class="ghost small" id="sb-free" ${toFree ? "" : "disabled"}>${ic("refresh", 12)}取消搁置 ${toFree} 个</button>`;
  document.getElementById("sb-all").onchange = e => {
    sel.clear();
    if (e.target.checked) vis.forEach(p => sel.add(p.rel));
    drawList(); drawSelBar();
  };
  document.getElementById("sb-shelve").onclick = () => bulkShelf(false);
  document.getElementById("sb-free").onclick = () => bulkShelf(true);
}

/* 当前左栏实际显示的产品（搜索 + 搁置过滤之后）—— 全选只应作用在看得见的这些上 */
function visibleProds() {
  const kw = q.toLowerCase();
  let rs = prods.filter(p => !kw || (p.name || "").toLowerCase().includes(kw));
  if (TAB === "review" && onlyPending) rs = rs.filter(p => p.status === "待复核");
  if (TAB === "manual" && !showShelved) rs = rs.filter(p => !p.shelved);
  return rs;
}

async function bulkShelf(undo) {
  const picked = visibleProds().filter(p => sel.has(p.rel) && (undo ? p.shelved : !p.shelved));
  if (!picked.length) return;
  const rels = picked.map(p => p.rel);
  const ok = await confirmBox(undo ? "批量取消搁置" : "批量搁置", `
    <p>${undo ? "把" : "把"} <b>${rels.length}</b> 个产品${undo ? "重新计入待办" : "标为「无法归类」，不再计入待办"}。</p>
    <div class="note" style="margin-top:10px">
      ${picked.slice(0, 6).map(p => esc(p.rel)).join("<br>")}${rels.length > 6 ? `<br>…等 ${rels.length} 个` : ""}
      <hr style="border:0;border-top:1px solid var(--line);margin:8px 0">
      产品原地留在 <code>_未匹配</code> 里，<b>不删除、不移动</b>，随时可以${undo ? "再搁置" : "取消搁置"}。
    </div>
    ${undo ? "" : `<label class="fl" style="margin-top:12px">原因（可选，这一批共用）</label>
      <input type="text" id="sh-why" placeholder="例：标准体系无此品类 / 页面已下架">`}`,
    { okText: undo ? `取消搁置这 ${rels.length} 个` : `搁置这 ${rels.length} 个` });
  if (!ok) return;
  const reason = (document.querySelector("#sh-why") || {}).value || "";
  try {
    const r = await api("/api/manual/shelve_many",
      { method: "POST", body: { company, rels, reason, undo } });
    toast(undo ? `已取消 ${r.removed} 个的搁置，重新计入待办`
               : `已搁置 ${r.shelved} 个，这家还剩 ${r.left} 个待处理`, "ok");
    sel.clear();
    invalidate(TAB, company);
    prods = await prodCache(TAB, company).load(true);
    drawCrumb(); drawList(); clearDetail(); refreshBadge();
  } catch (e) { toast(e.message, "err"); }
}

/* 公司列表与产品列表都要扫目录，切 tab / 切回页面都重来一遍很慢。
   缓存住，点「刷新」才真读；做完一条人工匹配/复核后按 tab 精准失效。 */
const cacheComp = {
  manual: cached("todo:comp:manual", () => T.manual.listCompanies()),
  review: cached("todo:comp:review", () => T.review.listCompanies()),
};
const cacheProd = {};
function prodCache(tab, name) {
  const k = tab + "|" + name;
  if (!cacheProd[k]) cacheProd[k] = cached("todo:prod:" + k, () => T[tab].listProducts(name));
  return cacheProd[k];
}
/* 某家公司的数据变了（重匹配/复核过）→ 丢掉它的缓存，下次自然重读 */
function invalidate(tab, name) {
  cacheComp[tab].drop();
  const c = cacheProd[tab + "|" + name];
  if (c) c.drop();
}

/* 顶部计数。fromCache 由 cached() 告诉我们，不能自己按 force 推断——
   以前写的是 !force，于是首次真拉数据也会标成「（缓存）」，一个用来建立信任的
   时间戳反倒在说谎（空 localStorage 的浏览器首访就能复现）。 */
let compFromCache = false;
function drawCount() {
  const rs = visibleComps();
  document.getElementById("tb-count").innerHTML = rs.length
    ? `<b>${rs.length}</b> 家公司 · 共 <b>${fmt(rs.reduce((s, c) => s + c.n, 0))}</b> ${T[TAB].countLabel}`
      + `<span class="mono" style="color:var(--faint);margin-left:8px">${stampText(cacheComp[TAB].stamp(), compFromCache)}</span>`
    : "";
}

async function loadCompanies(force = false) {
  drawCrumb();
  const box = document.getElementById("tb-list");
  if (force || !cacheComp[TAB].data()) box.innerHTML = `<div class="empty">读取中…</div>`;
  try {
    compFromCache = false;
    const c = cacheComp[TAB];
    // load() 会先吐缓存再（必要时）后台重取，这里用一次性回调抓住真实的 fromCache
    comps = await c.load(force, fc => { compFromCache = fc; });
  } catch (e) { box.innerHTML = `<div class="empty"><b>读不到数据</b>${esc(e.message)}</div>`; return; }
  drawList();
  drawCount();
  clearDetail();
  // PRE_COMPANY 只消费一次：否则「全部公司」返回键调 loadCompanies() 又会被弹回去，
  // 用户永远退不回列表（而这正是从总览「待人工」列点进来的主路径）。
  const pre = pendingPre;
  pendingPre = "";
  if (pre && comps.some(c2 => c2.name === pre)) openCompany(pre);
}

async function openCompany(name, force = false) {
  company = name;
  sel.clear();
  drawCrumb();
  const box = document.getElementById("tb-list");
  const pc = prodCache(TAB, name);
  if (force || !pc.data()) box.innerHTML = `<div class="empty">读取中…</div>`;
  try {
    prods = await pc.load(force);
  } catch (e) { box.innerHTML = `<div class="empty"><b>读不到产品</b>${esc(e.message)}</div>`; return; }
  drawList();
}

function drawList() {
  const box = document.getElementById("tb-list");
  const kw = q.toLowerCase();

  if (!company) {
    const pool = visibleComps();
    const rs = pool.filter(c => !kw || c.name.toLowerCase().includes(kw));
    document.getElementById("tb-q").placeholder = "搜索公司名…";
    if (!rs.length) {
      box.innerHTML = pool.length
        ? `<div class="empty"><b>没有匹配的公司</b>换个搜索词试试。</div>`
        : (TAB === "review" && onlyPending && comps.length)
        ? `<div class="empty"><b>没有还在待复核的公司</b>这 ${comps.length} 家的研判都已经看过了。<br>
            取消勾选「只看还有待复核的」可以回看历史研判。</div>`
        : (TAB === "manual"
          ? `<div class="empty"><b>没有待人工匹配的产品</b>规则和 AI 把这一轮的产品都归好类了。<br>
              下次跑完映射如果有漏网的，会自动出现在这里。</div>`
          : `<div class="empty"><b>没有待复核的研判</b>AI 研判的结果你都看过了。<br>
              到<a href="index.html">总览</a>对公司执行映射（开启 AI 研判），新结果会出现在这里。</div>`);
      return;
    }
    box.innerHTML = rs.map(c => `
      <button class="picker" data-c="${esc(c.name)}">
        <div class="t">${esc(c.name)}</div>
        <div class="m">${TAB === "manual"
          ? `${c.n} 个未匹配`
          : `待复核 ${c.pending} · 已确认 ${c.confirmed} · 已修改 ${c.modified}`}</div>
      </button>`).join("");
    box.querySelectorAll(".picker").forEach(b => b.onclick = () => openCompany(b.dataset.c));
    return;
  }

  document.getElementById("tb-q").placeholder = "搜索产品名…";
  const rs = visibleProds();
  if (!rs.length) {
    box.innerHTML = `<div class="empty"><b>这家公司没有要处理的了</b>
      <div class="btnrow"><button class="ghost small" onclick="document.getElementById('tb-back').click()">回到公司列表</button></div></div>`;
    return;
  }
  const ST = { "待复核": () => chip("warn", "stale", "待复核"), "已确认": () => chip("ok", "done", "已确认"),
               "已修改": () => chip("ai", "done", "已修改") };
  box.innerHTML = rs.map(p => {
    const i = prods.indexOf(p);
    const on = selMode ? sel.has(p.rel) : (cur && cur.rel === p.rel);
    return `<button class="picker ${on ? "on" : ""}${p.shelved ? " dim" : ""}" data-i="${i}">
      <div class="t">${selMode ? `<span class="selbox${sel.has(p.rel) ? " on" : ""}">${
          sel.has(p.rel) ? ic("check", 11, 2.4) : ""}</span>` : ""}${
        esc(p.name || "（无名）")}${TAB === "review" ? (ST[p.status] || ST["待复核"])() : ""}${
        p.shelved ? chip("idle", "done", "已搁置") : ""}${
        p.no_json && !p.shelved ? chip("idle", "idle", "无json") : ""}</div>
      <div class="m">${esc(p.rel)}</div>
      ${TAB === "review" ? `<div class="m" style="color:var(--ai)">AI：${esc(p.category1)} / ${esc(p.category2)}</div>` : ""}
    </button>`;
  }).join("");
  box.querySelectorAll(".picker").forEach(b => b.onclick = () => {
    const p = prods[+b.dataset.i];
    if (!selMode) return pick(+b.dataset.i);
    sel.has(p.rel) ? sel.delete(p.rel) : sel.add(p.rel);
    drawList(); drawSelBar();
  });
}

document.getElementById("tb-q").oninput = e => { q = e.target.value.trim(); drawList(); drawSelBar(); };

/* ── 右栏 ─────────────────────────────────────────────────────────────── */
/* 没选中条目时右栏别只放一句空话 —— 左栏几十家在滚，右栏一片留白很浪费。
   顺手把「活最多的几家」摆出来，点一下直接进去。 */
function clearDetail() {
  cur = null;
  document.getElementById("tb-dtitle").textContent = "处理";
  document.getElementById("tb-dbadge").innerHTML = "";
  const rs = visibleComps();
  const top = rs.slice(0, 6);
  const total = rs.reduce((s, c) => s + c.n, 0);
  document.getElementById("tb-detail").innerHTML =
    `<div class="empty" style="padding:22px 14px"><b>左边选一条开始</b>${TAB === "manual"
      ? "选中的产品会显示在这里，定好分类就能归位。"
      : "选中的研判会显示在这里，确认或改掉它。"}</div>`
    + (top.length ? `
      <div class="note" style="margin-top:4px"><b>还剩 ${fmt(total)} ${T[TAB].countLabel}</b>，
        集中在这几家（已按数量排序，点一下直接进去）：</div>
      <div class="bars" style="margin-top:10px">
        ${top.map(c => `
          <div class="brow clk" data-jump="${esc(c.name)}" tabindex="0" role="button">
            <div class="bl" title="${esc(c.name)}">${esc(c.name)}</div>
            <div class="bt"><div class="bf" style="width:${Math.max(c.n * 100 / (top[0].n || 1), 2)}%;background:var(--d1)"></div>
              <span class="bv">${fmt(c.n)}</span></div>
          </div>`).join("")}
      </div>` : "");
  document.querySelectorAll("#tb-detail [data-jump]").forEach(el => {
    const go = () => openCompany(el.dataset.jump);
    el.onclick = go;
    el.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } };
  });
}

function pick(i) {
  cur = prods[i];
  drawList();
  document.getElementById("tb-dtitle").textContent = cur.name || "（无名产品）";
  document.getElementById("tb-dbadge").innerHTML = cur.url
    ? `<a href="${esc(cur.url)}" target="_blank" rel="noopener">${ic("external", 13)} 打开产品页核实</a>` : "";
  TAB === "manual" ? drawManual() : drawReview();
}

function pathLine() {
  return `<div class="note" style="margin-top:0">原路径 <code>${esc(company)}/${esc(cur.rel)}</code></div>`;
}

/* 人工匹配 */
function drawManual() {
  const shelved = cur.shelved;
  document.getElementById("tb-detail").innerHTML = `
    ${pathLine()}
    ${cur.no_json ? `<div class="note" style="margin-top:8px">${chip("idle", "idle", "无 json")}
       这个产品目录里没有 info.json，产品名取自目录名、也没有页面 URL 可核实——
       参数在图或 PDF 里。</div>` : ""}
    ${shelved ? `<div class="note" style="margin-top:10px">${chip("idle", "done", "已搁置")}
        ${esc(cur.shelf_time || "")}${cur.shelf_reason ? " · " + esc(cur.shelf_reason) : ""}<br>
        它已经不计入待办了。数据仍原地留在 <code>_未匹配</code> 里，随时可以捞回来。</div>
       <div class="btnrow"><button class="ghost" id="m-unshelf">取消搁置，重新当作待办</button></div>` : ""}
    ${catPicker("m")}
    <label class="fl" style="margin-top:14px">归档关键词</label>
    <input type="text" id="m-kw" value="${esc(cur.name || "")}">
    <p class="hint">默认取产品名。<b>改成更通用的词更值钱</b>——型号名只能精确命中这一个产品，
      通用词能让同类新产品下次自动归位，你就不用再来一遍。</p>
    <div class="btnrow"><button id="m-go">重匹配到选中分类</button>
      ${shelved ? "" : `<button class="ghost" id="m-shelf">${ic("box", 13)}归不了类，搁置</button>`}</div>`;
  bindCatPicker("m");
  const sh = document.getElementById("m-shelf");
  if (sh) sh.onclick = async () => {
    const ok = await confirmBox("搁置这个产品", `
      <p>把「<b>${esc(cur.name)}</b>」标为<b>无法归类</b>，不再计入待办。</p>
      <div class="note" style="margin-top:10px">
        产品原地留在 <code>_未匹配</code> 里，<b>不删除、不移动</b>，随时可以取消搁置。<br>
        用途：标准体系里确实没有对应品类、或产品名毫无语义归不出来的。<br>
        搁置之后这家公司才有可能走到「已走完全程」。
      </div>
      <label class="fl" style="margin-top:12px">原因（可选，给将来的自己看）</label>
      <input type="text" id="sh-why" placeholder="例：标准体系无此品类 / 页面已下架">`,
      { okText: "确认搁置" });
    if (!ok) return;
    const why = (document.querySelector("#sh-why") || {}).value || "";
    await shelfCall({ company, rel: cur.rel, reason: why, undo: false });
  };
  const un = document.getElementById("m-unshelf");
  if (un) un.onclick = () => shelfCall({ company, rel: cur.rel, undo: true });
  document.getElementById("m-go").onclick = async () => {
    const c1 = document.getElementById("m-c1").value, c2 = document.getElementById("m-c2").value;
    if (!c1 || !c2) return toast("先选好一级和二级分类", "err");
    const kw = document.getElementById("m-kw").value.trim() || cur.name;
    const ok = await confirmBox("重匹配", `
      <p>把「<b>${esc(cur.name)}</b>」归到 <b>${esc(c1)} / ${esc(c2)}</b>。</p>
      <div class="note" style="margin-top:10px">
        产品回写映射输出，与规则命中、AI 研判的结果汇进同一棵树<br>
        关键词「<b>${esc(kw)}</b>」归档进规则库，下次同类产品自动命中<br>
        同时记进人工映射记录，这家公司重跑映射时会直接沿用
      </div>`, { okText: "确认归类" });
    if (!ok) return;
    await submit("/api/manual/rematch",
      { company, rel: cur.rel, category1: c1, category2: c2, is_new: false, keyword: kw });
  };
  document.getElementById("m-nsubmit").onclick = async () => {
    const v = readNew("m"), bad = validNew(v);
    if (bad) return toast(bad, "err");
    const ok = await confirmBox("新增分类并归位", `
      <p>新建分类 <b>${esc(v.category1)} / ${esc(v.category2)}</b>，并把「${esc(cur.name)}」归进去。</p>
      <p class="hint">新分类只注册进自定义分类表，不会写回标准分类树。</p>`, { okText: "新增并归类" });
    if (!ok) return;
    await submit("/api/manual/rematch", {
      company, rel: cur.rel, category1: v.category1, category2: v.category2,
      is_new: true, def1: v.def1, def2: v.def2,
      keyword: document.getElementById("m-kw").value.trim() || cur.name });
  };
}

/* AI 研判复核 */
function drawReview() {
  const done = cur.status !== "待复核";
  document.getElementById("tb-detail").innerHTML = `
    ${pathLine()}
    <div class="note" style="margin-top:10px">
      <b>AI 判成</b> ${esc(cur.category1)} / ${esc(cur.category2)}<br>
      <b>依据</b> ${esc(cur.basis || "（没有记录依据）")}
    </div>
    ${done ? `<p style="margin-top:14px">${chip("idle", "done", "这一条已经复核过了（" + esc(cur.status) + "），不能再改")}</p>` : `
    <div class="filters" style="margin:16px 0 12px">
      <div class="seg" id="r-mode">
        <button data-m="confirm" class="on">研判正确</button>
        <button data-m="modify">要改分类</button>
      </div>
    </div>
    <div id="r-confirm">
      <label class="fl">归档关键词</label>
      <input type="text" id="r-kw" value="${esc(cur.name || "")}">
      <p class="hint">确认之后这个词会以「人工已确认_ai研判」入库，下次遇到直接规则命中，不再花一次 LLM 调用。
        改成更通用的词（比如「窄线宽激光器」）召回更好。</p>
      <div class="row" style="margin-top:10px">
        <div style="max-width:150px"><label class="fl">置信度</label>
          <input type="number" id="r-conf" step="0.01" min="0.9" max="1" value="0.95"></div>
        <div style="align-self:flex-end"><button id="r-ok">确认并归档</button></div>
      </div>
    </div>
    <div id="r-modify" style="display:none">
      ${catPicker("r")}
      <div class="btnrow"><button id="r-go">提交修改</button></div>
    </div>`}`;
  if (done) return;
  bindCatPicker("r");
  document.getElementById("r-mode").onclick = e => {
    const b = e.target.closest("button"); if (!b) return;
    [...e.currentTarget.children].forEach(x => x.classList.toggle("on", x === b));
    document.getElementById("r-confirm").style.display = b.dataset.m === "confirm" ? "" : "none";
    document.getElementById("r-modify").style.display = b.dataset.m === "modify" ? "" : "none";
  };
  document.getElementById("r-ok").onclick = async () => {
    const kw = document.getElementById("r-kw").value.trim();
    if (!kw) return toast("填一个归档关键词", "err");
    await submit("/api/review/confirm", { company, rel: cur.rel, keyword: kw,
      confidence: parseFloat(document.getElementById("r-conf").value) || 0.95 });
  };
  document.getElementById("r-go").onclick = async () => {
    const c1 = document.getElementById("r-c1").value, c2 = document.getElementById("r-c2").value;
    if (!c1 || !c2) return toast("先选好一级和二级分类", "err");
    const ok = await confirmBox("修改研判", `
      <p>把「${esc(cur.name)}」从 <b>${esc(cur.category1)} / ${esc(cur.category2)}</b>
         改成 <b>${esc(c1)} / ${esc(c2)}</b>。</p>
      <p class="hint">产品在映射输出内移动到新分类，并记进人工映射记录，重跑时沿用。</p>`, { okText: "确认修改" });
    if (!ok) return;
    await submit("/api/review/modify", { company, rel: cur.rel, category1: c1, category2: c2, is_new: false });
  };
  document.getElementById("r-nsubmit").onclick = async () => {
    const v = readNew("r"), bad = validNew(v);
    if (bad) return toast(bad, "err");
    await submit("/api/review/modify", { company, rel: cur.rel,
      category1: v.category1, category2: v.category2, is_new: true, def1: v.def1, def2: v.def2 });
  };
}

/* 搁置 / 取消搁置：只改一条记录，不动数据，所以刷完列表就地回到原位 */
async function shelfCall(body) {
  try {
    await api("/api/manual/shelve", { method: "POST", body });
    toast(body.undo ? "已取消搁置，重新计入待办" : "已搁置，不再计入待办", "ok");
    invalidate(TAB, company);
    prods = await prodCache(TAB, company).load(true);
    const still = prods.find(p => p.rel === body.rel);
    cur = still || null;
    drawList();
    if (cur) drawManual(); else clearDetail();
    refreshBadge();
  } catch (e) { toast(e.message, "err"); }
}

async function submit(url, body) {
  try {
    const r = await api(url, { method: "POST", body });
    toast((r.is_new ? "新分类已注册；" : "") +
      (url.includes("confirm") ? "已确认，关键词已归档进规则库"
        : `已归到 ${r.category1 || body.category1} / ${r.category2 || body.category2}` +
          (r.keyword ? `，关键词「${r.keyword}」已归档` : "")), "ok");
    if (r.is_new) CATS = await api("/api/manual/categories");
    const keep = company;
    invalidate(TAB, keep);                 // 这条已经处理掉了，缓存作废
    prods = await prodCache(TAB, keep).load(true);
    const left = TAB === "review" ? prods.filter(p => p.status === "待复核").length : prods.length;
    clearDetail();
    if (!left) {
      toast(`「${keep}」这一批处理完了`, "ok");
      company = ""; await loadCompanies(true);
    } else { drawList(); }
    refreshBadge();
  } catch (e) { toast(e.message, "err"); }
}

async function batchConfirm() {
  const n = prods.filter(p => p.status === "待复核").length;
  if (!n) return toast("这家公司没有待复核的了", "err");
  const ok = await confirmBox("一键确认", `
    <p>把「${esc(company)}」的全部 <b>${n}</b> 条待复核研判都判为正确，
       并把 <b>${n}</b> 个产品名作为关键词归档进规则库。</p>
    <div class="note" style="margin-top:10px">归档关键词一律取产品名。<br>
      需要自定义关键词的产品，改用单条确认——通用词的召回率比型号名高得多。<br>
      已经在库里的关键词会自动跳过，不算失败。</div>`,
    { okText: `确认这 ${n} 条` });
  if (!ok) return;
  /* 归档要写规则库，条数多时不是瞬间完成 —— 必须给出"正在做"的信号。
     以前这里是裸 await：按钮不禁用、无提示，界面完全静止，看起来像点了没反应。 */
  const btn = document.getElementById("tb-batch");
  const html = btn ? btn.innerHTML : "";
  if (btn) { btn.disabled = true; btn.innerHTML = `${ic("refresh", 13)}正在归档 ${n} 条…`; }
  try {
    const r = await api("/api/review/confirm_all", { method: "POST", body: { company, rels: null } });
    toast(`已确认 ${r.confirmed} 条，归档关键词 ${r.archived} 条`
          + (r.duplicated ? `（${r.duplicated} 条已在库中，跳过）` : ""), "ok");
    invalidate(TAB, company);
    company = ""; clearDetail(); await loadCompanies(true); refreshBadge();
  } catch (e) {
    toast(e.message, "err");
    if (btn) { btn.disabled = false; btn.innerHTML = html; }
  }
}

/* ── 启动 ─────────────────────────────────────────────────────────────── */
document.getElementById("tb-tabs").onclick = e => {
  const b = e.target.closest("button"); if (!b || b.dataset.t === TAB) return;
  TAB = b.dataset.t;
  localStorage.setItem("todo:tab", TAB);
  selMode = false; sel.clear();
  company = ""; cur = null; q = "";
  document.getElementById("tb-q").value = "";
  drawTabs(); loadCompanies();
};
document.getElementById("tb-refresh").onclick = () => {
  company ? openCompany(company, true) : loadCompanies(true);
};

(async () => {
  drawTabs();
  CATS = await api("/api/manual/categories");
  await loadCompanies();
})();
