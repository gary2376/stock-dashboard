(function strategyNotesPage() {
  "use strict";

  const usageEl = document.getElementById("strategyUsage");
  const statusEl = document.getElementById("strategyStatus");
  const fileListEl = document.getElementById("strategyFileList");
  const fileInput = document.getElementById("strategyFileInput");
  const uploadButton = document.getElementById("uploadStrategyBtn");
  const pasteForm = document.getElementById("strategyPasteForm");
  const viewer = document.getElementById("strategyViewer");
  const viewerTitle = document.getElementById("strategyViewerTitle");
  const viewerContent = document.getElementById("strategyViewerContent");
  const fileListHeading = document.getElementById("strategyFilesTitle");
  let lastViewerButton = null;
  let fileListGeneration = 0;
  let strategySnapshot = null;
  const pinsListEl = document.getElementById("pinsList");
  const pinsStatusEl = document.getElementById("pinsStatus");
  const pinsUsageEl = document.getElementById("pinsUsage");
  const pinTextInput = document.getElementById("pinText");
  const pinScopeInput = document.getElementById("pinScope");
  const pinCreateForm = document.getElementById("pinCreateForm");
  let pinsRevision = 0;
  let pinsLimits = {};
  let pinsGeneration = 0;
  const compilingPins = new Set();
  const pendingPinMutations = new Map();
  let pinMutationQueue = Promise.resolve();
  let activeCompileController = null;
  let currentPins = [];

  const PIN_SCOPE_LABELS = { now: "現在", entry: "買入當時", both: "兩者" };
  const PIN_STATE_LABELS = {
    none: "未編譯",
    draft: "待確認",
    confirmed: "已確認",
    stale: "已過期",
    unsupported: "無法程式判斷",
  };
  const UNSUPPORTED_REASON_LABELS = {
    needs_event_calendar: "需要財報日期資料",
    needs_news: "需要新聞",
    needs_notes: "需要筆記判斷",
    qualitative: "屬主觀判斷",
    other: "其他原因",
  };

  function pinNode(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function pinState(pin) {
    const state = pin?.compiled?.state;
    return Object.prototype.hasOwnProperty.call(PIN_STATE_LABELS, state) ? state : "none";
  }

  function pinUnsupportedReason(reason) {
    if (!reason) return "其他原因";
    return UNSUPPORTED_REASON_LABELS[reason] || "其他原因";
  }

  function showPinsStatus(message, isError = false) {
    pinsStatusEl.textContent = message;
    pinsStatusEl.classList.toggle("error", isError);
  }

  class PinRequestError extends Error {
    constructor(status, detail) {
      super(detail);
      this.name = "PinRequestError";
      this.status = status;
      this.detail = detail;
    }
  }

  async function pinRequest(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) throw new PinRequestError(response.status, await responseDetail(response));
    if (response.status === 204) return {};
    return response.json();
  }

  function pinById(id) {
    return currentPins.find(pin => String(pin.id) === String(id));
  }

  function pendingPinCount(id) {
    return pendingPinMutations.get(String(id)) || 0;
  }

  function setPinControlsDisabled(id, disabled) {
    const row = Array.from(pinsListEl.querySelectorAll(".pin-row"))
      .find(candidate => candidate.dataset.pinId === String(id));
    if (!row) return;
    row.setAttribute("aria-busy", String(disabled));
    row.querySelectorAll("button, input, select").forEach(control => { control.disabled = disabled; });
  }

  function markPinMutation(id, delta, focusTarget = null) {
    if (id === null || id === undefined) return;
    const key = String(id);
    const next = Math.max(0, pendingPinCount(key) + delta);
    if (next) pendingPinMutations.set(key, next);
    else pendingPinMutations.delete(key);
    if (next) setPinControlsDisabled(key, true);
    else renderPins(pinSnapshot(), focusTarget);
  }

  function queuePinMutation(id, mutation, focusTarget = null) {
    markPinMutation(id, 1);
    const run = pinMutationQueue.then(mutation);
    const settled = run.finally(() => markPinMutation(id, -1, focusTarget));
    pinMutationQueue = settled.catch(() => {});
    return settled;
  }

  function focusedPinControl() {
    const active = document.activeElement;
    const row = active?.closest?.(".pin-row[data-pin-id]");
    const action = active?.dataset?.pinAction;
    return row && action ? { id: row.dataset.pinId, action } : null;
  }

  function restorePinFocus(target) {
    if (!target) return;
    const row = Array.from(pinsListEl.querySelectorAll(".pin-row"))
      .find(candidate => candidate.dataset.pinId === target.id);
    const control = row && Array.from(row.querySelectorAll("[data-pin-action]"))
      .find(candidate => candidate.dataset.pinAction === target.action);
    if (control) control.focus({ preventScroll: true });
  }

  function pinSnapshot() {
    return { revision: pinsRevision, pins: currentPins, limits: pinsLimits };
  }

  function adoptPinResponse(data, options = {}) {
    if (!data || typeof data !== "object") return;
    if (Number.isFinite(Number(data.revision))) pinsRevision = Number(data.revision);
    if (data.limits && typeof data.limits === "object") pinsLimits = data.limits;
    if (Array.isArray(data.pins)) {
      currentPins = data.pins.filter(pin => pin && typeof pin === "object");
    } else if (data.pin && typeof data.pin === "object") {
      const index = currentPins.findIndex(pin => String(pin.id) === String(data.pin.id));
      if (index >= 0) currentPins = currentPins.map((pin, pinIndex) => pinIndex === index ? data.pin : pin);
      else currentPins = [...currentPins, data.pin];
    }
    if (options.deletedId !== undefined) {
      currentPins = currentPins.filter(pin => String(pin.id) !== String(options.deletedId));
    }
    ++pinsGeneration;
    renderPins(pinSnapshot(), options.focusTarget);
  }

  function isCompileConflict(detail) {
    return /(compile|compiling|編譯|另一個)/i.test(String(detail || ""));
  }

  function isRevisionConflict(detail) {
    return /(revision|changed elsewhere|reload)/i.test(String(detail || ""));
  }

  async function handlePinConflict(error) {
    if (!(error instanceof PinRequestError) || error.status !== 409) return false;
    if (isCompileConflict(error.detail)) {
      showPinsStatus(error.detail, true);
      return true;
    }
    if (isRevisionConflict(error.detail)) {
      await loadPins();
      showPinsStatus("清單已在別處更新，已重新載入", true);
      return true;
    }
    showPinsStatus(error.detail, true);
    return true;
  }

  function scopeSelect(pin, disabled) {
    const select = document.createElement("select");
    select.className = "text-input pin-scope-select";
    select.dataset.pinAction = "scope";
    select.setAttribute("aria-label", `「${pin.text || "未命名規則"}」的檢查時間`);
    Object.entries(PIN_SCOPE_LABELS).forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      option.selected = pin.scope === value;
      select.appendChild(option);
    });
    select.disabled = disabled;
    select.addEventListener("change", async () => {
      await updatePin(pin.id, { scope: select.value });
    });
    return select;
  }

  function renderCompiledScope(container, scope, compiledScope) {
    if (!compiledScope || typeof compiledScope !== "object") return;
    const block = pinNode("section", "pin-review-scope");
    block.appendChild(pinNode("h4", "", PIN_SCOPE_LABELS[scope] || scope));
    const restatement = pinNode("p", "pin-restatement", compiledScope.restatement || "尚未提供白話解讀。");
    restatement.prepend(pinNode("strong", "", "符合的意思："));
    block.appendChild(restatement);
    const expression = pinNode("code", "pin-expression", compiledScope.expression || "—");
    block.appendChild(expression);
    const facts = Array.isArray(compiledScope.facts) ? compiledScope.facts.map(String) : [];
    block.appendChild(pinNode("p", "pin-review-facts", `使用事實：${facts.length ? facts.join("、") : "未列出"}`));
    container.appendChild(block);
  }

  function renderReview(pin, state, busy) {
    if (!pin.compiled || !["draft", "confirmed", "stale"].includes(state)) return null;
    const review = pinNode("div", "pin-review");
    const source = pin.compiled.source === "template" ? "本地範本" : pin.compiled.source === "llm" ? "AI 協助編譯" : "未知來源";
    review.appendChild(pinNode("p", "pin-review-source", `編譯來源：${source}`));
    if (pin.compiled.source === "llm") {
      review.appendChild(pinNode("p", "pin-ai-note", "AI 編譯時，釘選文字與可用事實清單會傳送至你本人已登入的 AI CLI。"));
    }
    const byScope = pin.compiled.by_scope && typeof pin.compiled.by_scope === "object" ? pin.compiled.by_scope : {};
    ["now", "entry"].forEach(scope => renderCompiledScope(review, scope, byScope[scope]));
    if (state === "draft") {
      const confirmButton = pinNode("button", "action-btn pin-confirm-btn", "確認這個解讀");
      confirmButton.type = "button";
      confirmButton.disabled = busy;
      confirmButton.dataset.pinAction = "confirm";
      confirmButton.setAttribute("aria-label", `確認「${pin.text || "未命名規則"}」的編譯解讀`);
      confirmButton.addEventListener("click", () => confirmPin(pin.id));
      review.appendChild(confirmButton);
    }
    return review;
  }

  function makePinRow(pin, index, total) {
    const state = pinState(pin);
    const compiling = compilingPins.has(String(pin.id));
    const busy = pendingPinCount(pin.id) > 0;
    const article = pinNode("article", "pin-row");
    article.dataset.pinId = String(pin.id);
    article.setAttribute("aria-busy", String(busy));

    const main = pinNode("div", "pin-row-main");
    const textInput = document.createElement("input");
    textInput.type = "text";
    textInput.className = "text-input pin-text-edit";
    textInput.dataset.pinAction = "text";
    textInput.value = String(pin.text || "");
    textInput.maxLength = Number(pinsLimits.max_text) > 0 ? Number(pinsLimits.max_text) : 1000;
    textInput.disabled = busy;
    textInput.setAttribute("aria-label", `編輯第 ${index + 1} 條釘選規則`);
    textInput.addEventListener("change", () => {
      const text = textInput.value.trim();
      if (!text) {
        textInput.value = String(pin.text || "");
        showPinsStatus("規則文字不可空白。", true);
        return;
      }
      if (text !== pin.text) updatePin(pin.id, { text });
    });
    main.appendChild(textInput);

    const controls = pinNode("div", "pin-row-controls");
    const enabledLabel = pinNode("label", "pin-enabled-toggle");
    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.dataset.pinAction = "enabled";
    enabled.checked = state === "confirmed" && pin.enabled !== false;
    enabled.disabled = busy || state !== "confirmed";
    enabled.setAttribute("aria-label", `啟用「${pin.text || "未命名規則"}」`);
    enabled.addEventListener("change", () => updatePin(pin.id, { enabled: enabled.checked }));
    enabledLabel.append(enabled, document.createTextNode("啟用"));
    controls.append(enabledLabel, scopeSelect(pin, busy));

    const badge = pinNode("span", `pin-state-badge pin-state-${state}`, PIN_STATE_LABELS[state]);
    controls.appendChild(badge);
    if (state === "unsupported") {
      controls.appendChild(pinNode("span", "pin-unsupported-reason", pinUnsupportedReason(pin.compiled?.unsupported_reason)));
    }
    if (state === "stale") controls.appendChild(pinNode("span", "pin-unsupported-reason", "待重新編譯"));
    main.appendChild(controls);
    article.appendChild(main);

    if (compiling) article.appendChild(pinNode("p", "pin-compiling", "AI 編譯中，最多約 3 分鐘…"));

    const actions = pinNode("div", "pin-row-actions");
    const actionSpecs = [
      ["編譯", "compile", "", () => compilePin(pin.id), false],
      ["上移", "move-up", "", () => reorderPin(pin.id, -1), index === 0],
      ["下移", "move-down", "", () => reorderPin(pin.id, 1), index === total - 1],
      ["刪除", "delete", "strategy-delete-btn", () => deletePin(pin), false],
    ];
    actionSpecs.forEach(([label, action, className, handler, unavailable]) => {
      const button = pinNode("button", `range-btn ${className}`.trim(), label);
      button.type = "button";
      button.dataset.pinAction = action;
      button.disabled = busy || unavailable;
      button.setAttribute("aria-label", `${label}「${pin.text || "未命名規則"}」`);
      button.addEventListener("click", handler);
      actions.appendChild(button);
    });
    article.appendChild(actions);
    const review = renderReview(pin, state, busy);
    if (review) article.appendChild(review);
    return article;
  }

  function setPinsCompileBusy(busy) {
    document.getElementById("pinnedRulesPanel").setAttribute("aria-busy", String(busy));
  }

  function renderPins(data, focusTarget = focusedPinControl()) {
    pinsRevision = Number(data.revision) || 0;
    pinsLimits = data.limits && typeof data.limits === "object" ? data.limits : {};
    currentPins = Array.isArray(data.pins) ? data.pins.filter(pin => pin && typeof pin === "object") : [];
    const maxText = Number(pinsLimits.max_text);
    if (maxText > 0) pinTextInput.maxLength = maxText;
    pinsUsageEl.textContent = `${currentPins.length} / ${Number(pinsLimits.max_pins) || "—"} 條`;
    pinsListEl.replaceChildren();
    pinsListEl.setAttribute("aria-busy", "false");
    if (!currentPins.length) {
      pinsListEl.appendChild(pinNode("p", "empty strategy-empty", "尚未新增固定檢查項目。"));
      return;
    }
    currentPins.forEach((pin, index) => pinsListEl.appendChild(makePinRow(pin, index, currentPins.length)));
    restorePinFocus(focusTarget);
  }

  async function loadPins(options = {}) {
    const generation = ++pinsGeneration;
    pinsListEl.setAttribute("aria-busy", "true");
    try {
      const response = await fetch("/api/pins");
      if (!response.ok) throw new Error(await responseDetail(response));
      const data = await response.json();
      if (generation !== pinsGeneration) return;
      renderPins(data && typeof data === "object" ? data : {});
      if (options.announce) showPinsStatus("已重新載入固定檢查項目。", false);
    } catch (error) {
      if (generation !== pinsGeneration) return;
      pinsListEl.setAttribute("aria-busy", "false");
      pinsListEl.replaceChildren(pinNode("p", "empty strategy-empty", "無法載入固定檢查項目。"));
      showPinsStatus(`載入失敗：${error.message}`, true);
    }
  }

  async function updatePin(id, changes) {
    const focusTarget = focusedPinControl();
    return queuePinMutation(id, async () => {
      try {
        const data = await pinRequest(`/api/pins/${encodeURIComponent(id)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...changes, revision: pinsRevision }),
        });
        adoptPinResponse(data, { focusTarget });
        showPinsStatus("已更新固定檢查項目。", false);
      } catch (error) {
        if (await handlePinConflict(error)) return;
        renderPins(pinSnapshot(), focusTarget);
        showPinsStatus(`更新失敗：${error.message}`, true);
      }
    }, focusTarget);
  }

  function compilePin(id) {
    const key = String(id);
    const focusTarget = { id: key, action: "compile" };
    compilingPins.add(key);
    setPinsCompileBusy(true);
    renderPins(pinSnapshot());
    showPinsStatus("AI 編譯中，最多約 3 分鐘…", false);
    return queuePinMutation(id, async () => {
      const controller = new AbortController();
      activeCompileController = controller;
      const timeout = window.setTimeout(() => controller.abort(), 200000);
      try {
        const data = await pinRequest(`/api/pins/${encodeURIComponent(id)}/compile`, {
          method: "POST",
          signal: controller.signal,
        });
        adoptPinResponse(data, { focusTarget });
        showPinsStatus("編譯完成，請檢查白話解讀後再確認。", false);
      } catch (error) {
        if (error?.name === "AbortError") {
          showPinsStatus("編譯逾時或已中止，請稍後重試", true);
        } else if (error instanceof PinRequestError && error.status === 429) {
          showPinsStatus(error.detail, true);
        } else if (!(await handlePinConflict(error))) {
          showPinsStatus(`編譯失敗：${error.message}`, true);
        }
      } finally {
        window.clearTimeout(timeout);
        if (activeCompileController === controller) activeCompileController = null;
        compilingPins.delete(key);
        setPinsCompileBusy(compilingPins.size > 0);
        renderPins(pinSnapshot(), focusTarget);
      }
    }, focusTarget);
  }

  function confirmPin(id) {
    const focusTarget = { id: String(id), action: "confirm" };
    return queuePinMutation(id, async () => {
      const pin = pinById(id);
      if (!pin || pinState(pin) !== "draft") {
        showPinsStatus("規則已更新，請重新編譯後再確認。", true);
        renderPins(pinSnapshot(), focusTarget);
        return;
      }
      try {
        const data = await pinRequest(`/api/pins/${encodeURIComponent(id)}/confirm`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ revision: pinsRevision }),
        });
        adoptPinResponse(data, { focusTarget });
        showPinsStatus("已確認規則解讀。", false);
      } catch (error) {
        if (await handlePinConflict(error)) return;
        showPinsStatus(`確認失敗：${error.message}`, true);
      }
    }, focusTarget);
  }

  function deletePin(pin) {
    if (!window.confirm(`確定刪除「${pin.text || "這條規則"}」？`)) return;
    return queuePinMutation(pin.id, async () => {
      try {
        const data = await pinRequest(`/api/pins/${encodeURIComponent(pin.id)}?revision=${encodeURIComponent(pinsRevision)}`, { method: "DELETE" });
        adoptPinResponse(data, { deletedId: pin.id });
        showPinsStatus("已刪除固定檢查項目。", false);
      } catch (error) {
        if (await handlePinConflict(error)) return;
        showPinsStatus(`刪除失敗：${error.message}`, true);
      }
    });
  }

  function reorderPin(id, delta) {
    const action = delta < 0 ? "move-up" : "move-down";
    const focusTarget = { id: String(id), action };
    return queuePinMutation(id, async () => {
      const index = currentPins.findIndex(pin => String(pin.id) === String(id));
      const nextIndex = index + delta;
      if (index < 0 || nextIndex < 0 || nextIndex >= currentPins.length) return;
      const order = currentPins.map(pin => pin.id);
      [order[index], order[nextIndex]] = [order[nextIndex], order[index]];
      try {
        const data = await pinRequest("/api/pins/reorder", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ order, revision: pinsRevision }),
        });
        adoptPinResponse(data, { focusTarget });
        showPinsStatus("已調整規則順序。", false);
      } catch (error) {
        if (await handlePinConflict(error)) return;
        showPinsStatus(`排序失敗：${error.message}`, true);
      }
    }, focusTarget);
  }

  async function loadPinCatalog() {
    const catalogEl = document.getElementById("pinCatalog");
    try {
      const response = await fetch("/api/pins/catalog");
      if (!response.ok) throw new Error(await responseDetail(response));
      const data = await response.json();
      const facts = Array.isArray(data) ? data.filter(item => item && typeof item === "object") : [];
      const groups = new Map();
      facts.forEach(fact => {
        const prefix = String(fact.key || "其他").split(/[._]/)[0] || "其他";
        if (!groups.has(prefix)) groups.set(prefix, []);
        groups.get(prefix).push(fact);
      });
      catalogEl.replaceChildren();
      groups.forEach((items, prefix) => {
        const section = pinNode("section", "pin-catalog-group");
        section.appendChild(pinNode("h3", "", prefix));
        const list = document.createElement("dl");
        items.forEach(fact => {
          list.append(pinNode("dt", "", String(fact.key || "—")), pinNode("dd", "", String(fact.description || "尚無說明")));
        });
        section.appendChild(list);
        catalogEl.appendChild(section);
      });
      if (!groups.size) catalogEl.appendChild(pinNode("p", "strategy-help", "目前沒有可用事實。"));
      catalogEl.setAttribute("aria-busy", "false");
    } catch (error) {
      catalogEl.textContent = `無法載入可用事實：${error.message}`;
      catalogEl.setAttribute("aria-busy", "false");
    }
  }

  function formatBytes(bytes) {
    const value = Number(bytes) || 0;
    if (value < 1024) return `${value}B`;
    if (value < 1024 * 1024) return `${Math.round(value / 1024)}KB`;
    return `${(value / (1024 * 1024)).toFixed(1)}MB`;
  }

  async function responseDetail(response) {
    try {
      const body = await response.json();
      if (typeof body.detail === "string") return body.detail;
      if (body.detail) return JSON.stringify(body.detail);
    } catch (_) {
      // A non-JSON server response falls back to its HTTP status below.
    }
    return `HTTP ${response.status}`;
  }

  function showStatus(message, isError) {
    statusEl.textContent = message;
    statusEl.classList.toggle("error", Boolean(isError));
  }

  function setBusy(button, busy) {
    button.disabled = busy;
    button.setAttribute("aria-busy", String(busy));
  }

  function strategyFileUrl(name) {
    return `/api/strategy/file/${encodeURIComponent(name)}`;
  }

  function renderUsage(data) {
    const limits = data.limits || {};
    const fileCount = Array.isArray(data.files) ? data.files.length : 0;
    usageEl.textContent = `${formatBytes(data.total_size)} / ${formatBytes(limits.max_total_bytes)}　·　${fileCount} / ${limits.max_files ?? "--"} 個檔案　·　單檔上限 ${formatBytes(limits.max_file_bytes)}`;
  }

  function rememberStrategySnapshot(data) {
    strategySnapshot = {
      files: Array.isArray(data.files) ? data.files : [],
      limits: data.limits || {},
      totalSize: Number(data.total_size) || 0,
    };
  }

  function contentSizeError(name, byteLength) {
    if (!strategySnapshot) {
      return "尚未取得儲存容量限制，請等待筆記清單載入後再試。";
    }
    const { files, limits, totalSize } = strategySnapshot;
    const maxFileBytes = Number(limits.max_file_bytes);
    const maxTotalBytes = Number(limits.max_total_bytes);
    if (!(maxFileBytes > 0) || !(maxTotalBytes > 0)) {
      return "無法取得完整的儲存容量限制，請重新整理筆記清單後再試。";
    }
    if (byteLength > maxFileBytes) {
      return `內容大小為 ${formatBytes(byteLength)}，超過單檔上限 ${formatBytes(maxFileBytes)}。`;
    }

    const existing = files.find(file => file.name === name);
    const nextTotal = totalSize - (Number(existing?.size) || 0) + byteLength;
    if (nextTotal > maxTotalBytes) {
      return `儲存後總用量將為 ${formatBytes(nextTotal)}，超過總容量 ${formatBytes(maxTotalBytes)}。`;
    }
    return "";
  }

  function makeFileRow(file) {
    const row = document.createElement("div");
    row.className = "strategy-file-row";

    const info = document.createElement("div");
    info.className = "strategy-file-info";
    const name = document.createElement("strong");
    name.textContent = file.name;
    const size = document.createElement("span");
    size.textContent = formatBytes(file.size);
    info.append(name, size);

    const actions = document.createElement("div");
    actions.className = "strategy-file-actions";
    const viewButton = document.createElement("button");
    viewButton.type = "button";
    viewButton.className = "range-btn";
    viewButton.textContent = "查看";
    viewButton.setAttribute("aria-label", `查看 ${file.name}`);
    viewButton.addEventListener("click", () => viewFile(file.name, viewButton));

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "range-btn strategy-delete-btn";
    deleteButton.textContent = "刪除";
    deleteButton.setAttribute("aria-label", `刪除 ${file.name}`);
    deleteButton.addEventListener("click", () => deleteFile(file.name, deleteButton));
    actions.append(viewButton, deleteButton);
    row.append(info, actions);
    return row;
  }

  function renderFileList(files) {
    fileListEl.replaceChildren();
    fileListEl.setAttribute("aria-busy", "false");
    if (!files.length) {
      const empty = document.createElement("p");
      empty.className = "empty strategy-empty";
      empty.textContent = "尚未儲存任何策略筆記。";
      fileListEl.appendChild(empty);
      return;
    }
    files.forEach(file => fileListEl.appendChild(makeFileRow(file)));
  }

  async function loadFiles(options = {}) {
    const generation = ++fileListGeneration;
    fileListEl.setAttribute("aria-busy", "true");
    try {
      const response = await fetch("/api/strategy");
      if (!response.ok) throw new Error(await responseDetail(response));
      const data = await response.json();
      if (generation !== fileListGeneration) return;
      rememberStrategySnapshot(data);
      renderUsage(data);
      renderFileList(Array.isArray(data.files) ? data.files : []);
      if (options.announce) showStatus("已重新整理策略筆記。", false);
    } catch (error) {
      if (generation !== fileListGeneration) return;
      fileListEl.setAttribute("aria-busy", "false");
      fileListEl.replaceChildren();
      const empty = document.createElement("p");
      empty.className = "empty strategy-empty";
      empty.textContent = "無法載入策略筆記。";
      fileListEl.appendChild(empty);
      showStatus(`載入失敗：${error.message}`, true);
    }
  }

  async function viewFile(name, button) {
    setBusy(button, true);
    try {
      const response = await fetch(strategyFileUrl(name));
      if (!response.ok) throw new Error(await responseDetail(response));
      const data = await response.json();
      viewerTitle.textContent = `筆記內容：${data.name}`;
      viewerContent.textContent = data.content || "";
      viewer.hidden = false;
      lastViewerButton = button;
      viewer.scrollIntoView({ behavior: "smooth", block: "start" });
      viewerContent.focus({ preventScroll: true });
    } catch (error) {
      showStatus(`查看失敗：${error.message}`, true);
    } finally {
      setBusy(button, false);
    }
  }

  async function deleteFile(name, button) {
    if (!window.confirm(`確定刪除「${name}」？`)) return;
    setBusy(button, true);
    try {
      const response = await fetch(strategyFileUrl(name), { method: "DELETE" });
      if (!response.ok) throw new Error(await responseDetail(response));
      if (!viewer.hidden && viewerTitle.textContent === `筆記內容：${name}`) {
        viewer.hidden = true;
        viewerContent.textContent = "";
      }
      showStatus(`已刪除 ${name}。`, false);
      await loadFiles();
      fileListHeading.setAttribute("tabindex", "-1");
      fileListHeading.focus();
    } catch (error) {
      showStatus(`刪除失敗：${error.message}`, true);
      setBusy(button, false);
    }
  }

  async function saveFile(name, content, button) {
    setBusy(button, true);
    try {
      const response = await fetch(strategyFileUrl(name), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (!response.ok) throw new Error(await responseDetail(response));
      showStatus(`已儲存 ${name}。`, false);
      await loadFiles();
      return true;
    } catch (error) {
      showStatus(`儲存失敗：${error.message}`, true);
      return false;
    } finally {
      setBusy(button, false);
    }
  }

  async function readSelectedFile() {
    const file = fileInput.files && fileInput.files[0];
    if (!file) {
      showStatus("請先選擇 .md 或 .txt 檔案。", true);
      fileInput.setAttribute("aria-invalid", "true");
      fileInput.focus();
      return;
    }
    fileInput.removeAttribute("aria-invalid");
    setBusy(uploadButton, true);
    try {
      const buffer = await file.arrayBuffer();
      let content;
      try {
        content = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
      } catch (_) {
        showStatus("檔案不是有效的 UTF-8 文字，未上傳。請先轉成 UTF-8 編碼。", true);
        fileInput.setAttribute("aria-invalid", "true");
        fileInput.focus();
        return;
      }
      const sizeError = contentSizeError(file.name, buffer.byteLength);
      if (sizeError) {
        showStatus(sizeError, true);
        fileInput.setAttribute("aria-invalid", "true");
        fileInput.focus();
        return;
      }
      const saved = await saveFile(file.name, content, uploadButton);
      if (saved) fileInput.value = "";
    } catch (_) {
      showStatus("無法讀取所選檔案。", true);
    } finally {
      setBusy(uploadButton, false);
    }
  }

  uploadButton.addEventListener("click", readSelectedFile);
  fileInput.addEventListener("change", () => fileInput.removeAttribute("aria-invalid"));
  document.getElementById("refreshStrategyBtn").addEventListener("click", () => loadFiles({ announce: true }));
  document.getElementById("closeStrategyViewerBtn").addEventListener("click", () => {
    viewer.hidden = true;
    viewerContent.textContent = "";
    if (lastViewerButton && lastViewerButton.isConnected) lastViewerButton.focus();
  });
  pasteForm.addEventListener("submit", async event => {
    event.preventDefault();
    const filenameInput = document.getElementById("strategyFilename");
    const contentInput = document.getElementById("strategyContent");
    const name = filenameInput.value.trim();
    const submitButton = pasteForm.querySelector('button[type="submit"]');
    if (!name) {
      showStatus("請輸入檔名。", true);
      filenameInput.setAttribute("aria-invalid", "true");
      filenameInput.focus();
      return;
    }
    filenameInput.removeAttribute("aria-invalid");
    const content = contentInput.value;
    const byteLength = new TextEncoder().encode(content).byteLength;
    const sizeError = contentSizeError(name, byteLength);
    if (sizeError) {
      showStatus(sizeError, true);
      contentInput.setAttribute("aria-invalid", "true");
      contentInput.focus();
      return;
    }
    contentInput.removeAttribute("aria-invalid");
    const saved = await saveFile(name, content, submitButton);
    if (saved) pasteForm.reset();
  });

  pinCreateForm.addEventListener("submit", event => {
    event.preventDefault();
    const text = pinTextInput.value.trim();
    if (!text) {
      showPinsStatus("請輸入規則文字。", true);
      pinTextInput.setAttribute("aria-invalid", "true");
      pinTextInput.focus();
      return;
    }
    pinTextInput.removeAttribute("aria-invalid");
    const submitButton = pinCreateForm.querySelector('button[type="submit"]');
    setBusy(submitButton, true);
    const scope = pinScopeInput.value;
    queuePinMutation(null, async () => {
      try {
        const data = await pinRequest("/api/pins", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, scope, revision: pinsRevision }),
        });
        adoptPinResponse(data);
        pinCreateForm.reset();
        showPinsStatus("已新增固定檢查項目，請按「編譯」。", false);
      } catch (error) {
        if (await handlePinConflict(error)) return;
        showPinsStatus(`新增失敗：${error.message}`, true);
      } finally {
        setBusy(submitButton, false);
      }
    });
  });
  pinTextInput.addEventListener("input", () => pinTextInput.removeAttribute("aria-invalid"));
  document.querySelectorAll(".pin-example-btn").forEach(button => {
    button.addEventListener("click", () => {
      pinTextInput.value = button.dataset.example || "";
      pinScopeInput.value = button.dataset.scope || "now";
      pinTextInput.focus();
    });
  });

  window.addEventListener("pagehide", () => {
    if (activeCompileController) activeCompileController.abort();
  });

  loadFiles();
  loadPins();
  loadPinCatalog();
})();
