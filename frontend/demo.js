(function setupDemoMode() {
  "use strict";

  const DEMO_MESSAGE = "示範模式：以下全是合成資料（示範公司 A–E），不是真實股票；AI 分析、聊天、更新與策略檢查已停用。";
  const DISABLED_MESSAGE = "示範模式：此功能需要你登入的 AI CLI 或網路資料，已停用";
  const DESCRIPTION_ID = "sdDemoDisabledDescription";
  let infoPromise;

  function domReady() {
    if (document.body) return Promise.resolve();
    return new Promise(resolve => document.addEventListener("DOMContentLoaded", resolve, { once: true }));
  }

  async function showDemoUi(info) {
    await domReady();
    document.documentElement.dataset.demo = "1";

    if (!document.getElementById(DESCRIPTION_ID)) {
      const description = document.createElement("span");
      description.id = DESCRIPTION_ID;
      description.className = "sr-only";
      description.textContent = DISABLED_MESSAGE;
      document.body.appendChild(description);
    }

    if (document.getElementById("sdDemoBanner")) return;
    const banner = document.createElement("aside");
    banner.id = "sdDemoBanner";
    banner.className = "demo-banner";
    banner.setAttribute("role", "status");
    banner.setAttribute("aria-label", "示範模式提醒");
    if (info.version) banner.dataset.version = String(info.version);

    const message = document.createElement("span");
    message.textContent = DEMO_MESSAGE;
    const readme = document.createElement("a");
    readme.href = "https://github.com/gary2376/stock-dashboard#readme";
    readme.target = "_blank";
    readme.rel = "noopener noreferrer";
    readme.textContent = "GitHub README";
    banner.append(message, document.createTextNode(" "), readme);
    document.body.prepend(banner);
  }

  function loadInfo() {
    if (!infoPromise) {
      infoPromise = (async () => {
        try {
          const response = await fetch("/api/info");
          if (!response.ok) return { demo: false, version: "" };
          const data = await response.json();
          const info = {
            demo: data?.demo === true,
            version: typeof data?.version === "string" ? data.version : "",
          };
          if (info.demo) await showDemoUi(info);
          return info;
        } catch (_) {
          return { demo: false, version: "" };
        }
      })();
    }
    return infoPromise;
  }

  window.sdDemo = {
    isDemo() {
      return loadInfo().then(info => info.demo);
    },
    onReady(callback) {
      if (typeof callback !== "function") return;
      loadInfo().then(info => {
        try {
          callback(info.demo, info);
        } catch (_) {
          // A page callback must not break the shared demo-mode bootstrap.
        }
      });
    },
  };

  loadInfo();
})();
