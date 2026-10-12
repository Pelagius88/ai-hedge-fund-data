"""Tests for Stage 2: recovery, backup, health, daily refresh."""
import json
import os
import sys
import tempfile

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")

PASS = []
FAIL = []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"PASS {name}")
    except Exception as e:
        FAIL.append((name, e))
        print(f"FAIL {name}: {e}")


# ---------------------------------------------------------- recovery ----
def test_retry_succeeds_after_failures():
    from pipelines import recovery
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("boom")
        return "ok"

    result, attempts = recovery.with_retries(
        flaky, attempts=3, base_delay=0.01, op_name="test-flaky")
    assert result == "ok", result
    assert attempts == 3, attempts


def test_final_failure_pages():
    from pipelines import recovery
    from store.canonical import CanonicalStore
    store = CanonicalStore()
    before = len(store.read("pages"))

    def always_fails():
        raise RuntimeError("simulated-outage-12345")

    try:
        recovery.with_retries(
            always_fails, attempts=2, base_delay=0.01,
            op_name="test-simulated-failure")
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert "simulated-outage-12345" in str(e)

    after = store.read("pages")
    assert len(after) == before + 1, "page record not written"
    page = after[-1]
    assert page["severity"] == "critical"
    assert page["feed"] == "test-simulated-failure"
    assert "simulated-outage-12345" in page["message"]
    assert page["provenance"]["source"] == "recovery"
    # clean up: acknowledge the test page
    from monitors import staleness
    staleness.acknowledge(page["record_id"])


# ------------------------------------------------------------ backup ----
def test_backup_restore_roundtrip():
    import store.backup as bkp
    from store.canonical import CanonicalStore
    from store.schemas import Provenance, Bar

    with tempfile.TemporaryDirectory() as tmp:
        # small synthetic store
        fake_root = os.path.join(tmp, "store")
        store = CanonicalStore(root=fake_root)
        prov = Provenance(source="test-backup")
        store.append_many("bars", [
            Bar(symbol="BKUP", date="2026-10-01", open=1.0, high=2.0,
                low=0.5, close=1.5, volume=100, provenance=prov),
            Bar(symbol="BKUP", date="2026-10-02", open=1.5, high=2.5,
                low=1.0, close=2.0, volume=200, provenance=prov),
        ])
        # point backup at the fake store via env-free override
        snap = bkp.snapshot(store_root=fake_root, kind="daily")
        assert os.path.isfile(snap), "snapshot not created"

        restored = bkp.restore(snap)
        rstore = CanonicalStore(root=restored)
        bars = rstore.current("bars", where={"symbol": "BKUP"})
        assert len(bars) == 2, f"expected 2 bars, got {len(bars)}"
        assert {b["date"] for b in bars} == {"2026-10-01", "2026-10-02"}


def test_backup_retention():
    import store.backup as bkp
    # prune on empty/missing dirs should not crash
    result = bkp.prune()
    assert "daily" in result and "weekly" in result


# ------------------------------------------------------------ health ----
def test_health_shape():
    from monitors import health as h
    snap = h.health()
    assert isinstance(snap, dict)
    for key in ("as_of", "overall", "feeds", "last_refresh",
                "open_pages", "errors", "store"):
        assert key in snap, f"missing {key}"
    assert snap["overall"] in ("ok", "warning", "critical", "no_data")
    assert "nasdaq-bars" in snap["feeds"]
    # JSON-serializable
    json.dumps(snap, default=str)


# ----------------------------------------------------- daily refresh ----
def test_daily_refresh_small():
    from pipelines import daily_refresh as dr
    # 2 symbols, real fetch path — proves end-to-end wiring
    summary = dr.run(symbols=["AAPL", "MSFT"], workers=2)
    assert summary["symbols"] == 2
    assert summary["ok"] + summary["failed"] == 2
    assert "new_bars" in summary
    assert "elapsed_s" in summary


def test_refresh_state_resume():
    from pipelines import daily_refresh as dr
    # second run same day should find everything done (or re-check cheaply)
    s1 = dr.run(symbols=["AAPL"], workers=1)
    s2 = dr.run(symbols=["AAPL"], workers=1)
    assert s2["symbols"] == 1


if __name__ == "__main__":
    check("test_retry_succeeds_after_failures", test_retry_succeeds_after_failures)
    check("test_final_failure_pages", test_final_failure_pages)
    check("test_backup_restore_roundtrip", test_backup_restore_roundtrip)
    check("test_backup_retention", test_backup_retention)
    check("test_health_shape", test_health_shape)
    check("test_daily_refresh_small", test_daily_refresh_small)
    check("test_refresh_state_resume", test_refresh_state_resume)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        sys.exit(1)
    print("ALL STAGE-2 TESTS PASS")
