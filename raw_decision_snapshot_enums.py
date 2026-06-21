"""Closed enumerations for the raw decision-snapshot recorder.

Split from ``raw_decision_snapshot.py`` per CLAUDE.md Rule 7 / plan §6.1
Alpha-1 "budget escape": the recorder module ran past 500 lines once the
gate_scope discipline plus the §3.D DropClass enum landed in v21/v22.
Putting the closed enumerations in their own module keeps the recorder
runtime path compact AND lets tests import the enums without instantiating
the recorder machinery.

Everything in this module is data ONLY — no logic that touches IO, locks,
or wall-clock reads. Importing this module from production code is safe.
"""

from __future__ import annotations

import enum


class GateName(str, enum.Enum):
    """Closed enumeration of every legal value for ``gates[i].name``.

    Mirrors §4.4. ``final_decision`` is universal-trailing on every record
    (appended by ``__exit__``); ``exception`` is appended only on the
    exception-exit path. The remaining members are written via
    ``.reject(...)`` from inside ``_make_trading_decision_body``.
    """

    LIVE_PAUSED_UNRESOLVED_SETTLEMENT = "live_paused_unresolved_settlement"
    QUOTE_STABILITY = "quote_stability"
    HISTORY_LENGTH = "history_length"
    SNAPSHOT_FRESHNESS_BEFORE_CONTEXT = "snapshot_freshness_before_context"
    SNAPSHOT_FRESHNESS_BEFORE_SIGNALS = "snapshot_freshness_before_signals"
    SNAPSHOT_FRESHNESS_BEFORE_EXECUTION = "snapshot_freshness_before_execution"
    SNAPSHOT_FRESHNESS_BEFORE_INTENT_PERSISTENCE = (
        "snapshot_freshness_before_intent_persistence"
    )
    NO_SIGNALS = "no_signals"
    FUSION = "fusion"
    TREND_FILTER = "trend_filter"
    SIGNAL_CONFIRMATION = "signal_confirmation"
    MIN_SIGNAL_CONFIDENCE = "min_signal_confidence"
    SIDE_QUOTE_AVAILABLE = "side_quote_available"
    DEPTH_AWARE_ENTRY = "depth_aware_entry"
    LIMIT_PRICE = "limit_price"
    LIMIT_TOKEN_QTY = "limit_token_qty"
    EV_GATE = "ev_gate"
    POSITION_SIZE_BELOW_MINIMUM = "position_size_below_minimum"
    POSITION_SIZE_EXCEEDS_MAX = "position_size_exceeds_max"
    BALANCE_GUARD = "balance_guard"
    RISK_ENGINE = "risk_engine"
    LIQUIDITY_FLOOR = "liquidity_floor"
    EXECUTOR_RETURNED_FALSE = "executor_returned_false"
    EXCEPTION = "exception"
    FINAL_DECISION = "final_decision"
    # Review-cycle additions (R3 round 3): bot.py uses these strings as
    # the gate-name arg of rec.reject(...) at existing sites. Adding them
    # to the enum lets mirror_reject preserve the real failing-gate
    # identity on the trailing `final_decision.inputs.failing_gate` field
    # instead of collapsing every unknown name to executor_returned_false
    # (which buried the analytical fidelity in inputs.original_gate_name).
    # Full §6.3 Gamma-4a refactor will split gate names from reason
    # strings; these enum entries are the minimum needed for the
    # current bot.py rec.reject sites to survive the validator without
    # downstream identity loss.
    HISTORY_TOO_SHORT = "history_too_short"
    QUOTE_STABILITY_BELOW_CONFIGURED_THRESHOLD = (
        "quote_stability_below_configured_threshold"
    )
    NO_YES_QUOTE = "no_yes_quote"
    NO_NO_QUOTE = "no_no_quote"
    FUSION_NO_CONSENSUS = "fusion_no_consensus"
    TREND_FILTER_NEUTRAL = "trend_filter_neutral"
    SIGNAL_CONFIRMATION_MISMATCH = "signal_confirmation_mismatch"
    SIZE_EXCEEDS_MAX_POSITION_SIZE = "size_exceeds_max_position_size"
    POSITION_SIZE_BELOW_LIVE_MINIMUM = "position_size_below_live_minimum"
    LIMIT_PRICE_OUT_OF_BOUNDS = "limit_price_out_of_bounds"
    LIMIT_IOC_BELOW_MIN_TOKENS = "limit_ioc_below_min_tokens"
    LIMIT_IOC_NO_YES_INSTRUMENT = "limit_ioc_no_yes_instrument"
    LIMIT_IOC_NO_NO_INSTRUMENT = "limit_ioc_no_no_instrument"
    LIMIT_IOC_INSTRUMENT_NOT_CACHED = "limit_ioc_instrument_not_cached"
    LIQUIDITY_FLOOR_YES_ASK = "liquidity_floor_yes_ask"
    LIQUIDITY_FLOOR_NO_ASK = "liquidity_floor_no_ask"
    DEPTH_AWARE_BOOK_SNAPSHOT_MISSING = "depth_aware_book_snapshot_missing"
    DEPTH_AWARE_BOOK_TOO_THIN = "depth_aware_book_too_thin"
    DEPTH_AWARE_EMPTY_ASKS = "depth_aware_empty_asks"
    DEPTH_AWARE_INVALID_BOOK_LEVEL = "depth_aware_invalid_book_level"
    DEPTH_AWARE_LIMIT_IOC_NO_LIQUIDITY = "depth_aware_limit_ioc_no_liquidity"
    DEPTH_AWARE_MISSING_TOKEN_ID = "depth_aware_missing_token_id"
    DEPTH_AWARE_NO_BOOK = "depth_aware_no_book"
    DEPTH_AWARE_TOKEN_SIDE_MISMATCH = "depth_aware_token_side_mismatch"
    SNAPSHOT_CAPTURE_EXCEPTION = "snapshot_capture_exception"
    EXECUTOR_ENQUEUE_EXCEPTION = "executor_enqueue_exception"


