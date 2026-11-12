const RANGES = ["1m", "3m", "6m", "1y", "3y"];
const RANGE_LABEL = { "1m": "一個月", "3m": "三個月", "6m": "半年", "1y": "一年", "3y": "三年" };
// mirrors backend RANGES row counts (data_provider.py) so the x-axis window matches the bar math
const RANGE_DAYS = { "1m": 22, "3m": 66, "6m": 130, "1y": 252, "3y": 756 };

const params = new URLSearchParams(window.location.search);
const ticker = params.get("ticker");
const root = document.getElementById("detailRoot");

let chart, series, chartEl, currentRange = "1y";
let fullCandles = [];
let indicatorSeries = {};
let indicatorData = {};
let trendLines = [];
let drawMode = false;
let pendingPoint = null;
let pendingMarkerEl = null;
let currentHolding = null; // {lots, lot_count, first_date, avg_price, total_shares, pl_pct, pl_amount, value} | null
let holdingPriceLine = null;
let radarChart = null;
let strategyPollTimer = null;
let strategyPollDeadline = 0;
let strategyPageActive = true;
let strategyStatusGeneration = 0;
let strategyPollFailureCount = 0;
let strategyCheckIsRunning = false;
let pinsEvalGeneration = 0;

const STRATEGY_POLL_INTERVAL_MS = 3000;
const STRATEGY_POLL_LIMIT_MS = 10 * 60 * 1000;
const STRATEGY_DISCLAIMER = "引文與事實引用由程式驗證；通過／未通過與情境建議是模型的判斷，不代表規則已被證明成立，也不構成投資建議。";
const INDICATOR_STORAGE_KEY = "stock_detail_chart_indicators_v1";
const INDICATORS = [
  { key: "ma10", label: "MA10", period: 10, color: "#f2c14e", defaultOn: false },
  { key: "ma20", label: "MA20", period: 20, color: "#50c9ce", defaultOn: true },
  { key: "ma50", label: "MA50", period: 50, color: "#b388ff", defaultOn: true },
  { key: "ma200", label: "MA200", period: 200, color: "#ff8a65", defaultOn: true },
  { key: "volume", label: "成交量", color: "#9aa0a6", defaultOn: true },
];

function loadIndicatorState() {
  const defaults = Object.fromEntries(INDICATORS.map(item => [item.key, item.defaultOn]));
  try {
    const saved = JSON.parse(localStorage.getItem(INDICATOR_STORAGE_KEY));
    if (!saved || typeof saved !== "object") return defaults;
    INDICATORS.forEach(item => {
      if (typeof saved[item.key] === "boolean") defaults[item.key] = saved[item.key];
    });
  } catch (_) {
    // Storage can be blocked or contain stale data; defaults keep the chart usable.
  }
  return defaults;
}

