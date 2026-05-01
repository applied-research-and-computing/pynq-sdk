from __future__ import annotations

import inspect
from typing import Any, List


def extract_args(handler: Any, scpi_args: List[str], injected_count: int) -> List[Any]:
    """
    Extract typed positional args from SCPI token list using handler type hints.

    The first `injected_count` parameters of the handler are IP objects injected
    by the SDK; this function processes only the remaining parameters.

    Supported annotations: int, float, bool, str (default).
    Quoted SCPI strings have their surrounding quotes stripped.
    """
    sig = inspect.signature(handler)
    params = list(sig.parameters.values())
    scpi_params = params[injected_count:]

    result: List[Any] = []
    for i, param in enumerate(scpi_params):
        if i >= len(scpi_args):
            if param.default is not inspect.Parameter.empty:
                result.append(param.default)
            else:
                raise ValueError(f"Missing required parameter '{param.name}'")
            continue

        raw = scpi_args[i]
        annotation = param.annotation

        if annotation is int:
            result.append(_parse_int(raw))
        elif annotation is float:
            result.append(float(raw))
        elif annotation is bool:
            result.append(_parse_bool(raw))
        else:
            # str or unannotated
            result.append(_strip_quotes(raw))

    return result


def _parse_int(s: str) -> int:
    s = s.strip()
    if s.startswith(("0x", "0X")):
        return int(s, 16)
    if s.startswith(("0b", "0B")):
        return int(s, 2)
    return int(s)


def _parse_bool(s: str) -> bool:
    upper = s.strip().upper()
    if upper in ("1", "ON", "TRUE"):
        return True
    if upper in ("0", "OFF", "FALSE"):
        return False
    raise ValueError(f"Invalid boolean value: {s!r}")


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] in ('"', "'") and s[-1] == s[0]:
        return s[1:-1]
    return s