GATE_NAME_VALUES: frozenset[str] = frozenset(member.value for member in GateName)


AUTO_APPENDED_GATE_NAMES: frozenset[str] = frozenset({GateName.FINAL_DECISION.value})
"""Closed set of gate names appended UNCONDITIONALLY by ``__exit__``."""


CONDITIONAL_TRAILING_GATE_NAMES: frozenset[str] = frozenset({GateName.EXCEPTION.value})
"""Closed set of gate names appended only on the exception path."""


GATE_LITERAL_EXEMPT_SET: frozenset[str] = frozenset(
    {"snapshot_capture_exception", "executor_enqueue_exception"}
)
"""§4.4 TC02d locked set — the two NOT-WIRED ``.reject(...)`` literals."""


SNAPSHOT_STALE_SUFFIXES: frozenset[str] = frozenset(
    {
        "before_context",
        "before_signals",
        "before_execution",
        "before_intent_persistence",
    }
)
"""§4.4 TC02a — closed set of legal ``gate_suffix`` values."""


BOT_MODES: frozenset[str] = frozenset({"live_gate", "shadow_policy", "simulation"})
"""§4.2 — recorder constructor rejects any other ``bot_mode``."""


class Unobservable(str, enum.Enum):
    """Closed §4.5 enumeration of legitimate-absence reasons.

    Recorder fields that are unobservable in a legitimate decision path
    are recorded as ``{"_unobservable": true, "reason": <member>}``. The
    parameterised family ``gate_exception_<gate_name>`` is materialized
    at validator-construction time from :class:`GateName`.

    The §4.5 comment "deribit_fetch_silently_failed — only present if §3.A
    item kept" required removal of that enum value once §3.A v21 re-
    dispositioned rows 1+2 (deribit HTTP fetch) as DROP per §3.D. The
    enum below reflects the post-v21 disposition.
    """

    NO_TOKEN_ID_ABSENT = "no_token_id_absent"
    DERIBIT_CACHE_HIT_NO_FRESH_FETCH = "deribit_cache_hit_no_fresh_fetch"
    COINBASE_SPOT_HISTORY_EMPTY = "coinbase_spot_history_empty"
    TOS_RETENTION_NOT_OPTED_IN = "tos_retention_not_opted_in"
    CONTEXT_FETCH_EXCEPTION_PRE_METADATA = "context_fetch_exception_pre_metadata"
    SIGNALS_EXCEPTION_POST_METADATA = "signals_exception_post_metadata"
    FUSION_EXCEPTION_POST_SIGNALS = "fusion_exception_post_signals"
    DEPTH_AWARE_ENTRY_EXCEPTION = "depth_aware_entry_exception"
    CONTAINER_DIGEST_UNSET = "container_digest_unset"
    EXCEPTION_BEFORE_SET = "exception_before_set"
    FINAL_DECISION_NOT_ACCEPTED = "final_decision_not_accepted"
    NO_GATE_FIRED_BEFORE_EXIT = "no_gate_fired_before_exit"


UNOBSERVABLE_VALUES: frozenset[str] = frozenset(member.value for member in Unobservable)


