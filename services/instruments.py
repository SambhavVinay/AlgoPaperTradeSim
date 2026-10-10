"""Resolve NIFTY futures and options from Kite instruments."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from kiteconnect import KiteConnect

from config import settings

logger = logging.getLogger("NeoTV.Instruments")


def _parse_expiry(exp: Any) -> Optional[date]:
    if isinstance(exp, datetime):
        return exp.date()
    if isinstance(exp, date):
        return exp
    if isinstance(exp, str) and exp:
        try:
            return datetime.strptime(exp[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


_NFO_CACHE: dict[str, Any] = {"ts": 0.0, "rows": []}


def load_nfo_instruments(kite: KiteConnect) -> list[dict[str, Any]]:
    """NFO instrument dump, reused for a few minutes."""
    now = time.time()
    cached = _NFO_CACHE.get("rows") or []
    if cached and now - float(_NFO_CACHE.get("ts") or 0) < 300:
        return cached
    rows = kite.instruments("NFO")
    if not isinstance(rows, list):
        rows = []
    _NFO_CACHE["ts"] = now
    _NFO_CACHE["rows"] = rows
    return rows


def _saved_expiry_date() -> Optional[date]:
    raw = str(getattr(settings, "NIFTY_OPTION_EXPIRY_DATE", "") or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def saved_expiry_is_current() -> bool:
    saved = _saved_expiry_date()
    return bool(saved and saved >= date.today())


def _choose_expiry(available: list[date]) -> Optional[date]:
    """Saved date when it is still ahead, otherwise the nearest upcoming expiry."""
    future = sorted({d for d in available if d >= date.today()})
    if not future:
        return None
    saved = _saved_expiry_date()
    if saved and saved in future:
        return saved
    return future[0]


def _is_nifty_option(row: dict[str, Any]) -> Optional[str]:
    name = str(row.get("name") or "").upper()
    if name != "NIFTY":
        return None
    itype = str(row.get("instrument_type") or "").upper()
    tsym = str(row.get("tradingsymbol") or "").upper()
    if itype in ("CE", "PE"):
        return itype
    if tsym.endswith("CE"):
        return "CE"
    if tsym.endswith("PE"):
        return "PE"
    return None


def list_nifty_option_expiries(kite: KiteConnect) -> dict[str, Any]:
    """Upcoming NIFTY option expiry dates and the nearest unexpired futures symbol."""
    try:
        rows = load_nfo_instruments(kite)
    except Exception as err:
        return {"ok": False, "error": f"Kite instruments(NFO) failed: {err}"}

    today = date.today()
    dates: set[date] = set()
    for row in rows:
        if not isinstance(row, dict) or not _is_nifty_option(row):
            continue
        exp_d = _parse_expiry(row.get("expiry"))
        if exp_d and exp_d >= today:
            dates.add(exp_d)

    ordered = sorted(dates)[:16]
    chosen = _choose_expiry(ordered)
    fut = resolve_nifty_fut_symbol(kite)
    return {
        "ok": True,
        "expiries": [
            {"date": d.isoformat(), "label": d.strftime("%d %b %Y")} for d in ordered
        ],
        "selected": chosen.isoformat() if chosen else "",
        "futures": {
            "tradingsymbol": fut.get("tradingsymbol"),
            "expiry": fut.get("expiry"),
            "source": fut.get("source"),
        },
    }


def list_nifty_option_strikes(kite: KiteConnect, expiry: date) -> dict[str, Any]:
    """CE and PE tradingsymbols for every NIFTY strike on one expiry."""
    try:
        rows = load_nfo_instruments(kite)
    except Exception as err:
        return {"ok": False, "error": f"Kite instruments(NFO) failed: {err}"}

    by_strike: dict[float, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_type = _is_nifty_option(row)
        if row_type not in ("CE", "PE"):
            continue
        exp_d = _parse_expiry(row.get("expiry"))
        if exp_d != expiry:
            continue
        try:
            strike = float(row.get("strike") or 0)
        except (TypeError, ValueError):
            continue
        if strike <= 0:
            continue
        slot = by_strike.setdefault(
            strike, {"strike": strike, "ce": "", "pe": ""}
        )
        symbol = str(row.get("tradingsymbol") or "").upper()
        if row_type == "CE":
            slot["ce"] = symbol
        else:
            slot["pe"] = symbol

    strikes = [by_strike[k] for k in sorted(by_strike)]
    return {
        "ok": True,
        "expiry": expiry.isoformat(),
        "strikes": strikes,
    }


def lookup_nifty_option(
    kite: KiteConnect,
    *,
    expiry: date,
    strike: float,
    option_type: str,
) -> dict[str, Any]:
    """Exact NIFTY option row for one expiry, strike, and CE/PE."""
    opt_type = str(option_type or "CE").strip().upper()
    if opt_type not in ("CE", "PE"):
        return {"ok": False, "error": "option_type must be CE or PE"}
    try:
        rows = load_nfo_instruments(kite)
    except Exception as err:
        return {"ok": False, "error": f"Kite instruments(NFO) failed: {err}"}

    for row in rows:
        if not isinstance(row, dict):
            continue
        row_type = _is_nifty_option(row)
        if row_type != opt_type:
            continue
        exp_d = _parse_expiry(row.get("expiry"))
        if exp_d != expiry:
            continue
        try:
            row_strike = float(row.get("strike") or 0)
        except (TypeError, ValueError):
            continue
        if abs(row_strike - float(strike)) >= 0.01:
            continue
        return {
            "ok": True,
            "tradingsymbol": str(row.get("tradingsymbol") or "").upper(),
            "exchange": "NFO",
            "expiry": exp_d.isoformat(),
            "strike": row_strike,
            "option_type": opt_type,
            "lot_size": int(row.get("lot_size") or settings.NIFTY_OPTION_LOT_SIZE),
            "instrument_token": row.get("instrument_token"),
        }
    return {
        "ok": False,
        "error": f"No NIFTY {opt_type} at strike {strike:g} for {expiry.isoformat()}",
    }


def resolve_nifty_fut_symbol(kite: KiteConnect) -> dict[str, Any]:
    """Pick nearest unexpired NIFTY monthly futures on NFO."""
    fallback = settings.NIFTY_FUT_SYMBOL.strip().upper()
    if not settings.NIFTY_FUT_AUTO_RESOLVE:
        return {
            "ok": True,
            "tradingsymbol": fallback,
            "exchange": "NFO",
            "source": "env",
            "lot_size": settings.NIFTY_FUT_LOT_SIZE,
        }

    try:
        instruments = load_nfo_instruments(kite)
    except Exception as err:
        logger.warning("Kite instruments(NFO) failed: %s — using env fallback %s", err, fallback)
        return {
            "ok": True,
            "tradingsymbol": fallback,
            "exchange": "NFO",
            "source": "env_fallback",
            "error": str(err),
            "lot_size": settings.NIFTY_FUT_LOT_SIZE,
        }

    today = date.today()
    candidates: list[dict[str, Any]] = []
    for row in instruments:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").upper()
        itype = str(row.get("instrument_type") or "").upper()
        tsym = str(row.get("tradingsymbol") or "").upper()
        if name != "NIFTY":
            continue
        if itype not in ("FUT",) and not tsym.endswith("FUT"):
            continue
        exp_d = _parse_expiry(row.get("expiry"))
        if not exp_d or exp_d < today:
            continue
        lot = int(row.get("lot_size") or settings.NIFTY_FUT_LOT_SIZE)
        candidates.append(
            {
                "tradingsymbol": tsym,
                "expiry": exp_d.isoformat(),
                "lot_size": lot,
                "instrument_token": row.get("instrument_token"),
            }
        )

    if not candidates:
        return {
            "ok": True,
            "tradingsymbol": fallback,
            "exchange": "NFO",
            "source": "env_fallback",
            "lot_size": settings.NIFTY_FUT_LOT_SIZE,
        }

    candidates.sort(key=lambda c: c["expiry"])
    best = candidates[0]
    symbol = str(best["tradingsymbol"]).upper()
    if symbol and symbol != fallback:
        try:
            from services.env_store import update_env_keys

            update_env_keys({"NIFTY_FUT_SYMBOL": symbol})
        except Exception as err:
            logger.warning("Could not persist futures symbol %s: %s", symbol, err)
            settings.NIFTY_FUT_SYMBOL = symbol
    return {
        "ok": True,
        "tradingsymbol": best["tradingsymbol"],
        "exchange": "NFO",
        "expiry": best["expiry"],
        "lot_size": best["lot_size"],
        "instrument_token": best.get("instrument_token"),
        "source": "kite_instruments",
        "alternatives": [c["tradingsymbol"] for c in candidates[:4]],
    }


def resolve_nifty_option_symbol(kite: KiteConnect) -> dict[str, Any]:
    """
    Resolve NIFTY options CE/PE for configured year/month.

    Strike 0 = ATM from NSE:NIFTY 50 LTP. Prefers furthest expiry in that month (monthly).
    """
    explicit = (settings.NIFTY_OPTION_SYMBOL or "").strip().upper()
    opt_type = (settings.NIFTY_OPTION_TYPE or "CE").strip().upper()
    if opt_type not in ("CE", "PE"):
        opt_type = "CE"
    lot_default = int(settings.NIFTY_OPTION_LOT_SIZE or settings.NIFTY_FUT_LOT_SIZE)

    if explicit and not settings.NIFTY_OPTION_AUTO_RESOLVE and saved_expiry_is_current():
        return {
            "ok": True,
            "tradingsymbol": explicit,
            "exchange": "NFO",
            "option_type": opt_type,
            "source": "env",
            "lot_size": lot_default,
        }

    spot = 0.0
    try:
        ltp = kite.ltp("NSE:NIFTY 50")
        spot = float(ltp.get("NSE:NIFTY 50", {}).get("last_price") or 0)
    except Exception as err:
        logger.warning("Could not fetch NIFTY 50 LTP for ATM: %s", err)

    strike_cfg = float(settings.NIFTY_OPTION_STRIKE or 0)

    try:
        instruments = load_nfo_instruments(kite)
    except Exception as err:
        if explicit:
            return {
                "ok": True,
                "tradingsymbol": explicit,
                "exchange": "NFO",
                "source": "env_fallback",
                "error": str(err),
                "lot_size": lot_default,
                "option_type": opt_type,
            }
        return {"ok": False, "error": f"Kite instruments(NFO) failed: {err}"}

    today = date.today()
    candidates: list[dict[str, Any]] = []
    for row in instruments:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").upper()
        itype = str(row.get("instrument_type") or "").upper()
        tsym = str(row.get("tradingsymbol") or "").upper()
        if name != "NIFTY":
            continue
        if itype not in ("CE", "PE") and not (tsym.endswith("CE") or tsym.endswith("PE")):
            continue
        row_type = itype if itype in ("CE", "PE") else ("CE" if tsym.endswith("CE") else "PE")
        if row_type != opt_type:
            continue
        exp_d = _parse_expiry(row.get("expiry"))
        if not exp_d or exp_d < today:
            continue
        try:
            strike = float(row.get("strike") or 0)
        except (TypeError, ValueError):
            continue
        lot = int(row.get("lot_size") or lot_default)
        candidates.append(
            {
                "tradingsymbol": tsym,
                "expiry": exp_d.isoformat(),
                "strike": strike,
                "lot_size": lot,
                "instrument_token": row.get("instrument_token"),
                "option_type": row_type,
            }
        )

    if not candidates:
        if explicit:
            return {
                "ok": True,
                "tradingsymbol": explicit,
                "exchange": "NFO",
                "source": "env_fallback_no_match",
                "lot_size": lot_default,
                "option_type": opt_type,
                "spot": spot,
            }
        return {
            "ok": False,
            "error": f"No upcoming NIFTY {opt_type}. Check the expiry date.",
            "spot": spot,
        }

    chosen = _choose_expiry(
        [datetime.strptime(c["expiry"], "%Y-%m-%d").date() for c in candidates]
    )
    if chosen is None:
        return {"ok": False, "error": f"No upcoming NIFTY {opt_type}.", "spot": spot}
    same_exp = [c for c in candidates if c["expiry"] == chosen.isoformat()]
    target_strike = strike_cfg if strike_cfg > 0 else spot

    if target_strike <= 0:
        same_exp.sort(key=lambda c: c["strike"])
        best = same_exp[len(same_exp) // 2]
    else:
        same_exp.sort(key=lambda c: abs(c["strike"] - target_strike))
        best = same_exp[0]

    logger.info(
        "Resolved NIFTY option %s (expiry=%s strike=%s lot=%s spot=%s)",
        best["tradingsymbol"],
        best["expiry"],
        best["strike"],
        best["lot_size"],
        spot,
    )
    return {
        "ok": True,
        "tradingsymbol": best["tradingsymbol"],
        "exchange": "NFO",
        "expiry": best["expiry"],
        "strike": best["strike"],
        "spot": spot,
        "option_type": best["option_type"],
        "lot_size": best["lot_size"],
        "instrument_token": best.get("instrument_token"),
        "source": "kite_instruments",
        "near_strikes": [c["tradingsymbol"] for c in same_exp[:5]],
    }


def round_spot_to_strike(spot: float) -> int:
    """Nearest 50-point NIFTY strike. A half step rounds up (25325 → 25350)."""
    steps = (Decimal(str(spot)) / Decimal(50)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(steps * 50)


def _premium_gap(ce_px: float, pe_px: float) -> Optional[float]:
    total = float(ce_px) + float(pe_px)
    if ce_px <= 0 or pe_px <= 0 or total <= 0:
        return None
    return abs(float(ce_px) - float(pe_px)) / total


def resolve_spot_atm_ce_pe(kite: KiteConnect) -> dict[str, Any]:
    """
    Lock one CE+PE strike from Nifty 50 spot, not from futures.

    Round spot to the nearest 50. Prefer that strike when call and put last
    prices differ by less than 10% of their sum. Otherwise try the strikes
    50 points either side. If none pass, use the closest pair anyway.
    """
    try:
        spot_payload = kite.ltp("NSE:NIFTY 50")
        spot = float(spot_payload.get("NSE:NIFTY 50", {}).get("last_price") or 0)
    except Exception as err:
        return {"ok": False, "error": f"Could not read Nifty 50 spot: {err}"}
    if spot <= 0:
        return {"ok": False, "error": "Nifty 50 spot price was empty"}

    anchor = round_spot_to_strike(spot)
    try:
        rows = load_nfo_instruments(kite)
    except Exception as err:
        return {"ok": False, "error": f"Kite instruments(NFO) failed: {err}"}

    today = date.today()
    expiries: list[date] = []
    book: dict[tuple[str, float, str], dict[str, Any]] = {}
    lot_default = int(settings.NIFTY_OPTION_LOT_SIZE or settings.NIFTY_FUT_LOT_SIZE)
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_type = _is_nifty_option(row)
        if row_type not in ("CE", "PE"):
            continue
        exp_d = _parse_expiry(row.get("expiry"))
        if not exp_d or exp_d < today:
            continue
        try:
            strike = float(row.get("strike") or 0)
        except (TypeError, ValueError):
            continue
        if strike <= 0:
            continue
        strike_i = int(round(strike))
        expiries.append(exp_d)
        book[(exp_d.isoformat(), strike_i, row_type)] = {
            "tradingsymbol": str(row.get("tradingsymbol") or "").upper(),
            "expiry": exp_d.isoformat(),
            "strike": float(strike_i),
            "lot_size": int(row.get("lot_size") or lot_default),
            "instrument_token": row.get("instrument_token"),
            "option_type": row_type,
        }

    chosen = _choose_expiry(expiries)
    if chosen is None:
        return {"ok": False, "error": "No upcoming NIFTY CE/PE. Pick a new expiry.", "spot": spot}
    chosen_iso = chosen.isoformat()
    wanted = [anchor - 50, anchor, anchor + 50]
    pairs: list[dict[str, Any]] = []
    symbols: list[str] = []
    for strike in wanted:
        ce = book.get((chosen_iso, int(strike), "CE"))
        pe = book.get((chosen_iso, int(strike), "PE"))
        if not ce or not pe:
            continue
        pairs.append({"strike": float(strike), "ce": ce, "pe": pe})
        symbols.extend([ce["tradingsymbol"], pe["tradingsymbol"]])
    if not pairs:
        return {
            "ok": False,
            "error": f"No NIFTY CE/PE near {anchor} for {chosen_iso}",
            "spot": spot,
            "anchor_strike": anchor,
        }

    keys = [f"NFO:{sym}" for sym in symbols]
    try:
        quote = kite.ltp(keys)
    except Exception as err:
        return {"ok": False, "error": f"Could not read option prices: {err}", "spot": spot}
    if not isinstance(quote, dict):
        quote = {}

    scored: list[dict[str, Any]] = []
    for pair in pairs:
        ce_key = f"NFO:{pair['ce']['tradingsymbol']}"
        pe_key = f"NFO:{pair['pe']['tradingsymbol']}"
        try:
            ce_px = float((quote.get(ce_key) or {}).get("last_price") or 0)
            pe_px = float((quote.get(pe_key) or {}).get("last_price") or 0)
        except (TypeError, ValueError):
            continue
        gap = _premium_gap(ce_px, pe_px)
        if gap is None:
            continue
        scored.append({**pair, "ce_ltp": ce_px, "pe_ltp": pe_px, "gap": gap})
    if not scored:
        return {
            "ok": False,
            "error": f"No live prices for NIFTY CE/PE near {anchor}",
            "spot": spot,
            "anchor_strike": anchor,
        }

    passing = [row for row in scored if row["gap"] < 0.10]
    anchor_hit = next((row for row in passing if abs(row["strike"] - anchor) < 0.01), None)
    if anchor_hit is not None:
        chosen_pair = anchor_hit
        balanced = True
    elif passing:
        chosen_pair = min(passing, key=lambda row: (abs(row["strike"] - anchor), row["gap"]))
        balanced = True
    else:
        chosen_pair = min(scored, key=lambda row: (row["gap"], abs(row["strike"] - anchor)))
        balanced = False

    ce = chosen_pair["ce"]
    pe = chosen_pair["pe"]
    gap_pct = round(chosen_pair["gap"] * 100, 1)
    if balanced:
        premium_note = (
            f"Call {chosen_pair['ce_ltp']:.2f} and put {chosen_pair['pe_ltp']:.2f} "
            f"are within 10% (gap {gap_pct}%)."
        )
    else:
        premium_note = (
            f"Premiums are more than 10% apart "
            f"(call {chosen_pair['ce_ltp']:.2f}, put {chosen_pair['pe_ltp']:.2f}, gap {gap_pct}%). "
            "Using the closest pair."
        )
    logger.info(
        "Spot ATM strike=%s anchor=%s spot=%.2f gap=%.1f%% balanced=%s → %s / %s",
        ce["strike"],
        anchor,
        spot,
        gap_pct,
        balanced,
        ce["tradingsymbol"],
        pe["tradingsymbol"],
    )
    return {
        "ok": True,
        "exchange": "NFO",
        "strike": ce["strike"],
        "expiry": ce["expiry"],
        "spot": spot,
        "anchor_strike": anchor,
        "reference_ltp": spot,
        "lot_size": ce["lot_size"],
        "ce": ce,
        "pe": pe,
        "ce_symbol": ce["tradingsymbol"],
        "pe_symbol": pe["tradingsymbol"],
        "ce_ltp": chosen_pair["ce_ltp"],
        "pe_ltp": chosen_pair["pe_ltp"],
        "premium_gap": chosen_pair["gap"],
        "premium_balanced": balanced,
        "premium_note": premium_note,
        "source": "nifty_spot",
    }


def resolve_atm_ce_pe(
    kite: KiteConnect,
    *,
    reference_ltp: float,
    nearest_to_reference: bool = False,
) -> dict[str, Any]:
    """
    Resolve CE + PE at one strike and one expiry.

    Futures mode (nearest_to_reference) uses the strike closest to the futures price
    for both CE and PE. Otherwise a saved strike is used when it is set.
    """
    lot_default = int(settings.NIFTY_OPTION_LOT_SIZE or settings.NIFTY_FUT_LOT_SIZE)
    try:
        explicit = float(settings.NIFTY_OPTION_STRIKE or 0)
    except (TypeError, ValueError):
        explicit = 0.0
    use_exact = explicit > 0 and not nearest_to_reference
    target = float(reference_ltp or 0.0) if nearest_to_reference or not use_exact else explicit
    if not use_exact and target <= 0:
        try:
            ltp = kite.ltp("NSE:NIFTY 50")
            target = float(ltp.get("NSE:NIFTY 50", {}).get("last_price") or 0)
        except Exception as err:
            return {"ok": False, "error": f"No reference LTP for ATM: {err}"}
    if target <= 0:
        return {"ok": False, "error": "No reference LTP for ATM strike"}

    try:
        instruments = load_nfo_instruments(kite)
    except Exception as err:
        return {"ok": False, "error": f"Kite instruments(NFO) failed: {err}"}

    today = date.today()
    by_type: dict[str, list[dict[str, Any]]] = {"CE": [], "PE": []}
    for row in instruments:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").upper()
        itype = str(row.get("instrument_type") or "").upper()
        tsym = str(row.get("tradingsymbol") or "").upper()
        if name != "NIFTY":
            continue
        if itype not in ("CE", "PE") and not (tsym.endswith("CE") or tsym.endswith("PE")):
            continue
        row_type = itype if itype in ("CE", "PE") else ("CE" if tsym.endswith("CE") else "PE")
        exp_d = _parse_expiry(row.get("expiry"))
        if not exp_d or exp_d < today:
            continue
        try:
            strike = float(row.get("strike") or 0)
        except (TypeError, ValueError):
            continue
        if strike <= 0:
            continue
        by_type[row_type].append(
            {
                "tradingsymbol": tsym,
                "expiry": exp_d.isoformat(),
                "strike": strike,
                "lot_size": int(row.get("lot_size") or lot_default),
                "instrument_token": row.get("instrument_token"),
                "option_type": row_type,
            }
        )

    available = [
        datetime.strptime(c["expiry"], "%Y-%m-%d").date()
        for c in by_type["CE"] + by_type["PE"]
    ]
    chosen = _choose_expiry(available)
    if chosen is None or not by_type["CE"] or not by_type["PE"]:
        return {
            "ok": False,
            "error": "No upcoming NIFTY CE/PE. Pick a new expiry.",
            "reference_ltp": target,
        }
    chosen_iso = chosen.isoformat()
    by_type["CE"] = [c for c in by_type["CE"] if c["expiry"] == chosen_iso]
    by_type["PE"] = [c for c in by_type["PE"] if c["expiry"] == chosen_iso]
    if not by_type["CE"] or not by_type["PE"]:
        return {
            "ok": False,
            "error": f"No NIFTY CE/PE found for {chosen_iso}",
            "reference_ltp": target,
        }

    def _pick(cands: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        cands = sorted(cands, key=lambda c: c["expiry"], reverse=True)
        best_exp = cands[0]["expiry"]
        same = [c for c in cands if c["expiry"] == best_exp]
        if use_exact:
            exact = [c for c in same if abs(c["strike"] - target) < 0.01]
            return exact[0] if exact else None
        same.sort(key=lambda c: abs(c["strike"] - target))
        return same[0]

    ce = _pick(by_type["CE"])
    if ce is None:
        return {
            "ok": False,
            "error": f"No NIFTY CE at strike {target:g} for {chosen_iso}",
            "reference_ltp": reference_ltp,
        }
    # PE must match CE strike + expiry
    pe_same = [
        c
        for c in by_type["PE"]
        if c["expiry"] == ce["expiry"] and abs(c["strike"] - ce["strike"]) < 0.01
    ]
    if not pe_same:
        pe = _pick(by_type["PE"])
        # If strikes diverge, prefer CE strike match only
        return {
            "ok": False,
            "error": (
                f"No PE matching CE strike {ce['strike']} expiry {ce['expiry']}; "
                f"nearest PE would be {pe['tradingsymbol']}"
            ),
            "reference_ltp": target,
            "ce": ce,
        }
    pe = pe_same[0]

    logger.info(
        "Resolved ATM CE+PE strike=%s expiry=%s ref=%.2f → %s / %s",
        ce["strike"],
        ce["expiry"],
        target,
        ce["tradingsymbol"],
        pe["tradingsymbol"],
    )
    return {
        "ok": True,
        "exchange": "NFO",
        "strike": ce["strike"],
        "expiry": ce["expiry"],
        "reference_ltp": target,
        "lot_size": ce["lot_size"],
        "ce": ce,
        "pe": pe,
        "ce_symbol": ce["tradingsymbol"],
        "pe_symbol": pe["tradingsymbol"],
        "source": "kite_instruments",
    }
