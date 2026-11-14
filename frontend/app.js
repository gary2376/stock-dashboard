const RANGE_LABEL = { "1m": "一個月", "3m": "三個月", "6m": "半年", "1y": "一年", "3y": "三年" };

const grid = document.getElementById("grid");
const rangeSelect = document.getElementById("rangeSelect");
const newTicker = document.getElementById("newTicker");
const addTickerBtn = document.getElementById("addTickerBtn");
const statusMsg = document.getElementById("statusMsg");
const marketFilterBtns = document.getElementById("marketFilterBtns");
const refreshPricesBtn = document.getElementById("refreshPricesBtn");
const refreshAnalysisBtn = document.getElementById("refreshAnalysisBtn");
const refreshPricesStatus = document.getElementById("refreshPricesStatus");
const refreshAnalysisStatus = document.getElementById("refreshAnalysisStatus");
const sortSelect = document.getElementById("sortSelect");
const sortDirBtn = document.getElementById("sortDirBtn");
const searchInput = document.getElementById("searchInput");
const exportBackupBtn = document.getElementById("exportBackupBtn");
const importBackupBtn = document.getElementById("importBackupBtn");
const importBackupFile = document.getElementById("importBackupFile");
const backupStatus = document.getElementById("backupStatus");
const firstRunEmptyTemplate = document.getElementById("firstRunEmptyTemplate");
const selectModeBtn = document.getElementById("selectModeBtn");
const selectionBar = document.getElementById("selectionBar");
const selectionCount = document.getElementById("selectionCount");
const selectionNote = document.getElementById("selectionNote");
const refreshSelectedBtn = document.getElementById("refreshSelectedBtn");
const cancelSelectionBtn = document.getElementById("cancelSelectionBtn");
const MAX_SELECTED = 20;
let selectionMode = false;
const selectedTickers = new Set();
let analysisRunning = false;
let knownTickerCount = 0;
let marketFilter = "all";
let searchQuery = "";
let sortDir = "asc";
let statusPollTimer = null;
let demoMode = false;
const wasRunning = { prices: false, analysis: false };

function applyDemoControls() {
  if (!demoMode) return;
  [addTickerBtn, refreshPricesBtn, refreshAnalysisBtn, selectModeBtn, refreshSelectedBtn].forEach(button => {
    if (!button) return;
    button.disabled = true;
    button.title = "示範模式：此功能已停用";
    button.setAttribute("aria-describedby", "sdDemoDisabledDescription");
  });
}

if (window.sdDemo?.onReady) {
  window.sdDemo.onReady(isDemo => {
    demoMode = isDemo;
    applyDemoControls();
  });
}


function showStatus(msg, isError = false) {
  statusMsg.textContent = msg;
  statusMsg.classList.toggle("error", isError);
}

function fmtRefreshTime(iso) {
  if (!iso) return "尚未更新過";
  const d = new Date(iso);
  return `上次更新 ${d.toLocaleString("zh-TW", { hour12: false })}`;
}

function updateRefreshUI(kind, state) {
  const btn = kind === "prices" ? refreshPricesBtn : refreshAnalysisBtn;
  const label = kind === "prices" ? refreshPricesStatus : refreshAnalysisStatus;
  btn.disabled = demoMode || state.running;
  if (kind === "analysis") {
    analysisRunning = Boolean(state.running);
    updateSelectionBar();
  }
  if (state.running) {
    label.textContent = kind === "analysis" && Array.isArray(state.tickers) && state.tickers.length
      ? `更新中（${state.tickers.length} 檔）…` : "更新中…";
    label.classList.remove("error");
  } else {
    const failed = state.success === false;
    label.textContent = failed ? `上次更新失敗（${fmtRefreshTime(state.last_run)}）` : fmtRefreshTime(state.last_run);
    label.classList.toggle("error", failed);
  }
}

