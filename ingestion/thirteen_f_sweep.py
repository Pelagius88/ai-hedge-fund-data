"""Full-universe 13F sweep.

Discovers all 13F-HR filers in a filing window (via EDGAR daily master indexes),
pulls each filer's Q2-2026 and Q1-2026 holdings, diffs them, and flags
interesting moves (INITIATED / EXITED / ADD / TRIM).

Stdlib only. Politeness is mandatory: ~4 req/s max, declared-contact User-Agent,
exponential backoff on 429/403. Checkpointed so long runs can resume.

Usage:
    from ingestion.thirteen_f_sweep import discover_filers, run_sweep, diff_all
"""
from __future__ import annotations
import datetime as dt
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

UA = {"User-Agent": "AIHedgeFund-DataLayer/0.1 (firm research use; contact: research@example.org)"}
SEC = "https://www.sec.gov"
DATA = "https://data.sec.gov"

Q2_REPORT = "2026-06-30"
Q1_REPORT = "2026-03-31"
MIN_VALUE_CHANGE = 1_000_000  # $1M noise threshold
ADD_RATIO = 1.5               # shares +50% or more
TRIM_RATIO = 0.5              # shares -50% or more

_last_req = 0.0
POLITE_GAP = 0.25  # seconds between requests; raise when running parallel workers


def _polite(min_gap: float | None = None) -> None:
    global _last_req
    gap = POLITE_GAP if min_gap is None else min_gap
    delta = time.time() - _last_req
    if delta < gap:
        time.sleep(gap - delta)
    _last_req = time.time()


def _get(url: str, retries: int = 5) -> bytes:
    """Fetch via curl with a hard max-time.

    urllib's urlopen can hang FOREVER on a half-open connection through the
    local proxy (socket timeout never fires). curl --max-time is the fix.
    Raises urllib.error.HTTPError on 404 so callers can skip missing days.
    """
    backoff = 5
    for attempt in range(retries):
        _polite()
        t0 = time.time()
        try:
            p = subprocess.run(
                ["curl", "-sS", "--max-time", "60", "-A", UA["User-Agent"],
                 "-w", "\nHTTP_CODE:%{http_code}", url],
                capture_output=True, timeout=75)
        except subprocess.TimeoutExpired:
            print(f"DEBUG _get timeout attempt={attempt} url={url}", flush=True)
            if attempt < retries - 1:
                time.sleep(backoff)
                backoff *= 3
                continue
            raise RuntimeError(f"curl timeout: {url}")
        dt_taken = time.time() - t0
        body, _, code_line = p.stdout.rpartition(b"\nHTTP_CODE:")
        try:
            code = int(code_line.strip())
        except ValueError:
            code = 0
        if code == 200:
            return body
        if code in (429, 403) and attempt < retries - 1:
            time.sleep(backoff)
            backoff *= 3
            continue
        raise urllib.error.HTTPError(url, code or 599, f"HTTP {code}", None, None)
    raise RuntimeError(f"failed after {retries} retries: {url}")


def _strip_ns(tag: str) -> str:
    return re.sub(r"^\{.*\}", "", tag)


# ----------------------------------------------------------------------------
# Filer discovery via EDGAR daily master indexes
# ----------------------------------------------------------------------------

def _business_days(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


def _parse_index_lines(text: str, filers: dict) -> int:
    """Parse pipe-delimited master index text, collecting 13F-HR filers."""
    n = 0
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) != 5:
            continue
        cik, name, form, filed, path = parts
        if form.strip() != "13F-HR":
            continue
        cik10 = cik.strip().zfill(10)
        entry = filers.setdefault(cik10, {"name": name.strip(), "filings": []})
        entry["filings"].append((filed.strip(), path.strip()))
        n += 1
    return n


def discover_filers_quarterly(year: int, quarters: list[int],
                              extra_days: list[dt.date] | None = None,
                              workdir: str | None = None) -> dict:
    """All 13F-HR filers from quarterly master indexes (+ optional daily files).

    The quarterly master.idx is one ~30MB download per quarter — far cheaper
    than ~70 daily files, and immune to individual missing days (S3 returns
    403 AccessDenied for some daily indexes instead of 404).
    """
    filers: dict = {}
    for qtr in quarters:
        url = f"{SEC}/Archives/edgar/full-index/{year}/QTR{qtr}/master.idx"
        print(f"discover: fetching {url}", flush=True)
        raw = _get(url)
        if workdir:
            os.makedirs(workdir, exist_ok=True)
            with open(os.path.join(workdir, f"master-{year}-Q{qtr}.idx"), "wb") as f:
                f.write(raw)
        n = _parse_index_lines(raw.decode("utf-8", "replace"), filers)
        print(f"discover: QTR{qtr} -> {n} 13F-HR lines", flush=True)
    for day in (extra_days or []):
        qtr = (day.month - 1) // 3 + 1
        stamp = day.strftime("%Y%m%d")
        url = f"{SEC}/Archives/edgar/daily-index/{day.year}/QTR{qtr}/master.{stamp}.idx"
        try:
            raw = _get(url)
        except urllib.error.HTTPError as e:
            if e.code in (404, 403):
                print(f"discover: {stamp} missing ({e.code}), skipped", flush=True)
                continue
            raise
        if workdir:
            os.makedirs(workdir, exist_ok=True)
            with open(os.path.join(workdir, f"master.{stamp}.idx"), "wb") as f:
                f.write(raw)
        n = _parse_index_lines(raw.decode("utf-8", "replace"), filers)
        print(f"discover: {stamp} -> {n} 13F-HR lines", flush=True)
    print(f"discover: {len(filers)} distinct 13F-HR filers", flush=True)
    return filers


