"""Typed shapes for everything the terminal renders.

These are the contract between the backend and the frontend. When you wire the
real Fonoloji API, map its responses into these models in providers/fonoloji.py
and the frontend keeps working unchanged.
"""
from pydantic import BaseModel


class Ticker(BaseModel):
    symbol: str          # e.g. "BTC/USD", "XU030", "ALTIN"
    price: str           # pre-formatted string, e.g. "62.201" or "6.161,46"
    change_pct: float    # e.g. -1.73
    unit: str = ""       # optional prefix/suffix like "₺" or "$"


class Stat(BaseModel):
    label: str
    value: str
    unit: str = ""


class MarketPulse(BaseModel):
    """The AI-written market narrative card (Fonoloji's "Piyasanın Nabzı")."""
    title: str
    body: list[str]          # paragraphs
    highlights: list[Stat]   # BIST100 / USDTL / Gram Altın chips


class MoverFund(BaseModel):
    rank: int
    code: str
    name: str
    change_pct: float


class SpotlightFund(BaseModel):
    code: str
    name: str
    monthly_return_pct: float
    size_label: str          # e.g. "107 milyon ₺"


class HeatCell(BaseModel):
    label: str
    value_pct: float         # drives the color intensity


class FlowFund(BaseModel):
    code: str
    name: str
    net_flow_label: str      # e.g. "+23,2 milyar ₺"
    fill_pct: float          # 0..100 bar width relative to top flow


class TopFund(BaseModel):
    rank: int
    code: str
    name: str
    category: str
    size_label: str
    return_1y_pct: float


class Dashboard(BaseModel):
    brand: str
    as_of: str
    tickers: list[Ticker]
    pulse: MarketPulse
    hero_stats: list[Stat]
    gainers: list[MoverFund]
    losers: list[MoverFund]
    spotlight: SpotlightFund
    heatmap: list[HeatCell]
    flows: list[FlowFund]
    top_funds: list[TopFund]


# --- Fund list + detail pages -------------------------------------------------
class FundListItem(BaseModel):
    code: str
    name: str
    category: str
    size_label: str
    price: str
    return_1m_pct: float
    return_1y_pct: float
    risk_score: int = 0
    investors: int = 0


class AllocSlice(BaseModel):
    label: str
    pct: float


class HistoryPoint(BaseModel):
    date: str
    price: float


class FundDetail(BaseModel):
    code: str
    name: str
    category: str
    management_company: str = ""
    isin: str = ""
    trading_status: str = ""
    kap_url: str = ""
    risk_score: int = 0
    price: str = ""
    price_date: str = ""
    return_1d_pct: float = 0
    returns: list[Stat] = []            # 1A / 3A / 6A / 1Y / YTD chips
    real_return_1y_pct: float = 0
    sharpe_90: float = 0
    volatility_90_pct: float = 0
    max_drawdown_1y_pct: float = 0
    aum: float = 0
    size_label: str = ""
    investors: int = 0
    allocation: list[AllocSlice] = []
    beats: list[str] = []               # e.g. ["TÜFE", "Mevduat"]
    history: list[HistoryPoint] = []    