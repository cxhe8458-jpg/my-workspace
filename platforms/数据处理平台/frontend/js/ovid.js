/* 总览页的「id 解析情况」抽屉 —— 从已删除的流水线页 ⑤ 面板搬过来。

   为什么单独保留这一块：公司 id 来自你在平台外维护的 公司id.json，
   平台只能"查得到 / 查不到"，查不到时一律不猜（猜错就是把一家公司的产品挂到另一家名下）。
   所以这里要能随时看清解析情况、就地补别名，而不是等跑完 ⑤ 去翻日志。 */

let imRows = [];

const cacheIdmap = cached("ov:idmap", () => api("/api/idmap/companies"),
  { onData: (d, fromCache) => { imRows = d.companies; drawIdmap(fromCache); } });
const cacheIdCats = cached("ov:idcats", () => api("/api/idmap/categories"),
  { onData: (d, fromCache) => drawIdCats(d, fromCache) });

function imBusy(msg) {
  document.querySelector("#im-tbl tbody").innerHTML =
    `<tr><td colspan="6" class="empty">${esc(msg)}</td></tr>`;
}

async function imLoad(force = false) {
  const btn = document.getElementById("im-reload");
  btn.disabled = true;
  if (force || !cacheIdmap.data()) imBusy("正在读取清洗后目录…（要全树 walk，约 20 秒）");
  try { await cacheIdmap.load(force); }
  catch (e) { imBusy("读不到清洗后目录：" + e.message); }
  finally { btn.disabled = false; }
  cacheIdCats.load(force).catch(() => {});
}

function drawIdmap(fromCache) {
  const tb = document.querySelector("#im-tbl tbody");
  document.getElementById("im-stamp").textContent = stampText(cacheIdmap.stamp(), fromCache);
  if (!imRows.length) {
    imBusy("清洗后目录还是空的 —— 先让公司走完 ③ 分类+清洗。");
    document.getElementById("im-sum").textContent = "清洗后目录为空";
    return;
  }
  tb.innerHTML = imRows.map(c => `
    <tr class="${c.resolved ? "" : "hot"}">
      <td>${esc(c.name)}</td>
      <td class="tdn">${fmt(c.products)}</td>
      <td class="mono" style="font-size:12px">${c.id ? esc(c.id) : '<span class="dash">—</span>'}</td>
      <td>${c.resolved
            ? chip(c.how === "直接命中" ? "ok" : "warn", c.how === "直接命中" ? "done" : "stale", c.how)
            : chip("err", "warn", "未解析")}</td>
      <td class="tdc">${c.mapped ? chip("ok", "done", "已映射") : chip("idle", "idle", "待映射")}</td>
      <td>${c.resolved && c.how !== "别名指定" ? '<span class="dash">—</span>' :
        `<span style="display:inline-flex;gap:8px;align-items:center">
           <input type="text" class="im-alias" data-name="${esc(c.name)}" value="${esc(c.id)}"
                  data-saved="${esc(c.id)}" placeholder="粘贴公司 id" style="width:190px">
           <button class="ghost small im-save" data-name="${esc(c.name)}" disabled></button></span>`}</td>
    </tr>`).join("");
  const un = imRows.filter(c => !c.resolved).length;
  const todo = imRows.filter(c => !c.mapped).length;
  // 同名变体：两个目录解析到同一个公司 id。跑下去后者会覆盖前者，后端已经拦，这里提前提示。
  const byId = {};
  imRows.forEach(c => { if (c.id) (byId[c.id] = byId[c.id] || []).push(c.name); });
  const clash = Object.values(byId).filter(v => v.length > 1);
  document.getElementById("im-sum").innerHTML =
    `${imRows.length} 家 · 待映射 ${todo}` + (un ? ` · ${un} 家未解析 id` : " · id 全部解析");
  document.getElementById("im-clash").innerHTML = clash.length ? `
    <div class="note err" style="margin:10px 0">
      <b>${clash.length} 组目录解析到同一个公司 id</b>，跑下去后一份会覆盖前一份。已阻止执行——
      请先到上面的「同名变体」合并，或给其中一个单独指定 id。
      <div class="mono" style="margin-top:6px;font-size:12px">
        ${clash.map(v => esc(v.join("  ＝  "))).join("<br>")}</div>
    </div>` : "";
  bindImAlias();
}

