"""Round-trip tests for every canonical schema: write -> read -> verify,
including provenance integrity and the supersede operation."""
import os
import tempfile

from store.canonical import CanonicalStore
from store.schemas import (
    Bar, CorporateAction, Filing, NewsItem, PaperPosition, Provenance,
    SymbolMaster, ThirteenFHolding, ThirteenFMove, TriageFlag,
    UnusualWhalesFlow,
)


def _store():
    tmp = tempfile.mkdtemp()
    return CanonicalStore(root=tmp)


def _prov(source="test"):
    return Provenance(source=source)


def _check(rec, source="test"):
    assert rec["record_id"], "record_id must be populated"
    assert rec["provenance"]["source"] == source
    assert rec["provenance"]["fetched_at"], "fetched_at must be populated"
    assert rec["provenance"]["supersedes"] is None


def test_bars():
    s = _store()
    s.append("bars", Bar(symbol="NVDA", date="2026-10-09", open=1.0, high=2.0,
                         low=0.5, close=1.5, volume=100,
                         provenance=_prov("nasdaq")))
    recs = s.read("bars", where={"symbol": "NVDA"})
    assert len(recs) == 1 and recs[0]["close"] == 1.5
    _check(recs[0], "nasdaq")


def test_bars_intraday():
    s = _store()
    s.append("bars", Bar(symbol="AAPL", date="2026-10-09", open=1, high=2,
                         low=1, close=1.5, volume=50, interval="15m",
                         timestamp="2026-10-09T14:30:00Z",
                         provenance=_prov("nasdaq")))
    recs = s.read("bars", where={"interval": "15m"})
    assert recs[0]["timestamp"] == "2026-10-09T14:30:00Z"


def test_corporate_actions():
    s = _store()
    s.append("corporate_actions",
             CorporateAction(symbol="NVDA", date="2024-06-10", action="split",
                             ratio=10.0, provenance=_prov("nasdaq")))
    recs = s.read("corporate_actions", where={"symbol": "NVDA"})
    assert recs[0]["ratio"] == 10.0
    _check(recs[0], "nasdaq")


def test_filings():
    s = _store()
    s.append("filings", Filing(cik="0001067983", accession="0001067983-26-000123",
                               form="10-K", filed_at="2026-02-20",
                               url="https://www.sec.gov/Archives/x",
                               local_path="/cold/sec/0001067983-26-000123.htm",
                               delta_summary="risk factors rewritten",
                               provenance=_prov("sec-edgar")))
    recs = s.read("filings", where={"form": "10-K"})
    assert recs[0]["delta_summary"] == "risk factors rewritten"
    _check(recs[0], "sec-edgar")


def test_news():
    s = _store()
    s.append("news", NewsItem(symbol="MU", published_at="2026-10-11T12:00:00Z",
                              headline="MU raises guide", url="https://x/y",
                              source="reuters", body_path="/cold/news/mu-1.txt",
                              provenance=_prov("news-crawl")))
    recs = s.read("news", where={"symbol": "MU"})
    assert recs[0]["body_path"] == "/cold/news/mu-1.txt"
    _check(recs[0], "news-crawl")


def test_thirteen_f_holdings():
    s = _store()
    s.append("thirteen_f_holdings",
             ThirteenFHolding(filer_cik="0001067983", filer_name="Berkshire",
                              quarter="2026-Q2", issuer="APPLE INC",
                              cusip="037833100", value_usd=1000, shares=10,
                              put_call=None, provenance=_prov("13f")))
    recs = s.read("thirteen_f_holdings", where={"cusip": "037833100"})
    assert recs[0]["shares"] == 10
    _check(recs[0], "13f")


