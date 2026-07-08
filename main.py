"""
Finansla Terminal API
----------------------
Wraps borsapy to serve BIST stocks, US stocks, and TEFAS funds as JSON.
Deployed on Railway (needs a real Python process — Hostinger shared hosting can't run this).

Endpoints:
  GET /quotes?market=tr        -> BIST watchlist
  GET /quotes?market=us        -> US watchlist
  GET /funds?type=hisse        -> TEFAS funds by category
  GET /health                  -> simple uptime check

CORS is open so terminal.finansla.net (or any origin) can call this directly from the browser.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from functools import lru_cache
import time
import math
import borsapy as bp
import yfinance as yf
import requests
from datetime import date, timedelta
import os
import json
import asyncio
import threading

# ---------------- Fonoloji (TEFAS fund data) ----------------
# Set FONOLOJI_API_KEY in Render's Environment tab. Free tier: 15k req/month.
FONOLOJI_KEY = os.environ.get("FONOLOJI_API_KEY", "")
FONOLOJI_BASE = "https://fonoloji.com/v1"


def fonoloji_get(path, params=None):
    r = requests.get(
        f"{FONOLOJI_BASE}{path}",
        params=params or {},
        headers={"X-API-Key": FONOLOJI_KEY},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def pct(v):
    """Fonoloji returns fractions (0.0914 = +9.14%); convert to percent."""
    return round(float(v) * 100, 2) if v is not None else None

# ---------------- AIS live ship tracking (aisstream.io) ----------------
# The API key is read from the environment — set AISSTREAM_API_KEY in
# Render's dashboard (Environment tab). Never hardcode it here or in the
# frontend, where anyone could read it from page source.
AISSTREAM_KEY = os.environ.get("AISSTREAM_API_KEY", "")

# Latest known ship positions: {mmsi: {...}}
_ships = {}
_ships_lock = threading.Lock()

# Bounding box around Turkish waters + East Med + Black Sea approaches
AIS_BBOX = [[[34.0, 22.0], [47.0, 42.0]]]  # [[lat_min, lon_min], [lat_max, lon_max]]


async def _ais_listener():
    try:
        import websockets
    except ImportError:
        print("websockets not installed; AIS tracking disabled")
        return
    if not AISSTREAM_KEY:
        print("AISSTREAM_API_KEY not set; AIS tracking disabled")
        return

    subscribe = {
        "APIKey": AISSTREAM_KEY,
        "BoundingBoxes": AIS_BBOX,
        "FilterMessageTypes": ["PositionReport"],
    }
    while True:
        try:
            async with websockets.connect("wss://stream.aisstream.io/v0/stream") as ws:
                await ws.send(json.dumps(subscribe))
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("MessageType") != "PositionReport":
                        continue
                    pos = msg.get("Message", {}).get("PositionReport", {})
                    meta = msg.get("MetaData", {})
                    mmsi = meta.get("MMSI")
                    if not mmsi:
                        continue
                    with _ships_lock:
                        _ships[mmsi] = {
                            "mmsi": mmsi,
                            "name": (meta.get("ShipName") or "").strip() or str(mmsi),
                            "lat": pos.get("Latitude"),
                            "lon": pos.get("Longitude"),
                            "sog": pos.get("Sog"),          # speed over ground
                            "cog": pos.get("Cog"),          # course over ground
                            "ts": meta.get("time_utc"),
                        }
                        # keep memory bounded
                        if len(_ships) > 800:
                            oldest = sorted(_ships, key=lambda k: _ships[k].get("ts") or "")[:200]
                            for k in oldest:
                                _ships.pop(k, None)
        except Exception as e:
            print(f"AIS stream error, reconnecting in 10s: {e}")
            await asyncio.sleep(10)


def _start_ais_thread():
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    asyncio.run_coroutine_threadsafe(_ais_listener(), loop)


def clean(obj):
    """Recursively replace NaN/inf with None so json.dumps doesn't crash.
    TEFAS/BIST data frequently has missing fields (e.g. return_3y for new funds)
    that pandas represents as NaN, which is not valid JSON."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    return obj

app = FastAPI(title="Finansla Terminal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to https://terminal.finansla.net once live
    allow_methods=["GET"],
    allow_headers=["*"],
)