let indicatorState = loadIndicatorState();

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

  <div class="panel" id="trendPanel">
    <h3>趨勢位階 <span class="meter-desc" id="trendRangeNote">（近${RANGE_LABEL[currentRange]}）</span></h3>
    <div class="meter-label-row">
      <span class="tag ${tagClass(s.trend.label)}" id="trendTag">${s.trend.label}　${s.trend.sigma > 0 ? "+" : ""}${s.trend.sigma}σ</span>
      <span class="meter-desc" id="trendDesc">${s.trend.desc}</span>
    </div>
    <div class="meter" style="margin-top:8px;"><span class="marker" id="trendMarker" style="left:${sigmaToX(s.trend.sigma)}%"></span></div>
  </div>

  <div class="panel" id="holdingPanel">
    <h3>我的持倉</h3>
    <div id="holdingPanelBody">${holdingPanelHtml(s.holding)}</div>
  </div>

  <div class="panel" id="priceChartPanel">
    <h3>走勢圖與區間位置</h3>
    <div class="chart-range-row" id="rangeBtns"></div>
    <div class="indicator-toggle-bar" id="indicatorToggles" role="group" aria-label="圖表指標">
      ${INDICATORS.map(item => `
        <button class="indicator-toggle ${indicatorState[item.key] ? "active" : ""}" type="button"
          data-indicator="${item.key}" aria-pressed="${indicatorState[item.key]}" style="--indicator-color:${item.color}">
          <span class="indicator-swatch" aria-hidden="true"></span>
          ${item.label}
        </button>`).join("")}
    </div>
    <div class="chart-range-row">
      <button class="range-btn" id="drawBtn">✏️ 畫趨勢線</button>
      <button class="range-btn" id="clearDrawBtn">清除畫線</button>
      <span class="meter-desc" id="drawHint"></span>
    </div>
    <div class="chart-crosshair-legend" id="chartCrosshairLegend" aria-hidden="true"></div>
    <div id="chart"></div>
    <div class="meter-label-row" style="margin-top:14px;">
      <span id="rangeLabel">近${RANGE_LABEL[currentRange]}區間位置</span>
      <span class="tag mid" id="rangePct">${s.range_position.pct}%</span>
    </div>
    <div class="range-meter" style="margin-top:6px;"><span class="marker" id="rangeMarker" style="left:${s.range_position.pct}%;position:absolute;top:-3px;width:3px;height:14px;background:#fff;border-radius:2px;transform:translateX(-50%);"></span></div>
    <div class="meter-desc" id="rangeMinMax">低 ${fmtMoney(s.range_position.low)}　高 ${fmtMoney(s.range_position.high)}</div>
  </div>

  <div class="panel" id="fundamentalsPanel">
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

  <section class="panel pinned-eval-panel" id="pinnedChecksPanel" aria-labelledby="pinnedChecksTitle">
    <div class="strategy-check-heading">
      <div>
        <h3 id="pinnedChecksTitle">固定檢查項目</h3>
        <p class="strategy-check-intro">由程式直接計算，不經過 AI；規則解讀由你確認過。</p>
      </div>
      <button class="range-btn" id="refreshPinnedChecksBtn" type="button">重新整理</button>
    </div>
    <p id="pinnedChecksStatus" class="strategy-check-status" role="status" aria-live="polite"></p>
    <div id="pinnedChecksResult" aria-busy="true">
      <p class="strategy-result-empty">正在計算固定檢查項目…</p>
    </div>
  </section>

  <section class="panel strategy-check-panel" id="strategyCheckPanel" aria-labelledby="strategyCheckTitle">
    <div class="strategy-check-heading">
      <div>
        <h3 id="strategyCheckTitle">策略檢查</h3>
        <p class="strategy-check-intro">依照「策略筆記」中的規則檢查這檔股票。</p>
      </div>
      <button class="action-btn" id="runStrategyCheckBtn" type="button">執行策略檢查</button>
    </div>
    <p class="strategy-result-disclaimer">${STRATEGY_DISCLAIMER}</p>
    <p id="strategyCheckStatus" class="strategy-check-status" role="status" aria-live="polite"></p>
    <div id="strategyCheckResult">
      <p class="strategy-result-empty">正在讀取已儲存的檢查結果…</p>
    </div>
  </section>

  <div class="panel" id="investmentAnalysisPanel">
    <h3>投資分析${a.ticker ? "" : "（尚未產生，等候Codex背景產生或執行 scripts/generate_analysis.py）"}</h3>
    ${analysisAsOfHtml(a.as_of)}
    <div class="analysis-score-section">
      <h4>五面向評分</h4>
      <p class="score-evidence-note">這些分數是依規則與 AI 判斷整理出的「閱讀輔助」，<strong>尚無回測證據顯示分數高的股票之後表現較好</strong>（技術面分數的歷史回測與同儕相比沒有顯著差異；財報、估值、新聞三軸無法回測）。請把它當成整理資訊的工具，不要當成買賣依據。</p>
      ${scoreRadarHtml(a.scores)}
      ${scoreReasonsHtml(a.scores, a.score_reasons)}
    </div>
    <div class="analysis-grid">
      <div class="analysis-block"><h4>投資論點</h4><p>${escapeHtml(a.thesis) || "--"}</p></div>
      <div class="analysis-block"><h4>時事與世界局勢影響</h4><p>${escapeHtml(a.world_context) || "--"}</p></div>
      <div class="analysis-block"><h4>反方觀點</h4><p>${escapeHtml(a.bear_case) || "--"}</p></div>
      <div class="analysis-block"><h4>現實檢視</h4><p>${escapeHtml(a.reality_check) || "--"}</p></div>
      ${a.position_note ? `<div class="analysis-block position-note"><h4>你的持倉與情境（模型說明，不影響分數）</h4><p>${escapeHtml(a.position_note)}</p></div>` : ""}
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

