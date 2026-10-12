"""Failure recovery: retries with exponential backoff, partial-progress resume,
and paging on final failure.

The daily refresh is resumable by design (per-symbol state file), so a retry
never restarts from zero — it picks up where the last attempt left off.
"""
from __future__ import annotations

import functools
import sys
import time
import traceback

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")

from store.canonical import CanonicalStore
from store.schemas import Page, Provenance

_store = CanonicalStore()

MAX_ATTEMPTS = 3
BASE_DELAY_S = 30  # doubles each attempt: 30s, 60s, 120s


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def with_retries(fn, *, attempts: int = MAX_ATTEMPTS,
                 base_delay: float = BASE_DELAY_S,
                 op_name: str = "operation"):
    """Run fn() with exponential-backoff retries.

    Returns (result, attempts_used). Raises the last exception after all
    attempts are exhausted — and writes a critical Page record first.
    """
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            result = fn()
            return result, attempt
        except Exception as e:  # noqa: BLE001 — we retry everything, then page
            last_exc = e
            if attempt < attempts:
                delay = base_delay * (2 ** (attempt - 1))
                print(f"[recovery] {op_name}: attempt {attempt}/{attempts} "
                      f"failed ({e}); retrying in {delay:.0f}s", flush=True)
                time.sleep(delay)
            else:
                print(f"[recovery] {op_name}: FAILED after {attempts} attempts",
                      flush=True)
    # Final failure — page. Append-only: a Page record, never an edit.
    msg = (f"{op_name} failed after {attempts} attempts. "
           f"Last error: {last_exc}. Traceback: "
           f"{traceback.format_exception(type(last_exc), last_exc, last_exc.__traceback__)[-1][:500]}")
    prov = Provenance(source="recovery", fetched_at=_now_iso())
    page_id = _store.append("pages", Page(
        feed=op_name, severity="critical", message=msg,
        acknowledged=False, provenance=prov))
    print(f"PAGE critical {op_name}: {last_exc} (page_id={page_id})", flush=True)
    raise last_exc


def retryable(*, attempts: int = MAX_ATTEMPTS,
              base_delay: float = BASE_DELAY_S,
              op_name: str | None = None):
    """Decorator form of with_retries."""
    def deco(fn):
        name = op_name or fn.__name__

        @functools.wraps(fn)
        def wrapper(*a, **kw):
            result, _ = with_retries(lambda: fn(*a, **kw),
                                     attempts=attempts,
                                     base_delay=base_delay,
                                     op_name=name)
            return result
        return wrapper
    return deco