# Auto-appended / conditional-trailing gates are recorder-internal rows
# that do NOT have an evaluation body; their ``gate_exception_<name>``
# form is therefore meaningless and rejected.
_GATE_EXCEPTION_VALID_SUFFIXES: frozenset[str] = (
    GATE_NAME_VALUES - AUTO_APPENDED_GATE_NAMES - CONDITIONAL_TRAILING_GATE_NAMES
)


def gate_exception_reason(gate_name: str) -> str:
    """Return the parameterised ``gate_exception_<gate_name>`` reason.

    Raises ``ValueError`` if ``gate_name`` is not a :class:`GateName`
    member with an evaluation body (i.e., excludes ``final_decision`` and
    ``exception``, which are recorder-internal trailing rows). TC34
    covers the typo case AND the auto-appended-gate carve-out.
    """
    if gate_name not in _GATE_EXCEPTION_VALID_SUFFIXES:
        raise ValueError(
            f"unknown or non-evaluable gate name {gate_name!r}; cannot "
            f"form gate_exception_*"
        )
    return f"gate_exception_{gate_name}"


def is_known_unobservable_reason(reason: str) -> bool:
    """True iff ``reason`` is a member of the closed Unobservable enum
    OR a legal ``gate_exception_<gate_name>`` parameterised reason
    (where ``<gate_name>`` is a gate with an evaluation body — i.e.,
    NOT ``final_decision`` and NOT ``exception``)."""
    if reason in UNOBSERVABLE_VALUES:
        return True
    if reason.startswith("gate_exception_"):
        suffix = reason[len("gate_exception_") :]
        return suffix in _GATE_EXCEPTION_VALID_SUFFIXES
    return False


class DropClass(str, enum.Enum):
    """§3.D closed enumeration of malformed/unexpected-data drop classes."""

    DERIBIT_FETCH_DROPPED = "deribit_fetch_dropped"
    DERIBIT_INSTRUMENT_PARSE_DROPPED = "deribit_instrument_parse_dropped"
    DERIBIT_SHORT_PCR_MISSING_DROPPED = "deribit_short_pcr_missing_dropped"
    ORDERBOOK_FETCH_DROPPED = "orderbook_fetch_dropped"
    ORDERBOOK_LEVEL_MALFORMED_DROPPED = "orderbook_level_malformed_dropped"
    ORDERBOOK_PROCESS_EXCEPTION_DROPPED = "orderbook_process_exception_dropped"
    DIVERGENCE_METADATA_MISSING_DROPPED = "divergence_metadata_missing_dropped"
    DIVERGENCE_COINBASE_MISSING_DROPPED = "divergence_coinbase_missing_dropped"
    UNKNOWN_SIGNAL_SOURCE_DROPPED = "unknown_signal_source_dropped"
    LOADER_TRUNCATED_TRAILING_LINE_DROPPED = "loader_truncated_trailing_line_dropped"


DROP_CLASS_VALUES: frozenset[str] = frozenset(member.value for member in DropClass)


class PolicyFilter(str, enum.Enum):
    """§3.D / §4.2 closed enumeration of policy-filter counter keys.

    Policy filters are NOT fallbacks and NOT drops — they record deliberate
    threshold decisions applied to well-formed data (e.g., a signal whose
    confidence is below the configured minimum is filtered, observably and
    by design). The §4.2 ``policy_filter_counters`` block is populated
    from this enum.
    """

    SIGNAL_BELOW_MIN_CONFIDENCE_FILTER = "signal_below_min_confidence_filter"
    FUSION_BELOW_MIN_CONTRIB_FILTER = "fusion_below_min_contrib_filter"


POLICY_FILTER_NAMES: frozenset[str] = frozenset(
    member.value for member in PolicyFilter
)
"""§3.D / §4.2 closed set of policy-filter counter keys (backward-compatible
alias of :class:`PolicyFilter` membership)."""


def empty_drop_counters() -> dict[str, int]:
    """Return a fresh dict containing every :class:`DropClass` key at 0."""
    return {member.value: 0 for member in DropClass}


def empty_policy_filter_counters() -> dict[str, int]:
    """Return a fresh dict with every :class:`PolicyFilter` key at 0.

    Uses declaration order for parity with :func:`empty_drop_counters`.
    The hash discipline in §5.5 (``sort_keys=True``) makes the iteration
    order irrelevant for the canonical hash, but matching declaration
    order keeps the human-facing JSON consistent across counter blocks.
    """
    return {member.value: 0 for member in PolicyFilter}
