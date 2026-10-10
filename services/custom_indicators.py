"""Pasted indicators. Built-ins stay in the paper desk."""

from __future__ import annotations

import re
import threading
from typing import Any, Optional

from services.pine_convert import convert_pine_to_python, load_strategy_state_from_code

_lock = threading.Lock()
_items: dict[str, dict[str, str]] = {}


def _slug(name: str) -> str:
    raw = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return raw or "indicator"


def list_public() -> list[dict[str, Any]]:
    with _lock:
        return [
            {"id": row["id"], "label": row["label"], "custom": True}
            for row in _items.values()
        ]


def known(mode: str) -> bool:
    with _lock:
        return mode in _items


def new_state(mode: str) -> Any:
    with _lock:
        row = _items.get(mode)
        python = row["python"] if row else ""
    if not python:
        raise KeyError(mode)
    return load_strategy_state_from_code(python, module_name=mode)


def add(name: str, pine: str = "", python: str = "") -> dict[str, Any]:
    label = (name or "").strip()
    if not label:
        return {"ok": False, "error": "Name the indicator"}
    source = (pine or "").strip()
    code = (python or "").strip()
    if code:
        try:
            load_strategy_state_from_code(code, module_name="restore_check")
        except Exception as err:
            return {"ok": False, "error": str(err)}
    else:
        converted = convert_pine_to_python(source)
        if not converted.get("ok"):
            return converted
        code = str(converted.get("python") or "")
    base = "custom_" + _slug(label)
    with _lock:
        mode = base
        n = 2
        while mode in _items and _items[mode].get("python") != code:
            mode = f"{base}_{n}"
            n += 1
        _items[mode] = {"id": mode, "label": label, "pine": source, "python": code}
    return {
        "ok": True,
        "indicator": {"id": mode, "label": label, "custom": True},
        "python": code,
    }


def remove(mode: str) -> dict[str, Any]:
    with _lock:
        if mode not in _items:
            return {"ok": False, "error": "That indicator is not a pasted script"}
        del _items[mode]
    return {"ok": True}


def get(mode: str) -> Optional[dict[str, str]]:
    with _lock:
        row = _items.get(mode)
        return dict(row) if row else None
