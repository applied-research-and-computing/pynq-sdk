from __future__ import annotations

from typing import List, Tuple


def normalize_scpi(raw: str) -> Tuple[str, List[str]]:
    """
    Normalize a raw SCPI string.

    Returns (mnemonic, args) where mnemonic is uppercased and args are the
    space-separated tokens that follow it (quoted strings kept intact).

    Examples:
        "gpio:set 1 HIGH"      -> ("GPIO:SET", ["1", "HIGH"])
        "*idn?"                -> ("*IDN?", [])
        "  TEMP:READ?  "       -> ("TEMP:READ?", [])
        'OVERLAY:LOAD "a.bit"' -> ("OVERLAY:LOAD", ['"a.bit"'])
    """
    s = raw.strip()
    if not s:
        return ("", [])

    # Split mnemonic from args on first whitespace
    idx = 0
    while idx < len(s) and not s[idx].isspace():
        idx += 1

    mnemonic = s[:idx].upper()
    rest = s[idx:].strip()

    if not rest:
        return (mnemonic, [])

    args = _split_args(rest)
    return (mnemonic, args)


def _split_args(s: str) -> List[str]:
    """Split SCPI argument string on whitespace, preserving quoted substrings."""
    args: List[str] = []
    current: List[str] = []
    in_quote = False
    quote_char = ""

    for ch in s:
        if in_quote:
            current.append(ch)
            if ch == quote_char:
                in_quote = False
        elif ch in ('"', "'"):
            in_quote = True
            quote_char = ch
            current.append(ch)
        elif ch in (" ", "\t"):
            if current:
                args.append("".join(current))
                current = []
        else:
            current.append(ch)

    if current:
        args.append("".join(current))

    return args


def is_query(mnemonic: str) -> bool:
    return mnemonic.endswith("?")
