"""SEC EDGAR filing ingestion — contract.

The existing SEC sweep (declared-contact UA, full-text downloads) migrates here.
Contract: poll submissions per watched CIK, download new filings (every form —
10-K/Q, 8-K, S-1/S-4, 13D/G, Form 4), stamp Provenance, append to the canonical
store's `filings` table, archive raw docs. Idempotent on accession number.
"""
from store.schemas import Filing, Provenance

SOURCE = "sec-edgar:filings"


def poll(cik: str) -> list[Filing]:
    """New filings for one CIK since last poll. Returns Filing records."""
    raise NotImplementedError("migrate existing SEC sweep here")
