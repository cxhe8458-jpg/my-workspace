/* 总览页 —— 现在是干活的地方，不只是看板。

   设计前提：**工作单元是公司，不是步骤**。
   矩阵已经回答了"每家公司走到哪"，那么动作就该挂在公司上，而不是让人先把
   "这家公司还差什么"翻译成"我该去点第几步的面板"——那层翻译正是混淆的来源。
   所以每一行都算出一个「下一步」，点了就执行；档位上的「一键」＝ 把这一档所有公司的
   下一步批量做掉。两者共用 nextAction()，不维护两份判定。

   任务串行排队（后端 manager.submit），所以可以把几个档位连着点完再去干别的。 */

/* 默认只看"要处理的"：走完全程的公司不需要占地方，全部走完时表格自然是空的 */
const FLT0 = new URLSearchParams(location.search).get("f") || "todo";
let OV = null, CFG = null, flt = FLT0, sortK = "stage", sortD = 1, q = "";

/* ── 状态编码 ─────────────────────────────────────────────────────────── */
const STAGE_LABEL = {
  map:   { done: "已映射", stale: "源数据有更新", none: "未映射" },
  clean: { done: "已清洗", stale: "需重跑清洗", none: "未清洗" },
  audit: { done: "复查通过", failed: "复查有损失", stale: "需重查", none: "未复查" },
  idmap: { done: "已 id 映射", todo: "待 id 映射", noid: "无 id", none: "未处理" },
};
/* 进度列：四个小方块，实心=完成、半实=有更新/需重跑、空心=没做、三角=出问题。
   收成一格是因为有了「下一步」列之后，这四列的作用从"做决定"退成"看过程"。 */
const STAGE_CLS = { done: "s-done", stale: "s-stale", failed: "s-bad", noid: "s-bad",
                    todo: "s-idle", none: "s-idle" };
const STAGES = [["map", "②"], ["clean", "③"], ["audit", "④"], ["idmap", "⑤"]];

function stageCell(r) {
  return `<span class="stagebar">` + STAGES.map(([k, n]) => {
    const st = r[k] || "none";
    return `<i class="${STAGE_CLS[st] || "s-idle"}" data-col="${k}"
              title="${n} ${esc((STAGE_LABEL[k] || {})[st] || st)}">${n}</i>`;
  }).join("") + `</span>`;
}

const todoOf = r => r.unmatched + r.review_pending;
const needsMap = r => !!r.in_input && r.map !== "done";
const needsMe = r => todoOf(r) > 0 || needsMap(r) || r.clean === "stale"
                     || r.idmap === "noid" || r.audit === "failed" || r.audit === "stale";

/* ── 下一步：全页唯一的判定 ────────────────────────────────────────────── */
const ACT = {
  variant: { t: "先处理同名变体", d: "两个目录会解析成同一个公司 id，跑下去后写的会覆盖先写的", bulk: 0 },
  pipeline:{ t: "跑全流程 ②→⑤", d: "新公司：映射 → 分类清洗 → 复查 → id 映射", bulk: 1 },
  remap:   { t: "从 ② 重跑",     d: "输入目录里的源数据变过了", bulk: 1 },
  reclean: { t: "从 ③ 重跑",     d: "映射输出变过（人工匹配/复核改过），要重新清洗", bulk: 1 },
  audit:   { t: "跑 ④ 复查",     d: "核对产品数 / PDF 误删 / 页数损失，三项必须为 0", bulk: 1 },
  idmap:   { t: "跑 ⑤ id 映射",  d: "公司名换 id、两级分类压成一层", bulk: 1 },
  noid:    { t: "填公司 id",     d: "id 表里查不到这个目录名，填一个存进别名表", bulk: 0 },
  manual:  { t: "去人工匹配",    d: "规则和 AI 都没命中的产品要你定分类", bulk: 0 },
  review:  { t: "去复核研判",    d: "AI 研判过的产品要你确认对不对", bulk: 0 },
  done:    { t: "", d: "", bulk: 0 },
  nosrc:   { t: "", d: "输入目录里没有这家公司的源数据，只在未匹配/过程记录里露过面", bulk: 0 },
};

/* 顺序即优先级：能自动跑的先跑完，人工的留到最后（待人工那一列另有入口）。

   ⚠ 「走完全程」与「无源可处理」必须分开：一家只在 _未匹配 或过程记录里露过面、
   输入目录和映射输出都没有它的公司，跑不了任何一步，但那不等于它做完了 ——
   把它标成"走完全程"会让终态统计骗人。 */
function nextAction(r) {
  if (r.variant_of) return "variant";
  if (needsMap(r)) return r.map === "stale" ? "remap" : "pipeline";
  if (r.map === "done") {
    if (r.clean !== "done") return "reclean";
    if (r.audit !== "done") return "audit";
    if (r.idmap === "noid") return "noid";
    if (r.idmap !== "done") return "idmap";
  }
  if (r.unmatched) return "manual";
  if (r.review_pending) return "review";
  return r.map === "done" ? "done" : "nosrc";
}

const JUMP = {
  unmatched: n => `todo.html?tab=manual&company=${encodeURIComponent(n)}`,
  review: n => `todo.html?tab=review&company=${encodeURIComponent(n)}`,
};

/* ── 执行 ─────────────────────────────────────────────────────────────── */
const advFlags = () => ({
  remap_all: !!document.getElementById("adv-remap").checked,
  reclean_all: !!document.getElementById("adv-reclean").checked,
});

/* 每种动作跑什么。步骤组合写在这一处，界面上的单行按钮和批量按钮共用。 */
async function runAction(kind, companies) {
  const a = advFlags();
  const base = { companies, model_id: CFG.model_id || null, use_ai: CFG.use_ai !== false,
                 threads: CFG.threads || 1, paths: {}, reclean_all: a.reclean_all };
  if (kind === "pipeline" || kind === "remap")
    return api("/api/process/run", { method: "POST", body:
      { ...base, do_map: true, do_clean: true, do_audit: true, do_idmap: true } });
  if (kind === "reclean")
    return api("/api/process/run", { method: "POST", body:
      { ...base, do_map: false, do_clean: true, do_audit: true, do_idmap: true } });
  if (kind === "audit")
    return api("/api/audit/run", { method: "POST", body: { companies } });
  if (kind === "idmap")
    return api("/api/idmap/run", { method: "POST", body: { companies, apply: true } });
  throw new Error("这个动作不需要跑任务");
}

/* 每个动作实际会跑哪几步（与后端 _meta 的 steps 同一套码）。 */
const STEPS_OF = {
  pipeline: ["map", "clean", "audit", "idmap"],
  remap:    ["map", "clean", "audit", "idmap"],
  reclean:  ["clean", "audit", "idmap"],
  audit:    ["audit"],
  idmap:    ["idmap"],
};
const STEP_CN = { map: "②映射", clean: "③分类+清洗", audit: "④复查", idmap: "⑤id映射" };

/* 提交前看一眼队列：要跑的这几步、这几家公司，是不是已经有任务在管了。

   队列是严格串行的，排在一个跑几小时的任务后面就是干等。上次正是这样——
   一个已经勾了 ⑤ 的任务在跑，用户又单独排了一个 ⑤，白等三个多小时。
   这里不拦，只把话说清楚，要不要排还是你定。 */
