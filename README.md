# AI Hedge Fund — Data Layer

The canonical data infrastructure for the firm. **One source of truth:** every number
in the firm traces to this store with provenance attached.

## Architecture

```
Sources → Ingestion → Canonical Store → Consumers
```

- **Sources:** Nasdaq (bars), SEC EDGAR (filings, 13F), news wires, brokers
  (Founders Book positions/P&L), paper ledger (AI Rotation — dashboard, no broker).
- **Ingestion:** scheduled, idempotent fetchers. Every record stamped with
  `source`, `fetched_at`, and what it `supersedes`.
- **Canonical store:** append-only raw log + curated tables. Raw is forever —
  never rewritten, only superseded.
- **Consumers:** Research, Risk, AI Rotation, Founders Book support.
  **One quote pipeline per symbol** — a single function every consumer calls.
  A second copy of the same data is a bug.

## Layout

| Dir | What |
|---|---|
| `ingestion/` | Fetchers, one per source. Contracts defined; existing sweep scripts migrate here. |
| `store/` | Schemas + the append-only canonical store. |
| `pipelines/` | The single quote pipeline every consumer calls. |
| `monitors/` | Staleness + data-quality checks. |
| `tests/` | Store round-trip and parser tests. |

## Build order

1. Canonical store + schemas ✅
2. One quote pipeline per symbol (read path live; ingestion migrates in)
3. **13F ingestion** ✅ v1 fetcher included — holdings tell you a lot
4. Corporate-action adjustment at ingestion
5. Staleness monitoring + backfill discipline
6. Cold archive for raw filings and old news

## 13F tracking

`ingestion/thirteen_f.py` lists a filer's 13F-HR filings from EDGAR and parses the
holdings table: issuer, CUSIP, value, shares. Tracked quarterly (due 45 days after
quarter-end). Watch for: new buys, adds, full exits, concentration shifts.

## Status

This repo is the new home. Existing sweep scripts (Nasdaq bars, SEC filings, news
crawl) migrate in rather than being duplicated. See each module's docstring.
