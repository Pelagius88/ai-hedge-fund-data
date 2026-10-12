"""13F sweep analysis: CUSIP map (inverted from holdings), diff, join, rank.

OpenFIGI's anonymous tier no longer returns CUSIP (uniqueID) due to licensing,
so the ticker->CUSIP map is built INVERTED: from actual 13F holdings data
(issuer name + CUSIP as filed), joined to our tickers via SEC company names.
This is ground truth for this task — we join on the exact CUSIPs that appear
in 13F filings.

Stdlib only.
"""
from __future__ import annotations
import json
import os
import re

REPO = "/home/hatch/workspace/ai-hedge-fund-data"

# Filers that are pure indexers — their flows are index flows, not active calls.
# Tagged, not excluded; matched against EDGAR-returned filer names.
INDEXER_PATTERNS = (
    "VANGUARD", "BLACKROCK", "STATE STREET", "STATE ST",
    "FIDELITY", "SCHWAB", "INVESCO", "NORTHERN TRUST",
    "BANK OF NEW YORK MELLON", "BNY MELLON", "DIMENSIONAL",
    "GEODE CAPITAL",
)

# Well-known active managers, tagged for the "famous funds" view.
# Identity is NEVER asserted beyond the EDGAR-returned filer name shown.
FAMOUS_PATTERNS = (
    "BERKSHIRE HATHAWAY", "PERSHING SQUARE", "BAUPOST", "ELLIOTT",
    "THIRD POINT", "GREENLIGHT", "APPALOOSA", "TIGER GLOBAL",
    "LONE PINE", "COATUE", "RENAISSANCE TECHNOLOGIES",
    "TWO SIGMA", "D. E. SHAW", "DE SHAW", "CITADEL",
    "MILLENNIUM MANAGEMENT", "POINT72", "DUQUESNE",
    "SOROS FUND", "BRIDGEWATER", "VIKING GLOBAL",
    "EGERTON", "AKO CAPITAL", "TCI FUND", "CHILDREN'S INVESTMENT",
    "VALUEACT", "TRIAN FUND", "STARBOARD VALUE", "JANA PARTNERS",
    "DANIEL LOEB", "BILL ACKMAN", "WARREN BUFFETT",
    "SEQUOIA CAPITAL", "ANDREESSEN", "a16z",
)


US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN "
    "MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA "
    "WI WV WY".split())
# country/region codes SEC appends to foreign titles
COUNTRY_CODES = frozenset(
    "UK GB IE CH DE FR NL ES IT JP CN HK SG AU CA MX BR KY BM VG JE GG IM".split())


def normalize_name(name: str) -> str:
    """Normalize a company/issuer name for matching."""
    n = name.upper()
    n = re.sub(r"[^A-Z0-9 ]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    # SEC titles append state/country of incorporation ("DE", "UK", "NEW")
    toks = n.split()
    if toks and (toks[-1] in US_STATES or toks[-1] in COUNTRY_CODES
                 or toks[-1] == "NEW"):
        n = " ".join(toks[:-1])
    # strip trailing entity suffixes (one pass, longest first)
    for suf in (" CORPORATION", " INCORPORATED", " LIMITED", " HOLDINGS",
                " COMPANY", " GROUP", " PARTNERS", " CAPITAL",
                " INC", " CORP", " LLC", " LTD", " PLC", " CO",
                " LP", " LLP", " TRUST", " FUND", " ETF"):
        if n.endswith(suf):
            n = n[: -len(suf)].strip()
            break
    return n


def build_issuer_index(holdings_paths: list[str]) -> dict:
    """Index actual 13F holdings: normalized issuer name -> {cusip: info}."""
    index: dict = {}
    n_rows = 0
    for path in holdings_paths:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                for q in ("Q2", "Q1"):
                    qd = rec["quarters"].get(q)
                    if not qd:
                        continue
                    for h in qd["holdings"]:
                        n_rows += 1
                        norm = normalize_name(h["issuer"])
                        if not norm or not h["cusip"]:
                            continue
                        entry = index.setdefault(norm, {})
                        c = entry.setdefault(h["cusip"], {
                            "issuer": h["issuer"],
                            "classes": set(),
                            "count": 0,
                        })
                        c["classes"].add(h["titleOfClass"])
                        c["count"] += 1
    # sets -> sorted lists for JSON safety (done at save time)
    print(f"issuer index: {len(index)} names from {n_rows} holding rows", flush=True)
    return index


def build_cusip_map(issuer_index: dict, ticker_names: dict,
                    class_overrides: dict | None = None,
                    dominance: float = 10.0) -> tuple[dict, dict]:
    """Map tickers -> CUSIP via normalized names.

    Filers occasionally file wrong CUSIPs, so the winner is the CUSIP reported
    by the most holding rows — the true CUSIP dominates errors by orders of
    magnitude. A ticker is ambiguous only when the top two candidates are
    close (genuine dual-class shares) or no candidate exists.

    Returns (cusip_map {ticker: cusip}, ambiguous {ticker: [candidates]}).
    class_overrides: {ticker: cusip} for dual-class names, verified from data.
    """
    cusip_map: dict = {}
    ambiguous: dict = {}
    for ticker, info in ticker_names.items():
        norm = normalize_name(info["name"])
        entry = issuer_index.get(norm)
        if not entry:
            ambiguous[ticker] = []
            continue
        if class_overrides and ticker in class_overrides:
            cusip_map[ticker] = class_overrides[ticker]
            continue
        ranked_c = sorted(entry, key=lambda c: entry[c]["count"], reverse=True)
        top, top_n = ranked_c[0], entry[ranked_c[0]]["count"]
        second_n = entry[ranked_c[1]]["count"] if len(ranked_c) > 1 else 0
        if second_n == 0 or top_n >= dominance * second_n:
            cusip_map[ticker] = top
        else:
            ambiguous[ticker] = [
                {"cusip": c, "classes": sorted(entry[c]["classes"]),
                 "issuer": entry[c]["issuer"], "count": entry[c]["count"]}
                for c in ranked_c[:6]
            ]
    return cusip_map, ambiguous


def tag_filer(name: str) -> list[str]:
    """Tag a filer by EDGAR-returned name. Never asserts beyond the name."""
    n = name.upper()
    tags = []
    if any(p in n for p in INDEXER_PATTERNS):
        tags.append("indexer")
    if any(p in n for p in FAMOUS_PATTERNS):
        tags.append("famous")
    return tags


def join_and_rank(moves: list[dict], cusip_map: dict,
                  scope: dict | None = None) -> list[dict]:
    """Join moves to our tickers via CUSIP; rank by |delta value|.

    scope: {cik: (q1_total, q2_total)} — filers whose total 13F value changed
    by >4x are tagged 'scope_change' (filing-scope change, not real trading).
    """
    cusip_to_ticker = {c: t for t, c in cusip_map.items()}
    out = []
    for m in moves:
        t = cusip_to_ticker.get(m["cusip"])
        if not t:
            continue
        m = dict(m)
        m["ticker"] = t
        m["tags"] = tag_filer(m["filer_name"])
        if scope and m["cik"] in scope:
            q1t, q2t = scope[m["cik"]]
            # scope change: Q1 filing was a stub/notice (~$0) but Q2 is real,
            # or total value changed >4x either way
            if (q1t < 1_000_000 and q2t > 100_000_000) or \
               (q1t > 0 and (q2t / q1t > 4.0 or q2t / q1t < 0.25)):
                m["tags"].append("scope_change")
        out.append(m)
    out.sort(key=lambda m: abs(m["delta_value"]), reverse=True)
    return out
