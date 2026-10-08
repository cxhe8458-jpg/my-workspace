/* 分类体系：分类树与定义 + 关键词规则。
   原本是两个页面，但「分类是什么」和「靠什么词命中这个分类」是同一件事的两面，
   查规则时经常要回头看定义，分开就得来回切页。 */

let CATS = [], rows = [], shown = 400;
let TAB = localStorage.getItem("tax:tab") || "tree";

/* ── 分类树 ───────────────────────────────────────────────────────────── */
function drawTree() {
  const q = document.getElementById("cat-search").value.trim().toLowerCase();
  const onlyNew = document.getElementById("only-new").checked;
  const hit = s => !q || (s || "").toLowerCase().includes(q);

  let n1 = 0, n2 = 0;
  const html = CATS.map(c => {
    // 两个条件要取交集：以前 if (q) 直接从 c.children 重新过滤，
    // 把「只看新增」的结果整个盖掉，两个筛选没法同时用。
    let kids = c.children;
    if (onlyNew) kids = kids.filter(k => k.new);
    if (q) kids = kids.filter(k => hit(k.name) || hit(k.def));
    const selfHit = hit(c.name) || hit(c.def);
    if (onlyNew && !c.new && !kids.length) return "";
    if (q && !selfHit && !kids.length) return "";
    const list = (q || onlyNew) ? kids : c.children;
    n1++; n2 += list.length;
    return `<details class="catcard" ${q || onlyNew ? "open" : ""}>
      <summary><span class="chev">${ic("chev", 14)}</span>${esc(c.name)}
        ${c.new ? chip("ai", "done", "新增") : ""}
        <span class="cnt">${c.children.length} 个二级</span></summary>
      <div class="catbody">
        <div class="catdef">${esc(c.def || "（还没有定义）")}</div>
        ${list.map(k => `<div class="c2row">
          <div class="c2n">${esc(k.name)} ${k.new ? chip("ai", "done", "新增") : ""}</div>
          <div class="c2d">${esc(k.def || "（还没有定义）")}</div></div>`).join("")}
      </div></details>`;
  }).join("");

  document.getElementById("cat-list").innerHTML = html ||
    `<div class="empty"><b>没有匹配的分类</b>${onlyNew ? "还没有人工新增过分类。" : "换个搜索词试试。"}</div>`;
  document.getElementById("cat-count").innerHTML =
    `<b>${n1}</b> 个一级 · <b>${n2}</b> 个二级`;
}

function fillCat1(sel, blank) {
  sel.innerHTML = (blank ? `<option value="">${blank}</option>` : "") +
    CATS.map(c => `<option value="${esc(c.name)}" title="${esc(c.def)}">${esc(c.name)}${c.new ? "（新增）" : ""}</option>`).join("");
}
function fillCat2(selId, c1, blank) {
  const c = CATS.find(x => x.name === c1);
  document.getElementById(selId).innerHTML = `<option value="">${blank}</option>` +
    (c ? c.children.map(k => `<option value="${esc(k.name)}" title="${esc(k.def)}">${esc(k.name)}${k.new ? "（新增）" : ""}</option>`).join("")
     : "");
}

document.getElementById("cat-search").oninput = drawTree;
document.getElementById("only-new").onchange = drawTree;
document.getElementById("n-mode").onchange = e => {
  const n1 = e.target.value === "cat1";
  document.getElementById("n-c1-sel").style.display = n1 ? "none" : "";
  document.getElementById("n-c1-input").style.display = n1 ? "" : "none";
  document.getElementById("n-def1-wrap").style.display = n1 ? "" : "none";
};
document.getElementById("n-submit").onclick = async () => {
  const isNew1 = document.getElementById("n-mode").value === "cat1";
  const c1 = isNew1 ? document.getElementById("n-c1-input").value.trim()
                    : document.getElementById("n-c1-sel").value;
  const c2 = document.getElementById("n-c2").value.trim();
  const def1 = document.getElementById("n-def1").value.trim();
  const def2 = document.getElementById("n-def2").value.trim();
  if (!c1) return toast(isNew1 ? "填写新一级分类的名称" : "选择一级分类", "err");
  if (!c2) return toast("填写新二级分类的名称", "err");
  if (isNew1 && !def1) return toast("新增一级分类必须写定义", "err");
  if (!def2) return toast("新增二级分类必须写定义", "err");
  try {
    await api("/api/categories/add", { method: "POST", body: { category1: c1, category2: c2, def1, def2 } });
    toast(`已新增 ${c1} / ${c2}`, "ok");
    ["n-c1-input", "n-c2", "n-def1", "n-def2"].forEach(i => document.getElementById(i).value = "");
    await loadCats();
  } catch (e) { toast(e.message, "err"); }
};