def discover_filers(start: dt.date, end: dt.date, idx_dir: str | None = None) -> dict:
    """All 13F-HR filers in [start, end] from daily master indexes.

    Returns {cik10: {"name": str, "filings": [(date_filed, accession_path)]}}.
    idx_dir optionally caches the .idx files.
    """
    filers: dict = {}
    n_days = n_ok = 0
    for day in _business_days(start, end):
        n_days += 1
        qtr = (day.month - 1) // 3 + 1
        stamp = day.strftime("%Y%m%d")
        url = f"{SEC}/Archives/edgar/daily-index/{day.year}/QTR{qtr}/master.{stamp}.idx"
        cached = (os.path.join(idx_dir, f"master.{stamp}.idx")
                  if idx_dir else None)
        try:
            if cached and os.path.exists(cached):
                with open(cached, "rb") as f:
                    raw = f.read()
            else:
                raw = _get(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:  # holiday / no filings
                continue
            raise
        n_ok += 1
        if cached and not os.path.exists(cached):
            os.makedirs(idx_dir, exist_ok=True)
            with open(cached, "wb") as f:
                f.write(raw)
        text = raw.decode("utf-8", "replace")
        for line in text.splitlines():
            parts = line.split("|")
            if len(parts) != 5:
                continue
            cik, name, form, filed, path = parts
            if form.strip() != "13F-HR":
                continue
            cik10 = cik.strip().zfill(10)
            entry = filers.setdefault(cik10, {"name": name.strip(), "filings": []})
            entry["filings"].append((filed.strip(), path.strip()))
    print(f"discover: {n_ok}/{n_days} index days ok, {len(filers)} distinct 13F-HR filers",
          flush=True)
    return filers


# ----------------------------------------------------------------------------
# Per-filer quarter filings + holdings
# ----------------------------------------------------------------------------

def quarter_filings(cik: str) -> dict:
    """The filer's 13F-HR filings for Q2-2026 and Q1-2026 by report date."""
    cik10 = cik.zfill(10)
    sub = json.loads(_get(f"{DATA}/submissions/CIK{cik10}.json"))
    name = sub.get("name", "")
    recent = sub["filings"]["recent"]
    out = {"name": name, "Q2": None, "Q1": None}
    for i in range(len(recent["accessionNumber"])):
        if recent["form"][i] != "13F-HR":
            continue
        rd = recent["reportDate"][i]
        key = "Q2" if rd == Q2_REPORT else ("Q1" if rd == Q1_REPORT else None)
        if key and out[key] is None:
            out[key] = {
                "accession": recent["accessionNumber"][i],
                "filed": recent["filingDate"][i],
                "report_date": rd,
            }
    return out


def filing_holdings(cik: str, filing: dict) -> list[dict]:
    """Parse holdings for one filing. Returns dicts with titleOfClass."""
    cik_num = str(int(cik.zfill(10)))
    acc = filing["accession"].replace("-", "")
    idx = json.loads(_get(f"{SEC}/Archives/edgar/data/{cik_num}/{acc}/index.json"))
    info_url = None
    for item in idx["directory"]["item"]:
        name = item["name"]
        if not name.lower().endswith(".xml") or name == "primary_doc.xml":
            continue
        candidate = f"{SEC}/Archives/edgar/data/{cik_num}/{acc}/{name}"
        try:
            root = ET.fromstring(_get(candidate))
        except ET.ParseError:
            continue
        if _strip_ns(root.tag) == "informationTable":
            info_url = candidate
            info_root = root
            break
    if not info_url:
        raise ValueError(f"no informationTable XML for {filing['accession']}")
    holdings: list[dict] = []
    for table in info_root.iter():
        if _strip_ns(table.tag) != "infoTable":
            continue

        def text(name: str) -> str:
            el = next((c for c in table if _strip_ns(c.tag) == name), None)
            return el.text.strip() if el is not None and el.text else ""

        shares = 0
        for child in table:
            if _strip_ns(child.tag) == "shrsOrPrnAmt":
                for cc in child:
                    if _strip_ns(cc.tag) == "sshPrnamt":
                        shares = int((cc.text or "0").strip().replace(",", ""))
        holdings.append({
            "issuer": text("nameOfIssuer"),
            "cusip": text("cusip"),
            "titleOfClass": text("titleOfClass"),
            "value": int((text("value") or "0").replace(",", "")),
            "shares": shares,
        })
    return holdings


def aggregate(holdings: list[dict]) -> dict:
    """Aggregate multi-manager rows by CUSIP: {cusip: {issuer, class, shares, value}}."""
    agg: dict = {}
    for h in holdings:
        c = h["cusip"]
        if not c:
            continue
        a = agg.setdefault(c, {"issuer": h["issuer"],
                               "titleOfClass": h["titleOfClass"],
                               "shares": 0, "value": 0})
        a["shares"] += h["shares"]
        a["value"] += h["value"]
    return agg


# ----------------------------------------------------------------------------
# Checkpointed sweep
# ----------------------------------------------------------------------------

def _load_state(state_path: str) -> dict:
    if os.path.exists(state_path):
        with open(state_path) as f:
            return json.load(f)
    return {"done": [], "failed": {}}


def run_sweep(ciks: list[str], out_path: str, state_path: str,
              limit: int | None = None) -> dict:
    """Sweep Q2+Q1 holdings for each CIK, checkpointing progress.

    Writes one JSON line per filer to out_path. Resumable via state_path.
    Returns {"done": n, "failed": n}.
    """
    state = _load_state(state_path)
    done = set(state["done"])
    todo = [c for c in ciks if c not in done]
    if limit:
        todo = todo[:limit]
    print(f"sweep: {len(todo)} filers to do ({len(done)} already done)", flush=True)
    n_done = n_fail = 0
    with open(out_path, "a") as out:
        for i, cik in enumerate(todo):
            try:
                qf = quarter_filings(cik)
                rec = {"cik": cik, "filer_name": qf["name"], "quarters": {}}
                for q in ("Q2", "Q1"):
                    f = qf[q]
                    if not f:
                        rec["quarters"][q] = None
                        continue
                    rec["quarters"][q] = {
                        "accession": f["accession"],
                        "filed": f["filed"],
                        "report_date": f["report_date"],
                        "holdings": filing_holdings(cik, f),
                    }
                out.write(json.dumps(rec) + "\n")
                out.flush()
                state["done"].append(cik)
                n_done += 1
            except Exception as e:  # noqa: BLE001 - long sweep must not die
                state["failed"][cik] = f"{type(e).__name__}: {str(e)[:200]}"
                n_fail += 1
            if (i + 1) % 25 == 0:
                with open(state_path, "w") as f:
                    json.dump(state, f)
                print(f"sweep: {i + 1}/{len(todo)} done, {n_fail} failed", flush=True)
    with open(state_path, "w") as f:
        json.dump(state, f)
    print(f"sweep complete: {n_done} done, {n_fail} failed", flush=True)
    return {"done": n_done, "failed": n_fail}


# ----------------------------------------------------------------------------
# Diff
# ----------------------------------------------------------------------------

def diff_filer(cik: str, filer_name: str, q2agg: dict, q1agg: dict) -> list[dict]:
    """Flag INITIATED / EXITED / ADD / TRIM per CUSIP, Q2 vs Q1."""
    moves: list[dict] = []
    for cusip in set(q2agg) | set(q1agg):
        s2 = q2agg.get(cusip, {"shares": 0, "value": 0, "issuer": "", "titleOfClass": ""})
        s1 = q1agg.get(cusip, {"shares": 0, "value": 0})
        dv = s2["value"] - s1["value"]
        if abs(dv) < MIN_VALUE_CHANGE:
            continue
        if s1["shares"] == 0:
            kind = "INITIATED"
        elif s2["shares"] == 0:
            kind = "EXITED"
        elif s2["shares"] >= ADD_RATIO * s1["shares"]:
            kind = "ADD"
        elif s2["shares"] <= TRIM_RATIO * s1["shares"]:
            kind = "TRIM"
        else:
            continue
        moves.append({
            "cik": cik,
            "filer_name": filer_name,
            "cusip": cusip,
            "issuer": s2["issuer"] or s1["issuer"],
            "titleOfClass": s2["titleOfClass"],
            "kind": kind,
            "q1_shares": s1["shares"],
            "q2_shares": s2["shares"],
            "q1_value": s1["value"],
            "q2_value": s2["value"],
            "delta_value": dv,
        })
    return moves


def diff_all(holdings_path: str) -> tuple[list[dict], dict]:
    """Diff every filer in the swept JSONL. Returns (moves, stats)."""
    moves: list[dict] = []
    stats = {"filers": 0, "with_both_quarters": 0, "missing_q2": 0,
             "missing_q1": 0, "holdings_rows": 0}
    with open(holdings_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            stats["filers"] += 1
            q2 = rec["quarters"].get("Q2")
            q1 = rec["quarters"].get("Q1")
            if not q2:
                stats["missing_q2"] += 1
                continue
            if not q1:
                stats["missing_q1"] += 1
                continue
            stats["with_both_quarters"] += 1
            stats["holdings_rows"] += len(q2["holdings"]) + len(q1["holdings"])
            moves.extend(diff_filer(rec["cik"], rec["filer_name"],
                                    aggregate(q2["holdings"]),
                                    aggregate(q1["holdings"])))
    return moves, stats
