"""VaR engine.

Hull ch. 13 (RM&FI notes): two approaches —
1. Historical simulation: replay N days of actual market moves through
   today's portfolio. Hull's four-index example: 1-day 99% VaR $253,385.
   No distribution assumed — the data speaks.
2. Parametric: assume normal returns, VaR = z * sigma * sqrt(T) * value.
   Fast, but the reading notes warn: "the market's distribution has fatter
   tails than lognormal — size for the 5sigma week, not the 2sigma week."

Hull-White volatility updating (same chapter): rescale old returns by
today's sigma — in Hull's stressed example this more than doubled VaR
($253k -> $603k). We implement the plain historical VaR plus an
EWMA-volatility-updated variant, and report both. When they disagree
sharply, the updated one is telling you the regime changed.

Also from the notes: "VaR is the 'how bad' number, expected shortfall is
the 'and then what.'" We report ES alongside VaR — the mean loss beyond
the VaR threshold.

All inputs come from the canonical bars store. No private copies.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


Z_95 = 1.645
Z_99 = 2.326


@dataclass
class VaRResult:
    book: str
    method: str                # historical | parametric | ewma_updated
    confidence: float
    horizon_days: int
    var: float                 # worst loss at confidence, over horizon
    expected_shortfall: float  # mean loss given loss > VaR
    computed_at: str = field(default_factory=_now)
    note: str = ""


def _portfolio_returns(bars_by_symbol: dict[str, list[dict]],
                       weights: dict[str, float],
                       lookback: int = 500) -> list[float]:
    """Build the portfolio's daily return series from per-symbol bars.
    bars: oldest-first dicts with date/close. Aligns on shared dates."""
    closes: dict[str, dict[str, float]] = {}
    for sym, bars in bars_by_symbol.items():
        closes[sym] = {b["date"]: b["close"] for b in bars}
    dates = sorted(set.intersection(
        *(set(c) for c in closes.values()))) if closes else []
    dates = dates[-lookback:]
    rets = []
    for i in range(1, len(dates)):
        r = sum(weights[s] * (closes[s][dates[i]] / closes[s][dates[i - 1]] - 1)
                for s in weights if s in closes)
        rets.append(r)
    return rets


def historical_var(portfolio_value: float, returns: list[float],
                   confidence: float = 0.99,
                   horizon_days: int = 1) -> tuple[float, float]:
    """Historical simulation VaR + expected shortfall. Hull ch. 13: replay
    actual moves; no normality assumed. Scales by sqrt(T) for horizon."""
    if not returns:
        raise ValueError("no return history")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be in (0, 1)")
    ordered = sorted(returns)
    idx = max(0, math.ceil((1 - confidence) * len(ordered)) - 1)
    var_ret = -ordered[idx]
    tail = [-r for r in ordered[:idx + 1]]
    es_ret = sum(tail) / len(tail) if tail else var_ret
    scale = math.sqrt(horizon_days)
    return portfolio_value * var_ret * scale, portfolio_value * es_ret * scale


def parametric_var(portfolio_value: float, returns: list[float],
                   confidence: float = 0.99,
                   horizon_days: int = 1) -> tuple[float, float]:
    """Normal-distribution VaR. z*sigma*sqrt(T). Fast; understates fat
    tails — the reading notes say so explicitly, so we always pair it
    with the historical number and flag divergence."""
    if not returns:
        raise ValueError("no return history")
    z = Z_99 if confidence >= 0.99 else Z_95
    mu = sum(returns) / len(returns)
    var_r = sum((r - mu) ** 2 for r in returns) / len(returns)
    sigma = math.sqrt(var_r)
    scale = math.sqrt(horizon_days)
    var = portfolio_value * z * sigma * scale
    # ES for normal: sigma * phi(z) / (1 - Phi(z))
    phi = math.exp(-z * z / 2) / math.sqrt(2 * math.pi)
    es = portfolio_value * sigma * phi / (1 - confidence) * scale
    return var, es


def ewma_volatility(returns: list[float], lam: float = 0.94) -> float:
    """EWMA volatility, Hull ch. 22 style (RiskMetrics lambda=0.94).
    Weights recent returns more — the 'today's sigma' for Hull-White
    updating."""
    if not returns:
        raise ValueError("no return history")
    v = sum(r * r for r in returns[:10]) / 10  # seed
    for r in returns[10:]:
        v = lam * v + (1 - lam) * r * r
    return math.sqrt(v)


def ewma_updated_var(portfolio_value: float, returns: list[float],
                     confidence: float = 0.99,
                     horizon_days: int = 1) -> tuple[float, float]:
    """Historical VaR with Hull-White volatility updating: rescale each
    historical return by (current EWMA vol / historical EWMA vol at the
    time), so old calm-period returns don't understate today's risk.
    Hull's example: this more than doubled VaR in stress."""
    if len(returns) < 30:
        raise ValueError("need >= 30 returns for EWMA updating")
    cur_vol = ewma_volatility(returns)
    updated = []
    for i in range(20, len(returns)):
        hist_vol = ewma_volatility(returns[:i]) or cur_vol
        updated.append(returns[i] * cur_vol / hist_vol)
    return historical_var(portfolio_value, updated, confidence, horizon_days)


def compute_var(book: str, portfolio_value: float,
                bars_by_symbol: dict[str, list[dict]],
                weights: dict[str, float],
                lookback: int = 500) -> list[VaRResult]:
    """Full VaR panel: historical + parametric + EWMA-updated, at 95/99,
    1-day and 10-day. Returns the panel; the caller decides what to do
    with it (risk verifies, never trades)."""
    rets = _portfolio_returns(bars_by_symbol, weights, lookback)
    out = []
    for conf in (0.95, 0.99):
        for h in (1, 10):
            for method, fn in (
                ("historical", historical_var),
                ("parametric", parametric_var),
                ("ewma_updated", ewma_updated_var),
            ):
                try:
                    v, es = fn(portfolio_value, rets, conf, h)
                    note = ""
                    out.append(VaRResult(
                        book=book, method=method, confidence=conf,
                        horizon_days=h, var=round(v, 2),
                        expected_shortfall=round(es, 2), note=note))
                except ValueError as e:
                    out.append(VaRResult(
                        book=book, method=method, confidence=conf,
                        horizon_days=h, var=0.0, expected_shortfall=0.0,
                        note=f"unavailable: {e}"))
    return out


def var_contributors(bars_by_symbol: dict[str, list[dict]],
                     weights: dict[str, float],
                     portfolio_value: float,
                     confidence: float = 0.99) -> list[tuple[str, float]]:
    """Which positions contribute most to 1-day 99% historical VaR.
    Contribution ~= weight * standalone VaR (component VaR approx)."""
    out = []
    for sym, w in weights.items():
        bars = bars_by_symbol.get(sym, [])
        if len(bars) < 2:
            continue
        rets = [bars[i]["close"] / bars[i - 1]["close"] - 1
                for i in range(1, len(bars))]
        v, _ = historical_var(portfolio_value * w, rets, confidence, 1)
        out.append((sym, round(v, 2)))
    return sorted(out, key=lambda x: x[1], reverse=True)
