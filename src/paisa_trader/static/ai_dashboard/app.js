let state = null;
let selectedSymbol = null;

const fmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const inr = (value) => "₹" + fmt.format(value ?? 0);
const pct = (value) => `${Number(value ?? 0).toFixed(2)}%`;

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => setConnection("connected", "ok");
  ws.onclose = () => {
    setConnection("reconnecting", "warn");
    setTimeout(connect, 1500);
  };
  ws.onerror = () => setConnection("error", "bad");
  ws.onmessage = (event) => {
    state = JSON.parse(event.data);
    render();
  };
}

function setConnection(text, cls) {
  const el = document.getElementById("connection");
  el.textContent = text;
  el.className = `pill ${cls}`;
}

async function control(action) {
  await fetch(`/api/control/${action}`, { method: "POST" });
}

function render() {
  if (!state) return;
  const symbols = Object.keys(state.symbols || {});
  if (!selectedSymbol || !state.symbols[selectedSymbol]) selectedSymbol = symbols[0];
  const symbolState = state.symbols[selectedSymbol] || {};
  const decisions = symbolState.decisions || [];
  const latest = decisions[decisions.length - 1] || null;

  renderHeader(symbols, latest);
  renderChart(symbolState);
  renderLatestDecision(latest);
  renderPredictionTable(visibleDecisions(decisions));
  renderCapitalAndTrades();
  renderTradePnl();
  renderFills();
}

function renderHeader(symbols, latest) {
  const stats = state.prediction_stats || {};
  document.getElementById("clock").textContent =
    `Replay ${new Date(state.generated_at).toLocaleString()} · ${state.config.interval} / ${state.config.period}`;
  document.getElementById("running").textContent = state.running ? "live" : "paused";
  document.getElementById("running").className = `pill ${state.running ? "ok" : "warn"}`;
  document.getElementById("equity").textContent = inr(state.portfolio.equity);
  document.getElementById("returnPct").textContent = `${pct(state.portfolio.return_pct)} return`;
  document.getElementById("hitRate").textContent = `${((stats.hit_rate || 0) * 100).toFixed(0)}%`;
  document.getElementById("hitRateDetail").textContent =
    `${stats.hits || 0} hits / ${stats.misses || 0} misses · ${stats.settled || 0} checked`;
  document.getElementById("latestAction").textContent = latest ? latest.action : "--";
  document.getElementById("latestActionDetail").textContent = latest
    ? latest.fill
      ? `${latest.fill.side} ${latest.fill.quantity} filled`
      : `${Math.round((latest.confidence || 0) * 100)}% · no trade`
    : "waiting";
  document.getElementById("modelName").textContent = `${state.config.model_provider}/${state.config.model_name}`;
  document.getElementById("decisionCount").textContent = `${(state.ai_decisions || []).length} decisions`;
  renderSymbolSelect(symbols);
}

function renderSymbolSelect(symbols) {
  const select = document.getElementById("symbolSelect");
  if (select.dataset.symbols !== symbols.join("|")) {
    select.innerHTML = symbols.map((symbol) => `<option value="${symbol}">${symbol}</option>`).join("");
    select.dataset.symbols = symbols.join("|");
  }
  select.value = selectedSymbol;
}

function renderChart(symbolState) {
  const canvas = document.getElementById("priceChart");
  const ctx = canvas.getContext("2d");
  const candles = symbolState.candles || [];
  const window = symbolState.data_window || {};
  document.getElementById("symbolDataInfo").textContent =
    `${selectedSymbol}: ${window.bars || "--"} bars · ${formatDate(window.start)} to ${formatDate(window.end)}`;
  clear(ctx, canvas);
  if (candles.length < 2) return;

  const values = candles.flatMap((c) => [c.high, c.low, c.bb_upper, c.bb_mid, c.bb_lower].filter((v) => v != null));
  const y = scalerY(canvas.height, Math.min(...values), Math.max(...values));
  const x = (i) => 40 + (i / Math.max(1, candles.length - 1)) * (canvas.width - 70);
  const candleWidth = Math.max(3, Math.min(9, ((canvas.width - 90) / candles.length) * 0.55));

  grid(ctx, canvas);
  candles.forEach((c, i) => drawCandle(ctx, x(i), y, c, candleWidth));
  drawLine(ctx, candles.map((c, i) => c.bb_upper == null ? null : [x(i), y(c.bb_upper)]), "#f5a524", 1);
  drawLine(ctx, candles.map((c, i) => c.bb_mid == null ? null : [x(i), y(c.bb_mid)]), "#64748b", 1);
  drawLine(ctx, candles.map((c, i) => c.bb_lower == null ? null : [x(i), y(c.bb_lower)]), "#f5a524", 1);
  label(ctx, `${selectedSymbol} price with Bollinger bands`, 42, 18);
}

