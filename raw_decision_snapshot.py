"""Raw per-candidate decision snapshot recorder (Alpha-1).

Public entry point for ``docs/RAW_DECISION_SNAPSHOT_PLAN.md``. Schema-
as-code is split across companion modules per the §6.1 Alpha-1 budget
escape: :mod:`raw_decision_snapshot_enums` (closed enums),
:mod:`raw_decision_snapshot_record` (record + serializer), and this
module (recorder context manager + Phase-Gamma-replaceable writer).

Invariants enforced here: M9 (UTC-aware datetimes), M10 (negative
snapshot age raises), M11 (callable default expressions raise), §4.4
universal-trailing ``final_decision`` row, §4.5 closed Unobservable
enumeration, §3.D closed DropClass enumeration, and §6.1 Alpha-1
gate_scope discipline (defer-pop on exception, innermost-wins
attribution, scope-slot clearing, exactly-once-per-decision invariant,
and ``record_reject`` inside a matching scope except for the
documented safety-net gates).
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, Optional

from raw_decision_snapshot_enums import (
    AUTO_APPENDED_GATE_NAMES,
    BOT_MODES,
    CONDITIONAL_TRAILING_GATE_NAMES,
    DROP_CLASS_VALUES,
    DropClass,
    GATE_LITERAL_EXEMPT_SET,
    GATE_NAME_VALUES,
    GateName,
    POLICY_FILTER_NAMES,
    PolicyFilter,
    SNAPSHOT_STALE_SUFFIXES,
    UNOBSERVABLE_VALUES,
    Unobservable,
    empty_drop_counters,
    empty_policy_filter_counters,
    gate_exception_reason,
    is_known_unobservable_reason,
)
from raw_decision_snapshot_record import (
    SCHEMA_VERSION,
    SOURCE_REPO,
    GateEntry,
    RawDecisionSnapshotRecord,
    canonical_bytes,
    json_default,
)


# Backward-compatibility alias for the prior private name.
_json_default = json_default


__all__ = [
    "AUTO_APPENDED_GATE_NAMES",
    "BOT_MODES",
    "CONDITIONAL_TRAILING_GATE_NAMES",
    "DROP_CLASS_VALUES",
    "DropClass",
    "GATE_LITERAL_EXEMPT_SET",
    "GATE_NAME_VALUES",
    "GateEntry",
    "GateName",
    "POLICY_FILTER_NAMES",
    "PolicyFilter",
    "RawDecisionSnapshotRecord",
    "RawDecisionSnapshotRecorder",
    "SCHEMA_VERSION",
    "SNAPSHOT_STALE_SUFFIXES",
    "SOURCE_REPO",
    "UNOBSERVABLE_VALUES",
    "Unobservable",
    "canonical_bytes",
    "empty_drop_counters",
    "empty_policy_filter_counters",
    "gate_exception_reason",
    "initialize_process_run_id",
    "is_known_unobservable_reason",
    "process_run_id",
    "reset_process_run_id_for_tests",
    "write_record",
]


# --------------------------------------------------------------------------- #
# Process-scope run_id                                                         #
# --------------------------------------------------------------------------- #


_PROCESS_RUN_ID: str = str(uuid.uuid4())
_PROCESS_RUN_ID_INITIALIZED: bool = False


def process_run_id() -> str:
    """Return the per-process UUID that scopes every raw record's ``run_id``."""
    return _PROCESS_RUN_ID


def initialize_process_run_id() -> str:
    """Reset and return a fresh process-scope UUID.

    Called from the bot's main entry point so the "per-process UUID, set
    at bot startup" semantic in §4.2 corresponds to actual bot startup
    (not module-import time, which can differ under tests, reload(), or
    multi-bot-in-one-interpreter configurations).

    Idempotent ONLY in the sense that the second call raises ``RuntimeError``
    — the spec says "set at bot startup", so a second call necessarily
    represents a process-startup bug. Tests that need a fresh UUID use
    :func:`reset_process_run_id_for_tests`.
    """
    global _PROCESS_RUN_ID, _PROCESS_RUN_ID_INITIALIZED
    if _PROCESS_RUN_ID_INITIALIZED:
        raise RuntimeError(
            "initialize_process_run_id() called twice; the per-process "
            "UUID may be set exactly once at bot startup (plan §4.2)."
        )
    _PROCESS_RUN_ID = str(uuid.uuid4())
    _PROCESS_RUN_ID_INITIALIZED = True
    return _PROCESS_RUN_ID


