const RANGES = ["1m", "3m", "6m", "1y", "3y"];
const RANGE_LABEL = { "1m": "一個月", "3m": "三個月", "6m": "半年", "1y": "一年", "3y": "三年" };
// mirrors backend RANGES row counts (data_provider.py) so the x-axis window matches the bar math
const RANGE_DAYS = { "1m": 22, "3m": 66, "6m": 130, "1y": 252, "3y": 756 };

const params = new URLSearchParams(window.location.search);
const ticker = params.get("ticker");
const root = document.getElementById("detailRoot");

let chart, series, chartEl, currentRange = "1y";
let fullCandles = [];
let trendLines = [];
let drawMode = false;
let pendingPoint = null;
let pendingMarkerEl = null;
let currentHolding = null; // {lots, lot_count, first_date, avg_price, total_shares, pl_pct, pl_amount, value} | null
let holdingPriceLine = null;
let radarChart = null;

const SCORE_KEYS = ["technical", "fundamentals", "valuation", "news", "market"];
const SCORE_LABELS = ["技術面", "財報", "估值", "新聞時事", "市場方向"];
const SCORE_REASON_FALLBACK = "（尚無說明，請重新「更新基本面分析」）";

function fmtMoney(v) {
  if (v === null || v === undefined) return "--";
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtSignedMoney(v) {
  if (v === null || v === undefined) return "--";
  const sign = v > 0 ? "+" : "";
  return `${sign}${fmtMoney(v)}`;
}
function fmtPct(v, digits = 1) {
  if (v === null || v === undefined) return "--";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}%`;
}
function fmtRatio(v) {
  if (v === null || v === undefined) return "--";
  return `${(v * 100).toFixed(1)}%`;
}
function fmtBig(v) {
  if (v === null || v === undefined) return "--";
  if (v >= 1e12) return `${(v / 1e12).toFixed(2)}兆`;
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)}億`;
  return v.toLocaleString();
}
function tagClass(label) {
  if (label === "低位階") return "low";
  if (label === "高位階") return "high";
  return "mid";
}
function sigmaToX(sigma) {
  const clamped = Math.max(-2.5, Math.min(2.5, sigma));
  return ((clamped + 2.5) / 5) * 100;
}
function list(items) {
  if (!items || !items.length) return "<p>--</p>";
  return `<ul class="bullets">${items.map(i => `<li>${escapeHtml(i)}</li>`).join("")}</ul>`;
}
function tradeSignalHtml(text) {
  // trade_signal is written as a " -> " chain (日線 -> 週線 -> 季線 -> 動能 -> 操作訊號) —
  // split it into the same <ul class="bullets"> step list used elsewhere rather than one dense
  // paragraph, so each timeframe's read is its own line.
  if (!text) return "<p>--</p>";
  const steps = text.split(/\s*->\s*/).filter(Boolean);
  return `<ul class="bullets">${steps.map(s => `<li>${escapeHtml(s)}</li>`).join("")}</ul>`;
}
function displayTicker(t) {
  return t.replace(/\.(TW|TWO)$/, "");
}
// LLM-generated analysis text goes through innerHTML templates — escape it so a weird
// company name or adversarial model output can't inject markup.
function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function hasCompleteScores(scores) {
  return scores && SCORE_KEYS.every(key => typeof scores[key] === "number" && Number.isFinite(scores[key]));
}

function scoreRadarHtml(scores) {
  if (!hasCompleteScores(scores)) {
    return '<p class="score-empty">尚無評分資料</p>';
  }
  return '<div class="score-chart-box"><canvas id="scoreRadar" aria-label="投資分析五面向評分雷達圖" role="img"></canvas></div>';
}

function scoreBandClass(score) {
  if (score >= 70) return "score-good";
  if (score < 40) return "score-critical";
  return "score-neutral";
}

