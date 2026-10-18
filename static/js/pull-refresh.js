/* ===========================================================================
   pull-refresh.js —— 仅 Android 壳加载（?shell=android）
   ---------------------------------------------------------------------------
   在页面最顶部下拉时显示一个圆形指示器，松手后通知 app.js 重新拉取当前 Tab 的
   数据（事件 campus:refresh）。这样刷新不会整页重载：当前 Tab、聊天记录、语言
   都保持原样。桌面浏览器不加载这个文件，行为完全不变。
   =========================================================================== */
(function () {
  "use strict";

  var screen = document.querySelector(".screen");
  if (!screen) {
    return;
  }

  var THRESHOLD = 64; // 松手时超过这个距离才触发刷新
  var MAX_PULL = 96; // 指示器最多跟手多远
  var SETTLE_MS = 1800; // 兜底：等不到 refresh-done 也要收起

  var indicator = document.createElement("div");
  indicator.className = "pull-indicator";
  indicator.innerHTML = '<span class="pull-arrow">\u21bb</span>';
  document.body.appendChild(indicator);

  var startY = null;
  var distance = 0;
  var busy = false;
  var settleTimer = null;

  function paint(px) {
    indicator.style.transition = "none";
    indicator.style.opacity = String(Math.min(1, px / THRESHOLD));
    indicator.style.transform =
      "translate(-50%, " + px + "px) rotate(" + Math.round(px * 2.4) + "deg)";
    if (px >= THRESHOLD) {
      indicator.classList.add("ready");
    } else {
      indicator.classList.remove("ready");
    }
  }

  function hide() {
    indicator.style.transition = "transform .18s ease, opacity .18s ease";
    indicator.style.transform = "translate(-50%, -48px)";
    indicator.style.opacity = "0";
    indicator.classList.remove("ready", "spinning");
    startY = null;
    distance = 0;
  }

  function finish() {
    if (settleTimer) {
      window.clearTimeout(settleTimer);
      settleTimer = null;
    }
    busy = false;
    hide();
  }

  screen.addEventListener(
    "touchstart",
    function (event) {
      if (busy || event.touches.length !== 1 || screen.scrollTop > 0) {
        return;
      }
      startY = event.touches[0].clientY;
      distance = 0;
    },
    { passive: true }
  );

  screen.addEventListener(
    "touchmove",
    function (event) {
      if (busy || startY === null || event.touches.length !== 1) {
        return;
      }
      var delta = event.touches[0].clientY - startY;
      if (delta <= 0 || screen.scrollTop > 0) {
        hide();
        return;
      }
      // 吃掉默认滚动/回弹，否则手势会先被 WebView 的 overscroll 特效消耗掉
      if (event.cancelable) {
        event.preventDefault();
      }
      distance = Math.min(delta * 0.55, MAX_PULL);
      paint(distance);
    },
    { passive: false }
  );

  function release() {
    if (busy || startY === null) {
      return;
    }
    var triggered = distance >= THRESHOLD;
    startY = null;
    if (!triggered) {
      hide();
      return;
    }
    busy = true;
    distance = 0;
    indicator.classList.add("spinning");
    indicator.style.transition = "transform .18s ease";
    indicator.style.transform = "translate(-50%, " + THRESHOLD + "px)";
    indicator.style.opacity = "1";
    document.dispatchEvent(new Event("campus:refresh"));
    settleTimer = window.setTimeout(finish, SETTLE_MS);
  }

  screen.addEventListener("touchend", release, { passive: true });
  screen.addEventListener(
    "touchcancel",
    function () {
      if (!busy) {
        hide();
      }
    },
    { passive: true }
  );
  document.addEventListener("campus:refresh-done", function () {
    if (busy) {
      window.setTimeout(finish, 120);
    }
  });
})();
