"""Backfill discipline.

find_gaps() compares expected NYSE trading days against bars in the store and
returns missing date ranges. backfill() fetches and appends the missing bars
through the one quote pipeline (idempotent, so re-runs are safe).
backfill_all() sweeps every symbol with a resumable state file.

Expected trading days come from a static NYSE calendar (2020-2027) with
observed-day rules, plus known one-off full-day closures (national days of
mourning, Hurricane Sandy). A gap the source cannot fill — e.g. an
unanticipated closure — is reported as unfilled, not hidden: backfill tries,
Nasdaq returns nothing, and the gap stays listed.
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import os as _os
import sys as _sys
import time as _time

_sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")

from pipelines.quotes import fetch_bars, append_bars
from store.canonical import CanonicalStore
from store.schemas import Provenance

_store = CanonicalStore()

STATE_PATH = "/home/hatch/workspace/ai-hedge-fund-data/_store/backfill_state.json"
LOG_PATH = "/home/hatch/workspace/ai-hedge-fund-data/_store/backfill_log.jsonl"


# ------------------------------------------------------------ NYSE calendar
def _easter_sunday(year: int) -> _dt.date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return _dt.date(year, month, day + 1)


def _observed(d: _dt.date) -> _dt.date:
    """Weekend -> nearest weekday (Sat -> Fri, Sun -> Mon)."""
    if d.weekday() == 5:
        return d - _dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + _dt.timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _dt.date:
    """n-th `weekday` (0=Mon) of month; n=-1 means last."""
    if n == -1:
        d = _dt.date(year, month + 1, 1) - _dt.timedelta(days=1) if month < 12 \
            else _dt.date(year, 12, 31)
        while d.weekday() != weekday:
            d -= _dt.timedelta(days=1)
        return d
    d = _dt.date(year, month, 1)
    while d.weekday() != weekday:
        d += _dt.timedelta(days=1)
    return d + _dt.timedelta(weeks=n - 1)


def nyse_holidays(year: int) -> set[_dt.date]:
    """NYSE holidays for one year (regular full-day closures)."""
    h = {
        _observed(_dt.date(year, 1, 1)),                    # New Year's
        _nth_weekday(year, 1, 0, 3),                        # MLK
        _nth_weekday(year, 2, 0, 3),                        # Presidents
        _easter_sunday(year) - _dt.timedelta(days=2),       # Good Friday
        _nth_weekday(year, 5, 0, -1),                       # Memorial
        _observed(_dt.date(year, 7, 4)),                    # Independence
        _nth_weekday(year, 9, 0, 1),                        # Labor
        _nth_weekday(year, 11, 3, 4),                       # Thanksgiving
        _observed(_dt.date(year, 12, 25)),                  # Christmas
    }
    if year >= 2022:                                        # Juneteenth
        h.add(_observed(_dt.date(year, 6, 19)))
    return h


# One-off full-day NYSE closures that are NOT holidays but are deterministic
# historical facts (national days of mourning, weather emergencies). These
# belong in the calendar: the exchange was closed, so no bars can exist.
_ONE_OFF_CLOSURES = {
    _dt.date(2012, 10, 29),  # Hurricane Sandy
    _dt.date(2012, 10, 30),  # Hurricane Sandy
    _dt.date(2018, 12, 5),   # National day of mourning: George H.W. Bush
    _dt.date(2025, 1, 9),    # National day of mourning: Jimmy Carter
}


_HOLIDAYS: dict[int, set[_dt.date]] = {}


def is_trading_day(d: _dt.date) -> bool:
    if d.weekday() >= 5:
        return False
    if d in _ONE_OFF_CLOSURES:
        return False
    h = _HOLIDAYS.get(d.year)
    if h is None:
        h = nyse_holidays(d.year)
        _HOLIDAYS[d.year] = h
    return d not in h


def expected_trading_days(start: str, end: str) -> list[str]:
    """YYYY-MM-DD strings for every expected trading day in [start, end]."""
    s = _dt.date.fromisoformat(start)
    e = _dt.date.fromisoformat(end)
    out = []
    d = s
    while d <= e:
        if is_trading_day(d):
            out.append(d.isoformat())
        d += _dt.timedelta(days=1)
    return out


# ------------------------------------------------------------------ gaps ---
def find_gaps(symbol: str, start: str | None = None, end: str | None = None,
              _dates: set[str] | None = None) -> list[dict]:
    """Missing trading-day ranges for symbol. Each gap: {start, end, days}."""
    sym = symbol.upper()
    if _dates is None:
        recs = _store.current("bars", where={"symbol": sym})
        _dates = {r["date"] for r in recs}
    if not _dates:
        return []
    start = start or min(_dates)
    end = end or _dt.date.today().isoformat()
    # Never expect today (session may be open) — cap at yesterday.
    today = _dt.date.today().isoformat()
    if end >= today:
        end = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    if end < start:
        return []
    missing = [d for d in expected_trading_days(start, end) if d not in _dates]
    gaps = []
    for d in missing:
        if gaps and gaps[-1]["end"] == (
                _dt.date.fromisoformat(d) - _dt.timedelta(days=1)).isoformat():
            gaps[-1]["end"] = d
            gaps[-1]["days"] += 1
        else:
            gaps.append({"start": d, "end": d, "days": 1})
    return gaps


def backfill(symbol: str, _dates: set[str] | None = None) -> dict:
    """Fetch and append missing bars for one symbol. Returns a report.

    One range fetch covers all gaps (append_bars is idempotent, so existing
    bars are skipped) — far fewer requests than one fetch per gap.
    Pass _dates (the symbol's known bar dates) to avoid re-scanning the store;
    the post-backfill check then reuses the in-memory set plus added dates.
    """
    sym = symbol.upper()
    gaps = find_gaps(sym, _dates=_dates)
    added = 0
    added_dates: set[str] = set()
    if gaps:
        bars = fetch_bars(sym, start=gaps[0]["start"], end=gaps[-1]["end"])
        if bars:
            prov = Provenance(source="nasdaq-bars")
            added = len(append_bars(sym, bars, prov))
            added_dates = {b["date"] for b in bars}
        _time.sleep(0.3)
    if _dates is not None:
        remaining = find_gaps(sym, _dates=_dates | added_dates)
    else:
        remaining = find_gaps(sym)
    return {"symbol": sym, "gaps_found": len(gaps),
            "bars_added": added,
            "gaps_remaining": len(remaining),
            "remaining": remaining}


def _load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            return _json.load(f)
    except (FileNotFoundError, _json.JSONDecodeError):
        return {"completed": {}}


def _save_state(state: dict) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        _json.dump(state, f)
    _os.replace(tmp, STATE_PATH)


def _log(entry: dict) -> None:
    with open(LOG_PATH, "a") as f:
        f.write(_json.dumps(entry) + "\n")


def all_symbols() -> list[str]:
    """Distinct symbols in the bars table (one full scan)."""
    syms: set[str] = set()
    tdir = _os.path.join(_store.root, "bars")
    if not _os.path.isdir(tdir):
        return []
    import json as _j
    for fn in sorted(_os.listdir(tdir)):
        with open(_os.path.join(tdir, fn)) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        syms.add(_j.loads(line).get("symbol"))
                    except Exception:
                        pass
    return sorted(s for s in syms if s)


def coverage_universe() -> set[str]:
    """The 580-name coverage universe from the Lab DB.

    Backfill scope is the universe — test/proof symbols that leaked into the
    bars table during development are out of scope (and reported, not fixed,
    here).
    """
    import sqlite3 as _sq
    con = _sq.connect(
        "/home/hatch/workspace/ts-spaces/valuation-dashboard/app.db")
    try:
        rows = con.execute("SELECT symbol FROM coverage_universe").fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}


def backfill_all(resume: bool = True, symbols: list[str] | None = None) -> dict:
    """Sweep every symbol for gaps. Resumable via the state file.

    One full table scan up front groups dates by symbol, so per-symbol work
    is in-memory. Only symbols with gaps hit the network.
    """
    state = _load_state() if resume else {"completed": {}}
    done = state["completed"]

    # One scan: dates per symbol.
    dates_by_symbol: dict[str, set[str]] = {}
    tdir = _os.path.join(_store.root, "bars")
    import json as _j
    for fn in sorted(_os.listdir(tdir)):
        with open(_os.path.join(tdir, fn)) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = _j.loads(line)
                except Exception:
                    continue
                s = r.get("symbol")
                if s:
                    dates_by_symbol.setdefault(s, set()).add(r["date"])
    auto = symbols is None
    syms = symbols or sorted(dates_by_symbol)
    out_of_scope = []
    if auto:
        # Scope to the coverage universe; report (don't backfill) anything else.
        universe = coverage_universe()
        out_of_scope = sorted(s for s in syms if s not in universe)
        syms = [s for s in syms if s in universe]
        if out_of_scope:
            print(f"{len(out_of_scope)} symbols out of universe scope, skipped: "
                  f"{', '.join(out_of_scope[:10])}"
                  f"{'...' if len(out_of_scope) > 10 else ''}", flush=True)
    todo = [s for s in syms if s not in done]
    print(f"{len(syms)} symbols, {len(todo)} to check "
          f"({len(syms) - len(todo)} already done)", flush=True)

    summary = {"checked": 0, "with_gaps": 0, "bars_added": 0,
               "still_gappy": [], "errors": [],
               "out_of_scope": out_of_scope}
    for i, sym in enumerate(todo, 1):
        try:
            dates = dates_by_symbol.get(sym, set())
            gaps = find_gaps(sym, _dates=dates)
            if not gaps:
                done[sym] = {"gaps": 0, "bars_added": 0}
                summary["checked"] += 1
                continue
            rep = backfill(sym, _dates=dates)
            done[sym] = {"gaps": rep["gaps_found"],
                         "bars_added": rep["bars_added"]}
            summary["checked"] += 1
            summary["with_gaps"] += 1
            summary["bars_added"] += rep["bars_added"]
            if rep["gaps_remaining"]:
                summary["still_gappy"].append(
                    {"symbol": sym, "remaining": rep["remaining"]})
            _log({"symbol": sym, **rep})
            print(f"[{i}/{len(todo)}] {sym}: {rep['gaps_found']} gaps, "
                  f"+{rep['bars_added']} bars", flush=True)
        except Exception as e:  # noqa: BLE001 — keep the sweep moving
            summary["errors"].append({"symbol": sym, "error": str(e)[:150]})
            print(f"[{i}/{len(todo)}] {sym}: ERROR {str(e)[:100]}", flush=True)
        if i % 25 == 0:
            _save_state(state)
    _save_state(state)
    _log({"summary": summary})
    return summary


def main() -> None:
    summary = backfill_all()
    print(_json.dumps(summary, indent=2)[:2000])


if __name__ == "__main__":
    main()
