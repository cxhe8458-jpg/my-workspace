/* 公共件：导航注入、API 封装、任务轮询与实时面板、toast、确认框。
   依赖 icons.js（ic / MK / chip），每个页面都要先引 icons.js 再引 common.js。 */

/* 导航分两段：主轴＝流水线上的三站，参考＝查阅与设置。
   主轴项可带徽标（待办数），这是「别忘了还有什么没做」的第一道提醒。 */
const PAGES_MAIN = [
  ["index.html",    "总览",   "overview"],
  ["todo.html",     "待办",   "todo", "badge"],
];
const PAGES_REF = [
  ["taxonomy.html", "分类体系", "taxonomy"],
  ["stats.html",    "统计",     "stats"],
  ["config.html",   "设置",     "gear"],
  ["history.html",  "历史",     "clock"],
];
/* 旧地址 → 现在归属的页面，用于把导航高亮到正确的项。
   2026-07-29 起流水线页整个并入总览：动作挂在公司上（矩阵的「下一步」列），
   不再按步骤分面板——每家公司本来就要走全流程，单独执行某一步只会产生混淆。 */
const PAGE_ALIAS = {
  "manual.html": "todo.html", "review.html": "todo.html",
  "categories.html": "taxonomy.html", "keywords.html": "taxonomy.html",
  "audit.html": "index.html", "process.html": "index.html", "": "index.html",
};

function renderNav() {
  let cur = location.pathname.split("/").pop() || "index.html";
  cur = PAGE_ALIAS[cur] || cur;
  const item = ([f, n, i, b]) =>
    `<a href="${f}" class="${f === cur ? "active" : ""}">${ic(i)}${n}` +
    (b ? `<span class="pill soft" data-badge hidden>0</span>` : "") + `</a>`;

  const bar = document.createElement("div");
  bar.className = "topbar";
  bar.innerHTML =
    `<a href="index.html" class="logo"><span class="mk">${ic("box", 15, 1.8)}</span>数据处理平台</a>
     <nav class="tnav">${PAGES_MAIN.map(item).join("")}</nav>
     <div class="tsep"></div>
     <nav class="tnav ref">${PAGES_REF.map(item).join("")}</nav>
     <div class="tspacer"></div>
     <span class="envtag" id="nav-env">127.0.0.1:8688</span>`;
  document.body.prepend(bar);
  refreshBadge();
}
/* 搜索框的放大镜。CSS 早就为 .srch svg 留好了位置（输入框 padding-left:30px），
   但没有一处往里塞过图标 —— 三个页面的搜索框就一直空着 30px 的左内边距。 */
function fillSearchIcons() {
  document.querySelectorAll(".srch").forEach(box => {
    if (!box.querySelector("svg")) box.insertAdjacentHTML("afterbegin", ic("search", 14));
  });
}
document.addEventListener("DOMContentLoaded", () => { renderNav(); fillSearchIcons(); });

/* 待办徽标：走缓存接口，绝不触发全盘扫描（那要 40 秒）。拿不到就安静地不显示。 */
async function refreshBadge() {
  const el = document.querySelector("[data-badge]");
  if (!el) return;
  try {
    const d = await api("/api/overview/badge");
    const n = d.pending || 0;
    el.hidden = !n;
    el.textContent = n;
    el.className = "pill" + (n ? "" : " soft");
    el.title = d.detail || "";
  } catch (e) { el.hidden = true; }
}

