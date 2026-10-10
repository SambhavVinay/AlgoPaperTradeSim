import os
import json
import logging
from typing import Optional, Dict, Any

from fastapi import HTTPException, status
from kiteconnect import KiteConnect, exceptions

from config import settings

logger = logging.getLogger("NeoTV.Dependencies")


def load_cached_token() -> Optional[str]:
    """Read the cached access token from disk. Returns None if missing/corrupt."""
    if not os.path.exists(settings.TOKEN_CACHE_FILE):
        return None
    try:
        with open(settings.TOKEN_CACHE_FILE, "r") as f:
            payload: Dict[str, Any] = json.load(f)
            return payload.get("access_token")
    except (json.JSONDecodeError, IOError) as err:
        logger.error(f"Could not read token cache: {err}")
        return None


def save_token_cache(access_token: str) -> bool:
    """Persist the access token to disk. Returns True on success."""
    try:
        with open(settings.TOKEN_CACHE_FILE, "w") as f:
            json.dump({"access_token": access_token}, f, indent=4)
        logger.info("Access token cached.")
        return True
    except IOError as err:
        logger.error(f"Could not write token cache: {err}")
        return False


def get_kite_client() -> KiteConnect:
    """
    FastAPI dependency. Returns a KiteConnect client with a validated access token.

    Raises:
        401 — no cached token, or the cached token failed validation (expired).
        503 — Kite API unreachable.
        500 — unexpected error.
    """
    kite = KiteConnect(api_key=settings.ZERODHA_API_KEY)
    token = load_cached_token()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No session found. Authenticate via /api/v1/auth/login",
        )

    kite.set_access_token(token)

    try:
        kite.profile()
        return kite
    except exceptions.TokenException:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Re-authenticate via /api/v1/auth/login",
        )
    except exceptions.NetworkException as err:
        logger.error(f"Kite API unreachable: {err}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kite Connect API is unreachable.",
        )
    except Exception as err:
        logger.error(f"Unexpected error validating session: {err}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error while validating session.",
        )
