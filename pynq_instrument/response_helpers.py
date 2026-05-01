from __future__ import annotations

from typing import Iterable


def respond_float(value: float) -> str:
    return f"{float(value):.6g}"


def respond_int(value: int) -> str:
    return str(int(value))


def respond_bool(value: bool) -> str:
    return "1" if value else "0"


def respond_enum(value: str) -> str:
    return str(value) if value is not None else ""


def respond_float_array(values: Iterable) -> str:
    return ",".join(f"{float(v):.6g}" for v in values)


def respond_error(code: int, message: str) -> str:
    return f"ERR:{code}:{message or ''}"