function computeMovingAverage(candles, period) {
  const points = [];
  let sum = 0;
  for (let i = 0; i < candles.length; i += 1) {
    sum += candles[i].close;
    if (i >= period) sum -= candles[i - period].close;
    if (i >= period - 1) points.push({ time: candles[i].time, value: sum / period });
  }
  return points;
}

function computeIndicatorData(candles) {
  const data = {};
  INDICATORS.filter(item => item.period).forEach(item => {
    data[item.key] = computeMovingAverage(candles, item.period);
  });
  data.volume = candles
    .filter(candle => Number.isFinite(candle.volume))
    .map(candle => ({
      time: candle.time,
      value: candle.volume,
      color: candle.close >= candle.open ? "rgba(230, 103, 103, 0.38)" : "rgba(57, 135, 229, 0.38)",
    }));
  return data;
}

function persistIndicatorState() {
  try {
    localStorage.setItem(INDICATOR_STORAGE_KEY, JSON.stringify(indicatorState));
  } catch (_) {
    // The controls still work for this page view when storage is unavailable.
  }
}

function applyIndicatorVisibility(key) {
  if (indicatorSeries[key]) indicatorSeries[key].applyOptions({ visible: indicatorState[key] });
  const button = document.querySelector(`[data-indicator="${key}"]`);
  if (!button) return;
  button.classList.toggle("active", indicatorState[key]);
  button.setAttribute("aria-pressed", String(indicatorState[key]));
}

function wireIndicatorToggles() {
  document.querySelectorAll("#indicatorToggles [data-indicator]").forEach(button => {
    button.addEventListener("click", () => {
      const key = button.dataset.indicator;
      indicatorState[key] = !indicatorState[key];
      applyIndicatorVisibility(key);
      persistIndicatorState();
    });
  });
}

function chartTimeKey(time) {
  if (typeof time === "string") return time;
  if (time && typeof time === "object" && "year" in time) {
    return `${time.year}-${String(time.month).padStart(2, "0")}-${String(time.day).padStart(2, "0")}`;
  }
  return "";
}

function formatCompactVolume(value) {
  return new Intl.NumberFormat("zh-TW", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function updateCrosshairLegend(param) {
  const legend = document.getElementById("chartCrosshairLegend");
  const time = chartTimeKey(param.time);
  if (!legend || !time || !param.point) {
    if (legend) {
      legend.textContent = "";
      legend.setAttribute("aria-hidden", "true");
    }
    return;
  }

  const parts = [time];
  INDICATORS.forEach(item => {
    if (!indicatorState[item.key]) return;
    const point = param.seriesData.get(indicatorSeries[item.key]);
    if (!point || !Number.isFinite(point.value)) return;
    parts.push(`${item.label} ${item.key === "volume" ? formatCompactVolume(point.value) : point.value.toFixed(2)}`);
  });
  legend.textContent = parts.join(" · ");
  legend.setAttribute("aria-hidden", "false");
}

function renderChart(candles) {
  fullCandles = candles;
  indicatorData = computeIndicatorData(fullCandles);
  indicatorSeries = {};
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
  series.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.22 } });

  INDICATORS.filter(item => item.period).forEach(item => {
    const line = chart.addLineSeries({
      color: item.color,
      lineWidth: 1,
      lastValueVisible: false,
      priceLineVisible: false,
      visible: indicatorState[item.key],
    });
    line.setData(indicatorData[item.key]);
    indicatorSeries[item.key] = line;
  });

  if (indicatorData.volume.length) {
    const volume = chart.addHistogramSeries({
      priceScaleId: "",
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
      visible: indicatorState.volume,
    });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    volume.setData(indicatorData.volume);
    indicatorSeries.volume = volume;
  }

  chart.subscribeClick(handleChartClick);
  chart.subscribeCrosshairMove(updateCrosshairLegend);
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
  wireIndicatorToggles();
  wireDrawButtons();
  wireStarButton();
  wireHoldingPanel();
  renderChart(full.candles);
  renderScoreRadar(full.analysis?.scores);
  applyHoldingMarker();
  wireStrategyCheckPanel();
  wirePinnedChecksPanel();
}

// --- pinned checks (deterministic, no LLM) -------------------------------

function pinnedStatus(status) {
  return ["pass", "fail", "unknown"].includes(status) ? status : "unknown";
}

function pinnedStatusLabel(status) {
  return { pass: "通過", fail: "未通過", unknown: "未知" }[pinnedStatus(status)];
}

