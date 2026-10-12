"""Corporate-action adjustment at ingestion.

Blueprint rule #3: corporate actions adjust history at ingestion, never in
each consumer's head.

Pipeline:
  detect (split-detector flags candidates, status="detected")
    -> confirm (human / nasdaq-ca / manual promotes to status="confirmed")
    -> adjust_bars (rewrites pre-action history via supersede(); old bars stay)

Nasdaq's historical feed is already split-adjusted, so real splits are rare in
this feed — but the pipeline must be correct for unadjusted feeds and for
manually confirmed actions. Dividend adjustment is subtractive on closes only
(documented choice): total-return continuity is what consumers need; O/H/L
are left raw and the limitation is explicit.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")

from store.canonical import CanonicalStore
from store.schemas import Bar, CorporateAction, Provenance

_store = CanonicalStore()


def _as_prov(provenance) -> Provenance:
    if isinstance(provenance, dict):
        return Provenance(**provenance)
    return provenance


def list_nasdaq_actions(symbol: str) -> list[dict]:
    """Nasdaq corporate-action lookup.

    No verified public api.nasdaq.com corporate-action endpoint exists, so
    this is an explicit seam returning []. Confirmation today comes from the
    split detector (status="detected" -> "confirmed") or manual entry.
    """
    return []


def confirm_action(
    symbol: str,
    action_type: str,
    date: str,
    ratio: float | None = None,
    amount: float | None = None,
    provenance: Provenance | dict | None = None,
    detection_id: str | None = None,
) -> str:
    """Promote a corporate action to confirmed.

    Writes a new CorporateAction(status="confirmed"). When detection_id is
    given, provenance.supersedes links the confirmation to the detection
    record, giving a full audit chain: detection -> confirmation -> bars.
    Confirmation sources: "split-detector" (reviewed), "nasdaq-ca", "manual".
    """
    if action_type == "split" and not ratio:
        raise ValueError("split requires ratio")
    if action_type == "dividend" and amount is None:
        raise ValueError("dividend requires amount")
    prov = _as_prov(provenance) if provenance else Provenance(source="manual")
    if detection_id:
        if prov.supersedes and prov.supersedes != detection_id:
            raise ValueError("provenance already supersedes a different record")
        prov.supersedes = detection_id
    rec = CorporateAction(
        symbol=symbol.upper(), date=date, action=action_type,
        ratio=ratio, amount=amount, status="confirmed", provenance=prov,
    )
    return _store.append("corporate_actions", rec)


def reject_detection(detection_id: str, provenance: Provenance | dict | None = None) -> str:
    """Mark a split-detector candidate as rejected (false positive)."""
    prov = _as_prov(provenance) if provenance else Provenance(source="manual")
    prov.supersedes = detection_id
    recs = _store.read("corporate_actions", where={"record_id": detection_id})
    if not recs:
        raise ValueError(f"detection {detection_id} not found")
    d = recs[0]
    rec = CorporateAction(
        symbol=d["symbol"], date=d["date"], action=d["action"],
        ratio=d.get("ratio"), amount=d.get("amount"),
        status="rejected", provenance=prov,
    )
    return _store.append("corporate_actions", rec)


def confirmed_actions(symbol: str) -> list[dict]:
    """All confirmed corporate actions for a symbol, oldest first."""
    recs = _store.read("corporate_actions", where={"symbol": symbol.upper()})
    return sorted(
        (r for r in recs if r.get("status", "detected") == "confirmed"),
        key=lambda r: r["date"],
    )


def _already_adjusted(symbol: str, action_id: str) -> bool:
    """Idempotency: has this action already been applied to current bars?"""
    marker = f"corp-action-adjustment:{action_id}"
    recs = _store.current("bars", where={"symbol": symbol.upper()})
    return any(r.get("provenance", {}).get("source") == marker for r in recs)


def adjust_bars(
    symbol: str,
    provenance: Provenance | dict | None = None,
) -> dict:
    """Apply all confirmed, not-yet-applied actions to the symbol's bars.

    For each confirmed action (oldest first):
      - split ratio r on date D: bars with date < D get O/H/L/C / r,
        volume * r.
      - dividend amount d on date D: bars with date < D get close - d.
        (O/H/L left raw — documented limitation; see module docstring.)
    Adjusted bars are written via supersede(): old bars stay readable via
    read(); current() returns only the adjusted series. Returns a summary.
    """
    sym = symbol.upper()
    base_prov = _as_prov(provenance) if provenance else None
    summary = {"symbol": sym, "actions_applied": 0, "bars_adjusted": 0,
               "skipped": []}
    for act in confirmed_actions(sym):
        action_id = act["record_id"]
        if _already_adjusted(sym, action_id):
            summary["skipped"].append(act["date"])
            continue
        bars = _store.current("bars", where={"symbol": sym})
        targets = [b for b in bars if b["date"] < act["date"]]
        if not targets:
            summary["skipped"].append(act["date"])
            continue
        marker = f"corp-action-adjustment:{action_id}"
        for b in targets:
            prov = Provenance(source=marker)
            if base_prov and base_prov.fetched_at:
                prov.fetched_at = base_prov.fetched_at
            if act["action"] == "split":
                r = act["ratio"]
                adj = Bar(
                    symbol=sym, date=b["date"],
                    open=b["open"] / r, high=b["high"] / r,
                    low=b["low"] / r, close=b["close"] / r,
                    volume=int(b["volume"] * r),
                    interval=b.get("interval", "1d"),
                    timestamp=b.get("timestamp"),
                    adjusted_close=b["close"] / r,
                    provenance=prov,
                )
            elif act["action"] == "dividend":
                d = act["amount"]
                adj = Bar(
                    symbol=sym, date=b["date"],
                    open=b["open"], high=b["high"], low=b["low"],
                    close=b["close"] - d,
                    volume=b["volume"],
                    interval=b.get("interval", "1d"),
                    timestamp=b.get("timestamp"),
                    adjusted_close=b["close"] - d,
                    provenance=prov,
                )
            else:
                continue  # merger/spinoff: no generic math; handled manually
            _store.supersede("bars", b["record_id"], adj)
            summary["bars_adjusted"] += 1
        summary["actions_applied"] += 1
    return summary