async function dupWarn(kind, companies) {
  const want = STEPS_OF[kind];
  if (!want) return "";
  let d;
  try { d = await api("/api/queue"); } catch { return ""; }
  const cs = new Set(companies);
  const hits = [];
  [...(d.running || []), ...(d.queued || [])].forEach(j => {
    const m = j.meta || {}, st = m.steps || [];
    const overlap = want.filter(s => st.includes(s));
    if (!overlap.length) return;
    const mc = m.companies || [];
    const same = mc.length ? mc.filter(c => cs.has(c)) : [...cs];   // 空 = 全量，必然覆盖
    if (same.length) hits.push({ j, overlap, same, all: !mc.length });
  });
  return hits.map(h => `
    <div class="note" style="margin-top:10px;border-left:3px solid var(--warn)">
      ⚠ <b>${esc(h.j.title)}</b>（${h.j.status === "running" ? "正在跑" : `排队第 ${h.j.position} 位`}）
      已经包含 <b>${h.overlap.map(s => STEP_CN[s]).join("、")}</b>，
      并覆盖${h.all ? "全部公司" : `其中 <b>${h.same.length}</b> 家`}。
      队列是串行的，再排一个只会排在它后面等着。
    </div>`).join("");
}

async function doAction(kind, companies, btn) {
  if (kind === "noid") { document.getElementById("ov-idbox").open = true;
    document.getElementById("ov-idbox").scrollIntoView({ behavior: "smooth" }); return; }
  if (kind === "variant") { document.getElementById("variants")
    .scrollIntoView({ behavior: "smooth", block: "start" }); return; }
  if (kind === "manual" || kind === "review") {
    location.href = (kind === "manual" ? JUMP.unmatched : JUMP.review)(companies[0]); return; }

  const a = advFlags();
  const n = companies.length;
  const dup = await dupWarn(kind, companies);
  const ok = await confirmBox(ACT[kind].t, `
    <p>对 <b>${n}</b> 家公司执行：<b>${ACT[kind].t}</b></p>
    <div class="note" style="margin-top:10px">${esc(ACT[kind].d)}<br>
      ${n <= 8 ? companies.map(esc).join("、")
               : companies.slice(0, 8).map(esc).join("、") + ` 等 ${n} 家`}<br>
      ${a.remap_all || a.reclean_all
        ? `<b>已开启强制重做：${[a.remap_all && "映射", a.reclean_all && "清洗"].filter(Boolean).join(" + ")}</b><br>` : ""}
      ${kind === "pipeline" || kind === "remap"
        ? `AI 研判：${CFG.use_ai !== false ? "开" : "关"} · 公司级线程 ${CFG.threads || 1}
           · <b>AI 产品级并发 ${CFG.ai_workers || 8}</b><br>` : ""}
      全程只以复制产出，原始数据零改动。任务会排进队列依次执行。
    </div>${dup}`, { okText: "开始执行" });
  if (!ok) return;
  if (btn) btn.disabled = true;
  try {
    await runAction(kind, companies);
    toast(`已排入队列：${ACT[kind].t} · ${n} 家`, "ok");
    pollQueue();
  } catch (e) { toast(e.message, "err"); }
  finally { if (btn) btn.disabled = false; }
}

/* ── 队列 ─────────────────────────────────────────────────────────────────
   进度条只说"跑到哪了"，出问题时你要看的是**日志**。所以每个在跑的任务都带一个
   可展开的日志区，增量拉取（log_from 游标），展开状态在重绘之间保持住。 */
let qTimer = null;
const qLogs = {};          // {jobId: {from, text, open}}

function qLogState(id) {
  if (!qLogs[id]) qLogs[id] = { from: 0, text: "", open: false };
  return qLogs[id];
}

async function pullLog(id) {
  const st = qLogState(id);
  try {
    const j = await api(`/api/jobs/${id}?log_from=${st.from}`);
    if (j.logs && j.logs.length) { st.text += j.logs.join("\n") + "\n"; st.from = j.log_next; }
    return j;
  } catch { return null; }
}

/* 任务状态的三重编码（形状 + 文字 + 颜色），与全站 chip() 口径一致 */
const JST = {
  running:     { c: "ai",   m: "stale", t: "运行中" },
  queued:      { c: "idle", m: "idle",  t: "排队中" },
  done:        { c: "ok",   m: "done",  t: "已完成" },
  error:       { c: "err",  m: "warn",  t: "失败" },
  cancelled:   { c: "warn", m: "warn",  t: "已取消" },
  interrupted: { c: "warn", m: "warn",  t: "中断（平台重启）" },
};
const hhmm = ts => { const d = new Date(ts * 1000), p = n => String(n).padStart(2, "0");
                     return `${p(d.getHours())}:${p(d.getMinutes())}`; };
const dur = s => !s ? "" : s < 60 ? `${s|0} 秒`
              : s < 3600 ? `${s/60|0} 分 ${s%60|0} 秒` : `${s/3600|0} 小时 ${(s%3600)/60|0} 分`;

/* mode: run（在跑） | wait（排队） | done（已结束，留在原地供查阅） */
function qRow(j, mode) {
  const st = qLogState(j.id);
  const s = JST[j.status] || JST.done;
  const chipEl = `<span class="chip ${s.c}">${MK[s.m]}${s.t}${
    mode === "wait" && j.position ? ` ${j.position}` : ""}</span>`;
  const logBtn = `<button class="ghost small" data-log="${j.id}">${ic("chev", 12)}${
    st.open ? "收起日志" : "查看日志"}</button>`;
  const dlBtn = `<button class="ghost small" data-dl="${j.id}">${ic("download", 12)}下载日志</button>`;

  if (mode === "wait") {
    return `<div class="qrow" data-job="${j.id}">${chipEl}
      <b style="color:var(--muted)">${esc(j.title)}</b><span style="flex:1"></span>
      <button class="ghost small" data-cancel="${j.id}">${ic("x", 12)}取消</button></div>`;
  }
  if (mode === "run") {
    /* ETA 是这次故障最缺的一样东西：进度条三小时从 17% 爬到 57%，
       没有"还要多久"，没人判断得出该继续等还是该砍掉。 */
    return `
      <div class="qrow" data-job="${j.id}">${chipEl}<b>${esc(j.title)}</b>
        <div class="progress" style="flex:1"><div style="width:${j.progress}%"></div></div>
        <span class="mono" data-pct="${j.id}"
              style="font-size:12px;color:var(--muted);white-space:nowrap">${pctText(j)}</span>
        ${logBtn}<button class="ghost small" data-cancel="${j.id}">${ic("x", 12)}取消</button>
      </div>
      <div class="qcur mono" data-cur="${j.id}">${esc(j.current || "准备中…")}</div>
      <div class="joblog" data-logbox="${j.id}" ${st.open ? "" : "hidden"}>${esc(st.text)}</div>`;
  }
  // done —— 结束的任务不再无声消失，原地留一张卡片，日志和计数器都还能看
  const kv = Object.entries(j.stats || {}).map(([k, v]) =>
    `<span class="lstat"><b>${esc(v)}</b>${esc(k)}</span>`).join("");
  return `
    <div class="qrow" data-job="${j.id}">${chipEl}<b>${esc(j.title)}</b>
      <span class="mono" style="font-size:12px;color:var(--muted);white-space:nowrap">
        ${j.total ? `${fmt(j.done)}/${fmt(j.total)} · ` : ""}${
          j.elapsed ? `用时 ${dur(j.elapsed)} · ` : ""}${
          j.finished ? `${hhmm(j.finished)} 结束` : ""}</span>
      <span style="flex:1"></span>${logBtn}${dlBtn}
      <button class="ghost small" data-close="${j.id}">${ic("x", 12)}关闭</button>
    </div>
    ${j.error ? `<div class="qcur mono" style="color:var(--err)">${esc(j.error)}</div>` : ""}
    ${kv ? `<div class="livestats">${kv}</div>` : ""}
    <div class="joblog" data-logbox="${j.id}" ${st.open ? "" : "hidden"}>${esc(st.text)}</div>`;
}

const pctText = j => `${j.progress}% · ${fmt(j.done)}/${fmt(j.total)}`
                     + (j.eta_text ? ` · 还需约 ${j.eta_text}` : "");