def test_thirteen_f_moves():
    s = _store()
    s.append("thirteen_f_moves",
             ThirteenFMove(filer_cik="0001067983", filer_name="Berkshire",
                           quarter="2026Q2", report_date="2026-06-30",
                           cusip="037833100", issuer="APPLE INC", ticker="AAPL",
                           title_of_class="COM", kind="ADD",
                           q1_shares=10, q2_shares=20,
                           q1_value=1000, q2_value=2200, delta_value=1200,
                           tags=["scope_change"], provenance=_prov("13f-sweep")))
    recs = s.read("thirteen_f_moves", where={"kind": "ADD"})
    assert recs[0]["delta_value"] == 1200 and recs[0]["tags"] == ["scope_change"]
    _check(recs[0], "13f-sweep")


def test_unusual_whales_flow():
    s = _store()
    s.append("unusual_whales_flow",
             UnusualWhalesFlow(symbol="NVDA", timestamp="2026-10-11T15:00:00Z",
                               flow_type="sweep", premium_usd=250000.0,
                               strike=200.0, expiry="2026-11-21",
                               direction="call", source_detail="ask-side",
                               provenance=_prov("uw-plugin")))
    recs = s.read("unusual_whales_flow", where={"flow_type": "sweep"})
    assert recs[0]["premium_usd"] == 250000.0
    _check(recs[0], "uw-plugin")


def test_triage_flags():
    s = _store()
    s.append("triage_flags",
             TriageFlag(symbol="SMR", rank=1, flag="DOE loan approved",
                        aggregate="line1\nline2", agent="grok-news-triage",
                        sweep_id="sweep-001", provenance=_prov("grok")))
    recs = s.read("triage_flags", where={"rank": 1})
    assert recs[0]["agent"] == "grok-news-triage"
    _check(recs[0], "grok")


def test_symbol_master():
    s = _store()
    s.append("symbol_master",
             SymbolMaster(symbol="NVDA", name="NVIDIA Corporation",
                          cik="0001045810", cusip="67066G104",
                          gics_l1="Information Technology",
                          provenance=_prov("reference")))
    recs = s.read("symbol_master", where={"symbol": "NVDA"})
    assert recs[0]["cusip"] == "67066G104"
    _check(recs[0], "reference")


def test_paper_positions():
    s = _store()
    s.append("paper_positions",
             PaperPosition(book="ai_rotation", symbol="AMD", asof="2026-10-11",
                           qty=100.0, avg_cost=150.0,
                           provenance=_prov("bottleneck")))
    recs = s.read("paper_positions", where={"book": "ai_rotation"})
    assert recs[0]["qty"] == 100.0
    _check(recs[0], "bottleneck")


def test_provenance_required():
    s = _store()
    try:
        s.append("bars", Bar(symbol="X", date="2026-10-11", open=1, high=1,
                             low=1, close=1, volume=1))  # no provenance
    except ValueError:
        return
    raise AssertionError("expected ValueError for missing provenance")


def test_supersede():
    s = _store()
    old_id = s.append("bars", Bar(symbol="NVDA", date="2026-10-09", open=1.0,
                                  high=2.0, low=0.5, close=1.5, volume=100,
                                  provenance=_prov("nasdaq")))
    new_id = s.supersede("bars", old_id,
                         Bar(symbol="NVDA", date="2026-10-09", open=1.0,
                             high=2.0, low=0.5, close=1.6, volume=110,
                             provenance=_prov("nasdaq")))
    assert old_id != new_id
    all_recs = s.read("bars", where={"symbol": "NVDA"})
    assert len(all_recs) == 2, "append-only: both records persist"
    new = [r for r in all_recs if r["record_id"] == new_id][0]
    assert new["provenance"]["supersedes"] == old_id
    cur = s.current("bars", where={"symbol": "NVDA"})
    assert len(cur) == 1 and cur[0]["record_id"] == new_id
    assert cur[0]["close"] == 1.6


def test_append_returns_record_id():
    s = _store()
    rid = s.append("bars", Bar(symbol="X", date="2026-10-11", open=1, high=1,
                               low=1, close=1, volume=1, provenance=_prov()))
    assert rid and isinstance(rid, str)
    assert s.read("bars", where={"record_id": rid})
