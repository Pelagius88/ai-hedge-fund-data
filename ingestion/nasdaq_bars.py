"""Nasdaq bar ingestion — contract.

The existing Nasdaq sweep (per-date curl fetches into JSONL) migrates here.
Contract: fetch daily bars per symbol, stamp Provenance, append to the
canonical store's `bars` table. Idempotent: re-running a date never duplicates.
"""
from store.canonical import CanonicalStore
from store.schemas import Bar, Provenance

SOURCE = "nasdaq:bars"


def ingest(symbol: str, date: str) -> int:
    """Fetch one symbol/date of bars into the canonical store. Returns rows written."""
    raise NotImplementedError("migrate existing Nasdaq sweep here")


def backfill(symbol: str, start: str, end: str) -> int:
    """Fill gaps over a date range. Returns rows written."""
    raise NotImplementedError("migrate existing Nasdaq sweep here")