/* 就地更新在跑那一行的进度/ETA/当前条目 —— 不碰日志框，也就不会打断你的滚动。 */
function patchRun(box, j) {
  const bar = box.querySelector(`.qrow[data-job="${j.id}"] .progress>div`);
  if (bar) bar.style.width = j.progress + "%";
  const pct = box.querySelector(`[data-pct="${j.id}"]`);
  if (pct) pct.textContent = pctText(j);
  const cur = box.querySelector(`[data-cur="${j.id}"]`);
  if (cur) cur.textContent = j.current || "准备中…";
}

/* 追加日志文本。**只有本来就贴着底部时才继续跟随** ——
   你往上翻去看某一条时，不能每 1.2 秒把滚动条抢回底部。 */
function patchLog(box, j) {
  const el = box.querySelector(`[data-logbox="${j.id}"]`);
  if (!el) return;
  const t = qLogState(j.id).text;
  if (el.textContent === t) return;
  const stick = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  el.textContent = t;
  if (stick) el.scrollTop = el.scrollHeight;
}

function bindQ(box) {
  box.querySelectorAll("[data-log]").forEach(b => b.onclick = () => {
    const st = qLogState(b.dataset.log);
    st.open = !st.open;
    const el = box.querySelector(`[data-logbox="${b.dataset.log}"]`);
    el.hidden = !st.open;
    b.innerHTML = ic("chev", 12) + (st.open ? "收起日志" : "查看日志");
    if (st.open) el.scrollTop = el.scrollHeight;   // 主动展开时才跳到底
  });
  box.querySelectorAll("[data-dl]").forEach(b => b.onclick = () => {
    location.href = `/api/jobs/${b.dataset.dl}/log`;      // 完整日志，不受内存窗口限制
  });
  box.querySelectorAll("[data-cancel]").forEach(b => b.onclick = async () => {
    b.disabled = true;
    try { await api(`/api/jobs/${b.dataset.cancel}/cancel`, { method: "POST" });
          toast("已发出取消，任务会停在最近一个安全点", "ok"); }
    catch (e) { toast(e.message, "err"); b.disabled = false; }
  });
  box.querySelectorAll("[data-close]").forEach(b => b.onclick = async () => {
    b.disabled = true;
    try { await api(`/api/jobs/${b.dataset.close}/dismiss`, { method: "POST" }); pollQueue(); }
    catch (e) { toast(e.message, "err"); b.disabled = false; }
  });
}

/* ── 任务日志（档案馆）─────────────────────────────────────────────────────
   队列区只放"刚发生的"（在跑 / 排队 / 2 小时内结束的），再早的都在这里 ——
   包括平台重启之前的，因为日志现在落在 data/jobs/<id>.log。
   能看、能下载、能删；列表本身可滚动，不会把首屏顶掉。 */
let LOGS = [], lgKind = localStorage.getItem("ov:lgkind") || "";
const KIND_CN = { process: "数据处理", pipeline: "一键全流程", consolidate: "分类+清洗",
                  audit: "复查", idmap: "id 映射", overview: "总览扫描",
                  dist: "类别分布扫描", export: "统计导出", purge: "移除公司" };

async function loadLogs(quiet) {
  try {
    const d = await api("/api/jobs?limit=80");
    LOGS = d.jobs || [];
  } catch (e) {
    if (!quiet) toast("读不到任务日志：" + e.message, "err");
    return;
  }
  const live = LOGS.filter(j => j.status === "running" || j.status === "queued").length;
  document.getElementById("lg-sum").textContent =
    `${LOGS.length} 条记录` + (live ? ` · ${live} 个在跑` : "")
    + (LOGS[0] ? ` · 最近 ${hhmm(LOGS[0].finished || LOGS[0].started || LOGS[0].created)}` : "");
  drawLogSeg();
  drawLogs();
}

function drawLogSeg() {
  const kinds = [...new Set(LOGS.map(j => j.kind))];
  document.getElementById("lg-seg").innerHTML =
    [["", "全部"], ...kinds.map(k => [k, KIND_CN[k] || k])]
      .map(([k, l]) => `<button data-k="${k}" class="${k === lgKind ? "on" : ""}">${esc(l)}${
        k ? ` ${LOGS.filter(j => j.kind === k).length}` : ` ${LOGS.length}`}</button>`).join("");
  document.getElementById("lg-seg").onclick = e => {
    const b = e.target.closest("button"); if (!b) return;
    lgKind = b.dataset.k; localStorage.setItem("ov:lgkind", lgKind);
    drawLogSeg(); drawLogs();
  };
}

function drawLogs() {
  const rs = lgKind ? LOGS.filter(j => j.kind === lgKind) : LOGS;
  document.getElementById("lg-count").textContent = `${rs.length} 条`;
  const box = document.getElementById("lg-list");
  if (!rs.length) {
    box.innerHTML = `<div class="empty">还没有任务记录。</div>`;
    return;
  }
  box.innerHTML = rs.map(j => {
    const s = JST[j.status] || JST.done;
    return `
    <div class="lgrow" data-job="${j.id}">
      <span class="chip ${s.c}">${MK[s.m]}${s.t}</span>
      <span class="lgk mono">${esc(KIND_CN[j.kind] || j.kind)}</span>
      <b class="lgt">${esc(j.title)}</b>
      <span class="mono lgm">${j.total ? `${fmt(j.done)}/${fmt(j.total)} · ` : ""}${
        j.elapsed ? `${dur(j.elapsed)} · ` : ""}${
        j.finished ? hhmm(j.finished) : (j.started ? hhmm(j.started) : "")}</span>
      <button class="ghost small" data-lgview="${j.id}">${ic("chev", 12)}查看</button>
      <button class="ghost small" data-lgdl="${j.id}">${ic("download", 12)}下载</button>
      <button class="ghost small" data-lgdel="${j.id}" ${
        j.status === "running" || j.status === "queued" ? "disabled" : ""}>${ic("trash", 12)}删除</button>
    </div>
    ${j.error ? `<div class="lgerr mono">${esc(j.error)}</div>` : ""}
    <div class="joblog" data-lgbox="${j.id}" hidden></div>`;
  }).join("");

  box.querySelectorAll("[data-lgview]").forEach(b => b.onclick = async () => {
    const id = b.dataset.lgview;
    const el = box.querySelector(`[data-lgbox="${id}"]`);
    if (!el.hidden) { el.hidden = true; b.innerHTML = ic("chev", 12) + "查看"; return; }
    if (!el.textContent) {
      el.hidden = false; el.textContent = "读取中…";
      try {
        const j = await api(`/api/jobs/${id}?log_from=0`);
        el.textContent = (j.logs || []).join("\n") || "（这个任务没有日志）";
      } catch (e) { el.textContent = "读不到日志：" + e.message; }
    }
    el.hidden = false;
    el.scrollTop = el.scrollHeight;
    b.innerHTML = ic("chev", 12) + "收起";
  });
  box.querySelectorAll("[data-lgdl]").forEach(b => b.onclick = () => {
    location.href = `/api/jobs/${b.dataset.lgdl}/log`;
  });
  box.querySelectorAll("[data-lgdel]").forEach(b => b.onclick = async () => {
    const j = LOGS.find(x => x.id === b.dataset.lgdel);
    const ok = await confirmBox("删除这条任务记录", `
      <p>删除「<b>${esc(j.title)}</b>」的运行记录与日志文件。</p>
      <div class="note" style="margin-top:10px">只删记录，<b>不动任何产品数据</b>。
        删掉之后这次运行的日志就找不回来了。</div>`, { okText: "删除", danger: true });
    if (!ok) return;
    try {
      await api(`/api/jobs/${b.dataset.lgdel}`, { method: "DELETE" });
      toast("已删除", "ok"); await loadLogs(true);
    } catch (e) { toast(e.message, "err"); }
  });
}

