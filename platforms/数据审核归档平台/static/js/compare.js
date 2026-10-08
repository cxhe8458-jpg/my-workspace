/* 对比模式：左=产品数据，右=原网页。两种方式：并排窗口 / 页内内嵌。

   COOP 是这里的核心约束：官网发了 Cross-Origin-Opener-Policy 时，浏览器会把弹窗
   从 opener 切断，此后 cmpWin.closed 恒为 true。它表示的是"我还能不能操作这个窗口"，
   **不是"窗口还在不在"**。上一代把它当后者用，于是切换产品时误判成"窗口已关"：
   既不清空旧页面、还撤销了半屏布局，而那个窗口仍开着停在上一个产品上。

   被切断的后果比"提示一下"严重得多：窗口关不掉、换不了页，连 window.open 的同名复用
   都失效（窗口名只在同一个浏览上下文组内可见），于是**每审一个产品就攒一个孤儿窗口**，
   浮动条也只能一直挂着"还停在上一个产品"。真正的解法是别让顶层文档是官网：
   COOP 只管顶层，所以对这类站点改开**同源跳板窗口**（static/cmpwin.html，官网放它的 iframe 里，
   官网禁止内嵌就代理渲染）。跳板窗口永远可控——切产品自动跟随换页，关对比时自己关掉。

   哪些站点要走跳板：优先问后端 /api/pagecheck（读响应头里的 COOP）；探测不到就照旧开真窗口，
   开完 1.5 秒回头看一眼，发现已经被切断就把这个域名记下来，下次自动改走跳板。 */
"use strict";

let cmpOn = localStorage.getItem("compare") === "1";
let cmpMode = localStorage.getItem("cmpMode") || "window";
let cmpSqueeze = localStorage.getItem("cmpSqueeze") !== "0";
let cmpFollow = localStorage.getItem("cmpFollow") !== "0";   // 切产品时对比窗口自动跟随
let cmpProxyHosts = new Set(JSON.parse(localStorage.getItem("cmpProxyHosts") || "[]"));
let cmpSevered = new Set(JSON.parse(localStorage.getItem("cmpSevered") || "[]"));  // 已知会切断 opener 的域名
let cmpWin = null;
let cmpWinOpen = false;    // 窗口开没开由我们自己记账，不信 closed
let cmpWinKind = null;     // "relay"=同源跳板（可控） | "native"=直开官网（可能被切断）
let cmpWinLost = false;    // 真窗口已被切断：关不掉也换不了，只能请用户手动关
let cmpShownPath = null;   // 窗口当前显示的是哪个产品
const cmpPolicy = new Map();   // host -> {coop, embeddable}

const hostOf = u => { try { return new URL(u).host; } catch (e) { return ""; } };
const cmpControllable = () => { try { return !!cmpWin && !cmpWin.closed; } catch (e) { return false; } };
const willSever = url => cmpSevered.has(hostOf(url)) || !!(cmpPolicy.get(hostOf(url)) || {}).coop;

function rememberSevered(host) {
  if (!host || cmpSevered.has(host)) return;
  cmpSevered.add(host);
  localStorage.setItem("cmpSevered", JSON.stringify([...cmpSevered]));
}

/* 提前问后端：这个站点会不会切断弹窗。一域名一次，结果缓存在内存里 */
function probeHost(url) {
  const h = hostOf(url);
  if (!h || cmpPolicy.has(h)) return;
  cmpPolicy.set(h, {});                        // 占位，避免同一域名并发重复探测
  api("/api/pagecheck?url=" + encodeURIComponent(url))
    .then(r => { cmpPolicy.set(h, r); if (r.coop) rememberSevered(h); })
    .catch(() => cmpPolicy.delete(h));
}

function closeCmpWin() {
  const stuck = cmpWinOpen && cmpWinKind === "native" && !cmpControllable();
  try { if (cmpControllable()) cmpWin.close(); } catch (e) { /* 被切断时 close 也调不动 */ }
  cmpWin = null; cmpWinOpen = false; cmpWinKind = null; cmpWinLost = false; cmpShownPath = null;
  if (stuck) toast("⚠ 上一个官网窗口已脱离平台控制（该站点 COOP），平台关不掉它，请手动关闭", 6000);
}

