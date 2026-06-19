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
  const symbols = Object.keys(state.symbols);
  if (!selectedSymbol || !state.symbols[selectedSymbol]) selectedSymbol = symbols[0];

  document.getElementById("clock").textContent = `Replay update ${new Date(state.generated_at).toLocaleString()} · ${state.config.interval} bars from ${state.config.period}`;
  const running = document.getElementById("runningPill");
  running.textContent = state.running ? "running" : "paused";
  running.className = `pill ${state.running ? "ok" : "warn"}`;

  document.getElementById("equity").textContent = inr(state.portfolio.equity);
  document.getElementById("cash").textContent = inr(state.portfolio.cash);
  document.getElementById("returnPct").textContent = pct(state.portfolio.return_pct);
  const fills = state.portfolio.fills || [];
  document.getElementById("tradeCount").textContent = fills.length;
  document.getElementById("totalCosts").textContent = inr(fills.reduce((sum, f) => sum + (f.costs || 0), 0));
  const sym = state.symbols[selectedSymbol];
  const progress = sym ? ((sym.cursor + 1) / Math.max(1, sym.total_bars)) * 100 : 0;
  document.getElementById("progressPct").textContent = `${progress.toFixed(1)}%`;

  renderConfig();
  renderSymbolSelect(symbols);
  renderChart(state.symbols[selectedSymbol]);
  renderEquityChart();
  renderSignal(state.symbols[selectedSymbol]);
  renderPositions(symbols);
  renderIndicators(state.symbols[selectedSymbol]);
  renderDepth(state.symbols[selectedSymbol]);
  renderFills();
  renderActivity();
}

function renderConfig() {
  const list = document.getElementById("configList");
  const entries = {
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
  ctx.fillText(`${selectedSymbol} close / SMA10 / SMA30`, 42, 18);
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

function renderPositions(symbols) {
  const body = document.getElementById("positionsBody");
  body.innerHTML = symbols.map(s => {
    const row = state.symbols[s];
    return `<tr><td>${s}</td><td>${row.position}</td><td>${(row.target_position * 100).toFixed(0)}%</td><td>${fmt.format(row.close)}</td></tr>`;
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

function renderActivity() {
  const box = document.getElementById("activity");
  box.innerHTML = (state.events || []).slice().reverse().map(e => `
    <div class="event ${e.kind}"><time>${new Date(e.time).toLocaleString()}</time>${e.message}</div>
  `).join("");
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

connect();
