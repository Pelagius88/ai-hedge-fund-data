"""Tests for corporate-action adjustment at ingestion.

Uses synthetic unadjusted data (Nasdaq's feed is already split-adjusted, so
real splits don't appear in it). Each test uses a unique TEST_ symbol to keep
the shared store clean.
"""
import sys

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")

from store.canonical import CanonicalStore
from store.schemas import CorporateAction, Provenance
from pipelines.quotes import append_bars, detect_splits
from pipelines import corp_actions as ca

_store = CanonicalStore()
_UID = 0
import random as _random
_TOKEN = _random.randrange(1000, 9999)


def _sym():
    global _UID
    _UID += 1
    return f"T{_TOKEN}{_UID:03d}"


def _prov(src="test"):
    return Provenance(source=src)


def _seed(sym, closes, start_day=1, base_vol=1_000_000):
    """Seed bars where close[i] = closes[i], O/H/L derived, dates 2024-06-0X."""
    bars = []
    for i, c in enumerate(closes):
        bars.append({
            "date": f"2024-06-{start_day + i:02d}",
            "open": round(c * 0.99, 2), "high": round(c * 1.01, 2),
            "low": round(c * 0.98, 2), "close": c, "volume": base_vol,
        })
    return append_bars(sym, bars, _prov())


def test_split_detect_confirm_adjust():
    """NVDA-like 10:1 split: detect -> confirm -> adjust -> continuous."""
    sym = _sym()
    pre = [1200.0, 1210.0, 1195.0, 1205.0, 1208.0]   # pre-split prices
    post = [121.0, 122.5, 119.0, 123.0, 124.0]        # post-split prices
    _seed(sym, pre + post)
    # 1. detect flags 10:1 on 2024-06-06
    det_ids = detect_splits(sym, [
        {"date": f"2024-06-{d:02d}", "open": o, "high": o, "low": o,
         "close": c, "volume": 1}
        for d, (o, c) in enumerate(
            [(1188.0, 1200.0), (1198.0, 1210.0), (1183.0, 1195.0),
             (1193.0, 1205.0), (1196.0, 1208.0),
             (121.0, 121.0), (122.5, 122.5), (119.0, 119.0),
             (123.0, 123.0), (124.0, 124.0)], start=1)
    ], _prov())
    assert len(det_ids) == 1, f"expected 1 detection, got {len(det_ids)}"
    det = _store.read("corporate_actions", where={"record_id": det_ids[0]})[0]
    assert det["action"] == "split" and det["ratio"] == 10.0
    assert det["date"] == "2024-06-06", det["date"]
    assert det["status"] == "detected"
    # 2. confirm
    cid = ca.confirm_action(sym, "split", "2024-06-06", ratio=10.0,
                            provenance=_prov("test"),
                            detection_id=det_ids[0])
    conf = _store.read("corporate_actions", where={"record_id": cid})[0]
    assert conf["status"] == "confirmed"
    assert conf["provenance"]["supersedes"] == det_ids[0]
    # 3. adjust
    summary = ca.adjust_bars(sym)
    assert summary["actions_applied"] == 1, summary
    assert summary["bars_adjusted"] == 5, summary
    # 4. continuity: pre-split closes now ~120, no cliff at the boundary
    cur = {b["date"]: b for b in _store.current("bars", where={"symbol": sym})}
    assert len(cur) == 10, f"expected 10 current bars, got {len(cur)}"
    assert abs(cur["2024-06-05"]["close"] - 120.8) < 0.01, cur["2024-06-05"]
    assert abs(cur["2024-06-06"]["close"] - 121.0) < 0.01
    gap = abs(cur["2024-06-06"]["close"] - cur["2024-06-05"]["close"])
    assert gap / cur["2024-06-05"]["close"] < 0.05, f"cliff remains: {gap}"
    # volume scaled up 10x pre-split
    assert cur["2024-06-01"]["volume"] == 10_000_000
    # 5. supersede chain: old bars still readable, linked
    all_recs = _store.read("bars", where={"symbol": sym})
    assert len(all_recs) == 15, f"expected 15 total (10 orig + 5 adj), got {len(all_recs)}"
    adj = [b for b in all_recs
           if b.get("provenance", {}).get("source", "").startswith("corp-action-adjustment")]
    assert len(adj) == 5
    for a in adj:
        assert a["provenance"]["supersedes"], "adjusted bar must link old record"
    # 6. idempotency: second run is a no-op
    summary2 = ca.adjust_bars(sym)
    assert summary2["actions_applied"] == 0 and summary2["bars_adjusted"] == 0
    print("PASS test_split_detect_confirm_adjust")


