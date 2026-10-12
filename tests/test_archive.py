"""Tests for the cold archive.

Uses synthetic filings/news with marker cik/symbol TESTARCH and a throwaway
archive dest under /tmp, so the real archive is untouched. Tombstones do land
in the real hot store (append-only), but only for TESTARCH records.
"""
import sys

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")

import gzip
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

from store.canonical import CanonicalStore
from store.schemas import Filing, NewsItem, Provenance
from store import archive as arc

_store = CanonicalStore()
_DEST = tempfile.mkdtemp(prefix="arc_test_")
_RUN = datetime.now(timezone.utc).strftime("%H%M%S%f")
_CIK = f"TESTARCH{_RUN}"      # unique per run: the store is append-only
_SYM = f"TA{_RUN[-6:]}"


def _prov():
    return Provenance(source="test-archive")


def _old_filing(i):
    d = (datetime.now(timezone.utc) - timedelta(days=800 + i)).strftime("%Y-%m-%d")
    return Filing(cik=_CIK, accession=f"TEST-{i:04d}", form="10-K",
                  filed_at=d, url=f"https://x/{i}", provenance=_prov())


def _old_news(i):
    d = (datetime.now(timezone.utc) - timedelta(days=400 + i)).isoformat()
    return NewsItem(symbol=_SYM, published_at=d, headline=f"t{i}",
                    url=f"https://x/n{i}", source="test", provenance=_prov())


def _fresh_filing():
    d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Filing(cik=_CIK, accession="TEST-FRESH", form="10-Q",
                  filed_at=d, url="https://x/fresh", provenance=_prov())


def test_archive_and_retrieve():
    ids = _store.append_many("filings", [_old_filing(i) for i in range(3)])
    r = arc.archive_old("filings", 730, dest=_DEST,
                        where={"cik": _CIK})
    assert r["archived"] == 3, r
    assert r["touched"] is True

    # originals drop out of current()
    cur_ids = {x["record_id"] for x in _store.current("filings")}
    for i in ids:
        assert i not in cur_ids, f"{i} still hot"

    # tombstones are present and point at cold files
    tombs = [x for x in _store.current("filings")
             if x.get("archived") and x.get("archived_record_id") in ids]
    assert len(tombs) == 3, len(tombs)
    assert all(t["cold_path"].endswith(".jsonl.gz") for t in tombs)

    # retrieve round-trip from cold storage
    for i in ids:
        rec = arc.retrieve(i, dest=_DEST)
        assert rec["record_id"] == i
        assert rec["cik"] == _CIK

    # cold file is actually gzip
    cold = os.path.join(_DEST, tombs[0]["cold_path"])
    with gzip.open(cold, "rt") as f:
        lines = [json.loads(l) for l in f if l.strip()]
    assert len(lines) == 3
    print("PASS test_archive_and_retrieve")


def test_hot_excludes_archived_and_tombstones():
    # fresh record stays hot; archived originals stay out; tombstones filtered
    fid = _store.append("filings", _fresh_filing())
    arc.archive_old("filings", 730, dest=_DEST, where={"cik": _CIK})
    hot = arc.hot_records("filings")
    hot_ids = {x["record_id"] for x in hot}
    assert fid in hot_ids, "fresh filing missing from hot"
    assert not any(x.get("archived") for x in hot), "tombstone leaked into hot"
    print("PASS test_hot_excludes_archived_and_tombstones")


def test_idempotent_rerun():
    r1 = arc.archive_old("filings", 730, dest=_DEST, where={"cik": _CIK})
    r2 = arc.archive_old("filings", 730, dest=_DEST, where={"cik": _CIK})
    assert r2["archived"] == 0, r2
    assert r2["touched"] is False
    print("PASS test_idempotent_rerun")


def test_dry_run_touches_nothing():
    nid = _store.append("news", _old_news(99))
    before = arc.archive_status(dest=_DEST)["news"]["cold_count"]
    r = arc.archive_old("news", 365, dest=_DEST, dry_run=True,
                        where={"symbol": _SYM})
    assert r["dry_run"] is True and r["candidates"] >= 1, r
    assert r["touched"] is False
    after = arc.archive_status(dest=_DEST)["news"]["cold_count"]
    assert before == after, "dry run wrote to cold"
    cur_ids = {x["record_id"] for x in _store.current("news")}
    assert nid in cur_ids, "dry run archived a record"
    # clean up the synthetic news record's candidacy is fine; leave it hot
    print("PASS test_dry_run_touches_nothing")


def test_bars_and_pages_never():
    for table in ("bars", "pages"):
        try:
            arc.archive_old(table, 1, dest=_DEST)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{table} was archived!")
    print("PASS test_bars_and_pages_never")


def test_retrieve_missing_raises():
    try:
        arc.retrieve("no-such-id", dest=_DEST)
    except KeyError:
        pass
    else:
        raise AssertionError("retrieve should raise KeyError")
    print("PASS test_retrieve_missing_raises")


if __name__ == "__main__":
    try:
        test_archive_and_retrieve()
        test_hot_excludes_archived_and_tombstones()
        test_idempotent_rerun()
        test_dry_run_touches_nothing()
        test_bars_and_pages_never()
        test_retrieve_missing_raises()
        print("ALL ARCHIVE TESTS PASS")
    finally:
        shutil.rmtree(_DEST, ignore_errors=True)
