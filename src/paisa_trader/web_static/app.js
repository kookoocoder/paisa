let state = null;
let selectedSymbol = null;

const fmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const inr = (v) => "₹" + fmt.format(v ?? 0);
const pct = (v) => `${(v ?? 0).toFixed(2)}%`;

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
  const isLive = state.mode === "live";
  document.body.classList.toggle("live-mode", isLive);
  document.querySelectorAll(".live-only").forEach((el) => el.classList.toggle("hidden", !isLive));
  document.getElementById("modeLabel").textContent = isLive ? "Live paper trading" : "Autonomous intraday replay";
  document.getElementById("pageTitle").textContent = isLive
    ? "Live Upstox market paper trading"
    : "Streaming historical market as live paper environment";
  document.getElementById("progressMetric").classList.toggle("hidden", isLive);

  const symbols = Object.keys(state.symbols);
  if (!selectedSymbol || !state.symbols[selectedSymbol]) selectedSymbol = symbols[0];

  if (isLive) {
    const refresh = state.last_quote_refresh ? new Date(state.last_quote_refresh).toLocaleString() : "pending";
    document.getElementById("clock").textContent = `Live quote refresh ${refresh} · poll every ${state.config.poll_seconds}s · strategy on ${state.config.trade_symbols.join(", ")}`;
    document.getElementById("marketStatus").textContent = state.market_status || "unknown";
    document.getElementById("universeCount").textContent = String(state.universe_count || 0);
    renderMarketScreener();
    renderTradeCalls();
  } else {
    document.getElementById("clock").textContent = `Replay update ${new Date(state.generated_at).toLocaleString()} · ${state.config.interval} bars from ${state.config.period}`;
    const sym = state.symbols[selectedSymbol];
    const progress = sym ? ((sym.cursor + 1) / Math.max(1, sym.total_bars)) * 100 : 0;
    document.getElementById("progressPct").textContent = `${progress.toFixed(1)}%`;
  }
  const running = document.getElementById("runningPill");
  running.textContent = state.running ? "running" : "paused";
  running.className = `pill ${state.running ? "ok" : "warn"}`;

  document.getElementById("equity").textContent = inr(state.portfolio.equity);
  document.getElementById("cash").textContent = inr(state.portfolio.cash);
  document.getElementById("returnPct").textContent = pct(state.portfolio.return_pct);
  const fills = state.portfolio.fills || [];
  document.getElementById("tradeCount").textContent = fills.length;
  document.getElementById("totalCosts").textContent = inr(fills.reduce((sum, f) => sum + (f.costs || 0), 0));

  renderConfig();
  renderSymbolSelect(symbols);
  renderChart(state.symbols[selectedSymbol]);
  renderEquityChart();
  renderSignal(state.symbols[selectedSymbol]);
  renderPositions(symbols);
  renderIndicators(state.symbols[selectedSymbol]);
  renderDepth(state.symbols[selectedSymbol]);
  renderFills();
  renderCapitalAndTrades();
}

function renderConfig() {
  const list = document.getElementById("configList");
  const isLive = state.mode === "live";
  const entries = isLive
    ? {
        Mode: "live",
        "Trade watchlist": (state.config.trade_symbols || state.config.symbols || []).join(", "),
        Strategy: state.config.strategy || "live-ensemble",
        Execution: state.execution_policy || "live_ltp_only",
        "Feature data": state.feature_data_policy || "cached for models",
        Period: state.config.period,
        Interval: state.config.interval,
        Poll: `${state.config.poll_seconds}s`,
        Intelligence: state.config.use_intelligence_filter ? "on" : "off",
      }
    : {
        Symbols: state.config.symbols.join(", "),
        Strategy: state.config.strategy,
        Period: state.config.period,
        Interval: state.config.interval,
        Speed: `${state.config.tick_seconds}s/bar`,
        Loop: state.config.loop ? "on" : "off",
        Intelligence: state.config.use_intelligence_filter ? "on" : "off",
      };
  list.innerHTML = Object.entries(entries).map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
}

let marketFilter = "";

function renderMarketScreener() {
  const rows = state.market_universe || [];
  const query = marketFilter.trim().toLowerCase();
  const filtered = query
    ? rows.filter((row) => `${row.symbol} ${row.name || ""}`.toLowerCase().includes(query))
    : rows;
  document.getElementById("marketBody").innerHTML = filtered.map((row) => {
    const tone = row.change_pct > 0 ? "positive" : row.change_pct < 0 ? "negative" : "neutral";
    return `
      <tr>
        <td><strong>${escapeHtml(row.symbol)}</strong></td>
        <td>${escapeHtml(row.name || "--")}</td>
        <td>${fmt.format(row.ltp)}</td>
        <td class="${tone}">${Number(row.change_pct || 0).toFixed(2)}%</td>
        <td>${fmt.format(row.open)}</td>
        <td>${fmt.format(row.high)}</td>
        <td>${fmt.format(row.low)}</td>
        <td>${fmt.format(row.volume)}</td>
      </tr>
    `;
  }).join("");
}

