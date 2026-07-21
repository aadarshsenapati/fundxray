"""ISIN validation and normalisation.

ISIN = 2-letter country code + 9 alphanumeric NSIN + 1 check digit (Luhn over
letters expanded to their alphabet position). Indian equities start with INE/INF.
"""
from __future__ import annotations

import re

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def _expand(isin: str) -> str:
    out = []
    for ch in isin:
        out.append(str(ord(ch) - 55) if ch.isalpha() else ch)
    return "".join(out)


def luhn_ok(digits: str) -> bool:
    total, double = 0, False  # rightmost digit is the check digit: not doubled
    for ch in reversed(digits):
        d = int(ch)
        if double:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        double = not double
    return total % 10 == 0


_NULL_TOKENS = {"", "NAN", "NONE", "NA", "NIL", "NULL", "N.A.", "-", "--"}


def normalise(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = re.sub(r"[^A-Za-z0-9]", "", str(raw)).upper()
    return None if s in _NULL_TOKENS else (s or None)


def is_valid(raw: str | None) -> bool:
    s = normalise(raw)
    if not s or not ISIN_RE.match(s):
        return False
    return luhn_ok(_expand(s))


def is_equity(raw: str | None) -> bool:
    """Indian listed equity ISINs begin with INE. INF = mutual fund units."""
    s = normalise(raw)
    return bool(s and s.startswith("INE") and is_valid(s))