function pinnedBadge(status) {
  const normalized = pinnedStatus(status);
  return strategyText("span", `strategy-status-badge status-${normalized}`, pinnedStatusLabel(normalized));
}

function pinnedEvidence(value) {
  return value === null || value === undefined || value === "" ? "未提供判斷依據" : String(value);
}

function pinnedOverallStatus(result) {
  const statuses = [];
  let missingRequiredSide = false;
  const scope = ["now", "entry", "both"].includes(result.scope) ? result.scope : "both";
  const expectsNow = scope === "now" || scope === "both";
  const expectsEntry = scope === "entry" || scope === "both";
  if (expectsNow) {
    if (result.now && typeof result.now === "object") statuses.push(pinnedStatus(result.now.status));
    else missingRequiredSide = true;
  }
  if (expectsEntry) {
    const entries = Array.isArray(result.entry) ? result.entry.filter(entry => entry && typeof entry === "object") : [];
    if (!entries.length || result.entry_reason) missingRequiredSide = true;
    entries.forEach(entry => statuses.push(pinnedStatus(entry.status)));
  }
  if (missingRequiredSide) return "unknown";
  if (statuses.includes("fail")) return "fail";
  if (!statuses.length || statuses.includes("unknown")) return "unknown";
  return "pass";
}

function renderPinnedChecks(data) {
  const container = document.getElementById("pinnedChecksResult");
  if (!container) return;
  container.replaceChildren();
  container.setAttribute("aria-busy", "false");
  const results = Array.isArray(data?.results) ? data.results.filter(item => item && typeof item === "object") : [];
  if (!results.length) {
    const empty = strategyText("p", "strategy-result-empty", "尚未設定固定檢查項目。");
    const link = document.createElement("a");
    link.href = "strategy.html#pinnedRulesPanel";
    link.textContent = "前往策略筆記新增釘選規則";
    empty.append(document.createTextNode(" "), link);
    container.appendChild(empty);
    return;
  }

  const list = document.createElement("div");
  list.className = "strategy-result-list pinned-eval-list";
  results.forEach((result, index) => {
    const article = strategyText("article", "strategy-result-row pinned-eval-row", "");
    const heading = document.createElement("div");
    heading.className = "strategy-result-row-heading";
    const overallStatus = pinnedOverallStatus(result);
    heading.append(pinnedBadge(overallStatus), strategyText("h4", "", result.text || `固定規則 ${index + 1}`));
    article.appendChild(heading);
    const now = result.now && typeof result.now === "object" ? result.now : null;
    if (now) {
      const line = document.createElement("p");
      line.className = "pinned-eval-line";
      line.append(pinnedBadge(now.status), document.createTextNode(` 現在：${pinnedEvidence(now.evidence)}`));
      article.appendChild(line);
    }
    const entries = Array.isArray(result.entry) ? result.entry.filter(item => item && typeof item === "object") : [];
    entries.forEach((entry, entryIndex) => {
      const line = document.createElement("p");
      line.className = "pinned-eval-line";
      line.append(
        document.createTextNode(`${entry.lot ?? entryIndex + 1}. ${entry.date || "日期未知"} 買入：`),
        pinnedBadge(entry.status),
        document.createTextNode(` ${pinnedEvidence(entry.evidence)}`),
      );
      article.appendChild(line);
    });
    if (result.entry_reason) {
      article.appendChild(strategyText("p", "pinned-eval-reason", `買入當時：${String(result.entry_reason)}`));
    }
    if (overallStatus === "unknown" && result.reason && result.reason !== result.entry_reason) {
      article.appendChild(strategyText("p", "pinned-eval-reason", `未知原因：${String(result.reason)}`));
    }
    list.appendChild(article);
  });
  container.appendChild(list);
}

async function loadPinnedChecks(options = {}) {
  const container = document.getElementById("pinnedChecksResult");
  const status = document.getElementById("pinnedChecksStatus");
  if (!container || !status) return;
  const generation = ++pinsEvalGeneration;
  container.setAttribute("aria-busy", "true");
  status.textContent = options.holdingsChanged ? "持倉已變更，正在重新計算…" : "";
  status.classList.remove("error");
  try {
    const response = await fetch(`/api/pins/eval/${encodeURIComponent(ticker)}`);
    if (!response.ok) throw new Error(await strategyResponseDetail(response));
    const data = await response.json();
    if (generation !== pinsEvalGeneration) return;
    renderPinnedChecks(data && typeof data === "object" ? data : {});
    status.textContent = options.announce || options.holdingsChanged ? "固定檢查項目已更新。" : "";
  } catch (error) {
    if (generation !== pinsEvalGeneration) return;
    container.setAttribute("aria-busy", "false");
    status.textContent = `固定檢查載入失敗：${error.message}`;
    status.classList.add("error");
  }
}