function renderSymbolSelect(symbols) {
  const select = document.getElementById("symbolSelect");
  if (select.dataset.symbols !== symbols.join("|")) {
    select.innerHTML = symbols.map(s => `<option value="${s}">${s}</option>`).join("");
    select.dataset.symbols = symbols.join("|");
  }
  select.value = selectedSymbol;
}

function renderEquityChart() {
  const canvas = document.getElementById("equityChart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  const points = state.portfolio.equity_curve || [];
  if (points.length < 2) return;
  const values = points.map(p => p.equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = Math.max((max - min) * 0.08, 1);
  const y = (v) => h - 24 - ((v - min + pad) / (max - min + pad * 2)) * (h - 48);
  const x = (i) => 40 + (i / Math.max(1, points.length - 1)) * (w - 70);
  drawLine(ctx, points.map((p, i) => [x(i), y(p.equity)]), "#1769aa", 2.5);
  const initial = state.portfolio.initial_cash;
  if (initial) {
    const yy = y(initial);
    ctx.strokeStyle = "#aab6c3";
    ctx.setLineDash([6, 4]);
    ctx.beginPath(); ctx.moveTo(35, yy); ctx.lineTo(w - 20, yy); ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.fillStyle = "#657080";
  ctx.font = "12px system-ui";
  ctx.fillText("Portfolio paper equity", 42, 18);
}

function renderChart(symbolState) {
  if (!symbolState) return;
  const canvas = document.getElementById("priceChart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  const candles = symbolState.candles || [];
  if (candles.length < 2) return;
  const values = candles.flatMap(c => [c.high, c.low, c.sma_10, c.sma_30].filter(v => v !== null && v !== undefined));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = Math.max((max - min) * 0.08, 1);
  const y = (v) => h - 24 - ((v - min + pad) / (max - min + pad * 2)) * (h - 48);
  const x = (i) => 40 + (i / Math.max(1, candles.length - 1)) * (w - 70);

  ctx.strokeStyle = "#dfe4ea";
  ctx.lineWidth = 1;
  for (let i = 0; i < 5; i++) {
    const yy = 20 + i * ((h - 40) / 4);
    ctx.beginPath(); ctx.moveTo(35, yy); ctx.lineTo(w - 20, yy); ctx.stroke();
  }

  drawLine(ctx, candles.map((c, i) => [x(i), y(c.close)]), "#1769aa", 2);
  drawLine(ctx, candles.map((c, i) => c.sma_10 == null ? null : [x(i), y(c.sma_10)]), "#177245", 1.5);
  drawLine(ctx, candles.map((c, i) => c.sma_30 == null ? null : [x(i), y(c.sma_30)]), "#8a5b00", 1.5);
  ctx.fillStyle = "#657080";
  ctx.font = "12px system-ui";
  ctx.fillText(`${selectedSymbol} close / SMA10 / SMA30${symbolState.live_ltp ? ` · live ${fmt.format(symbolState.live_ltp)}` : ""}`, 42, 18);
  ctx.fillText(max.toFixed(2), 4, 24);
  ctx.fillText(min.toFixed(2), 4, h - 24);
}

function drawLine(ctx, points, color, width) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.beginPath();
  let open = false;
  for (const p of points) {
    if (!p) { open = false; continue; }
    if (!open) { ctx.moveTo(p[0], p[1]); open = true; }
    else ctx.lineTo(p[0], p[1]);
  }
  ctx.stroke();
}

function renderSignal(symbolState) {
  const sig = symbolState.next_move;
  const box = document.getElementById("signalBox");
  box.innerHTML = `
    <div class="pill ${sig.action === "paper_long_candidate" ? "ok" : sig.action === "avoid_long_or_exit" ? "bad" : "warn"}">${sig.action.replaceAll("_", " ")}</div>
    <div class="score">${sig.score.toFixed(1)}</div>
    <div><strong>${sig.direction.toUpperCase()}</strong> · confidence ${(sig.confidence * 100).toFixed(0)}%</div>
    <div class="reasons">${(sig.reasons || []).join(", ") || "No strong reason yet."}</div>
    <div class="reasons">${(sig.disqualifiers || []).length ? "Disqualified: " + sig.disqualifiers.join(", ") : sig.paper_trade_candidate ? "Paper-trade candidate" : "No trade under current filters"}</div>
  `;
}

function renderTradeCalls() {
  const body = document.getElementById("tradeCallsBody");
  if (!body) return;
  const calls = (state.trade_calls || []).slice().reverse();
  if (!calls.length) {
    body.innerHTML = `<tr><td colspan="9" class="ledger-empty">No live trade calls yet.</td></tr>`;
    return;
  }
  body.innerHTML = calls.map((call) => {
    const time = call.closed_at || call.opened_at || state.generated_at;
    const tone = call.call === "BUY" ? "buy" : call.status === "TARGET_HIT" ? "ok" : "warn";
    return `
      <tr>
        <td>${time ? new Date(time).toLocaleString() : "--"}</td>
        <td><strong>${escapeHtml(call.stock_name || call.symbol)}</strong><br><small>${escapeHtml(call.symbol)}</small></td>
        <td>${escapeHtml(call.segment || "NSE_EQ")}</td>
        <td><span class="pill ${tone}">${escapeHtml(call.call)}</span></td>
        <td>${fmt.format(call.entry_inr)}</td>
        <td>${fmt.format(call.stop_loss_inr || call.trailing_sl_inr)}</td>
        <td>${fmt.format(call.target_inr)}</td>
        <td>${call.exit_inr == null ? "—" : fmt.format(call.exit_inr)}</td>
        <td>${escapeHtml(call.status)}</td>
      </tr>
    `;
  }).join("");
}

function renderPositions(symbols) {
  const body = document.getElementById("positionsBody");
  body.innerHTML = symbols.map(s => {
    const row = state.symbols[s];
    const openCall = row.open_trade_call || (state.open_trade_calls || {})[s];
    const ltp = row.live_ltp ?? row.close;
    return `<tr>
      <td>${s}</td>
      <td>${row.position}</td>
      <td>${fmt.format(ltp)}</td>
      <td>${openCall ? fmt.format(openCall.trailing_sl_inr || openCall.stop_loss_inr) : "--"}</td>
      <td>${openCall ? fmt.format(openCall.target_inr) : "--"}</td>
    </tr>`;
  }).join("");
}

function renderIndicators(symbolState) {
  const body = document.getElementById("indicatorBody");
  body.innerHTML = Object.entries(symbolState.indicators).map(([k, v]) => {
    const value = v === null ? "--" : (typeof v === "number" ? fmt.format(v) : v);
    return `<tr><th>${k}</th><td>${value}</td></tr>`;
  }).join("");
}

function renderDepth(symbolState) {
  const body = document.getElementById("depthBody");
  body.innerHTML = (symbolState.depth || []).map(d => `<tr><td>${d.level}</td><td>${d.side}</td><td>${fmt.format(d.price)}</td><td>${fmt.format(d.quantity)}</td></tr>`).join("");
}

function renderFills() {
  const body = document.getElementById("fillsBody");
  body.innerHTML = (state.portfolio.fills || []).slice().reverse().map(f => `
    <tr><td>${new Date(f.timestamp).toLocaleString()}</td><td>${f.symbol}</td><td>${f.side}</td><td>${f.quantity}</td><td>${fmt.format(f.price)}</td><td>${fmt.format(f.costs)}</td></tr>
  `).join("");
}

function signedInr(value) {
  const amount = Number(value || 0);
  return `${amount >= 0 ? "+" : "-"}${inr(Math.abs(amount))}`;
}

function pnlTone(value) {
  const amount = Number(value || 0);
  if (amount > 0) return "positive";
  if (amount < 0) return "negative";
  return "neutral";
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
    <article class="ledger-item ${isBuy ? "buy" : "sell"}">
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

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

document.getElementById("symbolSelect").addEventListener("change", (event) => {
  selectedSymbol = event.target.value;
  render();
});
document.getElementById("pauseBtn").addEventListener("click", () => control("pause"));
document.getElementById("resumeBtn").addEventListener("click", () => control("resume"));
document.getElementById("resetBtn").addEventListener("click", () => control("reset"));
document.getElementById("copySnapshot").addEventListener("click", async () => {
  const res = await fetch("/api/ai-snapshot");
  const text = JSON.stringify(await res.json(), null, 2);
  await navigator.clipboard.writeText(text);
});
document.getElementById("marketSearch")?.addEventListener("input", (event) => {
  marketFilter = event.target.value;
  renderMarketScreener();
});

connect();
