/* 归档页：门槛校验 → 勾选 → 移动文件夹 → 台账 */
"use strict";

let archList = [], archStage = localStorage.getItem("archStage") || "可归档";
const ARCH_STAGES = ["可归档", "审核中", "未审核", "已归档"];
const archSelected = new Set();

async function loadArchive() {
  const [list, led] = await Promise.all([
    api("/api/archive/candidates"),
    api("/api/archive/ledger"),
  ]);
  archList = list;
  if (!$("#archive-path").value) $("#archive-path").value = led.archive_dir;
  renderArchList();
  renderLedger(led.entries);
}

function renderArchList() {
  const g = Object.fromEntries(ARCH_STAGES.map(s => [s, []]));
  archList.forEach(c => (g[c.stage] || g["未审核"]).push(c));
  sortStageGroups(g);                       // 已归档按归档时间倒序，见 inspect.js
  const stage = g[archStage]?.length ? archStage : (ARCH_STAGES.find(s => g[s].length) || "可归档");
  archStage = stage;
  $("#arch-tabs").innerHTML = ARCH_STAGES.map(s =>
    `<button data-s="${s}" class="${s === stage ? "active" : ""}">${s} <b>${g[s].length}</b></button>`).join("");
  $$("#arch-tabs button").forEach(b => b.onclick = () => {
    archStage = b.dataset.s; localStorage.setItem("archStage", archStage); renderArchList();
  });

  $("#arch-list").innerHTML = g[stage].map(archCard).join("")
    || `<div class="empty" style="padding:16px">该阶段暂无公司</div>`;
  $$("#arch-list input[type=checkbox]").forEach(cb => cb.onchange = () => {
    cb.checked ? archSelected.add(cb.dataset.c) : archSelected.delete(cb.dataset.c);
  });
  // 点"去处理"直接跳到审核页那家公司，不用自己再翻一遍
  $$("#arch-list button[data-goto]").forEach(b => b.onclick = async () => {
    switchView("inspect");
    await openCompany(b.dataset.goto);
    const bad = S.products.find(p => p.status !== "通过");
    if (bad) openProduct(bad.path);
  });
  $$("#arch-list button[data-revoke]").forEach(b => b.onclick = e => busy(e.target, () => revoke(b.dataset.revoke)));
}

function archCard(c) {
  const e = c.eligibility || {};
  const k = c.counts;
  if (c.archived) {
    return `<div class="company-card archived">
      <h3>📦 ${esc(c.name)}</h3>
      <div class="meta"><span style="color:var(--green);font-weight:700">已归档</span>
        <span>${esc(c.archive_time || "")}</span><span>共 ${c.total}</span></div>
      <div class="meta ellipsis" title="${esc(c.archive_target || "")}">→ ${esc(c.archive_target || "")}</div>
      <div class="card-actions"><button class="btn small" data-revoke="${esc(c.name)}"
        title="把文件夹搬回库内，之后才能修改">↩ 撤回归档</button></div>
    </div>`;
  }
  return `<div class="company-card" style="cursor:default">
    <h3>${esc(c.name)}</h3>
    <div class="meta">
      <span>共 ${c.total}</span>
      <span style="color:var(--green)">通过 ${k.通过}</span>
      <span style="color:var(--red)">有问题 ${k.有问题}</span>
      <span>未检 ${k.未检}</span>
    </div>
    ${e.ok ? `<div class="meta" style="color:var(--green)">✓ 全部通过，可以归档</div>`
      : `<div class="warn">✗ ${esc(e.reason || "不可归档")}</div>`}
    <div class="card-actions">
      ${e.ok ? `<label><input type="checkbox" data-c="${esc(c.name)}" ${archSelected.has(c.name) ? "checked" : ""}> 选中归档</label>`
        : `<button class="btn small" data-goto="${esc(c.name)}">去处理 →</button>`}
    </div>
  </div>`;
}

function renderLedger(entries) {
  $("#ledger-count").textContent = `共 ${entries.length} 条`;
  if (!entries.length) { $("#ledger-table").innerHTML = `<tr><td class="empty">暂无归档记录</td></tr>`; return; }
  $("#ledger-table").innerHTML =
    `<thead><tr><th>公司</th><th>产品数</th><th>归档时间</th><th>去向</th><th>状态</th></tr></thead><tbody>` +
    entries.map(e => `<tr>
      <td>${esc(e.company)}</td><td>${e.total || 0}</td><td>${esc(e.time || "")}</td>
      <td class="ellipsis" style="max-width:340px" title="${esc(e.target || "")}">${esc(e.target || "")}</td>
      <td>${e.conflict ? `<span style="color:var(--red)">⚠ 库内又有同名文件夹，两份数据各自漂移</span>`
        : e.target_exists ? `<span style="color:var(--green)">正常</span>`
        : `<span style="color:var(--amber)">目录已不在原处</span>`}</td>
    </tr>`).join("") + `</tbody>`;
}

async function doArchive(btn) {
  const names = [...archSelected];
  if (!names.length) return toast("请先勾选要归档的公司");
  const target = $("#archive-path").value.trim();
  const ok = await confirmModal("确认归档",
    `<p>将把这 <b>${names.length}</b> 家公司的<b>整个文件夹移动</b>到：</p>
     <p style="color:var(--blue);word-break:break-all">${esc(target || "（默认归档路径）")}</p>
     <ul style="margin:8px 0 8px 20px">${names.map(n => `<li>${esc(n)}</li>`).join("")}</ul>
     <p class="muted">源目录会被清空；审核记录原地保留，之后可在归档页「撤回归档」搬回来。</p>`,
    `确认归档 ${names.length} 家`);
  if (!ok) return;
  await busy(btn, async () => {
    try {
      const r = await api("/api/archive", { method: "POST", json: { companies: names, target } });
      const bad = r.results.filter(x => !x.ok);
      toast(bad.length ? `已归档 ${r.moved} 家，${bad.length} 家失败：${bad[0].company} — ${bad[0].msg}`
        : `✔ 已归档 ${r.moved} 家公司`, bad.length ? 10000 : 4000);
      archSelected.clear();
      await loadArchive();
      await loadCompanies();
    } catch (e) { toast("✘ 归档失败: " + e.message, 8000); }
  });
}

async function revoke(company) {
  const ok = await confirmModal("撤回归档",
    `<p>将把 <b>${esc(company)}</b> 的文件夹从归档路径<b>搬回产品数据目录</b>，并从台账移除。</p>
     <p class="muted">这是修改已归档数据的唯一正规入口。</p>`, "确认撤回");
  if (!ok) return;
  try {
    await api("/api/archive/revoke", { method: "POST", json: { company } });
    toast(`✔ ${company} 已撤回，可以重新审核修改`, 4000);
    await loadArchive();
    await loadCompanies();
  } catch (e) { toast("✘ 撤回失败: " + e.message, 8000); }
}
