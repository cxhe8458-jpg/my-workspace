/* 图标系统 —— 内联 SVG，24 网格 / 1.5-1.6 描边 / currentColor。
   零外部请求（离线可用），随文字色走，尺寸统一。全站不再使用 emoji：
   emoji 在 Windows / macOS / 无字体的环境下渲染完全不同，做不了可靠的界面元素。

   用法：ic("overview")            → 16px
        ic("refresh", 13)         → 13px
        ic("check", 16, 2)        → 指定描边
   状态标记另见 MK（形状先于颜色，见 chipOf）。 */

const ICON_PATHS = {
  /* 导航 */
  overview:'M4 5h6v6H4zM14 5h6v4h-6zM14 13h6v6h-6zM4 15h6v4H4z',
  pipeline:'M4 7h5l3 5 3-5h5M4 17h16',
  todo:    'M4 13h4l2 3h4l2-3h4M4 13l2.5-7h11L20 13v5H4z',
  taxonomy:'M12 4v4M12 8H6v4M12 8h6v4M4 12h4v4H4zM10 12h4v4h-4zM16 12h4v4h-4z',
  stats:   'M4 19V9M10 19V5M16 19v-7M22 19H2',
  gear:    'M12 9a3 3 0 100 6 3 3 0 000-6zM4 12h2M18 12h2M12 4v2M12 18v2M6.7 6.7l1.4 1.4M15.9 15.9l1.4 1.4M17.3 6.7l-1.4 1.4M8.1 15.9l-1.4 1.4',
  clock:   'M12 21a9 9 0 100-18 9 9 0 000 18zM12 7v5l3 2',

  /* 流水线阶段与动作 */
  box:     'M4 8l8-4 8 4v8l-8 4-8-4zM4 8l8 4 8-4M12 12v8',
  map:     'M9 4L3 6v14l6-2 6 2 6-2V4l-6 2-6-2zM9 4v14M15 6v14',
  broom:   'M4 20l5-5M9 15l6-6 4 4-6 6zM13 5l6 6M11 7l6 6',
  shield:  'M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z',
  tag:     'M4 12V4h8l8 8-8 8z M8 8h.01',
  hand:    'M9 11V5.5a1.5 1.5 0 013 0V11m0-1.5a1.5 1.5 0 013 0V12m0-1a1.5 1.5 0 013 0v4a5 5 0 01-5 5h-2a5 5 0 01-5-5v-5a1.5 1.5 0 013 0',
  bot:     'M8 8h8a2 2 0 012 2v6a2 2 0 01-2 2H8a2 2 0 01-2-2v-6a2 2 0 012-2zM12 5v3M9 13h.01M15 13h.01',
  play:    'M8 5l11 7-11 7z',
  refresh: 'M20 12a8 8 0 11-2.3-5.6M20 4v5h-5',
  save:    'M5 4h11l3 3v13H5zM8 4v6h7V4M8 15h8',

  /* 界面 */
  search:  'M11 18a7 7 0 100-14 7 7 0 000 14zM20 20l-4-4',
  chev:    'M9 6l6 6-6 6',
  chevdown:'M6 9l6 6 6-6',
  back:    'M15 6l-6 6 6 6',
  check:   'M4 12.5l5 5L20 6.5',
  x:       'M6 6l12 12M18 6L6 18',
  plus:    'M12 5v14M5 12h14',
  info:    'M12 21a9 9 0 100-18 9 9 0 000 18zM12 11v5M12 8h.01',
  alert:   'M12 3l9 16H3zM12 9v4M12 16.5h.01',
  trash:   'M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13M10 11v6M14 11v6',
  download:'M12 4v11M8 11l4 4 4-4M5 20h14',
  external:'M14 5h5v5M19 5l-8 8M18 14v5H5V6h5',
  link:    'M10 14a4 4 0 005.7 0l3-3a4 4 0 10-5.7-5.7L11.5 7M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 105.7 5.7L12.5 17',
  folder:  'M3 7h6l2 2h10v10H3z',
  file:    'M6 3h8l4 4v14H6zM14 3v4h4',
  filter:  'M4 5h16l-6 7v6l-4 2v-8z',
  flag:    'M5 21V5m0 0h11l-2 3.5L16 12H5',
  spark:   'M13 3L5 14h6l-1 7 8-11h-6l1-7z',
  key:     'M14 10a4 4 0 10-4 4l1 1 2-1 1 1 2-1v-2zM15.5 8.5h.01',
  book:    'M5 4h10a3 3 0 013 3v13H8a3 3 0 01-3-3zM8 20a3 3 0 01-3-3',
};

function ic(name, size = 16, w = 1.6) {
  const d = ICON_PATHS[name];
  if (!d) return "";
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;
}

/* 状态标记 —— 形状先于颜色：实心=完成 / 半实=有更新 / 空心=未处理 / 三角=需处理。
   任何色盲、任何打印、任何强制高对比模式下都还能区分。 */
const MK = {
  done: '<svg width="11" height="11" viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="5" fill="currentColor"/></svg>',
  stale:'<svg width="11" height="11" viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="4.4" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M6 1.6A4.4 4.4 0 016 10.4z" fill="currentColor"/></svg>',
  idle: '<svg width="11" height="11" viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="4.4" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>',
  warn: '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M6 1.4l5 8.6H1z" fill="currentColor"/><path d="M6 4.6v2.2" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/><circle cx="6" cy="8.4" r=".7" fill="#fff"/></svg>',
};

/* chip("ok","done","已完成") → 带形状+文字的状态芯片 */
function chip(kind, mark, text, bare = false) {
  return `<span class="chip ${kind}${bare ? " bare" : ""}">${MK[mark] || ""}${text}</span>`;
}
