"""Position limits engine.

Hull ch. 15 (RM&FI): risk management owns the limit framework — position,
sector, gross/net exposure, VaR, drawdown triggers. Limits are set by the
risk function and enforced by the engine.

Simons/Renaissance (Brown): "deliberately does not interfere with the
trading systems — no discretionary overrides." A limit breach is a breach.
There is no override mechanism in this module by design — adding one would
be the exact failure mode Renaissance institutionalized against.

Hedge-fund org model: risk is MIDDLE OFFICE. It verifies; it never proposes
trades. check_limits() takes a portfolio and returns verdicts — it never
suggests what to buy or sell.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------ types ----
@dataclass
class Position:
    symbol: str
    quantity: float
    mark: float            # current price per share
    cost_basis: float      # per-share entry price

    @property
    def market_value(self) -> float:
        return self.quantity * self.mark


@dataclass
class LimitCheck:
    limit_name: str
    passed: bool
    actual: float
    threshold: float
    detail: str


@dataclass
class LimitReport:
    book: str
    checked_at: str = field(default_factory=_now)
    checks: list[LimitCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def breaches(self) -> list[LimitCheck]:
        return [c for c in self.checks if not c.passed]


# --------------------------------------------------------------- defaults ----
# Initial calibration. Every value here is versioned in risk/governance.py —
# changing any of them requires a reason string and writes an auditable
# record (Two Sigma lesson: no silent recalibrations).
DEFAULT_LIMITS = {
    # Max single-name weight of book NAV. Humility as risk control
    # (Simons): size as if the model is wrong about this name.
    "max_position_pct": 0.12,
    # Sleeve caps from the manual: Bottleneck sleeve 20%, any sleeve 35%.
    "max_sleeve_pct": 0.35,
    "bottleneck_sleeve_pct": 0.20,
    # Gross exposure: long-only book, so gross == net, but the check
    # exists for the day the book isn't.
    "max_gross_exposure": 1.0,
    "max_net_exposure": 1.0,
    # Concentration: top-3 names can't be more than half the book.
    # (Hull ch. 15: diversification is the engine; Bernstein ch. 15
    #  notes variance falls below the average of the parts.)
    "max_top3_concentration": 0.50,
}


# ----------------------------------------------------------------- engine ----
def check_limits(positions: list[Position], book: str,
                 nav: float, sleeve_weights: dict[str, float] | None = None,
                 limits: dict | None = None) -> LimitReport:
    """Check a book's positions against every limit. Returns pass/fail per
    limit with the numbers. Never proposes trades. No override path exists
    — a breach is a breach (Renaissance rule)."""
    lim = {**DEFAULT_LIMITS, **(limits or {})}
    report = LimitReport(book=book)
    total_mv = sum(p.market_value for p in positions)

    def check(name, actual, threshold, detail, higher_is_worse=True):
        passed = actual <= threshold if higher_is_worse else actual >= threshold
        report.checks.append(LimitCheck(
            limit_name=name, passed=passed, actual=round(actual, 4),
            threshold=threshold, detail=detail))

    # 1. Single-name max
    for p in positions:
        w = p.market_value / nav if nav else 0
        check(f"max_position_pct[{p.symbol}]", w, lim["max_position_pct"],
              f"{p.symbol} is {w:.1%} of NAV")

    # 2. Sleeve caps
    for sleeve, w in (sleeve_weights or {}).items():
        cap = lim["bottleneck_sleeve_pct"] if "bottleneck" in sleeve.lower() \
            else lim["max_sleeve_pct"]
        check(f"sleeve_cap[{sleeve}]", w, cap,
              f"{sleeve} sleeve is {w:.1%} of NAV")

    # 3. Gross / net exposure
    gross = total_mv / nav if nav else 0
    check("max_gross_exposure", gross, lim["max_gross_exposure"],
          f"gross exposure {gross:.1%}")
    # Long-only: net == gross. Kept separate for the short-capable future.
    check("max_net_exposure", gross, lim["max_net_exposure"],
          f"net exposure {gross:.1%}")

    # 4. Top-3 concentration
    weights = sorted((p.market_value / nav for p in positions),
                     reverse=True) if nav else []
    top3 = sum(weights[:3])
    check("max_top3_concentration", top3, lim["max_top3_concentration"],
          f"top-3 names are {top3:.1%} of NAV")

    return report
