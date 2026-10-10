"""Paper futures desk: several indicators, one shared Nifty spot strike.

Each indicator keeps its own signal timer and its own call/put book.
Fills use the option last price. Nothing is sent to Kotak.
"""

from __future__ import annotations

import copy
import datetime
import logging
import threading
from typing import Any, Optional

from kiteconnect import KiteConnect

from config import settings
from services.bxtrender_strategy import BXtrenderState
from services.candles import CandleAggregator
from services import custom_indicators
from services.instruments import resolve_nifty_fut_symbol, resolve_spot_atm_ce_pe
from services.market_hours import session_status
from services.qty import resolve_order_qty
from services.quotes import get_ltp
from services.ticker import tick_stream
from services.ut_bot_strategy import UTBotState
from services.warmup import warmup_from_history

logger = logging.getLogger("NeoTV.PaperFutures")

# A later indicator is one row here plus its signal class.
INDICATORS: list[dict[str, str]] = [
    {"id": "ut_bot_fut_cepe", "label": "UT Bot"},
    {"id": "bxtrender_fut_cepe", "label": "B-Xtrender"},
]


def _new_state(mode: str) -> Any:
    if mode.startswith("ut_bot"):
        return UTBotState()
    if mode.startswith("bxtrender"):
        return BXtrenderState()
    return custom_indicators.new_state(mode)


def _preview(state: Any, ltp: float) -> Optional[str]:
    snap = state.snapshot() if hasattr(state, "snapshot") else copy.deepcopy(state)
    return snap.process_candle(close=ltp)


class _Book:
    def __init__(self, mode: str, label: str) -> None:
        self.mode = mode
        self.label = label
        self.state = _new_state(mode)
        self.warmup_bars = 0
        self.active_side = "flat"
        self.wait_first = True
        self.preview_signal: Optional[str] = None
        self.preview_hold: Optional[str] = None
        self.pending_signal: Optional[str] = None
        self.pending_since: Optional[datetime.datetime] = None
        self.hold_ready = False
        self.pending_ignored = False
        self.closed_signal: Optional[str] = None
        self.closed_bar: Optional[datetime.datetime] = None
        self.acted_bar: Optional[datetime.datetime] = None
        self.stop_anchor: Optional[float] = None
        self.stop_wait: Optional[str] = None
        self.last_signal: Optional[str] = None
        self.trades: list[dict[str, Any]] = []
        self.open_row: Optional[dict[str, Any]] = None
        self.realized = 0.0
        self.error: Optional[str] = None


