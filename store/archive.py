"""Cold archive for raw filings and old news.

The hot store stays fast; old records move to compressed cold files.
Append-only is preserved: nothing is ever deleted. Each archived record gets
a tombstone appended to the hot table via supersede() — the tombstone points
at the cold file, so the audit trail stays intact and current() drops the
original automatically.

Cold layout:  <archive_root>/<table>/<record-month>.jsonl.gz
Index:        <archive_root>/_index.jsonl   (record_id -> table, cold_path)

Retention policy (apply_retention_policy):
  filings  older than 2 years (730d) -> cold
  news     older than 1 year  (365d) -> cold
  bars     NEVER archived (needed for backtests)
  pages    NEVER archived (audit trail)
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")

from store.canonical import CanonicalStore
from store.schemas import ArchiveTombstone, Provenance

ARCHIVE_ROOT = os.path.join(
    os.path.dirname(__file__), "..", "_archive")

# Tables that must never be archived, no matter what.
NEVER_ARCHIVE = {"bars", "pages"}

# table -> retention days. Only tables listed here are touched by the policy.
RETENTION_DAYS = {
    "filings": 730,
    "news": 365,
}

_INDEX_NAME = "_index.jsonl"

# Same priority order as CanonicalStore._MONTH_KEYS.
_MONTH_KEYS = ("date", "filed_at", "published_at", "asof", "as_of",
               "timestamp", "report_date")


def _record_date(rec: dict):
    """Best-effort date for a record; None if it can't be dated."""
    for key in _MONTH_KEYS:
        v = rec.get(key)
        if not v:
            continue
        try:
            s = str(v)[:10]
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _record_month(rec: dict) -> str:
    d = _record_date(rec)
    return d.strftime("%Y-%m") if d else datetime.now(timezone.utc).strftime("%Y-%m")


def _index_path(dest: str) -> str:
    return os.path.join(dest, _INDEX_NAME)


def _load_index(dest: str) -> dict:
    """record_id -> {"table": ..., "cold_path": ...}"""
    p = _index_path(dest)
    out = {}
    if os.path.isfile(p):
        with open(p) as f:
            for line in f:
                line = line.strip()
                if line:
                    e = json.loads(line)
                    out[e["record_id"]] = e
    return out


def hot_records(table: str, store: CanonicalStore | None = None) -> list[dict]:
    """Current records minus archive tombstones: what hot queries should see."""
    store = store or CanonicalStore()
    return [r for r in store.current(table) if not r.get("archived")]


def archive_old(table: str, older_than_days: int, dest: str | None = None,
                dry_run: bool = False,
                where: dict | None = None) -> dict:
    """Move records older than the threshold to compressed cold storage.

    Tombstones are appended via supersede() (never deletes). Idempotent:
    re-running archives nothing new. Raises ValueError for NEVER_ARCHIVE
    tables. `where` optionally restricts candidates by exact field match
    (used by tests to target synthetic records).
    """
    if table in NEVER_ARCHIVE:
        raise ValueError(f"{table} is never archived")
    dest = os.path.abspath(dest or ARCHIVE_ROOT)
    store = CanonicalStore()
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)

    recs = store.read(table)
    already = {r["archived_record_id"] for r in recs if r.get("archived")}
    candidates = []
    for r in recs:
        if r.get("archived"):
            continue                      # tombstone, not data
        if r["record_id"] in already:
            continue                      # already archived
        if where and not all(r.get(k) == v for k, v in where.items()):
            continue
        d = _record_date(r)
        if d is None:
            continue                      # undatable: leave hot, never guess
        if d < cutoff:
            candidates.append(r)

    buckets: dict[str, list[dict]] = {}
    for r in candidates:
        buckets.setdefault(_record_month(r), []).append(r)

    result = {
        "table": table,
        "older_than_days": older_than_days,
        "candidates": len(candidates),
        "months": sorted(buckets),
        "dry_run": dry_run,
        "archived": 0,
        "touched": False,
    }
    if dry_run or not candidates:
        return result

    os.makedirs(dest, exist_ok=True)
    index_entries = []
    for month, rs in sorted(buckets.items()):
        rel = os.path.join(table, f"{month}.jsonl.gz")
        full = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with gzip.open(full, "at", encoding="utf-8") as f:
            for r in rs:
                f.write(json.dumps(r, default=str) + "\n")
        for r in rs:
            index_entries.append({
                "record_id": r["record_id"],
                "table": table,
                "cold_path": rel,
                "archived_at": datetime.now(timezone.utc).isoformat(),
            })

    # Tombstones via supersede: originals drop out of current(), audit intact.
    # NOTE: fresh Provenance per tombstone — supersede() mutates
    # provenance.supersedes in place, so sharing one object breaks the chain.
    for r in candidates:
        entry = next(e for e in index_entries
                     if e["record_id"] == r["record_id"])
        tomb = ArchiveTombstone(
            archived_record_id=r["record_id"],
            table=table,
            cold_path=entry["cold_path"],
            provenance=Provenance(
                source="cold-archive",
                fetched_at=datetime.now(timezone.utc).isoformat()),
        )
        store.supersede(table, r["record_id"], tomb)

    with open(_index_path(dest), "a") as f:
        for e in index_entries:
            f.write(json.dumps(e) + "\n")

    result["archived"] = len(candidates)
    result["touched"] = True
    return result


def retrieve(record_id: str, dest: str | None = None) -> dict:
    """Fetch one record from cold storage by ID. Raises KeyError if absent."""
    dest = os.path.abspath(dest or ARCHIVE_ROOT)
    entry = _load_index(dest).get(record_id)
    if not entry:
        raise KeyError(f"{record_id} not in cold archive index")
    full = os.path.join(dest, entry["cold_path"])
    with gzip.open(full, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("record_id") == record_id:
                return rec
    raise KeyError(f"{record_id} indexed but not found in {entry['cold_path']}")


def archive_status(dest: str | None = None) -> dict:
    """Per-table: hot count, cold count, oldest hot record, storage sizes."""
    dest = os.path.abspath(dest or ARCHIVE_ROOT)
    store = CanonicalStore()
    index = _load_index(dest)
    cold_by_table: dict[str, int] = {}
    for e in index.values():
        cold_by_table[e["table"]] = cold_by_table.get(e["table"], 0) + 1

    def _dir_bytes(d: str) -> int:
        total = 0
        if os.path.isdir(d):
            for root, _, files in os.walk(d):
                for fn in files:
                    try:
                        total += os.path.getsize(os.path.join(root, fn))
                    except OSError:
                        pass
        return total

    status = {}
    for table in ("filings", "news", "bars", "pages", "thirteen_f_moves",
                  "corporate_actions", "unusual_whales_flow", "triage_flags"):
        hot = hot_records(table, store)
        dates = [d for d in (_record_date(r) for r in hot) if d]
        status[table] = {
            "hot_count": len(hot),
            "cold_count": cold_by_table.get(table, 0),
            "oldest_hot": min(dates).date().isoformat() if dates else None,
            "hot_bytes": _dir_bytes(os.path.join(store.root, table)),
            "cold_bytes": _dir_bytes(os.path.join(dest, table)),
            "never_archive": table in NEVER_ARCHIVE,
        }
    return status


def apply_retention_policy(dry_run: bool = False,
                           dest: str | None = None) -> dict:
    """Archive filings >2y and news >1y. Bars and pages are never touched."""
    out = {}
    for table, days in RETENTION_DAYS.items():
        out[table] = archive_old(table, days, dest=dest, dry_run=dry_run)
    return out