async function pollRefreshStatus() {
  const res = await fetch("/api/refresh/status");
  const s = await res.json();
  updateRefreshUI("prices", s.prices);
  updateRefreshUI("analysis", s.analysis);

  ["prices", "analysis"].forEach(kind => {
    if (wasRunning[kind] && !s[kind].running) {
      const label = kind === "prices" ? "股價" : "基本面分析";
      if (s[kind].success === false) {
        const reason = (s[kind].last_result || []).slice(-2).join("；");
        showStatus(`${label}更新失敗：${reason || "詳見終端機log"}`, true);
      } else {
        showStatus(`${label}更新完成`);
      }
      load();
    }
    wasRunning[kind] = s[kind].running;
  });

  const anyRunning = s.prices.running || s.analysis.running;
  if (anyRunning && !statusPollTimer) {
    statusPollTimer = setInterval(pollRefreshStatus, 3000);
  } else if (!anyRunning && statusPollTimer) {
    clearInterval(statusPollTimer);
    statusPollTimer = null;
  }
}

async function fetchCostHint() {
  try {
    const res = await fetch("/api/analysis/cost-hint");
    return res.ok ? await res.json() : null;
  } catch (_) {
    return null;
  }
}

// "預估約 $X、約 N 秒" for n tickers, or the generic text when there is no usage history yet
function costHintText(hint, n) {
  const parts = [];
  if (hint && typeof hint.per_ticker_usd === "number") parts.push(`約 $${(hint.per_ticker_usd * n).toFixed(2)}`);
  if (hint && typeof hint.per_ticker_seconds === "number") parts.push(`約 ${Math.round(hint.per_ticker_seconds * n)} 秒`);
  return parts.length ? `預估${parts.join("、")}（依你的 AI 而定）` : `每檔會呼叫一次 AI（共 ${n} 次）`;
}

async function triggerRefresh(kind, tickers = null) {
  if (demoMode) return;
  const options = { method: "POST" };
  if (kind === "analysis") {
    const n = tickers ? tickers.length : knownTickerCount;
    const hintText = costHintText(await fetchCostHint(), n);
    const scope = tickers ? `所選 ${n} 檔` : `全部 ${n} 檔`;
    if (!confirm(`將更新${scope}的 AI 分析。\n${hintText}\n確定嗎？`)) return;
    if (tickers) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify({ tickers });
    }
  }
  const res = await fetch(`/api/refresh/${kind}`, options);
  let data = {};
  try { data = await res.json(); } catch (_) { /* non-JSON error body */ }
  if (!res.ok) {
    const hint = res.status === 503 ? "（設定方式見 docs/ENGINES.md）" : "";
    showStatus(`${kind === "prices" ? "股價" : "基本面分析"}更新無法啟動：${data.detail || res.status}${hint}`, true);
    return;
  }
  showStatus(`${kind === "prices" ? "股價" : "基本面分析"}開始更新…`);
  if (tickers) setSelectionMode(false);
  pollRefreshStatus();
}

function updateSelectionBar() {
  if (!selectionBar) return;
  selectionBar.hidden = !selectionMode;
  const n = selectedTickers.size;
  selectionCount.textContent = `已選 ${n} 檔`;
  selectionNote.textContent = n >= MAX_SELECTED ? `最多一次選 ${MAX_SELECTED} 檔` : (analysisRunning ? "已有分析更新在執行中，完成後才能再按" : "");
  refreshSelectedBtn.disabled = demoMode || analysisRunning || n === 0;
  grid.querySelectorAll(".card-select-input").forEach(input => {
    input.disabled = !input.checked && n >= MAX_SELECTED;
  });
}

function setSelectionMode(on) {
  selectionMode = on;
  if (!on) selectedTickers.clear();
  selectModeBtn.setAttribute("aria-pressed", String(on));
  selectModeBtn.textContent = on ? "✕ 結束選擇" : "☑ 選擇股票更新分析";
  load();
  updateSelectionBar();
}

