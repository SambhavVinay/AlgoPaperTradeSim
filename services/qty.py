"""Paper quantity: lots times the Nifty lot size. No orders are sent."""

from typing import Optional

from config import settings


def resolve_order_qty(*, tradingsymbol: str, exchange: str, quantity: Optional[int] = None) -> int:
    if quantity is not None and quantity > 0:
        return int(quantity)
    fo = exchange.strip().upper() == "NFO" or tradingsymbol.upper().startswith("NIFTY")
    return settings.order_quantity_for(is_fo=fo)
