"""Phase Alpha unit tests for the raw decision-snapshot recorder.

Covers (per ``docs/RAW_DECISION_SNAPSHOT_PLAN.md`` §8):

* TC01  — recorder appends exactly one final_decision row per body invocation.
* TC04a — JSON serializer raises ``TypeError`` on a naïve datetime (M9).
* TC08  — recorder rejects unknown gate name.
* TC09  — Unobservable closed enumeration rejects unknown reasons.
* TC10  — Decimal precision preserved through writer round-trip.
* TC34  — Unobservable enumeration is closed; gate_exception_<typo> rejected.
* TC44  — canonical-bytes hash collision policy (different key orders → same).
* TC56b — recorder constructor rejects naïve ``decision_reference_time``.

The Alpha-1 module is intentionally unwired; these tests exercise the
schema-as-code surfaces only — no bot.py imports, no IO beyond a tmp file.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import raw_decision_snapshot as rds
from raw_decision_snapshot_enums import (
    DROP_CLASS_VALUES,
    DropClass,
    GateName,
    POLICY_FILTER_NAMES,
    Unobservable,
    empty_drop_counters,
    empty_policy_filter_counters,
    gate_exception_reason,
    is_known_unobservable_reason,
)


UTC = timezone.utc


def _new_recorder(**overrides) -> rds.RawDecisionSnapshotRecorder:
    kwargs = dict(
        decision_id="abc",
        bot_mode="live_gate",
        strategy_version="1.0.0",
        decision_reference_time=datetime(2026, 5, 24, 12, 0, tzinfo=UTC),
    )
    kwargs.update(overrides)
    return rds.RawDecisionSnapshotRecorder(**kwargs)


# --------------------------------------------------------------------------- #
# TC01 — exactly one final_decision row per body invocation                    #
# --------------------------------------------------------------------------- #


class TestTC01ExactlyOneFinalDecision:
    def test_accept_path_appends_one_final_decision(self):
        rec = _new_recorder()
        with rec:
            with rec.gate_scope(GateName.QUOTE_STABILITY.value):
                pass
        names = [g.name for g in rec.record.gates]
        assert names.count(GateName.FINAL_DECISION.value) == 1
        assert names[-1] == GateName.FINAL_DECISION.value
        assert rec.record.gates[-1].passed is True
        assert rec.record.gates[-1].inputs["outcome"] == "accepted"

    def test_reject_path_appends_one_final_decision(self):
        rec = _new_recorder()
        with rec:
            with rec.gate_scope(GateName.QUOTE_STABILITY.value):
                rec.record_reject(
                    GateName.QUOTE_STABILITY.value,
                    "quote_stability_below_configured_threshold",
                )
        names = [g.name for g in rec.record.gates]
        assert names.count(GateName.FINAL_DECISION.value) == 1
        assert names[-1] == GateName.FINAL_DECISION.value
        assert rec.record.gates[-1].passed is False
        assert rec.record.gates[-1].inputs["failing_gate"] == GateName.QUOTE_STABILITY.value
        assert (
            rec.record.gates[-1].output["reason"]
            == Unobservable.FINAL_DECISION_NOT_ACCEPTED.value
        )

    def test_exception_path_appends_exception_then_final_decision(self):
        rec = _new_recorder()
        with pytest.raises(RuntimeError, match="boom"):
            with rec:
                with rec.gate_scope(GateName.EV_GATE.value):
                    raise RuntimeError("boom")
        names = [g.name for g in rec.record.gates]
        # exception row at -2, final_decision row at -1 (post-append)
        assert names[-1] == GateName.FINAL_DECISION.value
        assert names[-2] == GateName.EXCEPTION.value
        assert names.count(GateName.FINAL_DECISION.value) == 1
        assert rec.record.gates[-1].inputs["outcome"] == "exception"
        assert rec.record.gates[-1].inputs["failing_gate"] == GateName.EV_GATE.value

    def test_exception_before_any_gate_scope_attributes_unobservable(self):
        rec = _new_recorder()
        with pytest.raises(ValueError):
            with rec:
                raise ValueError("escaped before any gate")
        assert rec.record.gates[-1].name == GateName.FINAL_DECISION.value
        failing = rec.record.gates[-1].inputs["failing_gate"]
        assert isinstance(failing, dict)
        assert failing["_unobservable"] is True
        assert failing["reason"] == Unobservable.NO_GATE_FIRED_BEFORE_EXIT.value


# --------------------------------------------------------------------------- #
# TC04a — naïve datetime raises (M9)                                           #
# --------------------------------------------------------------------------- #


class TestTC04aNaiveDatetimeRejected:
    def test_serializer_raises_on_naive_datetime(self):
        naive = datetime(2026, 5, 24, 12, 0)  # NO tzinfo
        with pytest.raises(TypeError, match="naïve datetime"):
            rds._json_default(naive)

    def test_serializer_accepts_utc_aware_datetime(self):
        aware = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
        result = rds._json_default(aware)
        assert isinstance(result, str)
        assert result.endswith("+00:00")

    def test_constructor_rejects_naive_reference_time(self):
        with pytest.raises(ValueError, match="UTC-aware"):
            rds.RawDecisionSnapshotRecorder(
                decision_id="x",
                bot_mode="live_gate",
                strategy_version="1.0.0",
                decision_reference_time=datetime(2026, 5, 24),  # naïve
            )


# --------------------------------------------------------------------------- #
# TC08 — unknown gate name rejected                                            #
# --------------------------------------------------------------------------- #


class TestTC08UnknownGateName:
    def test_record_reject_raises_on_unknown_gate(self):
        rec = _new_recorder()
        with rec:
            with rec.gate_scope(GateName.QUOTE_STABILITY.value):
                with pytest.raises(ValueError, match="unknown gate name"):
                    rec.record_reject("not_a_real_gate", "whatever")

    def test_gate_scope_raises_on_unknown_gate(self):
        rec = _new_recorder()
        with rec:
            with pytest.raises(ValueError, match="unknown gate name"):
                with rec.gate_scope("not_a_real_gate"):
                    pass

    def test_gate_entry_dataclass_rejects_unknown_name(self):
        with pytest.raises(ValueError, match="unknown gate name"):
            rds.GateEntry(name="not_a_real_gate", passed=True, reason="ok")


# --------------------------------------------------------------------------- #
# TC09 — Unobservable closed enumeration                                       #
# --------------------------------------------------------------------------- #


class TestTC09UnobservableClosedEnum:
    def test_known_member_is_known(self):
        assert is_known_unobservable_reason(Unobservable.NO_TOKEN_ID_ABSENT.value)
        assert is_known_unobservable_reason(
            Unobservable.FINAL_DECISION_NOT_ACCEPTED.value
        )

    def test_unknown_reason_rejected(self):
        assert not is_known_unobservable_reason("totally_made_up_reason")
        assert not is_known_unobservable_reason("")

    def test_gate_exception_parameterised_known_gate_accepted(self):
        assert is_known_unobservable_reason(
            f"gate_exception_{GateName.EV_GATE.value}"
        )

    def test_gate_exception_unknown_gate_rejected(self):
        assert not is_known_unobservable_reason("gate_exception_not_a_real_gate")

    def test_gate_exception_reason_builder_raises_on_unknown(self):
        with pytest.raises(ValueError):
            gate_exception_reason("not_a_real_gate")

    def test_gate_exception_reason_rejects_auto_appended_gate(self):
        # `final_decision` and `exception` are recorder-internal trailing
        # rows; they have no evaluation body and the `gate_exception_*`
        # family is meaningless for them.
        with pytest.raises(ValueError):
            gate_exception_reason(GateName.FINAL_DECISION.value)
        with pytest.raises(ValueError):
            gate_exception_reason(GateName.EXCEPTION.value)

    def test_gate_exception_reason_string_is_known(self):
        # An evaluable gate's gate_exception_<name> form IS known.
        assert is_known_unobservable_reason(
            gate_exception_reason(GateName.EV_GATE.value)
        )
        # The auto-appended forms are NOT known.
        assert not is_known_unobservable_reason(
            f"gate_exception_{GateName.FINAL_DECISION.value}"
        )
        assert not is_known_unobservable_reason(
            f"gate_exception_{GateName.EXCEPTION.value}"
        )


# --------------------------------------------------------------------------- #
# TC10 — Decimal precision preserved through round-trip                        #
# --------------------------------------------------------------------------- #


class TestTC10DecimalPrecisionRoundTrip:
    def test_decimal_serialised_as_string(self):
        payload = {"price": Decimal("0.12345678901234567890")}
        encoded = json.dumps(payload, default=rds._json_default)
        decoded = json.loads(encoded)
        assert decoded["price"] == "0.12345678901234567890"
        assert Decimal(decoded["price"]) == payload["price"]

    def test_write_record_round_trips_decimals(self, tmp_path: Path):
        path = tmp_path / "r.jsonl"
        rec = rds.RawDecisionSnapshotRecord(
            decision_id="abc",
            run_id="run-1",
            bot_mode="live_gate",
            strategy_version="1.0.0",
        )
        rec.signals.append({"price": Decimal("1.23456789")})
        rds.write_record(str(path), rec)
        line = path.read_text(encoding="utf-8").strip()
        assert "1.23456789" in line
        loaded = json.loads(line)
        assert loaded["signals"][0]["price"] == "1.23456789"


# --------------------------------------------------------------------------- #
# TC34 — closed Unobservable enum (typos rejected)                             #
# --------------------------------------------------------------------------- #


class TestTC34ClosedEnumeration:
    def test_drop_counters_have_every_known_class(self):
        counters = empty_drop_counters()
        assert set(counters) == DROP_CLASS_VALUES
        assert all(v == 0 for v in counters.values())

    def test_policy_filter_counters_have_every_known_name(self):
        counters = empty_policy_filter_counters()
        assert set(counters) == POLICY_FILTER_NAMES

    def test_dropclass_enum_size_at_baseline(self):
        # v22 baseline = 10 members per §3.D
        assert len(DropClass) == 10

    def test_unobservable_does_not_include_deribit_fetch_silently_failed(self):
        # §3.A v21 row 1+2 dispositioned as DROP per §3.D; the enum
        # value (only valid if the §3.A item was KEPT) is removed.
        assert "deribit_fetch_silently_failed" not in {
            m.value for m in Unobservable
        }

    def test_policy_filter_enum_baseline(self):
        from raw_decision_snapshot_enums import PolicyFilter
        assert len(PolicyFilter) == 2
        assert {m.value for m in PolicyFilter} == POLICY_FILTER_NAMES


class TestProcessRunId:
    def test_process_run_id_is_uuid_format(self):
        import uuid
        rid = rds.process_run_id()
        # Validates by attempting to parse.
        uuid.UUID(rid)

    def test_initialize_process_run_id_returns_fresh_value(self):
        rds.reset_process_run_id_for_tests()
        old = rds.process_run_id()
        new = rds.initialize_process_run_id()
        assert new != old
        assert rds.process_run_id() == new
        # Reset for any subsequent tests so the once-per-process flag
        # cannot bleed across test cases.
        rds.reset_process_run_id_for_tests()

    def test_initialize_process_run_id_raises_on_second_call(self):
        rds.reset_process_run_id_for_tests()
        rds.initialize_process_run_id()
        with pytest.raises(RuntimeError, match="called twice"):
            rds.initialize_process_run_id()
        # Reset for any subsequent tests.
        rds.reset_process_run_id_for_tests()


# --------------------------------------------------------------------------- #
# TC44 — canonical-bytes hash key-order invariance                             #
# --------------------------------------------------------------------------- #


class TestTC44CanonicalBytesHash:
    def test_equivalent_payloads_with_different_key_orders_hash_equal(self):
        a = rds.canonical_bytes({"b": 2, "a": 1})
        b = rds.canonical_bytes({"a": 1, "b": 2})
        assert a == b
        assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()

    def test_differing_drop_counters_produce_different_hashes(self):
        base = {
            "decision_id": "abc",
            "drop_counters": empty_drop_counters(),
        }
        bumped = {
            "decision_id": "abc",
            "drop_counters": dict(empty_drop_counters()),
        }
        bumped["drop_counters"][DropClass.DERIBIT_FETCH_DROPPED.value] = 1
        assert rds.canonical_bytes(base) != rds.canonical_bytes(bumped)

    def test_canonical_bytes_separators_are_compact(self):
        encoded = rds.canonical_bytes({"a": 1, "b": [2, 3]}).decode("utf-8")
        assert encoded == '{"a":1,"b":[2,3]}'


# --------------------------------------------------------------------------- #
# TC56b — required no-default kwargs (M11)                                     #
# --------------------------------------------------------------------------- #


class TestTC56bRequiredNoDefaultKwargs:
    def test_serializer_rejects_callable_default_expression(self):
        # A bare callable (like a lambda or partial leaked into a record)
        # would only be encountered if the implementer accidentally embedded
        # a default-value `now=datetime.now(...)` expression that produced a
        # function reference. The serializer rejects any callable to catch
        # that anti-pattern.
        def smuggled_default():
            return datetime.now(UTC)

        with pytest.raises(TypeError, match="callable"):
            rds._json_default(smuggled_default)

    def test_recorder_constructor_requires_keyword_only_args(self):
        # Positional construction would silently accept order-swapped args;
        # the recorder enforces keyword-only via signature.
        with pytest.raises(TypeError):
            rds.RawDecisionSnapshotRecorder("abc", "live_gate", "1.0.0")  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Bot-mode strictness (Gamma-5 reach-back — exercised here in Alpha)           #
# --------------------------------------------------------------------------- #


class TestBotModeStrict:
    @pytest.mark.parametrize(
        "mode", ["live_gate", "shadow_policy", "simulation"]
    )
    def test_legal_bot_mode_accepted(self, mode):
        rds.RawDecisionSnapshotRecorder(
            decision_id="x",
            bot_mode=mode,
            strategy_version="1.0.0",
        )

    @pytest.mark.parametrize(
        "mode", ["mode_check_pending", "snapshot_capture", "", "live"]
    )
    def test_illegal_bot_mode_rejected(self, mode):
        with pytest.raises(ValueError, match="unknown bot_mode"):
            rds.RawDecisionSnapshotRecorder(
                decision_id="x",
                bot_mode=mode,
                strategy_version="1.0.0",
            )


# --------------------------------------------------------------------------- #
# gate_scope discipline (TC02e / TC02f / TC02h reach-back)                     #
# --------------------------------------------------------------------------- #


class TestGateScopeDiscipline:
    def test_re_entry_forbidden(self):
        rec = _new_recorder()
        with rec:
            with rec.gate_scope(GateName.EV_GATE.value):
                with pytest.raises(RuntimeError, match="re-entry"):
                    with rec.gate_scope(GateName.EV_GATE.value):
                        pass

    def test_non_contiguous_re_entry_forbidden(self):
        rec = _new_recorder()
        with rec:
            with rec.gate_scope(GateName.EV_GATE.value):
                pass  # normal exit, success-append fires
            with pytest.raises(RuntimeError, match="non-contiguous re-entry"):
                with rec.gate_scope(GateName.EV_GATE.value):
                    pass

    def test_reject_in_scope_does_not_double_append(self):
        rec = _new_recorder()
        with rec:
            with rec.gate_scope(GateName.QUOTE_STABILITY.value):
                rec.record_reject(
                    GateName.QUOTE_STABILITY.value, "below_threshold"
                )
        named_qs = [
            g for g in rec.record.gates if g.name == GateName.QUOTE_STABILITY.value
        ]
        # Exactly one passed=false row, no extra passed=true row from the
        # gate_scope's normal-exit path.
        assert len(named_qs) == 1
        assert named_qs[0].passed is False

    def test_innermost_scope_wins_exception_attribution(self):
        rec = _new_recorder()
        with pytest.raises(RuntimeError):
            with rec:
                with rec.gate_scope(GateName.SIDE_QUOTE_AVAILABLE.value):
                    with rec.gate_scope(GateName.EV_GATE.value):
                        raise RuntimeError("inside ev_gate")
        # final_decision.inputs.failing_gate names the INNERMOST scope.
        assert (
            rec.record.gates[-1].inputs["failing_gate"]
            == GateName.EV_GATE.value
        )

    def test_reject_outside_any_scope_raises(self):
        rec = _new_recorder()
        with rec:
            with pytest.raises(RuntimeError, match="outside any gate_scope"):
                rec.record_reject(
                    GateName.QUOTE_STABILITY.value, "boom"
                )

    def test_reject_for_different_gate_than_active_scope_raises(self):
        rec = _new_recorder()
        with rec:
            with rec.gate_scope(GateName.EV_GATE.value):
                with pytest.raises(
                    RuntimeError, match="does not match the active"
                ):
                    rec.record_reject(
                        GateName.QUOTE_STABILITY.value, "wrong scope"
                    )

    def test_exception_after_prior_reject_in_separate_scope(self):
        rec = _new_recorder()
        with pytest.raises(RuntimeError):
            with rec:
                with rec.gate_scope(GateName.QUOTE_STABILITY.value):
                    rec.record_reject(
                        GateName.QUOTE_STABILITY.value, "below_threshold"
                    )
                # Exception fires after a prior reject AND outside any
                # gate_scope. failing_gate must fall back to the prior
                # passed=false gate name (LAST one wins per §4.4 contract).
                raise RuntimeError("unrelated explosion")
        assert (
            rec.record.gates[-1].inputs["failing_gate"]
            == GateName.QUOTE_STABILITY.value
        )

    def test_finalize_twice_raises(self):
        rec = _new_recorder()
        with rec:
            pass
        with pytest.raises(RuntimeError, match="called twice"):
            rec.__exit__(None, None, None)

    def test_last_failed_gate_wins_for_safety_net(self):
        rec = _new_recorder()
        with rec:
            with rec.gate_scope(GateName.RISK_ENGINE.value):
                rec.record_reject(GateName.RISK_ENGINE.value, "blocked")
            # Safety-net `executor_returned_false` row appended later
            # WITHOUT a gate_scope wrap (per plan §6.3 Gamma-4a — the
            # safety-net gate is documented as unwrapped).
            rec.record_reject(
                GateName.EXECUTOR_RETURNED_FALSE.value, "executor"
            )
        # final_decision.inputs.failing_gate must point at the LAST
        # passed=false gate (the safety-net), not the earlier one.
        assert (
            rec.record.gates[-1].inputs["failing_gate"]
            == GateName.EXECUTOR_RETURNED_FALSE.value
        )

    def test_executor_returned_false_allowed_outside_any_scope(self):
        # Per plan §6.3 Gamma-4a, the safety-net `executor_returned_false`
        # row is appended without a gate_scope wrap.
        rec = _new_recorder()
        with rec:
            rec.record_reject(
                GateName.EXECUTOR_RETURNED_FALSE.value, "no executor"
            )
        gate_names = [g.name for g in rec.record.gates]
        assert GateName.EXECUTOR_RETURNED_FALSE.value in gate_names
        assert (
            rec.record.gates[-1].inputs["failing_gate"]
            == GateName.EXECUTOR_RETURNED_FALSE.value
        )

    def test_executor_returned_false_inside_different_scope(self):
        # The safety-net is also allowed while a DIFFERENT scope is the
        # active topmost. The carve-out must NOT set
        # `_reject_during_scope`, so the outer scope's success-append
        # still runs on normal exit.
        rec = _new_recorder()
        with rec:
            with rec.gate_scope(GateName.RISK_ENGINE.value):
                rec.record_reject(
                    GateName.EXECUTOR_RETURNED_FALSE.value,
                    "while risk_engine active",
                )
        names_and_passed = [(g.name, g.passed) for g in rec.record.gates]
        assert (
            GateName.EXECUTOR_RETURNED_FALSE.value,
            False,
        ) in names_and_passed
        assert (GateName.RISK_ENGINE.value, True) in names_and_passed
        # final_decision picks the LAST passed=false row (the safety-net).
        assert (
            rec.record.gates[-1].inputs["failing_gate"]
            == GateName.EXECUTOR_RETURNED_FALSE.value
        )

    def test_gate_scope_forbids_final_decision_and_exception(self):
        # `final_decision` and `exception` are recorder-internal trailing
        # rows; wrapping them in a gate_scope would produce duplicate
        # rows and violate the universal-trailing invariant.
        rec = _new_recorder()
        with rec:
            with pytest.raises(ValueError, match="recorder-internal"):
                with rec.gate_scope(GateName.FINAL_DECISION.value):
                    pass
            with pytest.raises(ValueError, match="recorder-internal"):
                with rec.gate_scope(GateName.EXCEPTION.value):
                    pass

    def test_body_caught_gate_scope_exception_fails_stop(self):
        # G1 requires every in-body exception to escape the recorder.
        # If the body swallows an exception raised inside a gate_scope,
        # __exit__'s normal-exit branch fails-stop so the corrupted
        # record is never silently emitted.
        rec = _new_recorder()
        with pytest.raises(RuntimeError, match="body caught an exception"):
            with rec:
                try:
                    with rec.gate_scope(GateName.EV_GATE.value):
                        raise ValueError("inner failure")
                except ValueError:
                    pass

    def test_gate_scope_after_finalize_raises(self):
        rec = _new_recorder()
        with rec:
            pass
        with pytest.raises(RuntimeError, match="finalized"):
            with rec.gate_scope(GateName.EV_GATE.value):
                pass

    def test_record_reject_after_finalize_raises(self):
        rec = _new_recorder()
        with rec:
            pass
        with pytest.raises(RuntimeError, match="finalized"):
            rec.record_reject(
                GateName.EXECUTOR_RETURNED_FALSE.value, "after finalize"
            )

    def test_reenter_after_finalize_raises(self):
        rec = _new_recorder()
        with rec:
            pass
        with pytest.raises(RuntimeError, match="finalized"):
            rec.__enter__()
