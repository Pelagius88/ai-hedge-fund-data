"""Import the 13F quarterly moves file into the canonical store via append().

Source: universe/thirteen_f_moves_2026Q2.json (73,126 moves, Q2 vs Q1 2026).
Writes to table 'thirteen_f_moves' with full provenance. Idempotent-ish: every
run stamps its own fetched_at; dedupe by run provenance if re-importing.
"""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from store.canonical import CanonicalStore
from store.schemas import Provenance, ThirteenFMove

REPO = os.path.join(os.path.dirname(__file__), "..")
MOVES_FILE = os.path.join(REPO, "universe", "thirteen_f_moves_2026Q2.json")


def load_moves(path: str = MOVES_FILE) -> tuple[dict, list[dict]]:
    with open(path) as f:
        data = json.load(f)
    return data.get("meta", {}), data.get("moves", [])


def to_record(move: dict, meta: dict, fetched_at: str | None = None) -> ThirteenFMove:
    prov = Provenance(
        source=f"13f-sweep:{meta.get('quarter', '2026Q2')}:{move.get('cik', '')}",
    )
    if fetched_at:
        prov.fetched_at = fetched_at
    return ThirteenFMove(
        filer_cik=move.get("cik", ""),
        filer_name=move.get("filer_name", ""),
        quarter=meta.get("quarter", "2026Q2"),
        report_date=meta.get("report_date", "2026-06-30"),
        cusip=move.get("cusip", ""),
        issuer=move.get("issuer", ""),
        ticker=move.get("ticker"),
        title_of_class=move.get("titleOfClass"),
        kind=move.get("kind", ""),
        q1_shares=int(move.get("q1_shares") or 0),
        q2_shares=int(move.get("q2_shares") or 0),
        q1_value=int(move.get("q1_value") or 0),
        q2_value=int(move.get("q2_value") or 0),
        delta_value=int(move.get("delta_value") or 0),
        tags=list(move.get("tags") or []),
        provenance=prov,
    )


def import_moves(store: CanonicalStore | None = None,
                 path: str = MOVES_FILE) -> int:
    store = store or CanonicalStore()
    meta, moves = load_moves(path)
    records = [to_record(m, meta) for m in moves]
    store.append_many("thirteen_f_moves", records)
    return len(records)


def main() -> None:
    n = import_moves()
    print(f"imported {n} 13F moves into thirteen_f_moves")


if __name__ == "__main__":
    main()
