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
    const overlay = element("div", "consent-overlay");
    const dialog = element("section", "consent-dialog");
    const title = element("h2", "consent-dialog-title", "使用前確認");
    const message = element(
      "p",
      "consent-dialog-message",
      "您的持倉、策略筆記與分析內容會傳送至您本人已登入的 Claude／Codex CLI 所使用的 AI 服務。分析內容由 AI 產生，不構成投資建議。",
    );
    const confirmButton = element("button", "consent-confirm-btn", "我已了解並同意");

    title.id = "consentDialogTitle";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", title.id);
    confirmButton.type = "button";

    dialog.append(title, message, confirmButton);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);
    document.body.classList.add("consent-modal-open");

    function keepFocusInDialog(event) {
      if (!dialog.contains(event.target)) confirmButton.focus();
    }

    function handleKeydown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation(); // capture phase on document: runs before any page handler
        return;
      }
      if (event.key !== "Tab") return;

      event.preventDefault();
      confirmButton.focus();
    }

    function closeDialog() {
      document.removeEventListener("focusin", keepFocusInDialog);
      document.removeEventListener("keydown", handleKeydown, true);
      document.body.classList.remove("consent-modal-open");
      overlay.remove();
      if (previouslyFocused && previouslyFocused.isConnected && typeof previouslyFocused.focus === "function") {
        previouslyFocused.focus();
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
    document.addEventListener("keydown", handleKeydown, true);
    document.addEventListener("focusin", keepFocusInDialog);
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