def reset_process_run_id_for_tests() -> str:
    """Test-only escape hatch: regenerate the UUID and reset the
    initialized flag so a subsequent :func:`initialize_process_run_id`
    call succeeds.

    Production code MUST NOT call this. The function exists solely so
    pytest fixtures and the test for
    :func:`initialize_process_run_id` can exercise the
    once-per-process behaviour without leaking state across tests.
    """
    global _PROCESS_RUN_ID, _PROCESS_RUN_ID_INITIALIZED
    _PROCESS_RUN_ID = str(uuid.uuid4())
    _PROCESS_RUN_ID_INITIALIZED = False
    return _PROCESS_RUN_ID


# --------------------------------------------------------------------------- #
# Recorder                                                                     #
# --------------------------------------------------------------------------- #


class RawDecisionSnapshotRecorder:
    """Skeleton recorder context manager (unwired in Phase Alpha).

    Construction validates ``bot_mode`` (§4.4 Gamma-5). The ``__enter__`` /
    ``__exit__`` methods establish the record but do NOT yet persist it to
    disk — Phase Gamma adds the §4.1 ``os.write`` + ``os.fsync`` + flock
    discipline. ``gate_scope`` is fully implemented because Phase Beta /
    Gamma tests (TC02e, TC02f, TC02h) exercise it.

    Each recorder instance is SINGLE-USE: one ``with`` block per decision
    body. Calling ``__exit__`` twice raises ``RuntimeError`` (idempotency
    guard).
    """

    _process_append_lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        *,
        decision_id: str,
        bot_mode: str,
        strategy_version: str,
        git_sha: str = "",
        decision_reference_time: Optional[datetime] = None,
    ) -> None:
        if bot_mode not in BOT_MODES:
            raise ValueError(
                f"unknown bot_mode {bot_mode!r}; must be one of "
                f"{sorted(BOT_MODES)}"
            )
        if decision_reference_time is not None:
            if (
                decision_reference_time.tzinfo is None
                or decision_reference_time.utcoffset() is None
            ):
                raise ValueError(
                    "decision_reference_time must be UTC-aware (M9)"
                )
        self.record: RawDecisionSnapshotRecord = RawDecisionSnapshotRecord(
            decision_id=decision_id,
            run_id=_PROCESS_RUN_ID,
            bot_mode=bot_mode,
            strategy_version=strategy_version,
            git_sha=git_sha,
            decision_reference_time=decision_reference_time,
        )
        self._gate_scope_stack: list[str] = []
        self._scoped_gate_on_exception: Optional[str] = None
        self._reject_during_scope: Optional[str] = None
        # Exactly-once invariant — recorder is single-use AND each
        # gate_scope name may only be opened once per decision (the
        # stack-based check at line ~289 only catches contiguous
        # re-entry; this set catches non-contiguous re-entry too).
        self._already_entered_scopes: set[str] = set()
        self._finalized: bool = False
        # Minimal-Gamma slot: populated by set_final_accept_output() on
        # the bot's success path; consumed by __exit__ to populate the
        # accepted final_decision row's output dict (§6.4 Delta-7(d)).
        self._final_accept_output: Optional[dict] = None

    # ----- gate_scope ----------------------------------------------------- #

    @contextmanager
    def gate_scope(self, name: str) -> Iterator[None]:
        """Wrap a gate evaluation block (Alpha-1 / Gamma-4a).

        Normal exit: append ``passed=true`` unless a matching
        ``record_reject`` already wrote a ``passed=false`` row.
        Exception exit: defer the stack pop and set
        ``_scoped_gate_on_exception`` (innermost wins) before re-raising.
        Each gate name may be opened at most ONCE per recorder lifetime
        (both contiguous and non-contiguous re-entry raise). Calling
        after ``__exit__`` raises (post-finalize mutation would corrupt
        the universal-trailing ``final_decision`` row).
        """
        if self._finalized:
            raise RuntimeError(
                f"gate_scope({name!r}) on finalized recorder; recorders "
                "are single-use (one decision body per instance)."
            )
        if name not in GATE_NAME_VALUES:
            raise ValueError(f"unknown gate name {name!r}")
        if (
            name in AUTO_APPENDED_GATE_NAMES
            or name in CONDITIONAL_TRAILING_GATE_NAMES
        ):
            # `final_decision` and `exception` are recorder-internal
            # trailing rows appended by `__exit__`. Wrapping them in a
            # gate_scope would produce duplicate rows and violate the
            # universal-trailing invariant. Match the discipline already
            # applied to `gate_exception_reason` in the enums module.
            raise ValueError(
                f"gate_scope({name!r}) is forbidden: {name!r} is a "
                "recorder-internal trailing row appended by __exit__, "
                "not an evaluable gate."
            )
        if name in self._gate_scope_stack:
            raise RuntimeError(f"gate_scope re-entry: {name}")
        if name in self._already_entered_scopes:
            raise RuntimeError(
                f"gate_scope non-contiguous re-entry: {name} (already "
                "opened earlier in this decision)"
            )
        self._already_entered_scopes.add(name)
        self._gate_scope_stack.append(name)
        try:
            yield
        except BaseException:
            if self._scoped_gate_on_exception is None:
                self._scoped_gate_on_exception = name
            raise
        else:
            # Normal exit. Pop the stack and decide whether to append.
            self._gate_scope_stack.pop()
            if self._reject_during_scope == name:
                self._reject_during_scope = None
                return
            self.record.gates.append(
                GateEntry(name=name, passed=True, reason="ok")
            )

    def record_reject(self, name: str, reason: str, *, inputs: Any = None) -> None:
        """Append a ``passed=false`` gate row.

        The reject MUST be either the topmost active ``gate_scope`` OR a
        documented safety-net (``executor_returned_false``), per plan
        §6.3 Gamma-4a. Any other call raises. Post-finalize calls also
        raise so the universal-trailing invariant is preserved.
        """
        if self._finalized:
            raise RuntimeError(
                f"record_reject({name!r}) on finalized recorder; "
                "recorders are single-use."
            )
        if name not in GATE_NAME_VALUES:
            raise ValueError(f"unknown gate name {name!r}")
        # The TC02C_EXCLUSION_SET names (executor_returned_false,
        # exception, final_decision) are appended without a matching
        # gate_scope per plan §6.3. `exception` and `final_decision` are
        # never written through record_reject (the recorder's __exit__
        # writes them directly); only `executor_returned_false` reaches
        # record_reject from outside any scope.
        safety_net_names = {GateName.EXECUTOR_RETURNED_FALSE.value}
        if not self._gate_scope_stack:
            if name not in safety_net_names:
                raise RuntimeError(
                    f"record_reject({name!r}) called outside any "
                    "gate_scope; every reject site must be wrapped "
                    "(plan §6.3 Gamma-4a) except the documented "
                    "safety-net gates."
                )
        else:
            topmost = self._gate_scope_stack[-1]
            if name != topmost and name not in safety_net_names:
                raise RuntimeError(
                    f"record_reject({name!r}) does not match the active "
                    f"gate_scope ({topmost!r}); the reject must name the "
                    "scope it lives inside."
                )
        self.record.gates.append(
            GateEntry(name=name, passed=False, reason=reason, inputs=inputs)
        )
        if self._gate_scope_stack and name == self._gate_scope_stack[-1]:
            self._reject_during_scope = name

    # ----- minimal-Gamma reject mirror + accept output ------------------- #
    #
    # These two methods are the minimal-Gamma wiring that allows the bot's
    # ``_make_trading_decision_body_inner`` to report rejects and the
    # selected-side output dict without requiring a fully-scoped
    # gate_scope wrap around each gate site. Full §6.3 Gamma-4a wiring
    # (every reject inside its own gate_scope) is a deferred enhancement;
    # the minimal mirror keeps the captured record reflective of the
    # actual decision outcome so the §6.4 Delta-7(a)–(f) invariants hold.

    def mirror_reject(
        self,
        name: str,
        reason: str,
        *,
        inputs: Any = None,
    ) -> None:
        """Append a ``passed=false`` gate row reflecting a body-level reject.

        Unlike :meth:`record_reject` this helper does NOT require an
        active ``gate_scope`` and does NOT require ``name`` to live in
        ``GATE_NAME_VALUES`` — it is the minimal-Gamma mirror path that
        allows existing ``DecisionRecord.reject(...)`` sites in the bot
        to surface their gate name + reason into the raw record without
        a full §6.3 Gamma-4a refactor. Idempotent for the same gate-name
        within one decision body (subsequent mirrors of the same name
        are ignored so the first reject wins).
        """
        if self._finalized:
            raise RuntimeError(
                f"mirror_reject({name!r}) on finalized recorder; recorders "
                "are single-use."
            )
        # Idempotency: ignore subsequent mirrors of the same name so the
        # first reject is the failing_gate.
        for existing in self.record.gates:
            if existing.name == name and not existing.passed:
                return
        # Unknown gate names (existing bot.py sites pre-date the GateName
        # enum's closed set) are normalized to the safety-net gate
        # ``executor_returned_false`` and the original name is preserved
        # in ``inputs.original_gate_name`` for the validator to inspect.
        # Full §6.3 Gamma-4a refactor will eliminate the gap by splitting
        # gate name from reason at each bot.py reject site.
        if inputs is not None and not isinstance(inputs, dict):
            raise TypeError(
                f"mirror_reject({name!r}): inputs must be a dict or None; "
                f"got {type(inputs).__name__}"
            )
        if name not in GATE_NAME_VALUES:
            preserved = {"original_gate_name": name}
            if inputs is not None:
                preserved.update(inputs)
            self.record.gates.append(
                GateEntry(
                    name=GateName.EXECUTOR_RETURNED_FALSE.value,
                    passed=False,
                    reason=reason,
                    inputs=preserved,
                )
            )
            return
        self.record.gates.append(
            GateEntry(name=name, passed=False, reason=reason, inputs=inputs)
        )

    def set_final_accept_output(
        self,
        *,
        selected_side: str,
        selected_token_id: str,
        submitted_limit_price: str,
        accepted_limit_price: str,
        limit_order_token_qty: str,
        fusion_direction: str,
        fusion_confidence: str,
    ) -> None:
        """Store the §4.4 accept-row 7-key output dict.

        Called by the bot on the success path (just before returning True
        from the decision body). The recorder's ``__exit__`` reads this
        attribute when ``outcome == "accepted"`` and substitutes it for
        the trailing ``final_decision.output`` instead of ``None``,
        satisfying §6.4 Delta-7(d). Every value MUST be a string
        (Decimal-as-str per §5.5 canonical convention); the bot
        formats numerics before calling.
        """
        if self._finalized:
            raise RuntimeError(
                "set_final_accept_output on finalized recorder"
            )
        self._final_accept_output = {
            "selected_side": selected_side,
            "selected_token_id": selected_token_id,
            "submitted_limit_price": submitted_limit_price,
            "accepted_limit_price": accepted_limit_price,
            "limit_order_token_qty": limit_order_token_qty,
            "fusion_direction": fusion_direction,
            "fusion_confidence": fusion_confidence,
        }

    # ----- recorder lifecycle -------------------------------------------- #

    def __enter__(self) -> "RawDecisionSnapshotRecorder":
        if self._finalized:
            raise RuntimeError(
                "RawDecisionSnapshotRecorder.__enter__ on finalized "
                "recorder; recorders are single-use (one decision body "
                "per instance)."
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        # Phase Alpha: append the trailing final_decision row only; the
        # full §5 atomic write is deferred to Phase Gamma. The append is
        # intentionally last so every reader sees the universal-trailing
        # invariant gates[-1].name == "final_decision".
        if self._finalized:
            raise RuntimeError(
                "RawDecisionSnapshotRecorder.__exit__ called twice; "
                "recorders are single-use (one decision body per "
                "instance)."
            )
        self._finalized = True
        if exc_type is not None:
            outcome = "exception"
            # §6.3 Gamma-4 exception step (5) failing_gate resolution
            # (post-append index post-`final_decision` row append):
            #   1. scoped attribution wins;
            #   2. else fall back to the LAST passed=false gate before
            #      the exception (mirrors plan §4.4's "gates[-2].name"
            #      contract on the reject-then-exception edge case);
            #   3. else, no gate ever fired → emit the
            #      no_gate_fired_before_exit sentinel.
            failing_gate: Any
            if self._scoped_gate_on_exception is not None:
                failing_gate = self._scoped_gate_on_exception
            else:
                prior_failed = next(
                    (g.name for g in reversed(self.record.gates) if not g.passed),
                    None,
                )
                if prior_failed is not None:
                    failing_gate = prior_failed
                else:
                    failing_gate = {
                        "_unobservable": True,
                        "reason": Unobservable.NO_GATE_FIRED_BEFORE_EXIT.value,
                    }
            # Spec-mandated cleanup: clear the attribution slot after
            # reading it (plan §6.1 Alpha-1: "After the recorder
            # processes the attribution, it clears the slot to None").
            self._scoped_gate_on_exception = None
            self.record.gates.append(
                GateEntry(
                    name=GateName.EXCEPTION.value,
                    passed=False,
                    reason=f"{exc_type.__name__}: {exc_val}",
                    inputs={
                        "exception_type": exc_type.__name__,
                        "exception_str": str(exc_val),
                    },
                )
            )
            self.record.gates.append(
                GateEntry(
                    name=GateName.FINAL_DECISION.value,
                    passed=False,
                    reason="exception",
                    inputs={"outcome": outcome, "failing_gate": failing_gate},
                    output={
                        "_unobservable": True,
                        "reason": Unobservable.FINAL_DECISION_NOT_ACCEPTED.value,
                    },
                )
            )
            return False
        # Body-caught-exception guard: if `_scoped_gate_on_exception`
        # is set or the gate_scope stack is non-empty on the normal-exit
        # branch, the body raised inside a gate_scope and then caught
        # the exception before it reached the recorder boundary. The
        # gates[] row set is corrupted (the offending gate's success-
        # append was suppressed by the exception path but no failure row
        # was ever written). G1 requires exceptions escape to the
        # recorder; silently writing `outcome="accepted"` would diverge
        # the captured record from the actual body execution. Fail-stop.
        if (
            self._scoped_gate_on_exception is not None
            or self._gate_scope_stack
        ):
            raise RuntimeError(
                "body caught an exception raised inside gate_scope "
                f"({self._scoped_gate_on_exception or self._gate_scope_stack[-1]!r}); "
                "this corrupts the gates[] row set. Per plan G1 every "
                "in-body exception must propagate out of "
                "_make_trading_decision_body so the recorder can "
                "attribute it."
            )
        # Normal exit. Select the LAST passed=false gate (mirrors §4.4's
        # post-append gates[-2].name contract — the trailing
        # `executor_returned_false` safety-net row is documented as
        # potentially following an earlier reject, in which case the
        # safety-net IS the gate we report).
        failing_gate = None
        outcome = "accepted"
        reason = "accepted"
        prior_failed = next(
            (g.name for g in reversed(self.record.gates) if not g.passed),
            None,
        )
        if prior_failed is not None:
            outcome = "rejected"
            failing_gate = prior_failed
            reason = prior_failed
        # Minimal-Gamma: when outcome=="accepted" the bot's body should
        # have called set_final_accept_output(...) before returning True.
        # If the slot is unset (e.g., Alpha-era unit tests that exercise
        # the recorder's gate_scope discipline without wiring a body),
        # the output is None and the record will fail §6.4 Delta-7(d)
        # validation — that is the intended failure mode for test
        # fixtures lacking the accept wiring. A hard raise here would
        # block those Alpha discipline tests, so we keep the soft
        # behaviour and rely on validate_record() for downstream
        # detection. Full-Gamma will tighten this once every body path
        # is verified to wire set_final_accept_output (§12 deferral).
        if outcome == "accepted":
            final_output = self._final_accept_output
        else:
            final_output = {
                "_unobservable": True,
                "reason": Unobservable.FINAL_DECISION_NOT_ACCEPTED.value,
            }
        self.record.gates.append(
            GateEntry(
                name=GateName.FINAL_DECISION.value,
                passed=(outcome == "accepted"),
                reason=reason,
                inputs={"outcome": outcome, "failing_gate": failing_gate},
                output=final_output,
            )
        )
        # Clear the attribution slot on normal-exit too, even though it
        # is logically None already on this branch.
        self._scoped_gate_on_exception = None
        # Gamma-5: persist when RAW_DECISION_SNAPSHOT_DIR is set. Capture
        # remains opt-in (M8); with the env unset, __exit__ is a no-op
        # write. Path uses per-UTC-day rotation
        # (raw_decisions_<YYYYMMDD>.jsonl) under the configured directory.
        dirpath = os.environ.get("RAW_DECISION_SNAPSHOT_DIR")
        if dirpath:
            self._write_to_capture_dir(dirpath)
        return False

    def _write_to_capture_dir(self, dirpath: str) -> None:
        """Gamma-5: persist this record under the per-UTC-day file."""
        ref = self.record.decision_reference_time
        if ref is None:
            # Fail-stop per M9 — refusing to silently substitute wall-clock
            # would be a fallback.
            raise RuntimeError(
                "RAW_DECISION_SNAPSHOT_DIR set but record has no "
                "decision_reference_time; cannot pick a rotation file."
            )
        date_tag = ref.strftime("%Y%m%d")
        filename = f"raw_decisions_{date_tag}.jsonl"
        path = os.path.join(dirpath, filename)
        write_record(path, self.record)


# --------------------------------------------------------------------------- #
# Write helper                                                                 #
# --------------------------------------------------------------------------- #


def write_record(path: str, record: RawDecisionSnapshotRecord) -> None:
    """Append one JSONL line under the process-wide append lock.

    ``path`` MUST be directory-qualified — passing a bare filename
    raises ``ValueError`` rather than silently writing to the current
    working directory (defending against accidental CWD-substitution
    fallbacks under CLAUDE.md Rule 1). Phase Alpha exposes this single-
    shot writer for unit-test use; Phase Gamma replaces the body of the
    recorder's ``__exit__`` with the §5.1 full-fledged ``os.write`` /
    ``os.fsync`` / sidecar / skipped-log machinery.

    The JSONL line is intentionally NOT the §5.5 canonical form
    (``sort_keys=True``, compact separators) — those rules apply to
    sha256-input bytes for sidecar references, not to the durable
    record line. :func:`canonical_bytes` is the single source of truth
    for hash-input serialization; do NOT collapse the two by adding
    ``sort_keys=True`` here.

    The fd is opened with ``O_CLOEXEC`` so it is not inherited by any
    subprocess spawned during the write window.
    """
    payload = record.to_dict()
    line = json.dumps(payload, ensure_ascii=False, default=json_default)
    parent = os.path.dirname(path)
    if not parent:
        raise ValueError(
            f"write_record requires a directory-qualified path; got {path!r}"
        )
    with RawDecisionSnapshotRecorder._process_append_lock:
        os.makedirs(parent, exist_ok=True)
        fd = os.open(
            path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC,
            0o644,
        )
        try:
            os.write(fd, line.encode("utf-8") + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
