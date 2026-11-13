// categorical palette (dark-mode steps), fixed order — see dataviz skill reference/palette.md
const CATEGORICAL = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
const GOOD = "#0ca30c";
const BAD = "#e66767";

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
function displayTicker(t) {
  return t.replace(/\.(TW|TWO)$/, "");
}
function plClass(v) {
  if (v === null || v === undefined) return "";
  return v >= 0 ? "pl-pos" : "pl-neg";
}
// s.name/holding.date are external data (yfinance/TWSE company names, user-typed dates) —
// escape before dropping them into innerHTML.
function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function holdingRow(s, totalValue) {
  const h = s.holding;
  const value = s.holding_value;
  const weight = value !== null && totalValue ? (value / totalValue * 100) : null;
  const dateLabel = h.lot_count > 1 ? `${h.first_date}起（${h.lot_count}筆）` : h.first_date;
  return `<tr data-ticker="${s.ticker}">
    <td><strong>${escapeHtml(s.name)}</strong><br><span class="meter-desc">${escapeHtml(displayTicker(s.ticker))}</span></td>
    <td>${escapeHtml(dateLabel)}</td>
    <td>${fmtMoney(h.avg_price)}</td>
    <td>${h.total_shares ?? "--"}</td>
    <td>${fmtMoney(s.price)}</td>
    <td class="${plClass(s.holding_pl_pct)}">${fmtPct(s.holding_pl_pct)}</td>
    <td class="${plClass(s.holding_pl_amount)}">${s.holding_pl_amount !== null && s.holding_pl_amount !== undefined ? fmtSignedMoney(s.holding_pl_amount) : "--"}</td>
    <td>${value !== null && value !== undefined ? fmtMoney(value) : "--"}</td>
    <td>${weight !== null ? `${weight.toFixed(1)}%` : "--"}</td>
  </tr>`;
}

function renderPie(withShares, totalValue) {
  const sorted = [...withShares].sort((a, b) => b.holding_value - a.holding_value);
  const top = sorted.slice(0, 7);
  const rest = sorted.slice(7);
  const labels = top.map(s => s.name);
  const data = top.map(s => s.holding_value);
  if (rest.length) {
    labels.push("其他");
    data.push(rest.reduce((sum, s) => sum + s.holding_value, 0));
  }
  const colors = labels.map((_, i) => CATEGORICAL[i % CATEGORICAL.length]);
  new Chart(document.getElementById("pieChart"), {
    type: "doughnut",
    data: { labels, datasets: [{ data, backgroundColor: colors, borderColor: "#1a1a19", borderWidth: 2 }] },
    options: {
      plugins: {
        legend: { position: "right", labels: { color: "#c3c2b7", font: { size: 12 }, boxWidth: 14 } },
        tooltip: { callbacks: { label: (ctx) => `${ctx.label}: ${fmtMoney(ctx.raw)}（${(ctx.raw / totalValue * 100).toFixed(1)}%）` } },
      },
    },
  });
}

function renderBar(holdings) {
  const sorted = [...holdings].sort((a, b) => (b.holding_pl_pct ?? 0) - (a.holding_pl_pct ?? 0));
  const labels = sorted.map(s => s.name);
  const data = sorted.map(s => s.holding_pl_pct ?? 0);
  const colors = data.map(v => (v >= 0 ? GOOD : BAD));
  new Chart(document.getElementById("barChart"), {
    type: "bar",
    data: { labels, datasets: [{ data, backgroundColor: colors, borderRadius: 4 }] },
    options: {
      indexAxis: "y",
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => `${ctx.raw.toFixed(1)}%` } },
      },
      scales: {
        x: { ticks: { color: "#c3c2b7", callback: (v) => `${v}%` }, grid: { color: "#2c2c2a" } },
        y: { ticks: { color: "#c3c2b7" }, grid: { display: false } },
      },
    },
  });
}

async function load() {
  const root = document.getElementById("dashboardRoot");
  const res = await fetch("/api/stocks?range=1y");
  const stocks = await res.json();
  const holdings = stocks.filter(s => s.holding);

  if (!holdings.length) {
    root.innerHTML = `<div class="empty">還沒有任何持倉紀錄，到個股detail頁的「我的持倉」設定買入日期跟均價。</div>`;
    return;
  }

  const withShares = holdings.filter(s => s.holding.total_shares && s.holding_value !== null && s.holding_value !== undefined);
  const totalValue = withShares.reduce((sum, s) => sum + s.holding_value, 0);
  const totalCost = withShares.reduce((sum, s) => sum + s.holding.total_cost, 0);
  const totalPl = totalValue - totalCost;
  const totalPlPct = totalCost ? (totalPl / totalCost * 100) : null;

  root.innerHTML = `
    <div class="stat-row">
      <div class="stat-tile"><div class="stat-label">總市值</div><div class="stat-value">${fmtMoney(totalValue)}</div></div>
      <div class="stat-tile"><div class="stat-label">總成本</div><div class="stat-value">${fmtMoney(totalCost)}</div></div>
      <div class="stat-tile"><div class="stat-label">總損益</div><div class="stat-value ${plClass(totalPl)}">${fmtSignedMoney(totalPl)}</div></div>
      <div class="stat-tile"><div class="stat-label">總報酬率</div><div class="stat-value ${plClass(totalPlPct)}">${fmtPct(totalPlPct)}</div></div>
    </div>

    <div class="chart-row">
      <div class="panel chart-panel">
        <h3>持倉比重（依市值）</h3>
        ${withShares.length ? '<div class="chart-box"><canvas id="pieChart"></canvas></div>' : '<div class="meter-desc">沒有填股數的持倉，無法算市值比重。到detail頁補上股數即可納入。</div>'}
      </div>
      <div class="panel chart-panel">
        <h3>各檔損益%</h3>
        <div class="chart-box" style="height:${Math.max(180, holdings.length * 34)}px;"><canvas id="barChart"></canvas></div>
      </div>
    </div>

    <div class="panel">
      <h3>持倉明細</h3>
      <div class="table-scroll">
        <table class="holdings-table">
          <thead><tr><th>股票</th><th>買入日期</th><th>均價</th><th>股數</th><th>現價</th><th>損益%</th><th>損益金額</th><th>市值</th><th>佔比</th></tr></thead>
          <tbody>${holdings.map(s => holdingRow(s, totalValue)).join("")}</tbody>
        </table>
      </div>
    </div>
  `;

  if (withShares.length) renderPie(withShares, totalValue);
  renderBar(holdings);

  root.querySelectorAll("tr[data-ticker]").forEach(tr => {
    tr.addEventListener("click", () => { window.location.href = `detail.html?ticker=${tr.dataset.ticker}`; });
  });
}

load();
