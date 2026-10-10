"""
B-Xtrender (Puppytherapy filtered Buy/Sell v2) — Python port of B-xtrender.pine.

Signals on completed bars only. Defaults match the Pine script inputs.
"""

from __future__ import annotations

import copy
from typing import List, Optional


class _Ema:
    def __init__(self, length: int) -> None:
        self.length = max(1, int(length))
        self.alpha = 2.0 / (self.length + 1.0)
        self.value: Optional[float] = None
        self._seed: List[float] = []

    def update(self, x: float) -> float:
        if self.value is None:
            self._seed.append(x)
            if len(self._seed) < self.length:
                # Pine seeds EMA after length bars with SMA of first length values
                return sum(self._seed) / len(self._seed)
            self.value = sum(self._seed) / self.length
            return self.value
        self.value = self.alpha * x + (1.0 - self.alpha) * self.value
        return self.value


class _Rsi:
    """Wilder RSI matching TradingView rsi()."""

    def __init__(self, length: int) -> None:
        self.length = max(1, int(length))
        self.prev: Optional[float] = None
        self.avg_gain: Optional[float] = None
        self.avg_loss: Optional[float] = None
        self._gains: List[float] = []
        self._losses: List[float] = []

    def update(self, x: float) -> float:
        if self.prev is None:
            self.prev = x
            return 50.0
        change = x - self.prev
        self.prev = x
        gain = max(change, 0.0)
        loss = max(-change, 0.0)

        if self.avg_gain is None:
            self._gains.append(gain)
            self._losses.append(loss)
            if len(self._gains) < self.length:
                return 50.0
            self.avg_gain = sum(self._gains) / self.length
            self.avg_loss = sum(self._losses) / self.length
        else:
            self.avg_gain = (self.avg_gain * (self.length - 1) + gain) / self.length
            self.avg_loss = (self.avg_loss * (self.length - 1) + loss) / self.length

        if self.avg_loss == 0:
            return 100.0
        rs = self.avg_gain / self.avg_loss
        return 100.0 - (100.0 / (1.0 + rs))


class _T3:
    """Tillson T3 as in the Pine script (6 nested EMAs, b=0.7)."""

    def __init__(self, length: int = 5) -> None:
        self.e1 = _Ema(length)
        self.e2 = _Ema(length)
        self.e3 = _Ema(length)
        self.e4 = _Ema(length)
        self.e5 = _Ema(length)
        self.e6 = _Ema(length)
        b = 0.7
        self.c1 = -b * b * b
        self.c2 = 3 * b * b + 3 * b * b * b
        self.c3 = -6 * b * b - 3 * b - 3 * b * b * b
        self.c4 = 1 + 3 * b + b * b * b + 3 * b * b

    def update(self, src: float) -> float:
        xe1 = self.e1.update(src)
        xe2 = self.e2.update(xe1)
        xe3 = self.e3.update(xe2)
        xe4 = self.e4.update(xe3)
        xe5 = self.e5.update(xe4)
        xe6 = self.e6.update(xe5)
        return self.c1 * xe6 + self.c2 * xe5 + self.c3 * xe4 + self.c4 * xe3


