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
from tefas import Crawler
from datetime import date, timedelta
import os
import json
import asyncio
import threading

tefas_crawler = Crawler()

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


@app.get("/debug-tefas")
def debug_tefas():
    """Temporary diagnostic endpoint: returns one raw fund row exactly as
    TEFAS's list API sends it, plus the outcome of a price-history fetch.
    Remove once field mapping is confirmed."""
    result = {}
    try:
        rows = _tefas_list_rows("YAT")
        result["list_row_count"] = len(rows)
        result["first_raw_row"] = rows[0] if rows else None
    except Exception as e:
        result["list_error"] = repr(e)

    try:
        start = (date.today() - timedelta(days=90)).isoformat()
        hist = tefas_crawler.fetch(start=start, name="DGF")
        result["history_rows"] = len(hist)
        result["history_head"] = hist.head(3).to_dict(orient="records") if not hist.empty else []
    except Exception as e:
        result["history_error"] = repr(e)

    return clean(result)


# (fund category mapping now comes directly from TEFAS's fonTuruAciklama field)


def _tefas_list_rows(kind: str = "YAT"):
    """
    One call to TEFAS's return-based list endpoint. Returns raw rows that
    include fund code, name and period returns (1A/3A/6A/YB/1Y/3Y/5Y).
    Field names come from the tefas.gov.tr API and may vary — the
    normalizer below tries several candidate keys per field.
    """
    payload = {
        "dil": "TR", "fonTipi": kind, "kurucuKodu": None, "sfonTurKod": None,
        "fonTurAciklama": None, "islem": 1, "fonTurKod": None, "fonGrubu": None,
        "donemGetiri1a": "1", "donemGetiri3a": "1", "donemGetiri6a": "1",
        "donemGetiri1y": "1", "donemGetiriyb": "1", "donemGetiri3y": "1",
        "donemGetiri5y": "1", "basTarih": None, "bitTarih": None,
        "calismaTipi": 2, "getiriOrani": "1",
    }
    return tefas_crawler._do_post(tefas_crawler.list_endpoint, payload)


def _pick(row, *keys):
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return None


@app.get("/funds")
def funds(kind: str = "YAT", limit: int = 100):
    """
    All TEFAS funds with period returns, from the official tefas.gov.tr API
    (one request). kind: YAT (yatırım), EMK (emeklilik), BYF (borsa yatırım).
    """
    key = f"funds:{kind}:{limit}"

    def builder():
        rows = _tefas_list_rows(kind)
        out = []
        for r in rows[:limit]:
            out.append({
                "code": _pick(r, "fonKodu", "FONKODU"),
                "name": _pick(r, "fonAdi", "fonUnvan", "FONADI"),
                "founder": _pick(r, "kurucuAdi", "kurucuKodu"),
                "fund_type": _pick(r, "fonTuruAciklama", "fonTurAciklama", "fonTipi"),
                "return_1m": _pick(r, "getiri1A", "donemGetiri1a", "GETIRI1A"),
                "return_3m": _pick(r, "getiri3A", "donemGetiri3a"),
                "return_6m": _pick(r, "getiri6A", "donemGetiri6a"),
                "return_ytd": _pick(r, "getiriYB", "donemGetiriyb"),
                "return_1y": _pick(r, "getiri1Y", "donemGetiri1y"),
                "return_3y": _pick(r, "getiri3Y", "donemGetiri3y"),
                "return_5y": _pick(r, "getiri5Y", "donemGetiri5y"),
                "_raw_keys": list(r.keys()) if not out else None,  # debug aid on first row only
            })
        return out

    try:
        return clean(cached(key, builder))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"TEFAS list fetch failed: {e}")


@app.get("/fund/{code}")
def fund_detail(code: str, months: int = 12):
    """
    One fund's detail: latest price, daily change, price history for charting,
    and its period returns row from the list endpoint.
    """
    code = code.upper().strip()
    key = f"fund:{code}:{months}"

    def builder():
        start = (date.today() - timedelta(days=months * 30)).isoformat()
        hist = tefas_crawler.fetch(start=start, name=code)
        if hist.empty:
            raise ValueError(f"no data for fund {code}")
        hist = hist.sort_values("date")
        prices = [round(float(p), 6) for p in hist["price"].tolist()]
        dates = [str(d) for d in hist["date"].tolist()]

        latest = prices[-1]
        prev = prices[-2] if len(prices) > 1 else latest
        daily_change = ((latest - prev) / prev * 100) if prev else 0

        # find the period-returns row for this fund
        returns = {}
        name = code
        try:
            for r in _tefas_list_rows("YAT"):
                if _pick(r, "fonKodu", "FONKODU") == code:
                    name = _pick(r, "fonAdi", "fonUnvan") or code
                    returns = {
                        "1A": _pick(r, "getiri1A", "donemGetiri1a"),
                        "3A": _pick(r, "getiri3A", "donemGetiri3a"),
                        "6A": _pick(r, "getiri6A", "donemGetiri6a"),
                        "YB": _pick(r, "getiriYB", "donemGetiriyb"),
                        "1Y": _pick(r, "getiri1Y", "donemGetiri1y"),
                        "3Y": _pick(r, "getiri3Y", "donemGetiri3y"),
                        "5Y": _pick(r, "getiri5Y", "donemGetiri5y"),
                    }
                    break
        except Exception:
            pass  # detail page still works without the returns strip

        return {
            "code": code,
            "name": name,
            "price": latest,
            "daily_change_pct": round(daily_change, 2),
            "returns": returns,
            "history": {"dates": dates, "prices": prices},
        }

    try:
        return clean(cached(key, builder))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"fund detail fetch failed for {code}: {e}")


# NOTE: build_row() below also needs sanitizing, since BIST/US quote data
# can carry NaN in the same way (e.g. missing volume or change_pct fields).
# clean() is applied at the response boundary above, so it covers both
# /quotes and /funds without needing to touch build_row() itself.