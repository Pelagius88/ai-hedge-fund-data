"""Runner: discover 13F-HR filers for the Q2 2026 filing window.

Uses the Q3 2026 quarterly master index (one 27MB download) + October daily
indexes for late filers. Logs to discover.log.
"""
import datetime as dt
import json
import sys

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")
from ingestion.thirteen_f_sweep import discover_filers_quarterly

if __name__ == "__main__":
    oct_days = [dt.date(2026, 10, d) for d in (1, 2, 5, 6, 7, 8, 9)]
    filers = discover_filers_quarterly(
        2026, [3], extra_days=oct_days,
        workdir="/home/hatch/workspace/ai-hedge-fund-data/_store/thirteenf/idx")
    out = "/home/hatch/workspace/ai-hedge-fund-data/universe/filers_2026Q2.json"
    with open(out, "w") as f:
        json.dump({k: {"name": v["name"], "n_filings": len(v["filings"])}
                   for k, v in filers.items()}, f)
    print(f"DISCOVERY DONE: {len(filers)} filers -> {out}", flush=True)