function wirePinnedChecksPanel() {
  document.getElementById("refreshPinnedChecksBtn").addEventListener("click", () => loadPinnedChecks({ announce: true }));
  loadPinnedChecks();
}

// --- strategy check ------------------------------------------------------

function strategyCheckUrl() {
  return `/api/strategy/check/${encodeURIComponent(ticker)}`;
}

async function strategyResponseDetail(response) {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (body.detail) return JSON.stringify(body.detail);
  } catch (_) {
    // A non-JSON server response falls back to its HTTP status below.
  }
  return `HTTP ${response.status}`;
}

function setStrategyCheckStatus(message, isError = false) {
  const status = document.getElementById("strategyCheckStatus");
  if (!status) return;
  status.textContent = message;
  status.classList.toggle("error", isError);
}

function setStrategyCheckRunning(running) {
  strategyCheckIsRunning = running;
  const panel = document.getElementById("strategyCheckPanel");
  const button = document.getElementById("runStrategyCheckBtn");
  if (panel) panel.setAttribute("aria-busy", String(running));
  if (button) {
    button.disabled = running;
    button.textContent = running ? "檢查執行中…" : "執行策略檢查";
  }
}

function strategyText(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text;
  return node;
}

function appendStrategyMeta(container, label, value) {
  if (value === null || value === undefined || value === "") return;
  const item = document.createElement("span");
  const labelNode = document.createElement("strong");
  labelNode.textContent = `${label}：`;
  item.append(labelNode, document.createTextNode(String(value)));
  container.appendChild(item);
}

function strategyStatusLabel(status) {
  return {
    pass: "通過",
    fail: "未通過",
    unknown: "未知",
    conditional: "有條件",
  }[status] || status || "未知";
}

function renderStrategyAdvice(row) {
  const held = typeof row.if_held === "string" ? row.if_held.trim() : "";
  const notHeld = typeof row.if_not_held === "string" ? row.if_not_held.trim() : "";
  if (!held && !notHeld) return null;

  const block = document.createElement("section");
  block.className = "strategy-advice";
  block.appendChild(strategyText("h5", "", "情境建議（模型建議，未經程式驗證）"));
  if (held) {
    const paragraph = document.createElement("p");
    paragraph.append(strategyText("strong", "", "如果已持有："), document.createTextNode(held));
    block.appendChild(paragraph);
  }
  if (notHeld) {
    const paragraph = document.createElement("p");
    paragraph.append(strategyText("strong", "", "如果沒持有："), document.createTextNode(notHeld));
    block.appendChild(paragraph);
  }
  return block;
}

function renderStrategyRow(rawRow, index, scopeLabel = "現在") {
  const row = rawRow && typeof rawRow === "object" ? rawRow : {};
  const article = document.createElement("article");
  article.className = "strategy-result-row";

  const heading = document.createElement("div");
  heading.className = "strategy-result-row-heading";
  const badge = strategyText("span", `strategy-status-badge status-${row.status || "unknown"}`, strategyStatusLabel(row.status));
  const scopeBadge = strategyText("span", "strategy-scope-badge", scopeLabel);
  const rule = strategyText("h4", "", row.rule || `規則 ${index + 1}`);
  heading.append(badge, scopeBadge, rule);
  article.appendChild(heading);

  const source = document.createElement("p");
  source.className = "strategy-result-source";
  const sourceParts = [];
  if (row.source) sourceParts.push(String(row.source));
  if (row.file) sourceParts.push(`${row.file}${row.offset !== null && row.offset !== undefined ? `:${row.offset}` : ""}`);
  source.textContent = sourceParts.length ? `來源：${sourceParts.join(" · ")}` : "來源：未提供";
  article.appendChild(source);

  const quoteDetails = document.createElement("details");
  quoteDetails.className = "strategy-quote-details";
  quoteDetails.appendChild(strategyText("summary", "", "查看逐字引文"));
  quoteDetails.appendChild(strategyText("blockquote", "strategy-verbatim-quote", row.quote || "（無引文）"));
  quoteDetails.appendChild(strategyText("p", "strategy-quote-context", row.context || "（無上下文）"));
  article.appendChild(quoteDetails);

  const evidence = document.createElement("p");
  evidence.className = "strategy-evidence";
  evidence.append(strategyText("strong", "", "判斷依據："), document.createTextNode(String(row.evidence || "（未提供）")));
  article.appendChild(evidence);

  if (Array.isArray(row.fact_refs) && row.fact_refs.length) {
    const refs = document.createElement("p");
    refs.className = "strategy-fact-refs";
    refs.append(strategyText("strong", "", "事實引用："), document.createTextNode(row.fact_refs.map(String).join("、")));
    article.appendChild(refs);
  }

  const advice = renderStrategyAdvice(row);
  if (advice) article.appendChild(advice);
  return article;
}

