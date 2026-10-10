"""Continuous KiteTicker stream with an in-memory latest-tick buffer and subscriber callbacks."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from typing import Any, Optional

from kiteconnect import KiteTicker

from config import settings
from deps import load_cached_token

logger = logging.getLogger("NeoTV.Ticker")

TickCallback = Callable[[dict[str, Any]], None]


class TickStream:
    """Keeps a KiteTicker connection open and stores the latest tick per instrument."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._kws: Optional[KiteTicker] = None
        self._tokens: list[int] = []
        self._latest: dict[int, Any] = {}
        self._running = False
        self._error: Optional[str] = None
        self._subscribers: dict[str, TickCallback] = {}
        self._stream_mode: str = "ltp"

    @property
    def running(self) -> bool:
        return self._running

    @property
    def subscribed_tokens(self) -> list[int]:
        with self._lock:
            return list(self._tokens)

    def subscribe_ticks(self, callback: TickCallback) -> str:
        """Register a callback invoked on every tick. Returns subscriber id for unsubscribe."""
        sub_id = str(uuid.uuid4())
        with self._lock:
            self._subscribers[sub_id] = callback
        return sub_id

    def unsubscribe(self, subscriber_id: str) -> bool:
        with self._lock:
            return self._subscribers.pop(subscriber_id, None) is not None

    def _notify_subscribers(self, ticks: list[dict[str, Any]]) -> None:
        with self._lock:
            callbacks = list(self._subscribers.values())
        for tick in ticks or []:
            for cb in callbacks:
                try:
                    cb(tick)
                except Exception as err:
                    logger.error("Tick subscriber callback error: %s", err)

    def start(
        self,
        tokens: list[int],
        mode: str = "ltp",
        on_tick: Optional[TickCallback] = None,
    ) -> dict[str, Any]:
        tokens = [int(t) for t in tokens if t]
        if not tokens:
            return {"ok": False, "error": "No instrument tokens provided"}

        access_token = load_cached_token()
        if not access_token:
            return {"ok": False, "error": "No cached access token. Authenticate via /api/v1/auth/login"}

        subscriber_id: Optional[str] = None
        if on_tick is not None:
            subscriber_id = self.subscribe_ticks(on_tick)

        with self._lock:
            if self._running and self._kws is not None:
                new_tokens = [t for t in tokens if t not in self._tokens]
                if new_tokens:
                    self._tokens.extend(new_tokens)
                    try:
                        self._kws.subscribe(new_tokens)
                        self._apply_mode(self._kws, mode, new_tokens)
                    except Exception as err:
                        logger.error("Failed to extend subscription: %s", err)
                        if subscriber_id:
                            self._subscribers.pop(subscriber_id, None)
                        return {"ok": False, "error": str(err)}
                # Upgrade mode if caller requests richer data (full > quote > ltp)
                if self._mode_rank(mode) > self._mode_rank(self._stream_mode):
                    self._stream_mode = mode
                    try:
                        self._apply_mode(self._kws, mode, list(self._tokens))
                    except Exception as err:
                        logger.warning("Could not upgrade stream mode: %s", err)
                return {
                    "ok": True,
                    "message": "Stream already running; subscription updated",
                    "tokens": list(self._tokens),
                    "subscriber_id": subscriber_id,
                }

            self._tokens = list(tokens)
            self._latest = {}
            self._error = None
            self._stream_mode = mode
            kws = KiteTicker(settings.ZERODHA_API_KEY, access_token)

            def on_ticks(_ws, ticks):  # noqa: ANN001
                with self._lock:
                    for tick in ticks or []:
                        token = tick.get("instrument_token")
                        if token is not None:
                            self._latest[int(token)] = tick
                self._notify_subscribers(ticks)

            def on_connect(ws, _response):  # noqa: ANN001
                with self._lock:
                    current = list(self._tokens)
                    current_mode = self._stream_mode
                ws.subscribe(current)
                self._apply_mode(ws, current_mode, current)
                self._running = True
                logger.info("Tick stream connected; subscribed %s (mode=%s)", current, current_mode)

            def on_error(_ws, code, reason):  # noqa: ANN001
                msg = f"ticker error code={code} reason={reason}"
                logger.error(msg)
                self._error = msg

            def on_close(_ws, code, reason):  # noqa: ANN001
                logger.warning("Tick stream closed code=%s reason=%s", code, reason)
                self._running = False

            kws.on_ticks = on_ticks
            kws.on_connect = on_connect
            kws.on_error = on_error
            kws.on_close = on_close

            self._kws = kws
            kws.connect(threaded=True)
            self._running = True

        return {
            "ok": True,
            "message": "Stream started",
            "tokens": list(tokens),
            "subscriber_id": subscriber_id,
        }

    @staticmethod
    def _mode_rank(mode: str) -> int:
        mode_l = (mode or "ltp").lower()
        if mode_l == "full":
            return 3
        if mode_l == "quote":
            return 2
        return 1

    @staticmethod
    def _apply_mode(ws: KiteTicker, mode: str, tokens: list[int]) -> None:
        mode_l = (mode or "ltp").lower()
        if mode_l == "full":
            ws.set_mode(ws.MODE_FULL, tokens)
        elif mode_l == "quote":
            ws.set_mode(ws.MODE_QUOTE, tokens)
        else:
            ws.set_mode(ws.MODE_LTP, tokens)

    def stop(self) -> dict[str, Any]:
        with self._lock:
            kws = self._kws
            self._kws = None
            self._tokens = []
            self._running = False
            self._subscribers.clear()
        if kws is not None:
            try:
                kws.close()
            except Exception as err:
                logger.error("Error closing ticker: %s", err)
                return {"ok": False, "error": str(err)}
        return {"ok": True, "message": "Stream stopped"}

    def get_latest(self, token: Optional[int] = None) -> dict[str, Any]:
        with self._lock:
            if token is None:
                return {
                    "running": self._running,
                    "tokens": list(self._tokens),
                    "mode": self._stream_mode,
                    "subscribers": len(self._subscribers),
                    "error": self._error,
                    "ticks": {str(k): v for k, v in self._latest.items()},
                }
            tick = self._latest.get(int(token))
            return {
                "running": self._running,
                "instrument_token": int(token),
                "tick": tick,
                "error": self._error,
            }


tick_stream = TickStream()
