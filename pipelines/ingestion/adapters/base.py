"""Adapter framework.

~45 AMCs publish SEBI-mandated monthly disclosures in their own Excel/PDF
layouts that have changed over a decade. Each adapter declares how confident it
is that it can parse a given file; the registry picks the best match. An
unrecognised file is a loud failure, never a silent skip.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

from fundxray_core.schemas import RawHolding


@runtime_checkable
class AMCAdapter(Protocol):
    amc_code: str
    name: str

    def sniff(self, path: Path) -> float:
        """Confidence in [0, 1] that this adapter can parse the file."""
        ...

    def parse(self, path: Path) -> Iterator[RawHolding]:
        ...


class UnrecognisedFormatError(RuntimeError):
    """Raised when no adapter clears the confidence threshold."""
