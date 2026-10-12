"""Daily refresh: the full daily cycle in one idempotent, resumable script.

Steps, in order:
  1. Fetch the last ~7 days of bars for every coverage symbol (idempotent —
     append_bars skips dates already in the store).
  2. Run split detection on the fresh bars (corporate_actions table).
  3. Run staleness checks and page on critical breaches.
  4. Backfill any new gaps (resumable via the backfill state file).

Resumable: per-symbol completion is tracked in a state file, so an
interrupted run picks up where it left off. Safe to re-run any time —
every write path is idempotent.

Usage:  python3 pipelines/daily_refresh.py [--symbols N] [--workers N]
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")

from pipelines import backfill as bf
from pipelines.quotes import append_bars, detect_splits, fetch_bars
from pipelines.recovery import with_retries
from store.canonical import CanonicalStore
from store.schemas import Provenance
from monitors import staleness

ROOT = "/home/hatch/workspace/ai-hedge-fund-data"
STATE_PATH = os.path.join(ROOT, "_store", "daily_refresh_state.json")
LOG_PATH = os.path.join(ROOT, "_store", "daily_refresh_log.jsonl")

# Only the trailing window is fetched daily; history comes from backfill.
FETCH_WINDOW_DAYS = 10
WORKERS = 5
POLITENESS_S = 0.3


def _today() -> str:
    return datetime.date.today().isoformat()


def _load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_PATH)


def _log(entry: dict) -> None:
    entry = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
             **entry}
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _prov() -> Provenance:
    return Provenance(
        source="nasdaq-bars",
        fetched_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        supersedes=None,
    )


def refresh_one(symbol: str) -> dict:
    """Refresh one symbol. Idempotent — safe to call twice."""
    t0 = time.time()
    start = (datetime.date.today()
             - datetime.timedelta(days=FETCH_WINDOW_DAYS)).isoformat()
    bars = fetch_bars(symbol, start=start)
    if not bars:
        return {"symbol": symbol, "status": "no_data", "bars": 0,
                "splits": 0, "elapsed": round(time.time() - t0, 1)}
    prov = _prov()
    new_ids = append_bars(symbol, bars, prov)
    splits = detect_splits(symbol, bars, prov)
    return {"symbol": symbol, "status": "ok", "bars": len(new_ids),
            "splits": len(splits), "elapsed": round(time.time() - t0, 1)}


def run(symbols: list[str] | None = None, workers: int = WORKERS,
        max_symbols: int | None = None) -> dict:
    """Run the full daily cycle. Returns a summary dict."""
    t0 = time.time()
    run_date = _today()
    store = CanonicalStore()

    syms = symbols or bf.coverage_universe()
    syms = sorted(syms)
    if max_symbols:
        syms = syms[:max_symbols]

    state = _load_state()
    if state.get("run_date") != run_date:
        # New day — fresh state, but keep yesterday's summary for the log.
        state = {"run_date": run_date, "done": {}, "started_at": _now_iso()}

    todo = [s for s in syms if s not in state.get("done", {})]
    _log({"event": "refresh_start", "run_date": run_date,
          "symbols": len(syms), "todo": len(todo)})
    print(f"[daily] {run_date}: {len(todo)}/{len(syms)} symbols to refresh",
          flush=True)

    def _work(sym: str) -> dict:
        # Per-symbol retry: 3 attempts, backoff handled by recovery module.
        # A symbol that still fails is recorded, not fatal — the run continues.
        try:
            result, attempts = with_retries(
                lambda: refresh_one(sym), attempts=3, base_delay=10,
                op_name=f"daily-refresh:{sym}")
            result["attempts"] = attempts
            return result
        except Exception as e:  # noqa: BLE001 — recorded, run continues
            return {"symbol": sym, "status": "failed", "error": str(e)[:200],
                    "bars": 0, "splits": 0}

    ok = failed = new_bars = new_splits = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_work, s): s for s in todo}
        for fut in as_completed(futures):
            r = fut.result()
            state.setdefault("done", {})[r["symbol"]] = r["status"]
            _save_state(state)  # crash-safe: progress persisted per symbol
            _log({"event": "symbol", **r})
            if r["status"] == "ok":
                ok += 1
                new_bars += r["bars"]
                new_splits += r["splits"]
            elif r["status"] == "failed":
                failed += 1
            time.sleep(POLITENESS_S / workers)

    # Staleness sweep + paging.
    checks = staleness.check_all()
    paged = staleness.page_on_stale(checks)
    crit = [c for c in checks if c["status"] == "critical"]

    # Gap backfill (resumable on its own state file).
    bf_result = bf.backfill_all(resume=True)

    summary = {
        "run_date": run_date,
        "symbols": len(syms),
        "ok": ok,
        "failed": failed,
        "new_bars": new_bars,
        "new_splits": new_splits,
        "stale_critical": len(crit),
        "pages_issued": len(paged),
        "backfill": bf_result.get("summary", bf_result),
        "elapsed_s": round(time.time() - t0, 1),
    }
    state["summary"] = summary
    state["finished_at"] = _now_iso()
    _save_state(state)
    _log({"event": "refresh_done", **summary})
    print(f"[daily] done: {ok} ok, {failed} failed, {new_bars} new bars, "
          f"{new_splits} splits, {len(paged)} pages, "
          f"{summary['elapsed_s']}s", flush=True)
    return summary


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args()
    run(max_symbols=args.max_symbols, workers=args.workers)


if __name__ == "__main__":
    main()
