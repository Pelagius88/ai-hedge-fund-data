"""Staleness monitoring + paging.

Blueprint rule: stale feeds page someone. Every feed declares a freshness
contract (max age). Breaches at the critical level write a Page record to the
canonical store (append-only; acks are supersedes) AND print to stdout, which
the cron/log layer picks up.

Status levels:
  ok       — age within the contract
  warning  — age past 75% of the contract (heads-up, no page)
  critical — age past the contract (pages)
  no_data  — the feed has never landed a record. Not paged: that's a build
             gap, not an operational staleness event. Tracked separately.

Only critical staleness pages, and only once per feed — an open
(unacknowledged) page suppresses duplicates until acknowledged.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")

from store.canonical import CanonicalStore
from store.schemas import Page, Provenance

_store = CanonicalStore()

# feed -> table, provenance-source matcher (None = any source in the table),
# and how many recent monthly partitions to scan (bounds the read).
FEEDS = {
    "nasdaq-bars": {"table": "bars", "source_match": "nasdaq-bars",
                    "recent_months": 4},
    "13f": {"table": "thirteen_f_moves", "source_match": None,
            "recent_months": 6},
    "uw-flow": {"table": "unusual_whales_flow", "source_match": None,
                "recent_months": 2},
    "news": {"table": "news", "source_match": None, "recent_months": 1},
    "filings": {"table": "filings", "source_match": None, "recent_months": 2},
}

# Per-source freshness contracts (hours). 13F is quarterly by nature.
CONTRACTS = {
    "nasdaq-bars": 24,
    "13f": 24 * 100,
    "uw-flow": 24,
    "news": 1,
    "filings": 24,
}

_WARN_FRACTION = 0.75


def _recent_months(n: int) -> list[str]:
    now = datetime.now(timezone.utc)
    out = []
    y, m = now.year, now.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


def _parse_ts(v: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def _latest_fetched_at(table: str, source_match: str | None,
                       recent_months: int) -> str | None:
    """Newest provenance.fetched_at across (a slice of) the table."""
    recs = _store.read(table, months=_recent_months(recent_months))
    latest = None
    for r in recs:
        prov = r.get("provenance") or {}
        if source_match and prov.get("source") != source_match:
            continue
        ts = _parse_ts(prov.get("fetched_at", ""))
        if ts and (latest is None or ts > latest):
            latest = ts
    return latest.isoformat() if latest else None


def check_feed(feed: str, max_age_hours: float) -> dict:
    """Freshness check for one feed. Returns age + status.

    Status: ok | warning | critical | no_data | unknown_feed.
    """
    spec = FEEDS.get(feed)
    if spec is None:
        return {"feed": feed, "status": "unknown_feed"}
    latest = _latest_fetched_at(spec["table"], spec["source_match"],
                                spec["recent_months"])
    if latest is None:
        return {"feed": feed, "status": "no_data",
                "reason": f"no records in {spec['table']}"}
    age_h = (datetime.now(timezone.utc) -
             _parse_ts(latest)).total_seconds() / 3600
    age_h = round(age_h, 2)
    if age_h > max_age_hours:
        status = "critical"
    elif age_h > max_age_hours * _WARN_FRACTION:
        status = "warning"
    else:
        status = "ok"
    return {"feed": feed, "status": status, "latest": latest,
            "age_hours": age_h, "max_age_hours": max_age_hours}


def check_all() -> list[dict]:
    """Check every contracted feed. Returns one result per feed."""
    return [check_feed(feed, CONTRACTS[feed]) for feed in FEEDS]


def open_pages() -> list[dict]:
    """Current pages that have not been acknowledged."""
    return [r for r in _store.current("pages")
            if not r.get("acknowledged", False)]


def _page(feed: str, severity: str, message: str) -> str:
    prov = Provenance(source="staleness-monitor")
    rec = Page(feed=feed, severity=severity, message=message,
               acknowledged=False, provenance=prov)
    return _store.append("pages", rec)


def page_on_stale(checks: list[dict] | None = None) -> list[dict]:
    """Page on critically stale feeds.

    Writes a Page record per critical feed (deduped against open pages) and
    prints a PAGE line to stdout for the cron/log layer. Returns the checks
    that resulted in a (new or existing) page.
    """
    checks = checks if checks is not None else check_all()
    already_open = {p.get("feed") for p in open_pages()}
    paged = []
    for c in checks:
        if c.get("status") != "critical":
            continue
        feed = c["feed"]
        if feed in already_open:
            paged.append({**c, "page": "suppressed_duplicate"})
            continue
        msg = (f"{feed} stale: last record {c['latest']} "
               f"({c['age_hours']}h old, contract {c['max_age_hours']}h)")
        page_id = _page(feed, "critical", msg)
        print(f"PAGE critical {feed}: {msg} [page_id={page_id}]", flush=True)
        already_open.add(feed)
        paged.append({**c, "page": page_id})
    return paged


def acknowledge(page_id: str) -> str:
    """Acknowledge a page via supersede. Returns the new record_id.

    Raises KeyError if the page doesn't exist, ValueError if already acked.
    """
    history = {r["record_id"]: r for r in _store.read("pages")}
    old = history.get(page_id)
    if old is None:
        raise KeyError(f"no such page: {page_id}")
    current_ids = {r["record_id"] for r in _store.current("pages")}
    if page_id not in current_ids or old.get("acknowledged"):
        raise ValueError(f"page already acknowledged: {page_id}")
    prov = Provenance(source="staleness-monitor", supersedes=page_id)
    new = Page(feed=old["feed"], severity=old["severity"],
               message=old["message"], acknowledged=True, provenance=prov)
    return _store.supersede("pages", page_id, new)


def main() -> None:
    checks = check_all()
    for c in checks:
        print(f"{c['feed']:12s} {c['status']:8s} "
              f"age={c.get('age_hours', '-')}h "
              f"contract={CONTRACTS[c['feed']]}h")
    paged = page_on_stale(checks)
    if paged and not any(p.get("page") == "suppressed_duplicate" for p in paged):
        print(f"{len(paged)} page(s) written")
    elif not paged:
        print("no critical staleness — no pages")


if __name__ == "__main__":
    main()
