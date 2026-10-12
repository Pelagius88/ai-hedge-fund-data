# Data Layer Runbook

For the on-call operator (Mica). Each failure mode: what it looks like, what to check, how to recover. Tight by design — if it's not here, check the logs first.

## 1. Stale feed

**Looks like:** `monitors/health.py` shows a feed `critical`; a `PAGE critical <feed>` in stdout; a `Page` record in the `pages` table.

**Check:**
1. `python3 monitors/staleness.py` — which feed, how old.
2. Was it ever live? `no_data` means the feed was never ingested — that's a build gap, not an outage. Don't page on it.
3. For `nasdaq-bars`: did the daily refresh run? Check `_store/daily_refresh_log.jsonl` tail.

**Recover:**
1. Run the daily refresh: `python3 pipelines/daily_refresh.py`. It's idempotent and resumable — safe to re-run.
2. If the refresh fails on one symbol, the run continues; check the log for `status: failed` rows.
3. Acknowledge the page only after the feed is green again: `acknowledge(page_id)` in `monitors/staleness.py`. Acking doesn't fix anything — it just clears the alert.

## 2. Failed fetch (Nasdaq/API errors)

**Looks like:** `refresh_one` returns `status: failed`; recovery retries 3× with backoff, then writes a critical `Page`.

**Check:**
1. The error in the log — HTTP 429 (rate limit), timeout, or connection refused?
2. Is it one symbol or all? One symbol = bad ticker or delisting. All = API or network down (see §5).

**Recover:**
1. Single symbol: verify the ticker still trades. If delisted, remove from the coverage universe (Lab DB) — don't keep fetching a dead symbol forever.
2. Rate limit: the fetcher already sleeps 0.5s between tries and 0.3s politeness in the daily run. If 429s persist, lower `WORKERS` in `daily_refresh.py`.
3. Transient: just re-run. The state file resumes where it stopped.

## 3. Corrupt record

**Looks like:** JSON decode error reading a monthly partition; a test or consumer crashes on a specific file.

**Check:**
1. Which file: `_store/<table>/YYYY-MM.jsonl`, which line number.
2. Is it one bad line or a truncated file? (Truncation = crash mid-write.)

**Recover:**
1. One bad line: the store is append-only — do NOT edit the file in place. Append a corrected record via `supersede()` pointing at the bad record's ID, or if the line has no parseable ID, append a fresh record and note the bad line in the log.
2. Truncated file: re-fetch the affected symbol/date range and append. The idempotent append skips dates already present.
3. If the corruption is in a backup snapshot, fall back to the previous snapshot (see §5 of backup: 7 daily + 4 weekly retained).

## 4. Disk full

**Looks like:** writes fail with `OSError: [Errno 28]`; health shows store bytes not growing; backup fails.

**Check:**
1. `df -h` on the VM. The hot store is ~2GB; snapshots live in `_backups/`.
2. What's big: `_store/bars/` dominates. `_backups/` accumulates if prune isn't running.

**Recover:**
1. Immediate: run `python3 store/backup.py prune` to enforce retention (7 daily + 4 weekly).
2. Structural: run the cold-archive retention policy — `apply_retention_policy()` in `store/archive.py` moves filings >2y and news >1y to compressed cold storage. Bars are never archived (needed for backtests).
3. Never delete raw JSONL files by hand. Archive or snapshot-prune; the audit trail stays intact.

## 5. API down (Nasdaq unreachable)

**Looks like:** every symbol fails; `fetch_bars` returns `[]` across the board; recovery pages after 3 attempts.

**Check:**
1. Can you reach `api.nasdaq.com` at all? `curl -s -o /dev/null -w "%{http_code}" --max-time 15 https://api.nasdaq.com/api/quote/AAPL` — expect 200 (or 403 without headers; 000 = network down).
2. Is it DNS, the proxy, or Nasdaq? Try another host to isolate.

**Recover:**
1. Wait and re-run — the daily refresh is idempotent; missing days get picked up by the backfill sweep automatically.
2. Do NOT switch sources silently. If Nasdaq is down for >24h, the staleness contract pages (that's working as designed). A source switch is a design decision, not an ops improvisation — it changes provenance semantics.
3. While down: bars stay stale, everything else keeps working. Consumers read the last good data; nothing fabricates.

---

**Golden rules:**
- The store is append-only. Fix forward (supersede), never edit history.
- Every recovery action gets provenance. If it touched the store, it's a record.
- Re-running the daily refresh is always safe. When in doubt, re-run it.
- Pages are for humans. A page without a human reading it is just a log line — acknowledge only after the fix, never to silence the alert.
