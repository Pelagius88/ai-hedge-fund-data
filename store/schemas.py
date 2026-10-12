"""Canonical schemas. Every record carries provenance: where it came from,
when it was fetched, what it supersedes."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Provenance:
    source: str                      # e.g. "sec-edgar:13F-HR:0001067983"
    fetched_at: str = field(default_factory=_now)
    supersedes: str | None = None    # id of the record this replaces, if any


@dataclass
class Bar:
    symbol: str
    date: str                        # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: int
    adjusted_close: float | None = None
    provenance: Provenance | None = None


@dataclass
class CorporateAction:
    symbol: str
    date: str                        # YYYY-MM-DD
    action: str                      # split | dividend | merger | spinoff
    ratio: float | None = None
    amount: float | None = None
    provenance: Provenance | None = None


@dataclass
class Filing:
    cik: str
    accession: str
    form: str
    filed_at: str
    url: str
    local_path: str | None = None
    provenance: Provenance | None = None


@dataclass
class ThirteenFHolding:
    filer_cik: str
    filer_name: str
    quarter: str                     # YYYY-Qn
    issuer: str
    cusip: str
    value_usd: int                   # as reported in the 13F XML (dollars)
    shares: int
    provenance: Provenance | None = None


@dataclass
class NewsItem:
    symbol: str
    published_at: str
    headline: str
    url: str
    source: str
    provenance: Provenance | None = None


@dataclass
class PaperPosition:
    book: str                        # ai_rotation
    symbol: str
    asof: str
    qty: float
    avg_cost: float
    provenance: Provenance | None = None
