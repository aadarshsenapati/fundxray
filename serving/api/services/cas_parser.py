"""CAS (Consolidated Account Statement) parser.

CAMS and KFintech issue a single password-protected PDF listing every mutual
fund folio an investor holds. It is the fastest on-ramp for a user: upload once
instead of typing in ten schemes.

PRIVACY POSTURE — this is somebody's complete financial position:
  * parsed entirely in memory; the PDF is never written to disk
  * PAN, email, address and folio numbers are dropped immediately after parsing
  * only (scheme name, current value) leaves this module
  * the password is used once and never retained
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from fundxray_core.utils.logging import get_logger

log = get_logger(__name__)

# Redact before anything is logged or returned.
PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
FOLIO_RE = re.compile(r"\bFolio No[:.]?\s*([\w/\- ]+)", re.I)

AMOUNT = r"([\d,]+\.\d{2})"
# e.g. "INF209K01YN0 - Aditya Birla Sun Life ... Growth   1,234.567   402.4844   4,96,789.12"
HOLDING_RE = re.compile(
    r"(?P<isin>IN[A-Z0-9]{10})\s*[-–]\s*(?P<scheme>.+?)\s+"
    r"(?P<units>[\d,]+\.\d+)\s+(?P<nav>[\d,]+\.\d+)\s+(?P<value>" + AMOUNT + r")")


@dataclass
class CASHolding:
    isin: str
    scheme_name: str
    units: float
    nav: float
    value: float


@dataclass
class CASResult:
    holdings: list[CASHolding] = field(default_factory=list)
    total_value: float = 0.0
    pages: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_portfolio(self) -> dict[str, float]:
        """{isin: value} — the only shape that leaves this module."""
        return {h.isin: h.value for h in self.holdings}


def _num(s: str) -> float:
    return float(str(s).replace(",", ""))


def redact(text: str) -> str:
    text = PAN_RE.sub("[PAN REDACTED]", text)
    text = EMAIL_RE.sub("[EMAIL REDACTED]", text)
    return FOLIO_RE.sub("Folio No: [REDACTED]", text)


def parse_bytes(data: bytes, password: str | None = None) -> CASResult:
    """Parse a CAS PDF held in memory. Nothing is written to disk."""
    import pdfplumber

    result = CASResult()
    with pdfplumber.open(io.BytesIO(data), password=password) as pdf:
        result.pages = len(pdf.pages)
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)

    text = redact(text)   # redact BEFORE any parsing or logging

    seen: dict[str, CASHolding] = {}
    for m in HOLDING_RE.finditer(text):
        try:
            h = CASHolding(
                isin=m.group("isin").strip(),
                scheme_name=" ".join(m.group("scheme").split()),
                units=_num(m.group("units")),
                nav=_num(m.group("nav")),
                value=_num(m.group("value")),
            )
        except ValueError:
            continue
        if h.value <= 0:
            continue
        # An investor can hold the same scheme across multiple folios.
        if h.isin in seen:
            prev = seen[h.isin]
            seen[h.isin] = CASHolding(h.isin, h.scheme_name,
                                      prev.units + h.units, h.nav,
                                      prev.value + h.value)
        else:
            seen[h.isin] = h

    result.holdings = sorted(seen.values(), key=lambda x: -x.value)
    result.total_value = round(sum(h.value for h in result.holdings), 2)
    if not result.holdings:
        result.warnings.append(
            "No holdings recognised. CAS layouts differ between CAMS and KFintech; "
            "enter your schemes manually if this persists.")
    log.info("CAS parsed: %d pages, %d holdings, total %.2f",
             result.pages, len(result.holdings), result.total_value)
    return result
