"""Runner phase 1: diff all swept filers, build issuer index, draft CUSIP map.

Outputs:
  universe/thirteen_f_ambiguous.json - tickers needing manual CUSIP resolution
  universe/cusip_map.json            - ticker -> CUSIP (unambiguous only)
  universe/thirteen_f_moves_raw.json - all diff moves (pre-join)
"""
import json
import os
import sys

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")
from ingestion.thirteen_f_sweep import diff_all
from ingestion.thirteen_f_analyze import (
    build_issuer_index, build_cusip_map, join_and_rank)

REPO = "/home/hatch/workspace/ai-hedge-fund-data"


def load_holdings_deduped(paths: list[str]) -> list[str]:
    """Load holdings lines, deduped by CIK (last write wins).

    Workers checkpoint state every 25 filers but write holdings per filer;
    a worker killed mid-checkpoint can leave a filer written but unmarked,
    causing a duplicate on resume. Dedupe here.
    """
    by_cik: dict = {}
    order: dict = {}
    n = 0
    for path in paths:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                by_cik[rec["cik"]] = line
                order[rec["cik"]] = n
                n += 1
    print(f"holdings: {n} lines, {len(by_cik)} unique filers", flush=True)
    return [by_cik[c] for c in sorted(by_cik, key=lambda c: order[c])]

if __name__ == "__main__":
    paths = [f"{REPO}/_store/thirteenf/holdings_0.jsonl",
             f"{REPO}/_store/thirteenf/holdings_1.jsonl",
             f"{REPO}/_store/thirteenf/holdings_2.jsonl",
             f"{REPO}/_store/thirteenf/holdings_3.jsonl"]
    paths = [p for p in paths if os.path.exists(p)]
    merged = f"{REPO}/_store/thirteenf/holdings_merged.jsonl"
    with open(merged, "w") as f:
        for line in load_holdings_deduped(paths):
            f.write(line + "\n")
    print(f"merged -> {merged}", flush=True)
    moves, stats = diff_all(merged)
    print("diff stats:", json.dumps(stats), flush=True)
    json.dump(moves, open(f"{REPO}/universe/thirteen_f_moves_raw.json", "w"))
    print(f"raw moves: {len(moves)}", flush=True)

    index = build_issuer_index([merged])
    # sets aren't JSON-serializable; convert for the saved copy
    ticker_names = json.load(open(f"{REPO}/universe/ticker_names.json"))
    cusip_map, ambiguous = build_cusip_map(index, ticker_names)
    json.dump(cusip_map, open(f"{REPO}/universe/cusip_map.json", "w"), indent=1)
    json.dump(ambiguous, open(f"{REPO}/universe/thirteen_f_ambiguous.json", "w"),
              indent=1, default=list)
    print(f"cusip_map: {len(cusip_map)} tickers, ambiguous: {len(ambiguous)}",
          flush=True)
    for t, cands in list(ambiguous.items())[:20]:
        print(f"  AMBIG {t}: {json.dumps(cands, default=list)[:200]}", flush=True)
