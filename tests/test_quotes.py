"""Tests for the one quote pipeline. Uses a temp store root (never production)."""
import os
import sys
import tempfile

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")

import pipelines.quotes as q
from store.schemas import Provenance


def _prov():
    return Provenance(source="test", fetched_at="2026-10-11T00:00:00+00:00", supersedes=None)


def _bars():
    return [
        {"date": "2026-10-07", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1000},
        {"date": "2026-10-08", "open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0, "volume": 1100},
        {"date": "2026-10-09", "open": 102.0, "high": 104.0, "low": 101.0, "close": 103.0, "volume": 1200},
    ]


def setup_module():
    tmp = tempfile.mkdtemp(prefix="quotetest_")
    os.environ["CANONICAL_STORE_ROOT"] = tmp
    # rebind the module's store to the temp root
    from store.canonical import CanonicalStore
    q._store = CanonicalStore(root=tmp)


def test_append_and_get():
    ids = q.append_bars("TEST", _bars(), _prov())
    assert len(ids) == 3, f"expected 3, got {len(ids)}"
    got = q.get_bars("TEST", "2026-10-07", "2026-10-09")
    assert len(got) == 3
    assert got[0]["date"] == "2026-10-07" and got[-1]["date"] == "2026-10-09"
    assert got[0]["provenance"]["source"] == "test"


def test_idempotent_append():
    ids = q.append_bars("TEST", _bars(), _prov())
    assert ids == [], f"re-run should append 0, got {len(ids)}"


def test_provenance_required():
    try:
        q.append_bars("TEST2", _bars(), Provenance(source="", fetched_at="x", supersedes=None))
        assert False, "should have raised"
    except ValueError:
        pass


def test_latest_close():
    lc = q.latest_close("TEST")
    assert lc["date"] == "2026-10-09" and lc["close"] == 103.0
    assert q.latest_close("NOPE") is None


def test_split_detection():
    bars = [
        {"date": "2026-10-07", "open": 400.0, "high": 402.0, "low": 398.0, "close": 400.0, "volume": 1000},
        {"date": "2026-10-08", "open": 100.5, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 4000},
    ]
    ids = q.detect_splits("SPLIT", bars, _prov())
    assert len(ids) == 1, f"expected 1 split flag, got {len(ids)}"
    recs = q._store.current("corporate_actions", where={"symbol": "SPLIT"})
    assert recs and recs[0]["action"] == "split" and recs[0]["ratio"] == 0.25


def test_no_false_split():
    # 5% overnight move is not a split
    bars = [
        {"date": "2026-10-07", "open": 100.0, "high": 102.0, "low": 99.0, "close": 100.0, "volume": 1000},
        {"date": "2026-10-08", "open": 105.0, "high": 107.0, "low": 104.0, "close": 106.0, "volume": 2000},
    ]
    assert q.detect_splits("NOSPLIT", bars, _prov()) == []
