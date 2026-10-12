"""Model governance log.

Two Sigma lesson (SEC enforcement, Jan 2025): employees identified model
vulnerabilities as early as March 2019; the firm waited until August 2023
to address them and failed to supervise unauthorized model changes. Cost:
$165M repaid + $90M civil penalty. The memo's verdict: "Model risk controls
are existential, not bureaucratic. Version control, change review, and
vulnerability remediation for models deserve the same rigor as for
production software — because they ARE production software."

This module is that rigor for the risk engine:
- Every risk parameter (ATR window, ATR multiple, VaR confidence levels,
  limit thresholds, breaker drawdown, lookback windows) is versioned.
- set_parameter() REQUIRES a reason string. No reason, no change.
- Every change writes an append-only record to the store's
  `risk_parameters` table with provenance (who/when/what/why/supersedes).
- parameter_history() returns the full change log for any parameter.
- current_parameters() returns the live set.

There is no silent path. A parameter that changed without a record is a
governance breach by definition — the absence of a log entry IS the alarm.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# The versioned parameter registry. Defaults are v1; every set_parameter()
# call creates v(n+1) with a reason. Nothing here is changed in place.
DEFAULTS: dict[str, tuple] = {
    # (default_value, description)
    "atr_window": (14, "ATR lookback for stop computation (Hull ch. 18)"),
    "atr_multiple": (1.5, "Stop distance in ATRs — never a round number"),
    "var_confidences": ((0.95, 0.99), "VaR confidence levels reported"),
    "var_horizons": ((1, 10), "VaR horizons in trading days"),
    "var_lookback": (500, "Historical-simulation window, trading days"),
    "max_position_pct": (0.12, "Max single-name weight of NAV"),
    "max_sleeve_pct": (0.35, "Max any-sleeve weight of NAV"),
    "bottleneck_sleeve_pct": (0.20, "Max AI-bottleneck sleeve weight"),
    "max_top3_concentration": (0.50, "Max top-3 names weight of NAV"),
    "breaker_drawdown_pct": (0.20, "Circuit-breaker trip level"),
    "ewma_lambda": (0.94, "EWMA decay for volatility updating (RiskMetrics)"),
}


@dataclass
class ParameterChange:
    name: str
    version: int
    old_value: object
    new_value: object
    reason: str
    changed_by: str
    changed_at: str = field(default_factory=_now)
    record_id: str = ""


class GovernanceLog:
    """In-memory registry + change log. The caller persists ParameterChange
    records to the store's risk_parameters table (append-only)."""

    def __init__(self, changed_by: str = "mica"):
        self.changed_by = changed_by
        self._params: dict[str, object] = {
            k: v[0] for k, v in DEFAULTS.items()}
        self._history: dict[str, list[ParameterChange]] = {k: [] for k in DEFAULTS}
        # v1 baseline entries
        for k, (default, desc) in DEFAULTS.items():
            self._history[k].append(ParameterChange(
                name=k, version=1, old_value=None, new_value=default,
                reason=f"initial calibration: {desc}",
                changed_by="system"))

    def get(self, name: str):
        if name not in self._params:
            raise KeyError(f"unknown risk parameter: {name}")
        return self._params[name]

    def current_parameters(self) -> dict[str, object]:
        return dict(self._params)

    def set_parameter(self, name: str, value, reason: str) -> ParameterChange:
        """Change a risk parameter. Reason is mandatory — this is the
        Two Sigma rule: an unexplained model change is how $90M fines
        happen."""
        if name not in self._params:
            raise KeyError(f"unknown risk parameter: {name}")
        if not reason or not reason.strip():
            raise ValueError(
                f"refusing silent change to {name}: reason required")
        old = self._params[name]
        version = len(self._history[name]) + 1
        change = ParameterChange(
            name=name, version=version, old_value=old, new_value=value,
            reason=reason.strip(), changed_by=self.changed_by)
        self._params[name] = value
        self._history[name].append(change)
        return change

    def parameter_history(self, name: str) -> list[ParameterChange]:
        if name not in self._history:
            raise KeyError(f"unknown risk parameter: {name}")
        return list(self._history[name])

    def full_audit_log(self) -> list[ParameterChange]:
        """Every change to every parameter, chronological."""
        all_changes = [c for hist in self._history.values() for c in hist]
        return sorted(all_changes, key=lambda c: (c.changed_at, c.name))