function renderLatestDecision(decision) {
  const el = document.getElementById("latestDecision");
  if (!decision) {
    el.textContent = "Waiting for the first model decision.";
    el.className = "decision-card muted";
    return;
  }
  el.className = "decision-card";
  el.innerHTML = `
    <div class="decision-top">
      <span class="pill ${actionPill(decision.action)}">${decision.action}</span>
      <strong>${Math.round((decision.confidence || 0) * 100)}%</strong>
    </div>
    <p>${escapeHtml(decision.reasoning || "No reasoning returned.")}</p>
    <dl>
      <div><dt>Route</dt><dd>${decision.fill ? `${decision.fill.side} ${decision.fill.quantity} filled` : "No trade executed"}</dd></div>
      <div><dt>Forecasts</dt><dd>${forecastSummaryText(decision)}</dd></div>
      <div><dt>Signals</dt><dd>${(decision.key_signals || []).map(escapeHtml).join(", ") || "--"}</dd></div>
    </dl>
  `;
}

function visibleDecisions(decisions) {
  return decisions.filter((decision) => !isBlockedDecision(decision));
}

function isBlockedDecision(decision) {
  if (decision.fill) return false;
  const reason = String(decision.route_reason || "").toUpperCase();
  return reason.startsWith("BLOCKED") || decision.route_accepted === false;
}

