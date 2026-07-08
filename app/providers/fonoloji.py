"""Fonoloji data provider.

The ONE place that knows about the upstream Fonoloji API
(https://fonoloji.com/v1, auth via X-API-Key). Everything else speaks only in
app.models.

Endpoints and the /funds/:code response shape are confirmed from the official
docs (https://fonoloji.com/api-docs). A few list endpoints (movers, categories,
flow, market/live) aren't shown field-by-field in the docs, so their mappers
below are best-effort and fall back to mock per-section if a field is missing.
Run `python probe.py` with your key to dump the real shapes and finalise them.

IMPORTANT: Fonoloji does not serve live BIST *stock* prices/%-change/charts
(market-data licensing) — those endpoints return 451. Index levels, FX, gold and
ALL fund data (incl. NAV) are available. The ticker tape therefore uses indices
+ FX + metals, not individual stocks.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from ..config import settings
from ..models import (
    Dashboard, Ticker, Stat, MarketPulse, MoverFund, SpotlightFund,
    HeatCell, FlowFund, TopFund,
)

# Confirmed endpoint paths (see /api-docs).
_ENDPOINTS = {
    "market_live": "/market/live",     # BIST100, USD/TRY, EUR/TRY, silver
    "gold_live": "/gold/live",         # gram/quarter/ounce gold
    "digest": "/market/digest",        # AI daily market summary (404 if none)
    "summary_today": "/summary/today", # gainers/losers/aggregates
    "movers": "/market/movers",        # fund gainers/losers
    "categories": "/categories",       # category stats, 1Y avg return
    "flow": "/insights/flow",          # inflow/outflow leaders
    "funds": "/funds",                 # list; ?sort=aum&limit=8&order=desc
}

_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str) -> Any | None:
    hit = _cache.get(key)
    return hit[1] if hit and hit[0] > time.time() else None


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.time() + settings.cache_ttl, value)


def _client() -> httpx.Client:
    headers = {"Accept": "application/json"}
    if settings.fonoloji_api_key:
        scheme = (settings.fonoloji_auth_scheme or "").strip()
        headers[settings.fonoloji_auth_header] = (
            f"{scheme} {settings.fonoloji_api_key}".strip() if scheme else settings.fonoloji_api_key
        )
    return httpx.Client(base_url=settings.fonoloji_base_url, headers=headers, timeout=15.0)


def _get(path: str) -> Any:
    cached = _cache_get(path)
    if cached is not None:
        return cached
    with _client() as c:
        r = c.get(path)
        r.raise_for_status()
        data = r.json()
    _cache_set(path, data)
    return data


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _first(d: dict, *keys, default=None):
    """Return the first present key from a dict (handles naming variations)."""
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def get_dashboard() -> Dashboard:
    if not settings.use_live_data:
        return _mock_dashboard()

    m = _mock_dashboard()  # per-section fallback source
    notes: list[str] = []

    def section(name, fn, fallback):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            notes.append(name)
            print(f"[fonoloji] {name} failed -> mock: {exc}")
            return fallback

    dash = Dashboard(
        brand=settings.brand_name,
        as_of=_now_label(),
        tickers=section("tickers", _live_tickers, m.tickers),
        pulse=section("pulse", _live_pulse, m.pulse),
        hero_stats=section("hero_stats", _live_hero_stats, m.hero_stats),
        gainers=section("gainers", lambda: _live_movers(True), m.gainers),
        losers=section("losers", lambda: _live_movers(False), m.losers),
        spotlight=section("spotlight", _live_spotlight, m.spotlight),
        heatmap=section("heatmap", _live_heatmap, m.heatmap),
        flows=section("flows", _live_flows, m.flows),
        top_funds=section("top_funds", _live_top_funds, m.top_funds),
    )
    if notes:
        dash.as_of += f" · önizleme: {', '.join(notes)}"
    return dash


# ---------------------------------------------------------------------------
# Live section builders  (field names confirmed from probe.py output)
# ---------------------------------------------------------------------------
def _fmt_pct(frac: float) -> float:
    """Fonoloji returns most returns as fractions (0.0996 = 9.96%)."""
    return round(_f(frac) * 100, 2)


def _fmt_tr_number(x: float) -> str:
    """1234567.8 -> '1.234.567,8' (Turkish grouping)."""
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _live_tickers() -> list[Ticker]:
    out: list[Ticker] = []
    # Gold/FX first: pick a few well-known instruments.
    try:
        gold = _get(_ENDPOINTS["gold_live"]).get("items", [])
        by_code = {str(i.get("code", "")).upper(): i for i in gold if isinstance(i, dict)}
        wanted = [("USD", "USD/TL"), ("EUR", "EUR/TL"),
                  ("GRA", "GRAM ALTIN"), ("ALTIN", "GRAM ALTIN"),
                  ("GUMUS", "GÜMÜŞ"), ("XAG", "GÜMÜŞ")]
        seen = set()
        for code, label in wanted:
            it = by_code.get(code)
            if it and label not in seen:
                out.append(Ticker(symbol=label,
                                  price=_fmt_tr_number(_f(it.get("value"))),
                                  change_pct=_f(it.get("change_pct")), unit="₺"))
                seen.add(label)
    except Exception as exc:  # noqa: BLE001
        print(f"[fonoloji] gold/live skipped: {exc}")
    # Then a few indices.
    idx = _get(_ENDPOINTS["market_live"]).get("items", [])
    keep = {"BIST100": "XU100", "XU030": "XU030", "XU100": "XU100", "XBANK": "XBANK"}
    for it in idx:
        sym = str(it.get("symbol", "")).upper()
        if sym in keep:
            out.append(Ticker(symbol=keep[sym],
                              price=_fmt_tr_number(_f(it.get("value"))),
                              change_pct=_fmt_pct(it.get("change_pct")), unit="₺"))
    if not out:
        raise ValueError("no tickers mapped")
    return out


def _live_pulse() -> MarketPulse:
    d = _get(_ENDPOINTS["digest"])
    text = str(d.get("summary", ""))
    body = [p.strip() for p in text.split("\n\n") if p.strip()] or [text]
    return MarketPulse(title="Piyasanın Nabzı", body=body,
                       highlights=_pulse_highlights())


def _pulse_highlights() -> list[Stat]:
    """Build the 3 chips (BIST100 / USD/TL / Gram Altın) from live market data."""
    out: list[Stat] = []
    try:
        idx = _get(_ENDPOINTS["market_live"]).get("items", [])
        b = next((i for i in idx if str(i.get("symbol")).upper() == "BIST100"), None)
        if b:
            out.append(Stat(label="BIST 100", value=_fmt_tr_number(_f(b.get("value"))),
                            unit=_signed(_fmt_pct(b.get("change_pct")))))
        gold = _get(_ENDPOINTS["gold_live"]).get("items", [])
        by = {str(i.get("code", "")).upper(): i for i in gold}
        usd = by.get("USD")
        if usd:
            out.append(Stat(label="USD/TL", value=_fmt_tr_number(_f(usd.get("value"))),
                            unit=_signed(_f(usd.get("change_pct")))))
        gr = by.get("GRA") or by.get("ALTIN")
        if gr:
            out.append(Stat(label="Gram Altın", value=_fmt_tr_number(_f(gr.get("value"))),
                            unit=_signed(_f(gr.get("change_pct")))))
    except Exception as exc:  # noqa: BLE001
        print(f"[fonoloji] pulse highlights fallback: {exc}")
    return out or _mock_dashboard().pulse.highlights


def _signed(pct: float) -> str:
    return ("+" if pct >= 0 else "") + f"{pct:.2f}%".replace(".", ",")


def _live_hero_stats() -> list[Stat]:
    s = _get(_ENDPOINTS["summary_today"])
    aum = _f(s.get("totalAum"))
    return [
        Stat(label="Yönetilen büyüklük", value=_fmt_tr_number(aum / 1e12), unit="trilyon ₺"),
        Stat(label="Yatırımcı sayısı", value=f"{int(_f(s.get('totalInvestors'))):,}".replace(",", "."), unit="kişi"),
        Stat(label="Aktif TEFAS fonu", value=f"{int(_f(s.get('totalFunds'))):,}".replace(",", "."), unit="fon"),
        Stat(label="Takip edilen BIST hissesi", value="638", unit="hisse"),
    ]


def _live_movers(gainers: bool) -> list[MoverFund]:
    s = _get(_ENDPOINTS["summary_today"])
    items = (s.get("topGainers") if gainers else s.get("topLosers")) or []
    items = items[:5]
    if not items:
        raise ValueError("movers empty")
    return [MoverFund(rank=i, code=x.get("code", ""), name=x.get("name", ""),
                      change_pct=_fmt_pct(x.get("return_1d")))
            for i, x in enumerate(items, 1)]


def _live_spotlight() -> SpotlightFund:
    s = _get(_ENDPOINTS["summary_today"])
    g = (s.get("topGainers") or [])
    if not g:
        raise ValueError("no gainer for spotlight")
    top = g[0]
    code = top.get("code", "")
    monthly = _fmt_pct(top.get("return_1d"))  # fallback
    size = "—"
    try:
        fund = _get(f"{_ENDPOINTS['funds']}/{code}")
        fund = fund.get("fund", fund) if isinstance(fund, dict) else {}
        if fund.get("return_1m") is not None:
            monthly = _fmt_pct(fund.get("return_1m"))
        aum = _f(fund.get("aum"))
        if aum:
            size = f"{_fmt_tr_number(aum / 1e6).split(',')[0]} milyon ₺"
    except Exception as exc:  # noqa: BLE001
        print(f"[fonoloji] spotlight detail skipped: {exc}")
    return SpotlightFund(code=code, name=top.get("name", ""),
                         monthly_return_pct=monthly, size_label=size)


def _live_heatmap() -> list[HeatCell]:
    items = _get(_ENDPOINTS["categories"]).get("items", [])
    items = sorted(items, key=lambda c: _f(c.get("avg_return")), reverse=True)[:9]
    cells = [HeatCell(label=c.get("category", ""), value_pct=_fmt_pct(c.get("avg_return")))
             for c in items]
    if not cells:
        raise ValueError("categories empty")
    return cells


def _live_flows() -> list[FlowFund]:
    items = _get(_ENDPOINTS["flow"]).get("inflow", [])
    items = sorted(items, key=lambda i: _f(i.get("flow")), reverse=True)[:6]
    top = max((_f(i.get("flow")) for i in items), default=1) or 1
    out = []
    for i in items:
        flow_m = _f(i.get("flow"))  # value is in millions ₺
        if flow_m >= 1000:
            label = f"+{flow_m / 1000:.1f} milyar ₺".replace(".", ",")
        else:
            label = f"+{flow_m:.0f} milyon ₺"
        out.append(FlowFund(code=i.get("code", ""), name=i.get("name", ""),
                            net_flow_label=label,
                            fill_pct=max(6.0, min(100.0, flow_m / top * 100))))
    if not out:
        raise ValueError("flow empty")
    return out


def _live_top_funds() -> list[TopFund]:
    items = _get(f"{_ENDPOINTS['funds']}?sort=aum&order=desc&limit=8").get("items", [])
    out = []
    for i, f in enumerate(items[:8], 1):
        aum = _f(f.get("aum"))
        out.append(TopFund(rank=i, code=f.get("code", ""), name=f.get("name", ""),
                           category=f.get("category", ""),
                           size_label=(f"{aum / 1e9:.1f} milyar ₺".replace(".", ",") if aum else "—"),
                           return_1y_pct=_fmt_pct(f.get("return_1y"))))
    if not out:
        raise ValueError("funds list empty")
    return out


def _now_label() -> str:
    return time.strftime("%d.%m.%Y · %H:%M")


# ===========================================================================
# MOCK DATA — mirrors the reference screenshots; used until a key is set and as
# a per-section fallback if a live endpoint's shape differs from expectations.
# ===========================================================================
def _mock_dashboard() -> Dashboard:
    return Dashboard(
        brand=settings.brand_name,
        as_of=_now_label(),
        tickers=[
            Ticker(symbol="XU100", price="14.190", change_pct=-2.12, unit="₺"),
            Ticker(symbol="USD/TL", price="46,84", change_pct=0.06, unit="₺"),
            Ticker(symbol="EUR/TL", price="50,21", change_pct=0.14, unit="₺"),
            Ticker(symbol="GRAM ALTIN", price="6.162", change_pct=-1.24, unit="₺"),
            Ticker(symbol="GÜMÜŞ", price="88,55", change_pct=3.45, unit="₺"),
            Ticker(symbol="XU030", price="16.488,03", change_pct=-2.36, unit="₺"),
            Ticker(symbol="XBANK", price="16.886,36", change_pct=-3.25, unit="₺"),
        ],
        pulse=MarketPulse(
            title="Piyasanın Nabzı",
            body=[
                "TEFAS pazarında bugün RIH fonu yüzde 10.0 ile en çok yükselen olurken, "
                "BUB ve SNY fonları da sırasıyla %5.1 ve %3.7 artış gösterdi. Bu yükseliş, "
                "özellikle kıymetli madenler kategorisindeki fonların öne çıkmasıyla paralel "
                "ilerliyor; kıymetli madenler %3.1, şemsiye fonu %2.9, altın fonu ise %2.8 "
                "değer kazandı.",
                "Öte yandan, KHT, YCK ve DHV fonları yaklaşık %5 civarında değer kaybı yaşadı. "
                "Toplam fon sayısı 3212'ye ulaşırken, yönetilen varlıklar 12.39 trilyon TL "
                "seviyesinde seyrediyor. TÜFE'nin yıllık %32.1 olması, reel getiri açısından "
                "değerlendirmenin önemini artırıyor.",
            ],
            highlights=[
                Stat(label="BIST 100", value="14.190", unit="-2,12%"),
                Stat(label="USD/TL", value="46,84", unit="+0,06%"),
                Stat(label="Gram Altın", value="6.162", unit="-1,24%"),
            ],
        ),
        hero_stats=[
            Stat(label="Yönetilen büyüklük", value="12,42", unit="trilyon ₺"),
            Stat(label="Yatırımcı sayısı", value="66.437.776", unit="kişi"),
            Stat(label="Aktif TEFAS fonu", value="1.372", unit="fon"),
            Stat(label="Takip edilen BIST hissesi", value="638", unit="hisse"),
        ],
        gainers=[
            MoverFund(rank=1, code="RIH", name="RE-PIE PORTFÖY İKİNCİ HİSSE SENEDİ SERBEST FON", change_pct=9.96),
            MoverFund(rank=2, code="BUB", name="BULLS PORTFÖY BİRİNCİ FON SEPETİ FONU", change_pct=5.44),
            MoverFund(rank=3, code="SNY", name="ATLAS PORTFÖY SANAYİ SEKTÖRÜ HİSSE SENEDİ SERBEST FON", change_pct=3.67),
            MoverFund(rank=4, code="AEV", name="ALLBATROSS PORTFÖY SANAYİ ŞİRKETLERİ HİSSE SENEDİ FONU", change_pct=3.64),
            MoverFund(rank=5, code="BFE", name="ALLBATROSS PORTFÖY BİRİNCİ FON SEPETİ FONU", change_pct=3.19),
        ],
        losers=[
            MoverFund(rank=1, code="YIT", name="GARANTİ PORTFÖY YARI İLETKEN TEKNOLOJİLERİ DEĞİŞKEN FON", change_pct=-5.69),
            MoverFund(rank=2, code="CPT", name="ROTA PORTFÖY ÇİP TEKNOLOJİLERİ DEĞİŞKEN FON", change_pct=-5.59),
            MoverFund(rank=3, code="KHT", name="ALLBATROSS PORTFÖY KARTAL HİSSE SENEDİ SERBEST (TL) FON", change_pct=-5.54),
            MoverFund(rank=4, code="YCK", name="YAPI KREDİ PORTFÖY CİHANGİR SERBEST FON", change_pct=-5.35),
            MoverFund(rank=5, code="DHV", name="DENIZ PORTFÖY BİRİNCİ HİSSE SENEDİ SERBEST (TL) FON", change_pct=-5.25),
        ],
        spotlight=SpotlightFund(
            code="RIH",
            name="RE-PIE PORTFÖY İKİNCİ HİSSE SENEDİ SERBEST FON (HİSSE SENEDİ YOĞUN FON)",
            monthly_return_pct=37.91, size_label="107 milyon ₺",
        ),
        heatmap=[
            HeatCell(label="Gümüş Fonu", value_pct=91.2),
            HeatCell(label="Kıymetli Madenler", value_pct=57.5),
            HeatCell(label="Serbest Şemsiye Fonu", value_pct=57.2),
            HeatCell(label="Başlangıç Fonu", value_pct=49.5),
            HeatCell(label="Para Piyasası Fonu", value_pct=49.2),
            HeatCell(label="Para Piyasası Şemsiye Fonu", value_pct=48.2),
            HeatCell(label="Katılım Fonu", value_pct=48.1),
            HeatCell(label="Başlangıç Katılım Fonu", value_pct=47.6),
            HeatCell(label="Karma Şemsiye Fonu", value_pct=46.9),
        ],
        flows=[
            FlowFund(code="PRY", name="PUSULA PORTFÖY PARA PİYASASI (TL) FONU", net_flow_label="+23,2 milyar ₺", fill_pct=100),
            FlowFund(code="PHE", name="PUSULA PORTFÖY HİSSE SENEDİ FONU", net_flow_label="+23 milyar ₺", fill_pct=99),
            FlowFund(code="TLY", name="TERA PORTFÖY BİRİNCİ SERBEST FON", net_flow_label="+22,2 milyar ₺", fill_pct=96),
            FlowFund(code="ONK", name="AK PORTFÖY ONİKİNCİ SERBEST (DÖVİZ-AVRO) FON", net_flow_label="+18 milyar ₺", fill_pct=78),
            FlowFund(code="UNT", name="İŞ PORTFÖY ÜÇÜNCÜ SERBEST (TL) FON", net_flow_label="+15,7 milyar ₺", fill_pct=68),
            FlowFund(code="PBR", name="PUSULA PORTFÖY BİRİNCİ DEĞİŞKEN FON", net_flow_label="+11,8 milyar ₺", fill_pct=51),
        ],
        top_funds=[
            TopFund(rank=1, code="VGA", name="TÜRKİYE HAYAT VE EMEKLİLİK A.Ş. ALTIN KATILIM EMEKLİLİK YATIRIM FONU", category="Altın Katılım Fonu", size_label="228,5 milyar ₺", return_1y_pct=42.6),
            TopFund(rank=2, code="TLY", name="TERA PORTFÖY BİRİNCİ SERBEST FON", category="Serbest Şemsiye Fonu", size_label="201,4 milyar ₺", return_1y_pct=1173.4),
            TopFund(rank=3, code="GRO", name="GARANTİ PORTFÖY OTUZUNCU SERBEST (DÖVİZ) FON", category="Serbest Şemsiye Fonu", size_label="188,8 milyar ₺", return_1y_pct=21.6),
            TopFund(rank=4, code="PAL", name="AK PORTFÖY ALTINCI SERBEST (DÖVİZ) FON", category="Serbest Şemsiye Fonu", size_label="167,8 milyar ₺", return_1y_pct=21.6),
            TopFund(rank=5, code="ONS", name="İŞ PORTFÖY ONİKİNCİ SERBEST (DÖVİZ) FON", category="Serbest Şemsiye Fonu", size_label="164,4 milyar ₺", return_1y_pct=21.5),
            TopFund(rank=6, code="GEV", name="AGESA HAYAT VE EMEKLİLİK A.Ş. ALTIN KATILIM EMEKLİLİK YATIRIM FONU", category="Altın Katılım Fonu", size_label="164,1 milyar ₺", return_1y_pct=43.4),
            TopFund(rank=7, code="TP2", name="TERA PORTFÖY PARA PİYASASI (TL) FONU", category="Para Piyasası Şemsiye Fonu", size_label="160 milyar ₺", return_1y_pct=60.1),
            TopFund(rank=8, code="EUZ", name="GARANTİ PORTFÖY SERBEST (DÖVİZ-AVRO) FON", category="Serbest Şemsiye Fonu", size_label="136 milyar ₺", return_1y_pct=16.7),
        ],
    )


# ===========================================================================
# Fund list + detail (for /fonlar and /fon/:code pages)
# ===========================================================================
from ..models import FundListItem, FundDetail, AllocSlice, HistoryPoint  # noqa: E402

_ALLOC_FIELDS = [
    ("stock", "Hisse Senedi"), ("government_bond", "Devlet Tahvili"),
    ("treasury_bill", "Hazine Bonosu"), ("corporate_bond", "Özel Sektör"),
    ("eurobond", "Eurobond"), ("gold", "Kıymetli Maden"),
    ("cash", "Nakit / Mevduat"), ("other", "Diğer"),
]


def _size_label(aum: float) -> str:
    if not aum:
        return "—"
    if aum >= 1e9:
        return f"{aum / 1e9:.1f} milyar ₺".replace(".", ",")
    return f"{aum / 1e6:.0f} milyon ₺"


def get_fund_list(q: str = "", sort: str = "aum", limit: int = 60) -> list[FundListItem]:
    if not settings.use_live_data:
        return _mock_fund_list(q, limit)
    try:
        params = f"?sort={sort}&order=desc&limit={limit}"
        if q:
            params += f"&q={q}"
        items = _get(f"{_ENDPOINTS['funds']}{params}").get("items", [])
        out = [_map_fund_list_item(f) for f in items]
        if q:
            ql = q.lower()
            out = [f for f in out if ql in f.code.lower() or ql in f.name.lower()] or out
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"[fonoloji] fund list failed -> mock: {exc}")
        return _mock_fund_list(q, limit)


def _map_fund_list_item(f: dict) -> FundListItem:
    return FundListItem(
        code=f.get("code", ""), name=f.get("name", ""), category=f.get("category", ""),
        size_label=_size_label(_f(f.get("aum"))),
        price=_fmt_tr_number(_f(f.get("current_price"))),
        return_1m_pct=_fmt_pct(f.get("return_1m")),
        return_1y_pct=_fmt_pct(f.get("return_1y")),
        risk_score=int(_f(f.get("risk_score"))),
        investors=int(_f(f.get("investor_count"))),
    )


def get_fund(code: str) -> FundDetail | None:
    if not settings.use_live_data:
        return _mock_fund_detail(code)
    try:
        raw = _get(f"{_ENDPOINTS['funds']}/{code}")
        f = raw.get("fund", raw) if isinstance(raw, dict) else {}
        if not f.get("code"):
            return _mock_fund_detail(code)
        return _map_fund_detail(f, code)
    except Exception as exc:  # noqa: BLE001
        print(f"[fonoloji] fund {code} failed -> mock: {exc}")
        return _mock_fund_detail(code)


def _map_fund_detail(f: dict, code: str) -> FundDetail:
    returns = []
    for key, lbl in [("return_1m", "1A"), ("return_3m", "3A"), ("return_6m", "6A"),
                     ("return_1y", "1Y"), ("return_ytd", "YBB")]:
        if f.get(key) is not None:
            returns.append(Stat(label=lbl, value=_signed(_fmt_pct(f.get(key)))))
    alloc = []
    for field, lbl in _ALLOC_FIELDS:
        v = _f(f.get(field))
        if v > 0:
            alloc.append(AllocSlice(label=lbl, pct=round(v, 2)))
    alloc.sort(key=lambda a: a.pct, reverse=True)
    beats = [name for field, name in
             [("beats_tufe", "TÜFE"), ("beats_mevduat", "Mevduat"),
              ("beats_bist100", "BIST100"), ("beats_altin", "Altın"),
              ("beats_kategori", "Kategori")] if int(_f(f.get(field))) == 1]
    return FundDetail(
        code=code, name=f.get("name", ""), category=f.get("category", ""),
        management_company=f.get("management_company", ""), isin=f.get("isin", ""),
        trading_status=f.get("trading_status", ""), kap_url=f.get("kap_url", ""),
        risk_score=int(_f(f.get("risk_score"))),
        price=_fmt_tr_number(_f(f.get("current_price"))), price_date=f.get("current_date", ""),
        return_1d_pct=_fmt_pct(f.get("return_1d")), returns=returns,
        real_return_1y_pct=_fmt_pct(f.get("real_return_1y")),
        sharpe_90=round(_f(f.get("sharpe_90")), 2),
        volatility_90_pct=_fmt_pct(f.get("volatility_90")),
        max_drawdown_1y_pct=_fmt_pct(f.get("max_drawdown_1y")),
        aum=_f(f.get("aum")), size_label=_size_label(_f(f.get("aum"))),
        investors=int(_f(f.get("investor_count"))),
        allocation=alloc, beats=beats,
        history=get_fund_history(code),
    )


def get_fund_history(code: str) -> list[HistoryPoint]:
    if not settings.use_live_data:
        return _mock_history()
    try:
        raw = _get(f"{_ENDPOINTS['funds']}/{code}/history")
        pts = raw.get("items", raw.get("history", raw)) if isinstance(raw, dict) else raw
        out: list[HistoryPoint] = []
        for p in pts:
            if isinstance(p, dict):
                out.append(HistoryPoint(date=str(_first(p, "date", "d", default="")),
                                        price=_f(_first(p, "price", "value", "close", "p"))))
            elif isinstance(p, (list, tuple)) and len(p) >= 2:
                out.append(HistoryPoint(date=str(p[0]), price=_f(p[1])))
        return out or _mock_history()
    except Exception as exc:  # noqa: BLE001
        print(f"[fonoloji] history {code} failed -> mock: {exc}")
        return _mock_history()


# --- mock fallbacks for the new pages ---------------------------------------
def _mock_fund_list(q: str, limit: int) -> list[FundListItem]:
    base = _mock_dashboard().top_funds
    items = [FundListItem(code=t.code, name=t.name, category=t.category,
                          size_label=t.size_label, price="—",
                          return_1m_pct=round(t.return_1y_pct / 12, 2),
                          return_1y_pct=t.return_1y_pct, risk_score=5, investors=0)
             for t in base]
    if q:
        ql = q.lower()
        items = [f for f in items if ql in f.code.lower() or ql in f.name.lower()] or items
    return items[:limit]


def _mock_history() -> list[HistoryPoint]:
    import math
    pts, price = [], 100.0
    for i in range(120):
        price *= 1 + (math.sin(i / 7) * 0.01 + 0.006)
        pts.append(HistoryPoint(date=f"2026-{(i//30)+1:02d}-{(i%30)+1:02d}", price=round(price, 4)))
    return pts


def _mock_fund_detail(code: str) -> FundDetail:
    t = next((x for x in _mock_dashboard().top_funds if x.code == code),
             _mock_dashboard().top_funds[1])
    return FundDetail(
        code=code, name=t.name, category=t.category,
        management_company="Örnek Portföy Yönetimi A.Ş.", isin="TR" + code + "0000",
        trading_status="AKTİF", risk_score=6, price="7.235,18", price_date="2026-07-08",
        return_1d_pct=1.07,
        returns=[Stat(label="1A", value="+24,29%"), Stat(label="3A", value="+47,92%"),
                 Stat(label="6A", value="+135,44%"), Stat(label="1Y", value="+1173,40%"),
                 Stat(label="YBB", value="+143,96%")],
        real_return_1y_pct=863.9, sharpe_90=7.95, volatility_90_pct=18.77,
        max_drawdown_1y_pct=-23.24, aum=201379960617.65, size_label="201,4 milyar ₺",
        investors=91173,
        allocation=[AllocSlice(label="Hisse Senedi", pct=63.7), AllocSlice(label="Diğer", pct=18.9),
                    AllocSlice(label="Nakit / Mevduat", pct=14.8), AllocSlice(label="Özel Sektör", pct=2.6)],
        beats=["TÜFE", "Mevduat"], history=_mock_history(),
    )