function scoreReasonsHtml(scores, reasons) {
  const safeScores = scores && typeof scores === "object" ? scores : {};
  const safeReasons = reasons && typeof reasons === "object" ? reasons : {};
  const rows = SCORE_KEYS.map((key, index) => {
    const score = safeScores[key];
    const hasScore = typeof score === "number" && Number.isFinite(score);
    const reason = typeof safeReasons[key] === "string" && safeReasons[key].trim()
      ? safeReasons[key]
      : SCORE_REASON_FALLBACK;
    const badgeClass = hasScore ? scoreBandClass(score) : "score-missing";
    return `
      <div class="score-reason-row">
        <span class="score-reason-axis">${SCORE_LABELS[index]}</span>
        <span class="score-badge ${badgeClass}">${hasScore ? score : "--"}</span>
        <span class="score-reason-text">${escapeHtml(reason)}</span>
      </div>`;
  }).join("");
  return `<div class="score-reasons" aria-label="五面向分數理由">${rows}</div>`;
}

function analysisAsOfHtml(asOf) {
  if (typeof asOf !== "string" || !asOf.trim()) return "";
  return `<p class="analysis-as-of">分析產生於 ${escapeHtml(asOf.trim())}</p>`;
}

function newsListHtml(items) {
  if (!Array.isArray(items) || items.length === 0) {
    return '<p class="analysis-news-empty">（無）</p>';
  }
  return `<ul class="analysis-news-list">${items.map(rawItem => {
    const item = rawItem && typeof rawItem === "object" ? rawItem : {};
    const date = escapeHtml(item.date);
    const publisher = escapeHtml(item.publisher);
    const title = escapeHtml(item.title) || "--";
    const prefixParts = [date, publisher].filter(Boolean);
    const prefix = prefixParts.length ? `${prefixParts.join(" · ")} — ` : "";
    const hasSafeLink = typeof item.link === "string" && /^https?:\/\//i.test(item.link);
    const renderedTitle = hasSafeLink
      ? `<a href="${escapeHtml(item.link)}" target="_blank" rel="noopener noreferrer">${title}</a>`
      : title;
    return `<li>${prefix}${renderedTitle}</li>`;
  }).join("")}</ul>`;
}

function analysisNewsHtml(news, macro) {
  return `
    <div class="analysis-block analysis-news-panel">
      <h4>相關新聞與總經時事</h4>
      <div class="analysis-news-columns">
        <section class="analysis-news-group" aria-labelledby="companyNewsHeading">
          <h5 id="companyNewsHeading">公司新聞</h5>
          ${newsListHtml(news)}
        </section>
        <section class="analysis-news-group" aria-labelledby="macroNewsHeading">
          <h5 id="macroNewsHeading">總經／世界時事</h5>
          ${newsListHtml(macro)}
        </section>
      </div>
    </div>`;
}

