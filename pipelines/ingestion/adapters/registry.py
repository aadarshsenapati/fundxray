from __future__ import annotations

from pathlib import Path
from typing import Iterator, List

from fundxray_core.schemas import RawHolding
from fundxray_core.utils.logging import get_logger

from .base import AMCAdapter, UnrecognisedFormatError

log = get_logger(__name__)
CONFIDENCE_THRESHOLD = 0.35

_ADAPTERS: List[AMCAdapter] = []


def register(adapter: AMCAdapter) -> AMCAdapter:
    _ADAPTERS.append(adapter)
    return adapter


def resolve(path: Path) -> AMCAdapter:
    scored = sorted(((a.sniff(path), a) for a in _ADAPTERS),
                    key=lambda t: t[0], reverse=True)
    if not scored or scored[0][0] < CONFIDENCE_THRESHOLD:
        raise UnrecognisedFormatError(
            f"No adapter matched {path.name}. Best score: "
            f"{scored[0][0] if scored else 0:.2f}. Quarantining."
        )
    score, adapter = scored[0]
    log.info("%s -> %s (confidence %.2f)", path.name, adapter.name, score)
    return adapter


def parse(path: Path) -> Iterator[RawHolding]:
    yield from resolve(path).parse(path)


def registered() -> List[str]:
    return [a.name for a in _ADAPTERS]
