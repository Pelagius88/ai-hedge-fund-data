"""Stop engine.

Hull ch. 18: fixed/tight stop-losses whipsaw — stops must be volatility-scaled
or they're just noise-trading. This module computes ATR-trailing stops:

    stop = peak close - multiple x ATR(window)

Design notes:
- ATR window 14: Wilder's original specification, Hull-standard. Methodology,
  not a tuned parameter.
- ATR multiple 2.0: Turtle Traders' 2N stop (Covel, "Trend Following").
  Literature default, explicitly pending calibration against the book's own
  history. Change it through the governance log with a reason and evidence —
  never by preference.
- NO cost-basis floor. A trailing stop is a trailing stop. Whether a winner
  may become a loser is a PM decision at flag time, not an engine rule.
- Bernstein ch. 16: loss aversion is the enemy precommitted rules defeat —
  the stop forces the sale the ego refuses.

Stops are FLAGS, never orders. check_stops() outputs flags. Nothing in this
module places, previews, queues, or touches an order. That boundary is
compliance, not an ops rule.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


ATR_WINDOW = 14          # Wilder's specification (Hull ch. 18)
ATR_MULTIPLE = 2.0       # Turtle Traders 2N; LITERATURE DEFAULT — pending calibration


@dataclass
class StopLevel:
    symbol: str
    stop_price: float
    peak_close: float
    atr: float
    computed_at: str = field(default_factory=_now)


@dataclass
class StopFlag:
    symbol: str
    broken: bool
    mark: float
    stop_price: float
    distance_atr: float      # (mark - stop) / atr; negative = broken
    detail: str


def atr(bars: list[dict], window: int = ATR_WINDOW) -> float:
    """Average True Range over the last `window` bars. Wilder's smoothing.
    bars: dicts with high/low/close, oldest first."""
    if len(bars) < window + 1:
        raise ValueError(f"need {window + 1} bars for ATR({window})")
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    # Wilder's: seed with SMA, then smooth
    a = sum(trs[:window]) / window
    for tr in trs[window:]:
        a = (a * (window - 1) + tr) / window
    return a


def compute_stop(symbol: str, bars: list[dict],
                 multiple: float = ATR_MULTIPLE) -> StopLevel:
    """ATR-trailing stop. Peak = highest close in the available bars.
    The multiple is a governance-logged parameter, not a constant."""
    a = atr(bars)
    peak = max(b["close"] for b in bars)
    stop = peak - multiple * a
    return StopLevel(symbol=symbol, stop_price=round(stop, 2),
                     peak_close=round(peak, 2), atr=round(a, 4))


def check_stops(levels: list[StopLevel], marks: dict[str, float]) -> list[StopFlag]:
    """Compare live marks against computed stops. Returns flags — the engine
    never acts on them. A broken stop means: flag immediately with the facts.
    What happens next is a PM decision, recorded in the action log."""
    flags = []
    for lvl in levels:
        mark = marks.get(lvl.symbol)
        if mark is None:
            continue
        dist = (mark - lvl.stop_price) / lvl.atr if lvl.atr else 0.0
        broken = mark < lvl.stop_price
        flags.append(StopFlag(
            symbol=lvl.symbol, broken=broken, mark=mark,
            stop_price=lvl.stop_price, distance_atr=round(dist, 2),
            detail=(f"{lvl.symbol} STOP BROKEN: {mark} < {lvl.stop_price}"
                    if broken else
                    f"{lvl.symbol} ok: {dist:.1f} ATR above stop")))
    return flags