document.getElementById("lg-reload").onclick = () => loadLogs();
document.getElementById("lg-purge").onclick = async () => {
  const n = LOGS.filter(j => j.status !== "running" && j.status !== "queued").length;
  if (!n) return toast("没有已结束的任务记录");
  const ok = await confirmBox("清空已结束的任务记录", `
    <p>删除 <b>${n}</b> 条已结束任务的记录与日志文件。</p>
    <div class="note" style="margin-top:10px">在跑的和排队中的不动。
      只删记录，<b>不动任何产品数据</b>。</div>`, { okText: `清空这 ${n} 条`, danger: true });
  if (!ok) return;
  try {
    const r = await api("/api/jobs/purge", { method: "POST" });
    toast(`已清空 ${r.removed} 条`, "ok"); await loadLogs(true);
  } catch (e) { toast(e.message, "err"); }
};
/* 摘要行（"N 条记录 · 最近 hh:mm"）必须在收起状态下就是准的，否则它永远停在
   "读取中…"。这个接口只返回元信息、不带日志正文，很轻，首屏读一次没问题。 */
document.getElementById("ov-logs").addEventListener("toggle", function () {
  if (this.open) loadLogs(true);
});

let qSeen = {};        // {jobId: status}，用来识别"刚刚结束"并只弹一次提示
let qHadLive = false;  // 上一轮是否还有在跑/排队的任务（用于只在收尾时刷一次总览）
let qSig = "";         // 行集合签名：变了才重建 DOM，否则就地更新（见 tick 里的注释）

async function pollQueue() {
  clearInterval(qTimer);
  qTimer = null;
  const box = document.getElementById("ov-queue");
  let keep = false;          // 这一轮之后还需不需要继续轮询
  const tick = async () => {
    let d;
    try { d = await api("/api/queue"); } catch { return; }
    const live = d.running || [], wait = d.queued || [], fin = d.recent || [];

    /* 刚结束的弹一次提示。以前任务一失败/取消就同时从 running 和 queued 里消失，
       前端据此把整张卡片清空 —— 三小时的日志和进度一瞬间无处可寻，
       而失败原因（比如 ⑤ 撞上同名变体直接 raise）用户根本看不到。 */
    fin.forEach(j => {
      if (qSeen[j.id] === j.status) return;
      const first = !(j.id in qSeen);
      qSeen[j.id] = j.status;
      if (first && !qHadLive) return;      // 页面刚打开时的历史卡片，不补弹
      if (j.status === "error") toast(`任务失败：${j.title} —— ${j.error || "详见日志"}`, "err");
      else if (j.status === "cancelled") toast(`已取消：${j.title}（完成 ${fmt(j.done)}/${fmt(j.total)}）`);
      else if (j.status === "interrupted") toast(`任务中断（平台重启）：${j.title}`, "err");
      else if (j.status === "done") toast(`已完成：${j.title}`, "ok");
    });
    [...live, ...wait].forEach(j => { qSeen[j.id] = j.status; });

    keep = !!(live.length || wait.length);
    if (!live.length && !wait.length && !fin.length) {
      box.innerHTML = ""; qSig = ""; clearInterval(qTimer); qTimer = null;
      return;
    }
    // 在跑的增量拉日志；已结束但还没取过日志的补一次（这样取消后也能马上翻）
    const fresh = await Promise.all(live.map(j => pullLog(j.id)));
    live.forEach((j, i) => { if (fresh[i]) Object.assign(j, fresh[i]); });
    await Promise.all(fin.filter(j => !qLogState(j.id).text).map(j => pullLog(j.id)));

    /* **只有行集合变了才重建 DOM**。原先每 1.2 秒无条件 innerHTML= 整块重建，
       把你正在阅读的日志连同滚动位置一起销毁 —— 表现就是滚动条被反复弹回底部。
       行没变就走 patchRun/patchLog 就地更新。 */
    const sig = [...live.map(j => `${j.id}:run`),
                 ...wait.map(j => `${j.id}:q${j.position}`),
                 ...fin.map(j => `${j.id}:${j.status}`)].join("|");
    if (sig !== qSig) {
      qSig = sig;
      box.innerHTML = `<div class="card qcard">`
        + live.map(j => qRow(j, "run")).join("")
        + wait.map(j => qRow(j, "wait")).join("")
        + fin.map(j => qRow(j, "done")).join("") + `</div>`;
      bindQ(box);
      box.querySelectorAll("[data-logbox]").forEach(el => { el.scrollTop = el.scrollHeight; });
    }
    live.forEach(j => patchRun(box, j));
    [...live, ...fin].forEach(j => patchLog(box, j));

    if (keep) {
      qHadLive = true;
    } else {
      /* 活干完了：停轮询，但卡片留在原地供查阅。收尾钩子跑完任务才会离开 running，
         所以这时候读到的总览一定是新状态。 */
      clearInterval(qTimer); qTimer = null;
      if (qHadLive) {
        qHadLive = false;
        await load().catch(() => {});
        if (document.getElementById("ov-logs").open) loadLogs(true);
      }
    }
  };
  await tick();
  if (keep && !qTimer) qTimer = setInterval(tick, 1200);
}

/* ── 阶段轨道 ─────────────────────────────────────────────────────────── */
function drawRail() {
  const s = OV.summary;
  const stages = [
    { code: "IN", nm: "原始爬取", cls: s.unmapped ? "live" : "raw",
      big: s.unmapped || "—", unit: s.unmapped ? "家待映射" : "",
      sub: s.unmapped ? fmt(s.unmapped_products || 0) + " 产品在输入目录" : "输入目录里没有待映射的",
      flag: s.unmapped ? chip("warn", "stale", "新爬到的，等着走 ②")
                       : chip("idle", "idle", "不再驻留", true) },
    { code: "02", nm: "映射", cls: "live", big: s.mapped, unit: "家",
      sub: fmt(s.mapped_products ?? s.products) + " 产品",
      flag: s.mapped === s.companies ? chip("ok", "done", "全部完成")
                                     : chip("warn", "stale", `${s.companies - s.mapped} 家未映射`) },
    { code: "03", nm: "分类 + 清洗", cls: "live", big: s.cleaned, unit: "家", sub: fmt(s.cleaned_products) + " 产品",
      flag: s.clean_stale ? chip("warn", "stale", `${s.clean_stale} 家有更新`)
          : s.clean_todo ? chip("warn", "stale", `${s.clean_todo} 家未处理`)
                         : chip("ok", "done", "全部完成") },
    /* 只有「一家都不欠」才配绿灯。以前 audited=6 / 待查=45 也照样显示绿色「三项均为 0」——
       颜色和形状两层编码都在说"通过"，恰好违反了本项目「绝不靠颜色单独表意」的规矩。 */
    { code: "04", nm: "复查", cls: s.audited ? "live" : "pending",
      big: s.audited || "未复查", unit: s.audited ? "家" : "",
      sub: s.audit_failed ? `${s.audit_failed} 家有损失` : `${s.audit_unknown} 家待查`,
      flag: s.audit_failed ? chip("err", "warn", "有损失，必须处理")
          : s.audit_unknown ? chip("warn", "stale", `${s.audited}/${s.companies} 家通过，${s.audit_unknown} 家待查`)
          : s.audited ? chip("ok", "done", "三项均为 0")
                      : chip("idle", "idle", "跑一次复查即可点亮") },
    { code: "05", nm: "id 映射", cls: "live", big: s.idmapped, unit: "家", sub: fmt(s.idmapped_products) + " 产品",
      flag: s.no_id ? chip("err", "warn", `${s.no_id} 家未解析 id`)
          : s.idmap_todo ? chip("warn", "stale", `${s.idmap_todo} 家待映射`)
                         : chip("ok", "done", "全部完成") },
  ];
  document.getElementById("ov-rail").innerHTML = stages.map(x => `
    <div class="stage ${x.cls}">
      <div class="code">${x.code}</div><div class="nm">${esc(x.nm)}</div>
      <div class="track"><span class="knob"></span></div>
      <div class="big">${typeof x.big === "number" ? fmt(x.big) : esc(x.big)}${x.unit ? `<small>${x.unit}</small>` : ""}</div>
      <div class="sub">${esc(x.sub)}</div>
      <div class="flag">${x.flag}</div>
    </div>`).join("");
}

