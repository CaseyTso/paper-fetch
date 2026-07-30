"""Source protocol — every source module exposes one callable with this signature."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models import PaperIdentity, SourceResult


class Source(Protocol):
    """A single acquisition source.

    The ``name`` attribute is used in attempt records and logging.
    ``fetch()`` must be synchronous and self-contained — the pipeline never
    calls it from a thread pool.
    """

    name: str

    def fetch(self, identity: PaperIdentity, destination: Path) -> SourceResult:
        ...