function renderPredictionTable(decisions) {
  const rows = visibleDecisions(decisions).slice().reverse().slice(0, 12);
  if (!rows.length) {
    document.getElementById("predictionTable").innerHTML = "<p class='muted'>No forecasts yet.</p>";
    return;
  }
  document.getElementById("predictionTable").innerHTML = `
    <table>
      <thead><tr><th>Time</th><th>Action</th><th>Forecast</th><th>Evidence</th><th>Fill</th></tr></thead>
      <tbody>
        ${rows.map((d) => `
          <tr>
            <td>${new Date(d.timestamp).toLocaleTimeString()}</td>
            <td><strong>${d.action}</strong><br><span class="muted">${Math.round((d.confidence || 0) * 100)}%</span></td>
            <td>${forecastSummary(d)}</td>
            <td>${evidenceSummary(d)}</td>
            <td>${d.fill ? `${d.fill.side} ${d.fill.quantity}` : "<span class='muted'>none</span>"}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function renderCapitalAndTrades() {
  const stats = state.trade_pnl_stats || {};
  const capital = stats.capital || {};
  const ledger = stats.ledger || [];

  document.getElementById("capitalGrid").innerHTML = `
    <article><span>Total capital deployed</span><strong>${inr(capital.total_capital_deployed_inr)}</strong><small>All BUY fills</small></article>
    <article><span>Currently invested</span><strong>${inr(capital.currently_invested_inr)}</strong><small>Open cost basis</small></article>
    <article><span>Open market value</span><strong>${inr(capital.open_market_value_inr)}</strong><small>Mark-to-market</small></article>
    <article><span>Realized P&amp;L</span><strong class="${pnlTone(capital.realized_pnl_inr)}">${signedInr(capital.realized_pnl_inr)}</strong><small>Closed round trips</small></article>
    <article><span>Unrealized P&amp;L</span><strong class="${pnlTone(capital.unrealized_pnl_inr)}">${signedInr(capital.unrealized_pnl_inr)}</strong><small>Open positions</small></article>
    <article><span>Available cash</span><strong>${inr(state.portfolio.cash)}</strong><small>Paper broker cash</small></article>
  `;

  const box = document.getElementById("tradeLedger");
  if (!ledger.length) {
    box.innerHTML = '<div class="ledger-empty">No executed trades yet.</div>';
    return;
  }
  box.innerHTML = ledger.map((entry) => renderLedgerItem(entry)).join("");
}

function renderLedgerItem(entry) {
  const isBuy = entry.side === "BUY";
  const outcome = entry.outcome || entry.side;
  const outcomeClass = outcome === "PROFIT" ? "ok" : outcome === "LOSS" ? "bad" : isBuy ? "buy" : "warn";
  const detail = isBuy
    ? `<strong>${inr(entry.capital_inr)}</strong> capital deployed`
    : entry.pnl_inr == null
      ? `<strong>${inr(entry.proceeds_inr)}</strong> proceeds`
      : `<strong class="${pnlTone(entry.pnl_inr)}">${signedInr(entry.pnl_inr)}</strong> ${entry.label.toLowerCase()} · ${Number(entry.return_pct || 0).toFixed(2)}%`;

  return `
    <article class="ledger-item ${isBuy ? "buy-side" : "sell-side"}">
      <div class="ledger-top">
        <span class="pill ${outcomeClass}">${entry.side}</span>
        <strong>${escapeHtml(entry.symbol)}</strong>
        <span class="ledger-qty">${entry.quantity} @ ${fmt.format(entry.price_inr)}</span>
      </div>
      <div class="ledger-body">
        <time>${new Date(entry.timestamp).toLocaleString()}</time>
        <div>${detail}</div>
        <small>Costs ${inr(entry.costs_inr)}</small>
      </div>
    </article>
  `;
}

function renderTradePnl() {
  const stats = state.trade_pnl_stats || {};
  const overall = stats.overall || {};
  const portfolio = stats.portfolio || {};
  const profitFactor = overall.profit_factor;
  document.getElementById("pnlMetrics").innerHTML = `
    <article><span>Closed trades</span><strong>${overall.closed_trades || 0}</strong></article>
    <article><span>Win rate</span><strong>${((overall.win_rate || 0) * 100).toFixed(1)}%</strong><small>${overall.winning_trades || 0} wins / ${overall.losing_trades || 0} losses</small></article>
    <article><span>Realized P&amp;L</span><strong class="${pnlTone(portfolio.realized_pnl_inr)}">${signedInr(portfolio.realized_pnl_inr)}</strong></article>
    <article><span>Unrealized P&amp;L</span><strong class="${pnlTone(portfolio.unrealized_pnl_inr)}">${signedInr(portfolio.unrealized_pnl_inr)}</strong></article>
    <article><span>Total costs</span><strong>${inr(overall.total_costs_inr)}</strong></article>
    <article><span>Expectancy / trade</span><strong class="${pnlTone(overall.expectancy_inr)}">${signedInr(overall.expectancy_inr)}</strong><small>Profit factor ${profitFactor == null ? "n/a" : profitFactor.toFixed(2)}</small></article>
  `;

  const bySymbol = Object.entries(stats.by_symbol || {});
  document.getElementById("pnlBySymbolBody").innerHTML = bySymbol.length
    ? bySymbol.map(([symbol, row]) => `
      <tr>
        <td>${escapeHtml(symbol)}</td>
        <td>${row.closed_trades || 0}</td>
        <td>${((row.win_rate || 0) * 100).toFixed(1)}%</td>
        <td class="${pnlTone(row.net_pnl_inr)}">${signedInr(row.net_pnl_inr)}</td>
        <td class="${pnlTone((row.open || {}).unrealized_pnl_inr)}">${signedInr((row.open || {}).unrealized_pnl_inr || 0)}</td>
      </tr>
    `).join("")
    : "<tr><td colspan='5' class='muted'>No closed round trips yet.</td></tr>";

  const openPositions = Object.entries(stats.open_positions || {});
  document.getElementById("pnlOpenBody").innerHTML = openPositions.length
    ? openPositions.map(([symbol, row]) => `
      <tr>
        <td>${escapeHtml(symbol)}</td>
        <td>${row.quantity || 0}</td>
        <td>${fmt.format(row.mark_price_inr || 0)}</td>
        <td>${inr(row.cost_basis_inr)}</td>
        <td class="${pnlTone(row.unrealized_pnl_inr)}">${signedInr(row.unrealized_pnl_inr)}</td>
      </tr>
    `).join("")
    : "<tr><td colspan='5' class='muted'>No open paper positions.</td></tr>";

  const roundTrips = stats.round_trips || [];
  document.getElementById("pnlRoundTripsBody").innerHTML = roundTrips.length
    ? roundTrips.slice().reverse().map((trade) => `
      <tr>
        <td>${escapeHtml(trade.symbol)}</td>
        <td>${formatDate(trade.entry_time)}</td>
        <td>${formatDate(trade.exit_time)}</td>
        <td>${trade.quantity}</td>
        <td>${fmt.format(trade.entry_price)}</td>
        <td>${fmt.format(trade.exit_price)}</td>
        <td class="${pnlTone(trade.net_pnl_inr)}">${signedInr(trade.net_pnl_inr)}</td>
        <td>${Number(trade.return_pct || 0).toFixed(2)}%</td>
        <td><span class="pill ${trade.result === "WIN" ? "ok" : trade.result === "LOSS" ? "bad" : "warn"}">${trade.result}</span></td>
      </tr>
    `).join("")
    : "<tr><td colspan='9' class='muted'>No closed round trips yet.</td></tr>";
}

function signedInr(value) {
  const amount = Number(value || 0);
  return `${amount >= 0 ? "+" : "-"}${inr(Math.abs(amount))}`;
}

function pnlTone(value) {
  const amount = Number(value || 0);
  if (amount > 0) return "buy";
  if (amount < 0) return "sell";
  return "muted";
}

function renderFills() {
  const fills = state.portfolio.fills || [];
  if (!fills.length) {
    document.getElementById("fillsBody").innerHTML = "<tr><td colspan='7' class='muted'>No paper fills yet.</td></tr>";
    return;
  }
  document.getElementById("fillsBody").innerHTML = fills.slice().reverse().map((fill) => `
    <tr>
      <td>${new Date(fill.timestamp).toLocaleTimeString()}</td>
      <td>${fill.symbol}</td>
      <td>${fill.side}</td>
      <td>${fill.quantity}</td>
      <td>${fmt.format(fill.price)}</td>
      <td>${fmt.format(fill.costs)}</td>
      <td>${escapeHtml(fill.reason || "")}</td>
    </tr>
  `).join("");
}

function forecastSummary(decision) {
  const forecasts = decision.future_predictions || [];
  if (!forecasts.length) return `<strong>${decision.predicted_direction || "--"}</strong>`;
  return forecasts.map((f) =>
    `<strong>${f.horizon_label || "+" + f.horizon_bars + " bars"} ${f.direction}</strong><br><span class="muted">${Math.round((f.confidence || 0) * 100)}% · ${f.result}</span>`
  ).join("<br>");
}

function forecastSummaryText(decision) {
  const forecasts = decision.future_predictions || [];
  if (!forecasts.length) return `${decision.predicted_direction || "--"} (${decision.prediction_result || "PENDING"})`;
  return forecasts.map((f) =>
    `${f.horizon_label || "+" + f.horizon_bars + " bars"} ${f.direction} (${Math.round((f.confidence || 0) * 100)}%)`
  ).join(" · ");
}

function evidenceSummary(decision) {
  const forecasts = decision.future_predictions || [];
  if (!forecasts.length) return `<span class="muted">${decision.prediction_result || "PENDING"}</span>`;
  return forecasts.map((f) =>
    `${f.actual_direction || "pending"}<br><span class="muted">${f.actual_return_pct == null ? "waiting" : Number(f.actual_return_pct).toFixed(3) + "%"} · ${f.result}</span>`
  ).join("<br>");
}

function actionPill(action) {
  if (action === "BUY") return "ok";
  if (action === "SELL" || action === "CLOSE") return "bad";
  return "warn";
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString() : "--";
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function clear(ctx, canvas) {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
}

function scalerY(height, min, max) {
  const pad = Math.max((max - min) * 0.08, 1);
  return (v) => height - 24 - ((v - min + pad) / (max - min + pad * 2)) * (height - 48);
}

function grid(ctx, canvas) {
  ctx.fillStyle = "#0b0f14";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#1f2937";
  ctx.lineWidth = 1;
  for (let i = 0; i < 5; i += 1) {
    const y = 20 + i * ((canvas.height - 40) / 4);
    ctx.beginPath();
    ctx.moveTo(35, y);
    ctx.lineTo(canvas.width - 20, y);
    ctx.stroke();
  }
}

function drawCandle(ctx, x, y, candle, width) {
  const bullish = candle.close >= candle.open;
  const color = bullish ? "#22c55e" : "#ef4444";
  const openY = y(candle.open);
  const closeY = y(candle.close);
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  ctx.moveTo(x, y(candle.high));
  ctx.lineTo(x, y(candle.low));
  ctx.stroke();
  ctx.fillRect(x - width / 2, Math.min(openY, closeY), width, Math.max(2, Math.abs(closeY - openY)));
}

function drawLine(ctx, points, color, width) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.beginPath();
  let open = false;
  for (const point of points) {
    if (!point) {
      open = false;
      continue;
    }
    if (!open) {
      ctx.moveTo(point[0], point[1]);
      open = true;
    } else {
      ctx.lineTo(point[0], point[1]);
    }
  }
  ctx.stroke();
}

function label(ctx, text, x, y) {
  ctx.fillStyle = "#cbd5e1";
  ctx.font = "12px system-ui";
  ctx.fillText(text, x, y);
}

document.getElementById("symbolSelect").addEventListener("change", (event) => {
  selectedSymbol = event.target.value;
  render();
});
document.getElementById("pauseBtn").addEventListener("click", () => control("pause"));
document.getElementById("resumeBtn").addEventListener("click", () => control("resume"));
document.getElementById("resetBtn").addEventListener("click", () => control("reset"));

connect();
