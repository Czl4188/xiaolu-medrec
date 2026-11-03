/* ============================================================
   落地页脚本：入场编排 + 锚点滚动
   只做两件事，动效全部可降级。
   ============================================================ */

(function () {
  "use strict";

  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* —— 入场：进入视口 12% 时整组揭示，错峰由 CSS 的 transition-delay 负责 —— */
  var targets = document.querySelectorAll(".reveal");

  if (reduce || !("IntersectionObserver" in window)) {
    targets.forEach(function (el) { el.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("in");
        io.unobserve(entry.target);
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -8% 0px" });

    targets.forEach(function (el) { io.observe(el); });
  }

  /* —— 锚点：默认瞬时跳转是浏览器行为，这里换成缓动，减少动效时保持瞬时 —— */
  if (!reduce) {
    document.querySelectorAll('a[href^="#"]').forEach(function (a) {
      a.addEventListener("click", function (e) {
        var id = a.getAttribute("href");
        if (id.length < 2) return;
        var el = document.querySelector(id);
        if (!el) return;
        e.preventDefault();
        var top = el.getBoundingClientRect().top + window.pageYOffset - 72;
        window.scrollTo({ top: top, behavior: "smooth" });
      });
    });
  }
})();
