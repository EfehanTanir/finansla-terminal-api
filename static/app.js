// Finansla Terminal — renders the dashboard from /api/dashboard
const $ = (id) => document.getElementById(id);
const pct = (n) => (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
const cls = (n) => (n >= 0 ? "up-v" : "down-v");
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

async function load() {
  try {
    const res = await fetch("/api/dashboard", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    render(await res.json());
  } catch (e) {
    $("pulseBody").innerHTML = `<p class="loading">Veri alınamadı: ${esc(e.message)}. Backend çalışıyor mu?</p>`;
  }
}

function render(d) {
  renderTape(d.tickers);
  renderPulse(d.pulse);
  renderStats(d.hero_stats);
  renderMovers("gainers", d.gainers, true);
  renderMovers("losers", d.losers, false);
  renderSpotlight(d.spotlight);
  renderHeatmap(d.heatmap);
  renderFlows(d.flows);
  renderTopFunds(d.top_funds);
  $("asOf").textContent = "· " + d.as_of;
}

function renderTape(tickers) {
  const one = tickers.map((t) => {
    const sign = t.change_pct >= 0 ? "▲" : "▼";
    return `<span class="tape-item"><span class="sym">${esc(t.symbol)}</span>
      <span class="px">${t.unit || ""}${esc(t.price)}</span>
      <span class="${cls(t.change_pct)}">${sign} ${Math.abs(t.change_pct).toFixed(2)}%</span></span>`;
  }).join("");
  $("tapeTrack").innerHTML = one + one; // duplicate for seamless loop
}

function renderPulse(p) {
  $("pulseTitle").textContent = p.title;
  $("pulseBody").innerHTML = p.body.map((para) => `<p>${esc(para)}</p>`).join("");
  $("pulseStats").innerHTML = p.highlights.map((h) => {
    const neg = String(h.unit).trim().startsWith("-");
    return `<div><div class="ps-label">${esc(h.label)}</div>
      <div class="ps-val">${esc(h.value)}</div>
      <div class="ps-delta ${neg ? "down-v" : "up-v"}">${esc(h.unit)}</div></div>`;
  }).join("");
}

function renderStats(stats) {
  $("statRow").innerHTML = stats.map((s) =>
    `<div class="stat"><div><span class="v">${esc(s.value)}</span><span class="u">${esc(s.unit)}</span></div>
     <div class="l">${esc(s.label)}</div></div>`).join("");
}

function renderMovers(id, list, up) {
  $(id).innerHTML = list.map((m) =>
    `<li class="mover"><a class="mover-link" href="/fon/${encodeURIComponent(m.code)}">
      <span class="rk">${String(m.rank).padStart(2, "0")}</span>
      <span class="code">${esc(m.code)}</span>
      <span class="nm">${esc(m.name)}</span>
      <span class="pc ${up ? "up-v" : "down-v"}">${pct(m.change_pct)}</span></a></li>`).join("");
}

function renderSpotlight(s) {
  $("spotlight").innerHTML = `
    <div>
      <div class="sp-eyebrow">AYIN FONU</div>
      <div class="sp-code">${esc(s.code)}</div>
      <div class="sp-name">${esc(s.name)}</div>
      <div class="sp-metrics">
        <div><div class="k">Aylık getiri</div><div class="val">${pct(s.monthly_return_pct)}</div></div>
        <div><div class="k">Büyüklük</div><div class="val neutral">${esc(s.size_label)}</div></div>
      </div>
      <a class="sp-link" href="/fon/${encodeURIComponent(s.code)}">Fonu incele →</a>
    </div>`;
}

function heatColor(v, min, max) {
  // map value -> hue from red(0) through amber(45) to green(140)
  const t = max === min ? 1 : (v - min) / (max - min);
  const hue = 0 + t * 140;
  return `hsla(${hue},62%,${18 + t * 12}%,1)`;
}

function renderHeatmap(cells) {
  const vals = cells.map((c) => c.value_pct);
  const min = Math.min(...vals), max = Math.max(...vals);
  $("heatmap").innerHTML = cells.map((c) =>
    `<div class="heat-cell" style="background:${heatColor(c.value_pct, min, max)}">
       <div class="hl">${esc(c.label)}</div>
       <div class="hv">+${c.value_pct.toFixed(1)}%</div></div>`).join("");
}

function renderFlows(flows) {
  $("flows").innerHTML = flows.map((f) =>
    `<div class="flow">
       <div class="flow-top"><span class="fc">${esc(f.code)}</span>
         <span class="fn">${esc(f.name)}</span>
         <span class="fv">${esc(f.net_flow_label)}</span></div>
       <div class="bar"><i style="width:${f.fill_pct}%"></i></div></div>`).join("");
}

function renderTopFunds(funds) {
  $("topFunds").innerHTML = funds.map((f) =>
    `<a class="tf" href="/fon/${encodeURIComponent(f.code)}"><span class="rk">${String(f.rank).padStart(2, "0")}</span>
       <div class="info"><b>${esc(f.name)}</b><span>${esc(f.code)} · ${esc(f.category)}</span></div>
       <div class="size"><div class="rl">Büyüklük</div><div class="sz">${esc(f.size_label)}</div></div>
       <div class="ret ${cls(f.return_1y_pct)}">${pct(f.return_1y_pct)}</div></a>`).join("");
}

$("refreshBtn").addEventListener("click", load);
load();
setInterval(load, 60000); // auto-refresh every minute
