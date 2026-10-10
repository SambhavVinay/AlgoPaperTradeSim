"""Futures paper desk API. Several indicators, no broker orders."""

from fastapi import APIRouter, Depends
from kiteconnect import KiteConnect
from pydantic import BaseModel, Field

from deps import get_kite_client
from services.paper_futures import paper_futures_desk

router = APIRouter(prefix="/api/v1/paper/futures", tags=["paper-futures"])


class FuturesPaperStartBody(BaseModel):
    modes: list[str] = Field(
        default_factory=list,
        description="Checked indicators, e.g. ut_bot_fut_cepe and bxtrender_fut_cepe",
    )


@router.get("/indicators", summary="Indicators the futures paper page can run")
def futures_paper_indicators() -> dict:
    return {"ok": True, "indicators": paper_futures_desk.indicators()}


@router.post("/start", summary="Start every checked indicator on one shared strike")
def futures_paper_start(
    body: FuturesPaperStartBody = FuturesPaperStartBody(),
    kite: KiteConnect = Depends(get_kite_client),
) -> dict:
    return paper_futures_desk.start(kite, body.modes)


@router.post("/stop", summary="Stop every futures paper indicator. Does not sell.")
def futures_paper_stop() -> dict:
    return paper_futures_desk.stop()


@router.get("/status", summary="Shared contract and one book per indicator")
def futures_paper_status() -> dict:
    return {"ok": True, "status": paper_futures_desk.get_status()}
