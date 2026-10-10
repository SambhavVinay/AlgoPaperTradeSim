"""Futures paper page and API. One process for the browser and the Kite desk."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from routes.kite_auth import router as kite_auth_router
from routes.paper_futures import router as paper_futures_router

STATIC_DIR = Path(__file__).resolve().parent / "static"
PAGE = STATIC_DIR / "futures-paper.html"

app = FastAPI(title="Futures paper", description="Nifty futures paper desk. No broker orders.")
app.include_router(kite_auth_router)
app.include_router(paper_futures_router)


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(PAGE)


@app.get("/health")
def health() -> dict:
    return {"ok": True}
