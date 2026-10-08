/* 通用 UI 部件：toast / 弹窗 / 大图 / 撤销条 / 按钮 loading / 粘贴路由 */
"use strict";

function toast(msg, ms = 2800, type) {
  if (!type) type = /^[✔🎉]/.test(msg) ? "ok" : (/^[✘⚠]|失败|报错/.test(msg) ? "err" : "");
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show " + type;
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = ""), ms);
}

async function busy(btn, fn) {
  if (!btn || btn.classList.contains("loading")) return;
  btn.classList.add("loading"); btn.disabled = true;
  try { return await fn(); }
  finally { btn.classList.remove("loading"); btn.disabled = false; }
}

/* ---- 弹窗 ---- */
function showModal(html) {
  pasteHandler = null;      // 上一个弹窗的粘贴接管者不能泄漏到新弹窗
  cancelWinRaise();         // 弹窗要接管键盘（Ctrl+V），别让官网窗口把焦点抢走
  $("#modal").innerHTML = html;
  $("#modal-mask").classList.add("show");
}
window.closeModal = () => { $("#modal-mask").classList.remove("show"); pasteHandler = null; };
$("#modal-mask").onclick = e => { if (e.target.id === "modal-mask") closeModal(); };
const modalOpen = () => $("#modal-mask").classList.contains("show");

window.showLightbox = url => { $("#lightbox img").src = url; $("#lightbox").classList.add("show"); };
$("#lightbox").onclick = () => $("#lightbox").classList.remove("show");

/* ---- 确认框（归档、一键通过这类不可逆操作用） ---- */
function confirmModal(title, body, okLabel = "确认") {
  return new Promise(res => {
    showModal(`<h3>${esc(title)}</h3><div style="line-height:1.8">${body}</div>
      <div class="row"><button class="btn" id="cf-no">取消</button>
      <button class="btn primary" id="cf-yes">${esc(okLabel)}</button></div>`);
    $("#cf-no").onclick = () => { closeModal(); res(false); };
    $("#cf-yes").onclick = () => { closeModal(); res(true); };
  });
}

/* ---- 撤销条：删除类操作免确认，20 秒内可整体撤回 ---- */
let undoStack = [], undoTimer = null;

function pushUndo(item) {
  undoStack.push(item);
  const bar = $("#undo-bar");
  const last = undoStack[undoStack.length - 1];
  bar.innerHTML = `<span>已删除${last.type === "product" ? "产品" : ""} ${esc(last.label)}`
    + `${undoStack.length > 1 ? ` 等 ${undoStack.length} 项` : ""}</span>
    <button class="btn small" id="btn-undo">↩ 撤销${undoStack.length > 1 ? `全部 (${undoStack.length})` : ""}</button>
    <span class="undo-cd" id="undo-cd">20s</span>`;
  bar.classList.add("show");
  clearInterval(undoTimer);
  let left = 20;                       // 新的删除会重置倒计时，连续删可一起撤销
  undoTimer = setInterval(() => {
    left--;
    const cd = $("#undo-cd");
    if (cd) cd.textContent = left + "s";
    if (left <= 0) hideUndo();
  }, 1000);
  $("#btn-undo").onclick = e => busy(e.target, runUndo);
}

function hideUndo() { clearInterval(undoTimer); undoStack = []; $("#undo-bar").classList.remove("show"); }

async function runUndo() {
  const items = [...undoStack];
  let ok = 0, fail = 0, hadProduct = false;
  for (const it of items) {
    try {
      if (it.type === "product") { await api("/api/product/restore", { method: "POST", json: { id: it.id } }); hadProduct = true; }
      else await api("/api/fix/restore_file", { method: "POST", json: { path: it.path, filename: it.label, backup: it.backup } });
      ok++;
    } catch (e) { fail++; }
  }
  hideUndo();
  toast(fail ? `⚠ 已恢复 ${ok} 个，${fail} 个失败` : `✔ 已恢复 ${ok} 个`, 4000);
  if (hadProduct && S.company) await openCompany(S.company, true);
  else if (S.current) openProduct(S.current);
}

/* ---- 粘贴路由：弹窗开着交给弹窗；否则交给产品页 ---- */
let pasteHandler = null;
document.addEventListener("paste", async e => {
  const t = e.target instanceof Element ? e.target : null;
  if (t && t.closest("input, textarea, select, [contenteditable=true]")) return;  // 往输入框粘文本别拦
  const items = [...(e.clipboardData?.items || [])];
  // getAsFile 必须在事件同步阶段调用，await 之后 items 就失效了
  const files = items.filter(i => i.kind === "file" && i.type.startsWith("image/"))
    .map(i => i.getAsFile()).filter(Boolean);

  if (modalOpen()) {
    if (!pasteHandler) return;
    if (!files.length) return pasteHint(items);
    e.preventDefault();
    toast(pasteMsg(await pasteHandler(files) || {}));
    return;
  }
  if (!$("#view-inspect").classList.contains("active") || !S.current || !S.prod || S.readonly) return;
  if (!files.length) return pasteHint(items);
  e.preventDefault();
  toast(pasteMsg(await pasteChooser(files)) + "，请选择存为主图还是参数图", 4500);
});

function pasteHint(items) {
  // 粘不上的头号原因：复制的是网页片段而不是图片本身。说清楚，别静默。
  if (items.some(i => i.type === "text/html" || i.type === "text/uri-list"))
    toast("剪贴板里是网页片段/链接，不是图片本身：请在官网图片上右键 →「复制图片」后再粘贴", 6000);
}

function pasteMsg({ added = 0, dup = 0 } = {}) {
  if (!added) return `⚠ 这 ${dup} 张图片刚才已经粘过了，没有重复添加`;
  return `✔ 已粘贴 ${added} 张图片` + (dup ? `（跳过 ${dup} 张重复的）` : "");
}
