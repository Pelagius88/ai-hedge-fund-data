"""Runner: sweep holdings for a shard of filer CIKs. Logs to sweep_<shard>.log.

Usage: python3 universe/run_sweep.py <shard> <nshards> [gap]
Each shard writes _store/thirteenf/holdings_<shard>.jsonl + sweep_state_<shard>.json.
Seeds done-CIKs from universe/done_seed.json (for restarts with new sharding).
Keep combined rate under ~5 req/s: workers * (1/gap) <= 5.
"""
import json
import sys

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")
import ingestion.thirteen_f_sweep as sw

if __name__ == "__main__":
    shard = int(sys.argv[1])
    nshards = int(sys.argv[2])
    gap = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
    sw.POLITE_GAP = gap
    base = "/home/hatch/workspace/ai-hedge-fund-data"
    filers = json.load(open(f"{base}/universe/filers_2026Q2.json"))
    ciks = sorted(filers)
    mine = [c for i, c in enumerate(ciks) if i % nshards == shard]
    # seed already-done CIKs from a previous sharding
    try:
        seed = set(json.load(open(f"{base}/universe/done_seed.json")))
    except FileNotFoundError:
        seed = set()
    state_path = f"{base}/_store/thirteenf/sweep_state_{shard}.json"
    try:
        st = json.load(open(state_path))
    except FileNotFoundError:
        st = {"done": [], "failed": {}}
    before = len(st["done"])
    st["done"] = sorted(set(st["done"]) | (seed & set(mine)))
    json.dump(st, open(state_path, "w"))
    print(f"SWEEP shard {shard}/{nshards}: {len(mine)} filers "
          f"({len(st['done']) - before} seeded done, gap={gap}s)", flush=True)
    sw.run_sweep(mine, f"{base}/_store/thirteenf/holdings_{shard}.jsonl", state_path)
    print(f"SWEEP shard {shard} DONE", flush=True)
