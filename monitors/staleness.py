"""Staleness monitoring.

Every feed declares a freshness contract. Breaches page someone — an untested,
unmonitored feed is decoration.
"""
from __future__ import annotations
from datetime import datetime, timezone
from store.canonical import CanonicalStore


def check(table: str, ts_field: str, max_age_hours: float,
          where: dict | None = None) -> dict:
    """Is the newest record in `table` fresher than the contract allows?"""
    recs = CanonicalStore().read(table, where=where)
    if not recs:
        return {"table": table, "ok": False, "reason": "no records"}
    latest = max(r.get(ts_field, "") for r in recs)
    try:
        ts = datetime.fromisoformat(str(latest).replace("Z", "+00:00"))
    except Exception:
        return {"table": table, "ok": False,
                "reason": f"unparseable timestamp {latest!r}"}
    age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
    return {"table": table, "ok": age_h <= max_age_hours,
            "latest": latest, "age_hours": round(age_h, 2)}