class BXtrenderState:
    def __init__(
        self,
        short_l1: int = 5,
        short_l2: int = 20,
        short_l3: int = 15,
        long_l1: int = 20,
        long_l2: int = 15,
        use_trend_filter: bool = False,
        use_extreme_filter: bool = False,
        buy_extreme: float = -10.0,
        sell_extreme: float = 10.0,
        use_one_shot_filter: bool = False,
        use_re_arm_filter: bool = True,
        t3_len: int = 5,
    ) -> None:
        self.short_l1 = short_l1
        self.short_l2 = short_l2
        self.short_l3 = short_l3
        self.long_l1 = long_l1
        self.long_l2 = long_l2
        self.use_trend_filter = use_trend_filter
        self.use_extreme_filter = use_extreme_filter
        self.buy_extreme = buy_extreme
        self.sell_extreme = sell_extreme
        self.use_one_shot_filter = use_one_shot_filter
        self.use_re_arm_filter = use_re_arm_filter

        self._ema_s1 = _Ema(short_l1)
        self._ema_s2 = _Ema(short_l2)
        self._rsi_short = _Rsi(short_l3)
        self._ema_l1 = _Ema(long_l1)
        self._rsi_long = _Rsi(long_l2)
        self._t3 = _T3(t3_len)

        self._ma_hist: List[float] = []  # [0]=current after update, [1]=prev, [2]=prev2
        self.buy_fired_since_cross = False
        self.sell_fired_since_cross = False
        self.lowest_since_arm_buy: Optional[float] = None
        self.highest_since_arm_sell: Optional[float] = None

        self.last_short: Optional[float] = None
        self.last_long: Optional[float] = None
        self.last_ma: Optional[float] = None

    def snapshot(self) -> "BXtrenderState":
        """Deep copy so a forming-bar preview does not move the committed EMAs."""
        return copy.deepcopy(self)

    def process_candle(
        self,
        close: float,
        high: float = 0.0,  # unused; kept for runner API symmetry
        low: float = 0.0,
    ) -> Optional[str]:
        e1 = self._ema_s1.update(close)
        e2 = self._ema_s2.update(close)
        short_term = self._rsi_short.update(e1 - e2) - 50.0
        long_term = self._rsi_long.update(self._ema_l1.update(close)) - 50.0
        ma = self._t3.update(short_term)

        self.last_short = short_term
        self.last_long = long_term
        self.last_ma = ma

        # Shift history: after append, index -1 is current, -2 is [1], -3 is [2]
        self._ma_hist.append(ma)
        if len(self._ma_hist) > 8:
            self._ma_hist = self._ma_hist[-8:]

        if len(self._ma_hist) < 3:
            return None

        m0 = self._ma_hist[-1]
        m1 = self._ma_hist[-2]
        m2 = self._ma_hist[-3]

        raw_buy = m0 > m1 and m1 < m2
        raw_sell = m0 < m1 and m1 > m2

        trend_ok_buy = (long_term > 0) if self.use_trend_filter else True
        trend_ok_sell = (long_term < 0) if self.use_trend_filter else True
        extreme_ok_buy = (m1 < self.buy_extreme) if self.use_extreme_filter else True
        extreme_ok_sell = (m1 > self.sell_extreme) if self.use_extreme_filter else True

        zero_cross_up = m0 > 0 and m1 <= 0
        zero_cross_down = m0 < 0 and m1 >= 0

        if zero_cross_down:
            self.sell_fired_since_cross = False
            self.lowest_since_arm_buy = m0
        if zero_cross_up:
            self.buy_fired_since_cross = False
            self.highest_since_arm_sell = m0

        if self.lowest_since_arm_buy is not None and m0 < self.lowest_since_arm_buy:
            self.lowest_since_arm_buy = m0
            if self.use_re_arm_filter and self.buy_fired_since_cross:
                self.buy_fired_since_cross = False

        if self.highest_since_arm_sell is not None and m0 > self.highest_since_arm_sell:
            self.highest_since_arm_sell = m0
            if self.use_re_arm_filter and self.sell_fired_since_cross:
                self.sell_fired_since_cross = False

        one_shot_ok_buy = (not self.buy_fired_since_cross) if self.use_one_shot_filter else True
        one_shot_ok_sell = (not self.sell_fired_since_cross) if self.use_one_shot_filter else True

        buy_signal = raw_buy and trend_ok_buy and extreme_ok_buy and one_shot_ok_buy
        sell_signal = raw_sell and trend_ok_sell and extreme_ok_sell and one_shot_ok_sell

        if buy_signal:
            self.buy_fired_since_cross = True
            return "BUY"
        if sell_signal:
            self.sell_fired_since_cross = True
            return "SELL"
        return None
