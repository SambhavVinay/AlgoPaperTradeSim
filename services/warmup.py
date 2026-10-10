"""Feed recent Nifty futures minutes into an indicator before the live session."""

from __future__ import annotations

import datetime
import logging
import random
from typing import Any

from kiteconnect import KiteConnect

from config import settings

logger = logging.getLogger("FuturesPaper.Warmup")


def warmup_from_history(
    kite: KiteConnect,
    instrument_token: int,
    current_ltp: float,
    state: Any,
) -> int:
    to_date = datetime.datetime.now()
    from_date = to_date - datetime.timedelta(days=int(settings.STRATEGY_WARMUP_DAYS))
    historical_candles: list[dict[str, Any]] = []

    try:
        historical_candles = kite.historical_data(
            instrument_token=instrument_token,
            from_date=from_date,
            to_date=to_date,
            interval="minute",
            continuous=False,
            oi=False,
        )
        logger.info("Retrieved %d historical candles for warm-up", len(historical_candles))
    except Exception as err:
        logger.warning("Historical API unavailable (%s). Using synthetic warm-up candles.", err)
        seed_price = current_ltp or 100.0
        base_time = datetime.datetime.now() - datetime.timedelta(minutes=200)
        for i in range(200):
            seed_price += random.uniform(-0.1, 0.1) * (seed_price * 0.001)
            historical_candles.append(
                {
                    "date": base_time + datetime.timedelta(minutes=i),
                    "open": seed_price,
                    "high": seed_price + random.uniform(0.01, 0.05),
                    "low": seed_price - random.uniform(0.01, 0.05),
                    "close": seed_price,
                    "volume": random.randint(1000, 5000),
                }
            )

    for bar in historical_candles:
        state.process_candle(
            high=float(bar["high"]),
            low=float(bar["low"]),
            close=float(bar["close"]),
        )

    return len(historical_candles)
