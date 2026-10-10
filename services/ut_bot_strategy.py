"""
UT Bot Alerts — Python port of UT-Bot.pine.

Defaults: key value 1, ATR period 10, Heikin Ashi off (signals on close).
Buy when close crosses above the ATR trailing stop. Sell when it crosses below.
"""

from __future__ import annotations

import copy
from typing import List, Optional


class _Atr:
    """Wilder ATR matching TradingView atr()."""

    def __init__(self, length: int) -> None:
        self.length = max(1, int(length))
        self.prev_close: Optional[float] = None
        self.value: Optional[float] = None
        self._seed: List[float] = []

    def update(self, high: float, low: float, close: float) -> Optional[float]:
        if self.prev_close is None:
            tr = high - low
        else:
            tr = max(
                high - low,
                abs(high - self.prev_close),
                abs(low - self.prev_close),
            )
        self.prev_close = close
        if self.value is None:
            self._seed.append(tr)
            if len(self._seed) < self.length:
                return None
            self.value = sum(self._seed) / self.length
            return self.value
        self.value = (self.value * (self.length - 1) + tr) / self.length
        return self.value


class UTBotState:
    def __init__(self, key_value: float = 1.0, atr_period: int = 10) -> None:
        self.key_value = float(key_value)
        self.atr_period = int(atr_period)
        self._atr = _Atr(self.atr_period)
        self.stop: Optional[float] = None
        self.prev_src: Optional[float] = None
        self.last_atr: Optional[float] = None

    def snapshot(self) -> "UTBotState":
        """Deep copy so a forming-bar preview does not move the committed stop."""
        return copy.deepcopy(self)

    def process_candle(
        self,
        close: float,
        high: float = 0.0,
        low: float = 0.0,
    ) -> Optional[str]:
        if high == 0.0 and low == 0.0:
            high = low = close
        atr = self._atr.update(high, low, close)
        self.last_atr = atr
        src = float(close)
        if atr is None:
            self.prev_src = src
            return None

        n_loss = self.key_value * atr
        prev_stop = 0.0 if self.stop is None else self.stop
        src_prev = self.prev_src

        if src_prev is None:
            self.stop = (src - n_loss) if src > prev_stop else (src + n_loss)
            self.prev_src = src
            return None

        if src > prev_stop and src_prev > prev_stop:
            stop = max(prev_stop, src - n_loss)
        elif src < prev_stop and src_prev < prev_stop:
            stop = min(prev_stop, src + n_loss)
        elif src > prev_stop:
            stop = src - n_loss
        else:
            stop = src + n_loss

        # ema(src, 1) is src. crossover(src, stop) / crossover(stop, src).
        above = src > stop and src_prev <= prev_stop
        below = stop > src and prev_stop <= src_prev
        buy = src > stop and above
        sell = src < stop and below

        self.stop = stop
        self.prev_src = src
        if buy:
            return "BUY"
        if sell:
            return "SELL"
        return None
