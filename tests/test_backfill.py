"""Tests for backfill discipline. Temp store only, never production."""
import json
import os
import sys
import tempfile

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")

_tmp = tempfile.mkdtemp(prefix="backfilltest_")
os.environ["FUND_DATA_ROOT"] = _tmp

import pipelines.backfill as bf
import pipelines.quotes as q
from store.canonical import CanonicalStore
from store.schemas import Bar, Provenance

bf._store = CanonicalStore(root=_tmp)
q._store = CanonicalStore(root=_tmp)
bf.STATE_PATH = os.path.join(_tmp, "backfill_state.json")
bf.LOG_PATH = os.path.join(_tmp, "backfill_log.jsonl")


def _prov():
    return Provenance(source="nasdaq-bars")


def _mkbar(date):
    return {"date": date, "open": 10.0, "high": 11.0, "low": 9.0,
            "close": 10.5, "volume": 100}


def _seed_holey():
    # Mon 2026-10-05 .. Fri 2026-10-09, with Wed 2026-10-07 missing.
    # (2026-10-11 is a Sunday; no holidays in this window.)
    for d in ["2026-10-05", "2026-10-06", "2026-10-08", "2026-10-09"]:
        bf._store.append("bars", Bar(symbol="HOLE", provenance=_prov(),
                                     **_mkbar(d)))


def test_nyse_calendar():
    # Good Friday 2026: Easter Sun Apr 5 -> Fri Apr 3
    assert not bf.is_trading_day(bf._dt.date(2026, 4, 3))
    # Sat/Sun never trade
    assert not bf.is_trading_day(bf._dt.date(2026, 10, 10))
    assert not bf.is_trading_day(bf._dt.date(2026, 10, 11))
    # Regular weekday trades
    assert bf.is_trading_day(bf._dt.date(2026, 10, 7))
    # July 4 2026 is Saturday -> observed Friday Jul 3
    assert not bf.is_trading_day(bf._dt.date(2026, 7, 3))
    # Juneteenth observed (2022+)
    assert not bf.is_trading_day(bf._dt.date(2026, 6, 19))
    # Christmas 2026 is Friday
    assert not bf.is_trading_day(bf._dt.date(2026, 12, 25))


def test_find_gaps_detects_hole():
    _seed_holey()
    gaps = bf.find_gaps("HOLE", start="2026-10-05", end="2026-10-09")
    assert len(gaps) == 1, gaps
    assert gaps[0]["start"] == "2026-10-07"
    assert gaps[0]["end"] == "2026-10-07"
    assert gaps[0]["days"] == 1


def test_backfill_fills_hole(monkeypatch):
    def fake_fetch(symbol, start=None, end=None):
        assert symbol == "HOLE"
        return [_mkbar("2026-10-07")] if start <= "2026-10-07" <= end else []
    monkeypatch.setattr(bf, "fetch_bars", fake_fetch)
    rep = bf.backfill("HOLE")
    assert rep["gaps_found"] == 1, rep
    assert rep["bars_added"] == 1, rep
    assert rep["gaps_remaining"] == 0, rep
    got = q.get_bars("HOLE", "2026-10-05", "2026-10-09")
    assert len(got) == 5


def test_backfill_idempotent(monkeypatch):
    calls = []
    def fake_fetch(symbol, start=None, end=None):
        calls.append((start, end))
        return []
    monkeypatch.setattr(bf, "fetch_bars", fake_fetch)
    rep = bf.backfill("HOLE")
    assert rep["gaps_found"] == 0
    assert calls == []  # no gaps -> no network at all


def test_backfill_all_resumable(monkeypatch):
    def fake_fetch(symbol, start=None, end=None):
        return []
    monkeypatch.setattr(bf, "fetch_bars", fake_fetch)
    s1 = bf.backfill_all(resume=True, symbols=["HOLE"])
    assert s1["checked"] == 1
    assert os.path.exists(bf.STATE_PATH)
    state = json.load(open(bf.STATE_PATH))
    assert "HOLE" in state["completed"]
    # second run: already done, skipped
    s2 = bf.backfill_all(resume=True, symbols=["HOLE"])
    assert s2["checked"] == 0
