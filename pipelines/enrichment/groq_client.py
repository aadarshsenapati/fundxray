"""Groq LLM enrichment.

Two deliberately bounded uses. The LLM never produces a number that reaches the
user — every figure comes from deterministic computation over disclosed data.

  1. explain()      — turns computed metrics into plain-language narrative
  2. map_columns()  — fallback when the deterministic adapter cannot identify
                      columns in an unseen AMC layout; output is validated
                      against the real column list before use
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fundxray_core.config import settings
from fundxray_core.utils.logging import get_logger

log = get_logger(__name__)

EXPLAIN_SYSTEM = """You explain mutual fund portfolio analytics to Indian retail investors.

STRICT RULES:
- Use ONLY the numbers given to you. Never invent, estimate or extrapolate a figure.
- Never recommend buying, selling, switching or exiting anything.
- Never rank funds or call any fund good or bad.
- Never predict returns.
- Describe what the data shows and why the concept matters. Nothing more.
- Plain English, short sentences, no jargon without explanation.
- End with: "This is information, not investment advice."
"""


def _client():
    if not settings.groq_enabled:
        return None
    try:
        from groq import Groq
        return Groq(api_key=settings.groq_api_key)
    except Exception as e:  # pragma: no cover
        log.warning("Groq unavailable: %s", e)
        return None


def explain(metrics: dict[str, Any], focus: str = "portfolio") -> Optional[str]:
    """Narrate precomputed metrics. Returns None if Groq is not configured."""
    c = _client()
    if c is None:
        return None
    prompt = (
        f"Explain this {focus} analysis to the investor who owns it.\n\n"
        f"{json.dumps(metrics, indent=2, default=str)}"
    )
    try:
        r = c.chat.completions.create(
            model=settings.groq_model,
            messages=[{"role": "system", "content": EXPLAIN_SYSTEM},
                      {"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=700,
        )
        return r.choices[0].message.content
    except Exception as e:
        log.warning("Groq explain failed: %s", e)
        return None


def map_columns(columns: list[str], sample_rows: list[list[str]]) -> Optional[dict]:
    """Deterministic rules run first. This is the fallback for unseen layouts.

    The returned mapping is validated by the caller against `columns` — a
    hallucinated column name is discarded, not trusted.
    """
    c = _client()
    if c is None:
        return None
    prompt = (
        "This is a header row and sample rows from an Indian mutual fund "
        "monthly portfolio disclosure. Map these logical fields to the actual "
        "column names: instrument_name, isin, quantity, market_value, weight_pct.\n"
        "Return ONLY JSON. Use null when a field is absent.\n\n"
        f"Columns: {columns}\nSample rows: {sample_rows[:5]}"
    )
    try:
        r = c.chat.completions.create(
            model=settings.groq_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        mapping = json.loads(r.choices[0].message.content)
        valid = {k: v for k, v in mapping.items() if v is None or v in columns}
        dropped = set(mapping) - set(valid)
        if dropped:
            log.warning("discarded hallucinated column mappings: %s", dropped)
        return valid
    except Exception as e:
        log.warning("Groq column mapping failed: %s", e)
        return None
