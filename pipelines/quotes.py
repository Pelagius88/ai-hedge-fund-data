"""The one quote pipeline.

Every consumer calls get_bars(). Nobody fetches their own copy — a second copy
of the same data is a bug.

Source: Nasdaq historical API (api.nasdaq.com). Bars land in the canonical
`bars` table with provenance; corporate actions detected during ingestion are
flagged in `corporate_actions` (adjustment itself is Stage 1 step 3).
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import subprocess as _sp
import time as _time

from store.canonical import CanonicalStore
from store.schemas import Bar, CorporateAction, Provenance

_store = CanonicalStore()

_NASDAQ_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_ASSET_CLASSES = ("stocks", "etf", "mutualfunds")
_SPLIT_RATIOS = (2.0, 3.0, 4.0, 5.0, 10.0, 20.0, 0.5, 1 / 3, 0.25, 0.2, 0.1, 0.05)


def _fetch_nasdaq(symbol: str, fromdate: str, assetclass: str, tries: int = 2) -> list[dict] | None:
    """curl (never urllib — it hangs through the proxy) with --max-time."""
    url = (
        f"https://api.nasdaq.com/api/quote/{symbol}/historical"
        f"?assetclass={assetclass}&fromdate={fromdate}&limit=1500"
    )
    for attempt in range(tries):
        try:
            out = _sp.run(
                ["curl", "-s", "--max-time", "30", "-H", f"User-Agent: {_NASDAQ_UA}",
                 "-H", "Accept: application/json", url],
                capture_output=True, text=True, timeout=45,
            )
            d = _json.loads(out.stdout or "{}")
            rows = ((d.get("data") or {}).get("tradesTable") or {}).get("rows") or []
            if rows:
                return rows
        except Exception:
            pass
        _time.sleep(1 + attempt)
    return None


def _parse_row(r: dict) -> dict | None:
    """Nasdaq row -> normalized bar fields. Returns None on unparseable."""
    try:
        m, dd, y = r["date"].split()[0].split("/")
        iso = f"{y}-{m.zfill(2)}-{dd.zfill(2)}"

        def num(v):
            return float(str(v).replace("$", "").replace(",", "").strip())

        return {
            "date": iso,
            "open": num(r["open"]),
            "high": num(r["high"]),
            "low": num(r["low"]),
            "close": num(r["close"]),
            "volume": int(str(r["volume"]).replace(",", "").strip() or 0),
        }
    except (KeyError, ValueError, AttributeError):
        return None


def fetch_bars(symbol: str, start: str | None = None, end: str | None = None) -> list[dict]:
    """Fetch daily bars from Nasdaq. Returns oldest-first list of bar dicts.

    Tries asset classes stocks -> etf -> mutualfunds. `start` bounds the
    fromdate sent to Nasdaq (YYYY-MM-DD); `end` filters the returned rows.
    """
    sym = symbol.upper()
    fromdate = start or (_dt.date.today() - _dt.timedelta(days=400)).isoformat()
    rows = None
    for cls in _ASSET_CLASSES:
        rows = _fetch_nasdaq(sym, fromdate, cls)
        if rows:
            break
        _time.sleep(0.5)
    if not rows:
        return []
    bars = [b for b in (_parse_row(r) for r in rows) if b]
    if end:
        bars = [b for b in bars if b["date"] <= end]
    return sorted(bars, key=lambda b: b["date"])


def _existing_dates(symbol: str) -> set[str]:
    recs = _store.current("bars", where={"symbol": symbol.upper()})
    return {r["date"] for r in recs}


def append_bars(symbol: str, bars: list[dict], provenance: Provenance | dict) -> list[str]:
    """Idempotent append: skips (symbol, date) pairs already in the store.

    Returns the record_ids of newly appended bars (empty list = all dupes).
    """
    sym = symbol.upper()
    if isinstance(provenance, dict):
        provenance = Provenance(**provenance)
    seen = _existing_dates(sym)
    fresh = [b for b in bars if b["date"] not in seen]
    if not fresh:
        return []
    records = [Bar(symbol=sym, provenance=provenance, **b) for b in fresh]
    return _store.append_many("bars", records)


def latest_close(symbol: str) -> dict | None:
    """Most recent bar for symbol, or None if the store has nothing."""
    recs = _store.current("bars", where={"symbol": symbol.upper()})
    if not recs:
        return None
    return max(recs, key=lambda r: r["date"])


def get_bars(symbol: str, start: str, end: str) -> list[dict]:
    """THE function every consumer calls. Daily bars in [start, end], oldest first."""
    recs = _store.current("bars", where={"symbol": symbol.upper()})
    return sorted(
        (r for r in recs if start <= r["date"] <= end),
        key=lambda r: r["date"],
    )


def detect_splits(symbol: str, bars: list[dict], provenance: Provenance | dict) -> list[str]:
    """Flag clean-ratio overnight gaps as split candidates in corporate_actions.

    Detection only — no price adjustment (that's Stage 1 step 3). Returns
    the record_ids of flagged actions.
    """
    sym = symbol.upper()
    if isinstance(provenance, dict):
        provenance = Provenance(**provenance)
    ordered = sorted(bars, key=lambda b: b["date"])
    flagged = []
    for prev, cur in zip(ordered, ordered[1:]):
        if not prev["close"]:
            continue
        ratio = cur["open"] / prev["close"]
        for target in _SPLIT_RATIOS:
            if abs(ratio - target) / target < 0.01:
                flagged.append(
                    CorporateAction(
                        symbol=sym, date=cur["date"], action="split",
                        ratio=target, provenance=provenance,
                    )
                )
                break
    if not flagged:
        return []
    return _store.append_many("corporate_actions", flagged)
