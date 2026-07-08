// Finansla — fund list + detail pages
(function () {
  const pct = (n) => (n >= 0 ? "+" : "") + Number(n).toFixed(2) + "%";
  const cls = (n) => (n >= 0 ? "up-v" : "down-v");
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ---------------- Screener ----------------
  async function initScreener() {
    const rows = document.getElementById("rows");
    const qEl = document.getElementById("q");
    let sort = "aum";
    let timer;

    async function refresh() {
      rows.innerHTML = `<div class="loading">Yükleniyor…</div>`;
      try {
        const url = `/api/funds?sort=${sort}&limit=80&q=${encodeURIComponent(qEl.value.trim())}`;
        const data = await (await fetch(url, { cache: "no-store" })).json();
        if (!data.length) { rows.innerHTML = `<div class="loading">Sonuç yok.</div>`; return; }
        rows.innerHTML = data.map((f) => `
          <a class="fund-row" href="/fon/${encodeURIComponent(f.code)}">
            <span class="fr-info"><b>${esc(f.code)}</b><i>${esc(f.name)}</i>
              <small>${esc(f.category)}</small></span>
            <span class="mono fr-price">${esc(f.price)}</span>
            <span class="mono ${cls(f.return_1m_pct)}">${pct(f.return_1m_pct)}</span>
            <span class="mono ${cls(f.return_1y_pct)}">${pct(f.return_1y_pct)}</span>
            <span class="mono fr-size">${esc(f.size_label)}</span>
            <span class="risk risk-${f.risk_score}">${f.risk_score || "—"}</span>
          </a>`).join("");
      } catch (e) {
        rows.innerHTML = `<div class="loading">Hata: ${esc(e.message)}</div>`;
      }
    }

    document.getElementById("sortTabs").addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      [...e.currentTarget.children].forEach((x) => x.classList.remove("active"));
      b.classList.add("active"); sort = b.dataset.sort; refresh();
    });
    qEl.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(refresh, 300); });
    refresh();
  }

  // ---------------- Fund detail ----------------
  async function initFund() {
    const root = document.getElementById("fund");
    const code = decodeURIComponent(location.pathname.split("/").pop() || "").toUpperCase();
    try {
      const f = await (await fetch(`/api/funds/${encodeURIComponent(code)}`, { cache: "no-store" })).json();
      document.title = `${f.code} · ${f.name} — Finansla`;
      root.innerHTML = renderFund(f);
      drawChart(f.history);
      drawDonut(f.allocation);
    } catch (e) {
      root.innerHTML = `<div class="loading" style="padding:60px 0">Fon yüklenemedi: ${esc(e.message)}</div>`;
    }
  }

  function renderFund(f) {
    const risk = `<span class="risk risk-${f.risk_score}">${f.risk_score}/7</span>`;
    const beats = (f.beats || []).map((b) => `<span class="beat">${esc(b)} yener</span>`).join("");
    const returns = (f.returns || []).map((r) => `
      <div class="ret-chip"><div class="rc-k">${esc(r.label)}</div>
        <div class="rc-v ${r.value.startsWith("-") ? "down-v" : "up-v"}">${esc(r.value)}</div></div>`).join("");
    const y1 = (f.returns || []).find((r) => r.label === "1Y");
    return `
      <a class="crumb" href="/fonlar">← Fonlar</a>
      <div class="fund-head">
        <div>
          <div class="fh-code mono">${esc(f.code)}</div>
          <div class="fh-name">${esc(f.name)}</div>
          <div class="fh-meta">${esc(f.category)} · ${esc(f.management_company)} ${risk}</div>
          ${beats ? `<div class="beats">${beats}</div>` : ""}
        </div>
        <div class="fh-price">
          <div class="fp-label">FİYAT · NAV</div>
          <div class="fp-val mono">${esc(f.price)}</div>
          <div class="fp-day mono ${cls(f.return_1d_pct)}">${pct(f.return_1d_pct)} · ${esc(f.price_date)}</div>
          ${y1 ? `<div class="fp-1y mono ${y1.value.startsWith("-") ? "down-v" : "up-v"}">1Y ${esc(y1.value)}</div>` : ""}
        </div>
      </div>

      <div class="ret-row">${returns}</div>

      <div class="two-col" style="margin-top:26px">
        <div class="panel">
          <div class="panel-head">FİYAT GRAFİĞİ <small>dönemsel getiri</small></div>
          <div id="chart" class="chart-wrap"></div>
        </div>
        <div class="panel">
          <div class="panel-head">PORTFÖY DAĞILIMI</div>
          <div class="alloc-wrap"><div id="donut"></div><div id="legend" class="legend"></div></div>
        </div>
      </div>

      <div class="two-col" style="margin-top:20px">
        <div class="panel">
          <div class="panel-head">KALİTE · RİSK</div>
          <div class="metrics">
            ${metric("Sharpe (90g)", f.sharpe_90, f.sharpe_90 >= 0)}
            ${metric("Volatilite (90g)", pct(f.volatility_90_pct), false, true)}
            ${metric("Maks. düşüş (1Y)", pct(f.max_drawdown_1y_pct), false, true)}
            ${metric("Reel getiri (1Y)", pct(f.real_return_1y_pct), f.real_return_1y_pct >= 0)}
          </div>
        </div>
        <div class="panel">
          <div class="panel-head">BÜYÜKLÜK & YATIRIMCI</div>
          <div class="metrics">
            ${metric("Yönetilen varlık", f.size_label, null, true)}
            ${metric("Yatırımcı sayısı", (f.investors || 0).toLocaleString("tr-TR"), null, true)}
            ${metric("ISIN", f.isin || "—", null, true)}
            ${metric("Durum", f.trading_status || "—", null, true)}
          </div>
          ${f.kap_url ? `<a class="kap-link" href="${esc(f.kap_url)}" target="_blank" rel="noopener">KAP fon bilgisi ↗</a>` : ""}
        </div>
      </div>`;
  }

  function metric(k, v, good, neutral) {
    const c = neutral || good == null ? "" : good ? "up-v" : "down-v";
    return `<div class="metric"><div class="m-k">${esc(k)}</div>
      <div class="m-v mono ${c}">${esc(v)}</div></div>`;
  }

  // ---- SVG area chart ----
  function drawChart(history) {
    const el = document.getElementById("chart");
    if (!el || !history || history.length < 2) { if (el) el.innerHTML = '<div class="loading">Grafik verisi yok.</div>'; return; }
    const W = 640, H = 260, pad = 6;
    const prices = history.map((p) => p.price);
    const min = Math.min(...prices), max = Math.max(...prices);
    const x = (i) => pad + (i / (history.length - 1)) * (W - 2 * pad);
    const y = (v) => H - pad - ((v - min) / (max - min || 1)) * (H - 2 * pad);
    let d = `M ${x(0)} ${y(prices[0])}`;
    prices.forEach((p, i) => { if (i) d += ` L ${x(i)} ${y(p)}`; });
    const area = `${d} L ${x(prices.length - 1)} ${H - pad} L ${x(0)} ${H - pad} Z`;
    const up = prices[prices.length - 1] >= prices[0];
    const col = up ? "var(--up)" : "var(--down)";
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="price-chart">
      <defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0%" stop-color="${col}" stop-opacity="0.28"/>
        <stop offset="100%" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>
      <path d="${area}" fill="url(#g)"/>
      <path d="${d}" fill="none" stroke="${col}" stroke-width="2" vector-effect="non-scaling-stroke"/>
    </svg>
    <div class="chart-axis"><span>${esc(history[0].date)}</span><span>${esc(history[history.length - 1].date)}</span></div>`;
  }

  // ---- SVG donut ----
  const DONUT_COLORS = ["#f4a63b", "#5b8dff", "#2fd07a", "#a06bff", "#ff5470", "#8695a8", "#e9c46a", "#39c2c9"];
  function drawDonut(alloc) {
    const el = document.getElementById("donut");
    const leg = document.getElementById("legend");
    if (!el || !alloc || !alloc.length) { if (el) el.innerHTML = ""; return; }
    const total = alloc.reduce((s, a) => s + a.pct, 0) || 100;
    const R = 60, r = 38, C = 80, TAU = Math.PI * 2;
    let start = -Math.PI / 2, arcs = "";
    alloc.forEach((a, i) => {
      const ang = (a.pct / total) * TAU, end = start + ang;
      const large = ang > Math.PI ? 1 : 0;
      const x1 = C + R * Math.cos(start), y1 = C + R * Math.sin(start);
      const x2 = C + R * Math.cos(end), y2 = C + R * Math.sin(end);
      const xi2 = C + r * Math.cos(end), yi2 = C + r * Math.sin(end);
      const xi1 = C + r * Math.cos(start), yi1 = C + r * Math.sin(start);
      arcs += `<path d="M ${x1} ${y1} A ${R} ${R} 0 ${large} 1 ${x2} ${y2} L ${xi2} ${yi2} A ${r} ${r} 0 ${large} 0 ${xi1} ${yi1} Z" fill="${DONUT_COLORS[i % DONUT_COLORS.length]}"/>`;
      start = end;
    });
    const top = alloc[0];
    el.innerHTML = `<svg viewBox="0 0 160 160" class="donut">${arcs}
      <text x="80" y="76" text-anchor="middle" class="donut-c">${esc(top.label.split(" ")[0])}</text>
      <text x="80" y="98" text-anchor="middle" class="donut-p">%${top.pct.toFixed(1)}</text></svg>`;
    leg.innerHTML = alloc.map((a, i) =>
      `<div class="lg-item"><i style="background:${DONUT_COLORS[i % DONUT_COLORS.length]}"></i>
        <span>${esc(a.label)}</span><b class="mono">%${a.pct.toFixed(1)}</b></div>`).join("");
  }

  window.FinanslaPages = { initScreener, initFund };
})();