function setWinActive(on) {
  $("#workspace").classList.toggle("cmp-win", on && cmpSqueeze);
  const f = $("#cmp-float");
  f.classList.toggle("show", on);
  f.classList.toggle("fullw", !cmpSqueeze);
  $("#compare-pane").style.display = on ? "none" : "";
  if (on) {
    const pos = JSON.parse(localStorage.getItem("cmpFloatPos") || "null");
    if (pos) { f.style.left = pos.left; f.style.top = pos.top; f.style.right = "auto"; f.style.bottom = "auto"; }
  }
}

function toggleCompare(on) {
  cmpOn = on;
  localStorage.setItem("compare", on ? "1" : "0");
  $("#workspace").classList.toggle("compare", on);
  $("#btn-compare")?.classList.toggle("primary", on);
  if (on) updateComparePane();
  else { $("#cmp-frame").src = "about:blank"; closeCmpWin(); setWinActive(false); }
}
window.toggleCompareBtn = () => toggleCompare(!cmpOn);

function cmpShowStart(msg) {
  $("#cmp-start").style.display = "flex";
  $("#cmp-frame").style.display = "none";
  $("#cmp-frame").src = "about:blank";
  $("#cmp-start-tip").innerHTML = msg;
}

function updateComparePane() {
  if (!cmpOn) return;
  const url = S.prod && S.prod.url;
  if (url) probeHost(url);
  $("#cmp-url").textContent = url || "（无页面URL）";
  $("#cmp-url").title = url || "";
  const isWin = cmpMode === "window";
  $("#btn-cmp-mode").textContent = isWin ? "🗗 并排窗口" : "🖼 页内内嵌";
  $("#btn-cmp-proxy").style.display = isWin ? "none" : "";

  if (isWin && cmpWinOpen) {
    setWinActive(true);
    if (cmpFollow && url && cmpControllable()) cmpOpen(true);          // 自动跟随：直接换到当前产品
    // 跟不动就别乱动：被 COOP 切断时清不掉旧页面，那就靠浮动条明示，绝不悄悄撤销布局
    else if (!cmpFollow && cmpControllable() && cmpWinKind === "native") {
      try { cmpWin.location.replace("about:blank"); cmpShownPath = null; } catch (e) { /* 切断了 */ }
    }
    updateFloatGo();
    return;
  }
  setWinActive(false);
  if (!url) return cmpShowStart("该产品没有页面URL，无法对比（可点产品名旁 ✏️ 补录）");
  cmpShowStart(isWin
    ? "点击打开一个<b>停靠屏幕右半边的对比窗口</b>（任何网站都能开），平台自动压缩到左半屏。<br>之后每切换一个产品，窗口会自动跟着换页；关掉「🔁 跟随」则改为手动点「▶」。"
    : "点击后网页内嵌在此区域。部分网站拒绝被内嵌（空白时点「代理」由平台代抓展示）。");
}

/* 浮动条状态：必须写清窗口停在哪个产品，否则会对着上一个产品的网页核对数据 */
function updateFloatGo() {
  const url = S.prod && S.prod.url;
  const b = $("#btn-float-go");
  b.disabled = !url;
  b.title = url ? "打开当前产品的原网页：" + url : "该产品没有页面URL";
  const fo = $("#btn-float-follow");
  fo.classList.toggle("primary", cmpFollow);
  fo.title = cmpFollow ? "自动跟随：切换产品时对比窗口自动换页（点击关闭）"
    : "手动模式：切换产品后需自己点「▶」（点击开启自动跟随）";
  const behind = cmpShownPath && cmpShownPath !== S.current;
  const st = $("#cmp-float-state");
  st.textContent = cmpWinLost
    ? "⚠ 官网窗口已脱离平台控制（该站点 COOP），请手动关闭它；再点 ▶ 会换成可控的对比窗口"
    : behind ? "⚠ 对比窗口还停在上一个产品，点 ▶ 切到当前产品"
      : (cmpShownPath ? "🌐 对比中：当前产品" : "🌐 并排对比中，点 ▶ 打开产品页");
  st.classList.toggle("behind", cmpWinLost || !!behind);
}