def test_dividend_adjustment():
    sym = _sym()
    _seed(sym, [100.0, 101.0, 102.0, 100.5, 101.5])
    ca.confirm_action(sym, "dividend", "2024-06-04", amount=2.0,
                      provenance=_prov("test"))
    summary = ca.adjust_bars(sym)
    assert summary["bars_adjusted"] == 3, summary
    cur = {b["date"]: b for b in _store.current("bars", where={"symbol": sym})}
    assert abs(cur["2024-06-01"]["close"] - 98.0) < 0.01
    assert abs(cur["2024-06-03"]["close"] - 100.0) < 0.01
    assert abs(cur["2024-06-04"]["close"] - 100.5) < 0.01  # on/after: untouched
    print("PASS test_dividend_adjustment")


def test_reverse_split():
    sym = _sym()
    _seed(sym, [2.0, 2.1, 1.9, 20.5, 21.0])  # 1:10 reverse on 06-04
    ca.confirm_action(sym, "split", "2024-06-04", ratio=0.1,
                      provenance=_prov("test"))
    ca.adjust_bars(sym)
    cur = {b["date"]: b for b in _store.current("bars", where={"symbol": sym})}
    assert abs(cur["2024-06-01"]["close"] - 20.0) < 0.01
    assert cur["2024-06-01"]["volume"] == 100_000  # volume / 10
    print("PASS test_reverse_split")


def test_reject_detection():
    sym = _sym()
    det_id = _store.append("corporate_actions", CorporateAction(
        symbol=sym, date="2024-06-06", action="split", ratio=2.0,
        provenance=_prov("test")))
    rid = ca.reject_detection(det_id, _prov("test"))
    rec = _store.read("corporate_actions", where={"record_id": rid})[0]
    assert rec["status"] == "rejected"
    assert rec["provenance"]["supersedes"] == det_id
    # rejected actions are never applied
    _seed(sym, [50.0, 51.0])
    summary = ca.adjust_bars(sym)
    assert summary["actions_applied"] == 0
    print("PASS test_reject_detection")


def test_confirm_validation():
    sym = _sym()
    try:
        ca.confirm_action(sym, "split", "2024-06-06", provenance=_prov("test"))
        raise AssertionError("should have raised")
    except ValueError:
        pass
    try:
        ca.confirm_action(sym, "dividend", "2024-06-06", provenance=_prov("test"))
        raise AssertionError("should have raised")
    except ValueError:
        pass
    print("PASS test_confirm_validation")


def test_double_confirm_rejected():
    sym = _sym()
    ca.confirm_action(sym, "split", "2024-06-06", ratio=2.0,
                      provenance=_prov("test"))
    try:
        ca.confirm_action(sym, "split", "2024-06-06", ratio=2.0,
                          provenance=_prov("test"))
        raise AssertionError("should have raised")
    except ValueError:
        pass
    print("PASS test_double_confirm_rejected")


if __name__ == "__main__":
    test_split_detect_confirm_adjust()
    test_dividend_adjustment()
    test_reverse_split()
    test_reject_detection()
    test_confirm_validation()
    test_double_confirm_rejected()
    print("ALL CORP-ACTION TESTS PASS")
