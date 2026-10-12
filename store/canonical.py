"""Append-only canonical store.

One JSONL file per table per month: <root>/<table>/YYYY-MM.jsonl
Records are dataclasses carrying Provenance. Raw is forever: records are never
rewritten, only superseded by newer records whose provenance.supersedes points
at the old record's record_id.
"""
from __future__ import annotations
import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from uuid import uuid4

STORE_ROOT = os.environ.get(
    "FUND_DATA_ROOT",
    os.path.join(os.path.dirname(__file__), "..", "_store"),
)

# Date-ish fields used to pick the monthly partition, in priority order.
_MONTH_KEYS = ("date", "filed_at", "published_at", "asof", "as_of",
               "timestamp", "report_date")


class CanonicalStore:
    def __init__(self, root: str = STORE_ROOT):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, table: str, month: str) -> str:
        d = os.path.join(self.root, table)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{month}.jsonl")

    @staticmethod
    def _month_for(d: dict) -> str:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        for key in _MONTH_KEYS:
            if d.get(key):
                try:
                    month = str(d[key])[:7]
                    break
                except Exception:
                    pass
        return month

    @staticmethod
    def _prep(record) -> dict:
        assert is_dataclass(record), "records must be dataclasses with provenance"
        d = asdict(record)
        prov = d.get("provenance")
        if not prov or not prov.get("source"):
            raise ValueError("provenance with a source is required on every record")
        if not d.get("record_id"):
            d["record_id"] = uuid4().hex
        return d

    def append(self, table: str, record) -> str:
        """Append one record. Returns its record_id."""
        d = self._prep(record)
        with open(self._path(table, self._month_for(d)), "a") as f:
            f.write(json.dumps(d, default=str) + "\n")
        return d["record_id"]

    def append_many(self, table: str, records) -> list[str]:
        """Append many records, one file-open per monthly partition."""
        prepped = [self._prep(r) for r in records]
        buckets: dict[str, list[dict]] = {}
        for d in prepped:
            buckets.setdefault(self._month_for(d), []).append(d)
        for month, ds in buckets.items():
            with open(self._path(table, month), "a") as f:
                for d in ds:
                    f.write(json.dumps(d, default=str) + "\n")
        return [d["record_id"] for d in prepped]

    def supersede(self, table: str, old_record_id: str, new_record) -> str:
        """Append new_record with provenance.supersedes -> old_record_id."""
        d = self._prep(new_record)
        prov = d["provenance"]
        if prov.get("supersedes") and prov["supersedes"] != old_record_id:
            raise ValueError("new record already supersedes a different record")
        prov["supersedes"] = old_record_id
        # write back onto the dataclass so callers see the linkage
        try:
            new_record.provenance.supersedes = old_record_id
        except Exception:
            pass
        with open(self._path(table, self._month_for(d)), "a") as f:
            f.write(json.dumps(d, default=str) + "\n")
        return d["record_id"]

    def read(self, table: str, where: dict | None = None,
             months: list[str] | None = None) -> list[dict]:
        tdir = os.path.join(self.root, table)
        if not os.path.isdir(tdir):
            return []
        files = sorted(os.listdir(tdir))
        if months:
            files = [fn for fn in files if fn[:7] in months]
        out: list[dict] = []
        for fn in files:
            with open(os.path.join(tdir, fn)) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if where and not all(rec.get(k) == v for k, v in where.items()):
                        continue
                    out.append(rec)
        return out

    def current(self, table: str, where: dict | None = None) -> list[dict]:
        """Latest records only: drops anything superseded by another record."""
        recs = self.read(table, where=where)
        superseded = {r["provenance"]["supersedes"] for r in recs
                      if r.get("provenance", {}).get("supersedes")}
        return [r for r in recs if r.get("record_id") not in superseded]
