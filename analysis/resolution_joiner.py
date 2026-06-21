"""
Epsilon — Resolution Joiner

Iterates the raw corpus, collects distinct ``condition_id`` values, and
fetches Gamma-resolution snapshots via ``analysis.gamma_resolution``
(Alpha-4 shared helpers; per-caller policy preserved). Appends one
resolution line per ``(condition_id, fetched_at)`` tuple. Idempotent
re-runs: an existing resolution file is read first and only new tuples
are appended.

CLI: ``python -m analysis.resolution_joiner --corpus <dir> --out <path>``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from analysis.raw_snapshot_loader import iter_records


def _existing_tuples(out_path: Path) -> Set[Tuple[str, str]]:
    # Review-cycle fix: removed the try/except that swallowed
    # JSONDecodeError on malformed rows — a malformed prior-run line
    # was silently treated as "not seen", producing duplicate appends.
    # Let json.JSONDecodeError propagate; the operator repairs the file.
    seen: Set[Tuple[str, str]] = set()
    if not out_path.exists():
        return seen
    for line in out_path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        rec = json.loads(line)
        seen.add((str(rec["condition_id"]), str(rec["fetched_at"])))
    return seen


def _collect_condition_ids(corpus_dir: str) -> List[str]:
    seen: Set[str] = set()
    for record in iter_records(corpus_dir):
        market = record.get("market") or {}
        cid = market.get("condition_id")
        if isinstance(cid, str) and cid:
            seen.add(cid)
    return sorted(seen)


def join_resolutions(
    corpus_dir: str,
    out_path: str,
    *,
    fetcher=None,
    now: Optional[datetime] = None,
) -> int:
    """Append-only join. Returns the number of new rows written."""
    if fetcher is None:
        # Default to the closed-only Gamma resolver (Alpha-4 shared helper).
        from analysis.gamma_resolution import fetch_closed_resolution
        fetcher = fetch_closed_resolution
    if now is None:
        now = datetime.now(timezone.utc)
    out = Path(out_path)
    if not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)

    seen = _existing_tuples(out)
    written = 0
    fetched_at = now.isoformat()
    for cid in _collect_condition_ids(corpus_dir):
        key = (cid, fetched_at)
        if key in seen:
            continue
        # Review-cycle fix: removed the try/except that silently
        # substituted a `{"_unobservable": True, "reason":
        # "gamma_fetch_failed"}` row on fetcher errors (Rule 1
        # unapproved fallback). The joiner now fail-stops; the operator
        # diagnoses the upstream issue and re-runs.
        resolution = fetcher(cid)
        line = {
            "condition_id": cid,
            "fetched_at": fetched_at,
            "resolution": resolution,
        }
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        written += 1
    return written


def _cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Join market resolutions to a raw decision corpus."
    )
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    n = join_resolutions(args.corpus, args.out)
    sys.stdout.write(f"wrote {n} new resolution row(s) to {args.out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
