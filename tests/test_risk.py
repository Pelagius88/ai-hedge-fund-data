"""Tests for the risk package (Stage 3). Synthetic data only."""
import sys
sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")

from risk import limits, stops, circuit_breaker, var, governance


def _bars(symbol, closes, base=100.0):
    """Synthetic daily bars from a close series."""
    out = []
    prev = base
    for i, c in enumerate(closes):
        out.append({"symbol": symbol, "date": f"2026-01-{i+1:02d}",
                    "open": prev, "high": max(prev, c) * 1.01,
                    "low": min(prev, c) * 0.99, "close": c,
                    "volume": 1000000})
        prev = c
    return out


# ------------------------------------------------------------- limits ----
def test_limits_pass():
    pos = [limits.Position("A", 100, 50.0, 40.0),   # 5k
           limits.Position("B", 100, 30.0, 28.0)]   # 3k
    r = limits.check_limits(pos, "test", nav=100000.0)
    assert r.passed, [c.detail for c in r.breaches]
    print("PASS test_limits_pass")


def test_limits_breach_no_override():
    # 20% single name vs 12% cap -> breach, and there is no override path
    pos = [limits.Position("A", 1000, 20.0, 15.0)]  # 20k of 100k NAV
    r = limits.check_limits(pos, "test", nav=100000.0)
    assert not r.passed
    assert any("max_position_pct[A]" in c.limit_name for c in r.breaches)
    # no override mechanism exists on the module
    assert not hasattr(limits, "override") and not hasattr(r, "override")
    print("PASS test_limits_breach_no_override")


def test_top3_concentration():
    pos = [limits.Position("A", 100, 200.0, 150.0),  # 20k
           limits.Position("B", 100, 200.0, 150.0),  # 20k
           limits.Position("C", 100, 200.0, 150.0),  # 20k -> top3 = 60%
           limits.Position("D", 100, 10.0, 9.0)]
    r = limits.check_limits(pos, "test", nav=100000.0)
    assert any(c.limit_name == "max_top3_concentration" and not c.passed
               for c in r.checks), "top3 60% should breach 50% cap"
    print("PASS test_top3_concentration")


# -------------------------------------------------------------- stops ----
def test_stop_formula():
    # rising name: peak 129, ATR ~2 -> trailing = 129 - 2*atr (Turtle 2N)
    closes = [100 + i for i in range(30)]  # 100..129
    bars = _bars("T", closes)
    lvl = stops.compute_stop("T", bars)
    assert lvl.stop_price == round(129 - 2.0 * lvl.atr, 2)
    print("PASS test_stop_formula")


def test_stop_no_cost_floor():
    # flat then crash: pure trailing stop, no floor — the stop follows the
    # peak down through the crash, no artificial bound
    closes = [100.0] * 15 + [80.0] * 15
    bars = _bars("T", closes)
    lvl = stops.compute_stop("T", bars)
    assert lvl.stop_price < 100.0, f"trailing stop should track peak down: {lvl.stop_price}"
    print("PASS test_stop_no_cost_floor")


def test_stop_flag():
    lvl = stops.StopLevel("T", stop_price=100.0,
                          peak_close=120.0, atr=2.0)
    flags = stops.check_stops([lvl], {"T": 99.0})
    assert flags[0].broken and flags[0].distance_atr < 0
    flags = stops.check_stops([lvl], {"T": 105.0})
    assert not flags[0].broken and flags[0].distance_atr == 2.5
    print("PASS test_stop_flag")


# ----------------------------------------------------- circuit breaker ----
def test_breaker_trips_and_blocks():
    cb = circuit_breaker.CircuitBreaker("test", drawdown_pct=0.20)
    assert cb.update(100000.0) is None
    assert cb.allow_new_positions()
    ev = cb.update(79000.0)  # -21%
    assert ev is not None and ev.event == "tripped"
    assert not cb.allow_new_positions(), "new positions must be blocked"
    print("PASS test_breaker_trips_and_blocks")


def test_breaker_reset_needs_reason():
    cb = circuit_breaker.CircuitBreaker("test")
    cb.update(100000.0)
    cb.update(70000.0)
    assert cb.tripped
    try:
        cb.reset("")
        assert False, "empty reason should raise"
    except ValueError:
        pass
    ev = cb.reset("reviewed drawdown: single-name gap, thesis intact")
    assert ev.event == "reset" and cb.allow_new_positions()
    print("PASS test_breaker_reset_needs_reason")


# ------------------------------------------------------------------ var ----
def test_var_panel():
    import random
    random.seed(7)
    closes_a = [100 * (1 + random.gauss(0, 0.02)) ** 1 for _ in range(300)]
    # cumulative product for a price series
    px = 100.0
    series = []
    for _ in range(300):
        px *= 1 + random.gauss(0.0005, 0.02)
        series.append(px)
    bars = {"A": _bars("A", series)}
    weights = {"A": 1.0}
    panel = var.compute_var("test", 100000.0, bars, weights)
    assert len(panel) == 12, f"expected 12 results, got {len(panel)}"
    h99 = [r for r in panel if r.method == "historical"
           and r.confidence == 0.99 and r.horizon_days == 1][0]
    assert h99.var > 0 and h99.expected_shortfall >= h99.var, \
        "ES must be >= VaR"
    print(f"PASS test_var_panel (1d99% hist VaR=${h99.var:,.0f} "
          f"ES=${h99.expected_shortfall:,.0f})")


def test_var_contributors():
    bars = {"A": _bars("A", [100 + i * 0.5 for i in range(100)]),
            "B": _bars("B", [50 - i * 0.2 for i in range(100)])}
    contrib = var.var_contributors(bars, {"A": 0.7, "B": 0.3}, 100000.0)
    assert len(contrib) == 2 and contrib[0][1] >= contrib[1][1]
    print("PASS test_var_contributors")


# ----------------------------------------------------------- governance ----
def test_governance_change_logged():
    g = governance.GovernanceLog(changed_by="test")
    assert g.get("atr_multiple") == 2.0
    ch = g.set_parameter("atr_multiple", 2.5,
                         "widening stops after whipsaw review 2026-10-11")
    assert ch.version == 2 and ch.old_value == 2.0 and ch.new_value == 2.5
    assert "whipsaw" in ch.reason
    hist = g.parameter_history("atr_multiple")
    assert len(hist) == 2 and hist[-1].reason == ch.reason
    print("PASS test_governance_change_logged")


def test_governance_no_silent_change():
    g = governance.GovernanceLog()
    for bad in ("", "   ", None):
        try:
            g.set_parameter("atr_window", 20, bad)
            assert False, f"should refuse reason={bad!r}"
        except ValueError:
            pass
    assert g.get("atr_window") == 14, "value must be unchanged"
    try:
        g.set_parameter("nonexistent_param", 1, "x")
        assert False
    except KeyError:
        pass
    print("PASS test_governance_no_silent_change")


if __name__ == "__main__":
    test_limits_pass()
    test_limits_breach_no_override()
    test_top3_concentration()
    test_stop_formula()
    test_stop_cost_floor()
    test_stop_flag()
    test_breaker_trips_and_blocks()
    test_breaker_reset_needs_reason()
    test_var_panel()
    test_var_contributors()
    test_governance_change_logged()
    test_governance_no_silent_change()
    print("ALL RISK TESTS PASS")