/* ── 关键词规则 ───────────────────────────────────────────────────────── */
const TAG = {
  "原始": () => chip("idle", "idle", "原始"),
  "人工添加": () => chip("ok", "done", "人工添加"),
  "人工已确认_ai研判": () => chip("ai", "done", "AI 研判已确认"),
  "人工匹配归档": () => chip("warn", "stale", "人工匹配归档"),
};

async function loadKw() {
  const c1 = document.getElementById("k-cat1").value;
  const c2 = document.getElementById("k-cat2").value;
  const tag = document.getElementById("k-tag").value;
  const d = await api(`/api/keywords?category1=${encodeURIComponent(c1)}&category2=${encodeURIComponent(c2)}&tag=${encodeURIComponent(tag)}`);
  rows = d.rows;
  shown = 400;
  drawKw();
}

/* 4600 条规则一次全渲染会把页面卡住，所以分批：先 400 条，点「再显示」续。
   比一刀切「只显示前 800 条」诚实——后面的还在，只是没画出来。 */
function drawKw() {
  const q = document.getElementById("k-search").value.trim().toLowerCase();
  const list = rows.filter(r => !q || r.keyword.toLowerCase().includes(q) || (r.zh || "").toLowerCase().includes(q));
  const by = t => list.filter(r => r.tag === t).length;
  document.getElementById("k-summary").innerHTML =
    `<b>${fmt(list.length)}</b> 条 · 原始 ${fmt(by("原始"))} · 人工添加 ${by("人工添加")} · ` +
    `AI确认 ${by("人工已确认_ai研判")} · 人工归档 ${by("人工匹配归档")}`;

  const page = list.slice(0, shown);
  document.querySelector("#k-tbl tbody").innerHTML = page.map(r => `
    <tr style="${r.enabled ? "" : "opacity:.5"}">
      <td><b>${esc(r.keyword)}</b></td>
      <td>${esc(r.zh || "—")}</td>
      <td>${esc(r.category1)} / ${esc(r.category2)}</td>
      <td class="n">${(+r.confidence).toFixed(2)}</td>
      <td>${(TAG[r.tag] || TAG["原始"])()}<div class="cmeta">${esc(r.source)}</div></td>
      <td class="mono" style="font-size:11.5px;color:var(--faint)">${esc(r.time || "—")}</td>
      <td>${r.id ? `
        <button class="small ghost" data-kw="${esc(r.id)}" data-en="${r.enabled ? 0 : 1}">${r.enabled ? "禁用" : "启用"}</button>
        <button class="small danger" data-kw="${esc(r.id)}" data-del="1">删除</button>`
        : (r.enabled ? '<span class="dash">只读</span>' : chip("warn", "stale", "已停用"))}</td>
    </tr>`).join("") ||
    `<tr><td colspan="7" class="empty"><b>没有匹配的关键词</b>换个筛选条件或搜索词。</td></tr>`;

  const more = document.getElementById("k-more");
  if (list.length > shown) {
    more.style.display = "";
    more.innerHTML = `<button class="ghost small" id="k-loadmore">再显示 400 条（还有 ${fmt(list.length - shown)} 条）</button>`;
    document.getElementById("k-loadmore").onclick = () => { shown += 400; drawKw(); };
  } else more.style.display = "none";
}

