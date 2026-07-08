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
        return clean(cached(key, builder))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"upstream fetch failed: {e}")


# NOTE: build_row() below also needs sanitizing, since BIST/US quote data
# can carry NaN in the same way (e.g. missing volume or change_pct fields).
# clean() is applied at the response boundary above, so it covers both
# /quotes and /funds without needing to touch build_row() itself.