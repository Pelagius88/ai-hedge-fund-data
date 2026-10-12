"""The one quote pipeline.

Every consumer calls get_bars(). Nobody fetches their own copy — a second copy
of the same data is a bug.
"""
from __future__ import annotations
from store.canonical import CanonicalStore

_store = CanonicalStore()


def get_bars(symbol: str, start: str, end: str) -> list[dict]:
    """Daily bars for symbol in [start, end] (YYYY-MM-DD), oldest first."""
    recs = _store.read("bars", where={"symbol": symbol.upper()})
    return sorted(
        (r for r in recs if start <= r["date"] <= end),
        key=lambda r: r["date"],
    )


def latest_close(symbol: str) -> dict | None:
    """Most recent bar for symbol, or None if the store has nothing."""
    recs = _store.read("bars", where={"symbol": symbol.upper()})
    if not recs:
        return None
    return max(recs, key=lambda r: r["date"])
