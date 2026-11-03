/* ============================================================
   主题层脚本
   规则：先把 data-theme 打上（防闪白），再挂切换器。
   主题优先级 = URL 参数 > 本地记忆 > 默认处方笺。
   ============================================================ */

(function () {
  "use strict";

  var THEMES = [
    { id: "rx",      name: "处方笺", note: "暖纸 · 印泥红", paper: "#f2ece0", accent: "#a6341f" },
    { id: "herb",    name: "药房",   note: "米灰 · 松墨绿", paper: "#eae9dc", accent: "#1e5c42" },
    { id: "codex",   name: "医典",   note: "冷白 · 藏青章", paper: "#eceff2", accent: "#1d4e79" },
    { id: "night",   name: "值守",   note: "墨底 · 冷青",   paper: "#12161a", accent: "#5cb2d8" },
    { id: "sterile", name: "无菌",   note: "近白 · 医用青", paper: "#f4f6f6", accent: "#0c6e68" }
  ];

  var KEY = "xiaoxiao.theme";
  var root = document.documentElement;

  function normalize(id) {
    for (var i = 0; i < THEMES.length; i++) if (THEMES[i].id === id) return id;
    return null;
  }

  var params = new URLSearchParams(location.search);
  var fromUrl = normalize(params.get("theme"));
  var saved = null;
  try { saved = normalize(localStorage.getItem(KEY)); } catch (e) { /* 隐私模式 */ }

  var current = fromUrl || saved || "rx";

  function apply(id, persist) {
    current = id;
    root.setAttribute("data-theme", id);
    if (persist !== false) {
      try { localStorage.setItem(KEY, id); } catch (e) { /* 忽略 */ }
    }
    syncButtons();
  }

  /* embed=1 是选样台的预览，只看不记 */
  apply(current, params.get("embed") !== "1");

  /* —— 嵌入预览（choose.html 的 iframe）不挂切换器 —— */
  if (params.get("embed") === "1") return;

  /* —— 切换器 —— */
  var ther = document.createElement("div");
  ther.className = "themer";
  ther.setAttribute("data-open", "false");

  var toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "themer__toggle";
  toggle.setAttribute("aria-expanded", "false");
  toggle.setAttribute("aria-label", "切换视觉主题");
  toggle.innerHTML = '<span class="themer__dot"></span><span>主题</span>';

  var panel = document.createElement("div");
  panel.className = "themer__panel";
  panel.setAttribute("role", "group");
  panel.setAttribute("aria-label", "可选主题");

  var hd = document.createElement("div");
  hd.className = "themer__hd";
  hd.textContent = "五套纸质 · 内容是同一份";
  panel.appendChild(hd);

  THEMES.forEach(function (t, i) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "themer__opt";
    b.setAttribute("data-theme-id", t.id);
    b.setAttribute("aria-pressed", "false");
    b.innerHTML =
      '<span class="themer__chip" style="background:' + t.paper +
      ';--chip-accent:' + t.accent + '"></span>' +
      '<span>' + t.name + '<br><span style="color:var(--muted);font-size:11px">' +
      t.note + '</span></span>' +
      '<span class="themer__no">0' + (i + 1) + '</span>';
    b.addEventListener("click", function () { apply(t.id); });
    panel.appendChild(b);
  });

  var more = document.createElement("a");
  more.className = "themer__more";
  more.href = "choose.html" + location.search;   /* 部署后要把 sharecode 带过去 */
  more.textContent = "看完整选样台 ↗";
  panel.appendChild(more);

  ther.appendChild(panel);
  ther.appendChild(toggle);

  function syncButtons() {
    if (!panel || !toggle) return;   /* 切换器尚未构建，等挂载后再同步 */
    var opts = panel.querySelectorAll(".themer__opt");
    for (var i = 0; i < opts.length; i++) {
      opts[i].setAttribute(
        "aria-pressed",
        opts[i].getAttribute("data-theme-id") === current ? "true" : "false"
      );
    }
    var meta = null;
    for (var j = 0; j < THEMES.length; j++) if (THEMES[j].id === current) meta = THEMES[j];
    if (meta) toggle.querySelector(".themer__dot").style.background = meta.accent;
  }

  toggle.addEventListener("click", function () {
    var open = ther.getAttribute("data-open") === "true";
    ther.setAttribute("data-open", open ? "false" : "true");
    toggle.setAttribute("aria-expanded", open ? "false" : "true");
  });

  document.addEventListener("click", function (e) {
    if (!ther.contains(e.target)) {
      ther.setAttribute("data-open", "false");
      toggle.setAttribute("aria-expanded", "false");
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      ther.setAttribute("data-open", "false");
      toggle.setAttribute("aria-expanded", "false");
    }
  });

  /* —— 脚本在 head 里跑，挂载要等 body 存在 —— */
  function mount() {
    document.body.appendChild(ther);
    syncButtons();
  }
  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);

  /* —— 供 choose.html 等外部页读取当前主题 —— */
  window.xiaoxiaoTheme = {
    list: THEMES,
    get: function () { return current; },
    set: apply
  };
})();