/* auto=true 表示是切换产品时自动跟随触发的：不主动开新窗口、不抢焦点、无 URL 也不吭声 */
function cmpOpen(auto) {
  const url = S.prod && S.prod.url;
  if (!url) return auto ? undefined : toast("该产品没有页面URL");
  if (cmpMode !== "window") {
    $("#cmp-start").style.display = "none";
    const frame = $("#cmp-frame");
    frame.style.display = "block";
    const useProxy = cmpProxyHosts.has(hostOf(url));
    $("#btn-cmp-proxy").classList.toggle("primary", useProxy);
    frame.src = useProxy ? "/api/proxy?url=" + encodeURIComponent(url) : url;
    cmpShownPath = S.current;
    return updateFloatGo();
  }

  const relay = willSever(url);
  const name = (S.prod && S.prod.name) || "";
  // 已开着的窗口能不能接着用：跳板窗口同源，直接调它换页；真窗口靠同名 window.open 复用。
  // 目标站不需要跳板时，手动点 ▶ 会顺势把跳板窗口换回真窗口（同名复用，不会多开）
  if (cmpWinOpen && cmpWinKind === "relay" && cmpControllable() && (relay || auto)) {
    try {
      cmpWin.__cmpShow(url, name);
      cmpShownPath = S.current; cmpWinLost = false;
      if (!auto) try { cmpWin.focus(); } catch (e) { }
      setWinActive(true);
      return updateFloatGo();
    } catch (e) { /* 跳板页还没加载完：往下用同名 window.open 覆盖它，不会多开窗口 */ }
  }
  // 真窗口 + 自动跟随：只能用 location.replace 换页。复用同名窗口的 window.open 会把它
  // 抬到最前，等于每切一个产品就抢走一次焦点（坑 9：拖拽中止、Ctrl+V 粘到官网去）
  if (auto && cmpWinOpen && cmpWinKind === "native" && cmpControllable()) {
    try {
      cmpWin.location.replace(url);
      cmpShownPath = S.current;
      setWinActive(true);
      return updateFloatGo();
    } catch (e) { cmpWinLost = true; return updateFloatGo(); }   // 刚被切断，别再自动开一个
  }
  // 自动跟随只换页，不主动开窗：用户自己关掉的窗口别给他弹回来，失控的真窗口更不能再开一个
  if (auto && !(cmpWinOpen && cmpControllable())) return;
  if (cmpWinOpen && cmpWinKind === "relay" && !cmpControllable()) closeCmpWin();

  const w = Math.floor(screen.availWidth / 2), h = screen.availHeight;
  const target = relay
    ? "/static/cmpwin.html?url=" + encodeURIComponent(url) + "&name=" + encodeURIComponent(name)
    : url;
  cmpWin = window.open(target, "cmp_win", `popup=yes,left=${w},top=0,width=${w},height=${h}`);
  if (!cmpWin) return toast("✘ 弹窗被浏览器拦截，请允许本站弹窗后重试", 6000);
  cmpWinOpen = true;
  cmpWinKind = relay ? "relay" : "native";
  cmpWinLost = false;
  cmpShownPath = S.current;
  setWinActive(true);
  updateFloatGo();
  if (!relay) watchSevered(hostOf(url));
}

/* 真窗口开完回头看一眼：已经不可操作 = 被 COOP 切断（用户不至于 1.5 秒内自己关掉）。
   记下这个域名，下次它就走跳板窗口，不会再攒第二个关不掉的窗口。 */
function watchSevered(host) {
  setTimeout(() => {
    if (!cmpWinOpen || cmpWinKind !== "native" || cmpControllable()) return;
    rememberSevered(host);
    cmpWinLost = true;
    updateFloatGo();
    toast("⚠ 该站点禁止平台操作弹窗（COOP）：这个窗口关不掉也换不了页，请手动关闭。"
      + "下次点「▶」会自动换成平台可控的对比窗口。", 8000);
  }, 1500);
}

/* 点平台后把官网窗口抬回前面。
   ⚠ focus() 会把**操作系统焦点**从平台夺走：拖拽会被浏览器中止、Ctrl+V 粘到官网去、
   A/D 快捷键失灵。所以只在点了空白/纯阅读区域时才抬升，点任何可操作元素都保持焦点在平台。 */