function renderStrategyWarnings(warnings) {
  if (!Array.isArray(warnings) || !warnings.length) return null;
  const details = document.createElement("details");
  details.className = "strategy-warnings";
  details.appendChild(strategyText("summary", "", `警告（${warnings.length}）`));
  const list = document.createElement("ul");
  warnings.forEach(warning => list.appendChild(strategyText("li", "", String(warning))));
  details.appendChild(list);
  return details;
}

function strategyRowScope(row) {
  return row && row.scope === "entry" ? "entry" : "now";
}

function strategyLotKey(row, fallbackIndex) {
  if (Number.isInteger(row.lot)) return `lot:${row.lot}`;
  if (typeof row.lot_date === "string" && row.lot_date) return `date:${row.lot_date}`;
  return `unknown:${fallbackIndex}`;
}

function strategyEntryPrice(value) {
  if (value === null || value === undefined || value === "") return "--";
  const displayValue = String(value).replace(/\s+price$/i, "");
  const price = Number(displayValue);
  return Number.isFinite(price) ? fmtMoney(price) : displayValue;
}

function renderStrategySection(title, rows, scopeLabel) {
  const section = document.createElement("section");
  section.className = "strategy-result-section";
  section.appendChild(strategyText("h4", "strategy-result-section-title", title));
  if (!rows.length) {
    section.appendChild(strategyText("p", "strategy-result-empty", "目前沒有回傳這個範圍的規則結果。"));
    return section;
  }
  const list = document.createElement("div");
  list.className = "strategy-result-list";
  rows.forEach(({ row, originalIndex }) => list.appendChild(renderStrategyRow(row, originalIndex, scopeLabel)));
  section.appendChild(list);
  return section;
}

function renderStrategyEntrySection(entryRows, rawLots) {
  const lots = Array.isArray(rawLots)
    ? rawLots.map(lot => lot && typeof lot === "object" ? lot : {})
    : [];
  const lotMetaByNumber = new Map();
  const lotMetaByDate = new Map();
  lots.forEach(lot => {
    if (Number.isInteger(lot.lot)) lotMetaByNumber.set(lot.lot, lot);
    if (typeof lot.date === "string" && lot.date) lotMetaByDate.set(lot.date, lot);
  });

  const groups = new Map();
  entryRows.forEach((item, index) => {
    const key = strategyLotKey(item.row, index);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  });

  const section = document.createElement("section");
  section.className = "strategy-result-section strategy-entry-section";
  section.appendChild(strategyText(
    "h4",
    "strategy-result-section-title",
    "買入當時（你每一筆買入在買的那天，依當時的價格與均線資料檢查）",
  ));

  Array.from(groups.values()).forEach((items, groupIndex) => {
    const firstRow = items[0].row;
    const meta = (Number.isInteger(firstRow.lot) && lotMetaByNumber.get(firstRow.lot))
      || (firstRow.lot_date && lotMetaByDate.get(firstRow.lot_date))
      || {};
    const lotNumber = Number.isInteger(firstRow.lot)
      ? firstRow.lot
      : Number.isInteger(meta.lot) ? meta.lot : groupIndex + 1;
    const date = meta.date || firstRow.lot_date || "日期未提供";
    const group = document.createElement("section");
    group.className = "strategy-entry-lot";
    group.appendChild(strategyText(
      "h5",
      "strategy-entry-lot-title",
      `${lotNumber}. ${date} 買入 @${strategyEntryPrice(meta.price)}`,
    ));
    const list = document.createElement("div");
    list.className = "strategy-result-list";
    items.forEach(({ row, originalIndex }) => {
      list.appendChild(renderStrategyRow(row, originalIndex, `買入當時 第${lotNumber}筆`));
    });
    group.appendChild(list);
    section.appendChild(group);
  });
  return section;
}