/* ── 待办卡 ───────────────────────────────────────────────────────────── */
function drawTodos() {
  const s = OV.summary;
  const cards = [
    { k: s.unmapped ? "q-warn" : "q-ok", i: "play", n: s.unmapped || 0,
      l: `家新公司待映射（${fmt(s.unmapped_products || 0)} 个产品）`, f: "unmapped" },
    { k: "q-err", i: "hand", n: s.with_unmatched, l: `家公司有未匹配产品（${fmt(s.unmatched_products)} 个）`,
      href: "todo.html?tab=manual" },
    { k: "q-ai", i: "bot", n: s.with_review, l: `家公司有待复核研判（${fmt(s.review_products)} 条）`,
      href: "todo.html?tab=review" },
    { k: "q-warn", i: "refresh", n: s.clean_stale, l: "家公司需重跑分类+清洗", f: "stale" },
    { k: s.no_id ? "q-err" : "q-ok", i: "flag", n: s.no_id, l: "家公司未解析 id", f: "noid" },
    /* 搁置不是待办，但也不能是黑洞：埋进去多少东西必须看得见、捞得回来。
       放在这一排而不是 rail 的第 6 格 —— rail 讲的是"每一段的产品数应当对得上"，
       搁置不是流水线的一段，插进去会破坏那个读法。 */
    { k: "q-idle", i: "box", n: s.shelved_products || 0,
      l: `个产品已搁置（${s.with_shelved || 0} 家公司，不计入待办）`, jump: "shelf" },
  ];
  document.getElementById("ov-todos").innerHTML = cards.map(c => `
    <button class="todo ${c.k}" ${c.f ? `data-f="${c.f}"` : ""} ${c.href ? `data-href="${c.href}"` : ""}
            ${c.jump ? `data-jump="${c.jump}"` : ""}>
      <span class="ico">${ic(c.i, 18)}</span>
      <span><span class="n">${c.n}</span> <span class="l">${esc(c.l)}</span></span>
      <span class="go">${ic("chev", 16)}</span>
    </button>`).join("");
}

/* ── 已搁置 ───────────────────────────────────────────────────────────── */
const cacheShelf = cached("ov:shelf", () => api("/api/manual/shelved"),
  { onData: d => drawShelf(d) });

function drawShelf(d) {
  const sec = document.getElementById("shelf");
  sec.hidden = !d.products;
  if (!d.products) return;
  document.getElementById("sh-n").innerHTML =
    chip("idle", "done", `${fmt(d.products)} 个产品 · ${d.company_count} 家公司`);
  /* 陈旧记录会让「实际产品数 − 搁置数」偏小，严重时整家公司从待办里消失。
     检测到就明说，并给一个就地清理的按钮。 */
  document.getElementById("sh-stale").innerHTML = d.stale
    ? `<span style="color:var(--warn)">另有 ${d.stale} 条记录指向已不存在的产品</span>
       <button class="ghost small" id="sh-prune" style="margin-left:8px">清理陈旧记录</button>` : "";
  const pr = document.getElementById("sh-prune");
  if (pr) pr.onclick = async () => {
    pr.disabled = true;
    try {
      const r = await api("/api/manual/shelved/prune", { method: "POST" });
      toast(`已清理 ${r.removed} 条陈旧记录`, "ok");
      cacheShelf.drop(); await cacheShelf.load(true); refreshBadge();
    } catch (e) { toast(e.message, "err"); pr.disabled = false; }
  };
  document.getElementById("sh-list").innerHTML = d.companies.map(g => `
    <details class="drawer" style="margin-top:10px">
      <summary><span class="chev"></span><b>${esc(g.name)}</b>
        <span style="color:var(--muted)">${g.n} 个已搁置</span>
        <span class="cv">${esc(g.products.slice(0, 2).map(p => p.rel.split("/").pop()).join("、"))}${
          g.n > 2 ? " 等" : ""}</span></summary>
      <div class="body">
        <div class="btnrow" style="margin-top:2px">
          <button class="ghost small" data-unall="${esc(g.name)}">${ic("refresh", 12)}全部取消搁置（${g.n} 个）</button>
          <a class="ghost small" style="padding:6px 11px"
             href="todo.html?tab=manual&company=${encodeURIComponent(g.name)}">${ic("hand", 12)} 去人工匹配</a>
        </div>
        <div class="tablebox"><table>
          <thead><tr><th>产品（公司内相对路径）</th><th style="width:150px">搁置时间</th>
            <th>原因</th><th style="width:110px"></th></tr></thead>
          <tbody>${g.products.map(p => `<tr>
            <td class="mono" style="font-size:12px">${esc(p.rel)}</td>
            <td class="mono" style="font-size:12px;color:var(--muted)">${esc(p.time)}</td>
            <td style="color:var(--muted)">${esc(p.reason || "—")}</td>
            <td><button class="ghost small" data-un="${esc(g.name)}" data-rel="${esc(p.rel)}">取消搁置</button></td>
          </tr>`).join("")}</tbody>
        </table></div>
      </div>
    </details>`).join("");

  const un = async (name, rels, label) => {
    const ok = await confirmBox("取消搁置", `
      <p>把 <b>${esc(name)}</b> 的 <b>${rels.length}</b> 个产品重新计入待办。</p>
      <div class="note" style="margin-top:10px">${esc(label)}<br>
        数据本来就没动过，取消搁置只是把记录删掉，它们会重新出现在「待办 · 人工匹配」里。</div>`,
      { okText: "确认取消搁置" });
    if (!ok) return;
    try {
      const r = await api("/api/manual/shelve_many",
        { method: "POST", body: { company: name, rels, undo: true } });
      toast(`已取消 ${r.removed} 个的搁置，重新计入待办`, "ok");
      cacheShelf.drop(); await cacheShelf.load(true);
      refreshBadge(); await load().catch(() => {});
    } catch (e) { toast(e.message, "err"); }
  };
  document.querySelectorAll("#sh-list [data-un]").forEach(b => b.onclick = () =>
    un(b.dataset.un, [b.dataset.rel], b.dataset.rel));
  document.querySelectorAll("#sh-list [data-unall]").forEach(b => b.onclick = () => {
    const g = d.companies.find(x => x.name === b.dataset.unall);
    if (g) un(g.name, g.products.map(p => p.rel), `这家公司全部 ${g.n} 个已搁置产品`);
  });
}

/* ── 矩阵 ─────────────────────────────────────────────────────────────── */
const FSEG = [["todo", "要处理的"], ["unmapped", "待映射"], ["stale", "需重跑"],
              ["noid", "id 未解析"], ["variant", "同名变体"], ["all", "全部"],
              ["done", "已走完全程"]];

const ORD = {
  map:   { none: 0, stale: 1, done: 2 },
  clean: { none: 0, stale: 1, done: 2 },
  audit: { failed: 0, none: 1, stale: 2, done: 3 },
  idmap: { noid: 0, none: 1, todo: 2, done: 3 },
};
/* 「进度」列的排序权重：走得越靠前的排越前，这样一眼能看出队伍拉开多远 */
const stageScore = r => ["map", "clean", "audit", "idmap"]
  .reduce((s, k) => s + ((ORD[k][r[k]] ?? 0) === (Math.max(...Object.values(ORD[k]))) ? 1 : 0), 0);