function fmtPct(v, digits = 1) {
  if (v === null || v === undefined) return "--";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}%`;
}

function fmtMoney(v) {
  if (v === null || v === undefined) return "--";
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtSignedMoney(v) {
  if (v === null || v === undefined) return "--";
  const sign = v > 0 ? "+" : "";
  return `${sign}${fmtMoney(v)}`;
}

function sigmaToX(sigma) {
  const clamped = Math.max(-2.5, Math.min(2.5, sigma));
  return ((clamped + 2.5) / 5) * 100;
}

function displayTicker(t) {
  return t.replace(/\.(TW|TWO)$/, "");
}

function isTwTicker(t) {
  return /\.(TW|TWO)$/.test(t);
}

function sortValue(s, key) {
  switch (key) {
    case "ticker": return displayTicker(s.ticker);
    case "price": return s.price ?? -Infinity;
    case "day_change_pct": return s.day_change_pct ?? -Infinity;
    case "pct_from_high": return s.pct_from_high ?? -Infinity;
    case "sigma": return s.trend.sigma ?? -Infinity;
    case "range_pct": return s.range_position.pct ?? -Infinity;
    case "market_cap": return s.fundamentals.market_cap ?? -Infinity;
    case "holding_pl_pct": return s.holding_pl_pct ?? -Infinity;
    case "holding_pl_amount": return s.holding_pl_amount ?? -Infinity;
    default: return 0;
  }
}

function applySort(stocks) {
  const key = sortSelect.value;
  const sorted = [...stocks].sort((a, b) => {
    const va = sortValue(a, key), vb = sortValue(b, key);
    if (typeof va === "string") return va.localeCompare(vb);
    return va - vb;
  });
  const ordered = sortDir === "desc" ? sorted.reverse() : sorted;
  // favorites always pinned to the top, keeping whatever order the chosen sort gave within each group
  const favs = ordered.filter(s => s.favorite);
  const rest = ordered.filter(s => !s.favorite);
  return [...favs, ...rest];
}

function tagClass(label) {
  if (label === "低位階") return "low";
  if (label === "高位階") return "high";
  return "mid";
}

function gateRow(label, pass) {
  if (pass === null || pass === undefined) {
    return `<div class="gate-row"><span>${label}</span><span class="mark">--</span></div>`;
  }
  return `<div class="gate-row"><span>${label}</span><span class="mark ${pass ? "pass" : "fail"}">${pass ? "✓" : "✕"}</span></div>`;
}

// s.holding is the aggregate across every buy lot: {lots, lot_count, first_date, avg_price,
// total_shares, pl_pct, pl_amount, value} — see backend _holding_summary()
function holdingLine(s) {
  if (!s.holding) return "";
  const h = s.holding;
  const dateLabel = h.lot_count > 1 ? `${h.first_date}起共${h.lot_count}筆` : h.first_date;
  const sharesLabel = h.total_shares ? ` × ${h.total_shares}股` : "";
  const plLabel = s.holding_pl_amount !== null
    ? `${fmtSignedMoney(s.holding_pl_amount)}（${fmtPct(s.holding_pl_pct)}）`
    : fmtPct(s.holding_pl_pct);
  return `<div class="holding-info">持有 ${escapeHtml(dateLabel)} ＠均${fmtMoney(h.avg_price)}${sharesLabel}　<span class="${s.holding_pl_pct >= 0 ? "pl-pos" : "pl-neg"}">損益 ${plLabel}</span></div>`;
}

function renderCard(s, range) {
  const a = s.analysis || {};
  const bullets = (a.profit_bullets || []).map(b => `<li>${escapeHtml(b)}</li>`).join("");
  const bothPass = s.gates.cheap_price && s.gates.profit_not_declining;
  const verdict = escapeHtml(a.verdict || (bothPass ? "兩關都過" : "待觀察"));
  const dayChangeCls = (s.day_change_pct ?? 0) < 0 ? "tag high" : "tag low";
  return `
  <article class="card ${s.favorite ? "favorited" : ""} ${selectedTickers.has(s.ticker) ? "selected" : ""}" data-ticker="${s.ticker}">
    ${selectionMode ? `<label class="card-select"><input type="checkbox" class="card-select-input" data-ticker="${escapeHtml(s.ticker)}" ${selectedTickers.has(s.ticker) ? "checked" : ""} aria-label="選取 ${escapeHtml(s.name)} 更新分析"> 選取</label>` : ""}
    <div class="card-icons">
      <button class="star-btn ${s.favorite ? "active" : ""}" data-ticker="${s.ticker}" data-favorite="${s.favorite}" title="${s.favorite ? "取消關注" : "特別關注（置頂）"}">${s.favorite ? "★" : "☆"}</button>
      <button class="remove-btn" data-ticker="${s.ticker}" title="移除">✕</button>
    </div>
    <div class="card-top">
      <div>
        <div class="ticker">${escapeHtml(s.name)}</div>
        <div class="name">${escapeHtml(displayTicker(s.ticker))}</div>
      </div>
      <div class="price-row">
        <div class="price">${fmtMoney(s.price)}</div>
        <div class="pct-high"><span class="${dayChangeCls}">${fmtPct(s.day_change_pct)}</span> 今日　距高點 ${fmtPct(s.pct_from_high)}</div>
      </div>
    </div>

    ${holdingLine(s)}

    <div class="meter-block">
      <div class="meter-label-row">
        <span>長期趨勢位階</span>
        <span class="tag ${tagClass(s.trend.label)}">${s.trend.label}　${s.trend.sigma > 0 ? "+" : ""}${s.trend.sigma}σ</span>
      </div>
      <div class="meter"><span class="marker" style="left:${sigmaToX(s.trend.sigma)}%"></span></div>
      <div class="meter-desc">${s.trend.desc}</div>
    </div>

    <div class="meter-block">
      <div class="meter-label-row">
        <span>近${RANGE_LABEL[range]}區間位置</span>
        <span class="tag mid">${s.range_position.pct}%</span>
      </div>
      <div class="range-meter"><span class="marker" style="left:${s.range_position.pct}%;position:absolute;top:-3px;width:3px;height:14px;background:#fff;border-radius:2px;transform:translateX(-50%);"></span></div>
      <div class="meter-desc">低 ${fmtMoney(s.range_position.low)}　高 ${fmtMoney(s.range_position.high)}</div>
    </div>

    ${a.why_moved ? `<div class="why-moved"><span class="section-label">為什麼跌／漲：</span> ${escapeHtml(a.why_moved)}</div>` : ""}
    ${bullets ? `<ul class="bullets">${bullets}</ul>` : ""}

    <div class="gates">
      ${gateRow("股價在低位階", s.gates.cheap_price)}
      ${gateRow("獲利沒衰退", s.gates.profit_not_declining)}
    </div>

    <div class="verdict ${bothPass ? "" : "weak"}">${verdict}</div>
  </article>`;
}

function renderFirstRunEmptyState() {
  grid.replaceChildren(firstRunEmptyTemplate.content.cloneNode(true));
  grid.querySelector(".first-run-focus-btn")?.addEventListener("click", () => {
    newTicker.focus();
  });
}

async function load() {
  const range = rangeSelect.value;
  const res = await fetch(`/api/stocks?range=${range}`);
  const allStocks = await res.json();
  knownTickerCount = allStocks.length;
  for (const t of [...selectedTickers]) if (!allStocks.some(s => s.ticker === t)) selectedTickers.delete(t);
  if (!allStocks.length) {
    renderFirstRunEmptyState();
    return;
  }
  const q = searchQuery.trim().toLowerCase();
  const stocks = allStocks.filter(s => {
    if (marketFilter === "us" && isTwTicker(s.ticker)) return false;
    if (marketFilter === "tw" && !isTwTicker(s.ticker)) return false;
    if (q && !displayTicker(s.ticker).toLowerCase().includes(q) && !s.name.toLowerCase().includes(q)) return false;
    return true;
  });
  if (!stocks.length) {
    grid.innerHTML = `<div class="empty">${q ? `找不到符合「${escapeHtml(q)}」的股票` : "這個分類下沒有股票"}</div>`;
    return;
  }
  const sorted = applySort(stocks);
  grid.innerHTML = sorted.map(s => renderCard(s, range)).join("");
  grid.querySelectorAll(".card").forEach(el => {
    el.addEventListener("click", (e) => {
      if (e.target.closest(".card-select")) return;  // the selection checkbox never navigates
      window.location.href = `detail.html?ticker=${el.dataset.ticker}`;
    });
  });
  grid.querySelectorAll(".card-select-input").forEach(input => {
    input.addEventListener("change", () => {
      const t = input.dataset.ticker;
      if (input.checked && selectedTickers.size < MAX_SELECTED) selectedTickers.add(t);
      else selectedTickers.delete(t);
      input.checked = selectedTickers.has(t);
      input.closest(".card").classList.toggle("selected", input.checked);
      updateSelectionBar();
    });
  });
  updateSelectionBar();
  grid.querySelectorAll(".remove-btn").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const t = btn.dataset.ticker;
      if (!confirm(`確定移除 ${t}？`)) return;
      const res = await fetch(`/api/tickers/${t}`, { method: "DELETE" });
      if (!res.ok) {
        showStatus(`移除失敗：${(await res.json()).detail || res.status}`, true);
        return;
      }
      showStatus(`已移除 ${t}`);
      load();
    });
  });
  grid.querySelectorAll(".star-btn").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const t = btn.dataset.ticker;
      const isFavorite = btn.dataset.favorite === "true";
      const res = await fetch(`/api/favorites/${t}`, { method: isFavorite ? "DELETE" : "POST" });
      if (!res.ok) {
        showStatus(`更新關注失敗：${(await res.json()).detail || res.status}`, true);
        return;
      }
      load();
    });
  });
}

async function addTicker() {
  if (demoMode) return;
  const t = newTicker.value.trim().toUpperCase();
  if (!t) return;
  addTickerBtn.disabled = true;
  showStatus(`新增 ${t} 中...`);
  try {
    const res = await fetch("/api/tickers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker: t }),
    });
    const data = await res.json();
    if (!res.ok) {
      showStatus(`新增失敗：${data.detail || res.status}`, true);
      return;
    }
    newTicker.value = "";
    showStatus(`已新增 ${t}，AI 分析背景產生中，稍後重新整理可看到`);
    load();
  } finally {
    addTickerBtn.disabled = demoMode;
  }
}

async function exportBackup() {
  exportBackupBtn.disabled = true;
  try {
    const res = await fetch("/api/backup/export");
    if (!res.ok) {
      showStatus("匯出失敗", true);
      return;
    }
    const data = await res.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `stock-dashboard-backup-${data.exported_at.replace(/[:T]/g, "-")}.json`;
    a.click();
    URL.revokeObjectURL(url);
    backupStatus.textContent = `已匯出 ${data.tickers.length} 檔股票`;
  } finally {
    exportBackupBtn.disabled = false;
  }
}

async function importBackup(file) {
  let data;
  try {
    data = JSON.parse(await file.text());
  } catch (e) {
    showStatus("匯入失敗：不是有效的JSON檔", true);
    return;
  }
  if (!confirm(`匯入會覆蓋目前的觀察清單/關注/持倉/分析（共${(data.tickers || []).length}檔股票），確定嗎？`)) return;
  const res = await fetch("/api/backup/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tickers: data.tickers || [],
      favorites: data.favorites || [],
      holdings: data.holdings || {},
      analysis: data.analysis || {},
    }),
  });
  const result = await res.json();
  if (!res.ok) {
    showStatus(`匯入失敗：${result.detail || res.status}`, true);
    return;
  }
  showStatus(`匯入完成：${result.tickers}檔股票、${result.holdings}筆持倉、${result.analysis}份分析`);
  load();
}

addTickerBtn.addEventListener("click", addTicker);
newTicker.addEventListener("keydown", (e) => { if (e.key === "Enter") addTicker(); });
rangeSelect.addEventListener("change", load);
marketFilterBtns.querySelectorAll("button").forEach(btn => {
  btn.addEventListener("click", () => {
    marketFilter = btn.dataset.market;
    marketFilterBtns.querySelectorAll("button").forEach(b => b.classList.toggle("active", b === btn));
    load();
  });
});
refreshPricesBtn.addEventListener("click", () => triggerRefresh("prices"));
refreshAnalysisBtn.addEventListener("click", () => triggerRefresh("analysis"));
selectModeBtn.addEventListener("click", () => setSelectionMode(!selectionMode));
cancelSelectionBtn.addEventListener("click", () => setSelectionMode(false));
refreshSelectedBtn.addEventListener("click", () => {
  if (selectedTickers.size) triggerRefresh("analysis", [...selectedTickers]);
});
sortSelect.addEventListener("change", load);
sortDirBtn.addEventListener("click", () => {
  sortDir = sortDir === "asc" ? "desc" : "asc";
  sortDirBtn.textContent = sortDir === "asc" ? "↑" : "↓";
  load();
});
searchInput.addEventListener("input", () => {
  searchQuery = searchInput.value;
  load();
});
exportBackupBtn.addEventListener("click", exportBackup);
importBackupBtn.addEventListener("click", () => importBackupFile.click());
importBackupFile.addEventListener("change", () => {
  if (importBackupFile.files[0]) importBackup(importBackupFile.files[0]);
  importBackupFile.value = "";
});
load();
pollRefreshStatus();
