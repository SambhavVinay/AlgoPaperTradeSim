"""One-minute candle builder used by the futures paper desk."""

from __future__ import annotations

import datetime
from typing import Any, Optional


class CandleAggregator:
    def __init__(self, timeframe_minutes: int = 1):
        self.timeframe_minutes = timeframe_minutes
        self.current_candle_start = None
        self.open = None
        self.high = None
        self.low = None
        self.close = None
        self.volume_start = None
        self.volume_accumulated = 0

    def process_tick(self, price: float, cumulative_volume: float, tick_time: datetime.datetime) -> Optional[dict[str, Any]]:
        minute_offset = tick_time.minute % self.timeframe_minutes
        candle_start = tick_time.replace(
            minute=tick_time.minute - minute_offset,
            second=0,
            microsecond=0,
        )

        completed_candle = None

        if self.current_candle_start is None:
            self.current_candle_start = candle_start
            self.open = price
            self.high = price
            self.low = price
            self.close = price
            self.volume_start = cumulative_volume
            self.volume_accumulated = 0.0
        elif candle_start > self.current_candle_start:
            completed_candle = {
                "timestamp": self.current_candle_start,
                "open": self.open,
                "high": self.high,
                "low": self.low,
                "close": self.close,
                "volume": self.volume_accumulated,
            }
            self.current_candle_start = candle_start
            self.open = price
            self.high = price
            self.low = price
            self.close = price
            self.volume_accumulated = max(0.0, cumulative_volume - self.volume_start) if self.volume_start is not None else 0.0
            self.volume_start = cumulative_volume
        else:
            self.high = max(self.high, price)
            self.low = min(self.low, price)
            self.close = price
            if self.volume_start is not None:
                self.volume_accumulated = max(0.0, cumulative_volume - self.volume_start)

        return completed_candle