const sortKey = (r, k) =>
  k === "name" ? r.name : k === "products" ? r.products :
  k === "todo" ? todoOf(r) : k === "stage" ? stageScore(r) : 0;

function visible() {
  let rs = OV.rows.filter(r => !q || r.name.toLowerCase().includes(q));
  if (flt === "todo")     rs = rs.filter(needsMe);
  if (flt === "unmapped") rs = rs.filter(needsMap);
  if (flt === "stale")    rs = rs.filter(r => r.clean === "stale");
  if (flt === "noid")     rs = rs.filter(r => r.idmap === "noid");
  if (flt === "variant")  rs = rs.filter(r => r.variant_of);
  if (flt === "done")     rs = rs.filter(r => nextAction(r) === "done");
  // 各个"要处理"的档位里，把已经走完的和没源可跑的都收起来 —— 它们不需要你做任何事
  if (flt !== "done" && flt !== "all")
    rs = rs.filter(r => !["done", "nosrc"].includes(nextAction(r)));
  return rs.sort((a, b) => {
    const x = sortKey(a, sortK), y = sortKey(b, sortK);
    if (x === y) return a.name.localeCompare(b.name, "zh");
    return (typeof x === "string" ? x.localeCompare(y, "zh") : x - y) * sortD;
  });
}

/* 批量：把当前档位里所有公司的「下一步」按动作分组，每种给一个按钮 */
function drawBulk(rs) {
  const box = document.getElementById("ov-bulk");
  const groups = {};
  rs.forEach(r => {
    const k = nextAction(r);
    if (ACT[k] && ACT[k].bulk) (groups[k] = groups[k] || []).push(r.name);
  });
  const ks = Object.keys(groups);
  if (!ks.length) { box.innerHTML = ""; return; }
  box.innerHTML = ks.map(k =>
    `<button data-bulk="${k}" title="${esc(ACT[k].d)}">${ic("play", 12, 2)}${esc(ACT[k].t)} · ${groups[k].length} 家</button>`
  ).join("") + `<span class="hint" style="margin:0">任务串行排队，可以连着点。</span>`;
  box.querySelectorAll("[data-bulk]").forEach(b =>
    b.onclick = () => doAction(b.dataset.bulk, groups[b.dataset.bulk], b));
}

function drawMatrix() {
  const rs = visible(), tb = document.getElementById("ov-body");
  document.getElementById("ov-count").innerHTML =
    `<b>${rs.length}</b> / ${OV.rows.length} 家 · <b>${fmt(rs.reduce((s, r) => s + r.products, 0))}</b> 产品`;
  drawBulk(rs);
  const more = document.getElementById("ov-more");
  if (more) more.textContent = rs.length > 12
    ? "表格内可滚动，表头会跟着停在顶上；点列名排序。点进度格里的小方块看那一步的明细。" : "";

  if (!rs.length) {
    const allDone = OV.rows.length && OV.rows.every(r => nextAction(r) === "done");
    tb.innerHTML = `<tr><td colspan="5" class="empty">${allDone
      ? `<b>全部公司都走完了全程</b>没有待处理的东西了。切到「全部」可以查看所有公司。`
      : `<b>这一档没有公司</b>${q ? "换个搜索词，或" : ""}换个筛选条件看看。`}
      <div class="btnrow"><button class="ghost small" data-clear>看全部</button></div></td></tr>`;
    tb.querySelector("[data-clear]").onclick = () => {
      q = ""; flt = "all"; document.getElementById("ov-q").value = "";
      syncSeg(); drawMatrix();
    };
    return;
  }

  tb.innerHTML = rs.map(r => {
    const act = nextAction(r);
    const t = todoOf(r);
    const jump = (col, html) => `<a href="${JUMP[col](r.name)}" title="到「${esc(r.name)}」的这一步">${html}</a>`;
    const todoCell = t === 0 ? '<span class="dash">—</span>'
      : [r.unmatched ? jump("unmatched", chip("err", "warn", r.unmatched + " 未匹配")) : "",
         r.review_pending ? jump("review", chip("ai", "stale", r.review_pending + " 待复核")) : ""]
        .filter(Boolean).join(" ");
    const meta = needsMap(r)
      ? (r.count_pending ? "输入目录 · 正在后台清点产品数 …"
                         : `输入目录 · ${fmt(r.input_products || 0)} 个产品待映射`)
      : [r.cid || "id 未解析",
         r.clean_time ? r.clean_time.slice(5, 16) : ""].filter(Boolean).join(" · ");
    /* hot 只留给真正要动手的：51 家里 39 家都染成浅黄，高亮就等于没高亮 */
    const hot = r.variant_of || r.audit === "failed" || r.idmap === "noid";
    const btn = act === "done" ? `<span class="chip ok">${MK.done}走完全程</span>`
      : act === "nosrc" ? `<span class="chip idle" title="${esc(ACT.nosrc.d)}">${MK.idle}无源可处理</span>`
      : `<button class="ghost small act" data-act="${act}" data-c="${esc(r.name)}"
                 title="${esc(ACT[act].d)}">${esc(ACT[act].t)}${ic("chev", 12)}</button>`;
    const main = `<tr class="${hot ? "hot" : ""}">
      <td><div class="cname" title="${esc(r.name)}">${esc(r.name)}</div>
          <div class="cmeta">${esc(meta)}${r.variant_of
            ? `<span class="vtag" title="与「${esc(r.variant_of)}」是同名变体，很可能在重复处理同一批产品">同名变体</span>` : ""}</div></td>
      <td class="tdn">${fmt(r.products)}</td>
      <td class="tdc" data-stage="${esc(r.name)}">${stageCell(r)}</td>
      <td class="tdc">${todoCell}</td>
      <td>${btn}</td></tr>`;
    if (r.idmap !== "noid") return main;
    return main + `<tr class="idrow"><td colspan="5">
      <div class="idbox">
        <label for="al-${esc(r.name)}">给这个目录名指定公司 id</label>
        <input id="al-${esc(r.name)}" class="al" data-name="${esc(r.name)}" value="" data-saved="" placeholder="粘贴公司 id">
        <button class="ghost small al-save" data-name="${esc(r.name)}" disabled></button>
        <span class="note">存进平台别名表，不改动你维护的 <code>公司id.json</code></span>
      </div></td></tr>`;
  }).join("");
  bindAlias();
  tb.querySelectorAll(".act").forEach(b =>
    b.onclick = () => doAction(b.dataset.act, [b.dataset.c], b));
  tb.querySelectorAll("[data-stage] i").forEach(el => {
    el.onclick = () => stageDetail(el.closest("[data-stage]").dataset.stage, el.dataset.col);
  });
}

