"""Tests for staleness monitoring + paging. Temp store only, never production."""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")

import monitors.staleness as st
from store.canonical import CanonicalStore
from store.schemas import Bar, Provenance


@pytest.fixture(autouse=True)
def fresh_store():
    tmp = tempfile.mkdtemp(prefix="staletest_")
    st._store = CanonicalStore(root=tmp)
    yield


def _now():
    return datetime.now(timezone.utc)


def _bar(symbol, fetched_at):
    return Bar(symbol=symbol, date="2026-10-09", open=1.0, high=1.0,
               low=1.0, close=1.0, volume=10,
               provenance=Provenance(source="nasdaq-bars",
                                     fetched_at=fetched_at.isoformat()))


def test_fresh_feed_ok():
    st._store.append("bars", _bar("AAA", _now()))
    r = st.check_feed("nasdaq-bars", 24)
    assert r["status"] == "ok", r
    assert r["age_hours"] < 1


def test_stale_feed_critical():
    st._store.append("bars", _bar("BBB", _now() - timedelta(hours=30)))
    r = st.check_feed("nasdaq-bars", 24)
    assert r["status"] == "critical", r
    assert r["age_hours"] >= 30


def test_warning_band():
    # 20h old against a 24h contract: past 75% (18h), under 100% -> warning
    st._store.append("bars", _bar("CCC", _now() - timedelta(hours=20)))
    r = st.check_feed("nasdaq-bars", 24)
    assert r["status"] == "warning", r


def test_no_data_feed():
    r = st.check_feed("news", 1)
    assert r["status"] == "no_data", r


def test_unknown_feed():
    r = st.check_feed("nope", 24)
    assert r["status"] == "unknown_feed"


def _critical_check(feed="nasdaq-bars"):
    return {"feed": feed, "status": "critical", "latest": "x",
            "age_hours": 99, "max_age_hours": 0.001}


def test_page_created_and_deduped(capsys):
    paged = st.page_on_stale([_critical_check()])
    assert len(paged) == 1 and paged[0]["page"] != "suppressed_duplicate"
    assert "PAGE critical nasdaq-bars" in capsys.readouterr().out
    # page record landed in the store, unacknowledged
    assert len(st.open_pages()) == 1
    # second call: duplicate suppressed, no new page record
    before = len(st._store.current("pages"))
    paged2 = st.page_on_stale([_critical_check()])
    assert paged2[0]["page"] == "suppressed_duplicate"
    assert len(st._store.current("pages")) == before


def test_acknowledge():
    st.page_on_stale([_critical_check()])
    pid = st.open_pages()[0]["record_id"]
    new_id = st.acknowledge(pid)
    assert new_id != pid
    assert pid not in {p["record_id"] for p in st.open_pages()}
    # the ack is a supersede: history keeps both, current shows the ack
    cur = {r["record_id"]: r for r in st._store.current("pages")}
    assert cur[new_id]["acknowledged"] is True
    assert cur[new_id]["provenance"]["supersedes"] == pid
    # double-ack raises ValueError
    with pytest.raises(ValueError):
        st.acknowledge(pid)
    # acking the ack record itself also raises ValueError
    with pytest.raises(ValueError):
        st.acknowledge(new_id)
    # unknown page raises KeyError
    with pytest.raises(KeyError):
        st.acknowledge("deadbeef")
    # after ack, a new critical check pages again (no open page to suppress)
    paged = st.page_on_stale([_critical_check()])
    assert paged[0]["page"] != "suppressed_duplicate"


def test_no_page_when_nothing_critical(capsys):
    assert st.page_on_stale([{"feed": "news", "status": "no_data"}]) == []
    assert st.page_on_stale([{"feed": "nasdaq-bars", "status": "ok"}]) == []
    assert "PAGE" not in capsys.readouterr().out
