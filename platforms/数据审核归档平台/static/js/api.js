/* 网络层 + 全局状态。所有请求都从这里走。 */
"use strict";

const FRONT_VERSION = "8";   // 与 app/main.py 的 APP_VERSION 一致

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* 全局状态。刻意只有一份，避免"两处状态各说各话"。 */
const S = {
  companies: [], stage: localStorage.getItem("stage") || "",
  company: null, products: [], summary: null, eligibility: null,
  current: null, prod: null, record: null,
  readonly: false, filter: "", expanded: new Set(),
};

async function api(url, opts = {}) {
  if (opts.json) {
    opts.body = JSON.stringify(opts.json);
    opts.headers = { "Content-Type": "application/json" };
    delete opts.json;
  }
  let r;
  try {
    r = await fetch(url, opts);
  } catch (e) {
    // fetch 只在"根本没拿到响应"时抛。浏览器原文只有 Failed to fetch，
    // 对着它排查是浪费时间，这里把可能性说清楚。
    throw new Error(`连不上后端（${e.message}）——服务可能没在运行或正在重启；`
      + `写操作有可能已经在后台完成，请刷新页面确认后再重试`);
  }
  // 会话过期：直接送回登录页。不这么做的话，界面会把 401 当普通报错弹一堆
  // "加载失败"，人对着一个还在的页面反复点，却怎么点都不动。
  if (r.status === 401) {
    location.href = "/login";
    throw new Error("登录已过期，正在跳转登录页…");
  }
  if (!r.ok) {
    let msg = `HTTP ${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch (e) { /* 响应体不是 JSON，保留状态码文案 */ }
    throw new Error(msg);
  }
  return (r.headers.get("content-type") || "").includes("json") ? r.json() : r.text();
}

function fileUrl(relPath) {
  // encodeURI 不编码 # ? &，文件名带这些字符会被截断成 404，必须逐段编码
  return "/files/" + relPath.split("/").map(encodeURIComponent).join("/") + "?t=" + Date.now();
}

function readAsDataURL(file) {
  return new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(fr.result);
    fr.onerror = rej;
    fr.readAsDataURL(file);
  });
}

/* 版本自检：横幅必须能自己消失，并写清是哪一边旧了。
   上一代只在加载时查一次，重启服务那几秒刷新页面就会挂上一个永不消失的横幅。 */
let _verTimer = null;
async function checkVersion() {
  let state, back = "?";
  try {
    const v = await api("/api/version");
    back = v.version;
    state = back === FRONT_VERSION ? "ok"
      : (Number(back) > Number(FRONT_VERSION) ? "stale-front" : "stale-back");
  } catch (e) { state = "down"; }
  if (state === "ok") {
    $("#ver-banner")?.remove();
    clearInterval(_verTimer); _verTimer = null;
    return;
  }
  let b = $("#ver-banner");
  if (!b) { b = document.createElement("div"); b.id = "ver-banner"; document.body.prepend(b); }
  const ver = `（浏览器前端 v${FRONT_VERSION} / 后端 v${back}）`;
  b.innerHTML = state === "down"
    ? `⚠ 连不上后端服务（可能正在重启）——确认终端里 <b>python3 run.py</b> 在运行；恢复后本提示自动消失`
    : state === "stale-front"
      ? `⚠ 浏览器用的是<b>缓存里的旧前端</b>${ver} —— 重启服务没用，请按 <b>Ctrl+F5</b> 强制刷新`
      : `⚠ 后端跑的是<b>旧版本代码</b>${ver} —— 终端 Ctrl+C 后重新 <b>python3 run.py</b>`;
  if (!_verTimer) _verTimer = setInterval(checkVersion, 3000);
}