TR_SYMBOLS = ["THYAO", "GARAN", "ASELS", "EREGL", "KCHOL", "SAHOL", "AKBNK", "ISCTR"]
US_SYMBOLS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "JPM"]

# Simple in-memory cache: {key: (timestamp, data)}
_cache = {}
CACHE_TTL = 45  # seconds


def cached(key, builder):
    now = time.time()
    if key in _cache and now - _cache[key][0] < CACHE_TTL:
        return _cache[key][1]
    data = builder()
    _cache[key] = (now, data)
    return data


def yf_symbol(symbol, market):
    """BIST tickers on Yahoo Finance carry the .IS suffix (THYAO.IS)."""
    return f"{symbol}.IS" if market == "tr" else symbol


def fetch_batch(symbols, market):
    """
    One batched yfinance call for the whole watchlist — much faster than
    fetching each ticker separately. Returns rows shaped for the frontend.
    """
    yf_syms = [yf_symbol(s, market) for s in symbols]
    # 5 days of daily closes gives us the sparkline series + change calc
    data = yf.download(yf_syms, period="5d", interval="1d",
                       group_by="ticker", progress=False, threads=True)

    rows = []
    for orig, ysym in zip(symbols, yf_syms):
        try:
            df = data[ysym] if len(yf_syms) > 1 else data
            closes = df["Close"].dropna()
            vols = df["Volume"].dropna()
            if closes.empty:
                raise ValueError("no data")
            series = [round(float(v), 4) for v in closes.tolist()][-8:]
            price = series[-1]
            prev = series[-2] if len(series) > 1 else price
            change_pct = ((price - prev) / prev * 100) if prev else 0
            volume = float(vols.iloc[-1]) if not vols.empty else 0
            rows.append({
                "symbol": orig,
                "name": orig,
                "price": round(price, 4),
                "change_pct": round(change_pct, 2),
                "volume": f"{volume/1_000_000:.1f}M" if volume else "—",
                "series": series,
            })
        except Exception as e:
            rows.append({
                "symbol": orig, "name": orig, "price": 0,
                "change_pct": 0, "volume": "—", "series": [0],
                "error": str(e),
            })
    return rows


def build_row(symbol, market="tr"):
    """Single-symbol fetch (used by /quote/{symbol}); still batched under the hood."""
    return fetch_batch([symbol], market)[0]


@app.on_event("startup")
def startup():
    _start_ais_thread()


@app.get("/health")
def health():
    return {"status": "ok", "ais_ships_tracked": len(_ships)}


@app.get("/ships")
def ships(limit: int = 300):
    """Latest known ship positions in Turkish/East Med waters, via aisstream.io.
    Empty right after a cold start — positions accumulate as the stream runs."""
    with _ships_lock:
        rows = sorted(_ships.values(), key=lambda s: s.get("ts") or "", reverse=True)[:limit]
    return clean(rows)


@app.get("/quotes")
def quotes(market: str = "tr"):
    symbols = TR_SYMBOLS if market == "tr" else US_SYMBOLS
    key = f"quotes:{market}"

    def builder():
        rows = []
        for sym in symbols:
            try:
                rows.append(build_row(sym))
            except Exception as e:
                # Don't let one bad symbol take down the whole response
                rows.append({
                    "symbol": sym, "name": sym, "price": 0,
                    "change_pct": 0, "volume": "—", "series": [0],
                    "error": str(e),
                })
        return rows

    try:
        return clean(cached(key, builder))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"upstream fetch failed: {e}")


@app.get("/search")
def search(q: str, market: str = "tr"):
    """
    Search for stock symbols by name or ticker.
    market='tr' searches BIST, market='us' searches NASDAQ/NYSE.
    """
    exchange = "BIST" if market == "tr" else None
    key = f"search:{market}:{q}"

    def builder():
        results = bp.search(q, type="stock", exchange=exchange, limit=15)
        # bp.search can return plain symbol strings or dicts depending on args;
        # normalize to a simple list of {symbol, name} for the frontend.
        out = []
        for r in results:
            if isinstance(r, dict):
                out.append({
                    "symbol": r.get("symbol") or r.get("ticker"),
                    "name": r.get("name") or r.get("description") or r.get("symbol"),
                })
            else:
                out.append({"symbol": r, "name": r})
        return out

    try:
        return clean(cached(key, builder))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"search failed: {e}")


