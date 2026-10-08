/* 数据修正：图片 / PDF / URL / 删除产品。

   一条铁律：服务端可能"文件已改、写状态失败"，所以任何写操作报错后都要
   **回查磁盘真实状态并刷新面板**，再让用户决定。照着错误提示直接重试，
   会把同一张图存成好几份（上一代真发生过）。 */
"use strict";

const KIND_LABEL = { main: "主图", param: "参数图", pdf: "PDF" };

async function fixFailed(label, err) {
  toast(`✘ ${label}报错: ${err.message} —— 正在回查磁盘真实状态…`, 8000);
  try {
    await openProduct(S.current);
    toast(`✘ ${label}报错: ${err.message}　⚠ 面板已刷新为磁盘真实状态，请先核对是否其实已生效，不要直接重试（重试会产生重复数据）`, 12000);
  } catch (e2) {
    toast(`✘ ${label}报错: ${err.message}　⚠ 且无法回查（${e2.message}），请按 F5 刷新后核对再操作`, 12000);
  }
}

/* ---------- 图片渲染 + 改归类 ---------- */
function renderImgs(sel, p, files) {
  const box = $(sel);
  const kind = sel === "#prod-mainimgs" ? "main" : "param";
  const other = kind === "main" ? "param" : "main";
  const panel = box.closest(".panel");
  const ro = S.readonly;
  if (ro) { panel.ondragover = panel.ondrop = panel.ondragleave = null; }
  else bindDropZone(panel, kind);

  if (!files.length) {
    box.innerHTML = `<div class="empty">无图片文件${ro ? "" :
      `（可把「${KIND_LABEL[other]}」面板里归类错的图<b>拖到本面板任意位置</b>，或用那张图下方的「→${KIND_LABEL[kind]}」按钮）`}</div>`;
    return;
  }
  box.innerHTML = files.map(f => {
    const url = fileUrl(p.path + "/" + f);
    return `<figure ${ro ? "" : `draggable="true"`} data-f="${esc(f)}" data-kind="${kind}"
        ${ro ? "" : `title="按住可拖到「${KIND_LABEL[other]}」面板；不想拖就点下方「→${KIND_LABEL[other]}」"`}>
      <img src="${url}" loading="lazy" onclick="showLightbox('${url}')" alt="">
      <figcaption>${esc(f)}</figcaption>
      ${ro ? "" : `<div class="img-actions">
        <button onclick="reclassifyImg('${esc(f)}','${other}')" title="一键改归类，不依赖拖拽">→${KIND_LABEL[other]}</button>
        <button onclick="uploadFile('${kind}','${esc(f)}')">替换</button>
        <button onclick="deleteFile('${esc(f)}')">删除</button></div>`}
    </figure>`;
  }).join("");

  if (ro) return;
  $$(sel + " figure").forEach(fig => {
    fig.ondragstart = e => {
      e.dataTransfer.setData("text/reclass", JSON.stringify({ filename: fig.dataset.f, from: fig.dataset.kind }));
      e.dataTransfer.effectAllowed = "move";
      setImgDragging(true);
    };
    fig.ondragend = e => {
      setImgDragging(false);
      if (e.dataTransfer.dropEffect === "none")
        toast(`未落在面板内，归类没有执行 —— 目标面板不在屏幕上时，点图片下方的「→${KIND_LABEL[other]}」更快`, 5000);
    };
  });
}

/* 拖图期间让底部常驻栏穿透：#mark-dock 永远盖住视口底部，
   不穿透的话 drop 实际落在标记按钮上，dragover 从不 preventDefault，静默失败 */
function setImgDragging(on) {
  document.body.classList.toggle("img-dragging", on);
  if (!on) { stopEdgeScroll(); $$(".panel.drop-target").forEach(el => el.classList.remove("drop-target")); }
}

let _edgeTimer = null;
function stopEdgeScroll() { clearInterval(_edgeTimer); _edgeTimer = null; }
function edgeAutoScroll(y) {
  const M = 90, d = y < M ? -22 : (y > innerHeight - M ? 22 : 0);
  stopEdgeScroll();
  if (!d) return;
  _edgeTimer = setInterval(() => {
    if (!document.body.classList.contains("img-dragging")) return stopEdgeScroll();
    scrollBy(0, d);
  }, 30);
}
document.addEventListener("dragover", e => {
  if (!document.body.classList.contains("img-dragging")) return;
  // 已经悬在目标面板上就别滚了：目标面板常常正贴着屏幕下沿，继续滚会把它从指针下抽走
  const onTarget = e.target instanceof Element && e.target.closest("#p-main, #p-pimg");
  onTarget ? stopEdgeScroll() : edgeAutoScroll(e.clientY);
});
document.addEventListener("drop", () => setImgDragging(false));
document.addEventListener("dragend", () => setImgDragging(false));

