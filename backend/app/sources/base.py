"""Source-adapter interface.

Every data source (BSE today; NSE / paid APIs later) implements `Source.fetch()`
and returns a list of `RawAnnouncementDTO`. Downstream ingestion is source-agnostic,
so new sources can be added without touching the pipeline.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class RawAnnouncementDTO:
    source: str
    headline: str
    external_id: str | None = None
    bse_scrip_code: str | None = None
    body: str | None = None
    category: str | None = None
    subcategory: str | None = None
    attachment_url: str | None = None
    announced_at: dt.datetime | None = None
    raw_json: dict = field(default_factory=dict)

    def content_hash(self) -> str:
        """Stable hash for dedup. Prefers source+external_id, falls back to content."""
        if self.external_id:
            basis = f"{self.source}:{self.external_id}"
        else:
            ts = self.announced_at.isoformat() if self.announced_at else ""
            basis = f"{self.source}:{self.bse_scrip_code}:{self.headline}:{ts}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()


class Source(ABC):
    name: str = "base"

    @abstractmethod
    def fetch(self, since: dt.datetime | None = None) -> list[RawAnnouncementDTO]:
        """Return announcements published at or after `since` (best-effort)."""
        raise NotImplementedError
