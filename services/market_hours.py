"""NSE cash session clock. Monday to Friday, 09:15 to 15:30 IST."""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_OPEN = datetime.time(9, 15)
_CLOSE = datetime.time(15, 30)


def _at(day: datetime.date, clock: datetime.time) -> datetime.datetime:
    return datetime.datetime.combine(day, clock, tzinfo=IST)


def _next_open(now: datetime.datetime) -> datetime.datetime:
    day = now.date()
    if now.weekday() < 5 and now.time() < _OPEN:
        return _at(day, _OPEN)
    day = day + datetime.timedelta(days=1)
    while day.weekday() >= 5:
        day = day + datetime.timedelta(days=1)
    return _at(day, _OPEN)


def _fmt(moment: datetime.datetime) -> str:
    return moment.strftime("%A %d %b %Y, %H:%M IST")


def session_status(now: datetime.datetime | None = None) -> dict:
    """Regular session only. A weekday holiday still looks open until the ticks stay quiet."""
    current = now.astimezone(IST) if now is not None else datetime.datetime.now(IST)
    weekday = current.weekday()
    clock = current.time()
    opens_at = _next_open(current)
    if weekday < 5 and _OPEN <= clock < _CLOSE:
        state = "open"
        reason = "open"
        detail = "The regular session is live. A futures signal must hold 15 seconds before a paper fill."
    elif weekday >= 5:
        state = "closed"
        reason = "weekend"
        detail = "The market is closed for the weekend. Prices on screen are from the last session."
    elif clock < _OPEN:
        state = "closed"
        reason = "before_open"
        detail = "The market has not opened yet. Prices on screen are from the last session."
    else:
        state = "closed"
        reason = "after_close"
        detail = "The regular session is over for today. Prices on screen are from the close."

    later_day = opens_at.date() != current.date()
    if state == "open":
        login_note = ""
        armed_note = ""
    elif later_day:
        login_note = (
            "A Zerodha login from today will not still be valid at the next open. "
            "Log in again that morning and press Start. This run does not carry over."
        )
        armed_note = (
            "The desk is armed and idle. The strike stays locked, and no new signal can print until ticks resume."
        )
    else:
        login_note = "If this login is from yesterday, it has expired. Log in this morning, then press Start."
        armed_note = (
            "The desk is armed and idle. The strike stays locked, and no new signal can print until the open."
        )

    return {
        "open": state == "open",
        "reason": reason,
        "label": "Market open" if state == "open" else "Market closed",
        "detail": detail,
        "now_ist": _fmt(current),
        "next_open": None if state == "open" else _fmt(opens_at),
        "closes": _fmt(_at(current.date(), _CLOSE)) if state == "open" else None,
        "session": "09:15–15:30 IST, Monday to Friday",
        "login_note": login_note,
        "armed_note": armed_note,
    }