/* 进度格里的小方块点开：② 看映射报告，④ 看复查明细 */
async function stageDetail(company, col) {
  if (col === "map") {
    try {
      openModal(company + " · 映射报告",
        mdRender(await api("/api/company/report?company=" + encodeURIComponent(company))));
    } catch (e) { toast(e.message, "err"); }
    return;
  }
  if (col === "audit") {
    let d;
    try { d = await api("/api/company/audit?company=" + encodeURIComponent(company)); }
    catch (e) { return toast(e.message, "err"); }
    const list = (items, cols) => !items || !items.length ? "" :
      `<div class="tablebox" style="margin-top:8px"><table><tbody>${items.map(x =>
        `<tr>${cols.map(c => `<td>${esc(typeof x === "string" ? x : x[c])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    openModal(company + " · 复查结论", `
      <div class="stats">
        <div class="stat"><div class="v">${fmt(d.checked || 0)}</div><div class="k">已核对产品</div></div>
        <div class="stat ${d.unmatched ? "err" : "ok"}"><div class="v">${fmt(d.unmatched || 0)}</div><div class="k">缺失产品（须 = 0）</div></div>
        <div class="stat ${d.pdf_missing ? "err" : "ok"}"><div class="v">${fmt(d.pdf_missing || 0)}</div><div class="k">PDF 误删（须 = 0）</div></div>
        <div class="stat ${d.losses ? "err" : "ok"}"><div class="v">${fmt(d.losses || 0)}</div><div class="k">页数损失（须 = 0）</div></div>
        <div class="stat ${d.html_missing ? "err" : "ok"}"><div class="v">${fmt(d.html_missing || 0)}</div><div class="k">html 缺失（须 = 0）</div></div>
      </div>
      <p style="margin-top:12px">${d.ok ? chip("ok", "done", "复查通过") : chip("err", "warn", "存在损失，必须修复后重查")}
        <span class="hint" style="margin-left:8px">复查于 ${esc(d.time || "—")}</span></p>
      ${d.pdf_missing_items ? `<h3 style="margin:14px 0 4px;font-size:13px;color:var(--err)">PDF 整体缺失</h3>` + list(d.pdf_missing_items, ["rel", "imgs", "pdf_pages"]) : ""}
      ${d.html_missing_items ? `<h3 style="margin:14px 0 4px;font-size:13px;color:var(--err)">html 源文件缺失</h3>` + list(d.html_missing_items, ["rel", "exp", "got"]) : ""}
      ${d.loss_items ? `<h3 style="margin:14px 0 4px;font-size:13px">页数损失</h3>` + list(d.loss_items, ["rel", "exp", "got"]) : ""}
      ${d.unmatched_items ? `<h3 style="margin:14px 0 4px;font-size:13px;color:var(--err)">缺失产品</h3>` + list(d.unmatched_items, [0]) : ""}
      ${d.ok ? "" : `<div class="note" style="margin-top:12px"><b>怎么修</b>：删掉对应的清洗输出目录 → 对这家重跑「从 ③ 重跑」→ 再复查，直到四项都是 0。</div>`}`);
    return;
  }
  toast({ clean: "③ 分类+清洗", idmap: "⑤ id 映射" }[col] + "：" +
        (STAGE_LABEL[col] || {})[(OV.rows.find(r => r.name === company) || {})[col]] || "", "");
}

/* 别名保存三态：未指定 / 保存（有改动才激活）/ 保存中 / 已保存 */
function bindAlias() {
  document.querySelectorAll(".al").forEach(inp => {
    const btn = document.querySelector(`.al-save[data-name="${CSS.escape(inp.dataset.name)}"]`);
    if (!btn) return;
    const sync = () => {
      const saved = inp.dataset.saved || "";
      const dirty = inp.value.trim() !== saved;
      btn.disabled = !dirty;
      btn.className = "ghost small al-save" + (!dirty && saved ? " saved" : "");
      btn.innerHTML = dirty ? ic("save", 13) + "保存"
                    : saved ? MK.done + "已保存" : MK.idle + "未指定";
    };
    inp.oninput = sync;
    inp.onkeydown = e => { if (e.key === "Enter" && !btn.disabled) btn.click(); };
    btn.onclick = async () => {
      const v = inp.value.trim();
      btn.disabled = true; btn.innerHTML = "保存中…";
      try {
        await api("/api/idmap/alias", { method: "POST",
          body: { kind: "company", name: inp.dataset.name, id: v } });
        inp.dataset.saved = v;
        sync();
        toast(v ? `已为「${inp.dataset.name}」指定 id —— 现在可以对它跑 ⑤ id 映射了` : "已清除别名", "ok");
      } catch (e) { toast(e.message, "err"); sync(); }
    };
    sync();
  });
}

function syncSeg() {
  document.getElementById("ov-seg").innerHTML =
    FSEG.map(([k, n]) => `<button data-f="${k}" class="${k === flt ? "on" : ""}">${n}</button>`).join("");
}

/* ── 装载 ─────────────────────────────────────────────────────────────── */
function paint() {
  drawRail(); drawTodos(); syncSeg(); drawMatrix();
  document.getElementById("ov-when").innerHTML =
    `${OV.partial ? "更新于" : "扫描于"} <span class="mono">${esc(OV.generated)}</span>`
    + (OV.partial ? `<span class="hint" style="margin:0 0 0 6px">（跑完任务后自动更新了相关公司；点「重新扫描」做一次完整核对）</span>` : "");
  if (OV.id_error) toast("id 表读取失败，⑤ 列不可用：" + OV.id_error, "err");
  const added = OV.input_added || [];
  if (added.length) toast(`输入目录里发现 ${added.length} 家新公司，已加入总览：`
    + added.slice(0, 3).join("、") + (added.length > 3 ? " 等" : ""), "ok");
}

/* 后台清点新公司期间的回取定时器。新公司一进输入目录就以「待清点」占位出现，
   产品数是后台一家一家数出来的（大公司单家要几十秒），数完一家写回一家。
   没有这个回取，那些行就会一直停在「正在清点」上，直到用户自己刷新页面。 */
let countTimer = null;

function watchCounting(d) {
  const busy = (d.counting || []).length;
  if (countTimer) { clearTimeout(countTimer); countTimer = null; }
  if (busy) countTimer = setTimeout(() => load().catch(() => {}), 3000);
}

async function load() {
  const d = await api("/api/overview");
  watchCounting(d);
  if (!d.ready) {
    document.getElementById("ov-when").textContent = "尚未扫描";
    document.getElementById("ov-rail").innerHTML =
      `<div class="empty" style="grid-column:1/-1"><b>还没有流水线快照</b>
        扫描一次就能看到每家公司走到了哪一步。全盘扫描要两三分钟（映射输出、id 表、输入目录都要全树 walk），之后都是秒开。
        <div class="btnrow"><button data-first>开始扫描</button></div></div>`;
    document.getElementById("ov-rail").querySelector("[data-first]").onclick = rescan;
    return;
  }
  OV = d;
  paint();
}

async function rescan() {
  const box = document.getElementById("ov-scanbox");
  const btn = document.getElementById("ov-rescan");
  if (btn.disabled) return;
  btn.disabled = true;
  try {
    const r = await api("/api/overview/scan", { method: "POST" });
    const ui = liveUI(box, { cancel: () => api(`/api/jobs/${r.job_id}/cancel`, { method: "POST" }) });
    ui.open();
    pollJob(r.job_id, ui, async j => {
      btn.disabled = false;
      if (j.status === "done") { await load(); box.style.display = "none"; toast("流水线状态已更新", "ok"); }
    });
  } catch (e) { btn.disabled = false; toast(e.message, "err"); }
}

document.getElementById("ov-rescan").onclick = rescan;
document.getElementById("ov-seg").onclick = e => {
  const b = e.target.closest("button"); if (!b) return;
  flt = b.dataset.f; syncSeg(); drawMatrix();
};
document.getElementById("ov-q").oninput = e => { q = e.target.value.trim().toLowerCase(); drawMatrix(); };
document.getElementById("ov-tbl").querySelectorAll("th.srt").forEach(th => th.onclick = () => {
  const k = th.dataset.s;
  sortD = sortK === k ? -sortD : (k === "name" ? 1 : -1);
  sortK = k; drawMatrix();
});
document.getElementById("ov-todos").onclick = e => {
  const b = e.target.closest(".todo"); if (!b) return;
  if (b.dataset.href) { location.href = b.dataset.href; return; }
  if (b.dataset.jump) {          // 页内区块（已搁置）
    const el = document.getElementById(b.dataset.jump);
    if (el && !el.hidden) el.scrollIntoView({ behavior: "smooth", block: "start" });
    else toast("目前没有已搁置的产品");
    return;
  }
  flt = b.dataset.f; syncSeg(); drawMatrix();
  document.getElementById("ov-tbl").scrollIntoView({ behavior: "smooth", block: "start" });
};

document.getElementById("sh-reload").onclick = () => cacheShelf.load(true).catch(e => toast(e.message, "err"));

/* ── 同名变体 ─────────────────────────────────────────────────────────── */
let VR = [], VRIN = [];
async function loadVariants() {
  const card = document.getElementById("variants");
  try {
    const d = await api("/api/variants");
    VR = d.groups || []; VRIN = d.input || [];
  } catch { VR = []; VRIN = []; }
  card.hidden = !VR.length && !VRIN.length;
  if (card.hidden) return;
  document.getElementById("vr-n").innerHTML =
    chip("warn", "stale", (VRIN.length ? `输入目录 ${VRIN.length} 组` : "") +
      (VRIN.length && VR.length ? " · " : "") + (VR.length ? `已进流程 ${VR.length} 组` : ""));

  /* 输入目录级：只报不动。这时候原始数据还没铺开成四棵树，人工合并成本最低，
     而平台按约定不碰输入目录（原始数据零改动）。 */
  document.getElementById("vr-input").innerHTML = !VRIN.length ? "" : `
    <div class="note err" style="margin-bottom:14px">
      <b>输入目录里就有 ${VRIN.length} 组重名</b> —— 建议在跑映射之前先合并掉。
      现在处理最省事：原始数据还没被复制成映射/清洗/最终三棵树。
      <div style="margin-top:8px">${VRIN.map(g => `
        <div class="mono" style="font-size:12px;margin-top:4px">
          ${g.members.map(m => esc(m.name) + (m.products != null ? `（${fmt(m.products)}）` : "")).join("　＝　")}
        </div>`).join("")}</div>
      <div style="margin-top:8px">平台按约定<b>不改名也不删输入目录</b>：请在资源管理器里把它们并成一个，
        回来刷新即可。这些公司在下面的矩阵里会显示「先处理同名变体」，不会被误跑。</div>
    </div>`;

  document.getElementById("vr-list").innerHTML = VR.map((g, gi) => {
    const rows = g.members.map(m => `
      <tr>
        <td><label class="ck"><input type="radio" name="vr-${gi}" value="${esc(m.name)}"
              ${m.name === (g.suggest || g.base) ? "checked" : ""}> 保留这个</label></td>
        <td>${esc(m.name)}${m.is_base ? " " + chip("idle", "idle", "名称完整") : ""}</td>
        <td class="tdn">${fmt(m.mapped)}</td><td class="tdn">${fmt(m.mapped_pdfs)}</td>
        <td class="tdn">${fmt(m.cleaned)}</td><td class="tdn">${fmt(m.unmatched)}</td>
      </tr>`).join("");
    const diff = g.pairs.filter(p => !p.same).map(p => `
      <div class="note ${g.consistent ? "" : "err"}" style="margin-top:8px">
        <b>${esc(p.a)}</b> ↔ <b>${esc(p.b)}</b>：${esc(p.why)}
        ${(p.only_a || []).length ? `<div class="mono" style="margin-top:4px;font-size:12px">仅前者有：${p.only_a.slice(0, 4).map(esc).join("、")}${p.only_a.length > 4 ? ` 等 ${p.only_a.length} 个` : ""}</div>` : ""}
        ${(p.only_b || []).length ? `<div class="mono" style="margin-top:4px;font-size:12px">仅后者有：${p.only_b.slice(0, 4).map(esc).join("、")}${p.only_b.length > 4 ? ` 等 ${p.only_b.length} 个` : ""}</div>` : ""}
      </div>`).join("");
    return `
      <div class="vrgroup" data-g="${gi}">
        <div class="vrhead">
          <b>${esc(g.base)}</b>
          ${g.consistent ? chip("ok", "done", "两份一致") : chip("warn", "stale", "两份不一致")}
          <span class="hint" style="margin:0">合并后共 ${fmt(g.union_products)} 个产品</span>
        </div>
        <div class="tablebox"><table>
          <thead><tr><th style="width:110px"></th><th>目录名</th><th class="tdn">映射产品</th>
            <th class="tdn">PDF</th><th class="tdn">已清洗</th><th class="tdn">未匹配</th></tr></thead>
          <tbody>${rows}</tbody></table></div>
        ${diff}
        <p class="hint">${esc(g.advice)}</p>
        <div class="btnrow">
          <label class="ck"><input type="checkbox" class="vr-merge" ${g.consistent ? "" : "checked"}>
            先把另一份独有的产品并进保留方（不覆盖同名）</label>
          <button class="ghost small vr-prev" data-g="${gi}">预演</button>
          <button class="vr-go" data-g="${gi}">合并并删除多余目录</button>
        </div>
        <div class="vr-out" data-g="${gi}"></div>
      </div>`;
  }).join("");
}

async function vrRun(gi, apply) {
  const g = VR[gi];
  const box = document.querySelector(`.vrgroup[data-g="${gi}"]`);
  const keep = box.querySelector(`input[name="vr-${gi}"]:checked`).value;
  const merge = box.querySelector(".vr-merge").checked;
  const drop = g.members.map(m => m.name).filter(n => n !== keep);
  if (apply) {
    const ok = await confirmBox("合并同名变体", `
      <p>保留 <b>${esc(keep)}</b>，${merge ? "先把" : "<b>不合并</b>，直接删除"}
         ${drop.map(esc).join("、")}${merge ? " 独有的产品并进保留方，然后删除这些目录" : ""}。</p>
      <div class="note" style="margin-top:10px">会同时改动 映射输出 / 未匹配 / 清洗后 三处，并清掉被删目录的处理记忆。
        <b>删除不可撤销</b>，建议先点「预演」看清楚。</div>`,
      { okText: "确认合并", danger: true });
    if (!ok) return;
  }
  const out = box.querySelector(".vr-out");
  out.innerHTML = `<div class="empty">处理中…</div>`;
  try {
    const r = await api("/api/variants/resolve", { method: "POST", body: { keep, drop, merge, apply } });
    out.innerHTML = `<div class="note ${apply ? "ok" : ""}" style="margin-top:8px">
      ${apply ? "✓ 已完成" : "预演（未动盘）"}：保留 <b>${esc(r.keep)}</b>，
      ${merge ? `并入 <b>${r.merged}</b> 个产品，` : ""}删除 ${r.removed_dirs.length} 个目录。
      ${apply ? "保留方的清洗指纹已变，总览会把它的下一步标成「从 ③ 重跑」。" : ""}
      <div class="mono" style="margin-top:6px;font-size:12px">${r.removed_dirs.map(esc).join("<br>")}</div>
    </div>`;
    if (apply) { await loadVariants(); await load(); toast("同名变体已合并", "ok"); }
  } catch (e) {
    out.innerHTML = `<div class="note err" style="margin-top:8px">${esc(e.message)}</div>`;
  }
}

document.getElementById("vr-list").onclick = e => {
  const prev = e.target.closest(".vr-prev");
  if (prev) return vrRun(+prev.dataset.g, false);
  const go = e.target.closest(".vr-go");
  if (go) return vrRun(+go.dataset.g, true);
};

/* 页面加载时若有正在跑的扫描，接上它 */
resumeJob("overview", document.getElementById("ov-scanbox"), j => {
  if (j.status === "running") document.getElementById("ov-rescan").disabled = true;
  else document.getElementById("ov-scanbox").style.display = "none";
});

persistForm("ov-adv", ["adv-remap", "adv-reclean"]);
(async () => {
  try { CFG = await api("/api/pipeline/config"); } catch { CFG = {}; }
  await load().catch(e => toast(e.message, "err"));
  loadVariants().catch(() => {});
  cacheShelf.load().catch(() => {});
  loadLogs(true).catch(() => {});
  pollQueue().catch(() => {});
})();