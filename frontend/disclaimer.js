(function mountDisclaimerAndConsent() {
  "use strict";
  if (window.__sdDisclaimerMounted) return; // loaded twice: never duplicate the footer or the focus guards
  window.__sdDisclaimerMounted = true;

  const CONSENT_KEY = "sd_consent_v1";
  const DISCLAIMER_TEXT = "本工具為個人研究輔助軟體，由 AI 依公開資料自動產生分析，不構成任何投資建議或買賣邀約，也不保證獲利。分析可能出錯、過時或自相矛盾；資料來自第三方，可能延遲或不完整。所有投資決策與風險由使用者自行承擔。";

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function hasConsent() {
    try {
      return localStorage.getItem(CONSENT_KEY) === "1";
    } catch (_) {
      return false;
    }
  }

  function mountFooter() {
    const footer = element("footer", "site-disclaimer");
    const content = element("div", "site-disclaimer-content");
    const disclaimer = element("p", "site-disclaimer-text", DISCLAIMER_TEXT);
    const attribution = element("a", "site-disclaimer-attribution", "Charts by TradingView Lightweight Charts");
    attribution.href = "https://www.tradingview.com/";
    attribution.target = "_blank";
    attribution.rel = "noopener noreferrer";
    content.append(disclaimer, attribution);
    footer.appendChild(content);
    document.body.appendChild(footer);
  }

  function showConsentDialog() {
    const previouslyFocused = document.activeElement;
    const dialog = element("dialog", "consent-dialog");
    const title = element("h2", "consent-dialog-title", "使用前確認");
    const message = element(
      "p",
      "consent-dialog-message",
      "您的持倉、策略筆記與分析內容會傳送至您自己設定的 AI（已登入的 Claude／Codex CLI，或您設定的其他 AI 服務；使用雲端 API 時資料會送到該供應商，本機模型則不會離開您的電腦）。分析內容由 AI 產生，不構成投資建議。",
    );
    const confirmButton = element("button", "consent-confirm-btn", "我已了解並同意");

    title.id = "consentDialogTitle";
    dialog.setAttribute("aria-labelledby", title.id);
    confirmButton.type = "button";
    confirmButton.autofocus = true;

    dialog.append(title, message, confirmButton);
    document.body.appendChild(dialog);
    document.body.classList.add("consent-modal-open");

    const supportsModalDialog = typeof dialog.showModal === "function";

    function keepFocusInDialog(event) {
      if (!dialog.contains(event.target)) confirmButton.focus();
    }

    function handleKeydown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      if (event.key !== "Tab") return;

      event.preventDefault();
      confirmButton.focus();
    }

    function cleanup() {
      document.removeEventListener("focusin", keepFocusInDialog);
      document.removeEventListener("keydown", handleKeydown, true);
      document.body.classList.remove("consent-modal-open");
      dialog.remove();
      if (previouslyFocused && previouslyFocused.isConnected && typeof previouslyFocused.focus === "function") {
        previouslyFocused.focus();
      }
    }

    function closeDialog() {
      if (supportsModalDialog) dialog.close();
      else {
        dialog.removeAttribute("open");
        cleanup();
      }
    }

    confirmButton.addEventListener("click", () => {
      try {
        localStorage.setItem(CONSENT_KEY, "1");
      } catch (_) {
        // If storage is unavailable, consent is requested again on the next page load.
      }
      closeDialog();
    });
    dialog.addEventListener("cancel", event => event.preventDefault());
    dialog.addEventListener("close", cleanup, { once: true });

    if (supportsModalDialog) {
      dialog.showModal();
      // one focusable control: keep Tab/Shift+Tab on it instead of leaving the page for the browser UI
      dialog.addEventListener("keydown", event => {
        if (event.key === "Tab") {
          event.preventDefault();
          confirmButton.focus();
        }
      });
    } else {
      dialog.setAttribute("role", "dialog");
      dialog.setAttribute("aria-modal", "true");
      dialog.classList.add("consent-dialog-fallback");
      dialog.setAttribute("open", "");
      document.addEventListener("keydown", handleKeydown, true);
      document.addEventListener("focusin", keepFocusInDialog);
    }
    confirmButton.focus();
  }

  function init() {
    mountFooter();
    if (!hasConsent()) showConsentDialog();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
