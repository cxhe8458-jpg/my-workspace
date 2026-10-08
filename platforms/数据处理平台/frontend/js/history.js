const STEP_NAME = { mapping: "② 映射", consolidate: "③ 分类+清洗", audit: "④ 复查",
                    idmap: "⑤ id 映射", idrename: "ID 替换（旧）", pipeline: "全流程",
                    process: "流水线", manual: "人工匹配", review: "AI 研判复核",
                    category: "新增分类", classify: "③ 分类（旧）", clean: "④ 清洗（旧）" };
let items = [];

async function load() {
  items = await api("/api/history");
  document.getElementById("h-count").textContent = items.length;
  document.querySelector("#h-tbl tbody").innerHTML = items.map(x => {
    const comps = (x.companies || []);
    const compTxt = comps.length > 3 ? esc(comps.slice(0, 3).join("、")) + ` 等${comps.length}家` : esc(comps.join("、")) || "—";
    const stats = Object.entries(x.stats || {}).map(([k, v]) => `${esc(k)}:${esc(v)}`).join("　") || "—";
    const paths = Object.entries(x.paths || {}).map(([k, v]) => `${esc(k)}: <code>${esc(v)}</code>`).join("<br>") || "—";
    return `<tr><td><input type="checkbox" class="h-ck" value="${x.id}"></td>
      <td style="white-space:nowrap">${esc(x.time)}</td>
      <td style="white-space:nowrap">${STEP_NAME[x.step] || esc(x.step)}</td>
      <td>${esc(x.title)}</td><td title="${esc(comps.join("、"))}">${compTxt}</td>
      <td>${stats}</td><td style="font-size:11.5px">${paths}</td>
      <td>${x.ok ? chip("ok", "done", "通过") : chip("err", "warn", "未通过")}</td></tr>`;
  }).join("") || `<tr><td colspan="8" class="empty"><b>还没有处理记录</b>跑一次流水线之后，每次处理都会记在这里。</td></tr>`;
  document.getElementById("h-ckall").checked = false;
}

document.getElementById("h-refresh").onclick = load;
document.getElementById("h-ckall").onchange = e =>
  document.querySelectorAll(".h-ck").forEach(c => c.checked = e.target.checked);

async function del(ids) {
  if (!ids.length) return toast("先勾选要删的记录", "err");
  const ok = await confirmBox("删除历史记录", `
    <p>删掉 <b>${ids.length}</b> 条处理记录。</p>
    <p class="hint">只删记录本身，磁盘上已经产出的数据不受影响。</p>`,
    { okText: "删除", danger: true });
  if (!ok) return;
  const r = await api("/api/history/delete", { method: "POST", body: { ids } });
  toast(`已删除 ${r.deleted} 条`, "ok");
  load();
}
document.getElementById("h-delsel").onclick = () =>
  del([...document.querySelectorAll(".h-ck:checked")].map(c => c.value));
document.getElementById("h-delall").onclick = () => del(items.map(x => x.id));

load();