function shell(s) {
  const f = s.fundamentals || {};
  const a = s.analysis || {};
  return `
  <div class="detail-header">
    <div>
      <div class="ticker-row">
        <button id="detailStarBtn" class="star-btn-inline ${s.favorite ? "active" : ""}" data-favorite="${s.favorite}" title="${s.favorite ? "取消關注" : "特別關注（置頂）"}">${s.favorite ? "★" : "☆"}</button>
        <span class="ticker">${escapeHtml(s.name)}</span>
      </div>
      <div class="name">${escapeHtml(displayTicker(s.ticker))}</div>
    </div>
    <div class="price-row">
      <div class="price">${fmtMoney(s.price)}</div>
      <div class="pct-high">今日 ${fmtPct(s.day_change_pct)}　距一年高點 ${fmtPct(s.pct_from_high)}</div>
    </div>
  </div>

  <div class="panel">
    <h3>趨勢位階 <span class="meter-desc" id="trendRangeNote">（近${RANGE_LABEL[currentRange]}）</span></h3>
    <div class="meter-label-row">
      <span class="tag ${tagClass(s.trend.label)}" id="trendTag">${s.trend.label}　${s.trend.sigma > 0 ? "+" : ""}${s.trend.sigma}σ</span>
      <span class="meter-desc" id="trendDesc">${s.trend.desc}</span>
    </div>
    <div class="meter" style="margin-top:8px;"><span class="marker" id="trendMarker" style="left:${sigmaToX(s.trend.sigma)}%"></span></div>
  </div>

  <div class="panel">
    <h3>我的持倉</h3>
    <div id="holdingPanelBody">${holdingPanelHtml(s.holding)}</div>
  </div>

  <div class="panel">
    <h3>走勢圖與區間位置</h3>
    <div class="chart-range-row" id="rangeBtns"></div>
    <div class="chart-range-row">
      <button class="range-btn" id="drawBtn">✏️ 畫趨勢線</button>
      <button class="range-btn" id="clearDrawBtn">清除畫線</button>
      <span class="meter-desc" id="drawHint"></span>
    </div>
    <div id="chart"></div>
    <div class="meter-label-row" style="margin-top:14px;">
      <span id="rangeLabel">近${RANGE_LABEL[currentRange]}區間位置</span>
      <span class="tag mid" id="rangePct">${s.range_position.pct}%</span>
    </div>
    <div class="range-meter" style="margin-top:6px;"><span class="marker" id="rangeMarker" style="left:${s.range_position.pct}%;position:absolute;top:-3px;width:3px;height:14px;background:#fff;border-radius:2px;transform:translateX(-50%);"></span></div>
    <div class="meter-desc" id="rangeMinMax">低 ${fmtMoney(s.range_position.low)}　高 ${fmtMoney(s.range_position.high)}</div>
  </div>

  <div class="panel">
    <h3>基本面</h3>
    <div class="fund-grid">
      <div class="fund-item"><div class="label">本益比 (TTM)</div><div class="value">${f.trailing_pe ? f.trailing_pe.toFixed(1) : "--"}</div></div>
      <div class="fund-item"><div class="label">本益比 (預估)</div><div class="value">${f.forward_pe ? f.forward_pe.toFixed(1) : "--"}</div></div>
      <div class="fund-item"><div class="label">營收年增</div><div class="value">${fmtRatio(f.revenue_growth_yoy)}</div></div>
      <div class="fund-item"><div class="label">獲利年增</div><div class="value">${fmtRatio(f.earnings_growth_yoy)}</div></div>
      <div class="fund-item"><div class="label">毛利率</div><div class="value">${fmtRatio(f.gross_margins)}</div></div>
      <div class="fund-item"><div class="label">市值</div><div class="value">${fmtBig(f.market_cap)}</div></div>
    </div>
  </div>

  <div class="panel">
    <h3>投資分析${a.ticker ? "" : "（尚未產生，等候Codex背景產生或執行 scripts/generate_analysis.py）"}</h3>
    ${analysisAsOfHtml(a.as_of)}
    <div class="analysis-score-section">
      <h4>五面向評分</h4>
      ${scoreRadarHtml(a.scores)}
      ${scoreReasonsHtml(a.scores, a.score_reasons)}
    </div>
    <div class="analysis-grid">
      <div class="analysis-block"><h4>投資論點</h4><p>${escapeHtml(a.thesis) || "--"}</p></div>
      <div class="analysis-block"><h4>時事與世界局勢影響</h4><p>${escapeHtml(a.world_context) || "--"}</p></div>
      <div class="analysis-block"><h4>反方觀點</h4><p>${escapeHtml(a.bear_case) || "--"}</p></div>
      <div class="analysis-block"><h4>現實檢視</h4><p>${escapeHtml(a.reality_check) || "--"}</p></div>
      <div class="analysis-block"><h4>技術操作訊號（日／週／季／動能）</h4>${tradeSignalHtml(a.trade_signal)}</div>
      <div class="analysis-block"><h4>護城河</h4><p>${escapeHtml(a.moat) || "--"}</p></div>
      <div class="analysis-block"><h4>財務體質</h4><p>${escapeHtml(a.financial_health) || "--"}</p></div>
      <div class="analysis-block"><h4>競爭格局</h4><p>${escapeHtml(a.competitive_landscape) || "--"}</p></div>
      <div class="analysis-block"><h4>估值看法</h4><p>${escapeHtml(a.valuation_view) || "--"}</p></div>
      <div class="analysis-block"><h4>近期催化劑</h4>${list(a.catalysts)}</div>
      <div class="analysis-block"><h4>主要風險</h4>${list(a.risks)}</div>
      <div class="analysis-block"><h4>結論</h4><p>${escapeHtml(a.verdict) || "--"}</p></div>
    </div>
    ${analysisNewsHtml(a.news, a.macro)}
  </div>`;
}