function renderStrategyResult(result) {
  const container = document.getElementById("strategyCheckResult");
  if (!container) return;
  container.replaceChildren();

  const meta = document.createElement("div");
  meta.className = "strategy-result-meta";
  appendStrategyMeta(meta, "產生時間", result.generated_at);
  appendStrategyMeta(meta, "引擎", result.engine);
  const holdingsText = !Number.isInteger(result.holdings_lots)
    ? "未納入持倉（持倉資料無法讀取）"
    : result.holdings_lots === 0
      ? "目前沒有持倉（持倉相關規則會同時列出「如果已持有／如果沒持有」兩種情境）"
      : `已納入 ${result.holdings_lots} 批持倉（損益為截至今日）`;
  appendStrategyMeta(meta, "持倉", holdingsText);
  container.appendChild(meta);

  const warnings = renderStrategyWarnings(result.warnings);
  if (warnings) container.appendChild(warnings);

  container.appendChild(strategyText(
    "p",
    "strategy-scope-note",
    "「買入當時」只用買入日之前的價格資料重算；當時的財報、新聞、大盤沒有歷史資料，不會被拿來評價過去的買入。損益與「現在」的規則使用今天的資料。",
  ));

  const rows = Array.isArray(result.rows) ? result.rows : [];
  if (!rows.length) {
    container.appendChild(strategyText("p", "strategy-result-empty", "這次檢查沒有回傳規則結果。"));
    container.appendChild(renderStrategySection("現在（這檔股票今天的狀態）", [], "現在"));
    return;
  }
  const scopedRows = rows.map((rawRow, originalIndex) => ({
    row: rawRow && typeof rawRow === "object" ? rawRow : {},
    originalIndex,
  }));
  const entryRows = scopedRows.filter(item => strategyRowScope(item.row) === "entry");
  const nowRows = scopedRows.filter(item => strategyRowScope(item.row) === "now");
  if (entryRows.length) container.appendChild(renderStrategyEntrySection(entryRows, result.entry_lots_checked));
  container.appendChild(renderStrategySection("現在（這檔股票今天的狀態）", nowRows, "現在"));
}

function stopStrategyPolling() {
  if (strategyPollTimer !== null) {
    clearTimeout(strategyPollTimer);
    strategyPollTimer = null;
  }
}

function scheduleStrategyPoll(delay = STRATEGY_POLL_INTERVAL_MS) {
  stopStrategyPolling();
  if (!strategyPageActive) return;
  const remaining = strategyPollDeadline - Date.now();
  if (remaining <= 0) {
    setStrategyCheckRunning(false);
    setStrategyCheckStatus("策略檢查已超過 10 分鐘，已停止自動查詢；可重新整理頁面查看最新狀態。", true);
    return;
  }
  if (delay >= remaining) {
    strategyPollTimer = setTimeout(() => {
      strategyPollTimer = null;
      setStrategyCheckRunning(false);
      setStrategyCheckStatus("策略檢查已超過 10 分鐘，已停止自動查詢；可重新整理頁面查看最新狀態。", true);
    }, remaining);
    return;
  }
  strategyPollTimer = setTimeout(() => loadStrategyCheck({ polling: true }), delay);
}

