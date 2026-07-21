from __future__ import annotations

from typing import Dict

from pydantic import BaseModel, Field


class PortfolioRequest(BaseModel):
    holdings: Dict[str, float] = Field(
        ..., description="scheme_code -> rupee value",
        json_schema_extra={"example": {"FX0001": 250000, "FX0002": 150000, "FX0005": 100000}})
    explain: bool = Field(False, description="Add a plain-language narrative via Groq")


class FeeDragRequest(BaseModel):
    monthly_contribution: float = 10000
    years: int = 20
    gross_return_pct: float = 12.0
    ter_a_pct: float = 0.5
    ter_b_pct: float = 1.5
