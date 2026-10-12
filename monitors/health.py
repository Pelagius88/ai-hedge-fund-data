"""Health endpoint: one JSON-serializable snapshot of the data layer's state.

This is what the future Mica Console reads. No side effects — pure read.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")

from store.canonical import CanonicalStore
from store.schemas import TABLES
from monitors import staleness

ROOT = "/home/hatch/workspace/ai-hedge-fund-data"
REFRESH_LOG = os.path.join(ROOT, "_store", "daily_refresh_log.jsonl")
REFRESH_STATE = os.path.join(ROOT, "_store", "daily_refresh_state.json")
SEED_LOG = os.path.join(ROOT, "_store", "seed_log.jsonl")


def _tail_jsonl(path: str, n: int = 200) -> list[dict]:
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines[-n:]:
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _last_run() -> dict | None:
    """Most recent daily-refresh summary, if any."""
    for entry in reversed(_tail_jsonl(REFRESH_LOG, 500)):
        if entry.get("event") == "refresh_done":
            return entry
    return None


def _error_counts() -> dict:
    counts = {"refresh_failed_symbols_24h": 0, "recovery_pages_7d": 0}
    cutoff_24h = datetime.now(timezone.utc).timestamp() - 86400
    for entry in _tail_jsonl(REFRESH_LOG, 2000):
        ts = entry.get("ts", "")
        try:
            ets = datetime.fromisoformat(ts).timestamp()
        except (ValueError, TypeError):
            continue
        if entry.get("event") == "symbol" and entry.get("status") == "failed":
            if ets >= cutoff_24h:
                counts["refresh_failed_symbols_24h"] += 1
    cutoff_7d = datetime.now(timezone.utc).timestamp() - 7 * 86400
    store = CanonicalStore()
    for rec in store.read("pages"):
        prov = rec.get("provenance", {})
        if prov.get("source") == "recovery":
            try:
                ets = datetime.fromisoformat(prov.get("fetched_at", "")).timestamp()
            except (ValueError, TypeError):
                continue
            if ets >= cutoff_7d:
                counts["recovery_pages_7d"] += 1
    return counts


def _store_stats() -> dict:
    store = CanonicalStore()
    tables = {}
    total_records = 0
    total_bytes = 0
    for table in TABLES:
        tdir = os.path.join(store.root, table)
        recs = bytes_ = 0
        if os.path.isdir(tdir):
            for fn in os.listdir(tdir):
                fp = os.path.join(tdir, fn)
                bytes_ += os.path.getsize(fp)
                # count lines without loading the whole file
                with open(fp, "rb") as f:
                    recs += sum(1 for _ in f)
        tables[table] = {"records": recs, "bytes": bytes_}
        total_records += recs
        total_bytes += bytes_
    return {"tables": tables, "total_records": total_records,
            "total_bytes": total_bytes}


def health() -> dict:
    """JSON-serializable health snapshot."""
    feeds = staleness.check_all()
    worst = "ok"
    for f in feeds:
        if f["status"] == "critical":
            worst = "critical"
            break
        if f["status"] in ("warning", "no_data") and worst == "ok":
            worst = f["status"]
    last = _last_run()
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "overall": worst,
        "feeds": {f["feed"]: {"status": f["status"],
                              "age_hours": round(f.get("age_hours", 0) or 0, 2),
                              "contract_hours": f.get("max_age_hours")}
                 for f in feeds},
        "last_refresh": last,
        "open_pages": len(staleness.open_pages()),
        "errors": _error_counts(),
        "store": _store_stats(),
    }


def main() -> None:
    print(json.dumps(health(), indent=2, default=str))


if __name__ == "__main__":
    main()
