"""Turn pasted Pine Script into a StrategyState the paper desk can run."""

from __future__ import annotations

import ast
import importlib.util
import logging
import re
import sys
from typing import Any

import httpx

from config import settings

logger = logging.getLogger("FuturesPaper.PineConvert")

_ALLOWED_IMPORTS = {
    "__future__",
    "collections",
    "copy",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "functools",
    "itertools",
    "math",
    "statistics",
    "typing",
}
_BANNED_CALLS = {"exec", "eval", "open", "compile", "__import__"}

SYSTEM_PROMPT = """You convert TradingView Pine Script into a Python strategy class.

Output ONLY valid Python code. No markdown fences, no explanation.

Hard requirements:
1. Define exactly one class named StrategyState.
2. StrategyState.__init__(self) must initialize all indicator state.
3. StrategyState.process_candle(self, close: float, high: float = 0.0, low: float = 0.0) -> Optional[str]
   - Update indicators on each completed candle.
   - Return "BUY", "SELL", or None.
4. BUY means open the call. SELL means open the put. Do not stay flat after a sell.
5. Use only the Python standard library. No pandas, numpy, ta-lib, or network calls.
6. Allowed imports: typing, math, copy, dataclasses, collections, datetime, statistics, functools, itertools, decimal, enum.
7. Match Pine defaults and bar-close semantics as closely as practical.
8. Include `from __future__ import annotations` and `from typing import Optional, List` as needed.
"""


def _strip_code_fences(text: str) -> str:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:python)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _guard(code: str) -> tuple[bool, str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as err:
        return False, f"Syntax error in converted code: {err}"
    if "class StrategyState" not in code:
        return False, "Converted code must define class StrategyState"
    if "def process_candle" not in code:
        return False, "Converted code must define process_candle"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in _ALLOWED_IMPORTS:
                    return False, f"Import not allowed: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in _ALLOWED_IMPORTS:
                return False, f"Import not allowed: {node.module}"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _BANNED_CALLS:
                return False, f"Call not allowed: {node.func.id}"
    return True, ""


def load_strategy_state_from_code(python: str, module_name: str = "custom_pine"):
    code = (python or "").strip()
    if not code:
        raise ValueError("Strategy Python code is empty")
    ok, err = _guard(code)
    if not ok:
        raise ValueError(err)
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in (module_name or "strat"))
    if not safe or safe[0].isdigit():
        safe = f"pine_{safe}"
    mod_name = f"pine_strat_{safe}"
    spec = importlib.util.spec_from_loader(mod_name, loader=None)
    if spec is None:
        raise ImportError(f"Cannot create module spec for {mod_name}")
    mod = importlib.util.module_from_spec(spec)
    exec(compile(code, f"<{mod_name}>", "exec"), mod.__dict__)  # noqa: S102 — guarded strategy code
    sys.modules[mod_name] = mod
    cls = getattr(mod, "StrategyState", None)
    if cls is None:
        raise AttributeError("StrategyState not found in converted module")
    return cls()


def _endpoints() -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    groq_key = (settings.GROQ_API_KEY or "").strip()
    if groq_key:
        found.append(
            {
                "name": "groq",
                "api_key": groq_key,
                "base": (settings.GROQ_BASE_URL or "https://api.groq.com/openai/v1").rstrip("/"),
                "model": (settings.GROQ_MODEL or "openai/gpt-oss-120b").strip(),
            }
        )
    nvidia_key = (settings.NVIDIA_API_KEY or "").strip()
    if nvidia_key:
        found.append(
            {
                "name": "nvidia",
                "api_key": nvidia_key,
                "base": (settings.NVIDIA_BASE_URL or "https://integrate.api.nvidia.com/v1").rstrip("/"),
                "model": (settings.NVIDIA_MODEL or "deepseek-ai/deepseek-v4.1-flash").strip(),
            }
        )
    return found


def _chat(endpoint: dict[str, str], pine: str) -> dict[str, Any]:
    url = f"{endpoint['base']}/chat/completions"
    payload = {
        "model": endpoint["model"],
        "temperature": 0.2,
        "max_tokens": 8192,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Convert this Pine Script:\n\n" + pine},
        ],
    }
    headers = {
        "Authorization": f"Bearer {endpoint['api_key']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    name = endpoint["name"]
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0, read=90.0)) as client:
            resp = client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException:
        return {"ok": False, "error": f"{name} timed out"}
    except Exception as err:
        return {"ok": False, "error": f"{name} request failed: {err}"}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"{name} HTTP {resp.status_code}: {resp.text[:400]}"}
    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception as err:
        return {"ok": False, "error": f"Unexpected {name} response: {err}"}
    code = _strip_code_fences(content)
    ok, err = _guard(code)
    if not ok:
        return {"ok": False, "error": err}
    try:
        load_strategy_state_from_code(code, module_name="convert_check")
    except Exception as err:
        return {"ok": False, "error": f"Converted code did not load: {err}"}
    return {"ok": True, "python": code, "model": endpoint["model"], "provider": name}


def convert_pine_to_python(pine_source: str) -> dict[str, Any]:
    pine = (pine_source or "").strip()
    if not pine:
        return {"ok": False, "error": "Pine Script is empty"}
    endpoints = _endpoints()
    if not endpoints:
        return {"ok": False, "error": "No LLM key set. Add a Space secret named GROQAPIKEY, then restart."}
    errors: list[str] = []
    for endpoint in endpoints:
        result = _chat(endpoint, pine)
        if result.get("ok"):
            return result
        errors.append(str(result.get("error") or f"{endpoint['name']} failed"))
    return {"ok": False, "error": " | ".join(errors)}