document.querySelector("#k-tbl").onclick = async e => {
  const b = e.target.closest("button[data-kw]"); if (!b) return;
  const id = b.dataset.kw;
  if (b.dataset.del) {
    const ok = await confirmBox("删除这条关键词", `
      <p>删掉之后，靠这个词命中的产品下次映射就不会再自动归类了。</p>
      <p class="hint">只影响自定义库，原始规则库不受影响。</p>`, { okText: "删除", danger: true });
    if (!ok) return;
  }
  try {
    await api("/api/keywords/set", { method: "POST",
      body: { id, enabled: b.dataset.del ? null : b.dataset.en === "1", delete: !!b.dataset.del } });
    toast(b.dataset.del ? "已删除" : (b.dataset.en === "1" ? "已启用" : "已禁用"), "ok");
    await loadKw();
  } catch (e) { toast(e.message, "err"); }
};

document.getElementById("k-cat1").onchange = () => {
  fillCat2("k-cat2", document.getElementById("k-cat1").value, "全部二级分类");
  loadKw();
};
document.getElementById("k-cat2").onchange = loadKw;
document.getElementById("k-tag").onchange = loadKw;
document.getElementById("k-search").oninput = () => { shown = 400; drawKw(); };

function showADef() {
  const c1 = document.getElementById("a-cat1").value, c2 = document.getElementById("a-cat2").value;
  const c = CATS.find(x => x.name === c1);
  const k = c && c.children.find(x => x.name === c2);
  const box = document.getElementById("a-defbox");
  box.innerHTML = (c ? `<div><b>${esc(c.name)}</b>：${esc(c.def || "（还没有定义）")}</div>` : "") +
                  (k ? `<div style="margin-top:5px"><b>${esc(k.name)}</b>：${esc(k.def || "（还没有定义）")}</div>` : "");
  box.style.display = box.innerHTML ? "" : "none";
}
document.getElementById("a-cat1").onchange = () => {
  fillCat2("a-cat2", document.getElementById("a-cat1").value, "选择二级分类");
  showADef();
};
document.getElementById("a-cat2").onchange = showADef;

document.getElementById("a-submit").onclick = async () => {
  const kw = document.getElementById("a-kw").value.trim();
  const c1 = document.getElementById("a-cat1").value, c2 = document.getElementById("a-cat2").value;
  if (!kw) return toast("填写关键词", "err");
  if (!c1 || !c2) return toast("选好一级和二级分类", "err");
  try {
    await api("/api/keywords/add", { method: "POST", body: {
      keyword: kw, zh: document.getElementById("a-zh").value.trim(),
      category1: c1, category2: c2,
      confidence: parseFloat(document.getElementById("a-conf").value) || 0.95 } });
    toast(`「${kw}」→ ${c1} / ${c2}，下次映射立即生效`, "ok");
    document.getElementById("a-kw").value = "";
    document.getElementById("a-zh").value = "";
    await loadKw();
  } catch (e) { toast(e.message, "err"); }
};

/* ── tab ──────────────────────────────────────────────────────────────── */
function showTab(t) {
  TAB = t;
  localStorage.setItem("tax:tab", t);
  document.querySelectorAll("#tx-tabs button").forEach(b => b.classList.toggle("on", b.dataset.t === t));
  document.querySelectorAll("[data-panel]").forEach(p => p.hidden = p.dataset.panel !== t);
  if (t === "kw" && !rows.length) loadKw();
}
document.getElementById("tx-tabs").onclick = e => {
  const b = e.target.closest("button"); if (b) showTab(b.dataset.t);
};

async function loadCats() {
  CATS = await api("/api/manual/categories");
  const n2 = CATS.reduce((s, c) => s + c.children.length, 0);
  const nNew = CATS.reduce((s, c) => s + c.children.filter(k => k.new).length, 0) + CATS.filter(c => c.new).length;
  document.getElementById("tx-stat").innerHTML =
    `<span class="mono">${CATS.length}</span> 个一级 · <span class="mono">${n2}</span> 个二级`
    + (nNew ? ` · ${chip("ai", "done", nNew + " 个人工新增")}` : "");
  fillCat1(document.getElementById("n-c1-sel"));
  fillCat1(document.getElementById("k-cat1"), "全部一级分类");
  fillCat1(document.getElementById("a-cat1"), "选择一级分类");
  drawTree();
}

(async () => {
  persistForm("keywords", ["k-tag"]);
  await loadCats();
  showTab(TAB);
})();
