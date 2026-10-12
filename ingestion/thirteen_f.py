"""13F-HR ingestion (v1).

Lists a filer's 13F-HR filings from EDGAR and parses the holdings table
(infoTable XML): issuer name, CUSIP, value ($ thousands), shares.

Why 13Fs: they show what institutions actually hold — new buys, adds, full
exits, concentration shifts. Filed quarterly, due 45 days after quarter-end.

Usage:
    from ingestion.thirteen_f import latest_holdings
    holdings = latest_holdings("1067983")   # Berkshire Hathaway
    for h in holdings[:5]:
        print(h.issuer, h.value_usd_thousands, h.shares)
"""
from __future__ import annotations
import json
import re
import urllib.request
import xml.etree.ElementTree as ET

from store.schemas import ThirteenFHolding, Provenance

# Declared-contact UA: SEC gates bare tool UAs with 403 on www.sec.gov/Archives.
UA = {"User-Agent": "AIHedgeFund-DataLayer/0.1 (firm research use; contact: research@example.org)"}
SEC = "https://www.sec.gov"
DATA = "https://data.sec.gov"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _cik10(cik: str) -> str:
    return cik.strip().lstrip("0").zfill(10) or "0000000000"


def _quarter(report_date: str) -> str:
    y, m = report_date.split("-")[:2]
    return f"{y}-Q{(int(m) - 1) // 3 + 1}"


def list_13f_filings(cik: str) -> list[dict]:
    """All 13F-HR filings in the filer's recent submissions window."""
    cik10 = _cik10(cik)
    sub = json.loads(_get(f"{DATA}/submissions/CIK{cik10}.json"))
    name = sub.get("name", "")
    recent = sub["filings"]["recent"]
    out: list[dict] = []
    for i in range(len(recent["accessionNumber"])):
        if recent["form"][i] == "13F-HR":
            out.append({
                "filer_name": name,
                "accession": recent["accessionNumber"][i],
                "filed": recent["filingDate"][i],
                "report_date": recent["reportDate"][i],
            })
    return out


def _strip_ns(tag: str) -> str:
    return re.sub(r"^\{.*\}", "", tag)


def holdings_for_filing(cik: str, filing: dict) -> list[ThirteenFHolding]:
    cik_num = str(int(_cik10(cik)))
    acc = filing["accession"].replace("-", "")
    idx = json.loads(_get(f"{SEC}/Archives/edgar/data/{cik_num}/{acc}/index.json"))
    # The holdings doc isn't reliably named ("infoTable" in filename is a myth
    # for newer filings) — identify it by root element instead.
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
            break
    if not info_url:
        raise ValueError(f"no informationTable XML found for {filing['accession']}")
    root = ET.fromstring(_get(info_url))
    quarter = _quarter(filing["report_date"])
    holdings: list[ThirteenFHolding] = []
    for table in root.iter():
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
        holdings.append(ThirteenFHolding(
            filer_cik=_cik10(cik),
            filer_name=filing.get("filer_name", ""),
            quarter=quarter,
            issuer=text("nameOfIssuer"),
            cusip=text("cusip"),
            value_usd=int((text("value") or "0").replace(",", "")),
            shares=shares,
            provenance=Provenance(source=f"sec-edgar:13F-HR:{filing['accession']}"),
        ))
    return holdings


def latest_holdings(cik: str) -> list[ThirteenFHolding]:
    """Holdings from the filer's most recent 13F-HR."""
    filings = list_13f_filings(cik)
    if not filings:
        return []
    return holdings_for_filing(cik, filings[0])
