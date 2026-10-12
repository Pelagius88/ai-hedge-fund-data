"""Runner phase 2: resolve ambiguous CUSIPs, join moves to tickers, rank, save.

Reads universe/cusip_map.json + thirteen_f_ambiguous.json (phase 1 output).
CLASS_OVERRIDES below are verified from observed (CUSIP, titleOfClass) pairs
in the actual 13F data — fill after inspecting the ambiguous dump.

Output: universe/thirteen_f_moves_2026Q2.json
"""
import json
import sys

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")
from ingestion.thirteen_f_analyze import join_and_rank

REPO = "/home/hatch/workspace/ai-hedge-fund-data"

# ticker -> CUSIP, verified from observed (CUSIP, titleOfClass) pairs
# in the actual 13F data — never from memory.
CLASS_OVERRIDES: dict[str, str] = {
    "GOOGL": "02079K305",  # Alphabet Class C (02079K107 = Class A)
    "FOX": "35137L105",    # Fox Class A (35137L204 = Class B)
    "LEN": "526057104",    # Lennar Class A (526057302 = Class B)
    "ARBE": "M1R95N100",   # ordinary shares (M1R95N118 = warrants)
    "DSX": "Y2066G104",    # common (Y2066G138 = warrants)
    "CORZ": "21874A106",   # common (others = warrants)
    "VAL": "G9460G101",    # common (G9460G119 = warrants)
    "JD": "47215P106",     # ADR (47215PAJ5 = converts)
    "BEN": "35473M105",    # Franklin Resources common (35473P* = ETFs)
    "CPA": "P31076105",    # case variant of p31076105
    "CRDO": "G25457105",   # case variant of g25457105
    "HAE": "405024100",    # common (405024AD2 = bond)
    "MKSI": "55306N104",   # common (55306NAB0 = bond)
    "MTH": "59001A102",    # common (59001ABF8 = bond)
    "TDOC": "87918A105",   # common (87918AAF2 = bond)
    "WOLF": "97785W106",   # common (977852AP7 = bond)
    "NUAI": "64428N109",   # dominant CUSIP
    "DHI": "23331A109",    # D R Horton (word-order name variant)
    "VLRS": "21240E105",   # Vuela/Volairis ADR
    "SMTOY": "865617203",  # Sumitomo Electric ADR
    "SKHY": "78392B206",   # SK hynix ADR (low confidence: n=1 in 13Fs)
}


def fmt_usd(v: int) -> str:
    a = abs(v)
    s = f"${a/1e9:.2f}B" if a >= 1e9 else f"${a/1e6:.1f}M" if a >= 1e6 else f"${a:,}"
    return ("-" if v < 0 else "+") + s


if __name__ == "__main__":
    cusip_map = json.load(open(f"{REPO}/universe/cusip_map.json"))
    ambiguous = json.load(open(f"{REPO}/universe/thirteen_f_ambiguous.json"))
    for t, c in CLASS_OVERRIDES.items():
        cusip_map[t] = c
        ambiguous.pop(t, None)
    moves = json.load(open(f"{REPO}/universe/thirteen_f_moves_raw.json"))

    # filer total 13F value per quarter, for scope-change detection
    scope: dict[str, tuple[int, int]] = {}
    for line in open(f"{REPO}/_store/thirteenf/holdings_merged.jsonl"):
        r = json.loads(line)
        totals = {}
        for q in ("Q1", "Q2"):
            qd = r["quarters"].get(q)
            totals[q] = sum(h["value"] for h in qd["holdings"]) if qd else 0
        scope[r["cik"]] = (totals["Q1"], totals["Q2"])
    print(f"scope computed for {len(scope)} filers", flush=True)

    ranked = join_and_rank(moves, cusip_map, scope)

    tickers_hit = sorted({m["ticker"] for m in ranked})
    out = {
        "meta": {
            "quarter": "2026Q2",
            "report_date": "2026-06-30",
            "compared_against": "2026Q1",
            "n_moves": len(ranked),
            "n_tickers_with_moves": len(tickers_hit),
            "n_tickers_mapped": len(cusip_map),
            "n_tickers_unresolved": len(ambiguous),
            "note": "13F = long-only US equity positions as of 6/30/2026, "
                    "filed by mid-Aug 2026 (45-day lag). No shorts, no futures.",
        },
        "moves": ranked,
    }
    with open(f"{REPO}/universe/thirteen_f_moves_2026Q2.json", "w") as f:
        json.dump(out, f)
    print(f"saved {len(ranked)} ranked moves on {len(tickers_hit)} tickers", flush=True)
    print(f"unresolved tickers: {len(ambiguous)}", flush=True)

    clean = [m for m in ranked if "scope_change" not in m["tags"]]
    scoped = [m for m in ranked if "scope_change" in m["tags"]]
    print(f"clean moves: {len(clean)}, scope-change moves: {len(scoped)}", flush=True)

    print("\n=== TOP 20 by |delta value| (excl. scope-change artifacts) ===", flush=True)
    for m in clean[:20]:
        print(f"{m['ticker']:8} {m['kind']:9} {fmt_usd(m['delta_value']):>10}  "
              f"{m['filer_name'][:45]}  [{','.join(m['tags'])}]", flush=True)

    print("\n=== TOP 15 NON-INDEXER (excl. scope-change) ===", flush=True)
    n = 0
    for m in clean:
        if "indexer" in m["tags"]:
            continue
        print(f"{m['ticker']:8} {m['kind']:9} {fmt_usd(m['delta_value']):>10}  "
              f"{m['filer_name'][:45]}  [{','.join(m['tags'])}]", flush=True)
        n += 1
        if n >= 15:
            break

    print("\n=== FAMOUS-FUND MOVES, NON-INDEXER (top 20) ===", flush=True)
    n = 0
    for m in clean:
        if "famous" not in m["tags"] or "indexer" in m["tags"]:
            continue
        print(f"{m['ticker']:8} {m['kind']:9} {fmt_usd(m['delta_value']):>10}  "
              f"{m['filer_name'][:45]}", flush=True)
        n += 1
        if n >= 20:
            break
    print(f"\ntotal famous-tagged clean moves: "
          f"{sum(1 for m in clean if 'famous' in m['tags'])}", flush=True)

    print("\n=== LARGEST SCOPE-CHANGE ARTIFACTS (filing scope, not trading) ===",
          flush=True)
    for m in scoped[:10]:
        print(f"{m['ticker']:8} {m['kind']:9} {fmt_usd(m['delta_value']):>10}  "
              f"{m['filer_name'][:45]}", flush=True)
