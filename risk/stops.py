"""Stop engine.

Hull ch. 18 (derivatives 8th ed notes): fixed/tight stop-losses whipsaw —
stops must be volatility-scaled or they're just noise-trading. The book
standard, encoded here and never reinvented:

    stop = max(cost basis, peak close - 1.5 x ATR(14))

- The 1.5x ATR(14) trailing component: the stop ratchets up with the peak
  but breathes with the name's own volatility. (Hull: "set them outside
  the noise (ATR-scaled) or don't set them.")
- The cost-basis floor: Chris's hard rule — "I'm not taking a loss when I
  was up 13%." A winner never becomes a loser.
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


ATR_WINDOW = 14
ATR_MULTIPLE = 1.5


@dataclass
class StopLevel:
    symbol: str
    stop_price: float
    cost_basis: float
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


def compute_stop(symbol: str, cost_basis: float, bars: list[dict]) -> StopLevel:
    """The book-standard stop. Peak = highest close in the available bars."""
    a = atr(bars)
    peak = max(b["close"] for b in bars)
    trailing = peak - ATR_MULTIPLE * a
    stop = max(cost_basis, trailing)
    return StopLevel(symbol=symbol, stop_price=round(stop, 2),
                     cost_basis=cost_basis,
                     peak_close=round(peak, 2), atr=round(a, 4))


def check_stops(levels: list[StopLevel], marks: dict[str, float]) -> list[StopFlag]:
    """Compare live marks against computed stops. Returns flags — the engine
    never acts on them. A broken stop means: flag immediately, propose to
    Chris, he decides (Founders). For AI Rotation: the PM (Mica) decides,
    recorded in the action log."""
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