async function loadStrategyCheck(options = {}) {
  const generation = ++strategyStatusGeneration;
  try {
    const response = await fetch(strategyCheckUrl());
    if (!response.ok) throw new Error(await strategyResponseDetail(response));
    const data = await response.json();
    if (generation !== strategyStatusGeneration) return;
    strategyPollFailureCount = 0;
    if (data.status === "running") {
      setStrategyCheckRunning(true);
      setStrategyCheckStatus("策略檢查執行中，約每 3 秒更新一次。", false);
      if (!strategyPollDeadline || !options.polling) strategyPollDeadline = Date.now() + STRATEGY_POLL_LIMIT_MS;
      scheduleStrategyPoll();
      return;
    }

    stopStrategyPolling();
    strategyPollDeadline = 0;
    setStrategyCheckRunning(false);
    if (data.status === "done" && data.result && String(data.result.ticker || "").toUpperCase() === String(ticker || "").toUpperCase()) {
      renderStrategyResult(data.result);
      setStrategyCheckStatus(options.initial ? "已載入上次儲存的策略檢查結果。" : "策略檢查完成。", false);
    } else if (data.status === "error") {
      setStrategyCheckStatus(`策略檢查失敗：${data.error || "未知錯誤"}`, true);
    } else {
      const container = document.getElementById("strategyCheckResult");
      if (container) {
        container.replaceChildren(strategyText("p", "strategy-result-empty", "尚無策略檢查結果。"));
      }
      setStrategyCheckStatus("", false);
    }
  } catch (error) {
    if (generation !== strategyStatusGeneration) return;
    if (options.polling && strategyPageActive && Date.now() < strategyPollDeadline) {
      strategyPollFailureCount += 1;
      if (strategyPollFailureCount <= 3) {
        const retryDelay = STRATEGY_POLL_INTERVAL_MS * (2 ** (strategyPollFailureCount - 1));
        setStrategyCheckRunning(true);
        setStrategyCheckStatus(`連線發生問題，將在 ${retryDelay / 1000} 秒後重試（${strategyPollFailureCount}/3）：${error.message}`, true);
        scheduleStrategyPoll(retryDelay);
        return;
      }
    }
    stopStrategyPolling();
    strategyPollDeadline = 0;
    setStrategyCheckRunning(false);
    setStrategyCheckStatus(`讀取策略檢查失敗：${error.message}`, true);
  }
}

async function runStrategyCheck() {
  stopStrategyPolling();
  strategyStatusGeneration += 1;
  strategyPollFailureCount = 0;
  strategyPollDeadline = Date.now() + STRATEGY_POLL_LIMIT_MS;
  setStrategyCheckRunning(true);
  setStrategyCheckStatus("正在啟動策略檢查…", false);
  try {
    const response = await fetch(strategyCheckUrl(), { method: "POST" });
    if (response.status === 409) {
      const detail = await strategyResponseDetail(response);
      strategyPollDeadline = 0;
      setStrategyCheckRunning(false);
      setStrategyCheckStatus(detail || "目前已有另一個策略檢查正在執行，請稍後再試。", true);
      return;
    }
    if (!response.ok) throw new Error(await strategyResponseDetail(response));
    setStrategyCheckStatus("策略檢查已啟動，約每 3 秒更新一次。", false);
    await loadStrategyCheck({ polling: true });
  } catch (error) {
    strategyPollDeadline = 0;
    setStrategyCheckRunning(false);
    setStrategyCheckStatus(`無法執行策略檢查：${error.message}`, true);
  }
}

function wireStrategyCheckPanel() {
  document.getElementById("runStrategyCheckBtn").addEventListener("click", runStrategyCheck);
  loadStrategyCheck({ initial: true });
}

function leaveStrategyPage() {
  strategyPageActive = false;
  stopStrategyPolling();
}

function restoreStrategyPage() {
  strategyPageActive = true;
  if (!document.getElementById("strategyCheckPanel")) return;
  if (strategyCheckIsRunning && strategyPollDeadline && Date.now() < strategyPollDeadline) {
    loadStrategyCheck({ polling: true });
  } else {
    loadStrategyCheck();
  }
}

window.addEventListener("pagehide", leaveStrategyPage);
window.addEventListener("beforeunload", leaveStrategyPage);
window.addEventListener("pageshow", event => {
  if (event.persisted || !strategyPageActive) restoreStrategyPage();
});

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

function markStrategyResultStale() {
  const container = document.getElementById("strategyCheckResult");
  if (!container || !container.querySelector(".strategy-result-meta") || container.querySelector(".strategy-stale-notice")) return;
  const notice = strategyText("p", "strategy-stale-notice", "你的持倉在這份結果產生後有變更，下面的內容可能已過時，請重新執行策略檢查。");
  notice.setAttribute("role", "status");
  container.prepend(notice);
}

function renderHoldingPanel(holding) {
  markStrategyResultStale(); // initial render uses holdingPanelHtml directly, so every call here is a change
  currentHolding = holding;
  document.getElementById("holdingPanelBody").innerHTML = holdingPanelHtml(holding);
  wireHoldingPanel();
  applyHoldingMarker();
  loadPinnedChecks({ holdingsChanged: true });
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
