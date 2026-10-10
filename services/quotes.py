import logging
from typing import Any

from fastapi import HTTPException, status
from kiteconnect import KiteConnect, exceptions

from config import settings

logger = logging.getLogger("NeoTV.Quotes")


def instrument_key(exchange: str, tradingsymbol: str) -> str:
    return f"{exchange.strip().upper()}:{tradingsymbol.strip().upper()}"


def enforce_market_symbol(exchange: str, tradingsymbol: str) -> tuple[str, str]:
    exchange_u = exchange.strip().upper()
    symbol_u = tradingsymbol.strip().upper()

    if exchange_u not in settings.allowed_exchanges:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Exchange '{exchange_u}' not allowed. Allowed: {sorted(settings.allowed_exchanges)}",
        )
    if symbol_u not in settings.allowed_symbols:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Symbol '{symbol_u}' not allowlisted. "
                f"Allowed: {sorted(settings.allowed_symbols)}"
            ),
        )
    return exchange_u, symbol_u


def _map_kite_error(err: Exception) -> HTTPException:
    if isinstance(err, exceptions.TokenException):
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Re-authenticate via /api/v1/auth/login",
        )
    if isinstance(err, exceptions.NetworkException):
        logger.error(f"Kite API unreachable: {err}")
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kite Connect API is unreachable.",
        )
    if isinstance(err, exceptions.PermissionException):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Kite plan/permission denied for market data: {err}",
        )
    if isinstance(err, exceptions.InputException):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )
    logger.error(f"Unexpected Kite quote error: {err}")
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected error while fetching market data.",
    )


def get_ltp(
    kite: KiteConnect,
    exchange: str,
    tradingsymbol: str,
    *,
    enforce: bool = True,
) -> dict[str, Any]:
    if enforce:
        exchange_u, symbol_u = enforce_market_symbol(exchange, tradingsymbol)
    else:
        exchange_u = exchange.strip().upper()
        symbol_u = tradingsymbol.strip().upper()
    key = instrument_key(exchange_u, symbol_u)
    try:
        payload = kite.ltp(key)
    except Exception as err:
        raise _map_kite_error(err) from err
    return {"instrument": key, "data": payload.get(key, payload)}


def get_quote(
    kite: KiteConnect,
    exchange: str,
    tradingsymbol: str,
    *,
    enforce: bool = True,
) -> dict[str, Any]:
    if enforce:
        exchange_u, symbol_u = enforce_market_symbol(exchange, tradingsymbol)
    else:
        exchange_u = exchange.strip().upper()
        symbol_u = tradingsymbol.strip().upper()
    key = instrument_key(exchange_u, symbol_u)
    try:
        payload = kite.quote(key)
    except Exception as err:
        raise _map_kite_error(err) from err
    return {"instrument": key, "data": payload.get(key, payload)}
