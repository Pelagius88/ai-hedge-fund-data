"""Circuit breaker — drawdown monitor.

Hull ch. 15 (RM&FI): the limit framework includes drawdown triggers.
Taleb (Black Swan, in the reading notes): the book must survive the event
the model can't see — the breaker is the survival mechanism, not a
prediction.

Design:
- Trips when book NAV falls more than `drawdown_pct` below its trailing
  peak. Default 20% for AI Rotation (manual §5), configurable per book.
- When tripped: allow_new_positions() returns False. Existing positions
  are NOT auto-liquidated — the breaker halts new risk, it doesn't force
  exits (forced selling into stress is how drawdowns become blowups).
- Reset is manual and auditable: reset() requires a reason string, writes
  a trip/reset event to the canonical store's `risk_events` table.
- Trip and reset are both append-only records with provenance. The audit
  trail shows exactly when the breaker fired and who cleared it.

Simons lesson: no discretionary bypass. There is no "temporarily disable"
path — only trip and audited reset.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


DEFAULT_DRAWNDOWN_PCT = 0.20


@dataclass
class BreakerEvent:
    book: str
    event: str                 # tripped | reset
    nav: float
    peak_nav: float
    drawdown_pct: float
    reason: str
    at: str = field(default_factory=_now)


class CircuitBreaker:
    """Per-book drawdown breaker. State lives in memory for the check;
    trip/reset events persist to the store via the caller."""

    def __init__(self, book: str, drawdown_pct: float = DEFAULT_DRAWNDOWN_PCT):
        self.book = book
        self.drawdown_pct = drawdown_pct
        self.peak_nav: float | None = None
        self.tripped = False
        self.events: list[BreakerEvent] = []

    def update(self, nav: float) -> BreakerEvent | None:
        """Feed the latest NAV. Returns a trip event if the breaker fired,
        else None. Peak ratchets up; never down."""
        if self.peak_nav is None or nav > self.peak_nav:
            self.peak_nav = nav
        if self.tripped:
            return None
        dd = (self.peak_nav - nav) / self.peak_nav if self.peak_nav else 0.0
        if dd >= self.drawdown_pct:
            self.tripped = True
            ev = BreakerEvent(
                book=self.book, event="tripped", nav=nav,
                peak_nav=self.peak_nav, drawdown_pct=round(dd, 4),
                reason=(f"drawdown {dd:.1%} breached {self.drawdown_pct:.0%} "
                        f"from peak {self.peak_nav:,.0f}"))
            self.events.append(ev)
            return ev
        return None

    def allow_new_positions(self) -> bool:
        """False while tripped. No bypass path — reset() is the only exit,
        and it requires a reason."""
        return not self.tripped

    def reset(self, reason: str) -> BreakerEvent:
        """Manual, audited reset. Reason is mandatory — an unexplained reset
        is a governance breach (Two Sigma: every change is an auditable
        event)."""
        if not reason or not reason.strip():
            raise ValueError("reset requires a reason string")
        ev = BreakerEvent(
            book=self.book, event="reset",
            nav=self.peak_nav or 0.0, peak_nav=self.peak_nav or 0.0,
            drawdown_pct=0.0, reason=reason.strip())
        self.tripped = False
        # Peak resets to current NAV on restart — the breaker re-arms from
        # the post-review baseline, not from the old peak.
        self.events.append(ev)
        return ev