/* 分类 id：以前只有公司 id 有表可看，分类能不能解析要等跑完翻日志。 */
function drawIdCats(d, fromCache) {
  const box = document.getElementById("im-cats");
  if (!box) return;
  const rows = d.rows || [];
  if (!rows.length) { box.innerHTML = ""; return; }
  const VIA = { "未解析": () => chip("err", "warn", "未解析"),
                "退回一级": () => chip("warn", "stale", "退回一级 id"),
                "二级": () => chip("ok", "done", "二级 id") };
  const bad = rows.filter(r => r.via !== "二级");
  box.innerHTML = `
    <details class="drawer" ${bad.length ? "open" : ""} style="margin-top:14px">
      <summary>分类 id 解析情况
        <span class="cv">${rows.length} 个分类 · ${d.unresolved} 个未解析 · ${d.fallback} 个退回一级
          <span class="mono" style="color:var(--faint)">${stampText(cacheIdCats.stamp(), fromCache)}</span></span></summary>
      <div class="body">
        <p class="hint">二级分类查不到 id 会退回用一级的 id（既定规则）；两级都查不到就按中文名输出、零丢失。</p>
        <div class="tablebox"><table>
          <thead><tr><th>一级分类</th><th>二级分类</th><th class="tdn">产品</th><th>落地 id</th><th>解析</th></tr></thead>
          <tbody>${rows.map(r => `<tr class="${r.via === "未解析" ? "hot" : ""}">
            <td>${esc(r.category1)}</td><td>${esc(r.category2) || '<span class="dash">—</span>'}</td>
            <td class="tdn">${fmt(r.products)}</td>
            <td class="mono" style="font-size:12px">${esc(r.id)}</td>
            <td>${(VIA[r.via] || VIA["二级"])()}</td></tr>`).join("")}</tbody>
        </table></div>
      </div>
    </details>`;
}

/* 别名保存三态：未指定 / 保存（有改动才激活）/ 保存中 / 已保存 */
function bindImAlias() {
  document.querySelectorAll(".im-alias").forEach(inp => {
    const btn = document.querySelector(`.im-save[data-name="${CSS.escape(inp.dataset.name)}"]`);
    if (!btn) return;
    const sync = () => {
      const saved = inp.dataset.saved || "";
      const dirty = inp.value.trim() !== saved;
      btn.disabled = !dirty;
      btn.className = "ghost small im-save" + (!dirty && saved ? " saved" : "");
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
        const tr = btn.closest("tr");
        tr.children[2].innerHTML = v ? `<span class="mono" style="font-size:12px">${esc(v)}</span>` : '<span class="dash">—</span>';
        tr.children[3].innerHTML = v ? chip("warn", "stale", "别名指定") : chip("err", "warn", "未解析");
        tr.children[3].setAttribute("aria-live", "polite");
        tr.classList.toggle("hot", !v);
        cacheIdmap.drop();          // 自己改过盘，缓存作废
        toast(v ? `已为「${inp.dataset.name}」指定 id` : "已清除该别名", "ok");
      } catch (e) { toast(e.message, "err"); }
    };
    sync();
  });
}

document.getElementById("im-reload").onclick = () => imLoad(true);

/* 「重新解析并映射未完成的」：id 表本来就是每次运行时实时读的，所以"刷新"真正要做的是
   ① 重读一遍解析情况（别名可能刚填过、外部 id 表可能刚补过）
   ② 把解析得出来、但还没映射的公司排进队列跑 ⑤ */
document.getElementById("im-resolve").onclick = async () => {
  const btn = document.getElementById("im-resolve");
  btn.disabled = true;
  try {
    await imLoad(true);
    const todo = imRows.filter(c => c.resolved && !c.mapped).map(c => c.name);
    const un = imRows.filter(c => !c.resolved).map(c => c.name);
    if (!todo.length) {
      toast(un.length ? `没有可映射的：${un.length} 家仍未解析出 id，先在下表里填` : "全部已映射，无需重跑", un.length ? "err" : "ok");
      return;
    }
    const ok = await confirmBox("重新解析并映射", `
      <p>重读 id 表与别名表后，有 <b>${todo.length}</b> 家解析出了 id 但还没映射，将排进队列跑 ⑤。</p>
      <div class="note" style="margin-top:10px">${todo.slice(0, 8).map(esc).join("、")}${todo.length > 8 ? ` 等 ${todo.length} 家` : ""}
        ${un.length ? `<br>另有 <b>${un.length}</b> 家仍解析不出 id，不动它们（按原名输出会污染交付目录）。` : ""}</div>`,
      { okText: "开始映射" });
    if (!ok) return;
    await api("/api/idmap/run", { method: "POST", body: { companies: todo, apply: true } });
    toast(`已排入队列：id 映射 · ${todo.length} 家`, "ok");
    if (typeof pollQueue === "function") pollQueue();
  } catch (e) { toast(e.message, "err"); }
  finally { btn.disabled = false; }
};

/* 抽屉第一次展开时才去读（那两个接口都要全树 walk，不能页面一加载就跑） */
document.getElementById("ov-idbox").addEventListener("toggle", function () {
  if (this.open && !cacheIdmap.data()) imLoad(false);
  else if (this.open) { imRows = cacheIdmap.data().companies; drawIdmap(true);
                        const c = cacheIdCats.data(); if (c) drawIdCats(c, true); }
});
