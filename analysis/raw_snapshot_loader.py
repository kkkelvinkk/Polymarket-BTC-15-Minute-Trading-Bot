"""
Delta — Offline loader and schema validator for raw_decisions_*.jsonl.

Public API:
  - ``iter_records(corpus_dir)``: streams ``dict`` records from every
    ``raw_decisions_*.jsonl`` under the directory (capture-time order).
  - ``validate_record(record)``: enforces the §6.4 Delta-7 invariants
    (a)–(i); raises ``RawSnapshotInvariantViolation`` with a precise
    message naming the failing rule.
  - ``SidecarResolver``: indexes content-addressed sidecars and resolves
    ``_body_ref`` references on load.

CLI: ``python -m analysis.raw_snapshot_loader --validate <dir>``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional


# Closed enum of accepted outcomes (§6.4 Delta-7 (b)).
_ACCEPTED_OUTCOMES = {"accepted", "rejected", "exception"}

# §4.4 keys required on accepted final_decision.output (Delta-7 (d)).
_FINAL_DECISION_ACCEPT_KEYS = (
    "selected_side",
    "selected_token_id",
    "submitted_limit_price",
    "accepted_limit_price",
    "limit_order_token_qty",
    "fusion_direction",
    "fusion_confidence",
)

# §4.5 sentinel used for rejected/exception final_decision.output.
_UNOBSERVABLE_FINAL_REJECTED = {
    "_unobservable": True,
    "reason": "final_decision_not_accepted",
}


class RawSnapshotInvariantViolation(RuntimeError):
    """Raised when a record fails any §6.4 Delta-7 invariant."""


class SidecarResolver:
    """Indexes ``<dir>/sidecar/<sha256>`` files and resolves ``_body_ref``."""

    def __init__(self, corpus_dir: str):
        self._sidecar_root = Path(corpus_dir) / "sidecar"
        self._index: Dict[str, Path] = {}
        if self._sidecar_root.is_dir():
            for path in self._sidecar_root.iterdir():
                if path.is_file():
                    self._index[path.name] = path

    def resolve(self, body_ref: Dict[str, Any]) -> Any:
        """Resolve a ``{"_body_ref": "<sha256>"}`` reference.

        Raises ``RawSnapshotInvariantViolation`` on missing sidecar OR
        on sha256 mismatch (catches truncated or replaced sidecars).
        """
        if not isinstance(body_ref, dict) or "_body_ref" not in body_ref:
            raise RawSnapshotInvariantViolation(
                f"resolve called on non-body_ref dict: {body_ref!r}"
            )
        digest = body_ref["_body_ref"]
        path = self._index.get(digest)
        if path is None:
            raise RawSnapshotInvariantViolation(
                f"sidecar {digest!r} not found in {self._sidecar_root}"
            )
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != digest:
            raise RawSnapshotInvariantViolation(
                f"sidecar {digest!r} sha256 mismatch (got {actual!r})"
            )
        return json.loads(data.decode("utf-8"))


_LOADER_TRUNCATED_TAILS: List[Path] = []
"""Module-level observability list: every truncated-tail drop appends
its source path so the CLI summary can report N drops (review-cycle
fix: silent drop with no log violated Rule 1 spirit)."""


def _iter_lines_truncation_tolerant(path: Path) -> Iterator[str]:
    """Delta-5: tolerate a single truncated trailing line.

    Yields fully-terminated lines; a final unterminated tail is dropped
    AND recorded in ``_LOADER_TRUNCATED_TAILS`` so the CLI surface reports
    the event (review-cycle fix: prior silent drop had no observability).
    """
    with path.open("rb") as f:
        buf = f.read()
    if not buf:
        return
    lines = buf.split(b"\n")
    last = lines[-1]
    if last:
        # Truncated tail: drop it AND record the source for the CLI summary.
        _LOADER_TRUNCATED_TAILS.append(path)
        lines = lines[:-1]
    else:
        # Proper trailing \n: split() produced an empty final element; drop.
        lines = lines[:-1]
    for line in lines:
        if line:
            yield line.decode("utf-8")


def iter_records(corpus_dir: str) -> Iterator[Dict[str, Any]]:
    """Stream all records from ``raw_decisions_*.jsonl`` files under ``corpus_dir``.

    Capture-time order is preserved (Eta-5 invariant): files are visited
    in lexicographic name order (the date suffix is monotonic so this
    matches capture chronology), and lines within each file are yielded
    in arrival order.

    Review-cycle fix: clears ``_LOADER_TRUNCATED_TAILS`` at entry so a
    repeated call within the same process (e.g., the brute-force
    harness iterating after the joiner) starts with a clean per-call
    observability list.
    """
    _LOADER_TRUNCATED_TAILS.clear()
    root = Path(corpus_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"corpus dir does not exist: {corpus_dir!r}")
    for path in sorted(root.glob("raw_decisions_*.jsonl")):
        for line in _iter_lines_truncation_tolerant(path):
            yield json.loads(line)


def validate_record(record: Dict[str, Any]) -> None:
    """Enforce §6.4 Delta-7 invariants (a)–(i) on a single record.

    Raises ``RawSnapshotInvariantViolation`` on the first failure.
    """
    gates = record.get("gates")
    # (a) trailing final_decision
    if not isinstance(gates, list) or not gates:
        raise RawSnapshotInvariantViolation(
            "Delta-7(a): record has no gates"
        )
    if gates[-1].get("name") != "final_decision":
        raise RawSnapshotInvariantViolation(
            f"Delta-7(a): gates[-1].name != 'final_decision' "
            f"(got {gates[-1].get('name')!r})"
        )

    # (c) exactly one final_decision
    final_count = sum(1 for g in gates if g.get("name") == "final_decision")
    if final_count != 1:
        raise RawSnapshotInvariantViolation(
            f"Delta-7(c): expected exactly one final_decision row, found {final_count}"
        )

    final = gates[-1]
    inputs = final.get("inputs") or {}
    outcome = inputs.get("outcome")
    # (b) outcome in closed enum
    if outcome not in _ACCEPTED_OUTCOMES:
        raise RawSnapshotInvariantViolation(
            f"Delta-7(b): outcome {outcome!r} not in {_ACCEPTED_OUTCOMES}"
        )

    # Review-cycle fix: trailing-row passed flag MUST match outcome
    # (passed=True iff outcome=="accepted") per §4.4 universal-trailing
    # semantics.
    expected_passed = (outcome == "accepted")
    if final.get("passed") != expected_passed:
        raise RawSnapshotInvariantViolation(
            f"Delta-7 trailing-row consistency: passed={final.get('passed')!r} "
            f"does not match outcome={outcome!r} (expected passed={expected_passed!r})"
        )

    output = final.get("output")
    if outcome == "accepted":
        # (d) every required key present in output
        if not isinstance(output, dict):
            raise RawSnapshotInvariantViolation(
                "Delta-7(d): accepted record's final_decision.output is not a dict"
            )
        for key in _FINAL_DECISION_ACCEPT_KEYS:
            if key not in output:
                raise RawSnapshotInvariantViolation(
                    f"Delta-7(d): accepted record missing output key {key!r}"
                )
    else:
        # (e) sentinel
        if output != _UNOBSERVABLE_FINAL_REJECTED:
            raise RawSnapshotInvariantViolation(
                "Delta-7(e): rejected/exception final_decision.output "
                f"!= {_UNOBSERVABLE_FINAL_REJECTED}; got {output!r}"
            )

    if outcome == "rejected":
        # (f) failing_gate == gates[-2].name AND gates[-2].passed == False
        if len(gates) < 2:
            raise RawSnapshotInvariantViolation(
                "Delta-7(f): rejected record has fewer than 2 gates"
            )
        prior = gates[-2]
        failing = inputs.get("failing_gate")
        if failing != prior.get("name"):
            raise RawSnapshotInvariantViolation(
                f"Delta-7(f): failing_gate {failing!r} != gates[-2].name "
                f"{prior.get('name')!r}"
            )
        if prior.get("passed") is not False:
            raise RawSnapshotInvariantViolation(
                "Delta-7(f): gates[-2].passed is not False"
            )

    if outcome == "exception":
        # (g) exactly one exception row, at gates[-2]
        exc_count = sum(1 for g in gates if g.get("name") == "exception")
        if exc_count != 1:
            raise RawSnapshotInvariantViolation(
                f"Delta-7(g): expected exactly one 'exception' row, found {exc_count}"
            )
        if len(gates) < 2 or gates[-2].get("name") != "exception":
            raise RawSnapshotInvariantViolation(
                "Delta-7(g): exception row must be at gates[-2]"
            )

    # (h) recorder_internal_failure shape
    rif = record.get("recorder_internal_failure", "MISSING")
    if rif == "MISSING":
        raise RawSnapshotInvariantViolation(
            "Delta-7(h): top-level 'recorder_internal_failure' key is absent"
        )
    if rif is not None:
        if not isinstance(rif, dict):
            raise RawSnapshotInvariantViolation(
                "Delta-7(h): recorder_internal_failure must be dict or null"
            )
        for k in ("exception_type", "exception_str", "step"):
            if k not in rif or not isinstance(rif[k], str):
                raise RawSnapshotInvariantViolation(
                    f"Delta-7(h): recorder_internal_failure missing/non-string {k!r}"
                )
        if rif["step"] not in {"field_map_copy"}:
            raise RawSnapshotInvariantViolation(
                f"Delta-7(h): unknown recorder_internal_failure.step {rif['step']!r}"
            )


def _cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a raw_decisions_*.jsonl corpus directory."
    )
    parser.add_argument("--validate", required=True, help="corpus directory path")
    args = parser.parse_args(argv)
    bad = 0
    total = 0
    for record in iter_records(args.validate):
        total += 1
        try:
            validate_record(record)
        except RawSnapshotInvariantViolation as exc:
            bad += 1
            sys.stderr.write(
                f"INVALID: decision_id={record.get('decision_id')!r}: {exc}\n"
            )
    sys.stdout.write(f"validated {total} record(s); {bad} invalid\n")
    if _LOADER_TRUNCATED_TAILS:
        sys.stdout.write(
            f"loader dropped {len(_LOADER_TRUNCATED_TAILS)} truncated trailing "
            f"line(s) (Delta-5 tolerance); files: "
            f"{', '.join(str(p) for p in _LOADER_TRUNCATED_TAILS)}\n"
        )
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(_cli())
