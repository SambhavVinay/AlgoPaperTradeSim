import logging

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from kiteconnect import KiteConnect, exceptions

from config import settings
from deps import save_token_cache

logger = logging.getLogger("FuturesPaper.KiteAuth")

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/login", summary="Redirect to Zerodha login")
def login() -> RedirectResponse:
    kite = KiteConnect(api_key=settings.ZERODHA_API_KEY)
    login_url = kite.login_url()
    logger.info("Redirecting to Kite login")
    return RedirectResponse(url=login_url)


@router.get("/callback", summary="Handle Zerodha login redirect")
def callback(
    request_token: str | None = Query(default=None),
    status_param: str | None = Query(default=None, alias="status"),
) -> RedirectResponse:
    if not request_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Missing request_token. Open /api/v1/auth/login. "
                "On developers.kite.trade set Redirect URL to "
                f"{settings.ZERODHA_REDIRECT_URL}"
            ),
        )

    kite = KiteConnect(api_key=settings.ZERODHA_API_KEY)
    try:
        session = kite.generate_session(
            request_token=request_token,
            api_secret=settings.ZERODHA_API_SECRET,
        )
    except exceptions.TokenException as err:
        logger.error("Token exchange failed: %s", err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired request_token. Try /api/v1/auth/login again.",
        ) from err

    access_token = session["access_token"]
    if not save_token_cache(access_token):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Session established but failed to persist token cache.",
        )

    logger.info("Zerodha session cached")
    return RedirectResponse(url="/", status_code=303)
