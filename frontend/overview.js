(function strategyBatchOverviewPage() {
  "use strict";

  const MAX_TICKERS = 10;
  const POLL_INTERVAL_MS = 3000;
  const POLL_LIMIT_MS = 30 * 60 * 1000;
  const STATUS_FETCH_TIMEOUT_MS = 15000;
  const STATUS_LABELS = {
    pass: "通過",
    fail: "未通過",
    unknown: "未知",
    conditional: "有條件",
  };

  const tickerGroupsEl = document.getElementById("tickerGroups");
  const tickerCounterEl = document.getElementById("tickerCounter");
  const runButton = document.getElementById("runBatchBtn");
  const statusEl = document.getElementById("batchStatus");
  const progressEl = document.getElementById("batchProgress");
  const progressBar = document.getElementById("batchProgressBar");
  const progressText = document.getElementById("batchProgressText");
  const matrixEl = document.getElementById("batchMatrix");
  const errorsEl = document.getElementById("batchErrors");
  const metaEl = document.getElementById("batchMeta");
  const usageContentEl = document.getElementById("usageContent");

  let stocksByMarket = { tw: [], us: [] };
  let batchTimer = null;
  let batchDeadlineTimer = null;
  let batchDeadline = 0;
  let batchGeneration = 0;
  let batchFailureCount = 0;
  let statusFetchController = null;
  let pageActive = true;
  let batchIsRunning = false;
  let stocksLoaded = false;
  let demoMode = false;

  function applyDemoControl() {
    if (!demoMode) return;
    runButton.disabled = true;
    runButton.title = "示範模式：策略檢查已停用";
    runButton.setAttribute("aria-describedby", "sdDemoDisabledDescription");
  }

  function textNode(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value;
    return node;
  }

  async function responseDetail(response) {
    try {
      const body = await response.json();
      if (typeof body.detail === "string") return body.detail;
      if (body.detail !== undefined) return JSON.stringify(body.detail);
    } catch (_) {
      // Non-JSON responses fall back to the HTTP status.
    }
    return `HTTP ${response.status}`;
  }

  function setStatus(message, isError = false) {
    statusEl.textContent = message;
    statusEl.classList.toggle("error", isError);
  }

  function isTwTicker(ticker) {
    return /\.(TW|TWO)$/i.test(String(ticker || ""));
  }

  function displayTicker(ticker) {
    return String(ticker || "").replace(/\.(TW|TWO)$/i, "");
  }

  function selectedTickers() {
    return Array.from(tickerGroupsEl.querySelectorAll('input[type="checkbox"]:checked'), input => input.value);
  }

  function updateSelectionUi() {
    const count = selectedTickers().length;
    tickerCounterEl.textContent = `已選 ${count} / ${MAX_TICKERS} 檔`;
    runButton.disabled = demoMode || batchIsRunning || count === 0;
  }

  function makeTickerOption(stock, index, market) {
    const ticker = String(stock.ticker || "");
    const label = document.createElement("label");
    label.className = "overview-ticker-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = ticker;
    input.id = `batchTicker-${market}-${index}`;
    input.addEventListener("change", () => {
      if (input.checked && selectedTickers().length > MAX_TICKERS) {
        input.checked = false;
        setStatus(`最多只能選擇 ${MAX_TICKERS} 檔股票。`, true);
      } else {
        setStatus("", false);
      }
      updateSelectionUi();
    });
    const copy = document.createElement("span");
    copy.className = "overview-ticker-copy";
    copy.append(
      textNode("strong", "", displayTicker(ticker)),
      textNode("small", "", stock.name ? String(stock.name) : ticker),
    );
    label.append(input, copy);
    return label;
  }

  function renderTickerGroup(market, title, stocks) {
    const fieldset = document.createElement("fieldset");
    fieldset.className = "overview-ticker-group";
    const legend = document.createElement("legend");
    legend.textContent = `${title}（${stocks.length}）`;
    fieldset.appendChild(legend);
    const list = document.createElement("div");
    list.className = "overview-ticker-list";
    if (stocks.length) {
      stocks.forEach((stock, index) => list.appendChild(makeTickerOption(stock, index, market)));
    } else {
      list.appendChild(textNode("p", "overview-group-empty", "目前沒有股票。"));
    }
    fieldset.appendChild(list);
    return fieldset;
  }

  function renderTickerPicker() {
    tickerGroupsEl.replaceChildren(
      renderTickerGroup("tw", "台股", stocksByMarket.tw),
      renderTickerGroup("us", "美股", stocksByMarket.us),
    );
    stocksLoaded = true;
    tickerGroupsEl.setAttribute("aria-busy", "false");
    setRunning(batchIsRunning); // also (re)enables the quick-pick buttons now that stocksLoaded is true
  }

  async function loadStocks() {
    tickerGroupsEl.setAttribute("aria-busy", "true");
    try {
      const response = await fetch("/api/stocks");
      if (!response.ok) throw new Error(await responseDetail(response));
      const data = await response.json();
      const stocks = Array.isArray(data) ? data : [];
      stocksByMarket = { tw: [], us: [] };
      stocks.forEach(rawStock => {
        const stock = rawStock && typeof rawStock === "object" ? rawStock : { ticker: rawStock };
        if (!stock.ticker) return;
        const market = String(stock.market || "").toLowerCase();
        const group = market === "tw" || market === "taiwan" || isTwTicker(stock.ticker) ? "tw" : "us";
        stocksByMarket[group].push(stock);
      });
      renderTickerPicker();
    } catch (error) {
      tickerGroupsEl.setAttribute("aria-busy", "false");
      tickerGroupsEl.replaceChildren(textNode("p", "empty overview-empty", `無法載入觀察清單：${error.message}`));
      setStatus(`載入觀察清單失敗：${error.message}`, true);
    }
  }

  function quickPick(market) {
    const wanted = new Set(stocksByMarket[market].slice(0, 3).map(stock => String(stock.ticker)));
    let count = selectedTickers().length;
    tickerGroupsEl.querySelectorAll('input[type="checkbox"]').forEach(input => {
      if (!wanted.has(input.value) || input.checked || count >= MAX_TICKERS) return;
      input.checked = true;
      count += 1;
    });
    if (!wanted.size) setStatus(market === "tw" ? "觀察清單沒有台股。" : "觀察清單沒有美股。", true);
    else setStatus("", false);
    updateSelectionUi();
    applyDemoControl();
  }

  function setRunning(running) {
    batchIsRunning = running;
    tickerGroupsEl.querySelectorAll('input[type="checkbox"]').forEach(input => { input.disabled = running; });
    document.getElementById("pickTwBtn").disabled = running || !stocksLoaded;
    document.getElementById("pickUsBtn").disabled = running || !stocksLoaded;
    document.getElementById("refreshBatchBtn").disabled = running;
    runButton.textContent = running ? "批次檢查執行中…" : "執行批次檢查";
    document.getElementById("tickerPicker").setAttribute("aria-busy", String(running));
    updateSelectionUi();
  }

  function renderProgress(progress) {
    const safe = progress && typeof progress === "object" ? progress : {};
    const done = Math.max(0, Number(safe.done) || 0);
    const total = Math.max(0, Number(safe.total) || 0);
    const current = safe.current ? String(safe.current) : "—";
    progressEl.hidden = false;
    progressBar.max = Math.max(1, total);
    progressBar.value = Math.min(done, progressBar.max);
    progressText.textContent = `${done}/${total} · 目前：${current}`;
  }

  function statusLabel(status) {
    return STATUS_LABELS[status] || STATUS_LABELS.unknown;
  }

  function normalizeStatus(status) {
    return Object.prototype.hasOwnProperty.call(STATUS_LABELS, status) ? status : "unknown";
  }

  function makeStatusBadge(status) {
    const normalized = normalizeStatus(status);
    return textNode("span", `strategy-status-badge status-${normalized}`, statusLabel(normalized));
  }

  function statusFromValue(value) {
    if (typeof value === "string") return value;
    if (value && typeof value === "object") return value.status || value.state;
    return "unknown";
  }

  function pinStatusForTicker(pin, tickerResult) {
    const pinId = String(pin.id ?? pin.pin_id ?? pin.key ?? "");
    const ticker = String(tickerResult.ticker || "");
    const tickerMaps = [tickerResult.pins, tickerResult.pin_results];
    for (const map of tickerMaps) {
      if (map && typeof map === "object" && !Array.isArray(map) && pinId && map[pinId] !== undefined) {
        return statusFromValue(map[pinId]);
      }
      if (Array.isArray(map)) {
        const match = map.find(item => item && typeof item === "object" && String(item.pin_id ?? item.id ?? item.key ?? "") === pinId);
        if (match) return statusFromValue(match);
      }
    }
    const pinMaps = [pin.results, pin.rows, pin.statuses, pin.by_ticker];
    for (const map of pinMaps) {
      if (map && typeof map === "object" && !Array.isArray(map) && map[ticker] !== undefined) {
        return statusFromValue(map[ticker]);
      }
      if (Array.isArray(map)) {
        const match = map.find(item => item && typeof item === "object" && String(item.ticker || "") === ticker);
        if (match) return statusFromValue(match);
      }
    }
    return "unknown";
  }

  function countText(counts) {
    const safe = counts && typeof counts === "object" ? counts : {};
    return `通過 ${Number(safe.pass) || 0} · 未通過 ${Number(safe.fail) || 0} · 未知 ${Number(safe.unknown) || 0} · 有條件 ${Number(safe.conditional) || 0}`;
  }

  function appendHeaderCell(row, tickerResult) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.className = "overview-ticker-heading";
    const ticker = String(tickerResult.ticker || "");
    const link = document.createElement("a");
    link.href = `detail.html?ticker=${encodeURIComponent(ticker)}`;
    link.textContent = displayTicker(ticker) || "未知股票";
    cell.appendChild(link);
    const excluded = Number(tickerResult.entry_rows_excluded);
    if (Number.isInteger(excluded) && excluded > 0) {
      const excludedText = `${excluded} 筆買入當時的檢查未列入矩陣`;
      const note = textNode("small", "overview-ticker-excluded", excludedText);
      note.title = excludedText;
      note.setAttribute("aria-label", excludedText);
      cell.appendChild(note);
    }
    if (tickerResult.error) cell.appendChild(textNode("span", "overview-ticker-error", "檢查錯誤"));
    row.appendChild(cell);
  }

  function renderErrors(batchErrors, tickers) {
    const entries = [];
    if (batchErrors && typeof batchErrors === "object") {
      Object.entries(batchErrors).forEach(([ticker, message]) => entries.push([ticker, message]));
    }
    tickers.forEach(tickerResult => {
      if (tickerResult.error && !entries.some(([ticker]) => ticker === tickerResult.ticker)) {
        entries.push([tickerResult.ticker || "*", tickerResult.error]);
      }
    });
    errorsEl.replaceChildren();
    errorsEl.hidden = entries.length === 0;
    if (!entries.length) return;
    errorsEl.appendChild(textNode("strong", "", "批次錯誤"));
    const list = document.createElement("ul");
    entries.forEach(([ticker, message]) => {
      const label = ticker === "*" ? "整批" : displayTicker(ticker);
      list.appendChild(textNode("li", "", `${label}：${String(message || "未知錯誤")}`));
    });
    errorsEl.appendChild(list);
  }

  function renderOverview(rawOverview, batchErrors) {
    const overview = rawOverview && typeof rawOverview === "object" ? rawOverview : {};
    const rules = Array.isArray(overview.rules)
      ? overview.rules.map(rule => rule && typeof rule === "object" ? rule : {})
      : [];
    const pins = Array.isArray(overview.pins)
      ? overview.pins.map(pin => pin && typeof pin === "object" ? pin : {})
      : [];
    const tickers = Array.isArray(overview.tickers)
      ? overview.tickers.map(ticker => ticker && typeof ticker === "object" ? ticker : {})
      : [];
    renderErrors(batchErrors, tickers);
    matrixEl.classList.remove("is-stale");
    matrixEl.setAttribute("aria-busy", "false");
    matrixEl.replaceChildren();
    if (!pins.length && !rules.length && !tickers.length) {
      matrixEl.appendChild(textNode("p", "empty overview-empty", "尚無批次檢查結果。"));
      return;
    }

    const table = document.createElement("table");
    table.className = "overview-matrix";
    const caption = document.createElement("caption");
    caption.textContent = "固定檢查項目與策略筆記規則的股票狀態比較；矩陣只比較「現在」的結果，買入當時的檢查不列入。";
    table.appendChild(caption);
    const head = document.createElement("thead");
    const headerRow = document.createElement("tr");
    const ruleHeading = document.createElement("th");
    ruleHeading.scope = "col";
    ruleHeading.className = "overview-rule-column";
    ruleHeading.textContent = "策略規則";
    headerRow.appendChild(ruleHeading);
    tickers.forEach(tickerResult => appendHeaderCell(headerRow, tickerResult));
    head.appendChild(headerRow);
    table.appendChild(head);

    const body = document.createElement("tbody");
    pins.forEach((pin, index) => {
      const row = document.createElement("tr");
      row.className = "overview-pin-row";
      const heading = document.createElement("th");
      heading.scope = "row";
      heading.className = "overview-rule-column overview-pin-heading";
      heading.appendChild(textNode("strong", "overview-rule-text", pin.text || pin.rule || `固定規則 ${index + 1}`));
      heading.appendChild(textNode("span", "overview-rule-source", "固定檢查項目 · 程式直接計算"));
      row.appendChild(heading);
      tickers.forEach(tickerResult => {
        const cell = document.createElement("td");
        cell.appendChild(makeStatusBadge(pinStatusForTicker(pin, tickerResult)));
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
    if (pins.length && rules.length) {
      const separator = document.createElement("tr");
      separator.className = "overview-rule-separator";
      const label = document.createElement("th");
      label.scope = "row";
      label.className = "overview-rule-column";
      label.textContent = "策略筆記規則（AI 判斷）";
      separator.appendChild(label);
      const filler = document.createElement("td");
      filler.colSpan = Math.max(1, tickers.length);
      separator.appendChild(filler);
      body.appendChild(separator);
    }
    rules.forEach((rule, index) => {
      const row = document.createElement("tr");
      const heading = document.createElement("th");
      heading.scope = "row";
      heading.className = "overview-rule-column";
      heading.appendChild(textNode("strong", "overview-rule-text", rule.rule || `規則 ${index + 1}`));
      const source = [rule.file, rule.heading].filter(Boolean).map(String).join(" › ");
      if (source) heading.appendChild(textNode("span", "overview-rule-source", source));
      heading.appendChild(textNode("span", "overview-rule-counts", `未通過 ${Number(rule.count_fail) || 0} · 通過 ${Number(rule.count_pass) || 0} · 未知 ${Number(rule.count_unknown) || 0} · 有條件 ${Number(rule.count_conditional) || 0}`));
      row.appendChild(heading);
      tickers.forEach(tickerResult => {
        const cell = document.createElement("td");
        const rows = tickerResult.rows && typeof tickerResult.rows === "object" ? tickerResult.rows : {};
        cell.appendChild(makeStatusBadge(rows[rule.key]));
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
    table.appendChild(body);

    const foot = document.createElement("tfoot");
    const summaryRow = document.createElement("tr");
    const summaryHeading = document.createElement("th");
    summaryHeading.scope = "row";
    summaryHeading.className = "overview-rule-column";
    summaryHeading.textContent = "每檔統計";
    summaryRow.appendChild(summaryHeading);
    tickers.forEach(tickerResult => {
      const cell = document.createElement("td");
      cell.className = "overview-ticker-summary";
      cell.appendChild(textNode("span", "", countText(tickerResult.counts)));
      if (Number.isInteger(tickerResult.holdings_lots)) {
        cell.appendChild(textNode("small", "", `持倉 ${tickerResult.holdings_lots} 批`));
      }
      if (tickerResult.generated_at) cell.appendChild(textNode("small", "", String(tickerResult.generated_at)));
      summaryRow.appendChild(cell);
    });
    foot.appendChild(summaryRow);
    table.appendChild(foot);
    matrixEl.appendChild(table);
  }

  function markMatrixStale() {
    if (!matrixEl.querySelector(":scope > .overview-matrix")) {
      renderMatrixNotice("新批次執行中，完成後會顯示比較結果。");
      matrixEl.setAttribute("aria-busy", "true");
      return;
    }
    matrixEl.classList.add("is-stale");
    matrixEl.setAttribute("aria-busy", "true");
    let notice = matrixEl.querySelector(":scope > .overview-matrix-notice");
    if (!notice) {
      notice = textNode("p", "overview-matrix-notice", "以下為上一次批次的結果，新批次完成後會更新");
      matrixEl.prepend(notice);
    }
  }

  function renderMatrixNotice(message, isError = false) {
    matrixEl.classList.remove("is-stale");
    matrixEl.setAttribute("aria-busy", "false");
    matrixEl.replaceChildren(textNode("p", `overview-matrix-notice${isError ? " error" : ""}`, message));
  }

  function formatBatchMeta(data) {
    const parts = [];
    if (data.started_at) parts.push(`開始：${data.started_at}`);
    if (data.finished_at) parts.push(`完成：${data.finished_at}`);
    metaEl.textContent = parts.join("　");
  }

  function stopPolling() {
    if (batchTimer !== null) {
      clearTimeout(batchTimer);
      batchTimer = null;
    }
  }

  function clearDeadlineTimer() {
    if (batchDeadlineTimer !== null) {
      clearTimeout(batchDeadlineTimer);
      batchDeadlineTimer = null;
    }
  }

  function abortStatusFetch() {
    if (statusFetchController) {
      statusFetchController.abort();
      statusFetchController = null;
    }
  }

  function armDeadlineTimer() {
    clearDeadlineTimer();
    if (!pageActive || !batchDeadline) return;
    const remaining = batchDeadline - Date.now();
    if (remaining <= 0) {
      timeoutPolling();
      return;
    }
    batchDeadlineTimer = setTimeout(timeoutPolling, remaining);
  }

  function preserveRunDeadline(startedAt) {
    const startedAtMs = Date.parse(String(startedAt || ""));
    if (Number.isFinite(startedAtMs)) batchDeadline = startedAtMs + POLL_LIMIT_MS;
    else if (!batchDeadline) batchDeadline = Date.now() + POLL_LIMIT_MS;
    armDeadlineTimer();
  }

  function timeoutPolling() {
    stopPolling();
    clearDeadlineTimer();
    batchDeadline = 0;
    batchGeneration += 1;
    abortStatusFetch();
    setRunning(false);
    progressEl.hidden = true;
    setStatus("批次檢查已超過 30 分鐘，已停止自動查詢；可按「重新整理」查看最新狀態。", true);
  }

  function schedulePoll(delay = POLL_INTERVAL_MS) {
    stopPolling();
    if (!pageActive) return;
    const remaining = batchDeadline - Date.now();
    if (remaining <= 0) {
      timeoutPolling();
      return;
    }
    if (delay >= remaining) {
      batchTimer = setTimeout(timeoutPolling, remaining);
      return;
    }
    armDeadlineTimer();
    batchTimer = setTimeout(() => loadBatch({ polling: true }), delay);
  }

  function applyBatchState(data, options = {}) {
    batchFailureCount = 0;
    formatBatchMeta(data);

    if (data.status === "running") {
      if (data.overview) renderOverview(data.overview, data.errors);
      else if (data.errors) renderErrors(data.errors, []);
      markMatrixStale();
      preserveRunDeadline(data.started_at);
      if (!batchDeadline || Date.now() >= batchDeadline) return;
      setRunning(true);
      renderProgress(data.progress);
      setStatus(options.runningMessage || "批次檢查執行中，約每 3 秒更新一次。", false);
      schedulePoll();
      return;
    }

    stopPolling();
    clearDeadlineTimer();
    batchDeadline = 0;
    setRunning(false);
    progressEl.hidden = true;
    if (data.overview) renderOverview(data.overview, data.errors);
    else {
      renderErrors(data.errors, []);
      if (data.status === "error") renderMatrixNotice("批次檢查失敗，沒有可顯示的新結果。", true);
      else if (data.status === "none") renderMatrixNotice("尚無批次檢查結果。");
      else renderMatrixNotice("批次已結束，但沒有可顯示的比較結果。", data.status !== "done");
    }
    if (data.status === "done") {
      setStatus(options.initial ? "已載入上次儲存的批次結果。" : "批次檢查完成。", false);
      loadUsage();
    } else if (data.status === "error") {
      setStatus("批次檢查失敗；請查看錯誤訊息。", true);
    } else if (data.status === "none") {
      setStatus("", false);
    } else {
      setStatus(`收到未知的批次狀態：${String(data.status || "未提供")}`, true);
    }
  }

  async function loadBatch(options = {}) {
    const generation = ++batchGeneration;
    if (batchDeadline && Date.now() >= batchDeadline) {
      timeoutPolling();
      return null;
    }
    abortStatusFetch();
    const controller = new AbortController();
    statusFetchController = controller;
    const remaining = batchDeadline ? Math.max(1, batchDeadline - Date.now()) : STATUS_FETCH_TIMEOUT_MS;
    const fetchTimer = setTimeout(() => controller.abort(), Math.min(STATUS_FETCH_TIMEOUT_MS, remaining));
    try {
      const response = await fetch("/api/strategy/batch", { signal: controller.signal });
      if (!response.ok) throw new Error(await responseDetail(response));
      const data = await response.json();
      if (generation !== batchGeneration) return;
      if (batchDeadline && Date.now() >= batchDeadline) {
        timeoutPolling();
        return null;
      }
      applyBatchState(data && typeof data === "object" ? data : {}, options);
      return data;
    } catch (error) {
      if (generation !== batchGeneration) return;
      if (!pageActive && error.name === "AbortError") return null;
      if (batchDeadline && Date.now() >= batchDeadline) {
        timeoutPolling();
        return null;
      }
      const message = error.name === "AbortError" ? "狀態查詢逾時" : error.message;
      if (options.polling && pageActive && Date.now() < batchDeadline) {
        batchFailureCount += 1;
        if (batchFailureCount <= 3) {
          const retryDelay = POLL_INTERVAL_MS * (2 ** (batchFailureCount - 1));
          setRunning(true);
          setStatus(`連線發生問題，將在 ${retryDelay / 1000} 秒後重試（${batchFailureCount}/3）：${message}`, true);
          schedulePoll(retryDelay);
          return null;
        }
      }
      stopPolling();
      clearDeadlineTimer();
      batchDeadline = 0;
      setRunning(false);
      setStatus(`讀取批次狀態失敗：${message}`, true);
      return null;
    } finally {
      clearTimeout(fetchTimer);
      if (statusFetchController === controller) statusFetchController = null;
    }
  }

  async function runBatch() {
    if (demoMode) return;
    const tickers = selectedTickers();
    if (!tickers.length) {
      setStatus("請至少選擇一檔股票。", true);
      return;
    }
    if (!window.confirm(`即將對 ${tickers.length} 檔股票執行策略檢查，會消耗 AI 額度。確定繼續嗎？`)) return;

    stopPolling();
    const generation = ++batchGeneration;
    batchFailureCount = 0;
    batchDeadline = Date.now() + POLL_LIMIT_MS;
    armDeadlineTimer();
    setRunning(true);
    setStatus("正在啟動批次檢查…", false);
    renderProgress({ done: 0, total: tickers.length, current: tickers[0] });
    markMatrixStale();
    try {
      const response = await fetch("/api/strategy/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tickers }),
      });
      if (generation !== batchGeneration) return;
      if (response.status === 409) {
        const detail = await responseDetail(response);
        if (generation !== batchGeneration) return;
        const attached = await loadBatch({
          runningMessage: `${detail}；另一個批次已在執行，已連接目前進度。`,
        });
        if (!attached || attached.status !== "running") {
          stopPolling();
          clearDeadlineTimer();
          batchDeadline = 0;
          progressEl.hidden = true;
          setRunning(false);
          setStatus(detail, true);
        }
        return;
      }
      if (response.status === 400) {
        const detail = await responseDetail(response);
        if (generation !== batchGeneration) return;
        clearDeadlineTimer();
        batchDeadline = 0;
        progressEl.hidden = true;
        setRunning(false);
        setStatus(detail, true);
        return;
      }
      if (!response.ok) throw new Error(await responseDetail(response));
      const data = await response.json();
      if (generation !== batchGeneration) return;
      setStatus(`已啟動 ${Number(data.total) || tickers.length} 檔批次檢查，約每 3 秒更新一次。`, false);
      await loadBatch({ polling: true });
    } catch (error) {
      if (generation !== batchGeneration) return;
      clearDeadlineTimer();
      batchDeadline = 0;
      progressEl.hidden = true;
      setRunning(false);
      setStatus(`無法執行批次檢查：${error.message}`, true);
    }
  }

  function numberText(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toLocaleString("zh-TW") : "0";
  }

  function costText(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(4) : "0.0000";
  }

  function renderUsage(data) {
    usageContentEl.replaceChildren();
    const summary = data.summary && typeof data.summary === "object" ? data.summary : {};
    const kinds = Object.entries(summary);
    const summaryHeading = textNode("h3", "", "分類摘要");
    usageContentEl.appendChild(summaryHeading);
    if (kinds.length) {
      const wrapper = document.createElement("div");
      wrapper.className = "overview-table-scroll";
      const table = document.createElement("table");
      table.className = "overview-usage-table";
      const header = document.createElement("tr");
      ["種類", "呼叫次數", "秒數", "Tokens", "約 USD（API 等值，訂閱方案不一定實際計費）"].forEach(label => {
        const th = document.createElement("th");
        th.scope = "col";
        th.textContent = label;
        header.appendChild(th);
      });
      const thead = document.createElement("thead");
      thead.appendChild(header);
      table.appendChild(thead);
      const tbody = document.createElement("tbody");
      kinds.forEach(([kind, rawStats]) => {
        const stats = rawStats && typeof rawStats === "object" ? rawStats : {};
        const row = document.createElement("tr");
        [kind, numberText(stats.calls), numberText(stats.seconds), numberText(stats.tokens), costText(stats.cost_usd)].forEach(value => {
          const cell = document.createElement("td");
          cell.textContent = value;
          row.appendChild(cell);
        });
        tbody.appendChild(row);
      });
      table.appendChild(tbody);
      wrapper.appendChild(table);
      usageContentEl.appendChild(wrapper);
    } else {
      usageContentEl.appendChild(textNode("p", "overview-usage-empty", "尚無使用摘要。"));
    }

    usageContentEl.appendChild(textNode("h3", "", "最近呼叫"));
    const recent = Array.isArray(data.recent) ? data.recent : [];
    if (!recent.length) {
      usageContentEl.appendChild(textNode("p", "overview-usage-empty", "尚無呼叫紀錄。"));
      return;
    }
    const wrapper = document.createElement("div");
    wrapper.className = "overview-table-scroll";
    const table = document.createElement("table");
    table.className = "overview-usage-table overview-recent-table";
    const columns = [
      ["時間", "ts"], ["引擎", "engine"], ["標籤", "tag"], ["秒數", "seconds"],
      ["結果", "ok"], ["輸入", "input_tokens"], ["輸出", "output_tokens"],
      ["快取建立", "cache_creation_tokens"], ["快取讀取", "cache_read_tokens"],
      ["總 Tokens", "total_tokens"], ["約 USD", "cost_usd"],
    ];
    const thead = document.createElement("thead");
    const header = document.createElement("tr");
    columns.forEach(([label]) => {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = label;
      header.appendChild(th);
    });
    thead.appendChild(header);
    table.appendChild(thead);
    const tbody = document.createElement("tbody");
    recent.forEach(item => {
      const record = item && typeof item === "object" ? item : {};
      const row = document.createElement("tr");
      columns.forEach(([, key]) => {
        const cell = document.createElement("td");
        if (key === "ok") cell.textContent = record.ok === true ? "成功" : record.ok === false ? "失敗" : "—";
        else if (key === "cost_usd") cell.textContent = costText(record[key]);
        else cell.textContent = record[key] === null || record[key] === undefined ? "—" : String(record[key]);
        row.appendChild(cell);
      });
      tbody.appendChild(row);
    });
    table.appendChild(tbody);
    wrapper.appendChild(table);
    usageContentEl.appendChild(wrapper);
  }

  async function loadUsage() {
    try {
      const response = await fetch("/api/llm-usage");
      if (!response.ok) throw new Error(await responseDetail(response));
      const data = await response.json();
      renderUsage(data && typeof data === "object" ? data : {});
    } catch (error) {
      usageContentEl.replaceChildren(textNode("p", "overview-usage-empty", `無法載入使用紀錄：${error.message}`));
    }
  }

  function leavePage() {
    pageActive = false;
    stopPolling();
    clearDeadlineTimer();
    batchGeneration += 1;
    abortStatusFetch();
  }

  function restorePage() {
    pageActive = true;
    loadUsage();
    if (batchIsRunning && batchDeadline) {
      if (Date.now() >= batchDeadline) timeoutPolling();
      else {
        armDeadlineTimer();
        loadBatch({ polling: true });
      }
      return;
    }
    loadBatch();
  }

  document.getElementById("pickTwBtn").addEventListener("click", () => quickPick("tw"));
  document.getElementById("pickUsBtn").addEventListener("click", () => quickPick("us"));
  document.getElementById("refreshBatchBtn").addEventListener("click", () => loadBatch());
  runButton.addEventListener("click", runBatch);
  window.addEventListener("pagehide", leavePage);
  window.addEventListener("beforeunload", leavePage);
  window.addEventListener("pageshow", event => {
    if (event.persisted || !pageActive) restorePage();
  });

  if (window.sdDemo?.onReady) {
    window.sdDemo.onReady(isDemo => {
      demoMode = isDemo;
      updateSelectionUi();
      applyDemoControl();
    });
  }

  loadStocks();
  loadBatch({ initial: true });
  loadUsage();
})();