function cssColor(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function hexToRgba(hex, alpha) {
  const value = hex.replace("#", "");
  if (!/^[0-9a-f]{6}$/i.test(value)) return `rgba(57, 135, 229, ${alpha})`;
  const number = parseInt(value, 16);
  return `rgba(${number >> 16}, ${(number >> 8) & 255}, ${number & 255}, ${alpha})`;
}

function renderScoreRadar(scores) {
  if (radarChart) {
    radarChart.destroy();
    radarChart = null;
  }
  const canvas = document.getElementById("scoreRadar");
  if (!canvas || !hasCompleteScores(scores)) return;

  const blue = cssColor("--blue");
  const gridline = cssColor("--gridline");
  const labelColor = cssColor("--text-secondary");
  const mutedColor = cssColor("--text-muted");
  radarChart = new Chart(canvas, {
    type: "radar",
    data: {
      labels: SCORE_LABELS,
      datasets: [{
        data: SCORE_KEYS.map(key => scores[key]),
        borderColor: blue,
        backgroundColor: hexToRgba(blue, 0.2),
        pointBackgroundColor: blue,
        pointBorderColor: cssColor("--surface-1"),
        pointHoverBackgroundColor: cssColor("--text-primary"),
        borderWidth: 2,
        pointRadius: 3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 250 },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: context => `${context.raw} / 100` } },
      },
      scales: {
        r: {
          min: 0,
          max: 100,
          beginAtZero: true,
          ticks: { stepSize: 20, display: false },
          grid: { color: gridline },
          angleLines: { color: gridline },
          pointLabels: { color: labelColor, font: { size: 12 } },
          border: { color: mutedColor },
        },
      },
    },
  });
}

function renderRangeButtons() {
  const wrap = document.getElementById("rangeBtns");
  wrap.innerHTML = RANGES.map(r =>
    `<button class="range-btn ${r === currentRange ? "active" : ""}" data-range="${r}">${RANGE_LABEL[r]}</button>`
  ).join("");
  wrap.querySelectorAll("button").forEach(btn => {
    btn.addEventListener("click", () => {
      currentRange = btn.dataset.range;
      wrap.querySelectorAll("button").forEach(b => b.classList.toggle("active", b === btn));
      applyRange();
    });
  });
}

// --- trend-line drawing ---------------------------------------------------

function setDrawMode(on) {
  drawMode = on;
  clearPendingMarker();
  document.getElementById("drawBtn").classList.toggle("active", on);
  chartEl.style.cursor = on ? "crosshair" : "default";
  document.getElementById("drawHint").textContent = on ? "在圖上點第一個點" : "";
}

function clearPendingMarker() {
  pendingPoint = null;
  if (pendingMarkerEl) {
    pendingMarkerEl.remove();
    pendingMarkerEl = null;
  }
}

function placeMarker(x, y) {
  const el = document.createElement("div");
  el.className = "chart-point-marker";
  el.style.left = `${x}px`;
  el.style.top = `${y}px`;
  chartEl.appendChild(el);
  return el;
}

function candleIndex(timeStr) {
  return fullCandles.findIndex(c => c.time === timeStr);
}

// ray: fixed origin at the first click, extended through the second click
// all the way to the edge of the loaded data in that direction (not just a
// segment stopping at the second point)
function rayPoints(a, b) {
  const aIdx = candleIndex(a.time);
  const bIdx = candleIndex(b.time);
  if (aIdx === -1 || bIdx === -1 || aIdx === bIdx) {
    return [{ time: a.time, value: a.price }, { time: b.time, value: b.price }];
  }
  const slope = (b.price - a.price) / (bIdx - aIdx);
  const endIdx = bIdx >= aIdx ? fullCandles.length - 1 : 0;
  const end = { time: fullCandles[endIdx].time, value: a.price + slope * (endIdx - aIdx) };
  const start = { time: a.time, value: a.price };
  return [start, end].sort((p, q) => candleIndex(p.time) - candleIndex(q.time));
}

