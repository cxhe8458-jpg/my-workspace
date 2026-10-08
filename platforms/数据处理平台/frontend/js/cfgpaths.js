/* 设置页的「流水线目录」与「AI 研判」—— 从已删除的流水线页搬来。

   这些是设一次就不再看的东西，放在处理页面上只会占地方；更糟的是以前 AI 模型/线程/开关
   写在 ②映射 面板里，而「连续执行」在背后偷读它们的值 —— 用户站在另一个面板上，
   看不见也改不了，却不知道自己在用哪个模型、AI 开没开。现在它们有了自己的家。 */

const PKEYS = ["input", "mapping_out", "unmatched", "process", "clean_out",
               "final_out", "company_id", "product_id"];

function pCollect() {
  const cfg = {};
  PKEYS.forEach(k => cfg[k] = document.getElementById("p-" + k).value.trim());
  cfg.model_id = document.getElementById("p-model").value;
  cfg.use_ai = document.getElementById("p-useai").checked;
  cfg.threads = parseInt(document.getElementById("p-threads").value) || 1;
  cfg.ai_workers = parseInt(document.getElementById("p-ai_workers").value) || 8;
  return cfg;
}

/* 摘要芯片：一眼看出"现在用的是哪个模型、AI 到底开没开" */
function pAiSummary() {
  const el = document.getElementById("p-aisum");
  const on = document.getElementById("p-useai").checked;
  const sel = document.getElementById("p-model");
  const name = sel.selectedOptions[0] ? sel.selectedOptions[0].textContent : "";
  el.className = "chip " + (on ? (sel.value ? "ai" : "err") : "idle");
  el.innerHTML = on
    ? (sel.value ? MK.done + esc(name) : MK.warn + "已启用，但没有可用模型")
    : MK.idle + "已关闭，只用关键词规则";
}

async function pSave() {
  try {
    await api("/api/pipeline/config", { method: "POST", body: pCollect() });
    pAiSummary();
    pIdState();
  } catch (e) { toast(e.message, "err"); }
}

/* id 表是否齐备：缺表时说清楚，⑤ id 映射会跑不了 */
async function pIdState() {
  const el = document.getElementById("p-idstate");
  try {
    const d = await api("/api/idmap/status");
    el.innerHTML = [["company_id", "公司id.json"], ["product_id", "产品id.json"]].map(([k, label]) => {
      const x = d.detail[k] || {};
      return x.ok ? chip("ok", "done", `${label} ${x.total} 条 · ${x.usable} 个可查键`)
                  : chip("err", "warn", `${label} 缺失或无法解析`);
    }).join(" ") + (d.ready ? "" : " — 补齐 id 表后，总览上的 ⑤ id 映射才可用");
  } catch (e) { el.innerHTML = ""; }
}

(async () => {
  let cfg = {};
  try { cfg = await api("/api/pipeline/config"); }
  catch (e) { return toast("读不到流水线配置：" + e.message, "err"); }
  PKEYS.forEach(k => { const el = document.getElementById("p-" + k); if (el) el.value = cfg[k] || ""; });
  document.getElementById("p-threads").value = cfg.threads || 1;
  document.getElementById("p-ai_workers").value = cfg.ai_workers || 8;
  document.getElementById("p-useai").checked = cfg.use_ai !== false;

  const d = await api("/api/models").catch(() => ({ models: [] }));
  const sel = document.getElementById("p-model");
  sel.innerHTML = d.models.length
    ? d.models.map(m => `<option value="${m.id}" ${m.id === cfg.model_id ? "selected" : ""}>${esc(m.name)}（${esc(m.model)}）</option>`).join("")
    : `<option value="">（还没有任务模型 — 在下面建一个）</option>`;

  PKEYS.map(k => "p-" + k).concat(["p-model", "p-threads", "p-ai_workers", "p-useai"])
    .forEach(id => document.getElementById(id).addEventListener("change", pSave));
  pAiSummary();
  pIdState();
})();