async function api(path, opts = {}) {
  const isForm = opts.body instanceof FormData;
  const r = await fetch(path, {
    ...(isForm ? {} : { headers: { "Content-Type": "application/json" } }),
    ...opts,
    body: opts.body && typeof opts.body !== "string" && !isForm
      ? JSON.stringify(opts.body) : opts.body,
  });
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`);
  return data;
}

function toast(msg, type = "") {
  let box = document.getElementById("toast");
  if (!box) { box = document.createElement("div"); box.id = "toast"; document.body.appendChild(box); }
  const el = document.createElement("div");
  el.className = "tmsg " + type;
  el.setAttribute("role", type === "err" ? "alert" : "status");
  el.innerHTML = `<span class="ti">${ic(type === "err" ? "alert" : type === "ok" ? "check" : "info", 15)}</span><span></span>`;
  el.lastElementChild.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), type === "err" ? 8000 : 5000);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const fmt = n => (typeof n === "number" ? n : 0).toLocaleString("en-US");

/* 确认框：取代原生 confirm()。原生弹窗在长文案下不可读，也没法区分「危险」和「例行」。
   返回 Promise<boolean>。 */
function confirmBox(title, bodyHtml, { okText = "确认", danger = false } = {}) {
  return new Promise(resolve => {
    const m = document.createElement("div");
    m.className = "modal open";
    m.innerHTML = `<div class="box narrow" role="dialog" aria-modal="true">
      <div class="bh">${esc(title)}</div>
      <div class="bc" style="white-space:normal">${bodyHtml}</div>
      <div class="bf"><button class="ghost" data-no>取消</button>
        <button ${danger ? 'class="danger"' : ""} data-yes>${esc(okText)}</button></div></div>`;
    document.body.appendChild(m);
    const done = v => { m.remove(); document.removeEventListener("keydown", key); resolve(v); };
    const key = e => { if (e.key === "Escape") done(false); };
    m.querySelector("[data-no]").onclick = () => done(false);
    m.querySelector("[data-yes]").onclick = () => done(true);
    m.addEventListener("click", e => { if (e.target === m) done(false); });
    document.addEventListener("keydown", key);
    m.querySelector("[data-yes]").focus();
  });
}

/* 把任务快照画到 ui 上（进度条/百分比/计数器/当前条目） */
function paintJob(j, ui) {
  /* 取消按钮跟着状态走。放在这里而不是 finish() 里，是因为 finish() 只有轮询那条路会调，
     而 resumeJob 恢复一个**已经结束**的任务时不走轮询 —— 结果就是任务明明 100%、显示
     「完成」，旁边还杵着一个「取消」按钮，点了什么也不会发生（后端对终态任务返回 false）。 */
  if (ui.cancelBtn) ui.cancelBtn.hidden = !["running", "queued"].includes(j.status);
  if (ui.bar) ui.bar.style.width = j.progress + "%";
  if (ui.pct) ui.pct.textContent =
    `${j.progress}% · ${j.done}/${j.total}` + (j.status === "running" ? "" : " · " + statusText(j.status));
  if (ui.stats && j.stats && Object.keys(j.stats).length) {
    ui.stats.innerHTML = Object.entries(j.stats).map(([k, v]) =>
      `<span class="lstat"><b>${esc(v)}</b>${esc(k)}</span>`).join("");
  }
  if (ui.cur) {
    ui.cur.textContent = j.status === "running"
      ? (j.current ? "正在处理：" + j.current : "准备中…")
      : statusText(j.status);
    ui.cur.classList.toggle("done", j.status === "done");
  }
}

/* ── 列表缓存 ───────────────────────────────────────────────────────────
   为什么要有这个：几个列表接口都要在 /mnt/d(NTFS) 上全树 walk，
   /api/consolidate/status 实测 58 秒。原先每次切页/切 tab 都重新拉一遍，
   点四下就等四分钟。改成：默认吃缓存秒开，用户点「刷新」才真去读。

   cached(key, fetcher, {onData, ttl}) → {load(force), data(), stamp()}
   · load()      有缓存就先渲染缓存、不发请求
   · load(true)  强制重取（刷新按钮走这个）
   · ttl 毫秒，超时的缓存仍然先渲染，但会在后台自动重取一次（不挡手）
*/
function cached(key, fetcher, { onData, ttl = 0 } = {}) {
  const K = "cache:" + key;
  const read = () => { try { return JSON.parse(localStorage.getItem(K) || "null"); } catch { return null; } };
  let cur = read();

  const write = d => {
    cur = { t: Date.now(), d };
    try { localStorage.setItem(K, JSON.stringify(cur)); } catch { /* 配额满就不缓存 */ }
  };

  async function pull(cb) {
    const d = await fetcher();
    write(d);
    if (onData) onData(d, false);
    if (cb) cb(false);
    return d;
  }

  return {
    /* cb(fromCache) 可选：告诉调用方这一份数据到底是缓存还是刚拉的。
       别在外面按 force 推断 —— force=false 也可能因为没有缓存而真去拉了一次。 */
    async load(force = false, cb = null) {
      if (!force && cur && cur.d !== undefined) {
        if (onData) onData(cur.d, true);
        if (cb) cb(true);
        if (ttl && Date.now() - cur.t > ttl) pull().catch(() => {});
        return cur.d;
      }
      return pull(cb);
    },
    drop() { cur = null; try { localStorage.removeItem(K); } catch {} },
    data() { return cur ? cur.d : null; },
    stamp() { return cur ? cur.t : 0; },
  };
}

/* 缓存时间戳 → “读取于 14:52（缓存）”。让用户永远知道自己看的是哪一刻的数据。 */
function stampText(ts, fromCache) {
  if (!ts) return "";
  const d = new Date(ts), p = n => String(n).padStart(2, "0");
  return `读取于 ${p(d.getHours())}:${p(d.getMinutes())}` + (fromCache ? "（缓存）" : "");
}

/* 表单持久化：恢复 localStorage 保存的值，并在修改时自动保存。 */
function persistForm(key, ids) {
  const K = "form:" + key;
  const saved = JSON.parse(localStorage.getItem(K) || "{}");
  const save = () => {
    const o = {};
    ids.forEach(id => {
      const el = document.getElementById(id);
      if (el) o[id] = el.type === "checkbox" ? el.checked : el.value;
    });
    localStorage.setItem(K, JSON.stringify(o));
  };
  ids.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    if (id in saved && saved[id] !== "" && saved[id] !== null) {
      if (el.type === "checkbox") el.checked = saved[id];
      else el.value = saved[id];
    }
    el.addEventListener("change", save);
    el.addEventListener("input", save);
  });
  return { save, saved };
}

/* 页面加载时重连该类型最近一次任务：运行中→继续实时轮询；已结束→恢复完整结果。 */
async function resumeJob(kind, boxEl, onDone) {
  try {
    const j = await api("/api/joblatest?kind=" + kind);
    if (!j || !j.id) return null;
    const ui = liveUI(boxEl, { cancel: () => api(`/api/jobs/${j.id}/cancel`, { method: "POST" }) });
    paintJob(j, ui);
    if (ui.log && j.logs.length) {
      ui.log.textContent = j.logs.join("\n") + "\n";
      ui.log.scrollTop = ui.log.scrollHeight;
    }
    /* queued 也是"还没结束"。以前只认 running，一个排队中的任务会被当成已完成，
       立刻回调 onDone 并显示"完成" —— 队列功能上线后这条一直是错的。 */
    if (j.status === "running" || j.status === "queued") {
      ui.open(); pollJob(j.id, ui, onDone, j.log_next);
    } else {
      // 恢复的是一个已经结束的任务：收尾语义要和轮询那条路完全一致
      // （收起取消按钮、失败时保持日志展开），否则界面上会留下一个点不动的「取消」。
      ui.finish(j);
      if (onDone) onDone(j);
    }
    return j;
  } catch (e) { return null; }
}

/* 任务轮询：ui = liveUI() 的返回值，onDone(result) */
function pollJob(jobId, ui, onDone, startLogFrom = 0) {
  let logFrom = startLogFrom;
  const timer = setInterval(async () => {
    try {
      const j = await api(`/api/jobs/${jobId}?log_from=${logFrom}`);
      logFrom = j.log_next;
      paintJob(j, ui);
      if (ui.log && j.logs.length) {
        ui.log.textContent += j.logs.join("\n") + "\n";
        ui.log.scrollTop = ui.log.scrollHeight;
      }
      if (j.status !== "running" && j.status !== "queued") {
        clearInterval(timer);
        if (j.status === "error") toast("任务失败：" + (j.error || ""), "err");
        ui.finish && ui.finish(j);
        refreshBadge();
        onDone && onDone(j);
      }
    } catch (e) { clearInterval(timer); toast(e.message, "err"); }
  }, 700);
  return timer;
}

/* 统一的实时面板：进度条 + 计数器 + 当前条目 + 日志。
   日志默认收起 —— 跑的时候自动展开，跑完自动收回，没人要在结果页盯着 200 行日志。 */
function liveUI(boxEl, { cancel = null } = {}) {
  boxEl.innerHTML = `
    <div class="jobline">
      <div class="progress" style="flex:1"><div></div></div>
      <span class="pct mono">0%</span>
      ${cancel ? `<button class="ghost small btn-cancel">${ic("x", 13)}取消</button>` : ""}
    </div>
    <div class="livestats"></div>
    <div class="curline">准备中…</div>
    <details class="logwrap"><summary>${ic("chev", 13)}运行日志</summary><div class="joblog"></div></details>`;
  boxEl.style.display = "";
  const cbtn = boxEl.querySelector(".btn-cancel");
  if (cancel && cbtn) cbtn.onclick = cancel;
  const det = boxEl.querySelector(".logwrap");
  return {
    bar: boxEl.querySelector(".progress>div"),
    pct: boxEl.querySelector(".pct"),
    stats: boxEl.querySelector(".livestats"),
    cur: boxEl.querySelector(".curline"),
    log: boxEl.querySelector(".joblog"),
    cancelBtn: cbtn,            // paintJob 据此在任务结束后收起它
    open: () => { det.open = true; },
    /* 失败/取消/中断时日志留着展开——那正是你要看的东西 */
    finish: j => {
      det.open = ["error", "cancelled", "interrupted"].includes(j.status);
      if (cbtn) cbtn.hidden = true;
    },
  };
}
function statusText(s) {
  return { done: "完成", error: "失败", cancelled: "已取消", running: "运行中",
           queued: "排队中", interrupted: "中断（平台重启）" }[s] || s;
}

/* 简易 Markdown 渲染（映射报告用：标题/表格/列表/粗体/代码） */
function mdRender(md) {
  const lines = String(md || "").split("\n"); let html = "", inTable = false;
  const inline = s => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  for (const ln of lines) {
    if (/^\|.*\|$/.test(ln.trim())) {
      const cells = ln.trim().slice(1, -1).split("|");
      if (/^[\s:|-]+$/.test(ln.replace(/\|/g, ""))) continue;
      if (!inTable) { html += '<div class="tablebox"><table>'; inTable = true; }
      html += "<tr>" + cells.map(c => `<td>${inline(c.trim())}</td>`).join("") + "</tr>";
      continue;
    } else if (inTable) { html += "</table></div>"; inTable = false; }
    if (/^# /.test(ln)) html += `<h2 style="margin:10px 0">${inline(ln.slice(2))}</h2>`;
    else if (/^## /.test(ln)) html += `<h3 style="margin:14px 0 6px">${inline(ln.slice(3))}</h3>`;
    else if (/^> /.test(ln)) html += `<div style="color:var(--muted)">${inline(ln.slice(2))}</div>`;
    else if (/^- /.test(ln)) html += `<div>· ${inline(ln.slice(2))}</div>`;
    else html += inline(ln) + "<br>";
  }
  if (inTable) html += "</table></div>";
  return html;
}

function openModal(title, contentHtml) {
  let m = document.getElementById("gmodal");
  if (!m) {
    m = document.createElement("div");
    m.id = "gmodal"; m.className = "modal";
    m.innerHTML = `<div class="box" role="dialog" aria-modal="true">
      <div class="bh"><span id="gm-t"></span>
        <button class="ghost small" data-close>${ic("x", 13)}关闭</button></div>
      <div class="bc" id="gm-c"></div></div>`;
    document.body.appendChild(m);
    m.addEventListener("click", e => { if (e.target === m || e.target.closest("[data-close]")) m.classList.remove("open"); });
    document.addEventListener("keydown", e => { if (e.key === "Escape") m.classList.remove("open"); });
  }
  m.querySelector("#gm-t").textContent = title;
  m.querySelector("#gm-c").innerHTML = contentHtml;
  m.classList.add("open");
}

/* ── 图表公共件 ─────────────────────────────────────────────────────────
   自绘 tooltip：原生 title 有 1 秒延迟、不能带结构、样式不可控。 */
function tipEl() {
  let t = document.getElementById("tip");
  if (!t) { t = document.createElement("div"); t.id = "tip"; t.setAttribute("role", "tooltip"); document.body.appendChild(t); }
  return t;
}
function showTip(e, title, val) {
  const t = tipEl();
  t.innerHTML = `<div class="tt"></div><div class="tv"></div>`;
  t.firstElementChild.textContent = title;
  t.lastElementChild.textContent = val;
  t.classList.add("on");
  const r = t.getBoundingClientRect();
  t.style.left = Math.max(8, Math.min(e.clientX + 14, innerWidth - r.width - 10)) + "px";
  t.style.top = Math.max(8, e.clientY - r.height - 12) + "px";
}
const hideTip = () => tipEl().classList.remove("on");

/* 横向条形图。单系列＝一个蓝、不放图例（标题已经指明它是什么）；
   hover 联动把其余降到 40%，读者的注意力才有落点。
   pct:true 时行尾追加占比列（.bp，样式在 stats2.css）——统计页用，默认 false 全站兼容。 */
function drawBars(box, items, {
  color = "var(--d1)", unit = "个产品", clickable = false, onPick = null,
  empty = "这一段还没有数据。", max = null, pct = false,
} = {}) {
  if (!items || !items.length) { box.innerHTML = `<p class="hint">${esc(empty)}</p>`; return; }
  const tot = items.reduce((s, x) => s + x.n, 0);
  const m = max ?? Math.max(...items.map(x => x.n), 1);
  box.className = "bars";
  box.innerHTML = items.map((x, i) => `
    <div class="brow${clickable ? " clk" : ""}" data-i="${i}"${clickable ? ' tabindex="0" role="button"' : ""}>
      <div class="bl" title="${esc(x.name)}">${esc(x.name)}</div>
      <div class="bt"><div class="bf" style="width:${Math.max(x.n * 100 / m, .6)}%;background:${color}"></div>
        <span class="bv">${fmt(x.n)}</span></div>
      ${pct ? `<div class="bp">${(x.n * 100 / tot).toFixed(1)}%</div>` : ""}
    </div>`).join("");
  box.querySelectorAll(".brow").forEach(row => {
    const x = items[+row.dataset.i];
    row.onmouseenter = () => { box.classList.add("hov"); row.classList.add("on"); };
    row.onmousemove = e => showTip(e, x.name, `${fmt(x.n)} ${unit} · 占 ${(x.n * 100 / tot).toFixed(1)}%`);
    row.onmouseleave = () => { box.classList.remove("hov"); row.classList.remove("on"); hideTip(); };
    if (clickable && onPick) {
      row.onclick = () => { hideTip(); onPick(x.name); };
      row.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(x.name); } };
    }
  });
}

/* 数据表兜底：低对比色块 + 小数值的无障碍出口，dataviz 要求每张图都有 */
function dataTable(headers, rows, title = "") {
  if (!rows || !rows.length) return "";
  return `<details class="dt"><summary>${esc(title ? "查看" + title + "数据表" : "查看数据表")}</summary><div class="in"><table>
    <thead><tr>${headers.map((h, i) => `<th${i ? ' class="n"' : ""}>${esc(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(r => `<tr>${r.map((c, i) => `<td${i ? ' class="n"' : ""}>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div></details>`;
}