function handleChartClick(param) {
  if (!drawMode || !param.point || !param.time || !series) return;
  const price = series.coordinateToPrice(param.point.y);
  if (price === null) return;

  if (!pendingPoint) {
    pendingPoint = { time: param.time, price };
    pendingMarkerEl = placeMarker(param.point.x, param.point.y);
    document.getElementById("drawHint").textContent = "再點第二個點完成畫線（射線會延伸到資料邊緣）";
    return;
  }
  if (param.time === pendingPoint.time) return; // same bar, need a different second point

  const a = pendingPoint;
  const b = { time: param.time, price };
  const line = chart.addLineSeries({ color: "#eda100", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
  line.setData(rayPoints(a, b));
  trendLines.push(line);

  clearPendingMarker();
  setDrawMode(false); // one ray per click-in, re-click 畫趨勢線 to draw another
}

function wireDrawButtons() {
  document.getElementById("drawBtn").addEventListener("click", () => setDrawMode(!drawMode));
  document.getElementById("clearDrawBtn").addEventListener("click", () => {
    trendLines.forEach(l => chart.removeSeries(l));
    trendLines = [];
    setDrawMode(false);
  });
}

// --- chart: built once, range switches only pan/zoom the x-axis -----------

function renderChart(candles) {
  fullCandles = candles;
  chartEl = document.getElementById("chart");
  chartEl.innerHTML = "";
  chart = LightweightCharts.createChart(chartEl, {
    layout: { background: { color: "#1a1a19" }, textColor: "#c3c2b7" },
    grid: { vertLines: { color: "#2c2c2a" }, horzLines: { color: "#2c2c2a" } },
    timeScale: { borderColor: "#383835" },
    rightPriceScale: { borderColor: "#383835" },
    height: 380,
  });
  series = chart.addCandlestickSeries({
    upColor: "#e66767", downColor: "#3987e5", borderVisible: false,
    wickUpColor: "#e66767", wickDownColor: "#3987e5",
  });
  series.setData(candles.map(c => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));
  chart.subscribeClick(handleChartClick);
  setChartWindow(currentRange);
}

function setChartWindow(range) {
  if (!fullCandles.length) return;
  const n = Math.min(RANGE_DAYS[range] || fullCandles.length, fullCandles.length);
  if (n >= fullCandles.length) {
    chart.timeScale().fitContent();
    return;
  }
  const from = fullCandles[fullCandles.length - n].time;
  const to = fullCandles[fullCandles.length - 1].time;
  chart.timeScale().setVisibleRange({ from, to });
}

// --- bars (trend sigma / range position) recompute per range via backend --

async function applyRange() {
  const res = await fetch(`/api/stocks/${ticker}/history?range=${currentRange}`);
  const data = await res.json();
  setChartWindow(currentRange); // pan/zoom only; candles + trend lines stay as-is
  document.getElementById("rangeLabel").textContent = `近${RANGE_LABEL[currentRange]}區間位置`;
  document.getElementById("rangePct").textContent = `${data.range_position.pct}%`;
  document.getElementById("rangeMarker").style.left = `${data.range_position.pct}%`;
  document.getElementById("rangeMinMax").textContent = `低 ${fmtMoney(data.range_position.low)}　高 ${fmtMoney(data.range_position.high)}`;
  document.getElementById("trendRangeNote").textContent = `（近${RANGE_LABEL[currentRange]}）`;
  document.getElementById("trendTag").className = `tag ${tagClass(data.trend.label)}`;
  document.getElementById("trendTag").textContent = `${data.trend.label}　${data.trend.sigma > 0 ? "+" : ""}${data.trend.sigma}σ`;
  document.getElementById("trendDesc").textContent = data.trend.desc;
  document.getElementById("trendMarker").style.left = `${sigmaToX(data.trend.sigma)}%`;
}

async function init() {
  if (!ticker) {
    root.innerHTML = `<div class="empty">缺少股票代號</div>`;
    return;
  }
  // fetch the longest window once so the chart always holds the full series;
  // switching range below only adjusts which slice of it is visible.
  const res = await fetch(`/api/stocks/${ticker}?range=3y`);
  if (!res.ok) {
    root.innerHTML = `<div class="empty">找不到 ${ticker}</div>`;
    return;
  }
  const full = await res.json();
  const initial = await (await fetch(`/api/stocks/${ticker}/history?range=${currentRange}`)).json();
  full.trend = initial.trend;
  full.range_position = initial.range_position;

  document.title = `${full.name} ${displayTicker(full.ticker)} - 選股系統`;
  currentHolding = full.holding || null;
  root.innerHTML = shell(full);
  renderRangeButtons();
  wireDrawButtons();
  wireStarButton();
  wireHoldingPanel();
  renderChart(full.candles);
  renderScoreRadar(full.analysis?.scores);
  applyHoldingMarker();
}

// --- holding (買入日期/均價) --------------------------------------------

// holding is the aggregate across every buy lot (backend _holding_summary()):
// {lots, lot_count, first_date, avg_price, total_shares, pl_pct, pl_amount, value} | null
function holdingPanelHtml(holding) {
  const lotRows = holding ? holding.lots.map(lot => `
    <div class="lot-row" data-lot-id="${escapeHtml(lot.id)}">
      <span>${escapeHtml(lot.date)}　＠${fmtMoney(lot.price)}${lot.shares ? `　× ${lot.shares}股` : ""}</span>
      <button class="lot-remove-btn" data-lot-id="${escapeHtml(lot.id)}" title="刪除這筆">✕</button>
    </div>`).join("") : "";

  const summary = holding ? `
    <div class="meter-label-row" style="margin-top:10px;">
      <span>均價${holding.total_shares ? " / 總股數" : ""}</span>
      <span>${fmtMoney(holding.avg_price)}${holding.total_shares ? `　/　${holding.total_shares}股` : ""}</span>
    </div>
    <div class="meter-label-row">
      <span>損益</span>
      <span class="${holding.pl_pct >= 0 ? "pl-pos" : "pl-neg"}">${holding.pl_amount !== null ? `${fmtSignedMoney(holding.pl_amount)}（${fmtPct(holding.pl_pct)}）` : fmtPct(holding.pl_pct)}</span>
    </div>
    ${holding.value !== null ? `<div class="meter-label-row"><span>目前市值</span><span>${fmtMoney(holding.value)}</span></div>` : ""}
  ` : `<div class="meter-desc" style="margin-top:10px;">還沒有任何交易紀錄，輸入買入日期跟均價後按「新增交易」。</div>`;

  return `
    <div class="holding-form">
      <label class="section-label" for="holdingDate">買入日期</label>
      <input type="date" id="holdingDate" class="text-input">
      <label class="section-label" for="holdingPrice">均價</label>
      <input type="number" id="holdingPrice" class="text-input" step="0.01" min="0" placeholder="均價">
      <label class="section-label" for="holdingShares">股數</label>
      <input type="number" id="holdingShares" class="text-input" step="1" min="0" placeholder="股數（選填）">
      <button class="action-btn" id="holdingAddBtn">新增交易</button>
      ${holding ? `<button class="range-btn" id="holdingClearAllBtn">清除全部</button>` : ""}
    </div>
    ${lotRows ? `<div class="lot-list">${lotRows}</div>` : ""}
    <div id="holdingSummary">${summary}</div>
    <label class="checkbox-row">
      <input type="checkbox" id="showHoldingMarker" ${holding ? "checked" : ""} ${holding ? "" : "disabled"}>
      在圖表上顯示買入點
    </label>
  `;
}

function renderHoldingPanel(holding) {
  currentHolding = holding;
  document.getElementById("holdingPanelBody").innerHTML = holdingPanelHtml(holding);
  wireHoldingPanel();
  applyHoldingMarker();
}

function snapToCandleTime(dateStr) {
  if (!fullCandles.length) return dateStr;
  const exact = fullCandles.find(c => c.time === dateStr);
  if (exact) return exact.time;
  const next = fullCandles.find(c => c.time > dateStr);
  return next ? next.time : fullCandles[fullCandles.length - 1].time;
}

function applyHoldingMarker() {
  if (!series) return;
  const show = document.getElementById("showHoldingMarker")?.checked;
  if (holdingPriceLine) {
    series.removePriceLine(holdingPriceLine);
    holdingPriceLine = null;
  }
  if (show && currentHolding && currentHolding.lots.length) {
    // backend already returns lots sorted by date; lightweight-charts markers must be ascending
    series.setMarkers(currentHolding.lots.map(lot => ({
      time: snapToCandleTime(lot.date),
      position: "belowBar",
      color: "#0ca30c",
      shape: "arrowUp",
      text: `買 ${lot.price}${lot.shares ? ` x${lot.shares}` : ""}`,
    })));
    holdingPriceLine = series.createPriceLine({
      price: currentHolding.avg_price,
      color: "#0ca30c",
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: "均價",
    });
  } else {
    series.setMarkers([]);
  }
}

function wireHoldingPanel() {
  document.getElementById("holdingAddBtn").addEventListener("click", async () => {
    const date = document.getElementById("holdingDate").value;
    const price = parseFloat(document.getElementById("holdingPrice").value);
    const sharesRaw = document.getElementById("holdingShares").value;
    const shares = sharesRaw ? parseFloat(sharesRaw) : null;
    if (!date || !price || price <= 0) {
      showDetailStatus("日期跟均價都要填，均價要大於0", true);
      return;
    }
    if (shares !== null && shares <= 0) {
      showDetailStatus("股數要大於0", true);
      return;
    }
    const res = await fetch(`/api/holdings/${ticker}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date, price, shares }),
    });
    const data = await res.json();
    if (!res.ok) {
      showDetailStatus(`新增失敗：${data.detail || res.status}`, true);
      return;
    }
    renderHoldingPanel(data);
    showDetailStatus("已新增交易紀錄");
  });

  const clearAllBtn = document.getElementById("holdingClearAllBtn");
  if (clearAllBtn) {
    clearAllBtn.addEventListener("click", async () => {
      if (!confirm("確定清除這檔股票全部的持倉紀錄？")) return;
      const res = await fetch(`/api/holdings/${ticker}`, { method: "DELETE" });
      if (!res.ok) {
        showDetailStatus("清除失敗，請重試", true);
        return;
      }
      renderHoldingPanel(null);
      showDetailStatus("持倉已全部清除");
    });
  }

  document.querySelectorAll(".lot-remove-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const res = await fetch(`/api/holdings/${ticker}/${btn.dataset.lotId}`, { method: "DELETE" });
      const data = await res.json();
      if (!res.ok) {
        showDetailStatus(`刪除失敗：${data.detail || res.status}`, true);
        return;
      }
      renderHoldingPanel(data);
      showDetailStatus("已刪除該筆交易");
    });
  });

  document.getElementById("showHoldingMarker").addEventListener("change", applyHoldingMarker);
}

function showDetailStatus(msg, isError = false) {
  // lightweight inline feedback; reuses the same visual language as index.html's status line
  let el = document.getElementById("detailStatusMsg");
  if (!el) {
    el = document.createElement("div");
    el.id = "detailStatusMsg";
    el.className = "status-msg";
    document.querySelector(".holding-form").insertAdjacentElement("afterend", el);
  }
  el.textContent = msg;
  el.classList.toggle("error", isError);
  el.style.padding = "4px 0 0";
  setTimeout(() => { if (el) el.textContent = ""; }, 3000);
}

function wireStarButton() {
  const btn = document.getElementById("detailStarBtn");
  btn.addEventListener("click", async () => {
    const isFavorite = btn.dataset.favorite === "true";
    const res = await fetch(`/api/favorites/${ticker}`, { method: isFavorite ? "DELETE" : "POST" });
    if (!res.ok) return;
    const nowFavorite = !isFavorite;
    btn.dataset.favorite = String(nowFavorite);
    btn.classList.toggle("active", nowFavorite);
    btn.textContent = nowFavorite ? "★" : "☆";
    btn.title = nowFavorite ? "取消關注" : "特別關注（置頂）";
  });
}

init();
