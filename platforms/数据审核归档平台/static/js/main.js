/* 装配：导航、按钮绑定、键盘层、启动 */
"use strict";

function switchView(name) {
  $$("#topbar nav button").forEach(b => b.classList.toggle("active", b.dataset.view === name));
  $$(".view").forEach(v => v.classList.remove("active"));
  $("#view-" + name).classList.add("active");
  if (name === "archive") loadArchive().catch(e => toast("✘ 加载失败: " + e.message, 6000));
}
$$("#topbar nav button").forEach(b => b.onclick = () => switchView(b.dataset.view));

/* ---- 审核页按钮 ---- */
$("#btn-back").onclick = backToCompanies;
$("#btn-add-product").onclick = () => S.company ? createProductModal() : toast("请先进入一个公司");
$("#btn-pass-all").onclick = e => passAll(e.target);
$("#btn-company-refresh").onclick = e => busy(e.target, async () => {
  const r = await api("/api/refresh?company=" + encodeURIComponent(S.company), { method: "POST" });
  const parts = [];
  if (r.total_new) parts.push(`新增 ${r.total_new} 个产品`);
  if (r.total_changed) parts.push(`${r.total_changed} 个产品内容有更新`);
  toast(parts.length ? "✔ " + parts.join("，") : "✔ 刷新完成，数据无变化", 4000);
  await openCompany(S.company, true);
});
$("#btn-refresh").onclick = e => busy(e.target, async () => {
  const r = await api("/api/refresh", { method: "POST" });
  toast(r.total_new || r.total_changed
    ? `✔ 刷新完成：新增 ${r.total_new}，内容更新 ${r.total_changed}` : "✔ 刷新完成，数据无变化", 4000);
  if (S.company) await openCompany(S.company, true);
  else await loadCompanies();
});
$("#btn-logout").onclick = async () => {
  if (!await confirmModal("退出登录", "<p>退出后需要重新输入用户名和口令才能继续审核。</p>", "退出")) return;
  try { await api("/api/logout", { method: "POST" }); } catch (e) { /* 退了就行，失败也照样跳 */ }
  location.href = "/login";
};
$$("#status-filters button").forEach(b => b.onclick = () => {
  $$("#status-filters button").forEach(x => x.classList.toggle("active", x === b));
  S.filter = b.dataset.f;
  renderTree();
});

/* ---- 归档页按钮 ---- */
$("#btn-archive").onclick = e => doArchive(e.target);

/* ---- 键盘层：KEYMAP 是快捷键与帮助弹窗的唯一来源 ---- */
const KEYMAP = [
  ["A", "标记通过", () => $("#btn-pass")?.click()],
  ["D", "展开/收起「有问题」说明框", () => $("#btn-problem")?.click()],
  ["J", "下一个产品", () => stepProduct(1)],
  ["K", "上一个产品", () => stepProduct(-1)],
  ["N", "跳到下一个未检产品", () => jumpUnchecked()],
  ["W", "打开/关闭 原网页对比", () => $("#btn-compare")?.click()],
  ["E", "全部展开 / 收起参数子表", () => $("#pn-toggle")?.click()],
  ["Esc", "关闭说明框 / 弹窗 / 大图", null],
  ["Ctrl+V", "粘贴剪贴板里的图片（无需先点「＋上传」）", null],
  ["?", "显示本快捷键表", null],
];

function visibleProducts() { return S.products.filter(matchFilter); }

function stepProduct(d) {
  const list = visibleProducts();
  if (!list.length) return;
  const i = list.findIndex(p => p.path === S.current);
  const next = list[(i < 0 ? 0 : i + d + list.length) % list.length];
  if (next) openProduct(next.path);
}

function jumpUnchecked() {
  const list = S.products.filter(p => p.status === "未检");
  if (!list.length) return toast("该公司已无未检产品");
  const later = list.find(p => p.path > (S.current || ""));
  openProduct((later || list[0]).path);
}

document.addEventListener("keydown", e => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const t = e.target;
  if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
  if (e.key === "Escape") {
    if ($("#lightbox").classList.contains("show")) return $("#lightbox").classList.remove("show");
    if (modalOpen()) return closeModal();
    const f = $("#problem-form");
    if (f?.classList.contains("show")) { f.classList.remove("show"); e.preventDefault(); }
    return;
  }
  if (e.key === "?" || (e.key === "/" && e.shiftKey)) { e.preventDefault(); return showKeys(); }
  if (modalOpen()) return;
  if (!$("#view-inspect").classList.contains("active")) return;
  if (!S.current || S.readonly) return;
  const hit = KEYMAP.find(k => k[2] && k[0].toLowerCase() === e.key.toLowerCase());
  if (hit) { e.preventDefault(); hit[2](); }
});

function showKeys() {
  showModal(`<h3>⌨ 键盘快捷键</h3>
    <div class="muted" style="margin-bottom:12px">在「产品审核」页、光标不在输入框内时生效。</div>
    <table class="keys"><tbody>` +
    KEYMAP.map(([k, d]) => `<tr><td><kbd>${esc(k)}</kbd></td><td>${esc(d)}</td></tr>`).join("") +
    `</tbody></table><div class="row"><button class="btn primary" onclick="closeModal()">知道了</button></div>`);
}
$("#btn-keys").onclick = showKeys;

/* ---- 启动 ---- */
checkVersion();
loadCompanies().catch(e => toast("✘ 加载公司列表失败: " + e.message, 8000));
if (cmpOn) $("#workspace").classList.add("compare");
