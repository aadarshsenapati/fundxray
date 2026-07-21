"""Canonical schema. Every adapter must produce rows in this shape."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

AssetClass = Literal["equity", "debt", "cash", "derivative", "reit", "other"]

HOLDING_COLUMNS = [
    "scheme_code", "amc_code", "disclosure_month", "isin", "company_id",
    "instrument_name_raw", "quantity", "market_value", "weight_pct",
    "asset_class", "source_file", "ingestion_run_id", "parsed_at",
]


class RawHolding(BaseModel):
    """What an adapter emits, before resolution."""
    scheme_name: str
    instrument_name_raw: str
    isin: Optional[str] = None
    quantity: Optional[Decimal] = None
    market_value: Optional[Decimal] = None
    weight_pct: Optional[Decimal] = None
    asset_class: AssetClass = "other"
    source_file: str = ""

    @field_validator("instrument_name_raw", "scheme_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return " ".join(str(v).split())


class Holding(BaseModel):
    """Silver-layer row: resolved, reconciled, provenance-carrying."""
    scheme_code: str
    amc_code: str
    disclosure_month: date
    isin: Optional[str]
    company_id: Optional[str]
    instrument_name_raw: str
    quantity: Optional[Decimal]
    market_value: Optional[Decimal]
    weight_pct: Decimal = Field(ge=-100, le=110)
    asset_class: AssetClass
    source_file: str
    ingestion_run_id: str
    parsed_at: datetime
    resolution_method: Optional[str] = None
    resolution_confidence: Optional[float] = None
