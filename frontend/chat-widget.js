(function mountChatWidget() {
  "use strict";

  const OPEN_STATE_KEY = "stockDashboard.chatWidgetOpen";
  const messages = [];
  let isLoading = true;
  let isBusy = false;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function storedOpenState() {
    try {
      return localStorage.getItem(OPEN_STATE_KEY) === "true";
    } catch (_) {
      return false;
    }
  }

  function storeOpenState(isOpen) {
    try {
      localStorage.setItem(OPEN_STATE_KEY, String(isOpen));
    } catch (_) {
      // The widget still works if storage is unavailable.
    }
  }

  const widget = element("aside", "chat-widget");
  widget.setAttribute("aria-label", "投資助理");

  const bubble = element("button", "chat-widget-bubble", "AI");
  bubble.type = "button";
  bubble.setAttribute("aria-label", "開啟投資助理對話");
  bubble.setAttribute("aria-expanded", "false");

  const panel = element("section", "chat-widget-panel");
  panel.id = "chatWidgetPanel";
  panel.setAttribute("aria-labelledby", "chatWidgetTitle");
  bubble.setAttribute("aria-controls", panel.id);

  const header = element("header", "chat-widget-header");
  const title = element("h2", "chat-widget-title", "問問投資助理");
  title.id = "chatWidgetTitle";
  const headerActions = element("div", "chat-widget-header-actions");
  const clearButton = element("button", "chat-widget-text-btn", "清空對話");
  clearButton.type = "button";
  const collapseButton = element("button", "chat-widget-icon-btn", "−");
  collapseButton.type = "button";
  collapseButton.setAttribute("aria-label", "收合投資助理對話");
  headerActions.append(clearButton, collapseButton);
  header.append(title, headerActions);

  const messageList = element("div", "chat-widget-messages");
  messageList.setAttribute("aria-live", "polite");
  messageList.setAttribute("aria-label", "對話紀錄");

  const form = element("form", "chat-widget-form");
  const inputLabel = element("label", "sr-only", "輸入投資問題");
  inputLabel.htmlFor = "chatWidgetInput";
  const input = element("textarea", "chat-widget-input");
  input.id = "chatWidgetInput";
  input.rows = 2;
  input.maxLength = 2000;
  input.placeholder = "問我：哪幾檔現在比較值得留？";
  input.required = true;
  const sendButton = element("button", "chat-widget-send-btn", "送出");
  sendButton.type = "submit";
  form.append(inputLabel, input, sendButton);

  panel.append(header, messageList, form);
  widget.append(bubble, panel);
  document.body.appendChild(widget);

  function setOpen(isOpen, focusInput = true) {
    panel.hidden = !isOpen;
    bubble.hidden = isOpen;
    bubble.setAttribute("aria-expanded", String(isOpen));
    storeOpenState(isOpen);

    if (focusInput && isOpen && !input.disabled) input.focus();
    if (focusInput && !isOpen) bubble.focus();
  }

  function renderMessages() {
    messageList.replaceChildren();

    if (isLoading) {
      messageList.appendChild(element("div", "chat-widget-empty", "載入對話中…"));
      return;
    }

    if (!messages.length) {
      messageList.appendChild(element(
        "div",
        "chat-widget-empty",
        "可以問：「哪幾檔現在比較值得留？」或比較各檔的技術面、估值與新聞。",
      ));
      return;
    }

    messages.forEach(message => {
      const bubbleNode = element("div", `chat-widget-message ${message.role}${message.pending ? " pending" : ""}`);
      bubbleNode.textContent = message.text;
      messageList.appendChild(bubbleNode);
    });
    messageList.scrollTop = messageList.scrollHeight;
  }

  function setBusy(busy) {
    isBusy = busy;
    input.disabled = busy || isLoading;
    sendButton.disabled = busy || isLoading;
    clearButton.disabled = busy || isLoading;
    sendButton.textContent = busy ? "思考中…" : "送出";
  }

  async function responseError(response) {
    try {
      const data = await response.json();
      return data.detail || data.error || `伺服器回傳 ${response.status}`;
    } catch (_) {
      return `伺服器回傳 ${response.status}`;
    }
  }

  async function loadHistory() {
    setBusy(false);
    renderMessages();

    try {
      const response = await fetch("/api/chat/history");
      if (!response.ok) throw new Error(await responseError(response));
      const data = await response.json();
      if (!data || !Array.isArray(data.messages)) throw new Error("對話紀錄格式不正確");

      messages.length = 0;
      data.messages.forEach(message => {
        if ((message.role === "user" || message.role === "assistant") && typeof message.text === "string") {
          messages.push({ role: message.role, text: message.text });
        }
      });
    } catch (error) {
      messages.length = 0;
      messages.push({ role: "error", text: `⚠️ 無法載入對話：${error.message || "網路連線異常"}` });
    } finally {
      isLoading = false;
      setBusy(false);
      renderMessages();
    }
  }

  bubble.addEventListener("click", () => setOpen(true));
  collapseButton.addEventListener("click", () => setOpen(false));

  input.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  form.addEventListener("submit", async event => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message || isBusy || isLoading) return;

    messages.push({ role: "user", text: message });
    const pending = { role: "assistant", text: "思考中…", pending: true };
    messages.push(pending);
    input.value = "";
    setBusy(true);
    renderMessages();

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const data = await response.json();
      if (typeof data.reply !== "string" || !data.reply.trim()) throw new Error("回覆內容是空的");
      pending.text = data.reply.trim();
      pending.pending = false;
    } catch (error) {
      pending.role = "error";
      pending.text = `⚠️ 查詢失敗：${error.message || "網路連線異常"}`;
      pending.pending = false;
    } finally {
      setBusy(false);
      renderMessages();
      input.focus();
    }
  });

  clearButton.addEventListener("click", async () => {
    if (isBusy || isLoading || !confirm("確定要清空全部對話紀錄嗎？")) return;

    setBusy(true);
    try {
      const response = await fetch("/api/chat/history", { method: "DELETE" });
      if (!response.ok) throw new Error(await responseError(response));
      messages.length = 0;
    } catch (error) {
      messages.push({ role: "error", text: `⚠️ 清空失敗：${error.message || "網路連線異常"}` });
    } finally {
      setBusy(false);
      renderMessages();
      input.focus();
    }
  });

  setOpen(storedOpenState(), false);
  loadHistory();
})();
