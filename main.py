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
import borsapy as bp

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


def build_row(symbol):
    """Fetch one ticker's info + a short price series, shaped for the frontend."""
    t = bp.Ticker(symbol)
    info = t.info
    try:
        hist = t.history(period="5d")
        series = [round(float(v), 4) for v in hist["Close"].tolist()][-8:]
    except Exception:
        series = [info.get("last", 0)]

    price = info.get("last") or info.get("regularMarketPrice") or 0
    change_pct = info.get("change_percent") or info.get("regularMarketChangePercent") or 0
    volume = info.get("volume") or info.get("regularMarketVolume") or 0
    name = info.get("name") or info.get("longName") or symbol

    return {
        "symbol": symbol,
        "name": name,
        "price": round(float(price), 4) if price else 0,
        "change_pct": round(float(change_pct), 2) if change_pct else 0,
        "volume": f"{volume/1_000_000:.1f}M" if volume else "—",
        "series": series if series else [price],
    }


@app.get("/health")
def health():
    return {"status": "ok"}


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
        return cached(key, builder)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"upstream fetch failed: {e}")


FUND_TYPE_MAP = {
    "hisse": None,       # borsapy doesn't filter by asset class directly; see note below
    "borclanma": None,
    "para": None,
    "katilim": None,
    "karma": None,
}


@app.get("/funds")
def funds(min_return_1y: float | None = None, limit: int = 20):
    """
    Returns top TEFAS investment funds (YAT) sorted by 1-year return.
    borsapy's screen_funds() doesn't expose the exact same category buckets
    shown in the frontend (hisse/borclanma/para/katilim/karma) — that mapping
    needs to come from each fund's `info` (asset allocation), so for now this
    returns the general screener result. Refine once you decide which field
    to key the category filter off of.
    """
    key = f"funds:{min_return_1y}:{limit}"

    def builder():
        df = bp.screen_funds(fund_type="YAT", min_return_1y=min_return_1y, limit=limit)
        return df.to_dict(orient="records")

    try:
        return cached(key, builder)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"upstream fetch failed: {e}")