let _raiseTimer = null;
function cancelWinRaise() { clearTimeout(_raiseTimer); _raiseTimer = null; }
const NO_RAISE = "input,textarea,select,[contenteditable=true],button,a,label,"
  + ".img-grid,.upload-zone,.tree-item,.tree-cat,.company-card,.model-name,#mark-dock,#modal";
document.addEventListener("pointerdown", e => {
  if (!cmpWinOpen || !$("#cmp-float").classList.contains("show")) return;
  if (modalOpen()) return;
  if (e.target instanceof Element && e.target.closest(NO_RAISE)) return;
  clearTimeout(_raiseTimer);
  _raiseTimer = setTimeout(() => { try { if (cmpControllable()) cmpWin.focus(); } catch (e) {} }, 80);
}, true);
// 弹窗打开、拖拽开始都发生在 pointerdown 之后，modal 判断来不及，这里兜底取消
document.addEventListener("dragstart", cancelWinRaise, true);

/* 浮动条可拖动，位置记住 */
(function () {
  const bar = $("#cmp-float");
  let on = false, sx = 0, sy = 0, ox = 0, oy = 0;
  bar.addEventListener("pointerdown", e => {
    if (e.target.closest("button")) return;
    on = true; bar.setPointerCapture(e.pointerId);
    const r = bar.getBoundingClientRect();
    sx = e.clientX; sy = e.clientY; ox = r.left; oy = r.top;
    bar.classList.add("dragging");
  });
  bar.addEventListener("pointermove", e => {
    if (!on) return;
    const x = Math.max(4, Math.min(ox + e.clientX - sx, innerWidth - bar.offsetWidth - 4));
    const y = Math.max(4, Math.min(oy + e.clientY - sy, innerHeight - bar.offsetHeight - 4));
    bar.style.left = x + "px"; bar.style.top = y + "px"; bar.style.right = "auto"; bar.style.bottom = "auto";
  });
  const end = () => {
    if (!on) return;
    on = false; bar.classList.remove("dragging");
    localStorage.setItem("cmpFloatPos", JSON.stringify({ left: bar.style.left, top: bar.style.top }));
  };
  bar.addEventListener("pointerup", end);
  bar.addEventListener("pointercancel", end);
})();

$("#btn-cmp-go").onclick = () => cmpOpen();
$("#btn-float-go").onclick = () => cmpOpen();
$("#btn-float-close").onclick = () => toggleCompare(false);
$("#btn-cmp-close").onclick = () => toggleCompare(false);
$("#btn-float-follow").onclick = () => {
  cmpFollow = !cmpFollow;
  localStorage.setItem("cmpFollow", cmpFollow ? "1" : "0");
  toast(cmpFollow ? "已开启自动跟随：切换产品时对比窗口自动换页" : "已关闭自动跟随：切换产品后自己点「▶」");
  if (cmpFollow) cmpOpen(true);
  updateFloatGo();
};
$("#btn-float-raise").onclick = () => {
  if (!cmpWinOpen) return toast("官网窗口未打开，请先点「▶ 打开产品页」");
  if (!cmpControllable()) return toast("该窗口已脱离平台控制（站点 COOP），只能用任务栏切过去", 5000);
  try { cmpWin.focus(); } catch (e) {}
};
$("#btn-float-squeeze").onclick = () => {
  cmpSqueeze = !cmpSqueeze;
  localStorage.setItem("cmpSqueeze", cmpSqueeze ? "1" : "0");
  setWinActive(true);
  toast(cmpSqueeze ? "已压缩到左半屏" : "已恢复全宽");
};
$("#btn-cmp-mode").onclick = () => {
  cmpMode = cmpMode === "window" ? "iframe" : "window";
  localStorage.setItem("cmpMode", cmpMode);
  closeCmpWin(); setWinActive(false); updateComparePane();
};
$("#btn-cmp-proxy").onclick = () => {
  const url = S.prod && S.prod.url;
  if (!url) return;
  const h = hostOf(url);
  cmpProxyHosts.has(h) ? cmpProxyHosts.delete(h) : cmpProxyHosts.add(h);
  localStorage.setItem("cmpProxyHosts", JSON.stringify([...cmpProxyHosts]));
  if ($("#cmp-frame").style.display === "block") cmpOpen();
};
