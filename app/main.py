"""Finansla Terminal — FastAPI backend + frontend.

API:
  GET /api/dashboard
  GET /api/funds?q=&sort=&limit=
  GET /api/funds/{code}
  GET /api/funds/{code}/history
  GET /api/health
Pages:
  GET /                 dashboard
  GET /fonlar           fund list / screener
  GET /fon/{code}       fund detail
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .config import settings
from .models import Dashboard, FundListItem, FundDetail, HistoryPoint
from .providers import fonoloji

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title=f"{settings.brand_name} Terminal API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins_list,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "brand": settings.brand_name, "live_data": settings.use_live_data}


@app.get("/api/dashboard", response_model=Dashboard)
def dashboard() -> Dashboard:
    return fonoloji.get_dashboard()


@app.get("/api/funds", response_model=list[FundListItem])
def funds(q: str = "", sort: str = "aum", limit: int = 60) -> list[FundListItem]:
    return fonoloji.get_fund_list(q=q, sort=sort, limit=min(limit, 200))


@app.get("/api/funds/{code}", response_model=FundDetail)
def fund(code: str) -> FundDetail:
    return fonoloji.get_fund(code.upper())


@app.get("/api/funds/{code}/history", response_model=list[HistoryPoint])
def fund_history(code: str) -> list[HistoryPoint]:
    return fonoloji.get_fund_history(code.upper())


# --- Pages ------------------------------------------------------------------
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/fonlar")
    def fund_list_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "fonlar.html")

    @app.get("/fon/{code}")
    def fund_page(code: str) -> FileResponse:
        return FileResponse(STATIC_DIR / "fon.html")
