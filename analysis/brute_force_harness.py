"""
Eta — Brute-Force Harness.

Sweeps a declarative parameter grid against a raw corpus, joining each
(record, override) pair to the recorded market resolution and producing
a deterministic ranked CSV. The first row is the §9 live-equivalence
banner, also emitted via ``--help``.

CLI:
  ``python -m analysis.brute_force_harness --corpus <dir>
  --resolutions <path> --grid <yaml> --out <csv>``

Determinism rules (Eta-5):
  - Numeric values formatted via ``_csv_format_number()`` (no float
    ``repr()``).
  - Override-dict keys lex-sorted before flattening.
  - CSV row order: ``sorted(rows, key=(override_key_tuple, decision_id))``.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from analysis.policy_replayer import replay
from analysis.raw_snapshot_loader import iter_records


BANNER = (
    "# POLICY/DECISION REPLAY — NOT TRADE SIMULATION. "
    "Every numeric column is prefixed `policy_replay_` or "
    "`hypothetical_decision_`. No realized P&L is computed by this "
    "harness; live-equivalence boundary per docs/RAW_DECISION_SNAPSHOT_PLAN.md §9."
)


def _csv_format_number(val: Any) -> str:
    """Deterministic numeric formatting via Decimal."""
    if val is None:
        return ""
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int,)):
        return str(val)
    if isinstance(val, float):
        return str(Decimal(str(val)))
    return str(val)


def _load_grid(path: str) -> Dict[str, List[Any]]:
    """Load a declarative YAML grid file.

    Review-cycle fix: hard-require PyYAML; the prior ImportError-to-JSON
    silent format-switch was a Rule 1 unapproved fallback. Operators
    install ``pyyaml`` per ``requirements.txt`` before running the
    harness.
    """
    import yaml  # type: ignore  # noqa: E402 — hard dep
    text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise RuntimeError(
            f"grid file {path!r} did not parse to a dict (got {type(data).__name__})"
        )
    return data


def _expand_grid(grid: Dict[str, List[Any]]) -> Iterator[Dict[str, Any]]:
    """Eta-2: cartesian-product expansion of the declarative grid."""
    if not grid:
        yield {}
        return
    keys = sorted(grid.keys())
    value_lists = [grid[k] for k in keys]
    for combo in itertools.product(*value_lists):
        yield dict(zip(keys, combo))


def _load_resolutions(path: str) -> Dict[str, Any]:
    """Read the resolution joiner output into a dict indexed by condition_id."""
    out: Dict[str, Any] = {}
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        row = json.loads(line)
        cid = row.get("condition_id")
        if cid is not None:
            out[cid] = row.get("resolution")
    return out


def _override_key_tuple(override: Dict[str, Any]) -> Tuple[Tuple[str, str], ...]:
    """Lex-sorted tuple of (key, str(value)) for deterministic row ordering."""
    return tuple((k, _csv_format_number(override[k])) for k in sorted(override.keys()))


def _aggregate_row(
    override: Dict[str, Any],
    records: List[Dict[str, Any]],
    resolutions: Dict[str, Any],
) -> Dict[str, Any]:
    candidate = 0
    accepted = 0
    rejected = 0
    exception = 0
    win = 0
    loss = 0
    undecided = 0
    for record in records:
        candidate += 1
        result = replay(record, override)
        if result.exception is not None:
            exception += 1
            continue
        gates = record.get("gates") or []
        outcome = (gates[-1].get("inputs") or {}).get("outcome") if gates else None
        if outcome == "exception":
            exception += 1
            continue
        if result.decided_direction is None:
            rejected += 1
            continue
        accepted += 1
        market = record.get("market") or {}
        cid = market.get("condition_id")
        resolution = resolutions.get(cid)
        # Minimal win/loss heuristic — full Eta-3 logic per resolution
        # outcome will land in a follow-up commit.
        if not resolution:
            undecided += 1
            continue
        if isinstance(resolution, dict) and resolution.get("_unobservable"):
            undecided += 1
            continue
        # Review-cycle fix: accumulate (+=) rather than overwrite (=);
        # full directional win/loss matrix per Eta-3 (yes_won/no_won
        # crossed with long/short).
        yes_won = bool(resolution.get("yes_won"))
        no_won = bool(resolution.get("no_won"))
        if yes_won and result.decided_direction == "long":
            win += 1
        elif yes_won and result.decided_direction == "short":
            loss += 1
        elif no_won and result.decided_direction == "short":
            win += 1
        elif no_won and result.decided_direction == "long":
            loss += 1
        else:
            undecided += 1
    return {
        "override": json.dumps(override, sort_keys=True),
        "policy_replay_candidate_count": candidate,
        "policy_replay_accepted_count": accepted,
        "policy_replay_rejected_count": rejected,
        "policy_replay_exception_records": exception,
        "hypothetical_decision_win_count": win,
        "hypothetical_decision_loss_count": loss,
        "hypothetical_decision_undecided_count": undecided,
        "bot_mode_scope": "live_gate+shadow_policy+simulation",
    }


def run(
    corpus_dir: str,
    resolutions_path: str,
    grid_path: str,
    out_csv: str,
) -> int:
    grid = _load_grid(grid_path)
    resolutions = _load_resolutions(resolutions_path)
    records = list(iter_records(corpus_dir))
    rows: List[Dict[str, Any]] = []
    for override in _expand_grid(grid):
        rows.append(_aggregate_row(override, records, resolutions))
    rows.sort(key=lambda r: _override_key_tuple(json.loads(r["override"])))
    out = Path(out_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        fh.write(BANNER + "\n")
        if not rows:
            fh.write("\n")
            return 0
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            formatted = {k: _csv_format_number(v) for k, v in row.items()}
            writer.writerow(formatted)
    return len(rows)


def _cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=BANNER,
    )
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--resolutions", required=True)
    parser.add_argument("--grid", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    n = run(args.corpus, args.resolutions, args.grid, args.out)
    sys.stdout.write(f"wrote {n} row(s) to {args.out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
