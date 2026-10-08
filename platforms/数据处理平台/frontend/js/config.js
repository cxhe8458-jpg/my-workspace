let providers = [], models = [], curId = null, skillsAll = [];

async function load() {
  try {
    providers = await api("/api/providers");
  } catch (e) { return toast("读不到供应商列表：" + e.message, "err"); }
  const sel = document.getElementById("f-provider");
  sel.innerHTML = providers.map(p => `<option value="${p.key}">${esc(p.name)}</option>`).join("");
  sel.onchange = () => {
    const p = providers.find(x => x.key === sel.value);
    document.getElementById("f-base").value = p.base_url;
    document.getElementById("f-model").value = p.default_model;
  };
  await loadSkills();
  await refresh();
  sel.onchange();
}

async function loadSkills() {
  skillsAll = await api("/api/skills");
  const box = document.getElementById("f-skills");
  // 「查看」不再用内联 onclick 拼字符串：文件名里只要有个单引号就会把它拼坏
  box.innerHTML = skillsAll.map(s =>
    `<label><input type="checkbox" value="${esc(s.file)}"> ${esc(s.file)}
     <span style="color:var(--muted)">(${(s.size/1024).toFixed(1)} KB)</span>
     <a href="#" data-skill="${esc(s.file)}">查看</a></label>`).join("");
  box.querySelectorAll("[data-skill]").forEach(a => a.onclick = e => {
    e.preventDefault();
    viewSkill(a.dataset.skill).catch(err => toast(err.message, "err"));
  });
}

async function viewSkill(f) {
  const txt = await api("/api/skills/" + encodeURIComponent(f));
  openModal(f, mdRender(txt));
}

async function refresh() {
  const d = await api("/api/models");
  models = d.models;
  document.getElementById("cnt").textContent = models.length;
  const g = document.getElementById("slots");
  g.innerHTML = models.map(m => `
    <div class="slot ${m.id === curId ? "sel" : ""}" data-edit="${esc(m.id)}">
      <div class="name">${esc(m.name)}</div>
      <div class="meta">${esc(m.model)}<br>${esc(m.base_url)}</div>
      <div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap">
        ${(m.skills||[]).map(s => `<span class="chip ai">${esc(s)}</span>`).join("")}
        ${m.api_key_set ? chip("ok", "done", "已存 Key") : chip("err", "warn", "缺 Key")}</div>
    </div>`).join("") +
    (models.length < 5 ? `<div class="slot empty" data-blank>＋ 新建任务模型</div>` : "");
  g.querySelectorAll("[data-edit]").forEach(el => el.onclick = () => edit(el.dataset.edit));
  const nb = g.querySelector("[data-blank]");
  if (nb) nb.onclick = blank;
}

function edit(id) {
  const m = models.find(x => x.id === id);
  if (!m) return;
  curId = id;
  document.getElementById("editing").textContent = "编辑：" + m.name;
  document.getElementById("f-name").value = m.name;
  document.getElementById("f-provider").value = m.provider || "custom";
  document.getElementById("f-base").value = m.base_url;
  document.getElementById("f-model").value = m.model;
  document.getElementById("f-temp").value = m.temperature ?? 0.2;
  document.getElementById("f-key").value = "";
  document.getElementById("f-key").placeholder = m.api_key_set ? `已保存 (${m.api_key})，留空保留` : "sk-...";
  document.getElementById("f-role").value = m.role || "";
  document.getElementById("f-prompt").value = m.prompt || "";
  document.querySelectorAll("#f-skills input").forEach(c => c.checked = (m.skills||[]).includes(c.value));
  document.getElementById("btn-test").disabled = false;
  document.getElementById("btn-test").textContent = "测试连通";
  document.getElementById("btn-del").disabled = false;
  refresh();
}

function blank() {
  curId = null;
  document.getElementById("editing").textContent = "新建";
  ["f-name","f-base","f-model","f-key","f-role","f-prompt"].forEach(i => document.getElementById(i).value = "");
  document.getElementById("f-temp").value = 0.2;
  document.querySelectorAll("#f-skills input").forEach(c => c.checked = false);
  document.getElementById("btn-test").disabled = true;
  document.getElementById("btn-del").disabled = true;
  document.getElementById("f-provider").onchange();
  refresh();
}

document.getElementById("btn-new").onclick = blank;

document.getElementById("btn-save").onclick = async () => {
  const prov = providers.find(p => p.key === document.getElementById("f-provider").value);
  const payload = {
    id: curId,
    name: document.getElementById("f-name").value.trim(),
    provider: prov.key, api: prov.api,
    base_url: document.getElementById("f-base").value.trim(),
    api_key: document.getElementById("f-key").value.trim(),
    model: document.getElementById("f-model").value.trim(),
    temperature: parseFloat(document.getElementById("f-temp").value) || 0.2,
    role: document.getElementById("f-role").value,
    prompt: document.getElementById("f-prompt").value,
    skills: [...document.querySelectorAll("#f-skills input:checked")].map(c => c.value),
  };
  if (!payload.name || !payload.base_url || !payload.model) return toast("名称 / Base URL / 模型名 为必填", "err");
  try {
    const r = await api("/api/models", { method: "POST", body: payload });
    curId = r.id;
    toast("已保存", "ok");
    await refresh(); edit(curId);
  } catch (e) { toast(e.message, "err"); }
};

document.getElementById("btn-del").onclick = async () => {
  if (!curId) return;
  const ok = await confirmBox("删除任务模型", `
    <p>删掉这个任务模型，它的 API Key 也一并删除。</p>
    <p class="hint">正在用它的页面需要重新选一个模型。</p>`, { okText: "删除", danger: true });
  if (!ok) return;
  await api("/api/models/" + curId, { method: "DELETE" });
  toast("已删除", "ok"); blank();
};

document.getElementById("btn-test").onclick = async () => {
  const btn = document.getElementById("btn-test"), out = document.getElementById("test-out");
  btn.disabled = true; btn.textContent = "测试中…"; out.textContent = "";
  try {
    const r = await api(`/api/models/${curId}/test`, { method: "POST" });
    out.innerHTML = r.ok ? chip("ok", "done", "连通正常") + ` 回复：${esc(r.reply)}`
                         : chip("err", "warn", "连通失败") + ` ${esc(r.error)}`;
  } catch (e) { out.innerHTML = chip("err", "warn", "连通失败") + ` ${esc(e.message)}`; }
  btn.disabled = false; btn.textContent = "测试连通";
};

document.getElementById("f-upload").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  const r = await fetch("/api/skills/upload", { method: "POST", body: fd });
  if (r.ok) { toast("Skill 已上传", "ok"); await loadSkills(); }
  else toast("上传失败", "err");
  e.target.value = "";
};

load();
