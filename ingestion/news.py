"""News ingestion — contract.

The existing 15-min news crawl (claim-then-process cursors) migrates here.
Contract: per-symbol timestamped items, stamp Provenance, append to the
canonical store's `news` table. Overlapping runs never double-cover.
"""
from store.schemas import NewsItem, Provenance

SOURCE = "news:crawl"


def crawl(symbols: list[str]) -> int:
    """One crawl pass over symbols. Returns items written."""
    raise NotImplementedError("migrate existing news crawl here")
