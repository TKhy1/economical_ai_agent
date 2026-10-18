/* ===========================================================================
   android-native.js —— 仅 Android 壳加载
   ---------------------------------------------------------------------------
   底部「设置」Tab 是本 App 唯一的设置入口：其中的「本机功能」卡片通过
   window.CampusNative（WebBridge.java）调用原生能力：

     openAiSettings()   → 原生 AI 后端页（Key / 模型 / 测试连接）
     exportCsv()        → 导出账本 CSV 到 下载/CampusWallet
     seedDemo()         → 灌入演示数据（原生确认框）
     resetData()        → 清空全部数据（原生确认框）
     showDiagnostics()  → 诊断信息（原生对话框）

   桥方法都是"立刻返回、后台干活、结果用原生对话框展示"，所以点按钮不会卡页面。
   桌面浏览器没有这个桥：卡片本身不会被渲染，下面还会兜底移除一次。
   =========================================================================== */
(function () {
  "use strict";

  var card = document.getElementById("native-card");
  if (!card) {
    return;
  }

  var bridge = window.CampusNative;
  if (!bridge) {
    // No native bridge (desktop, or a WebView without the interface): drop the
    // card instead of showing buttons that cannot work.
    card.remove();
    return;
  }

  function bind(id, action) {
    var button = document.getElementById(id);
    if (button) {
      button.addEventListener("click", function () {
        try {
          action();
        } catch (error) {
          console.error("native action failed", error);
        }
      });
    }
  }

  bind("native-ai", function () { bridge.openAiSettings(); });
  bind("native-export", function () { bridge.exportCsv(); });
  bind("native-seed", function () { bridge.seedDemo(); });
  bind("native-reset", function () { bridge.resetData(); });
  bind("native-diagnostics", function () { bridge.showDiagnostics(); });
})();
