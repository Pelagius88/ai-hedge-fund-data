"""Canonical schemas. Every record carries provenance: where it came from,
when it was fetched, what it supersedes. Append-only: records are never
rewritten, only superseded by newer records pointing at them."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rid() -> str:
    return uuid4().hex


@dataclass
class Provenance:
    source: str                      # e.g. "sec-edgar:13F-HR:0001067983"
    fetched_at: str = field(default_factory=_now)
    supersedes: str | None = None    # record_id of the record this replaces


# ---------------------------------------------------------------- bars ----
@dataclass
class Bar:
    symbol: str
    date: str                        # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: int
    interval: str = "1d"             # 1d | 1h | 15m ...
    timestamp: str | None = None     # ISO-8601, for intraday bars
    adjusted_close: float | None = None
    record_id: str = field(default_factory=_rid)
    provenance: Provenance | None = None


# ---------------------------------------------------- corporate actions ----
@dataclass
class CorporateAction:
    symbol: str
    date: str                        # YYYY-MM-DD
    action: str                      # split | dividend | merger | spinoff
    ratio: float | None = None       # e.g. 4.0 for a 4:1 split
    amount: float | None = None      # e.g. dividend dollars per share
    status: str = "detected"         # detected | confirmed | rejected
    record_id: str = field(default_factory=_rid)
    provenance: Provenance | None = None


# --------------------------------------------------------------- filings ----
@dataclass
class Filing:
    cik: str
    accession: str
    form: str                        # 10-K | 10-Q | 8-K | 13F-HR | 4 | ...
    filed_at: str                    # YYYY-MM-DD
    url: str
    local_path: str | None = None    # pointer to raw doc in cold storage
    delta_summary: str | None = None # what changed vs prior filing, if computed
    record_id: str = field(default_factory=_rid)
    provenance: Provenance | None = None


# ------------------------------------------------------------------ news ----
@dataclass
class NewsItem:
    symbol: str
    published_at: str                # ISO-8601
    headline: str
    url: str
    source: str                      # masthead, e.g. "reuters"
    body_path: str | None = None     # pointer to full text in cold storage
    record_id: str = field(default_factory=_rid)
    provenance: Provenance | None = None


# ------------------------------------------------------------------- 13F ----
@dataclass
class ThirteenFHolding:
    filer_cik: str
    filer_name: str
    quarter: str                     # YYYY-Qn
    issuer: str
    cusip: str
    value_usd: int                   # as reported in the 13F XML
    shares: int
    put_call: str | None = None      # Put | Call | None (long common)
    record_id: str = field(default_factory=_rid)
    provenance: Provenance | None = None


@dataclass
class ThirteenFMove:
    filer_cik: str
    filer_name: str
    quarter: str                     # YYYY-Qn
    report_date: str                 # YYYY-MM-DD
    cusip: str
    issuer: str
    ticker: str | None
    title_of_class: str | None
    kind: str                        # INITIATED | ADD | EXITED | TRIM
    q1_shares: int = 0
    q2_shares: int = 0
    q1_value: int = 0
    q2_value: int = 0
    delta_value: int = 0
    tags: list[str] = field(default_factory=list)
    record_id: str = field(default_factory=_rid)
    provenance: Provenance | None = None


# -------------------------------------------------------- unusual whales ----
@dataclass
class UnusualWhalesFlow:
    symbol: str
    timestamp: str                   # ISO-8601
    flow_type: str                   # sweep | block | unusual_activity | dark_pool
    premium_usd: float | None = None
    strike: float | None = None
    expiry: str | None = None        # YYYY-MM-DD
    direction: str | None = None     # call | put
    source_detail: str | None = None # e.g. "ask-side sweep"
    record_id: str = field(default_factory=_rid)
    provenance: Provenance | None = None


# ---------------------------------------------------------- triage flags ----
@dataclass
class TriageFlag:
    symbol: str
    rank: int                        # 1 = most urgent
    flag: str                        # one-line flag text
    aggregate: str                   # up to 10-line aggregate
    agent: str                       # e.g. "grok-news-triage"
    sweep_id: str | None = None
    record_id: str = field(default_factory=_rid)
    provenance: Provenance | None = None


# --------------------------------------------------------- symbol master ----
@dataclass
class SymbolMaster:
    symbol: str
    name: str
    cik: str | None = None
    cusip: str | None = None
    gics_l1: str | None = None
    gics_l2: str | None = None
    gics_l3: str | None = None
    gics_l4: str | None = None
    record_id: str = field(default_factory=_rid)
    provenance: Provenance | None = None


# ------------------------------------------------------------------ books ----
@dataclass
class PaperPosition:
    book: str                        # ai_rotation
    symbol: str
    asof: str
    qty: float
    avg_cost: float
    record_id: str = field(default_factory=_rid)
    provenance: Provenance | None = None


# ------------------------------------------------------------------ pages ----
@dataclass
class Page:
    feed: str                        # feed name, e.g. "nasdaq-bars"
    severity: str                    # warning | critical
    message: str
    acknowledged: bool = False       # ack via supersede, never in place
    record_id: str = field(default_factory=_rid)
    provenance: Provenance | None = None


# Every table the store knows about.
TABLES = (
    "bars",
    "corporate_actions",
    "filings",
    "news",
    "thirteen_f_holdings",
    "thirteen_f_moves",
    "unusual_whales_flow",
    "triage_flags",
    "symbol_master",
    "paper_positions",
    "pages",
)