function bindDropZone(panel, kind) {
  panel.ondragover = e => {
    if (![...e.dataTransfer.types].includes("text/reclass")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    panel.classList.add("drop-target");
  };
  // relatedTarget 仍在面板内 = 只是掠过子元素，不能取消高亮（否则整片闪烁）
  panel.ondragleave = e => { if (!panel.contains(e.relatedTarget)) panel.classList.remove("drop-target"); };
  panel.ondrop = e => {
    panel.classList.remove("drop-target");
    setImgDragging(false);
    const raw = e.dataTransfer.getData("text/reclass");
    if (!raw) return;
    e.preventDefault();
    const { filename, from } = JSON.parse(raw);
    if (from === kind) return;              // 拖回原面板 = 无操作
    reclassifyImg(filename, kind);
  };
}

window.reclassifyImg = async (filename, to) => {
  try {
    const r = await api("/api/fix/reclassify", { method: "POST", json: { path: S.current, filename, to } });
    if (r.unchanged) return toast(`${filename} 本来就是${KIND_LABEL[to]}，无需归类`);
    toast(`✔ ${filename} 已归类为${KIND_LABEL[to]}（${r.new_name}，原文件已备份）`, 3500);
    openProduct(S.current);
  } catch (e) { fixFailed("归类", e); }
};

/* ---------- 上传 ---------- */
let upImages = [];

/* 同一张图重复粘贴不再入库两次：连按 Ctrl+V 很容易把同一张截图存成三份 */
async function stageImages(files) {
  let added = 0, dup = 0;
  for (const f of files) {
    if (!f || !f.type.startsWith("image/")) continue;
    const data = await readAsDataURL(f);
    if (upImages.includes(data)) { dup++; continue; }
    upImages.push(data); added++;
  }
  renderThumbs();
  return { added, dup };
}

function renderThumbs() {
  const box = $("#up-thumbs");
  if (!box) return;
  box.innerHTML = upImages.map((u, i) =>
    `<div class="thumb"><img src="${u}" alt=""><button class="rm" onclick="upImages.splice(${i},1);renderThumbs()">×</button></div>`).join("");
  ["#btn-up-do", "#btn-up-main", "#btn-up-param"].forEach(s => { const b = $(s); if (b) b.disabled = !upImages.length; });
}
window.renderThumbs = renderThumbs;

window.uploadFile = (kind, replace) => {
  if (kind === "pdf") {
    const input = document.createElement("input");
    input.type = "file"; input.accept = "application/pdf";
    input.onchange = async () => {
      const f = input.files[0];
      if (!f) return;
      toast("上传处理中…", 30000);
      try {
        await api("/api/fix/upload", { method: "POST", json: { path: S.current, kind, data: await readAsDataURL(f), replace } });
        toast(`✔ PDF ${replace ? "已替换" : "已上传"}（原文件已备份，info.json 已同步）`, 3500);
        openProduct(S.current);
      } catch (e) { fixFailed("PDF上传", e); }
    };
    input.click();
    return;
  }
  upImages = [];
  const label = KIND_LABEL[kind];
  showModal(`<h3>${replace ? `替换 ${esc(replace)}` : `上传${label}`} — ${esc(S.prod.name)}</h3>
    <div class="muted" style="margin-bottom:8px">官网图片右键<b>复制图片</b>后 <b>Ctrl+V 粘贴</b>，或拖拽/点击选择文件${replace ? "（替换只取第一张）" : "（可多张）"}。
      任何格式都会自动转 PNG 并按 ${kind === "main" ? "主图.png / 主图_N.png" : "参数图_N.png"} 规范命名，原文件自动备份。</div>
    <div class="upload-zone" id="up-zone">📷 点击选择 / 拖拽 / Ctrl+V 粘贴图片</div>
    <div class="upload-thumbs" id="up-thumbs"></div>
    <div class="row"><button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" id="btn-up-do" disabled>上传</button></div>`);
  const add = async files => { if (replace) upImages = []; return stageImages(files); };
  pasteHandler = add;
  const pick = files => add(files).then(r => toast(pasteMsg(r)));
  const zone = $("#up-zone");
  zone.onclick = () => {
    const input = document.createElement("input");
    input.type = "file"; input.accept = "image/*"; input.multiple = !replace;
    input.onchange = () => pick([...input.files]);
    input.click();
  };
  zone.ondragover = e => { e.preventDefault(); zone.classList.add("drag"); };
  zone.ondragleave = () => zone.classList.remove("drag");
  zone.ondrop = e => { e.preventDefault(); zone.classList.remove("drag"); pick([...e.dataTransfer.files]); };
  $("#btn-up-do").onclick = e => busy(e.target, () => doUpload(kind, replace));
};

/* 页面级 Ctrl+V：不用先点「＋上传」，粘完再选存到哪个面板 */
async function pasteChooser(files) {
  upImages = [];
  const staged = await stageImages(files);
  showModal(`<h3>📋 已粘贴 ${upImages.length} 张图片 — ${esc(S.prod.name)}</h3>
    <div class="muted" style="margin-bottom:8px">选择存入哪个面板：自动转 PNG 并按规范命名，原文件自动备份。可继续 Ctrl+V 追加。</div>
    <div class="upload-thumbs" id="up-thumbs"></div>
    <div class="row"><button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" id="btn-up-main">存为主图</button>
      <button class="btn primary" id="btn-up-param">存为参数图</button></div>`);
  pasteHandler = stageImages;
  renderThumbs();
  $("#btn-up-main").onclick = e => busy(e.target, () => doUpload("main", ""));
  $("#btn-up-param").onclick = e => busy(e.target, () => doUpload("param", ""));
  return staged;
}

async function doUpload(kind, replace) {
  let ok = 0;
  try {
    for (const data of upImages) {
      await api("/api/fix/upload", { method: "POST", json: { path: S.current, kind, data, replace } });
      ok++;
      if (replace) break;
    }
    toast(`✔ ${replace ? "已替换" : `已存为${KIND_LABEL[kind]} ${ok} 张`}（原文件已备份，info.json 已同步）`, 3500);
    closeModal();
    openProduct(S.current);
  } catch (e) { closeModal(); fixFailed(`上传（已成功 ${ok} 张）`, e); }
}

/* ---------- 删除 / URL / 产品 ---------- */
window.deleteFile = async filename => {
  try {
    const r = await api("/api/fix/delete_file", { method: "POST", json: { path: S.current, filename } });
    pushUndo({ type: "file", path: S.current, label: filename, backup: r.backup });
    openProduct(S.current);
  } catch (e) { fixFailed("删除", e); }
};

window.editField = async field => {
  const cur = field === "页面URL" ? S.prod.url : S.prod.manual_url;
  const val = prompt(`修正「${field}」（原 info.json 会自动备份）`, cur || "");
  if (val === null || val === cur) return;
  try {
    await api("/api/fix/field", { method: "PUT", json: { path: S.current, field, value: val.trim() } });
    toast("✔ 已修改并备份");
    openProduct(S.current);
  } catch (e) { fixFailed("修改URL", e); }
};

window.deleteProduct = btn => busy(btn, async () => {
  const path = S.current;
  const idx = S.products.findIndex(p => p.path === path);
  try {
    const r = await api("/api/product/delete", { method: "POST", json: { path } });
    pushUndo({ type: "product", id: r.undo.id, label: r.undo.name });
    S.products = S.products.filter(p => p.path !== path);
    const next = S.products[Math.min(Math.max(idx, 0), S.products.length - 1)];
    if (next) openProduct(next.path);
    else {
      S.current = null; S.prod = null;
      renderTree();
      $("#detail-body").style.display = "none";
      document.body.classList.remove("dock-on");
      $("#detail-empty").style.display = "block";
    }
  } catch (e) { fixFailed("删除产品", e); }
});

window.createProductModal = () => {
  const cats = [...new Set(S.products.map(p => p.categories.join("/")).filter(Boolean))];
  showModal(`<h3>＋ 新增产品 — ${esc(S.company)}</h3>
    <div class="muted" style="margin-bottom:10px">补录<b>漏提取</b>的产品：先建目录与 info.json 骨架（状态未检），创建后用上传功能补齐图片与 PDF。</div>
    <div class="form-row"><label>分类路径</label><input id="np-cat" list="np-cats" placeholder="如 激光器件/隔离器，多级用 / 分隔，留空=公司根级">
      <datalist id="np-cats">${cats.map(c => `<option value="${esc(c)}">`).join("")}</datalist></div>
    <div class="form-row"><label>产品名 *</label><input id="np-name" placeholder="必填，将作为产品文件夹名"></div>
    <div class="form-row"><label>页面URL</label><input id="np-url" placeholder="官网产品页链接（建议填写）"></div>
    <div class="row"><button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" id="btn-np">创建产品</button></div>`);
  $("#np-name").focus();
  $("#btn-np").onclick = e => busy(e.target, async () => {
    const name = $("#np-name").value.trim();
    if (!name) return toast("请填写产品名");
    try {
      const r = await api("/api/product/create", { method: "POST", json: {
        company: S.company, category: $("#np-cat").value.trim(), name, url: $("#np-url").value.trim() } });
      toast("✔ 已创建产品骨架，请补齐图片/PDF 后审核", 4500);
      closeModal();
      await openCompany(S.company, true);
      openProduct(r.product.path);
    } catch (err) { toast("✘ 创建失败: " + err.message, 6000); }
  });
};
