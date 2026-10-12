"""Append-only canonical store.

One JSONL file per table per month: <root>/<table>/YYYY-MM.jsonl
Records are dataclasses carrying Provenance. Raw is forever: records are never
rewritten, only superseded by newer records.
"""
from __future__ import annotations
import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone

STORE_ROOT = os.environ.get(
    "FUND_DATA_ROOT",
    os.path.join(os.path.dirname(__file__), "..", "_store"),
)


class CanonicalStore:
    def __init__(self, root: str = STORE_ROOT):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, table: str, month: str) -> str:
        d = os.path.join(self.root, table)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{month}.jsonl")

    def append(self, table: str, record) -> None:
        assert is_dataclass(record), "records must be dataclasses with provenance"
        d = asdict(record)
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        for key in ("date", "filed_at", "published_at", "asof"):
            if d.get(key):
                try:
                    month = str(d[key])[:7]
                    break
                except Exception:
                    pass
        with open(self._path(table, month), "a") as f:
            f.write(json.dumps(d, default=str) + "\n")

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
