"""PDF disclosure adapter.

Smaller AMCs still publish monthly portfolios as PDF rather than Excel. Tables
are extracted with pdfplumber, then handed to the same column-mapping and
row-classification logic the tabular adapters use, so a PDF-sourced holding is
indistinguishable downstream from an Excel-sourced one.

PDF extraction is inherently lossier than Excel: merged cells, wrapped
instrument names, and multi-page tables whose header appears only on page 1.
Those are handled explicitly below.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional

import pandas as pd

from fundxray_core.identifiers.isin import is_valid, normalise
from fundxray_core.schemas import RawHolding
from fundxray_core.utils.logging import get_logger

from .base_tabular import (
    FOOTNOTE_RE,
    ISIN_HINTS,
    NAME_HINTS,
    QTY_HINTS,
    TOTAL_RE,
    VALUE_HINTS,
    WEIGHT_HINTS,
    classify,
    match_col,
    to_num,
    value_multiplier,
)
from .registry import register

log = get_logger(__name__)


class PDFAdapter:
    amc_code = "PDF"
    name = "pdf-tabular"

    def sniff(self, path: Path) -> float:
        if path.suffix.lower() != ".pdf":
            return 0.0
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                text = (pdf.pages[0].extract_text() or "").lower()
        except Exception:
            return 0.0
        score = 0.0
        if "isin" in text:
            score += 0.4
        if any(h in text for h in NAME_HINTS):
            score += 0.3
        if any(h in text for h in WEIGHT_HINTS):
            score += 0.3
        return min(score, 1.0)

    @staticmethod
    def _tables(path: Path) -> Iterator[pd.DataFrame]:
        import pdfplumber

        header: Optional[list[str]] = None
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    if not table or len(table) < 2:
                        continue
                    first = [str(c or "").strip() for c in table[0]]
                    looks_like_header = (any(match_col(c, NAME_HINTS) for c in first)
                                         and any(match_col(c, WEIGHT_HINTS) for c in first))
                    if looks_like_header:
                        header = first
                        body = table[1:]
                    elif header and len(first) == len(header):
                        # Continuation page: the header appeared only on page 1.
                        body = table
                    else:
                        continue
                    df = pd.DataFrame(body, columns=header)
                    yield df

    def parse(self, path: Path) -> Iterator[RawHolding]:
        found = False
        for df in self._tables(path):
            found = True
            cols = list(df.columns)

            def pick(hints):
                for c in cols:
                    if match_col(c, hints):
                        return c
                return None

            c_name, c_isin = pick(NAME_HINTS), pick(ISIN_HINTS)
            c_qty, c_val, c_wt = pick(QTY_HINTS), pick(VALUE_HINTS), pick(WEIGHT_HINTS)
            if not c_name:
                continue
            mult = value_multiplier(c_val) if c_val else 1.0

            for _, row in df.iterrows():
                nm = str(row.get(c_name) or "").replace("\n", " ")
                nm = " ".join(nm.split())
                if not nm or nm.lower() == "none":
                    continue
                if TOTAL_RE.match(nm) or FOOTNOTE_RE.match(nm):
                    continue

                isin_val = normalise(row.get(c_isin)) if c_isin else None
                if isin_val and not is_valid(isin_val):
                    isin_val = None
                wt = to_num(row.get(c_wt)) if c_wt else None
                val = to_num(row.get(c_val)) if c_val else None
                qty = to_num(row.get(c_qty)) if c_qty else None
                if wt is None and val is None:
                    continue

                yield RawHolding(
                    scheme_name=path.stem,
                    instrument_name_raw=nm,
                    isin=isin_val,
                    quantity=qty,
                    market_value=(val * mult) if val is not None else None,
                    weight_pct=wt,
                    asset_class=classify(nm, isin_val, qty),
                    source_file=path.name,
                )
        if not found:
            raise ValueError(f"no parseable tables found in {path.name}")


register(PDFAdapter())
