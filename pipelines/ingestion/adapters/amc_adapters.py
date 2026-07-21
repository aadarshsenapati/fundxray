"""Per-AMC adapters.

Each subclass documents the specific structural quirk that makes the AMC
different. Adding a new AMC is a subclass plus a fixture plus a golden test —
that is the whole extension path.
"""
from __future__ import annotations

from pathlib import Path

from .base_tabular import BaseTabularAdapter
from .registry import register


class BluewaterAdapter(BaseTabularAdapter):
    """Preamble rows above a clean single-scheme table; values in lakhs."""
    amc_code, name, filename_hint = "BLUEWATER", "bluewater", "bluewater"


class NorthstarAdapter(BaseTabularAdapter):
    """Several schemes stacked in one sheet, each preceded by its name row."""
    amc_code, name, filename_hint = "NORTHSTAR", "northstar", "northstar"


class MeridianAdapter(BaseTabularAdapter):
    """Percentages as text with '%' suffix; Indian digit grouping; footnotes."""
    amc_code, name, filename_hint = "MERIDIAN", "meridian", "meridian"


class KaveriAdapter(BaseTabularAdapter):
    """Header buried at row ~12; quantity named 'No. of Shares'; crore units."""
    amc_code, name, filename_hint = "KAVERI", "kaveri", "kaveri"
    header_search_rows = 60


class SentinelAdapter(BaseTabularAdapter):
    """Legacy layout with NO ISIN column — forces name-based entity resolution."""
    amc_code, name, filename_hint = "SENTINEL", "sentinel", "sentinel"


class ZenithAdapter(BaseTabularAdapter):
    """CSV export, reordered columns, blank ISINs, inconsistent name casing."""
    amc_code, name, filename_hint = "ZENITH", "zenith", "zenith"


class GenericTabularAdapter(BaseTabularAdapter):
    """Fallback for unseen AMCs. Scores lower than any named adapter, so a
    specific adapter always wins when one matches."""
    amc_code, name = "GENERIC", "generic-tabular"

    def sniff(self, path: Path) -> float:
        return min(super().sniff(path), 0.55)


for _a in (BluewaterAdapter(), NorthstarAdapter(), MeridianAdapter(),
           KaveriAdapter(), SentinelAdapter(), ZenithAdapter(),
           GenericTabularAdapter()):
    register(_a)