@app.get("/quote/{symbol}")
def single_quote(symbol: str):
    """Fetch one symbol on demand — used when the user picks a search result
    that isn't already in the default watchlist."""
    key = f"quote:{symbol}"

    def builder():
        return build_row(symbol)

    try:
        return clean(cached(key, builder))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"quote fetch failed for {symbol}: {e}")



# ---------------- TEFAS funds via Fonoloji API ----------------

PERIOD_MAP = {1: "1m", 3: "3m", 6: "6m", 12: "1y", 36: "5y", 60: "5y"}


@app.get("/funds")
def funds(limit: int = 100, category: str | None = None, sort: str | None = None):
    """All TEFAS funds from Fonoloji, with period returns already computed."""
    key = f"funds:{limit}:{category}:{sort}"

    def builder():
        params = {"limit": limit}
        if category:
            params["category"] = category
        if sort:
            params["sort"] = sort
        data = fonoloji_get("/funds", params)
        rows = data if isinstance(data, list) else data.get("funds") or data.get("data") or []
        out = []
        for f in rows:
            out.append({
                "code": f.get("code"),
                "name": f.get("name"),
                "founder": f.get("management_company"),
                "fund_type": f.get("category"),
                "price": f.get("current_price"),
                "risk_score": f.get("risk_score"),
                "return_1m": pct(f.get("return_1m")),
                "return_3m": pct(f.get("return_3m")),
                "return_6m": pct(f.get("return_6m")),
                "return_ytd": pct(f.get("return_ytd")),
                "return_1y": pct(f.get("return_1y")),
                "aum": f.get("aum"),
            })
        return out

    try:
        return clean(cached(key, builder))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Fonoloji funds fetch failed: {e}")


@app.get("/fund/{code}")
def fund_detail(code: str, months: int = 12):
    """One fund's full detail from Fonoloji: metrics, price history, AI summary."""
    code = code.upper().strip()
    period = PERIOD_MAP.get(months, "1y")
    key = f"fund:{code}:{period}"

    def builder():
        detail = fonoloji_get(f"/funds/{code}")
        fund = detail.get("fund", detail)
        hist = fonoloji_get(f"/funds/{code}/history", {"period": period})
        points = hist.get("points", [])
        dates = [p.get("date") for p in points]
        prices = [p.get("price") for p in points if p.get("price") is not None]

        # Optional AI summary — 404 if not cached, so failure is fine
        summary = None
        try:
            ai = fonoloji_get(f"/funds/{code}/ai-summary")
            summary = ai.get("summary")
        except Exception:
            pass

        return {
            "code": fund.get("code", code),
            "name": fund.get("name", code),
            "founder": fund.get("management_company"),
            "category": fund.get("category"),
            "risk_score": fund.get("risk_score"),
            "price": fund.get("current_price"),
            "price_date": fund.get("current_date"),
            "daily_change_pct": pct(fund.get("return_1d")),
            "aum": fund.get("aum"),
            "investor_count": fund.get("investor_count"),
            "sharpe_90": fund.get("sharpe_90"),
            "volatility_90": pct(fund.get("volatility_90")),
            "returns": {
                "1A": pct(fund.get("return_1m")),
                "3A": pct(fund.get("return_3m")),
                "6A": pct(fund.get("return_6m")),
                "YB": pct(fund.get("return_ytd")),
                "1Y": pct(fund.get("return_1y")),
                "5Y": None,  # not in the single-fund payload; history covers the chart
            },
            "ai_summary": summary,
            "history": {"dates": dates, "prices": prices},
        }

    try:
        return clean(cached(key, builder))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"fund detail fetch failed for {code}: {e}")


@app.get("/market-live")
def market_live():
    """BIST100, USD/TRY, EUR/TRY snapshot via Fonoloji — handy for the ticker tape."""
    def builder():
        return fonoloji_get("/market/live")
    try:
        return clean(cached("market-live", builder))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"market live fetch failed: {e}")