class PaperFuturesDesk:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._running = False
        self._kite: Optional[KiteConnect] = None
        self._subscriber_id: Optional[str] = None
        self._aggregator: Optional[CandleAggregator] = None
        self._instrument_token: Optional[int] = None
        self._fut_symbol = ""
        self._ce_symbol = ""
        self._pe_symbol = ""
        self._qty = 0
        self._atm: Optional[dict[str, Any]] = None
        self._books: list[_Book] = []
        self._last_ltp: Optional[float] = None
        self._last_tick_time: Optional[str] = None
        self._error: Optional[str] = None
        self._px_cache: dict[str, float] = {}

    def indicators(self) -> list[dict[str, Any]]:
        rows = [{**row, "custom": False} for row in INDICATORS]
        rows.extend(custom_indicators.list_public())
        return rows

    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self, kite: KiteConnect, modes: list[str]) -> dict[str, Any]:
        chosen = []
        seen: set[str] = set()
        for raw in modes or []:
            mode = str(raw or "").strip().lower()
            known = {row["id"] for row in self.indicators()}
            if mode in known and mode not in seen:
                seen.add(mode)
                chosen.append(mode)
        if not chosen:
            return {"ok": False, "error": "Check at least one indicator"}

        if self.running():
            self.stop()

        resolved_fut = resolve_nifty_fut_symbol(kite)
        if not resolved_fut.get("ok") and not resolved_fut.get("tradingsymbol"):
            return resolved_fut if isinstance(resolved_fut, dict) else {"ok": False, "error": "Fut resolve failed"}
        fut_symbol = str(resolved_fut.get("tradingsymbol") or settings.NIFTY_FUT_SYMBOL).strip().upper()
        if not fut_symbol:
            return {"ok": False, "error": "No NIFTY futures symbol"}

        try:
            ltp_payload = get_ltp(kite, "NFO", fut_symbol, enforce=False)
            instrument_token = int(ltp_payload["data"].get("instrument_token"))
            fut_ltp = float(ltp_payload["data"].get("last_price", 0.0))
        except Exception as err:
            return {"ok": False, "error": f"Failed to resolve futures LTP: {err}"}

        atm = resolve_spot_atm_ce_pe(kite)
        if not atm.get("ok"):
            return atm
        ce_symbol = str(atm["ce_symbol"]).upper()
        pe_symbol = str(atm["pe_symbol"]).upper()
        qty = resolve_order_qty(tradingsymbol=ce_symbol, exchange="NFO")

        labels = {row["id"]: row["label"] for row in self.indicators()}
        books = [_Book(mode, labels[mode]) for mode in chosen]
        for book in books:
            book.warmup_bars = warmup_from_history(
                kite, instrument_token, fut_ltp, book.state
            )

        aggregator = CandleAggregator(timeframe_minutes=settings.STRATEGY_TIMEFRAME_MINUTES)
        desk = self

        def on_tick(tick: dict[str, Any]) -> None:
            desk._on_tick(tick)

        stream = tick_stream.start([instrument_token], mode="full", on_tick=on_tick)
        if not stream.get("ok"):
            return {"ok": False, "error": stream.get("error", "Failed to start tick stream")}

        with self._lock:
            self._running = True
            self._kite = kite
            self._subscriber_id = stream.get("subscriber_id")
            self._aggregator = aggregator
            self._instrument_token = instrument_token
            self._fut_symbol = fut_symbol
            self._ce_symbol = ce_symbol
            self._pe_symbol = pe_symbol
            self._qty = qty
            self._atm = atm
            self._books = books
            self._last_ltp = fut_ltp
            self._last_tick_time = None
            self._error = None
            self._px_cache = {}

        names = ", ".join(book.label for book in books)
        premium = str(atm.get("premium_note") or "").strip()
        logger.info(
            "Paper futures started %s fut=%s strike=%s ce=%s pe=%s",
            names,
            fut_symbol,
            atm.get("strike"),
            ce_symbol,
            pe_symbol,
        )
        return {
            "ok": True,
            "message": (
                f"Paper. No Kotak orders. {names} on {fut_symbol}; "
                f"Nifty spot {atm.get('spot')} → strike {atm.get('strike')} "
                f"{ce_symbol} / {pe_symbol}. "
                + (premium + " " if premium else "")
                + "Waiting for the first futures signal on each indicator (BUY → CE, SELL → PE)."
            ),
            "status": self.get_status(),
        }

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self._running:
                return {"ok": True, "message": "Paper is not running", "status": self._status_locked()}
            subscriber_id = self._subscriber_id
            self._running = False
            self._subscriber_id = None
            self._aggregator = None
        if subscriber_id:
            tick_stream.unsubscribe(subscriber_id)
        logger.info("Paper futures stopped")
        return {
            "ok": True,
            "message": "Paper stopped. Open legs were not sold.",
            "status": self.get_status(),
        }

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            status = self._status_locked()
            kite = self._kite
        self._mark_open(status, kite)
        return status

    def _status_locked(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "fut_symbol": self._fut_symbol or None,
            "ce_symbol": self._ce_symbol or None,
            "pe_symbol": self._pe_symbol or None,
            "qty": self._qty,
            "resolved_atm": self._atm,
            "last_ltp": self._last_ltp,
            "last_tick_time": self._last_tick_time,
            "error": self._error,
            "warmup_bars": max((book.warmup_bars for book in self._books), default=0),
            "hold_seconds": float(settings.FUT_SIGNAL_HOLD_SECONDS),
            "stop_points": float(settings.FUT_STOP_POINTS),
            "market": session_status(),
            "modes": [book.mode for book in self._books],
            "indicators": [self._book_public(book) for book in self._books],
        }

    def _book_public(self, book: _Book) -> dict[str, Any]:
        open_row = book.open_row
        if book.wait_first and self._running:
            phase = "Waiting for first signal (BUY → CE, SELL → PE)"
        elif book.active_side == "ce" and self._running:
            phase = "Long call — wait for SELL"
        elif book.active_side == "pe" and self._running:
            phase = "Long put — wait for BUY"
        elif book.stop_wait == "SELL" and book.active_side == "flat" and self._running:
            phase = "Stopped out — wait for SELL"
        elif book.stop_wait == "BUY" and book.active_side == "flat" and self._running:
            phase = "Stopped out — wait for BUY"
        elif book.active_side != "flat" and not self._running:
            phase = "Stopped — open leg was not sold"
        elif self._running:
            phase = "Waiting for futures signal"
        else:
            phase = "Stopped"
        signal = book.preview_hold or book.preview_signal
        return {
            "id": book.mode,
            "label": book.label,
            "phase": phase,
            "active_side": book.active_side,
            "wait_first": book.wait_first,
            "live_signal": (signal + " holding") if book.preview_hold else (signal or None),
            "last_signal": book.last_signal,
            "warmup_bars": book.warmup_bars,
            "error": book.error,
            "stop_wait": book.stop_wait,
            "stop_anchor": book.stop_anchor,
            "stop_price": self._stop_price(book),
            "book": {
                "trades": [dict(row) for row in book.trades],
                "realized_pnl": round(book.realized, 2),
                "unrealized_pnl": None,
                "total_pnl": round(book.realized, 2),
                "mark_price": None,
                "open_symbol": open_row.get("symbol") if open_row else None,
                "open_entry": open_row.get("entry_price") if open_row else None,
                "open_qty": open_row.get("qty") if open_row else None,
            },
        }

    def _mark_open(self, status: dict[str, Any], kite: Optional[KiteConnect]) -> None:
        if kite is None:
            return
        prices: dict[str, Optional[float]] = {}
        for row in status.get("indicators") or []:
            book = row.get("book") or {}
            symbol = book.get("open_symbol")
            if not symbol:
                continue
            if symbol not in prices:
                prices[symbol] = self._fetch_px(kite, symbol)
            px = prices.get(symbol)
            if px is None:
                continue
            entry = float(book.get("open_entry") or 0)
            qty = int(book.get("open_qty") or 0)
            unreal = round((px - entry) * qty, 2)
            book["mark_price"] = round(px, 2)
            book["unrealized_pnl"] = unreal
            book["total_pnl"] = round(float(book.get("realized_pnl") or 0) + unreal, 2)
            for trade in book.get("trades") or []:
                if trade.get("status") == "open":
                    trade["pnl"] = unreal
                    trade["mark_price"] = round(px, 2)
                    trade["running_total"] = book["total_pnl"]

    def _option_ltp(self, kite: KiteConnect, symbol: str) -> Optional[float]:
        cached = self._px_cache.get(symbol)
        if cached:
            return cached
        px = self._fetch_px(kite, symbol)
        if px is not None:
            self._px_cache[symbol] = px
        return px

    def _fetch_px(self, kite: KiteConnect, symbol: str) -> Optional[float]:
        try:
            payload = get_ltp(kite, "NFO", symbol, enforce=False)
            px = float((payload.get("data") or {}).get("last_price") or 0)
        except Exception as err:
            logger.warning("Paper option price failed %s: %s", symbol, err)
            return None
        return px if px > 0 else None

    def _on_tick(self, tick: dict[str, Any]) -> None:
        with self._lock:
            if not self._running or self._aggregator is None or self._instrument_token is None:
                return
            if int(tick.get("instrument_token", 0)) != self._instrument_token:
                return
            aggregator = self._aggregator
            books = list(self._books)
            kite = self._kite

        ltp = float(tick.get("last_price", 0.0))
        volume = float(tick.get("volume_traded", tick.get("volume", 0.0)))
        tick_time = tick.get("timestamp")
        if not isinstance(tick_time, datetime.datetime):
            tick_time = datetime.datetime.now()
        with self._lock:
            self._last_ltp = ltp
            self._last_tick_time = tick_time.isoformat()
            self._px_cache = {}

        if settings.TV_WEBHOOK_ENABLED:
            return

        completed = aggregator.process_tick(ltp, volume, tick_time)
        for book in books:
            self._advance(book, aggregator, ltp, completed, kite)

    def _stop_price(self, book: _Book) -> Optional[float]:
        anchor = book.stop_anchor
        if anchor is None or book.active_side not in ("ce", "pe"):
            return None
        points = float(settings.FUT_STOP_POINTS)
        if book.active_side == "ce":
            return round(float(anchor) - points, 2)
        return round(float(anchor) + points, 2)

    def _stop_exit_if_hit(
        self,
        book: _Book,
        ltp: float,
        kite: Optional[KiteConnect],
    ) -> bool:
        """Sell the open leg when futures is past the stop.

        Returns True when the exit failed, so this tick must not open the other leg.
        """
        with self._lock:
            price = self._stop_price(book)
            side = book.active_side
            symbol = self._ce_symbol if side == "ce" else self._pe_symbol
        if price is None or side not in ("ce", "pe"):
            return False
        hit = float(ltp) <= price if side == "ce" else float(ltp) >= price
        if not hit:
            return False
        if kite is None or not symbol:
            with self._lock:
                book.error = "Paper stop could not price the open option"
            return True
        px = self._option_ltp(kite, symbol)
        if px is None:
            with self._lock:
                book.error = f"Paper stop could not price {symbol}"
            return True
        self._close(book, px)
        with self._lock:
            if book.active_side != "flat":
                book.error = f"Paper stop could not close {symbol}"
                return True
            book.stop_anchor = None
            book.stop_wait = "SELL" if side == "ce" else "BUY"
        logger.info("PAPER %s stop closed %s. Wait for %s", book.label, side.upper(), "SELL" if side == "ce" else "BUY")
        return False

    def _advance(
        self,
        book: _Book,
        aggregator: CandleAggregator,
        ltp: float,
        completed_bar: Optional[dict[str, Any]],
        kite: Optional[KiteConnect],
    ) -> None:
        if completed_bar:
            closed_signal = book.state.process_candle(
                close=completed_bar["close"],
                high=completed_bar["high"],
                low=completed_bar["low"],
            )
            bar_ts = completed_bar["timestamp"]
            with self._lock:
                acted = book.acted_bar == bar_ts
                if closed_signal in ("BUY", "SELL") and not acted:
                    book.last_signal = closed_signal
                    book.closed_signal = closed_signal
                    book.closed_bar = bar_ts
                else:
                    book.closed_signal = None
                    book.closed_bar = None

        if self._stop_exit_if_hit(book, ltp, kite):
            return

        with self._lock:
            pending_signal = book.closed_signal
            pending_bar = book.closed_bar
            pending_acted = pending_bar is not None and book.acted_bar == pending_bar
        if pending_signal in ("BUY", "SELL") and not pending_acted:
            outcome = self._flip(book, pending_signal, kite)
            with self._lock:
                if outcome is True:
                    book.acted_bar = pending_bar
                    book.closed_signal = None
                    book.preview_hold = None
                elif outcome is None:
                    book.closed_signal = None

        forming = aggregator.current_candle_start
        if forming is None:
            return
        preview = _preview(book.state, ltp)
        hold_seconds = float(settings.FUT_SIGNAL_HOLD_SECONDS)
        now = datetime.datetime.now()
        with self._lock:
            book.preview_signal = preview
            already = book.acted_bar == forming
            if preview in ("BUY", "SELL"):
                book.last_signal = preview
            if preview not in ("BUY", "SELL"):
                book.pending_signal = None
                book.pending_since = None
                book.hold_ready = False
                book.pending_ignored = False
                book.preview_hold = None
                return
            if already:
                book.preview_hold = None
                return
            if preview != book.pending_signal:
                book.pending_signal = preview
                book.pending_since = now
                book.hold_ready = hold_seconds <= 0
                book.pending_ignored = False
            elif (
                not book.hold_ready
                and book.pending_since is not None
                and (now - book.pending_since).total_seconds() >= hold_seconds
            ):
                book.hold_ready = True
            ready = book.hold_ready
            ignored = book.pending_ignored
            book.preview_hold = None if ready else preview
        if not ready or ignored:
            return
        outcome = self._flip(book, preview, kite)
        if outcome is True:
            with self._lock:
                book.acted_bar = forming
                book.preview_hold = None
        elif outcome is None:
            with self._lock:
                book.pending_ignored = True

    def _flip(self, book: _Book, signal: str, kite: Optional[KiteConnect]) -> Optional[bool]:
        if signal not in ("BUY", "SELL") or kite is None:
            return None
        with self._lock:
            wait = book.stop_wait
            active = book.active_side
            ce_symbol = self._ce_symbol
            pe_symbol = self._pe_symbol
        if wait == "SELL" and signal == "BUY":
            return None
        if wait == "BUY" and signal == "SELL":
            return None
        if not ce_symbol or not pe_symbol:
            with self._lock:
                book.error = "CE/PE symbols missing"
            return False
        if signal == "BUY":
            if active == "pe":
                px = self._option_ltp(kite, pe_symbol)
                if px is None:
                    with self._lock:
                        book.error = f"Could not read put price {pe_symbol}"
                    return False
                self._close(book, px)
                active = "flat"
            if active == "flat":
                px = self._option_ltp(kite, ce_symbol)
                if px is None:
                    with self._lock:
                        book.error = f"Could not read call price {ce_symbol}"
                    return False
                self._open(book, "CE", ce_symbol, px)
        else:
            if active == "ce":
                px = self._option_ltp(kite, ce_symbol)
                if px is None:
                    with self._lock:
                        book.error = f"Could not read call price {ce_symbol}"
                    return False
                self._close(book, px)
                active = "flat"
            if active == "flat":
                px = self._option_ltp(kite, pe_symbol)
                if px is None:
                    with self._lock:
                        book.error = f"Could not read put price {pe_symbol}"
                    return False
                self._open(book, "PE", pe_symbol, px)
        with self._lock:
            side = book.active_side
        return side == ("ce" if signal == "BUY" else "pe")

    def _close(self, book: _Book, price: float) -> None:
        now = datetime.datetime.now().isoformat(timespec="seconds")
        with self._lock:
            row = book.open_row
            if not row:
                return
            entry = float(row["entry_price"])
            qty = int(row["qty"])
            pnl = round((float(price) - entry) * qty, 2)
            book.realized = round(book.realized + pnl, 2)
            row["exit_price"] = round(float(price), 2)
            row["exit_at"] = now
            row["pnl"] = pnl
            row["status"] = "closed"
            row["running_total"] = book.realized
            book.open_row = None
            book.active_side = "flat"
            book.wait_first = False
            book.error = None
        logger.info(
            "PAPER %s close %s %s @ %.2f pnl=%.2f total=%.2f",
            book.label,
            row.get("leg"),
            row.get("symbol"),
            price,
            pnl,
            book.realized,
        )

    def _open(self, book: _Book, leg: str, symbol: str, price: float) -> None:
        now = datetime.datetime.now().isoformat(timespec="seconds")
        with self._lock:
            qty = int(self._qty or resolve_order_qty(tradingsymbol=symbol, exchange="NFO"))
            row = {
                "n": len(book.trades) + 1,
                "leg": leg,
                "symbol": symbol,
                "qty": qty,
                "entry_price": round(float(price), 2),
                "entry_at": now,
                "exit_price": None,
                "exit_at": None,
                "pnl": None,
                "status": "open",
                "running_total": round(book.realized, 2),
            }
            book.trades.append(row)
            book.open_row = row
            book.active_side = "ce" if leg == "CE" else "pe"
            book.wait_first = False
            book.stop_anchor = float(self._last_ltp) if self._last_ltp else None
            book.stop_wait = None
            book.error = None
        logger.info("PAPER %s open %s %s @ %.2f qty=%s", book.label, leg, symbol, price, row["qty"])


paper_futures_desk = PaperFuturesDesk()
