"""Shared tabular parsing machinery for AMC disclosure adapters.

Every real quirk this handles was observed in actual SEBI-mandated monthly
portfolio disclosures:
  * header row anywhere in the first ~40 rows (preamble varies by AMC)
  * several stacked schemes in one sheet, separated by a bare scheme-name row
  * value columns denominated in lakhs OR crores, declared only in the header
  * percentages stored as text with a '%' suffix
  * Indian digit grouping ("3,20,000")
  * footnote blocks after the data
  * legacy files with no ISIN column at all
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, Optional

import pandas as pd

from fundxray_core.identifiers.isin import is_valid, normalise
from fundxray_core.schemas import RawHolding
from fundxray_core.utils.logging import get_logger

log = get_logger(__name__)

NAME_HINTS = ["name of the instrument", "name of instrument", "security name",
              "instrument", "particulars", "name of the security"]
ISIN_HINTS = ["isin"]
QTY_HINTS = ["quantity", "qty", "no. of shares", "no of shares", "units", "number of shares"]
VALUE_HINTS = ["market value", "market/fair value", "fair value", "value", "amount"]
WEIGHT_HINTS = ["% to nav", "% of nav", "% to net assets", "% to aum", "percentage", "% to"]

SCHEME_RE = re.compile(r"\b(fund|scheme|plan)\b", re.I)
DEBT_RE = re.compile(r"\b(bond|debenture|ncd|g-?sec|gilt|t-?bill|sdl|treasury|commercial paper)\b", re.I)
CASH_RE = re.compile(r"\b(treps|repo|cash|net receivable|net current asset|"
                     r"cash equivalent|net payable|margin|other receivable)\b", re.I)
DERIV_RE = re.compile(r"\b(future|option|swap|forward)\b", re.I)
TOTAL_RE = re.compile(r"^\s*(grand\s+)?total|^\s*net asset", re.I)
FOOTNOTE_RE = re.compile(r"^\s*(notes?\s*:|\d+\.\s|\*|disclaimer|source\s*:)", re.I)

LAKH_RE = re.compile(r"\blakh?s?\b", re.I)
CRORE_RE = re.compile(r"\bcrores?\b", re.I)


def match_col(col: str, hints: list[str]) -> bool:
    c = str(col).strip().lower()
    return any(h in c for h in hints)


def to_num(v) -> Optional[float]:
    """Handles Indian digit grouping, % suffix, currency symbols, dashes."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = re.sub(r"[,%\s₹()]", "", str(v))
    if s in ("", "-", "--", "nan", "NA", "N.A.", "NIL"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def classify(name: str, isin: Optional[str], quantity: Optional[float] = None) -> str:
    """Asset class from name, ISIN prefix, and shape of the row.

    Legacy disclosures often omit ISIN entirely, so a share-quantity-bearing row
    that is not cash, debt or a derivative is treated as an equity CANDIDATE.
    The silver layer confirms or overturns this once entity resolution has run —
    see pipelines/spark/silver/build_silver.py.
    """
    if CASH_RE.search(name):
        return "cash"
    if DERIV_RE.search(name):
        return "derivative"
    if DEBT_RE.search(name):
        return "debt"
    n = normalise(isin)
    if n and n.startswith("INE"):
        return "equity"
    if n and n.startswith("IN") and not n.startswith("INE"):
        return "debt"
    if quantity is not None and quantity > 0:
        return "equity"          # candidate; confirmed after resolution
    return "other"


def value_multiplier(header: str) -> float:
    """Convert declared units to rupees. Lakh = 1e5, crore = 1e7."""
    if CRORE_RE.search(header):
        return 1e7
    if LAKH_RE.search(header):
        return 1e5
    return 1.0


class BaseTabularAdapter:
    """Subclasses set amc_code/name and optionally override sniff()."""

    amc_code = "GENERIC"
    name = "generic-tabular"
    filename_hint: str = ""
    header_search_rows = 40

    # -- io ----------------------------------------------------------------
    @staticmethod
    def read_raw(path: Path, nrows: int | None = None) -> pd.DataFrame:
        if path.suffix.lower() == ".csv":
            return pd.read_csv(path, header=None, nrows=nrows, dtype=str,
                               on_bad_lines="skip", encoding_errors="replace")
        return pd.read_excel(path, header=None, nrows=nrows, dtype=str)

    # -- detection ---------------------------------------------------------
    def sniff(self, path: Path) -> float:
        if path.suffix.lower() not in (".xlsx", ".xls", ".csv"):
            return 0.0
        try:
            df = self.read_raw(path, nrows=self.header_search_rows)
        except Exception:
            return 0.0
        blob = " ".join(str(x).lower() for x in df.astype(str).values.ravel())
        score = 0.0
        if any(h in blob for h in NAME_HINTS):
            score += 0.30
        if any(h in blob for h in WEIGHT_HINTS):
            score += 0.30
        if "isin" in blob:
            score += 0.15
        if self.filename_hint and self.filename_hint in path.stem.lower():
            score += 0.40
        return min(score, 1.0)

    def find_header_rows(self, df: pd.DataFrame) -> list[int]:
        """Returns every header row — several stacked schemes means several."""
        out = []
        for i in range(min(len(df), 400)):
            row = " ".join(str(x).lower() for x in df.iloc[i].tolist())
            if any(h in row for h in NAME_HINTS) and any(h in row for h in WEIGHT_HINTS):
                out.append(i)
        return out

    def scheme_for_block(self, df: pd.DataFrame, header_idx: int,
                         default: str) -> str:
        """Scheme name is the last non-empty, non-header text row above the header."""
        for i in range(header_idx - 1, max(-1, header_idx - 8), -1):
            cells = [str(c) for c in df.iloc[i].tolist()
                     if c is not None and str(c) != "nan" and str(c).strip()]
            if len(cells) == 1 and SCHEME_RE.search(cells[0]):
                return " ".join(cells[0].split())
        return default

    # -- parsing -----------------------------------------------------------
    def parse(self, path: Path) -> Iterator[RawHolding]:
        raw = self.read_raw(path)
        headers = self.find_header_rows(raw)
        if not headers:
            raise ValueError(f"no header row found in {path.name}")

        bounds = headers + [len(raw)]
        for bi, hdr in enumerate(headers):
            cols = [str(c).strip() for c in raw.iloc[hdr].tolist()]
            block = raw.iloc[hdr + 1: bounds[bi + 1]].copy()
            block.columns = cols
            scheme = self.scheme_for_block(raw, hdr, path.stem)
            yield from self._parse_block(block, cols, scheme, path)

    def _parse_block(self, block: pd.DataFrame, cols: list[str],
                     scheme: str, path: Path) -> Iterator[RawHolding]:
        def pick(hints):
            for c in cols:
                if match_col(c, hints):
                    return c
            return None

        c_name = pick(NAME_HINTS)
        c_isin, c_qty = pick(ISIN_HINTS), pick(QTY_HINTS)
        c_val, c_wt = pick(VALUE_HINTS), pick(WEIGHT_HINTS)
        if not c_name:
            raise ValueError(f"no instrument-name column in {path.name}")
        mult = value_multiplier(c_val) if c_val else 1.0

        for _, row in block.iterrows():
            nm = row.get(c_name)
            if nm is None or (isinstance(nm, float) and pd.isna(nm)):
                continue
            nm = " ".join(str(nm).split())
            if not nm or nm.lower() == "nan":
                continue
            if TOTAL_RE.match(nm) or FOOTNOTE_RE.match(nm):
                continue

            isin_val = normalise(row.get(c_isin)) if c_isin else None
            if isin_val and not is_valid(isin_val):
                log.warning("invalid ISIN %r for %r in %s", isin_val, nm, path.name)
                isin_val = None

            wt = to_num(row.get(c_wt)) if c_wt else None
            val = to_num(row.get(c_val)) if c_val else None
            qty = to_num(row.get(c_qty)) if c_qty else None
            if wt is None and val is None:
                continue

            yield RawHolding(
                scheme_name=scheme,
                instrument_name_raw=nm,
                isin=isin_val,
                quantity=qty,
                market_value=(val * mult) if val is not None else None,
                weight_pct=wt,
                asset_class=classify(nm, isin_val, qty),
                source_file=path.name,
            )
