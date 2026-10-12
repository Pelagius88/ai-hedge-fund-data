"""Backup: daily snapshots of the hot store, with retention and restore.

Snapshots are timestamped tar.gz files of the store root:
    <BACKUP_ROOT>/daily/YYYY-MM-DD-HHMMSS.tar.gz
    <BACKUP_ROOT>/weekly/YYYY-MM-DD-HHMMSS.tar.gz   (promoted from a daily)

Retention: keep the 7 most recent daily snapshots and the 4 most recent
weekly snapshots. A weekly is promoted from the newest daily each Monday
(or on demand via promote_weekly()).

Restore unpacks a snapshot into a target directory. It never overwrites
the live store unless explicitly told to — the default target is a
sibling directory for verification.
"""
from __future__ import annotations

import datetime
import glob
import os
import shutil
import sys
import tarfile

sys.path.insert(0, "/home/hatch/workspace/ai-hedge-fund-data")

from store.canonical import CanonicalStore, STORE_ROOT

BACKUP_ROOT = os.environ.get(
    "FUND_DATA_BACKUP_ROOT",
    os.path.join(os.path.dirname(__file__), "..", "_backups"),
)
BACKUP_ROOT = os.path.abspath(BACKUP_ROOT)

KEEP_DAILY = 7
KEEP_WEEKLY = 4


def _stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%d-%H%M%S")


def _list(kind: str) -> list[str]:
    d = os.path.join(BACKUP_ROOT, kind)
    if not os.path.isdir(d):
        return []
    return sorted(glob.glob(os.path.join(d, "*.tar.gz")))


def snapshot(store_root: str | None = None, kind: str = "daily") -> str:
    """Snapshot the hot store. Returns the snapshot path."""
    src = os.path.abspath(store_root or CanonicalStore().root)
    if not os.path.isdir(src):
        raise FileNotFoundError(f"store not found: {src}")
    dest_dir = os.path.join(BACKUP_ROOT, kind)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{_stamp()}.tar.gz")
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(src, arcname="store")
    return dest


def prune() -> dict:
    """Enforce retention. Returns what was kept/removed."""
    result = {}
    for kind, keep in (("daily", KEEP_DAILY), ("weekly", KEEP_WEEKLY)):
        snaps = _list(kind)
        remove = snaps[:-keep] if len(snaps) > keep else []
        for p in remove:
            os.remove(p)
        result[kind] = {"kept": len(snaps) - len(remove),
                       "removed": len(remove)}
    return result


def promote_weekly() -> str | None:
    """Promote the newest daily snapshot to weekly. Returns new path or None."""
    dailies = _list("daily")
    if not dailies:
        return None
    src = dailies[-1]
    dest_dir = os.path.join(BACKUP_ROOT, "weekly")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(src))
    if os.path.exists(dest):
        return dest
    shutil.copy2(src, dest)
    return dest


def restore(snapshot_path: str, target: str | None = None,
            overwrite_live: bool = False) -> str:
    """Restore a snapshot. Default target is a sibling dir for verification.

    Set overwrite_live=True to restore directly over the live store —
    this is the disaster-recovery path. The live store is moved aside
    (not deleted) first: <store>.pre-restore-<stamp>.
    """
    if not os.path.isfile(snapshot_path):
        raise FileNotFoundError(f"snapshot not found: {snapshot_path}")
    live = os.path.abspath(CanonicalStore().root)
    if overwrite_live:
        backup_live = live + f".pre-restore-{_stamp()}"
        os.rename(live, backup_live)
        target = live
        print(f"live store moved aside to {backup_live}")
    target = os.path.abspath(target or (live + f".restored-{_stamp()}"))
    os.makedirs(target, exist_ok=True)
    with tarfile.open(snapshot_path, "r:gz") as tar:
        # Snapshot contains a single top-level "store/" dir.
        tar.extractall(target)
    inner = os.path.join(target, "store")
    if os.path.isdir(inner):
        # Flatten: move contents up one level, remove the wrapper.
        for name in os.listdir(inner):
            shutil.move(os.path.join(inner, name), os.path.join(target, name))
        os.rmdir(inner)
    return target


def backup_daily() -> dict:
    """One-shot daily backup: snapshot + prune. Returns a report."""
    path = snapshot(kind="daily")
    # Promote to weekly on Mondays.
    promoted = None
    if datetime.datetime.now(datetime.timezone.utc).weekday() == 0:
        promoted = promote_weekly()
    pruned = prune()
    size = os.path.getsize(path)
    return {"snapshot": path, "bytes": size, "promoted_weekly": promoted,
            "pruned": pruned}


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["backup", "prune", "restore", "list"])
    ap.add_argument("--snapshot", default=None)
    ap.add_argument("--target", default=None)
    ap.add_argument("--overwrite-live", action="store_true")
    args = ap.parse_args()
    if args.cmd == "backup":
        print(backup_daily())
    elif args.cmd == "prune":
        print(prune())
    elif args.cmd == "list":
        print("daily:", _list("daily"))
        print("weekly:", _list("weekly"))
    elif args.cmd == "restore":
        print(restore(args.snapshot, args.target,
                      overwrite_live=args.overwrite_live))


if __name__ == "__main__":
    main()
