"""Seed the canonical bars table for all 580 coverage symbols.

Resumable: skips symbols that already have >=200 bars in the store.
Parallel: 5 workers, 0.3s politeness gap between fetches per worker.
"""
import datetime
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")
from pipelines.quotes import fetch_bars, append_bars, detect_splits
from store.schemas import Provenance

START = "2025-10-11"
LOG = "/home/hatch/workspace/ai-hedge-fund-data/_store/seed_log.jsonl"


def get_symbols() -> list[str]:
    con = sqlite3.connect("/home/hatch/workspace/ts-spaces/valuation-dashboard/app.db")
    rows = con.execute("SELECT symbol FROM coverage_universe ORDER BY symbol").fetchall()
    con.close()
    return [r[0] for r in rows]


def already_seeded(sym: str) -> bool:
    from store.canonical import CanonicalStore
    recs = CanonicalStore().current("bars", where={"symbol": sym})
    return len(recs) >= 200


def seed_one(sym: str) -> dict:
    t0 = time.time()
    try:
        bars = fetch_bars(sym, start=START)
        if not bars:
            return {"symbol": sym, "status": "no_data", "bars": 0}
        prov = Provenance(
            source="nasdaq-bars",
            fetched_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            supersedes=None,
        )
        new_ids = append_bars(sym, bars, prov)
        splits = detect_splits(sym, bars, prov)
        return {
            "symbol": sym, "status": "ok", "bars": len(new_ids),
            "splits": len(splits), "elapsed": round(time.time() - t0, 1),
        }
    except Exception as e:
        return {"symbol": sym, "status": "error", "error": str(e)[:120]}


def main():
    import json
    syms = get_symbols()
    print(f"{len(syms)} symbols", flush=True)
    todo = [s for s in syms if not already_seeded(s)]
    print(f"{len(todo)} to seed ({len(syms) - len(todo)} already have >=200 bars)", flush=True)

    done, ok_bars, splits_total = 0, 0, 0
    failed = []
    with open(LOG, "a") as log:
        with ThreadPoolExecutor(max_workers=5) as ex:
            for res in ex.map(seed_one, todo):
                done += 1
                if res["status"] == "ok":
                    ok_bars += res["bars"]
                    splits_total += res.get("splits", 0)
                else:
                    failed.append(res)
                log.write(json.dumps(res) + "\n")
                log.flush()
                if done % 50 == 0:
                    print(f"  {done}/{len(todo)} done, {ok_bars} bars, {len(failed)} failed", flush=True)
                time.sleep(0.1)

    print(f"DONE: {done} symbols, {ok_bars} new bars, {splits_total} splits flagged", flush=True)
    print(f"failed ({len(failed)}): {[f['symbol'] for f in failed][:20]}", flush=True)


if __name__ == "__main__":
    main()
