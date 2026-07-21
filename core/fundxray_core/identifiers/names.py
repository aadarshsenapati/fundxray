"""Company name normalisation — the unglamorous core of entity resolution.

'HDFC Bank Ltd' / 'HDFC Bank Limited' / 'HDFC BANK LTD.' are one company.
"""
from __future__ import annotations

import re

_SUFFIXES = [
    "limited", "ltd", "private", "pvt", "public", "plc", "corporation", "corp",
    "company", "co", "incorporated", "inc", "the",
]
_NOISE = re.compile(r"\b(equity|shares?|holdings? of|listed|unlisted)\b", re.I)
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def normalise_name(raw: str | None) -> str:
    if not raw:
        return ""
    s = str(raw).lower()
    s = _NOISE.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    tokens = [t for t in _WS.split(s) if t and t not in _SUFFIXES]
    return " ".join(tokens).strip()


def blocking_key(raw: str | None) -> str:
    """Cheap key to bucket candidates before expensive fuzzy comparison."""
    n = normalise_name(raw)
    return n[:4] if n else ""
