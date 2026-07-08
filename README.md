# Finansla Terminal

A financial terminal for `terminal.finansla.net` — TEFAS funds, fund movers,
category heatmap and money flows on one screen. FastAPI backend (deploys to
Render) + a self-contained dark frontend, branded **Finansla**.

Data comes from the Fonoloji API (`https://fonoloji.com/v1`, `X-API-Key` auth).
The backend wraps it behind one provider module and serves the frontend. It ships
with realistic mock data so it runs and looks finished **before** the key is set.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add your key, or leave blank for mock mode
uvicorn app.main:app --reload
```

http://localhost:8000 — full terminal. API: `/api/dashboard` · health: `/api/health`.

## Deploy to Render

1. Push this folder to a GitHub repo.
2. Render → **New → Blueprint**, pick the repo (`render.yaml` is auto-detected).
3. Service **Environment** tab → set `FONOLOJI_API_KEY` (`fon_...`).
4. Deploy → add **Custom Domain** `terminal.finansla.net` → create the CNAME it
   gives you at your DNS.

Live mode switches on automatically once the key is present.

## Important: BIST stock prices are not available via this API

Fonoloji can't redistribute Borsa İstanbul market data (licensing), so
`/stocks/:ticker/price` and `/chart` return **451**. That means **no live stock
price, daily %, previous close, volume, or price chart** for individual BIST
shares. Available for stocks: name, fundamentals (P/E, PB, ROE, dividend, market
cap), analyst targets, fund ownership. **All fund data incl. NAV is available.**

The ticker tape therefore uses **indices + FX + metals** (`/market/live`,
`/gold/live`), not individual stock prices — which is what the API permits.

## Endpoints in use (confirmed from /api-docs)

| Dashboard section | Endpoint |
|---|---|
| Ticker tape       | `/market/live`, `/gold/live` |
| Market pulse (AI) | `/market/digest` |
| Gainers / losers  | `/market/movers` |
| Spotlight fund    | `/market/movers` + `/funds/:code` |
| Category heatmap  | `/categories` |
| Money flows       | `/insights/flow` |
| Biggest funds     | `/funds?sort=aum&order=desc&limit=8` |

The `/funds/:code` shape is confirmed. The list endpoints' exact field names
aren't in the docs, so those mappers are best-effort with per-section mock
fallback. To lock them in:

```bash
export FONOLOJI_API_KEY="fon_..."
python probe.py          # dumps real JSON shapes (~8 requests)
```

Then adjust the `_first(...)` field names in `app/providers/fonoloji.py`.
If a section's shape doesn't match, the terminal shows that section's mock data
and appends "önizleme: <section>" to the footer timestamp — nothing breaks.

## Quota & terms

Free plan is 15,000 requests/month. The backend caches upstream responses for
`CACHE_TTL` seconds (default 60) so repeat visitors don't burn quota. Before
going public under your brand, check Fonoloji's terms of use re: redistributing
their data on another site.

## Pages

- `/`            dashboard (ticker tape, market pulse, movers, heatmap, flows, biggest funds)
- `/fonlar`      fund screener — search + sort by size / 1Y / 1M, links into detail
- `/fon/{code}`  fund detail — price chart, return chips, allocation donut, Sharpe/vol/drawdown, KAP link

All are served by the same FastAPI service, so deploying the one repo publishes the
whole site. Fund pages use `/api/funds`, `/api/funds/{code}`, `/api/funds/{code}/history`.

## Structure

```
app/
  main.py                FastAPI app + static serving
  config.py              env-driven settings
  models.py              data contract (frontend <-> backend)
  providers/fonoloji.py  the only file that knows the upstream API
static/                  index.html, fonlar.html, fon.html, style.css, app.js, pages.js
render.yaml              Render blueprint
probe.py                 dump real response shapes to finalise mappers
```
