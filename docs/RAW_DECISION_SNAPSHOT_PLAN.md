# Raw Decision Snapshot Capture and Policy Replay Harness — Implementation Plan

Plan version: v22 (v21 + round-18 three-reviewer P1 fixes:
  - B10 IMPLEMENTATION DIRECTIVE: divergence_processor.py needs an
    EARLY-RETURN at process() entry when spot_price is None;
    removing the lines 127-129 substitution alone is insufficient
    because SIGNAL 1 fade branches still fire with
    spot_momentum=0.0.
  - PRODUCTION_DEFAULT_WEIGHTS clarified as forward-looking to
    Beta-5 (does not exist in current code). v22 REMOVES the
    "default" key entirely from PRODUCTION_DEFAULT_WEIGHTS (was
    7 keys → now 6). TC61c asserts.
  - drop_counters and policy_filter_counters explicitly INCLUDED
    in canonical-bytes hash (§5.5) — two records identical except
    for drop counts produce different sha256s. TC44 extended.
  - TC86 (d) reworded: enum is closed at Phase-Beta sign-off but
    Alpha-4 may extend; test reads source-of-truth at test time.
  - TC86 (g) added: static AST check that no drop_counters
    increment exists in risk_engine or post-EV-gate handlers
    (enforces §3.D guard-rail 3 mechanically).

v21 prior: added §3.D Malformed/Unexpected Data Drop Principle +
fsync retry-once APPROVED FALLBACK (user 2026-05-24 Option A).

v21 changes (user-authorized 2026-05-24):
  - Added §3.D Malformed/Unexpected Data Drop Principle with three
    guard-rails (COUNTED+OBSERVABLE, UPSTREAM-OF-DECISION SCOPE,
    NEVER IN LIVE-TRADING VERDICT PATH). The principle resolves
    13 of 17 §3.A pre-existing-fallback dispositions in one shot
    by establishing a clean design pattern: malformed/unexpected
    upstream data is dropped (NOT substituted), with a structured
    counter recorded in the raw snapshot.
  - Added §4.2 top-level `drop_counters` block (10-element
    closed enum: deribit_fetch / deribit_instrument_parse /
    deribit_short_pcr_missing / orderbook_fetch / orderbook_level_
    malformed / orderbook_process_exception / divergence_metadata_
    missing / divergence_coinbase_missing / unknown_signal_source /
    loader_truncated_trailing_line) and `policy_filter_counters`
    block (2-element: signal_below_min_confidence_filter /
    fusion_below_min_contrib_filter).
  - §3.A v21 dispositions: B1-B10 and B17 → DROP per §3.D;
    B11, B12 → POLICY FILTER per §3.D (reclassified — not
    fallbacks); B13, B14, B15, B16 → Remove (unchanged from
    v12-v20); B17 (signal_fusion.py:95) → DROP per §3.D (v21
    replaces the v20 "grandfathered" wording explicitly).
  - Added §3.A.1 with C1-C5 dispositions for recorder-internal
    I/O paths flagged in the v20 audit:
      * C1 (skipped-log inner try/except): KEPT as observability
        chaining; documented as NOT a Rule-1 fallback (no error
        masked; original raw-write OSError propagates).
      * C2 (loader truncated tail): resolved by §3.D drop
        principle with `loader_truncated_trailing_line_dropped`
        counter.
      * C3 (lsof diagnostic): small try/except for diagnostic
        enrichment only; main flock-contention error still
        propagates; not a fallback under §3.D guard-rails.
      * C4 (sidecar dedup index seeding): explicit `path.exists()`
        check on day-1 (no try/except); normal initial-state
        handling; not a fallback.
      * C5 (fsync retry-once): **APPROVE THIS FALLBACK** granted
        by user 2026-05-24 ("Option A"). Recorder catches first
        fsync OSError, logs warning, retries exactly once, on
        second failure raises chained OSError that fail-stops
        the bot. Full Rule-1 approval block (control-flow,
        cases, justification, code-comment) in §3.A.1 C5.
        TC87 enforces.
  - Added TC86 (drop-counter coverage) and TC87 (fsync
    retry-once behaviour) to §8.

Prior plan history retained in earlier-version notes below.

v20 prior: narrowed Delta-7 (i) to NORMAL-EXIT FMC only (the
round-16 P2 scope); corrected "(a)-(h)" → "(a)-(i)" enumeration;
added TC84 catalog row.

v18 prior: §8 TC47b row stale "trailing gates[-1]==exception"
wording finally swept; Gamma-4 step (3) cross-ref to
`self._scoped_gate_on_exception`; Alpha-1 stale anchor removed.

Indexing-convention preamble note for this changelog cluster: all
`gates[-1]` references throughout this changelog are POST-APPEND
(the universal-trailing invariant introduced in v13 and locked in
v16 — `gates[-1].name=="final_decision"` on every record).

v18 changes:
  - §8 TC47b row (lines ~3450) rewritten to the v16 post-append
    layout: gates[-3] for the ev_gate _unobservable marker,
    gates[-2] for "exception", gates[-1] for "final_decision".
  - Gamma-4 exception step (3) cross-reference reworded to point
    at `self._scoped_gate_on_exception` (the actual mechanism per
    Alpha-1 defer-pop) instead of "gate_scope context-manager stack".
  - Alpha-1 line ~1251 stale anchor "(lines 2436-2466)" replaced
    with a non-numeric reference.

v17 prior changes:
  - Fixed stale "trailing gates[-1] with name=exception" wording in
    §4.5 enum comment, TC47b spec (Gamma-4 prose), and Gamma-4a
    wrap-list intro. (v17 missed the §8 TC47b table row mirror;
    v18 closed that gap.)
  - Delta-7 (g) "potentially zero" phrasing RETRACTED.
  - Alpha-1 cross-reference fixed: line ~1146 reads "TC02h".
  - TC02g semantics REVISED to lazy FIELD_MAP-driven population.
  - RP13 self-test pre-emptive token additions to clusters.

Plan history retained:
  - v16 (round-14): FMC observability REDESIGNED out of gates[] into
    top-level field `recorder_internal_failure`; universal-trailing
    invariant restored; gate_scope defer-pop discipline;
    gate_scope-to-DecisionRecord.reject wiring pinned; Gamma-2
    instrument-unbound fix; reject-then-return TC02j; RP13 per-cluster
    relaxation; sentinel shape on failing_gate.
  - v15 (round-13): user APPROVED FALLBACK for FIELD_MAP try/except;
    schema_version reverted to 1 (Option A); gate_scope API spec;
    pre-/post-append convention.
  - v14: schema_version bump (later reverted); FMC trailing-row
    design (later retracted); Gamma-4 edge-case hardening.
  - v13: added final_decision trailing gates entry per user
    clarification.
  Snapshot date: 2026-05-23.

Indexing-convention preamble note for this changelog cluster: all
`gates[-1]` references throughout this changelog are POST-APPEND
(the universal-trailing invariant introduced in v13 and locked in
v16 — `gates[-1].name=="final_decision"` on every record).

Plan history retained:
  - v16 (round-14): FMC observability REDESIGNED out of gates[] into
    a new top-level field `recorder_internal_failure` per §4.2;
    universal-trailing invariant restored; gate_scope defer-pop
    discipline; gate_scope-to-DecisionRecord.reject wiring pinned;
    Gamma-2 instrument-unbound fix; reject-then-return TC02j;
    RP13 per-cluster relaxation; sentinel shape on failing_gate;
    schema_version stale=2 swept.
  - v15 (round-13): user APPROVED FALLBACK for FIELD_MAP try/except;
    schema_version reverted to 1 (Option A); _unobservable_reason
    reshape; gate_scope API spec; pre-/post-append convention.
  - v14 (round-12): schema_version bump (later reverted); FMC
    trailing-row design (later retracted); Gamma-4 edge-case
    hardening; FIELD_MAP.IGNORED refresh; Phase Zeta/Delta/Eta
    wiring; tests TC83 expansion + TC65b/TC84/TC84a.
  - v13: added final_decision trailing gates entry per user
    clarification.
  Snapshot date: 2026-05-23.

This plan is the authoritative specification for adding a per-candidate
raw decision snapshot capture, an offline policy-replay corpus, a brute-
force parameter search harness, and the surrounding test, regression,
and review infrastructure. It is intentionally written so a cold reader
can pick up the work without prior conversation context.

Read alongside the existing
[`docs/DATA_COLLECTION_INVENTORY.md`](DATA_COLLECTION_INVENTORY.md) and
[`docs/STRATEGY_ALGORITHM_INVENTORY.md`](STRATEGY_ALGORITHM_INVENTORY.md).

Phase labels (Alpha, Beta, Gamma, Delta, Epsilon, Zeta, Eta, Theta)
appear ONLY in this Markdown file. Per CLAUDE.md Rule 8, no source file,
module, class, function, or variable carries a phase keyword.

---

## 1. Goals

The deliverable is a self-contained raw evidence corpus that lets an
offline harness brute-force every adjustable strategy parameter listed
in this document, against the exact market state seen at decision time,
for every live and shadow candidate the decision body evaluated,
including rejects and no-signal cases.

- **G1 — Per-candidate raw snapshot (scoped).** For every invocation of
  `_make_trading_decision_body` (the single call site after mode
  resolution — see §6.3), the recorder writes exactly one raw snapshot
  record, regardless of which gate the decision exits at, including
  no-signal early returns, freshness rejects, quote-stability rejects,
  and any exception that escapes the body. The recorder is NOT wired
  at any other `DecisionRecord` site (snapshot-capture exception
  writer, executor-enqueue exception writer, the pre-resolution
  `mode_check_pending` window at the top of `_make_trading_decision`).
  Those sites continue to write exactly one `decisions.jsonl` line and
  zero raw lines. The intentional divergence is exposed as the G10
  join-completeness metric and is documented, not silent.
- **G2 — Field completeness.** Each raw record contains every field in
  §4 ("Raw Snapshot Schema"), populated when observable at capture
  time, or explicitly marked unobservable per §4.5.
- **G3 — Verdict-tuple replay parity.** The offline replayer (Phase
  Zeta) reconstructs the verdict tuple `(decided_direction,
  rejected_at_gate, rejection_reason)` and the intermediate computed
  values for any raw record. The reconstructed verdict tuple for the
  recorded config MUST equal the recorded verdict tuple for ALL
  in-scope records (≥ 99.9% is the operational SLO; 100% is the
  sign-off gate). Records whose final gate is `exception` are out of
  scope for parity and are reported in a separate `exception_records`
  column. Every parity mismatch on an in-scope record is either
  resolved before sign-off or carries an `APPROVED_PARITY_DEVIATION`
  annotation in a checked-in exception list signed off by the
  implementer (see §13).
- **G4 — Brute-force harness.** A reproducible offline harness consumes
  raw records, sweeps a declarative parameter grid (§7), and outputs
  a ranked CSV of policy variants joined to recorded market resolution.
  The harness produces deterministic, byte-equal output across re-runs
  for the same corpus, grid, code SHA, and Python version (§6.7
  determinism rules).
- **G5 — Live-equivalence boundary.** All harness output is labelled
  policy/decision replay, never trade simulation, per CLAUDE.md Rule 3
  (§9).
- **G6 — Regression safety, with two acknowledged couplings.** The
  capture path does not change any observable trading verdict, any
  order submitted, any ledger write, or any existing `decisions.jsonl`
  field semantics. Existing `decisions.jsonl` writers continue to emit
  the current compact summary unchanged. There are exactly TWO new
  observable trading behaviours, both acknowledged and mitigated:
  - **(a) Capture write failure → fail-stop.** When capture is
    enabled (`RAW_DECISION_SNAPSHOT_DIR` set) and the recorder's
    `__exit__` write raises (disk full, EIO, permission revocation),
    the bot fail-stops via the same propagation discipline the
    existing `decision_log.py` writer uses. Mitigated by §6.8
    capacity planning (dedicated filesystem with alerting).
  - **(b) Daily-stats reset boundary moves from local-TZ to UTC.**
    The pre-existing fallback at `risk_engine.py:85` (init),
    `:514` (`reset_daily_stats`), and `:519` (`_maybe_reset_daily_stats`)
    uses naïve `datetime.now()`, which evaluates in the bot's local
    timezone. Beta-8 replaces these with UTC-aware reads so the
    captured `decision_reference_time` (always UTC) can be compared
    against `_stats_date` without paradox (§6.2 Beta-8 + §3.A item
    14). The OBSERVABLE EFFECT is: the daily-stats rollover instant
    moves from local midnight to UTC midnight (a single fixed
    boundary shift, applied once at deploy time). Operators in non-
    UTC deploys will see their `max_loss_per_day` window roll over
    at a different wall-clock instant than before. This is a one-
    time deploy-coordinated change documented in
    `docs/RAW_DECISION_SNAPSHOT_OPERATIONS.md` and gated by an
    explicit operator acknowledgement env var
    `POLYBOT_ACKNOWLEDGE_UTC_DAILY_RESET=1` (refusing to start
    without it after Beta-8 lands). Without this coupling the only
    alternative is to leave the local-TZ ambiguity in place, which
    M9 forbids for any code path the recorder touches.
  Phase Beta (introspection) and Phase Gamma (wiring) each carry
  per-phase byte-equal-fixture assertions for the verdict path
  EXCLUDING the daily-stats-reset rollover window (the window is
  deliberately shifted; the test fixture pins the new UTC boundary).
- **G7 — Operational durability, no kill switch.** Capture failures
  surface to the operator and propagate out of the decision body. The
  ONLY way to disable capture in production is to restart the bot
  without the capture-enable env var. There is no live kill switch by
  design — a kill switch that masks a failing recorder would be a
  fallback under M4. The capacity-planning §6.8 work items document
  the operational sizing required to make a disk-full event
  effectively impossible under normal load.
- **G8 — No unapproved migration.** No existing artifact, schema, or
  on-disk format is transformed. The raw corpus is a new, separate
  file family. The `DecisionInputSnapshot` dataclass IS extended
  forward-additively in-memory; §3.C explains why this is not a
  migration. Test fixture corpora may be rebuilt freely (§3.C bullet 5).
  Any change qualifying as a migration under CLAUDE.md Rule 2 pauses
  and requests explicit approval.
- **G9 — No unapproved fallback.** No code path that masks failure,
  substitutes a default, retries silently, or swallows an error is
  added without explicit per-instance approval under CLAUDE.md Rule 1.
  Pre-existing fallbacks in files this plan touches are enumerated in
  §3.A and dispositioned BEFORE Phase Alpha begins (the closed
  Unobservable enumeration in Phase Alpha depends on the disposition).
- **G10 — Join-completeness invariant.** For every `decisions.jsonl`
  line whose `decision_id` was created inside
  `_make_trading_decision_body`, there exists exactly one raw corpus
  line with the same `decision_id`, and vice versa. For every
  `decisions.jsonl` line whose `decision_id` was created outside that
  body, there are zero raw lines. Asserted by TC15a. Decision IDs
  recorded in `decisions.jsonl` but absent from the raw corpus
  because the recorder's `__exit__` raised (capture-disk-full class)
  are written to a sibling `raw_decisions_skipped.jsonl` with the
  failure metadata before the bot fail-stops (this is NOT a fallback
  under M4 because it does NOT mask the failure — the bot still
  fail-stops; the skipped log is observability, not recovery; see
  §5.6 work item).
- **G11 — Operational capacity planning.** The capture volume is
  provisioned per §6.8 Theta-7 with explicit minimum free-space
  alerting at 50% (warn) and 80% (page) of the projected daily
  consumption × retention window. This is a deploy-time requirement
  documented in `docs/RAW_DECISION_SNAPSHOT_OPERATIONS.md`.

## 2. Non-Goals

- This plan does NOT modify the existing `decisions.jsonl` schema or
  rename any existing field.
- This plan does NOT add a trade simulator. See §9.
- This plan does NOT change observable trading verdicts. Refactors that
  extract `min_signals=2`/`min_score=55.0`/`TREND_UP_THRESHOLD`/etc.
  into a single effective-config helper (§6.2 Phase Beta) preserve
  every numeric value bit-for-bit and are guarded by byte-equal-
  fixture tests.
- This plan does NOT alter the live ledger (`live_trades.json`) shape,
  the paper trades file, the credentials vault, or any Redis key.
- This plan does NOT introduce automated parameter tuning into the
  live bot.

## 3. Mandatory Process Constraints

Violating any one invalidates the work product.

- **M1 — Main worktree only.** Every edit lands directly in the main
  worktree (CLAUDE.md Rule 9).
- **M2 — Three-agent review per phase.** Spawn three independent
  reviewers in parallel with the full plan and the per-phase diff.
  Each answers the §10 five questions plus six standing checks. Phase
  is sign-off complete only when all three independently report zero
  concerns at any priority. Repeat until that holds (CLAUDE.md Rule 6).
- **M3 — No migration without explicit approval.** Pause, file a
  migration approval request, wait for reply containing the literal
  `APPROVE THIS MIGRATION`. Unapproved migrations are P0 ULTRA
  CRITICAL and reverted (CLAUDE.md Rule 2).
- **M4 — No fallback without explicit approval.** Pause, file a
  fallback approval request, wait for reply containing the literal
  `APPROVE THIS FALLBACK`. Unapproved fallbacks are P0 ULTRA CRITICAL
  (CLAUDE.md Rule 1).
- **M5 — File length discipline.** 350–500 lines per source file
  (CLAUDE.md Rule 7).
- **M6 — No meaningless identifiers.** Phase terms appear only in MD
  (CLAUDE.md Rule 8).
- **M7 — Capture is read-only.** Capture code MUST NOT mutate any
  object it observes, MUST NOT trigger external fetches, MUST NOT
  call any function with observable side effects beyond writing its
  own output file.
- **M8 — No silent enablement.** Capture is opt-in via exactly one
  switch: `RAW_DECISION_SNAPSHOT_DIR`. With the env unset, the
  recorder is a startup no-op that logs one INFO line "raw decision
  snapshot capture disabled". There is no CWD or sibling default;
  fail-fast on enable if directory missing or unlockable.
- **M9 — UTC-aware datetimes only.** Every datetime read, written,
  compared, or serialized by code introduced by this plan MUST be
  UTC-aware (`tzinfo=timezone.utc`). The new JSON serializer raises
  `TypeError` on a naïve datetime. Static check RP8.
- **M10 — `decision_snapshot_age_seconds < 0` is fatal.** The recorder
  raises; the bot fail-stops. Mirrors the existing
  `_live_decision_snapshot_is_fresh` discipline.
- **M11 — Required `now=` kwargs have no default.** Any new datetime
  parameter introduced by this plan MUST be a required keyword-or-
  positional argument with NO default value. Default expressions like
  `now: datetime = datetime.now(timezone.utc)` are forbidden because
  (a) they are evaluated at function-definition time in Python (the
  classic mutable-default anti-pattern in datetime form), and (b)
  silently falling back to wall clock when the caller forgot to pass
  `now=` is itself a fallback under M4. Static check RP8 enforces;
  TC56b enforces the runtime contract.

### 3.A Pre-existing Fallbacks in Touched Files (pre-Phase-Alpha gate)

Phase Alpha lands the closed `Unobservable` enumeration (§4.5), so the
fallback disposition decisions MUST be complete BEFORE Phase Alpha
starts (the enumeration values that correspond to each kept fallback
must be known). The implementer files one fallback approval request
per item below containing the exact code, affected scenarios, and
either a removal plan or an explicit `APPROVE THIS FALLBACK`
justification.

| Loc | Pattern | Affected scenarios |
| --- | --- | --- |
| `core/strategy_brain/signal_processors/deribit_pcr_processor.py:187–189` | try/except around HTTP fetch; returns None | Deribit API outage, rate-limit, timeout |
| `core/strategy_brain/signal_processors/deribit_pcr_processor.py:216–220` | try/except around `_fetch_pcr`; returns None | Same as above |
| `core/strategy_brain/signal_processors/deribit_pcr_processor.py:111-112` | try/except in `_parse_dte`; date-parse failure → skip | Malformed instrument name |
| `core/strategy_brain/signal_processors/deribit_pcr_processor.py:237` | `pcr_data.get("short_pcr") or pcr_data.get("overall_pcr", 1.0)` — both `or` fallback and `default=1.0` | Cache slot missing short_pcr key |
| `core/strategy_brain/signal_processors/orderbook_processor.py:101–111` | try/except around `fetch_order_book`; returns None | CLOB outage, malformed payload |
| `core/strategy_brain/signal_processors/orderbook_processor.py:121–122` | `except (ValueError, TypeError): continue` in `_parse_levels` | Malformed book level |
| `core/strategy_brain/signal_processors/orderbook_processor.py:136–137` | Same pattern in `_detect_wall` | Same |
| `core/strategy_brain/signal_processors/orderbook_processor.py:240–242` | `except Exception` at top of `process()` | Any unexpected exception |
| `core/strategy_brain/signal_processors/divergence_processor.py:107-108` | `if not metadata: return None` (silent absence handler) | Missing metadata dict |
| `core/strategy_brain/signal_processors/divergence_processor.py:127-129` | `elif spot_price is None: spot_momentum = poly_momentum` (fallback to polymarket momentum) | Coinbase spot missing |
| `core/strategy_brain/signal_processors/divergence_processor.py:221-222` | `if confidence < self.min_confidence: return None` (silent below-threshold return) | Confidence below configured floor |
| `core/strategy_brain/fusion_engine/signal_fusion.py:125-127` | `if total_contrib < 0.0001: ... return None` (silent return) | Degenerate fusion |
| `execution/risk_engine.py:70-77` | `Decimal(os.getenv("MAX_POSITION_SIZE", "1.0"))` etc. — every env default | Operator did not set the env var |
| `execution/risk_engine.py:85, 223, 302, 303, 453, 482, 507, 514, 519` | Naïve `datetime.now().date()` / `datetime.now()` calls (Beta-8 touches the file). Line 302 additionally has a `.get("entry_time", datetime.now())` DOUBLE fallback (silent default-on-missing-key AND naïve datetime). | Day-boundary semantics; local-TZ ambiguity. Disposition for this item is FORCED to "Promote/Remove" via Beta-8: every call site is rewritten to `datetime.now(timezone.utc).date()` OR takes a `now: datetime` UTC-aware arg. Line 302's `.get(...)` fallback is a separate REMOVE disposition: positions MUST be constructed with `entry_time` in metadata; missing → raise (not silent substitute). The constructor at line 85 gates the bot's startup on the `POLYBOT_ACKNOWLEDGE_UTC_DAILY_RESET=1` env per G6 bullet (b). The local→UTC daily-boundary shift IS an observable behaviour change; explicit acknowledgement required. |
| `bot.py:5593-5596` | `if rec.fields["rejected_at_gate"] is None: rec.reject("executor_returned_false", ...)` — defaulting-on-None branch | Executor returns False without naming a gate. **DISPOSITION (this plan): KEEP, with `APPROVE THIS FALLBACK` to be filed by the implementer before Phase Beta.** The full-REMOVE alternative would require naming a stable gate for each of ~47 `return False` sites in `_place_real_order` (bot.py:5843+) plus the `_record_paper_trade` sites — a refactor large enough to warrant its own plan. The KEEP rationale: the trailing `executor_returned_false` row in §4.4 is a SAFETY NET that the executor refactor (deferred follow-up plan) will subdivide. Until that plan lands, every executor failure that escapes without a `rec.reject(...)` lands on `executor_returned_false`; TC02 + TC02a still hold; G3 parity is preserved because the trailing safety-net gate is deterministic. The follow-up plan will enumerate every `return False` with a stable gate name and delete this row. |
| `calibration_decision_join.py` (every try/except, `or` default, `.get(default)`) | Pre-existing fallbacks in a file Alpha-4 EXTRACTS shared helpers from | If the extracted helpers carry the fallbacks into `analysis/gamma_resolution.py`, Alpha-4 inherits them. Enumerated and dispositioned BEFORE Alpha-4 merges. |
| `estimate_decision_results.py` (every try/except, `or` default, `.get(default)`) | Same | Same |

For each, the disposition is one of:
1. **Remove**: rewrite to fail-stop. Update affected callers and tests.
2. **Keep, approved**: receive `APPROVE THIS FALLBACK` and document the
   justification with a comment in code naming the approval.
3. **Promote to required env**: applies to the `risk_engine.py:70-77`
   env-default family. Refactor each to a required env via the §12
   "promoted-constant" convention; default removal is a behaviour-
   preserving change because every operator deploy already sets these
   vars (Phase Beta verifies via CI smoke test).
4. **DROP per §3.D** (v21, user-approved 2026-05-24): the row is
   resolved by the Malformed/Unexpected Data Drop Principle. The
   implementer rewrites the existing try/except (or `or default` /
   `.get(default)` pattern) to: (a) catch the malformed-data case;
   (b) increment the matching `drop_counters` field per §4.2; (c)
   continue with the relevant signal/data dropped (NOT substituted).
   No per-instance Rule-1 approval needed because the principle's
   three guard-rails (counted+observable, upstream-only,
   never-in-verdict) are satisfied by construction.
5. **Policy filter** (v21): the pattern is a deliberate threshold or
   policy decision applied to well-formed data, NOT a fallback. The
   implementer keeps the existing behaviour but increments a
   `policy_filter_counters` field per §4.2 for observability. Rule
   1 does not apply.

**Per-row v21 dispositions** (mapping each row to its principle/
disposition; the original "Affected scenarios" column above is
informational):
- Rows 1, 2 (`deribit_pcr_processor.py:187-189` and `:216-220`
  HTTP try/except → None): **DROP per §3.D**, increments
  `deribit_fetch_dropped`.
- Row 3 (`deribit_pcr_processor.py:111-112` `_parse_dte` skip):
  **DROP per §3.D**, increments
  `deribit_instrument_parse_dropped` (per-instrument; the rest of
  the payload continues to be parsed).
- Row 4 (`deribit_pcr_processor.py:237` `short_pcr or overall_pcr
  or 1.0`): **DROP per §3.D**, increments
  `deribit_short_pcr_missing_dropped`; the hardcoded `1.0`
  default is REMOVED (was actively biasing PCR signal).
- Rows 5, 6, 7, 8 (`orderbook_processor.py:101-111` /
  `:121-122` / `:136-137` / `:240-242`): **DROP per §3.D**.
  Row 5: `orderbook_fetch_dropped` (whole-book fetch failure).
  Rows 6+7: `orderbook_level_malformed_dropped` (per-level
  drop, book continues with surviving levels). Row 8:
  `orderbook_process_exception_dropped` (bare-except at
  process() top; whole orderbook signal dropped for the
  decision).
- Row 9 (`divergence_processor.py:107-108` missing metadata):
  **DROP per §3.D**, increments
  `divergence_metadata_missing_dropped`.
- Row 10 (`divergence_processor.py:127-129` Coinbase missing →
  use polymarket momentum): **DROP per §3.D** with explicit
  REMOVAL of the polymarket-momentum substitution. Increments
  `divergence_coinbase_missing_dropped`. The substitution was a
  silent semantic corruption; the principle forbids it.
  **IMPLEMENTATION DIRECTIVE (v22, fixes round-18 P1 scope-leak
  finding)**: removing the elif at lines 127-129 ALONE is
  insufficient because `spot_momentum` keeps its default `0.0`
  from the init at line ~122, and `0.0` STILL satisfies the
  extreme-prob fade gating conditions
  `spot_momentum <= 0.001` (line ~141) and
  `spot_momentum >= -0.001` (line ~175) — the divergence
  processor would continue emitting BEARISH/BULLISH fade
  signals on corrupted state. The implementer MUST add an
  EARLY-RETURN at the top of `process()`: as the FIRST
  statement after the `is_enabled` guard (around line ~108),
  `if spot_price is None: self._drop_counters['divergence_coinbase_missing_dropped'] += 1; return None`.
  This drops the divergence signal entirely from the decision's
  signals[] (NOT merely the substitution); subsequent fade /
  momentum branches never run; the counter is incremented
  exactly once per Coinbase-missing decision. TC86 sub-case
  (d-divergence-coinbase) is updated to assert that the
  produced signals[] contains ZERO divergence signals AND
  drop_counters.divergence_coinbase_missing_dropped == 1 on the
  fixture. Without this directive, TC86 would falsely pass
  (the SIGNAL 2 momentum branch trivially fails `abs(0.0) >= 0.003`
  so the fixture description "divergence signal OMITTED" is
  satisfied for that branch, masking the SIGNAL 1 leak).
- Row 11 (`divergence_processor.py:221-222` confidence below
  threshold): **POLICY FILTER per §3.D** (NOT a fallback);
  increments `signal_below_min_confidence_filter`.
- Row 12 (`signal_fusion.py:125-127` total_contrib < 0.0001):
  **POLICY FILTER per §3.D**; increments
  `fusion_below_min_contrib_filter`.
- Row 13 (`risk_engine.py:70-77` env defaults): **Promote to
  required env** (unchanged from v12 disposition; foundational
  config, not droppable per §3.D guard-rail 2).
- Row 14 (`risk_engine.py:302` `metadata.get("entry_time",
  datetime.now())`): **Remove** (unchanged; foundational
  trading-state metadata, not droppable per §3.D guard-rail 3).
- Row 15 (`bot.py:5593-5596` executor_returned_false): **Keep,
  approved** (existing v12 disposition; the implementer files
  `APPROVE THIS FALLBACK` before Phase Beta with the v12
  justification).
- Rows 16, 17 (`calibration_decision_join.py` /
  `estimate_decision_results.py` per-instance fallbacks): per
  the v21 principle, every malformed-data drop in these files
  is resolved as **DROP per §3.D** with a counter added to
  `drop_counters` (the implementer extends the §3.D closed
  enum as needed when extracting Alpha-4 shared helpers).
  Every NON-malformed-data fallback (e.g., `or default` that
  substitutes a value rather than dropping) is **Remove** per
  the existing v12 discipline.
- Row 18 (`signal_fusion.py:95` `self.weights.get(signal.source,
  self.weights["default"])`): **DROP per §3.D** (v21 replaces
  the v20 "grandfathered" wording). An unknown signal source is
  unexpected data; the signal is dropped; increments
  `unknown_signal_source_dropped`. Implementation directive
  (v22 fix for round-18 P1): the `"default"` key is REMOVED
  ENTIRELY from `PRODUCTION_DEFAULT_WEIGHTS` (which Beta-5
  introduces at the bot's startup module per §6.2 Beta-5 —
  it does not yet exist in the current code; the v22 reference
  is forward-looking to the Beta-5 dict definition, NOT to
  any current `signal_fusion.py` symbol). Keeping the
  `"default"` key as dead data would be a Chekhov's gun — a
  future contributor might plug it back in. The lookup path
  changes from
  `self.weights.get(signal.source, self.weights["default"])`
  to:
  `weight = self.weights.get(signal.source)
   if weight is None:
       drop_counters['unknown_signal_source_dropped'] += 1
       continue  # drop this signal from fusion`
  A new unit test (TC61c, paired with the existing TC61b
  static `set_weight()` check) asserts that
  `PRODUCTION_DEFAULT_WEIGHTS` contains EXACTLY the six
  processor-source keys and DOES NOT contain a `"default"`
  key. Beta-5's `PRODUCTION_DEFAULT_WEIGHTS` definition
  therefore has SIX keys (not seven as previously specified —
  v22 supersedes v15's "SEVEN keys (six processor sources +
  default)" wording).

The §4.5 closed enumeration is finalized only after every item is
dispositioned. DROP-per-§3.D items DO NOT add §4.5 Unobservable
enum values; they add §3.D `DropClass` enum values instead (the
two enums are distinct surfaces per §3.D's enum-distinction note).
If an item is REMOVED, no enumeration value is added for it. If
KEPT under per-instance approval (Row 15 only), an enumeration
value like `executor_returned_false_safety_net` corresponds to it.

### 3.A.1 New Implementation-Path Fallbacks (v21 — recorder-internal I/O)

The v20 audit surfaced FIVE potential implementation-introduced
fallbacks (originally C1-C5 in the audit). v21 dispositions:

- **C1 — Skipped-log write inner try/except**: KEPT, documented
  as NOT a fallback under Rule 1. The existing §5.6 v15 pattern is
  RETAINED: when `os.write()` for the main raw line fails, the
  recorder attempts the `raw_decisions_skipped.jsonl` write; if
  THAT also fails, the inner failure is captured as
  `skipped_write_exc` and the recorder re-raises the ORIGINAL
  raw-write OSError with `raise original_exc from skipped_write_exc`
  (the skipped-log failure becomes `__cause__` for observability).
  The try/except DOES NOT mask any error — the original raw-write
  OSError propagates unchanged and the bot fail-stops via
  `DecisionRecord.__exit__`. The inner try/except exists ONLY to
  preserve the chain of failure context (Python's PEP 3134
  semantics on re-raise within an except block). Per user guidance
  2026-05-24 ("log cannot write → throw, exit app, no problem"),
  the bot fail-stop outcome is the user-approved behaviour; the
  chained `__cause__` is bonus diagnostic context that costs
  nothing operationally. Not a Rule-1 fallback because no error
  is hidden, substituted, or recovered.
- **C2 — Phase Delta loader truncated trailing-line tolerance**:
  resolved by §3.D drop principle (increment
  `loader_truncated_trailing_line_dropped` per record). Not a
  bare fallback; observable drop.
- **C3 — `lsof` diagnostic enrichment at flock-contention**:
  small try/except around the `subprocess.run(["lsof", ...])`
  call, logs "could not identify lock holder via lsof: <err>" on
  failure; the underlying flock-contention error STILL propagates
  unchanged (the try/except only suppresses the lsof failure, not
  the main flock-contention failure). Resolved under §3.D as a
  "diagnostic data dropped" pattern; the lsof output is treated
  as drop-eligible diagnostic data; the main error never masks.
  No new counter (single-event diagnostic; logged inline).
- **C4 — Sidecar dedup index seeding at recorder `__enter__`**:
  uses explicit `if path.exists():` check (NOT try/except). On
  day-1 (no prior sidecar), logs "sidecar file not found for
  {date}, starting with empty dedup index" and proceeds. Normal
  initial-state handling; not a fallback at all.
- **C5 — `os.fsync` retry-once-then-throw**: **APPROVED
  FALLBACK** per user reply 2026-05-24 ("APPROVE THIS FALLBACK :
  Option A"). The recorder catches the first `os.fsync` OSError,
  logs a warning, retries the fsync EXACTLY ONCE, and on the
  second failure raises a chained OSError that fail-stops the
  bot. Approval rationale (Rule 1 documentation):
    * **Control-flow.** `try: os.fsync(fd); except OSError as e1:
      log.warning('fsync failed, retrying once: {e1}'); try:
      os.fsync(fd); except OSError as e2: raise OSError(
      f'fsync failed twice: original={e1}, retry={e2}') from e2`.
    * **Cases enumerated.**
      (a) fsync succeeds first attempt: normal path, no retry.
      (b) fsync fails once then succeeds on retry: log line at
          WARNING level; recorder continues normally; bot does
          NOT fail-stop.
      (c) fsync fails both attempts: chained OSError raised;
          recorder.__exit__ propagates per the W2 write-failure
          sub-path (raw_decisions_skipped.jsonl written first
          per §5.6, then re-raise); bot fail-stops.
    * **Justification.** fsync failures are commonly transient
      at the OS / storage-layer (NFS hiccup, brief I/O queue
      saturation, block-device reconfiguration); a single retry
      catches the transient class without masking the persistent
      class. Without the retry, every transient fsync flake
      would fail-stop the bot — high operational cost for a
      class of errors that self-resolve.
    * **Why primary path can't be reliable.** OS-level fsync
      semantics are not under application control; the
      application cannot prevent transient I/O hiccups.
    * **Implementer's code-comment.** `# APPROVE THIS FALLBACK
      2026-05-24 (Rule 1, Option A); see §3.A.1 C5 approval
      block`. TC87 (new in v21) asserts the retry-once-then-
      throw behaviour with a fault-injected fsync mock.

### 3.B Data Retention / Vendor ToS Pre-Phase-Alpha Gate

Before Phase Alpha merges, document per vendor whether raw payload
retention is permitted, in `docs/RAW_DECISION_SNAPSHOT_OPERATIONS.md`:

- Polymarket CLOB (order book payloads)
- Coinbase Exchange (spot ticker payloads)
- Alternative.me (Fear & Greed payloads)
- Deribit (option summaries)

Each vendor has a per-vendor opt-in env var (§12). Default is hash-only
— the recorder stores `raw_payload_hash` but omits the raw body,
marking the body field `_unobservable: tos_retention_not_opted_in`.

### 3.C Why Extending `DecisionInputSnapshot` Is Not a Migration

Phase Beta extends the frozen `DecisionInputSnapshot` dataclass with
new in-memory fields. Per CLAUDE.md Rule 2, this is NOT a migration
because:

1. The dataclass is in-process state, never persisted.
2. The change is mostly additive. New fields (`yes_quote_timestamp`,
   `no_quote_timestamp`) are pure additions. The `price_history`
   element-type change (`Decimal` → `PriceHistoryEntry`) IS a
   structural change to an existing field; it is not a migration
   under Rule 2 because every existing reader is updated in the
   same Beta-1 diff. The complete writer + reader enumeration is in
   §6.2 Beta-1. The Phase Beta byte-equal-fixture test (TC06)
   catches verdict regressions; a Beta-1 static check asserts zero
   unchanged element-access sites (`historical_prices[N]` patterns
   without `.value`).
3. No transformation of existing on-disk data; the dataclass instance
   is constructed fresh per decision.
4. No rollback transform needed because there is nothing to roll back
   on disk.
5. Test fixture corpora under `tests/fixtures/raw_corpus/**` are CI
   artifacts, not production state. Rebuilding them when the schema
   changes is normal CI activity, not a migration under Rule 2.

The implementer documents this in the Phase Beta diff cover letter. If
any reviewer disagrees, they file the M3 request before Phase Beta
merges.

### 3.D Malformed/Unexpected Data Drop Principle (v21, user-approved 2026-05-24)

A unifying design principle that resolves the majority of §3.A
pre-existing fallback dispositions. Authorized by user reply
2026-05-24 ("any malformed data or unexpected data, cannot be part
of analysis or trading signal, it should be dropped").

**THE PRINCIPLE.** When the recorder, an upstream data fetcher, or a
signal processor encounters data that is malformed (parse failure,
unexpected type, missing required field) OR unexpected (value
outside documented range, source unknown to the consumer), that
specific data element MUST be dropped from analysis. Dropping is
the canonical disposition for upstream data anomalies; per-instance
Rule-1 approval is NOT required when the implementation satisfies
the three guard-rails below.

**THREE GUARD-RAILS (mandatory; failure of any one demotes the drop
back to a Rule-1 fallback requiring per-instance approval).**

1. **COUNTED + OBSERVABLE.** Every drop increments a closed-enum
   structured counter that lands in the §4.2 top-level
   `drop_counters` block of the raw snapshot. The counter is
   keyed by drop class (e.g., `deribit_fetch_dropped`,
   `orderbook_levels_dropped_n`, `unknown_signal_source_dropped`).
   Silent drop (no counter increment) is FORBIDDEN and degrades
   to a Rule-1 fallback. TC86 (new in v21) asserts every drop
   class has a corresponding counter key AND the counter is
   incremented exactly once per drop event.
2. **UPSTREAM-OF-DECISION SCOPE.** Drop applies ONLY to data that
   feeds analysis / signal generation BEFORE the final decision is
   computed. Examples of legitimate drops: an individual malformed
   order-book level (drop the level, keep the book); a failed
   Deribit HTTP fetch (drop the Deribit signal contribution);
   a missing Coinbase spot price (drop the divergence signal,
   NOT substitute polymarket momentum). FOUNDATIONAL state that
   the trading decision depends on for correctness MUST NOT be
   dropped: missing config values (MAX_POSITION_SIZE etc.),
   missing position metadata (entry_time), missing risk-engine
   state. These must fail-stop (existing §3.A REMOVE
   dispositions stand).
3. **NEVER IN LIVE-TRADING VERDICT PATH.** Drop NEVER applies
   downstream of the EV-gate computation or inside risk_engine.
   If risk_engine sees malformed position data, the bot must
   fail-stop, not drop-and-continue. Dropping anywhere in the
   verdict-computation or order-submission path = real money on
   corrupted state, which the live-equivalence boundary (§9 /
   CLAUDE.md Rule 3) forbids absolutely.

**Drop-class closed enumeration** (in `raw_decision_snapshot.DropClass`
enum, mirrors §4.5 Unobservable closed-enum discipline):

```text
deribit_fetch_dropped                # B1/B2 — full HTTP fetch failure
deribit_instrument_parse_dropped     # B3 — malformed instrument name skipped
deribit_short_pcr_missing_dropped    # B4 — short_pcr key absent in cache slot
orderbook_fetch_dropped              # B5 — full CLOB fetch failure
orderbook_level_malformed_dropped    # B6/B7 — individual book level rejected
orderbook_process_exception_dropped  # B8 — bare `except Exception` in process()
divergence_metadata_missing_dropped  # B9 — divergence processor missing context
divergence_coinbase_missing_dropped  # B10 — Coinbase spot unavailable, divergence signal dropped (no polymarket-momentum substitution)
unknown_signal_source_dropped        # B17 — fusion saw a source not in PRODUCTION_DEFAULT_WEIGHTS
loader_truncated_trailing_line_dropped  # C2 — Phase Delta loader truncated-tail tolerance (counted per record)
```

Adding a new class requires editing both the enum AND this list;
TC86 reconciles. The drop-class enum is DISTINCT from §4.5
`Unobservable` (Unobservable marks individual field-level
unobservability; DropClass marks decision-level data-source drops
with counter semantics).

**Reclassified items (NOT fallbacks, NOT drops — policy filters).**
The following §3.A items are NEITHER fallbacks NOR drops; they are
deliberate threshold/policy filters applied to well-formed data:

- B11 (`divergence_processor.py:221-222` `confidence <
  min_confidence` → None): the data is well-formed; the signal
  simply fails the configured confidence threshold. RECLASSIFIED
  as `signal_below_min_confidence_filter` policy filter; observed
  via a separate `policy_filter_counters` block per §4.2 (also
  added in v21). NOT covered by Rule 1 because no failure is
  hidden — the policy is doing exactly what it was configured to do.
- B12 (`signal_fusion.py:125-127` `total_contrib < 0.0001`):
  same — RECLASSIFIED as `fusion_below_min_contrib_filter` policy
  filter, observed via `policy_filter_counters`.

TC86 also asserts the policy-filter counters increment correctly.

**§3.A dispositions referencing this principle.** The §3.A
table rows below now reference §3.D for their disposition (see
the row-by-row updates within §3.A itself).

## 4. Raw Snapshot Schema

### 4.1 Format, Atomicity, and Lock Discipline

- One JSON object per candidate decision, JSONL output.
- UTF-8, no BOM, `ensure_ascii=False`, newline-terminated.
- The recorder maintains TWO distinct lock surfaces:
  - **Per-decision state**: owned by exactly one thread for the
    duration of `_make_trading_decision_body`. Updates via
    `update_field()` / `record_gate()` / `record_signal()` /
    `record_fusion_diagnostics()` are pure in-memory operations on
    this state object. NO LOCK IS TAKEN by these helpers. (TC56c
    enforces.)
  - **Append lock**: a single process-wide `threading.Lock` (NOT
    `RLock`, per TC29c) acquired exactly once at `__exit__` for the
    duration of the final write. Acquisition discipline: always
    AFTER any other lock the body might hold (so the recorder is
    never the bottom of a deadlock cycle).
- Plus a corpus-wide `fcntl.flock` held for the process lifetime
  (§5.7). Combination prevents intra-process byte interleave AND
  inter-process corpus sharing.
- Each record encoded in memory; written in ONE `os.write()` call;
  `os.fsync` per record (mirrors `decision_log._atomic_append_jsonl`).
- For any field whose encoded length exceeds 4096 bytes, the body is
  written to the content-addressed sidecar (§5.3). No
  inline-vs-sidecar conditional (that branch would be a fallback
  under M4).

### 4.2 Top-Level Fields

```text
schema_version                int, currently 1 (first-ever version).
                              GREENFIELD JUSTIFICATION (per §3.C, locked
                              by user as "Option A" in round-13): no
                              raw_decisions_*.jsonl record has EVER been
                              written to disk by any commit on any
                              branch — the recorder module
                              (`raw_decision_snapshot.py`), the analysis
                              package (`analysis/*`), and the corpus
                              directory family are entirely new. The
                              universal trailing `final_decision` row is
                              part of `schema_version=1` from the FIRST
                              commit that lands Phase Alpha + Gamma; no
                              v1-without-final_decision record will ever
                              exist; no CLAUDE.md Rule 2 migration is
                              triggered (there is nothing to migrate).
                              The `schema_version` field is reserved for
                              future forward-additive changes (a hypothetical
                              v2 would append fields per §4.6's "additive-
                              only" rule); the §4.6 "Loader reads any
                              schema_version ≤ its own" clause exists for
                              those future bumps, not for any v0→v1 transit.
run_id                        per-process UUID, set at bot startup
decision_id                   matches decisions.jsonl decision_id
source_repo                   "Polymarket-BTC-15-Minute-Trading-Bot"
git_sha                       resolved ONLY when capture is enabled
                              (RAW_DECISION_SNAPSHOT_DIR set). Disabled
                              deploys do not require .git/.
                              SINGLE SOURCE OF TRUTH: required env
                              POLYBOT_GIT_SHA. No git-rev-parse probe;
                              missing env raises at startup. This avoids
                              the probe-then-env fallback shape (which
                              would qualify as M4-regulated). Git-deploy
                              operators set POLYBOT_GIT_SHA via a
                              startup wrapper:
                              `POLYBOT_GIT_SHA="$(git rev-parse HEAD)"
                              exec polybot ...`; tarball deploys set it
                              from the build manifest. The recipe is
                              in docs/RAW_DECISION_SNAPSHOT_OPERATIONS.md.
strategy_version              from strategy_version.STRATEGY_VERSION
bot_mode                      "live_gate" | "shadow_policy" | "simulation"
                              (only three values reachable inside
                              _make_trading_decision_body; recorder
                              constructor raises on any other value)
captured_at                   UTC-aware datetime at __exit__ write
recorder_internal_failure     {"exception_type": str, "exception_str": str,
                              "step": "field_map_copy" | <future enum>} |
                              null. Populated by Gamma-4 when the FMC
                              fallback fires (or any future recorder-
                              internal failure that the APPROVED-FALLBACK
                              discipline catches); null on the happy path.
                              CRITICAL: this is the SINGLE OBSERVABILITY
                              channel for the user-approved FMC fallback
                              (v16 redesign — v15 used a trailing exception
                              row in gates[], which broke the universal-
                              trailing invariant and cascaded P0s into
                              Delta-7/TC83/Zeta-7/Eta-3). The top-level
                              field PRESERVES the post-append
                              gates[-1]==final_decision invariant for
                              every record while still
                              satisfying the user's "identify the
                              exception record AND show the reason"
                              requirement (the field is a top-level
                              record property; non-null means fallback
                              fired; the type+str carry the failure
                              detail). Replayer/harness MUST ignore this
                              field (it's recorder-side observability,
                              not a property of the captured run).
                              Delta-7 (new check) asserts the field shape.
decision_reference_time       from DecisionInputSnapshot.reference_time
drop_counters                 dict[str, int] — closed-enum keyed by
                              `raw_decision_snapshot.DropClass` enum
                              members (§3.D). Every drop event during
                              this decision increments the matching
                              counter exactly once. Happy path: all
                              counters are 0. Required-present on every
                              record; never null; never absent. Schema:
                              {"deribit_fetch_dropped": int,
                              "deribit_instrument_parse_dropped": int,
                              "deribit_short_pcr_missing_dropped": int,
                              "orderbook_fetch_dropped": int,
                              "orderbook_level_malformed_dropped": int,
                              "orderbook_process_exception_dropped": int,
                              "divergence_metadata_missing_dropped": int,
                              "divergence_coinbase_missing_dropped": int,
                              "unknown_signal_source_dropped": int,
                              "loader_truncated_trailing_line_dropped":
                              int}. TC86 asserts schema + correct
                              increment-per-drop-event semantics.
policy_filter_counters        dict[str, int] — closed-enum for policy
                              threshold filters that are NOT fallbacks
                              and NOT drops, but observable for analysis.
                              Schema:
                              {"signal_below_min_confidence_filter": int,
                              "fusion_below_min_contrib_filter": int}.
                              Happy path: counters are 0. Same TC86
                              coverage.
body_runtime_seconds          captured_at - decision_reference_time
                              (Decimal-as-str). Informational; equals total
                              wall-clock duration of the body. NOT the
                              snapshot-staleness used at freshness gates.
decision_snapshot_age_at_gate {
                                before_context: <Decimal-as-str> | _unobservable,
                                before_signals: <Decimal-as-str> | _unobservable,
                                before_execution: <Decimal-as-str> | _unobservable,
                                before_intent_persistence: <Decimal-as-str> | _unobservable,
                              }
                              Populated by FIELD_MAP from rec.update(
                              decision_snapshot_age_seconds=...) at each
                              freshness-gate evaluation site. Replayer
                              compares each value against the swept
                              MAX_DECISION_SNAPSHOT_AGE_SECONDS. M10
                              ("negative is fatal") applies to EACH gate-
                              time age individually.

provenance: {
  python_version             "3.11.6"-style
  os_release                 platform.release()
  tz_env                     os.environ.get("TZ", "")
  sys_float_repr_style       "short" — verified at startup
  container_digest           env "POLYBOT_IMAGE_DIGEST" or
                             _unobservable: container_digest_unset
  requirements_lock_sha256   sha256(requirements_lock_path bytes),
                             resolved at startup. Lock-file path is
                             `POLYBOT_REQUIREMENTS_LOCK_PATH` env if
                             set, else `requirements.txt`. Missing
                             path raises at startup.
}

market: { market_slug, condition_id, market_start_time,
          market_end_time, market_timestamp, sub_interval,
          seconds_into_sub_interval, trade_window_label,
          yes_token_id, no_token_id, yes_instrument_id,
          no_instrument_id, cached_yes_token_id }
# All numeric fields are serialized as Decimal-as-str via
# Decimal(str(value)) coercion in the recorder. This applies to any
# float field (e.g., seconds_into_sub_interval is Optional[float] on
# the snapshot per decision_snapshot.py:55). Float→str→Decimal
# round-trip preserves the value at the precision of the source
# float; cross-version repr drift is bounded by sys_float_repr_style
# == "short" assertion at startup (provenance block).

frozen_quotes: {
  current_price                YES mid / probability (Decimal)
  yes_bid, yes_ask, yes_mid
  yes_quote_timestamp          UTC-aware; captured into the extended
                               DecisionInputSnapshot per Beta-1
  no_bid, no_ask, no_mid
  no_quote_timestamp           UTC-aware; null when no_token_id
                               absent, in which case the four no_*
                               value fields are Unobservable.no_token_id_absent
  stable_tick_count
}

price_history: [
  { index, price, ts, source, synthetic }
]
# index: 0-based array offset (purely positional).
# ts: UTC-aware OR null when the upstream Nautilus event lost it.
# source: "live_quote_tick" | "synthetic_startup" — closed enum.
# synthetic: bool, true iff source == "synthetic_startup".

tick_buffer: [ { ts, price } ]
# ts: UTC-aware. INSERTION ORDER PRESERVED. TickVelocity's "closest
# tick to target" tie-break: FIRST in insertion order wins (strict
# < per tick_velocity_processor.py:96-109).

yes_order_book: {
  fetched_at                   UTC-aware
  token_id
  bids: [{ price, size }]
  asks: [{ price, size }]
  raw_payload_hash             sha256, per §5.5
  raw_payload                  inline iff RAW_DECISION_SNAPSHOT_INCLUDE_POLYMARKET_RAW=1;
                               else Unobservable.tos_retention_not_opted_in
}
no_order_book: same; Unobservable.no_token_id_absent when applicable.

fear_greed: {
  fetched_at, source_timestamp, value, classification,
  raw_payload                  inline iff opt-in env
  raw_payload_hash             sha256
}

coinbase_spot: {
  fetched_at, price,
  raw_payload                  inline iff opt-in env
  raw_payload_hash             sha256
}

coinbase_spot_history_before_process: [
  { fetched_at, price }
]
# Captured INSIDE the body, AFTER mode resolution selects the live or
# shadow processor instance, and BEFORE the chosen processor's
# process() runs (Beta-1 + Gamma-2). JSON ARRAY preserving insertion
# order; replayer pre-seeds `_spot_history` by iterating in order.

deribit_pcr: {
  used_cached_result           bool
  cache_time_before            UTC-aware; always populated
  cache_age_seconds            Decimal
  fresh_fetch_performed        bool. true iff used_cached_result==false
                               AND _fetch_pcr completed without raising.
  fetched_at                   UTC-aware. Unobservable.deribit_cache_hit_no_fresh_fetch
                               when used_cached_result==true. If the
                               §3.A KEEP-approved fallback fired this
                               decision, Unobservable.deribit_fetch_silently_failed.
  overall_pcr, short_pcr, put_oi, call_oi, short_put_oi,
  short_call_oi, total_contracts
  raw_option_summaries         inline iff RAW_DECISION_SNAPSHOT_INCLUDE_DERIBIT_RAW=1
  raw_option_summaries_hash    sha256 of the canonical-bytes encoding
                               (per §5.5) of the raw `data["result"]`
                               LIST as returned by Deribit, EXCLUDING
                               any synthesized fetched_at field. Cache
                               retains the raw list bytes + hash from
                               fetch time; cache-hits reuse them
                               without recomputation. TC60 enforces
                               byte-equal hash across two cache-hit
                               records.
}

effective_config: { ... }   # §4.3, populated by the helper from §6.2 Beta-6.

risk_engine_state: {
  positions: [ ... ]            # DEEP COPY at capture; TC61 verifies
                                # mutation after capture does not
                                # affect recorded value.
  total_exposure_usd, open_position_count
  daily_pnl, daily_trade_count
  current_balance, peak_balance
  stats_date                    ISO date string
  stats_date_source             "captured_pre_reset" | "captured_post_reset"
                                — see §6.2 Beta-8 for semantics.
  max_drawdown_pct, max_loss_per_day  # from effective_config
}

signals: [
  { signal_id, source, timestamp, signal_type, direction,
    strength, strength_value, confidence, score,
    current_price, target_price, stop_loss, metadata }
]
# signal_id = f"{decision_id}:{processor_name}:{N}" where N is the
# instance-attribute `self._signal_ordinal`, reset to 0 at every
# process() entry and post-incremented per emitted TradingSignal
# (Beta-3 spec). Deterministic; replay reconstructs the same set.
# `timestamp` is UTC-aware, sourced from the `now=` kwarg the
# recorder injects.

fusion: {
  call_inputs: {
    min_signals, min_score, recency_window_seconds
    weights                    SHALLOW COPY (dict[str, float]) of
                               fusion_engine.weights at fusion-call
                               time (TC61 verifies post-capture
                               mutation does not retroactively alter
                               the recorded snapshot).
    now                        UTC-aware reference_time injected
  }
  bullish_contrib, bearish_contrib, total_contrib
  num_bullish, num_bearish
  recent_signal_ids            signal_ids passing the recency filter
  score, direction, confidence
  rejected_reason_if_none      one of: null (success) | "no_signals"
                               | "min_signals_unmet" |
                               "recency_window_empty" |
                               "total_contrib_below_floor" |
                               "consensus_score_below_min"
}
# Populated on every fusion call by the Beta-5 refactor that sets
# _last_diagnostics at the start of fuse_signals() and finalizes at
# every return point. The recorder reads last_fusion_diagnostics()
# IMMEDIATELY after fuse_signals() returns, while still inside any
# enclosing _signal_processing_lock window (Gamma-2 + TC62).

gates: [
  { name, passed, reason, inputs, output }
]
# Names, reasons, inputs, output per §4.4.

depth_replay: {
  selected_side, selected_token_id, selected_order_book_side,
  top_of_book_entry, order_type, accepted_limit_price,
  submitted_limit_price, limit_order_token_qty,
  instrument_price_precision, instrument_size_precision,
  estimated_vwap, estimated_tokens_filled,
  estimated_actual_cost, depth_fully_filled
}
```

### 4.3 Effective Config Fields

Beta-6 introduces `effective_decision_config.py` exposing
`build_effective_decision_config(env_reader, processor_registry,
fusion_engine, risk_engine) -> dict`. Called EXACTLY ONCE per body
invocation, AFTER `observation_only` is resolved, with `fusion_engine`
and `processor_registry` selected for that observation mode (live or
shadow). The returned dict is the single source of truth for every
value listed below for the rest of the call.

Order / EV / sizing / risk / freshness:

```text
ORDER_TYPE
QUOTE_STABILITY_REQUIRED
LIMIT_REQUIRED_EDGE
LIMIT_IOC_FILL_POLICY
EV_FEE_BUFFER
EV_SPREAD_BUFFER
REQUIRE_SIGNAL_CONFIRMATION
MIN_SIGNAL_CONFIDENCE
MARKET_BUY_USD
SIZING_MODE
PCT_OF_FREE_COLLATERAL_PER_TRADE
MAX_ACCOUNT_STATE_AGE_SECONDS
MAX_DECISION_SNAPSHOT_AGE_SECONDS
BALANCE_SAFETY_BUFFER_USD
MAX_POSITION_SIZE
MAX_TOTAL_EXPOSURE
MAX_POSITIONS
MAX_DRAWDOWN_PCT
MAX_LOSS_PER_DAY
POLYMARKET_LIMIT_MIN_TOKENS
LIVE_MIN_MARKET_BUY_USD
```

Trend filter / liquidity (currently hardcoded; promoted to env in
Beta-6 per the §12 "promoted-constant" convention with required-
presence validation):

```text
trend_up_threshold              env TREND_UP_THRESHOLD
trend_down_threshold            env TREND_DOWN_THRESHOLD
liquidity_floor                 env LIQUIDITY_FLOOR
```

Fusion parameters (hardcoded in `bot.py:5280` and `signal_fusion.py`;
promoted to env-driven via `FUSION_MIN_SIGNALS`, `FUSION_MIN_SCORE`,
`FUSION_RECENCY_WINDOW_SECONDS`):

```text
fusion_min_signals
fusion_min_score
fusion_recency_window_seconds
fusion_weights_by_source        resolved dict from fusion_engine.weights
                                at call time (SHALLOW COPY per §4.2)
```

Per-processor parameters (read via each processor's `effective_params()`
method from Beta-4):

```text
spike_threshold, spike_lookback_periods, spike_min_confidence,
spike_velocity_threshold

sentiment_extreme_fear_threshold, sentiment_extreme_greed_threshold,
sentiment_min_confidence

divergence_min_confidence, divergence_momentum_threshold,
divergence_extreme_prob_threshold, divergence_low_prob_threshold,
divergence_spot_history_max_len   constructor arg per Beta-4

orderbook_imbalance_threshold, orderbook_wall_threshold,
orderbook_min_book_volume, orderbook_min_confidence,
orderbook_top_levels

tick_velocity_threshold_60s, tick_velocity_threshold_30s,
tick_velocity_min_ticks, tick_velocity_min_confidence,
tick_velocity_tolerance_seconds   constructor arg per Beta-4

deribit_bullish_pcr_threshold, deribit_bearish_pcr_threshold,
deribit_max_days_to_expiry, deribit_min_open_interest,
deribit_cache_seconds, deribit_min_confidence
```

### 4.4 Gate Names, Rejection-Reason Mapping, Required Inputs, Output

**Indexing convention (CANONICAL, applies to every cross-reference
to `gates[]` in this document).** All `gates[i]` indices in this
document are POST-APPEND (the reader-perspective view of the
record AFTER the recorder's `__exit__` has completed every step
in §6.3 Gamma-4). Post-append `gates[-1]` is ALWAYS the trailing
`final_decision` row on EVERY record (v16 universal invariant
— the v15 FMC-fallback carve-out is RETRACTED; FMC observability
now lives in the top-level `recorder_internal_failure` field per
§4.2). When this document needs to refer to the snapshot BEFORE
the trailing append, it uses the explicit name `gates_pre`
(e.g., Gamma-4 step (2)). The recorder's internal pre-append
view and the post-append reader view differ by exactly one
index; mixing them is a common source of off-by-one bugs and
every cross-reference in this document is pinned to one
convention or the other. RP13 (docs-lint, v16-revised) enforces:
no plan section references `gates[-N]` (for any N) within a
"gates[-N] cluster" (a contiguous sequence of paragraphs each
containing at least one `gates[-N]` reference, terminated by
the first paragraph with NO such reference) without naming
"post-append" or "gates_pre" SOMEWHERE in the cluster (not
necessarily the same paragraph — the v15 "same paragraph" scope
flagged 10+ existing paragraphs as false-positives; v16 relaxes
to per-cluster).

Each `gates[]` entry: `name`, `passed`, `reason`, `inputs`, `output`.
For `passed=true`, `reason="ok"`. For `passed=false`, `reason` is the
verbatim string passed to `<receiver>.reject(...)` (the receiver is
usually `rec` but the freshness-helper at `bot.py:5073` uses
`decision_record.reject(...)`; TC02a accepts both).

The table below maps every GATE-NAME LITERAL passed as the first arg
to `<receiver>.reject(GATE_NAME, REASON)` inside
`_make_trading_decision_body` (and its called helpers reachable from
inside the body — explicitly NOT including the TWO Gamma-3 NOT-WIRED
`.reject(...)` sites at `bot.py:4854` (inside
`_record_decision_snapshot_capture_exception`) and `bot.py:4908`
(inside `_record_decision_executor_enqueue_exception`)). Sites walked by TC02a:
`bot.py` lines `1210, 1217, 1224, 5073, 5097, 5176, 5186, 5195, 5255,
5283, 5335, 5353, 5363, 5372, 5385, 5396, 5415, 5423, 5429, 5435,
5453, 5501, 5532, 5552, 5563, 5596, 5659, 5680, 5693, 5701, 5713,
5724, 5754, 5766`. REASON strings can be dynamic f-strings (e.g.,
lines 5093-5094) — TC02a matches on the GATE-NAME literal only, not
on the reason.

The `exception` gate produced by `DecisionRecord.__exit__` is set via
direct field assignment at `decision_log.py:182`
(`self.fields["rejected_at_gate"] = "exception"`), NOT via a
`.reject()` call. TC02a's regex `\.reject\(` cannot match it. A
separate test (TC68b) statically asserts the literal `"exception"`
is the RHS of the single `self.fields["rejected_at_gate"] = ...`
assignment in `DecisionRecord.__exit__` and that the literal appears
in §4.4's `name` column (under the `exception` row). Adding any
other `self.fields["rejected_at_gate"] = <literal>` assignment in
`decision_log.py` fails TC68b unless the new literal is also added
to §4.4.

| `name` | Gate-name literals | `inputs` keys | `output` |
| --- | --- | --- | --- |
| `live_paused_unresolved_settlement` | `live_paused_unresolved_settlement` | `pending_settlement_count`, `pending_settlement_keys` | null |
| `quote_stability` | `quote_stability_below_configured_threshold` | `stable_tick_count`, `required` | null |
| `history_length` | `history_too_short` | `price_history_len`, `min_required` | null |
| `snapshot_freshness_before_context` | `decision_snapshot_stale_before_context` | `age_seconds`, `max_age_seconds` | null |
| `snapshot_freshness_before_signals` | `decision_snapshot_stale_before_signals` | `age_seconds`, `max_age_seconds` | null |
| `snapshot_freshness_before_execution` | `decision_snapshot_stale_before_execution` | `age_seconds`, `max_age_seconds` | null |
| `snapshot_freshness_before_intent_persistence` | `decision_snapshot_stale_before_intent_persistence` (emitted by `_live_order_reference_time_is_fresh` at `bot.py:5073` when called from `_place_real_order` at `bot.py:6175` with `gate_suffix="before_intent_persistence"`) | `age_seconds`, `max_age_seconds` | null |
| `no_signals` | `no_signals` | `processors_run`, `signals_emitted_count` | null |
| `fusion` | `fusion_no_consensus` | (see `fusion.call_inputs` in §4.2 — DO NOT duplicate) | `fusion.direction` (see §4.2 fusion block — DO NOT duplicate) |
| `trend_filter` | `trend_filter_neutral` | `current_price`, `trend_up_threshold`, `trend_down_threshold` | `trend_direction` ∈ {"long","short","neutral"} |
| `signal_confirmation` | `signal_confirmation_mismatch` | `fused_direction`, `trend_direction` | null |
| `min_signal_confidence` | `min_signal_confidence` | `fused_confidence`, `min_signal_confidence` | null |
| `side_quote_available` | `no_yes_quote`, `no_no_quote` | `side`, `yes_bid_ask_present`, `no_bid_ask_present` | `chosen_side` ∈ {"yes","no",null} |
| `depth_aware_entry` | `depth_aware_book_snapshot_missing`, `depth_aware_missing_token_id`, `depth_aware_token_side_mismatch`, `depth_aware_no_book`, `depth_aware_empty_asks`, `depth_aware_invalid_book_level`, `depth_aware_book_too_thin`, `depth_aware_limit_ioc_no_liquidity` | `selected_side`, `token_id`, `book_present`, `bid_levels`, `ask_levels`, `usd_to_spend_or_token_qty`, `order_type` | (see `depth_replay` in §4.2 — DO NOT duplicate) |
| `limit_price` | `limit_price_out_of_bounds` | `fused_confidence`, `accepted_limit_price`, `limit_required_edge`, `price_precision`, `submitted_limit_price` | `submitted_limit_price` |
| `limit_token_qty` | `limit_ioc_no_yes_instrument`, `limit_ioc_no_no_instrument`, `limit_ioc_instrument_not_cached`, `limit_ioc_below_min_tokens` | `budget_usd`, `submitted_limit_price`, `size_precision`, `polymarket_limit_min_tokens`, `computed_token_qty` | `computed_token_qty` |
| `ev_gate` | `ev_gate` | `fused_confidence`, `executable_entry`, `ev_fee_buffer`, `ev_spread_buffer`, `entry_source` | `ev_value` (Decimal computed inside the gate) |
| `position_size_below_minimum` | `position_size_below_live_minimum` | `resolved_trade_usd`, `live_min_market_buy_usd`, `sizing_mode` | null |
| `position_size_exceeds_max` | `size_exceeds_max_position_size` | `resolved_trade_usd`, `max_position_size`, `sizing_mode` | null |
| `balance_guard` | `balance_guard` | `free_collateral`, `required_free_collateral`, `balance_safety_buffer_usd`, `resolved_trade_usd` | null |
| `risk_engine` | `risk_engine` | (see `risk_engine_state` in §4.2 — DO NOT duplicate) + `proposed_size`, `proposed_direction`, `proposed_token_id` | `risk_engine_reason_string` |
| `liquidity_floor` | `liquidity_floor_yes_ask`, `liquidity_floor_no_ask` | `side`, `yes_ask_or_no_ask` (the raw frozen-quote value the production gate at `bot.py:5546`/`5557` compares — NOT `executable_entry`), `min_liquidity_threshold` (the configured floor; see Beta-10 below for promotion of the hardcoded `Decimal("0.02")` at `bot.py:5544` to the env-driven `LIQUIDITY_FLOOR`) | null |
| `executor_returned_false` | `executor_returned_false` (per §3.A row 15: KEPT in v5 as safety-net trailing gate with `APPROVE THIS FALLBACK`; deferred executor-refactor plan will subdivide into per-failure-mode gates) | `executor_returned_false_kind` ∈ {`live`, `paper`} | null |
| `exception` | (set by `DecisionRecord.__exit__` on unhandled exception per `decision_log.py:182`; also set by recorder `__exit__` for exceptions inside the body) | `exception_type`, `exception_str` | null |
| `final_decision` | NOT written via `.reject()`. Appended UNCONDITIONALLY by the recorder `__exit__` as the LAST element of `gates[]` on EVERY record (accept, reject, exception). Member of the closed `GateName` enum (Alpha-1). All indices below are POST-APPEND (reader perspective, after `__exit__` has finished): `final_decision` is `gates[-1]`; the prior-failing gate on reject (if any) is `gates[-2]`. `passed == (outcome == "accepted")`. `reason` is the literal `"accepted"` on accept, `gates[-2].name` on reject (the gate that actually fired), the literal `"exception"` on in-body exception. | `outcome` ∈ {`accepted`, `rejected`, `exception`} computed at write-time as: `accepted` iff (a) `depth_replay.selected_side is not None` AND (b) every prior `gates[i].passed == true` for `i ∈ [0, len(gates_before_append))`; `exception` iff an in-body exception is in flight at `__exit__` entry; `rejected` otherwise. `failing_gate` ∈ {gate-name string, `null`}: post-append `gates[-2].name` on reject; `null` on accept; on exception, the `gate_scope`-attributed name when the exception was raised inside a `gate_scope` block (per Gamma-4a) else `null`. | On accept: `{selected_side, selected_token_id, submitted_limit_price, accepted_limit_price, limit_order_token_qty, fusion_direction, fusion_confidence}` — values are REFERENCE-COPIED (no re-coercion) from the already-encoded Decimal-as-str slots in §4.2 `depth_replay.selected_side / .selected_token_id / .submitted_limit_price / .accepted_limit_price / .limit_order_token_qty` and `fusion.direction / .confidence`; the `output`-dict keys use the `fusion_` prefix on the two fusion fields only to namespace-disambiguate within the dict (the source-of-truth keys remain bare `direction` / `confidence` in §4.2). TC83 asserts byte-equality of each duplicated value against its §4.2 source slot. On reject / exception: `{"_unobservable": true, "reason": "final_decision_not_accepted"}` (NOT `null`; preserves §4.5's "never null" invariant; the new enum value `final_decision_not_accepted` is added to the §4.5 closed enumeration). |

`output` semantics: `null` when the gate's outcome is captured
entirely by `passed`/`reason`; a single scalar or short identifier
when the gate selects a value used by downstream code; a cross-
reference to a §4.2 block when the output would be a verbatim
duplicate (the recorder does NOT duplicate; the harness reads from
the referenced block).

Gates not evaluated because an earlier gate failed are OMITTED, not
written with `passed=false`. TC02 + TC02a enforce:
- TC02 asserts every gate name has at least one fixture record.
- TC02a is a static AST walk over every `<receiver>.reject(GATE_NAME,
  REASON)` call in `bot.py` (any receiver name, matched by regex
  `\.reject\(`). It extracts the GATE_NAME first-arg AST node:
  - If GATE_NAME is a string literal (most sites), assert the literal
    appears in the table column "Gate-name literals".
  - If GATE_NAME is an f-string of the exact form
    `f"decision_snapshot_stale_{gate_suffix}"` (sites at `bot.py:5073`
    and `bot.py:5097`), assert that for every legal value of
    `gate_suffix` (closed enumeration:
    `{"before_context", "before_signals", "before_execution",
    "before_intent_persistence"}`), the materialized literal appears
    in the table column. The closed enumeration is declared in
    `raw_decision_snapshot.SNAPSHOT_STALE_SUFFIXES` and any new
    suffix triggers test failure unless added to BOTH the enumeration
    AND §4.4.
  - Any other non-literal first-arg fails the test.
  REASON column is informational only.
- The TWO Gamma-3 NOT-WIRED `.reject(...)` sites (literals at
  `bot.py:4854` "snapshot_capture_exception" and `bot.py:4908`
  "executor_enqueue_exception") are EXPLICITLY EXEMPT from TC02a's
  walked-set via a closed enumeration declared in
  `raw_decision_snapshot.GATE_LITERAL_EXEMPT_SET =
  {"snapshot_capture_exception", "executor_enqueue_exception"}`.
  Adding a new exempt entry requires explicit plan edit (TC02d
  asserts the exempt set has exactly those two entries; reduction
  fails CI, addition requires this plan to grow the set).
- TC02a additionally asserts `SNAPSHOT_STALE_SUFFIXES` equals the
  exact set of `gate_suffix` string-literal values passed into
  `_live_decision_snapshot_is_fresh` and `_live_order_reference_time_is_fresh`
  call sites in `bot.py` (extracted via AST walk over keyword args
  at those call sites). Reduction (a suffix declared in the enum
  with no caller) or addition (a new caller suffix not in the
  enum) fails CI. This mirrors TC02d's locked-set discipline for
  the freshness-suffix family.
- `decision_log.py:182` is handled SEPARATELY via TC68b (not
  TC02a) because the `exception` gate is set via direct field
  assignment, not via `.reject()`.
- The `final_decision` row is handled SEPARATELY via TC83 (not
  TC02a, NOT TC02c) because it is NEVER passed as a string literal
  to `.reject(...)` and is NEVER wrapped in `raw_rec.gate_scope(...)`
  — it is appended UNCONDITIONALLY by the recorder `__exit__`.
  `raw_decision_snapshot.AUTO_APPENDED_GATE_NAMES = {"final_decision"}`
  is the closed set of gate names whose member is appended on
  EVERY record (universal trailing). TC83(d) asserts equality.
  `raw_decision_snapshot.CONDITIONAL_TRAILING_GATE_NAMES =
  {"exception"}` is the closed set of gate names appended ONLY on
  the exception path. The TC02c exclusion set is the union of
  these two trailing sets plus the safety-net `executor_returned_false`:
  `TC02C_EXCLUSION_SET = AUTO_APPENDED_GATE_NAMES ∪
  CONDITIONAL_TRAILING_GATE_NAMES ∪ {"executor_returned_false"} =
  {"final_decision", "exception", "executor_returned_false"}`
  (three elements). Reduction of either trailing set fails CI;
  addition requires this plan to grow the set.

### 4.5 Unobservable Sentinel and Closed Enumeration

A field that is unobservable in a legitimate decision path is recorded
as `{"_unobservable": true, "reason": "<short_string>"}`. MUST NOT be
omitted, null, or substituted with a default.

Closed enumeration in `raw_decision_snapshot.Unobservable`:

```text
no_token_id_absent
deribit_cache_hit_no_fresh_fetch
deribit_fetch_silently_failed       only present if §3.A item kept
coinbase_spot_history_empty
tos_retention_not_opted_in
context_fetch_exception_pre_metadata
signals_exception_post_metadata
fusion_exception_post_signals
depth_aware_entry_exception
container_digest_unset
exception_before_set
final_decision_not_accepted         used for `final_decision.output` on
                                    every reject/exception record per §4.4
no_gate_fired_before_exit           used in v15 as the `reason` inside the
                                    standard §4.5 sentinel dict assigned to
                                    `final_decision.inputs.failing_gate`
                                    when an exception escapes the body before
                                    any gate has fired (i.e., failing_gate is
                                    `{"_unobservable": true, "reason":
                                    "no_gate_fired_before_exit"}` — uses the
                                    canonical sentinel shape, NOT a separate
                                    field). See Gamma-4 exception step (5).
# Plus the parameterised family gate_exception_<gate_name>. Producer:
# §6.3 Gamma-4 sub-bullet — when an exception is raised INSIDE a
# specific gate's evaluation body (e.g., a NoneType error during the
# ev_gate computation), the recorder marks that gate's `inputs` and
# `output` fields with `_unobservable: gate_exception_<gate.name>`
# AND appends a `name="exception"` gates entry at the POST-APPEND
# index `gates[-2]` (the trailing `gates[-1]` is ALWAYS
# `final_decision` per §4.4's universal-trailing invariant; v16
# fix). The cartesian expansion {gate_exception_<g> for g in §4.4
# names} is materialized at validator construction time as the
# explicit set. TC34 includes typo-suffix test: an unexpected
# suffix raises. TC47b asserts that a synthetic exception-inside-
# ev_gate produces (post-append):
# (a) gates[].inputs._unobservable.reason == gate_exception_ev_gate
# on the synthesized/updated `ev_gate` row at index `gates[-3]`;
# (b) gates[-2].name == "exception" with reason carrying the
# in-flight exception's type+str (per Gamma-4 exception step (4));
# (c) gates[-1].name == "final_decision" with
# inputs.outcome=="exception" and inputs.failing_gate=="ev_gate".
```

Unknown reason → recorder raises before write. Adding a value
requires editing the enumeration in `raw_decision_snapshot.py` AND
the §4.5 list; reconciled by unit test.

### 4.6 Forward Compatibility

- Validator validates known fields only. Unknown fields surface on
  `record.unknown_fields`.
- `schema_version` increments are additive-only.
- Loader reads any `schema_version ≤` its own; higher raises.
- Validator rejects when (a) a known required field is missing,
  (b) a known field has an invalid value, (c) `schema_version` is
  higher than known, (d) `_unobservable.reason` is not in the closed
  enumeration.

### 4.7 `sub_interval` and `seconds_into_sub_interval` Canonical Source

Derivable from `market.market_start_time` and
`decision_reference_time`. Recorder writes the captured values; the
Phase Delta loader recomputes and asserts byte-equality. Mismatch is
a P0 corruption finding (TC49).

## 5. Storage Layout

### 5.1 Path Resolution (fail-fast, no silent default)

```text
1. RAW_DECISION_SNAPSHOT_DIR set:
     use that exact directory; raise at startup if it does not exist,
     is not a directory, or flock cannot be acquired (§5.7).
2. Unset:
     capture is disabled. Recorder is a startup no-op that logs:
     "raw decision snapshot capture disabled (RAW_DECISION_SNAPSHOT_DIR
     not set)". The decision body proceeds unchanged.
```

No CWD-default. No LIVE_TRADE_LEDGER_PATH-sibling default.

### 5.2 File Naming and Rotation

```text
$RAW_DECISION_SNAPSHOT_DIR/
  YYYY-MM-DD/
    raw_decisions_YYYYMMDD.jsonl
    raw_bodies_YYYYMMDD.jsonl       # content-addressed sidecar
  resolutions/
    market_resolutions.jsonl
  raw_decisions_skipped.jsonl       # see G10 / §5.6
  .write.lock                       # flock target (§5.7)
```

Day rotation by `captured_at.date()` UTC. Prior days never reopened
(no migration).

File lines are appended in capture-time order (the Phase Delta loader
preserves that order; the harness re-sorts for determinism per §6.7).
TC63 asserts capture-order matches `captured_at` ascending within a
single process.

### 5.3 Content-Addressed Body Sidecar

Recorder ALWAYS uses the sidecar for fields whose encoded length
exceeds 4 KiB. No inline-vs-sidecar conditional.

Sidecar line shape:

```text
{"sha256": "<hex>", "length": <int>, "body": <json value>}
```

In-line reference in the main record:

```text
{"_body_ref": {"sha256": "<hex>", "length": <int>}}
```

Dedup: recorder skips writing if sha256 is already present in its
in-memory index. On `__enter__`, the recorder reads the existing
`raw_bodies_YYYYMMDD.jsonl` for the current UTC day (if any) and
seeds the in-memory index from existing sha256 entries. This makes
the sidecar dedup-correct across same-day process restarts. TC64
asserts: stop process, restart, capture the same body → sidecar has
exactly one matching sha256 line.

Determinism: the spill ref is purely a function of body content. Two
captures of the same body produce identical refs. Cross-process and
cross-run reproducibility hold for both the ref AND the sidecar file
contents.

Dedup scope is the current UTC day's sidecar file. Day-rotated files
are NOT re-read; cross-day duplicate bodies are written once per day.
This is intentional — keeps the in-memory index bounded and avoids
the recorder loading arbitrarily many historical sidecars at startup.

### 5.4 Resolution Sibling File

One JSON object per `(condition_id, fetched_at)` in
`resolutions/market_resolutions.jsonl`. Shape:

```text
{ market_slug, condition_id, fetched_at, closed, outcomes,
  outcomePrices, winning_outcome, winning_token_id,
  raw_payload_hash, raw_payload }
```

Phase Eta harness joins by `condition_id` AND picks the latest
`fetched_at` among `closed=true` entries. No closed-true entry →
`resolution_status="not_closed"` and the record is excluded from
win/loss metrics.

### 5.5 Hashing Convention

SHA-256 over bytes from:

```python
json.dumps(payload, ensure_ascii=False, sort_keys=True,
           separators=(",", ":"), default=_json_default).encode("utf-8")
```

Collision-on-write to sidecar is a hard error (assert
`existing_body == new_body` for matching sha256 before treating as a
hit). Equivalent payloads with different key orders produce same
hash (TC44).

For `deribit_pcr.raw_option_summaries_hash`, the canonical bytes are
of the raw `data["result"]` list as returned by Deribit, EXCLUDING
any `fetched_at` field the processor synthesizes. The cache retains
the raw list bytes and hash from fetch time; cache-hits reuse them
without recomputation (TC60).

**`drop_counters` and `policy_filter_counters` hash discipline
(v22 per round-18 P1):** the two top-level counter blocks
introduced in v21 ARE included in the per-record canonical-bytes
hash used by the §5.3 content-addressed sidecar. Two records with
the same decision_id, same captured data, but different drop
counts WILL produce different body hashes — this is intentional,
because the drops are observable state of the decision and the
hash MUST distinguish them for forensic integrity. The schema
in §4.2 lists every counter key explicitly so `sort_keys=True`
serialization produces deterministic byte ordering. TC44 (the
existing canonical-bytes collision test) is extended in v22 to
include a sub-case asserting: two synthetic records identical
in every field except a single `drop_counters` value produce
DIFFERENT sha256s.

### 5.6 Atomicity, Corruption Recovery, and Skipped-Decision Log

- Recorder builds encoded line in memory; writes via single
  `os.write()` under the append lock; `os.fsync` per record.
- If `os.write()` raises (disk full, EIO, permission, NFS hiccup):
  1. Recorder writes a single-line entry to
     `raw_decisions_skipped.jsonl` with `{decision_id, bot_mode,
     captured_at, failure_type, failure_message}`. This write is
     attempted; if IT also raises, the recorder re-raises the
     ORIGINAL raw-write exception with explicit
     `raise original_exc from skipped_write_exc` (the original is
     the actionable problem; the skipped-log failure is contextual
     and travels as `__cause__`). TC65 asserts: (a) the original
     exception is the one propagated out of `__exit__`; (b)
     `__cause__` is the skipped-log exception when skipped-log
     write failed; (c) `__cause__` is None when skipped-log write
     succeeded.
  2. Recorder re-raises out of `__exit__`. Bot fail-stops.
  3. The skipped log is observability, NOT recovery — the bot still
     fail-stops; it is not a fallback under M4 because nothing about
     the failure is masked. TC65 verifies the skipped log is written
     before the re-raise.
- Loader (Phase Delta) tolerates ONE truncated trailing line per
  file: logs `truncated_trailing_line=true` and skips trailing bytes
  after the last valid newline. Earlier truncations are P0 (loader
  raises).
- Sidecar uses same atomicity rule. A truncated sidecar body is P0
  iff any record's `_body_ref` points to it.

### 5.7 Cross-Process Locking

At startup, recorder opens `${RAW_DECISION_SNAPSHOT_DIR}/.write.lock`
with `fcntl.flock(LOCK_EX | LOCK_NB)`. On contention, raise at startup
naming the conflicting PID (best-effort via `lsof`). Lock held for
runtime. TC29a asserts second process fails fast.

### 5.8 Disk Volume Estimate

- Baseline per record (no raw opt-in): ~200 KB (tick_buffer ~160KB,
  price_history ~3KB, config/gates/signals/fusion/depth ~30KB,
  market/frozen_quotes/provenance ~2KB, sidecar refs ~200B).
- Polymarket + Coinbase + F&G raw opt-ins: +50–200 KB inline OR
  sidecar-amortized.
- Deribit raw opt-in: +200KB–1MB per cache-miss decision; cache-hits
  dedup via the §5.3 in-memory index seeded from existing sidecar
  (TC64).

Cadence in production: live + 4 shadow windows ≈ 5 candidates per
15-min market sub-interval × 96 sub-intervals/day = ~480 records/day
peak (plus rejects on every qualifying tick).

**Projected 24h disk usage**: ~240 MB baseline; ~720 MB with Deribit
raw opt-in. Over 30-day retention: ~7–22 GB.

Phase Theta updates deploy templates AND adds capacity-planning
guidance (Theta-7).

## 6. Implementation Phases

Phase order: introspection (Beta) BEFORE wiring (Gamma), so Gamma can
wire against existing surfaces with no rework.

### 6.1 Phase Alpha — Schema Infra, Strategy Version, Closed Enumerations, Shared Gamma Resolver (Helpers Only)

Aim: schema-as-code, recorder skeleton (unwired), strategy_version,
closed enumerations, canonical serializer, extract Gamma resolver
helpers WITHOUT changing per-caller policy.

Work items:

- **Alpha-1** — New `raw_decision_snapshot.py` (≤500 lines) exposing
  `RawDecisionSnapshotRecord`, `RawDecisionSnapshotRecorder` (with
  `__enter__`/`__exit__` semantics specified in Gamma-4), `Unobservable`
  enum, `GateName` enum (members include `"final_decision"`,
  `"exception"`, and every §4.4 row's `name` column entry — the
  closed enum is the single source of truth for legal gate names;
  TC08 asserts unknown names are rejected at record-write time),
  `AUTO_APPENDED_GATE_NAMES = {"final_decision"}` and
  `CONDITIONAL_TRAILING_GATE_NAMES = {"exception"}` closed sets
  per §4.4's TC02C_EXCLUSION_SET, `write_record(path, record)`,
  `_json_default` raising on naïve datetime (M9) and on any
  default-value `now=...` expression (M11; TC56b).
  **`gate_scope` API** — `RawDecisionSnapshotRecorder` exposes a
  `@contextmanager def gate_scope(self, name: str) -> Iterator[None]`
  method with these documented semantics (pinned here to close
  the v14 implementation gap flagged by round-12 reviewer #2 AND
  the v15 round-13 reviewer #3 P0 about Python contextmanager
  pop ordering):
    * **Stack maintenance + EXCEPTION-PATH DEFER-POP (v16).** On
      `__enter__`, push `name` to `self._gate_scope_stack:
      list[str]`. On NORMAL `__exit__` (no exception in flight),
      pop `name` from the stack. On EXCEPTION `__exit__` (the
      contextmanager's `__exit__` is called with non-None
      exc_type), the gate_scope does NOT pop the stack itself.
      Instead it sets `self._scoped_gate_on_exception = name`
      (a single-slot recorder field) and RE-RAISES the exception.
      The recorder's OWN `__exit__` (running OUTSIDE all
      gate_scope frames per Python's nested-contextmanager
      unwinding semantics) then reads
      `self._scoped_gate_on_exception` for attribution at
      Gamma-4 exception step (3); this read is reliable because
      no gate_scope has popped the slot. After the recorder
      processes the attribution, it clears the slot to None.
      RATIONALE: with `@contextmanager`-decorated generators,
      the INNERMOST `gate_scope.__exit__` runs first and unwinds
      the with-stack one frame at a time. If the gate_scope's
      `finally:` clause popped the stack, the recorder's own
      `__exit__` (which runs LAST, outermost) would see an
      empty stack — losing the attribution. The
      `_scoped_gate_on_exception` slot survives across
      gate_scope's exception-path exit, preserving the
      attribution for the recorder to read. The slot is
      single-valued (only the INNERMOST gate_scope on the
      exception-unwind path sets it, because Python unwinds
      innermost-first and earlier slot writes are overwritten
      by inner-frame writes — actually NO: the gate_scope
      writes its own name unconditionally on exception, so the
      OUTERMOST gate_scope's exit overwrites with its own name;
      to preserve innermost-attribution, gate_scope writes ONLY
      IF the slot is None — i.e., the first contextmanager to
      see the exception "wins" attribution. The first to see is
      the innermost. TC02h asserts this discipline.). TC02h
      (new in v16) asserts the recorder's `__exit__` reads
      `_scoped_gate_on_exception` BEFORE the slot is cleared
      and that gate_scope's exception-path EXIT leaves the
      slot set to the innermost scope name.
    * **Ownership.** `_gate_scope_stack` lives on the recorder's
      per-decision state, owned by the body thread for the full
      `_make_trading_decision_body` lifetime. No lock — `gate_scope`
      enters and exits are pure single-threaded operations
      relative to the per-decision state (matches §4.1's
      "per-decision state: NO LOCK IS TAKEN" rule).
    * **Re-entry forbidden.** Calling `gate_scope("G")` while
      `"G"` is already in the stack raises `RuntimeError(
      f"gate_scope re-entry: {G}")`. TC02e (new in v15) asserts.
    * **On successful exit (no exception, no `.reject(G, ...)`
      fired inside the block): APPEND a `gates[]` entry**
      `{name: G, passed: true, reason: "ok", inputs: <recorder-
      collected inputs>, output: <recorder-collected output or
      null>}`. This is the v15 resolution of round-12 reviewer
      #1's P0 — without it, `gates_pre` would be empty on accept
      and Gamma-4's reject-detection logic would be ambiguous.
      With it, accept-path `gates_pre` contains one `passed=true`
      row per evaluated gate; the §4.4 omitted-not-failed
      convention still holds because OMITTED gates (not entered
      via `gate_scope` because earlier reject already returned)
      do not appear. TC02e + TC83(a) assert.
    * **On `.reject(G, reason)` fired inside the block**:
      `gate_scope`'s exit logic SUPPRESSES the passed=true append
      by checking `rec.fields["rejected_at_gate"] == name` on
      exit (v16-pinned wiring). The `DecisionRecord.reject(...)`
      already populates `rec.fields["rejected_at_gate"]` with the
      gate name (per `decision_log.py:165-170`); the recorder
      holds a reference to `rec` (passed in at recorder
      construction; see Gamma-1 `decision_record=rec` kwarg) so
      `gate_scope.__exit__` can read the field directly without
      requiring `DecisionRecord.reject(...)` to call back into
      the recorder. The recorder ALSO synthesizes a passed=false
      row into raw_rec's own `gates[]` from `rec.fields` at
      `__exit__` step (1) via the FIELD_MAP MAPPED walk (the
      existing wiring). No double-append:
      - `gate_scope.__exit__` sees `rec.fields["rejected_at_gate"]
        == name` → SUPPRESS the passed=true append.
      - The reject's `passed=false` row is added later by the
        recorder's own `__exit__` via FIELD_MAP, AT MOST ONCE.
      TC02f asserts both branches (suppression on reject + no
      double-row). TC02f sub-case (b-wiring) specifically asserts
      that gate_scope reads `rec.fields["rejected_at_gate"]` and
      that the recorder's FIELD_MAP MAPPED handler for
      `rejected_at_gate` writes the passed=false row exactly once.
    * **On exception inside the block**: per Gamma-4 exception
      step 3, the recorder synthesizes-or-updates the row for `G`
      with `_unobservable: gate_exception_G`. `gate_scope`'s
      success-append is SUPPRESSED. TC47b asserts.
    * **Inputs/output collection (v17-pinned).** `gate_scope`
      accepts an optional `inputs: dict` kwarg passed at
      `__enter__`; the block body may call
      `raw_rec.set_gate_output(value)` to assign the output
      before exit. Both default to None — when both are None
      AND the gate's §4.4 "inputs"/"output" columns specify
      keys (rather than "(see X — DO NOT duplicate)"), the
      recorder reads the corresponding values from CANONICAL
      SOURCES at success-append time:
      - For gates whose §4.4 "inputs" column lists specific
        scalar keys (e.g., `quote_stability` needs
        `stable_tick_count`, `required`): the recorder collects
        these from `rec.fields[k]` via the existing FIELD_MAP
        machinery at __exit__ time. The success-append row's
        `inputs` dict is populated lazily from `rec.fields` AT
        THE RECORDER'S `__exit__`, NOT at gate_scope's exit.
      - For gates whose §4.4 "inputs" column says "(see X — DO
        NOT duplicate)": the success-append row's `inputs` is
        an empty dict; the harness/loader reads from the
        cross-referenced block.
      - For gates whose §4.4 "output" column is non-null AND
        non-cross-referenced (e.g., `trend_filter` →
        `trend_direction`): the block body calls
        `raw_rec.set_gate_output(value)` inline.
      TC02g (new in v16, REVISED in v17) is a static-AST
      check asserting: for every Gamma-4a wrap site whose §4.4
      "inputs" column lists scalar keys, the corresponding
      keys ARE present in `rec.fields` keyspace AND in
      `FIELD_MAP.MAPPED`'s codomain (so the lazy-population
      path actually has data to copy). Mismatch fails CI.
      This v17 relaxation removes the requirement for every
      wrap site to literally pass `inputs={...}` — the wrap
      list at Gamma-4a (the per-gate enumeration within the
      Gamma-4a section below; specific line range omitted to
      avoid stale-anchor drift) stays compact
      (`gate_scope("G")` only, no `inputs=` kwarg shown per
      site), and the population mechanism is centralized at
      the recorder's `__exit__`.
  TC02f (new in v15) covers all four exit branches of
  `gate_scope`: success (append passed=true), success-after-reject
  (no append), exception scoped (synthesize), exception not scoped
  (no synthesize). The Alpha-1 module budget pre-v15 was tight
  (~500 lines); if `gate_scope` + the v15 additions push past,
  the implementer factors per §6.1 Alpha-1 budget escape (Rule 7).
- **Alpha-2** — New `strategy_version.py` exporting `STRATEGY_VERSION`
  constant.
- **Alpha-3** — CI check failing when any file under
  `core/strategy_brain/**` is modified relative to `main` with
  semantic content changes (ignore comment-only, blank-line, type-
  annotation-only edits via `git diff -G '^[^#]'`) without
  `strategy_version.py` also modified.
- **Alpha-4** — New module `analysis/gamma_resolution.py` (≤500 lines)
  extracting only the SHARED HELPERS (HTTP fetch, JSON parsing,
  payload canonicalization) from `calibration_decision_join` and
  `estimate_decision_results`. Both existing files are refactored to
  import the helpers but RETAIN THEIR EXISTING PER-CALLER POLICY
  (`closed-only` in calibration; `accept-unclosed-as-pending` in
  estimate). Policy convergence is OUT OF SCOPE for this plan;
  Alpha-4 is a pure extract refactor. Parity-tested against the
  existing two implementations on a checked-in Gamma fixture.
- **Alpha-5** — New package directory `analysis/` with empty
  `__init__.py`. No production source imports `analysis/*` (RP2).
- **Alpha-6** — No call sites of `raw_decision_snapshot.py` are
  wired. Pure infrastructure.
- **Alpha-7** — Finalize §4.5 closed Unobservable enumeration based
  on §3.A dispositions (which MUST be complete before Alpha starts).

Verification:

- `pytest tests/test_raw_decision_snapshot_recorder.py` passes.
- `pytest tests/test_gamma_resolution.py` passes; parity vs prior
  implementations.
- `_json_default` raises on naïve datetime (TC04a) and on default-
  value `now=` expression (TC56b).
- Validator round-trip on synthetic fixture.

Validation:

- Dataclass round-trips every §4 field.
- Unknown gate name raises before write.
- Unknown `_unobservable.reason` raises.

Test coverage adds: TC01, TC04a, TC08, TC09, TC10, TC34, TC44, TC45,
TC56b.

Regression prevention adds:

- Sentinel test that `decision_log.py` behaviour is unchanged.
- Static check that no production source imports
  `raw_decision_snapshot` yet.

Per-phase 3-reviewer gate per §10.

### 6.2 Phase Beta — Introspection Surfaces, Required `now=`, Effective Config, DecisionInputSnapshot Extension, Risk Engine Accessors

Aim: expose metadata the recorder needs; refactor for replay
determinism; preserve trading verdicts bit-for-bit (G6).

Work items:

- **Beta-1** — Extend `DecisionInputSnapshot` (forward-additive per
  §3.C):
  - `yes_quote_timestamp`, `no_quote_timestamp` (UTC-aware,
    Optional).
  - `price_history: tuple[PriceHistoryEntry, ...]` where
    `PriceHistoryEntry` is a new frozen dataclass
    `{value: Decimal, ts: Optional[datetime], source: str, synthetic: bool}`.
    The PRODUCTION mutable list `self.price_history: List[Decimal]`
    (note: List of Decimals, not Entries) remains the in-process
    representation; the snapshot constructor wraps each Decimal in
    a `PriceHistoryEntry` at snapshot-build time.
  - **Writer enumeration** (sites that APPEND to `self.price_history`):
    - `bot.py:4088` (`self.price_history.append(current_price)`
      — initial price).
    - `bot.py:4128` (`self.price_history.append(new_price)`
      — synthetic startup back-fill; sets `source="synthetic_startup"`
      contextually).
    - `bot.py:4509` (`self.price_history.append(mid_price)`
      — quote-tick path; sets `source="live_quote_tick"`,
      `ts=tick.ts_event` UTC-aware).
    These three sites APPEND raw Decimals AND record `(source, ts)`
    metadata in parallel lists (`self._price_history_sources`,
    `self._price_history_ts`) on the bot. All three lists are
    mutated ONLY via a single atomic helper on the bot:
    `self._append_price_history(value: Decimal, *, source: str,
    ts: Optional[datetime]) -> None`. This helper appends to all
    three lists in one call (no intermediate yield), enforcing
    equal lengths and a single max-length truncation policy.
    Per-site source/ts values:
    - `bot.py:4088` (initial price): `source="synthetic_startup"`,
      `ts=None` (no upstream tick).
    - `bot.py:4128` (synthetic startup back-fill):
      `source="synthetic_startup"`, `ts=None`.
    - `bot.py:4509` (live quote tick): `source="live_quote_tick"`,
      `ts=tick.ts_event` (UTC-aware).
    The snapshot constructor at `bot.py:4936` zips the three lists
    into the tuple of `PriceHistoryEntry`.
    RP12 EXTENDED (per §11): static check forbids any direct write
    to `self.price_history`, `self._price_history_sources`,
    `self._price_history_ts` outside the `_append_price_history`
    function body in `bot.py`, with the same alias-pattern
    coverage as the `divergence_processor` case. TC42c asserts
    `len(self.price_history) == len(self._price_history_sources)
    == len(self._price_history_ts)` after every body invocation
    including exception paths.
  - **Reader enumeration** (sites that read `decision_snapshot.price_history`
    elements, all of which now read `.value` for numeric access):
    - `decision_context.py:23` (length check).
    - `decision_context.py:28` (`recent_prices = [float(p) for p
      in snapshot.price_history[-20:]]` → updated to `float(p.value)`).
    - `decision_context.py:32` — TWO `float(snapshot.price_history[-5])`
      calls on one line (denominator and numerator of the momentum
      expression); BOTH updated to `.value`. A grep that misses
      the second instance fails the Beta-1 static check.
    - `bot.py:6275` (`historical_prices = decision_snapshot.price_history`
      — passed downstream to processors; processors update reads).
    - `core/strategy_brain/signal_processors/spike_detector.py:87`
      (`recent = historical_prices[-self.lookback_periods:]` —
      each element accessed via `.value` where the float() call
      currently lives at line ~94).
    - `core/strategy_brain/signal_processors/spike_detector.py:97`
      (`float(historical_prices[-3])` → `float(historical_prices[-3].value)`).
    - Other processors with `historical_prices[-N]` element
      access: enumerate via `grep -n "historical_prices\[" core/strategy_brain/signal_processors/`
      and update each; the search must yield zero unchanged
      element-access sites after Beta-1. TC42d (registered in
      §8 and added by Beta-1) is a static check: greps
      `historical_prices\[-?\d*\]` over
      `core/strategy_brain/signal_processors/**` and
      `decision_context.py`; asserts every match is followed on
      the same line (or within the same expression) by `.value`,
      OR is consumed only by length/iteration patterns (`len()`,
      `for ... in`). Mismatch fails CI. The Beta-1 diff cover
      letter records the grep result (file:line list) as a
      checked artifact.
    - LENGTH-ONLY readers at `bot.py:4901, 5156, 5193-5197` are
      unaffected (length API unchanged).
  - Pre-state for replay is NOT captured at snapshot-build time
    (because mode is not yet resolved — live vs shadow processor
    instance is ambiguous). Pre-state is captured INSIDE the body
    in Gamma-2.
- **Beta-2** — Add `now: datetime` REQUIRED kwarg (no default; M11)
  to every processor `process()` and to
  `SignalFusionEngine.fuse_signals()`. The bot ALWAYS passes
  `decision_snapshot.reference_time`. The replayer ALWAYS passes
  `record.decision_reference_time`. Every processor's internally-used
  `datetime.now()` is replaced with the `now` kwarg. TC56 + TC56b
  enforce: TC56 forbids `datetime.now()` without `timezone.utc`;
  TC56b forbids `datetime.now(timezone.utc)` as a default-value
  expression; TC56c asserts calling `process()`/`fuse_signals()`
  without `now=` raises `TypeError`.
  - Refactor `_process_signals(...)` to take required `now: datetime`
    kwarg and forward it to every `processor.process(now=now, ...)`
    call (six sites: bot.py:6306, 6315, 6324, 6334, 6344, 6353).
    Caller `_make_trading_decision_body` at bot.py:5247 passes
    `now=decision_snapshot.reference_time`.
  - **Dead-file cleanup**: DELETE
    `core/strategy_brain/fusion_engine/divergence_processor.py`
    (it is an unimported duplicate of
    `core/strategy_brain/signal_processors/divergence_processor.py`;
    confirmed dead by import-graph check). Removing it eliminates
    one RP8 false-positive without weakening the static check.
  - **Per-processor `datetime.now(...)` audit (replay-determinism
    guard).** RP8 catches naïve `datetime.now()`, but a processor
    that calls `datetime.now(timezone.utc)` INTERNALLY (rather than
    using the injected `now=` kwarg) is RP8-CLEAN yet REPLAY-DIRTY:
    the wall-clock instant the processor reads at production time
    differs from the `decision_reference_time` the replayer
    injects, breaking G3 verdict parity for any decision where the
    processor's read happens ε seconds after `decision_reference_time`.
    Beta-2 therefore requires a per-processor audit: grep each of
    the six production processors for any `datetime.now`-family
    call (naïve OR aware) and replace EVERY such call with the
    injected `now: datetime` kwarg. The audit is documented in
    the Beta-2 diff cover letter with line numbers per file.
    Known sites today (must be replaced):
    - `spike_detector.py:140, 192` (TradingSignal timestamp)
    - `sentiment_processor.py:157`
    - `divergence_processor.py:151, 181, 227` (line 181 is the
      TradingSignal `timestamp` kwarg; `TradingSignal(` opens at
      180)
    - `orderbook_processor.py:216`
    - `tick_velocity_processor.py:214`
    - `deribit_pcr_processor.py:108` (`_parse_dte`, already covered
      by Beta-7's `now=` kwarg), `:176` (synthesized `fetched_at`
      inside `_fetch_pcr` output dict — replace with the injected
      `now=`), `:202` (`now = datetime.now(timezone.utc)` inside
      `process()` driving the cache-validity check at line 206 —
      THIS IS THE CANONICAL RP8-CLEAN-BUT-REPLAY-DIRTY EXAMPLE:
      the cache decision uses wall-clock vs `self._cache_time`,
      not the `now=` kwarg. Replace with the injected `now=` so
      the recorded `cache_age_seconds`, `cache_time_before`, and
      `fresh_fetch_performed` all align with
      `decision_reference_time`), `:286` (TradingSignal timestamp).
    TC11b is a determinism-diff test: run `process(now=T)` then
    `process(now=T + 60s)` on the same input; assert the OUTPUT
    differs ONLY by fields that are explicit functions of `now`
    (i.e., catches the RP8-clean-but-replay-dirty case where a
    processor smuggled in a fresh `datetime.now(timezone.utc)`).
  - **Legacy scaffold disposition**: refactor the four naïve
    `datetime.now()` sites in
    `core/strategy_brain/strategies/btc_15min_strategy.py`
    (lines 161, 236, 318, 324) to `datetime.now(timezone.utc)`
    OR delete the file if no test references it. Decision
    documented in the Beta-2 diff cover letter.
- **Beta-3** — Add `signal_id: str` to `TradingSignal`. Canonical
  scheme: `f"{decision_id}:{processor_name}:{ordinal_within_decision}"`.
  - `processor_name`: the processor's `self.name` slug — the
    EXISTING class-default PascalCase slug from each subclass's
    `super().__init__("<Slug>")` call (verified):
    - `spike_detector.py:57` → `"SpikeDetection"`
    - `sentiment_processor.py:50` → `"SentimentAnalysis"`
    - `divergence_processor.py:74` → `"PriceDivergence"`
    - `orderbook_processor.py:77` → `"OrderBookImbalance"`
    - `tick_velocity_processor.py:73` → `"TickVelocity"`
    - `deribit_pcr_processor.py:73` → `"DeribitPCR"`
    Beta-3 updates the bot's processor construction sites to pass
    `name=` EXPLICITLY (so the M11 required-no-default discipline
    holds), but the VALUE passed is the EXISTING PascalCase slug
    bit-for-bit. Live AND shadow instances use the SAME slug
    (e.g., both pass `name="SpikeDetection"`). NO `__shadow`
    suffix — that would break the `SignalFusionEngine.weights`
    lookup at `signal_fusion.py:95`
    (`self.weights.get(signal.source, self.weights["default"])`)
    because the weight dict is keyed by the base PascalCase slug
    in BOTH `_configure_fusion_engine`-set values (bot.py:1111-1116)
    and the new `PRODUCTION_DEFAULT_WEIGHTS`.
    Same-class collision between live and shadow instances within
    one decision is impossible because EVERY decision body runs
    against exactly one registry (live OR shadow per
    `observation_only`) — never both. TC51c (the round-9 collision
    test) is REVISED to assert: constructing two instances of the
    same processor class with the same `name=` value and the same
    `decision_id` does NOT collide on `signal_id` provided each
    instance is reset via its own `process()` entry (`_signal_ordinal`
    is per-instance per-call). The replayer constructs a single
    fresh instance per processor class per record (Zeta-2), so the
    in-decision collision surface is empty.
  - `ordinal_within_decision`: an INSTANCE attribute `self._signal_ordinal`
    on each processor, RESET TO `0` AT THE FIRST LINE OF EVERY
    `process()` CALL and post-incremented after each emitted
    `TradingSignal`. Two distinct processor instances (live vs
    shadow) cannot collide because the `signal_id` namespace
    includes `processor_name`. The same instance cannot collide
    across decisions because the counter is reset at every
    `process()` entry AND the namespace includes `decision_id`.
  - NOT a class-level counter (would cause races between live/shadow
    instances) and NOT a process-global counter (would be non-
    deterministic across replay). Per-instance, per-call.
  - TC51b asserts the replayer produces the same `signal_id` set as
    the recorded record for every fixture. TC51c constructs two
    instances of the same processor class within one fake
    `decision_id` and asserts no `signal_id` collision.
  - **Per-subclass `__init__` signature change.** The six processor
    subclasses currently hardcode `super().__init__("<DefaultSlug>")`
    and do NOT accept a `name=` kwarg in their own `__init__`. Beta-3
    updates each subclass to accept `name: str` as a REQUIRED kwarg
    (NO default; M11-clean; no `Optional` / no sentinel-default
    fallback under M4). The forward becomes simply
    `super().__init__(name)` — no `None` branch, no default-substitute.
    Files (with `super().__init__(...)` line numbers to update):
    - `core/strategy_brain/signal_processors/spike_detector.py:57`
    - `core/strategy_brain/signal_processors/sentiment_processor.py:50`
    - `core/strategy_brain/signal_processors/divergence_processor.py:74`
    - `core/strategy_brain/signal_processors/orderbook_processor.py:77`
    - `core/strategy_brain/signal_processors/tick_velocity_processor.py:73`
    - `core/strategy_brain/signal_processors/deribit_pcr_processor.py:73`
    Bot startup at `bot.py:986-1035` is updated to ALWAYS pass an
    explicit `name=` kwarg — covering BOTH the LIVE constructor
    block at `bot.py:986-1010` AND the SHADOW constructor block at
    `bot.py:1011-1035`. EVERY construction site (live AND shadow,
    twelve total) passes the SAME bit-for-bit existing PascalCase
    slug as the class-default — e.g., `SpikeDetectionProcessor(
    spike_threshold=..., lookback_periods=..., name="SpikeDetection")`,
    same for shadow. Without updating BOTH blocks, the bot fails
    to start: every live processor construction at lines 986-1010
    raises TypeError because the subclass `__init__` now requires
    `name=`. Beta-3 diff cover letter enumerates all twelve
    construction sites with the explicit `name=` argument added
    AND verifies via grep that every site's slug literal exactly
    matches the corresponding class-default `super().__init__(
    "<Slug>")` literal (zero drift). Test fixtures that currently
    construct processors with no `name=` MUST be updated in the
    SAME Beta-3 diff to pass an explicit `name=`. The implementer greps
    `tests/` for processor constructors and updates each call site;
    this is consistent with the M11 discipline already applied to
    `now=`, `max_spot_history=`, `tolerance_seconds=`,
    `recency_window_seconds=`, `weights=`, `pcr_data_override=`,
    and all the Beta-8 `now=` callers. The test-fixture update list
    appears in the Beta-3 diff cover letter. TC81 asserts each
    subclass `__init__` raises TypeError when called without `name=`.
- **Beta-4** — Per processor (six files):
  - `effective_params() -> dict` returning every §4.3 parameter for
    that processor, with `sorted(d.items())` ordering for CSV
    determinism.
  - For `divergence_processor`: add
    `spot_history_pre_state_snapshot() -> tuple[(datetime, float), ...]`
    returning entries captured BEFORE `process()` mutates state. The
    PRODUCTION `_spot_history` remains `List[float]` (no behaviour
    change to momentum math). A PARALLEL recorder-only structure
    `_spot_history_ts: List[datetime]` tracks per-entry timestamps.
    Atomicity: mutations of `_spot_history` and `_spot_history_ts`
    MUST be inside a single helper `_append_spot(value, ts)` that
    appends to both atomically AND truncates both atomically with
    `max_spot_history`. A static check forbids any other code in
    `divergence_processor.py` from writing to either list directly.
    Insertion order preserved.
    Constructor gains `max_spot_history: int` — REQUIRED kwarg, NO
    DEFAULT (per §12 promoted-constant rule; per M11). Bot startup
    reads `int(os.environ["DIVERGENCE_SPOT_HISTORY_MAX_LEN"])` and
    passes the value at construction; missing env raises KeyError.
    Calling `PriceDivergenceProcessor(max_spot_history=...)` without
    the kwarg raises TypeError. The replayer reads
    `record.effective_config.divergence_spot_history_max_len` and
    passes it explicitly per Zeta-2.
    TC42b asserts `len(_spot_history) == len(_spot_history_ts)`
    after every `process()` call across a multi-decision sequence
    including an exception-raising path.
    Static check (RP12) forbids ANY non-direct write to
    `self._spot_history` or `self._spot_history_ts` in
    `divergence_processor.py`: also forbids alias patterns like
    `h = self._spot_history; h.append(...)` and
    `getattr(self, '_spot_history*')`. The two lists are accessed
    ONLY via the `_append_spot(value, ts)` helper.
  - For `tick_velocity_processor`: constructor gains
    `tolerance_seconds: int` — REQUIRED kwarg, NO DEFAULT (same
    discipline as above; env `TICK_VELOCITY_TOLERANCE_SECONDS`).
  - For `spike_detector`: document that velocity sub-mode's `[-3]`
    lookup is fixed (independent of `spike_lookback_periods`); §7.1
    marks the partial scope.
- **Beta-5** — In `signal_fusion.py`:
  - Add `recency_window_seconds: int` constructor arg — REQUIRED
    kwarg, NO DEFAULT (M11; promoted-constant convention). Bot
    startup reads `int(os.environ["FUSION_RECENCY_WINDOW_SECONDS"])`
    and passes the value. Replayer reads
    `record.effective_config.fusion_recency_window_seconds`.
  - Add `weights: Dict[str, float]` constructor arg — REQUIRED kwarg,
    NO DEFAULT (uniform with `recency_window_seconds`; per M11; no
    sentinel-default fallback). The default-weights dict moves OUT
    of the class body to the bot's startup module so production
    wiring is the single source of truth (no silent class-level
    default).
  - **Singleton accessor disposition.** `get_fusion_engine()`
    (defined at `signal_fusion.py:196`) returns a process-wide
    singleton constructed via `SignalFusionEngine()` with no args.
    After this Beta-5 change, that no-arg singleton constructor
    raises TypeError. Beta-5 DELETES `get_fusion_engine()` and the
    module-level `_fusion_engine_instance`. A repo-wide grep
    `grep -rn "get_fusion_engine\|_fusion_engine_instance"
    --include="*.py" | grep -v "/\\.claude/"` reveals FIVE caller
    sites that ALL need explicit disposition before the deletion
    is safe:
    - `bot.py:105` (import) + `bot.py:1038` (call): replaced by
      explicit `SignalFusionEngine(weights=..., recency_window_seconds=...)`
      construction at line 1038 (see below).
    - `feedback/learning_engine.py:17` (import) +
      `learning_engine.py:63` (call inside `LearningEngine.__init__`).
      `LearningEngine` is constructed at `bot.py:1056` via
      `get_learning_engine()` (NOT `LearningEngine(...)` directly).
      `get_learning_engine` is itself a sibling singleton at
      `feedback/learning_engine.py:313-318` constructing
      `LearningEngine()` with no args. Without disposing of both,
      bot startup crashes at `bot.py:1056` once
      `LearningEngine.__init__` requires `fusion_engine`.
      Disposition: refactor `LearningEngine.__init__` to accept a
      required `fusion_engine: SignalFusionEngine` arg (M11-style,
      no default); DELETE `get_learning_engine` and
      `_learning_engine_instance`; change `bot.py:1056` from
      `self.learning_engine = get_learning_engine()` to
      `self.learning_engine = LearningEngine(fusion_engine=self.fusion_engine)`.
      The Beta-5 cover-letter audit grep extends to
      `get_learning_engine\|_learning_engine_instance` (must return
      ZERO hits post-merge). TC79 also asserts `LearningEngine()`
      called without `fusion_engine=` raises TypeError. The
      `optimize_weights` function deletion already specified in
      Beta-5 covers the only `set_weight` callsite inside this
      module.
    - `core/strategy_brain/strategies/btc_15min_strategy.py:20`
      (import) + `:73` (call inside `IntegratedBTCStrategy.__init__`):
      this file is the legacy scaffold already flagged for
      delete-or-refactor in Beta-2. Beta-5 makes the deletion
      MANDATORY (since the file is not imported by `bot.py` —
      verified — its sole runtime use is the leftover
      `test_strategy.py` script). Both files are deleted.
    - `core/strategy_brain/test_strategy.py:31` (import) +
      `:110` (call): file deleted alongside the legacy scaffold
      above (same Beta-2 disposition extended to Beta-5).
    - `tests/test_simulation_mode_safety.py:268` (patch target
      `get_fusion_engine=_DummyFusion`): test patch target is
      updated to patch `SignalFusionEngine` construction at
      `bot.py:1038` directly, or the test is restructured to
      construct a `_DummyFusion` instance and inject it via
      `LearningEngine`'s new required-arg signature.
    Beta-5 cover letter records the audit:
    `grep -rn "get_fusion_engine\|_fusion_engine_instance"
    --include="*.py" | grep -v "/\\.claude/"` returns ZERO hits
    post-merge (except for the deletion itself, if it lands in a
    later commit). TC79 additionally asserts
    `from core.strategy_brain.fusion_engine.signal_fusion
    import get_fusion_engine` raises ImportError post-Beta-5.
    Replacement at the bot startup site:
    `bot.py:1038`: `self.fusion_engine = SignalFusionEngine(
        weights=PRODUCTION_DEFAULT_WEIGHTS,
        recency_window_seconds=int(os.environ["FUSION_RECENCY_WINDOW_SECONDS"]),
    )`
    `bot.py:1039`: `self._shadow_fusion_engine = SignalFusionEngine(
        weights=PRODUCTION_DEFAULT_WEIGHTS,
        recency_window_seconds=int(os.environ["FUSION_RECENCY_WINDOW_SECONDS"]),
    )`
    `PRODUCTION_DEFAULT_WEIGHTS` is a new module-level dict at the
    bot's startup module. CRITICAL: it reproduces the SIX
    processor-source numeric weights currently established by
    `_configure_fusion_engine` at `bot.py:1109-1116`
    (`OrderBookImbalance: 0.30`, `TickVelocity: 0.25`,
    `PriceDivergence: 0.18`, `SpikeDetection: 0.12`,
    `DeribitPCR: 0.10`, `SentimentAnalysis: 0.05`) PLUS the
    `"default": 0.10` key (preserved from the pre-Beta-5 class-
    level dict at `signal_fusion.py:51`). The `"default"` key is
    REQUIRED because `signal_fusion.py:95` reads
    `self.weights.get(signal.source, self.weights["default"])`
    where the second arg is evaluated unconditionally — removing
    `"default"` would raise `KeyError` on every fusion call.
    `PRODUCTION_DEFAULT_WEIGHTS` therefore has SEVEN keys (six
    processor sources + `"default"`). Source values DIFFER from
    the four-key class-level dict at `signal_fusion.py:46-52`
    (`SpikeDetection: 0.40`, `PriceDivergence: 0.30`,
    `SentimentAnalysis: 0.20`, `default: 0.10`); seeding
    `PRODUCTION_DEFAULT_WEIGHTS` from the class default (instead
    of `_configure_fusion_engine`) would BIT-FOR-BIT REGRESS the
    bot's fusion behaviour for five of six processors (caught by
    TC06 byte-equal-fixture but the spec should point at the
    right source). The implementer copies the six processor
    weights verbatim from `_configure_fusion_engine` AND adds
    `"default": 0.10` (the only value preserved from the class
    default) into `PRODUCTION_DEFAULT_WEIGHTS`. The class-level
    four-key default dict in `signal_fusion.py:46-52` is REMOVED
    in the same Beta-5 diff (the constructor now REQUIRES
    `weights=`, so no in-class default is needed; the `"default"`
    key lives only on the operator-supplied dict from now on).
    `signal_fusion.py:95`'s `self.weights["default"]` read is
    NOT refactored — the `"default"` key is a documented fallback
    for unknown signal sources (kept under the pre-existing
    fallback approval implicit in the bot's pre-plan behaviour;
    no new fallback under M4 because this is the existing
    semantics preserved bit-for-bit).
    The replayer (Zeta-2) passes weights according to two modes:
    - **Parity mode** (no override): passes
      `weights=record.effective_config.fusion_weights_by_source`
      for deterministic parity against the recorded run.
    - **Sweep mode** (harness `config_override` supplies
      `fusion_weights_by_source`): passes
      `weights=config_override["fusion_weights_by_source"]`
      so the harness's weight sweep actually changes engine
      construction. Same mode-split applies to every other §7.1
      swept parameter resolvable through `config_override`.
    No silent default under M4: the parity path explicitly uses
    the recorded value; the sweep path explicitly uses the
    operator-supplied override. TC23 enforces verdict change on
    weight sweep; TC79 asserts bot startup succeeds with the new
    construction and TypeErrors when `SignalFusionEngine()` is
    called without `weights=`.
  - **`_configure_fusion_engine` disposition (single source of
    truth).** With `PRODUCTION_DEFAULT_WEIGHTS` wired in at
    construction time, the existing `_configure_fusion_engine` at
    `bot.py:1108-1116` becomes redundant (it would `set_weight`
    the same values that the constructor just installed). Beta-5
    DELETES `_configure_fusion_engine` AND the calls at
    `bot.py:1040, 1041`. `PRODUCTION_DEFAULT_WEIGHTS` becomes the
    single source of truth. TC61b's allowed-call-sites list
    SHRINKS to exactly the constructor (`SignalFusionEngine.__init__`);
    `_configure_fusion_engine` is no longer in the list. This
    eliminates the drift hazard between two weight-setting paths.
  - Replace `current_time = datetime.now()` at `signal_fusion.py:81`
    with the `now: datetime` kwarg (M11; required). Replace
    `FusedSignal(timestamp=current_time, ...)` at line 145 with
    `timestamp=now`. Recency expression becomes
    `(now - s.timestamp) < timedelta(seconds=self.recency_window_seconds)`.
  - Add `_last_diagnostics: dict` set at START of every
    `fuse_signals()` call (cleared from prior call), populated
    incrementally, finalized at every return (success + five early-
    None returns).
  - Add `last_fusion_diagnostics() -> dict` returning the §4.2 fusion
    block fields (excluding `call_inputs` which the recorder fills
    from `effective_config`).
  - Invariant: `set_weight()` MUST NOT be called outside the
    constructor (`SignalFusionEngine.__init__`). Per the
    `_configure_fusion_engine` deletion below, that helper is no
    longer an allowed call site — the constructor is the SOLE
    allowed site post-Beta-5. A static grep check enforces (TC61b).
    Weights are NEVER mutated during a decision body or while the
    recorder is reading them.
    Existing caller `feedback/learning_engine.py:230` (inside
    `optimize_weights`) IS such a call site. Beta-5 deletes the
    `optimize_weights` function (verified zero callers in
    production: `grep -rn "optimize_weights" --include="*.py"`
    returns only the definition itself), and a one-line note in
    §12 documents that automated weight-tuning is deferred to a
    follow-up plan. After deletion AND the
    `_configure_fusion_engine` deletion above, TC61b's allowed
    call sites are EXACTLY the constructor
    (`SignalFusionEngine.__init__`); any `.set_weight(` anywhere
    else fails CI.
- **Beta-6** — New file `effective_decision_config.py` (≤500 lines).
  - Defines `ProcessorRegistry`, a frozen dataclass with attrs
    `divergence, deribit_pcr, spike, sentiment, orderbook,
    tick_velocity` (one per processor; type is the production
    processor class).
  - Adds `self._processor_registry_for_decision(observation_only:
    bool) -> ProcessorRegistry` to the bot, mirroring the existing
    `_fusion_engine_for_decision`. Returns the live or shadow
    registry instance constructed at bot startup.
  - Exposes `build_effective_decision_config(env_reader,
    processor_registry: ProcessorRegistry,
    fusion_engine: SignalFusionEngine, risk_engine: RiskEngine)
    -> dict`. Called EXACTLY ONCE per body AFTER `observation_only`
    is resolved — specifically at the TOP of
    `_make_trading_decision_body`, BEFORE any gate fires, so the
    recorder can be opened next (Gamma-1). The bot call site passes
    `fusion_engine=self._fusion_engine_for_decision(observation_only)`
    and `processor_registry=self._processor_registry_for_decision(observation_only)`
    so shadow records reflect the shadow surfaces.
  - Every env read, literal constant, and per-processor param read
    inside `_make_trading_decision_body` is refactored to read from
    this dict. RP9 + TC57 enforce.
- **Beta-7** — In `deribit_pcr_processor.py`:
  - Cache `_cached_result` extended to retain `raw_payload_hash`
    (always) and `raw_payload` bytes (when
    `RAW_DECISION_SNAPSHOT_INCLUDE_DERIBIT_RAW=1`). Hash is over the
    raw Deribit `data["result"]` LIST as returned upstream — i.e.,
    the raw upstream list of option-summary dicts BEFORE the
    processor builds its derived output dict. The processor's
    derived output dict (which contains synthesized `fetched_at`)
    is NOT hashed. Memory cost: ~200KB–1MB per cache slot when raw
    opt-in is on; cache size is 1 entry → bounded.
  - Add `pcr_data_override: Optional[dict]` kwarg to `process()`.
    When set, return override values without HTTP call. Replayer
    always sets it. The cache-hit path constructs a FRESH
    `TradingSignal` with `timestamp=now` (the `now=` kwarg from
    Beta-2), NOT the original `fetched_at`. This preserves the §7.1
    "fusion recency window inert" rationale.
  - Add `_parse_dte(..., now: datetime)` required arg (M11). Sweeps
    that recompute use the captured fetch-time clock.
  - Add `last_fetch_diagnostics()` returning the §4.2 deribit_pcr
    block including `fresh_fetch_performed`.
  - If §3.A row 3 disposition (`_parse_dte` try/except) is REMOVED,
    a malformed instrument name causes `_fetch_pcr` to raise;
    `fresh_fetch_performed=false`; the cached bytes from a prior
    successful fetch (if any) remain available as the cache-hit
    payload until the next successful refresh. The recorder marks
    `fetched_at` with `Unobservable.deribit_fetch_silently_failed`
    on a parse-driven miss when no prior successful cache exists.
- **Beta-8** — In `execution/risk_engine.py`:
  - Replace every naïve `datetime.now()` / `datetime.now().date()`
    in `risk_engine.py` (lines 85 init, 223, 302, 303, 453, 482,
    507, 514, 519 per §3.A item 14) with UTC-aware reads. Line 302
    `metadata.get("entry_time", datetime.now())` is REMOVED — entry_time
    is required-present; missing raises. Specifically:
    - Line 85 (init `_stats_date`): `datetime.now(timezone.utc).date()`.
    - Line 223 (`add_position` metadata write `"entry_time": datetime.now()`):
      rewrite to `"entry_time": now` — re-use the required `now=`
      kwarg the method now takes (per Beta-8 five-method
      propagation). Using a fresh wall-clock `datetime.now(timezone.utc)`
      here instead of `now` would smuggle a wall-clock read inside
      the helper (M11 violation) AND diverge by ε seconds from
      the caller's `now`, breaking the recorder's "captured state
      == validate state" invariant.
    - Line 303 (`update_position` time_held computation): DELETE
      `update_position` entirely. `grep -n "update_position" bot.py`
      returns zero hits, BUT a repo-wide grep
      `grep -rn "\.update_position\b" --include="*.py" | grep -v "/\\.claude/"`
      returns THREE additional caller sites that MUST also be
      dispositioned in the same Beta-8 diff:
      - `execution/execution_engine.py:410`
        (`risk_pos = self.risk_engine.update_position(position_id,
        current_price)` inside `ExecutionEngine.update_positions`
        defined at line 398). The surrounding `update_positions`
        method is ALSO deleted (verified its sole caller is the
        test below; production grep
        `grep -rn "\.update_positions\b" --include="*.py" | grep -v test_`
        returns zero hits in production code).
      - `execution/test_execution.py:103`
        (`risk_pos = risk.update_position("test_pos_1", Decimal("67000"))`):
        the surrounding test function is deleted in the same diff.
      - `execution/test_execution.py:175`
        (`await execution.update_positions(Decimal("67000"))`):
        the surrounding test function is deleted in the same diff.
      Same cascade-deletion discipline Beta-5 applies to
      `optimize_weights` callers. TC80 extended to also assert
      `risk_engine.update_position` and
      `execution_engine.update_positions` raise `AttributeError`
      when accessed post-Beta-8, and that
      `grep -rn "\.update_position\b\|\.update_positions\b"
      --include="*.py" | grep -v "/\\.claude/"` returns zero hits.
      Additional dead-helper cleanup: after `update_position` is
      deleted, `_assess_risk_level` (`risk_engine.py:413`),
      `_check_stop_loss` (`risk_engine.py:426`), and
      `_check_take_profit` (`risk_engine.py:438`) become
      unreferenced in production (`update_position` was their
      sole caller). Beta-8 DELETES all three private helpers in
      the same diff (file-length discipline per CLAUDE.md Rule 7;
      avoids dead-code drift). Confirm with `grep -n
      "_assess_risk_level\|_check_stop_loss\|_check_take_profit"
      risk_engine.py` returns ONLY the def lines pre-deletion
      and ZERO hits post-deletion.
    - Lines 514 (`reset_daily_stats`) and 519
      (`_maybe_reset_daily_stats`): accept a `now: datetime` UTC-
      aware arg (M11; required) and use `now.date()`. Every caller
      propagates the UTC `now` (typically `decision_reference_time`).
    - This shifts the daily-reset boundary from local-TZ to UTC per
      G6 bullet (b). Bot startup gates on
      `POLYBOT_ACKNOWLEDGE_UTC_DAILY_RESET=1` (M8-style required-
      presence enforcement); without it, the bot raises at startup
      with a message explaining the boundary shift and pointing at
      the operations doc.
  - Add `state_snapshot(now: datetime) -> dict` (required arg, M11)
    returning the §4.2 `risk_engine_state` block.
    **READ-AFTER-IDEMPOTENT-RESET semantics** (NOT pure read — the
    "Pure read" label in v3/v4 was inaccurate):
    `state_snapshot` internally calls
    `self._maybe_reset_daily_stats(now=now)` as its FIRST step,
    THEN reads the resulting state. The reset is the same one
    `validate_new_position` would run a moment later (idempotent
    when both share `now`). This eliminates the v3 cross-day
    paradox without introducing a new mutation surface — the
    reset is one that the production code path already performs.
    The ONLY fields `state_snapshot` may write are
    `_stats_date`, `_daily_pnl`, `_daily_trades` (the reset trio).
    Does NOT call into IO; does NOT enqueue any work.
    `stats_date_source` in the recorded `risk_engine_state` is
    `"captured_pre_reset"` when `now.date() == _stats_date`
    before this call (no reset fired), `"captured_post_reset"`
    when the call performed the reset. The recorder's view and
    `validate_new_position`'s view are guaranteed identical
    because they share the same `now`.
    Pre-existing concurrency model: callers serialize access to
    the risk engine through `_signal_processing_lock` (or accept
    the race, as today). This plan does not introduce a new
    concurrency surface; documenting the existing model is for
    reviewer clarity.
    TC46 asserts: state_snapshot does NOT write any field other
    than the reset trio; does NOT call IO; does NOT enqueue work.
  - Add `state_override: Optional[dict]` kwarg to
    `validate_new_position`. Replayer injects recorded state.
  - `validate_new_position(now=...)` and
    `_maybe_reset_daily_stats(now=...)` both become required-`now`
    callers. `_maybe_reset_daily_stats()` is called from FIVE sites
    inside `risk_engine.py`: line 115 (`validate_new_position`),
    209 (`add_position`), 340 (`remove_position`), 388
    (`record_realized_pnl`), 403 (`restore_daily_stats`). Beta-8
    promotes `now: datetime` to a REQUIRED kwarg on each of those
    public methods, propagated into the internal
    `_maybe_reset_daily_stats(now=...)` call. Production caller
    enumeration (with the source for `now` at each site):
    - `bot.py:5525` — `validate_new_position(now=decision_snapshot.reference_time, ...)`
      (the in-scope variable is `decision_snapshot`, not `snapshot`).
    - `bot.py:1687` — `add_position(now=datetime.now(timezone.utc),
      ...)` (open-settlement-risk rehydrate inside
      `_rehydrate_open_settlement_risk`; called once from
      `__init__` at startup; NO `decision_snapshot` in scope; this
      is the SECOND documented wall-clock-startup carve-out,
      paired with bot.py:1753). Equivalent cleaner shape: thread a
      single `now: datetime` UTC-aware kwarg into
      `_rehydrate_open_settlement_risk(now=datetime.now(timezone.utc))`
      from the `__init__` callsite at `bot.py:1047`, so the helper
      layer stays M11-clean and the wall-clock read lives at the
      single `__init__` frame. Implementer's choice; both shapes
      preserve the "wall-clock read is unconditional" semantics
      that distinguishes this from an M4 fallback.
    - `bot.py:6212` — `add_position(now=decision_reference_time, ...)`
      (live-fill bookkeeping inside `_place_real_order(...,
      decision_reference_time: datetime, ...)`; the bare param is
      in scope).
    - `bot.py:3569` — `remove_position(now=exit_time, ...)`
      (settlement reconciliation inside
      `_record_settlement_accounting(..., exit_time: datetime,
      ...)`; the bare `exit_time` param is in scope and is
      UTC-aware as derived from the settlement event payload —
      NOT `datetime.now()`).
    - `bot.py:1753` — `restore_daily_stats(now=datetime.now(timezone.utc),
      ...)` (daily-stats rehydrate path inside
      `_rehydrate_settled_daily_risk`; called once from `__init__`
      at startup; no decision context exists; first documented
      wall-clock-startup carve-out).
      **Rehydrate-filter UTC coherence (R1-Q4 fix).** The same
      function also computes `today` at `bot.py:1702` via
      `datetime.now().astimezone().date()` (local-TZ) and filters
      settlements by `settled_at.astimezone().date() != today`
      at `bot.py:1722` (also local-TZ). Beta-8 MUST convert both
      lines to UTC:
      - `bot.py:1702` → `today = datetime.now(timezone.utc).date()`
        (derived from the same UTC `now` passed to
        `restore_daily_stats` so both axes share the UTC
        calendar).
      - `bot.py:1722` → filter via
        `settled_at.astimezone(timezone.utc).date() != today`.
      Without this conversion, a non-UTC operator restarting
      during the UTC↔local-TZ midnight overlap window sums trades
      labeled by local-TZ "today" into `daily_pnl`, then
      `restore_daily_stats(now=UTC_now)` writes a
      `_stats_date = UTC_now.date()` that disagrees by one
      calendar day; `_maybe_reset_daily_stats(now=UTC_reference_time)`
      on the next decision body then skips the reset and the
      `max_loss_per_day` gate at `risk_engine.py:135` evaluates
      against an inflated `_daily_pnl`. TC72b asserts that a
      pre-populated settlement fixture straddling the UTC
      boundary produces a rehydrate `daily_pnl` sum matching the
      UTC-day window exactly (paired with TC72's bot-started-at-
      22:00-PST scenario).
    - Internal at `risk_engine.py:242` — `adjust_position` calls
      `self.add_position(now=now, ...)` forwarding its own required
      `now` kwarg.
    - `bot.py:3578` — `record_realized_pnl(now=exit_time, ...)`
      (inside `_record_settlement_accounting(..., exit_time:
      datetime, ...)`; same UTC-aware `exit_time` already used at
      the adjacent `remove_position@3569` call). This is the SOLE
      production caller of `record_realized_pnl` (verified via
      `grep -n "record_realized_pnl" bot.py`).
    NO `datetime.now()` default is acceptable at the helper layer
    (M11). The wall-clock-read exceptions, ALL documented as
    deliberate boundaries (NOT fallbacks under M4 because the
    reads are unconditional at their frame, not branch-substituting
    ones):
    - `bot.py:1687` (open-settlement-risk rehydrate at startup).
    - `bot.py:1753` (daily-stats rehydrate at startup).
    - `risk_engine.py:482, 507` inside `get_risk_summary()` and
      `risk_engine.py:453` inside `_create_alert()` —
      metrics/diagnostics surfaces NOT on the verdict path.
      `get_risk_summary` is consumed by `monitoring/grafana_exporter.py`
      (read-only metrics polling). `_create_alert` produces an
      operator-visible alert and is reasonable to timestamp via
      wall-clock at the alert-creation instant rather than
      threading `now` from every caller. Documented as a
      "metrics/diagnostics surface" carve-out — meaning the
      carve-out is for CALLER-PROPAGATION ONLY (no `now=` kwarg
      added; wall-clock read remains at the helper). The wall-
      clock reads at lines 453, 482, 507 ARE rewritten to
      `datetime.now(timezone.utc)` (UTC-aware) per RP8 — this is
      not relaxed by the carve-out. Without UTC-aware
      `_create_alert` writes, the `(datetime.now(timezone.utc) -
      a["timestamp"]).seconds` subtraction at line 507 raises
      `TypeError: can't subtract offset-naive and offset-aware
      datetimes` the moment Grafana polls `get_risk_summary()`
      after the first alert. TC82 enforces.
    All other helper-layer reads thread `now=` from the decision
    context.
    The previously-cited callers `execution/execution_engine.py:182,
    410` are NOT reached by production code paths — `bot.py` does
    not import `ExecutionEngine` directly; the file IS imported
    transitively via `monitoring/grafana_exporter.py:31`, but the
    only consumer method (`get_statistics`) does not transit
    `validate_new_position` / `_maybe_reset_daily_stats`. So Beta-8
    `now=` propagation is genuinely unneeded there.
    `execution/execution_engine.py` is added to the RP8 EXCLUDED
    list (§11) with this caveat documented.
    Audit confirmation in Beta-8 cover letter:
    `grep -rn "risk_engine\." bot.py | grep -v "now="` returns
    zero hits post-Beta-8 for `add_position`, `remove_position`,
    `record_realized_pnl`, `restore_daily_stats`,
    `validate_new_position`. TC80 mirrors TC56c: calling each of
    those five public methods without `now=` raises TypeError.
  - Refactor risk-engine env defaults at lines 70-77 per §3.A item
    13: promoted to required envs via the §12 convention. Audit
    the `RiskEngine(...)` construction site in `bot.py`: if the
    caller passes an explicit `limits=` arg, the env path is dead
    and the promotion is a no-op refactor; if the env path is
    reachable, removing defaults is an observable change captured
    by the byte-equal-fixture test (TC06). The audit result is
    documented in the Beta diff cover letter.
  - **`_alerts` aware/naïve safety.** The UTC conversion at
    `risk_engine.py:482, 507` (inside `get_risk_summary()`)
    changes `datetime.now()` to `datetime.now(timezone.utc)`,
    AND every `self._alerts.append({..., "timestamp": ..., ...})`
    population site (grep
    `self\._alerts\.append\|self\._alerts =` in risk_engine.py)
    is updated to write a UTC-aware datetime. This prevents the
    aware-minus-naïve `TypeError` in the line-507 subtraction
    `(datetime.now(timezone.utc) - a["timestamp"]).seconds`.
    `_alerts` is in-process state cleared at every restart, so
    no on-disk migration is needed (NOT an M3 migration).
  - **Grafana panel timestamp drift — defensive callout only.**
    `get_risk_summary()` returns a `timestamp` field that becomes
    UTC-aware after Beta-8. CURRENT consumer at
    `monitoring/grafana_exporter.py:336` reads `risk_summary` and
    ONLY consumes `risk_summary['exposure']['utilization_pct']`
    (lines 339-341); the `timestamp` field is NOT surfaced into
    any Grafana panel today. The Theta-7 callout is therefore
    DEFENSIVE: "if a future panel JSON adds a query reading
    `risk.timestamp` (e.g., from `exec_stats['risk']['timestamp']`),
    it will see the UTC-aware value." No operator action required
    for the current dashboard.
  - TC82 asserts `get_risk_summary()["timestamp"].tzinfo is not
    None` AND the subtraction at line 507 does not raise on a
    fixture that pre-populates `_alerts` via the new
    UTC-aware-only append helper.
- **Beta-9** — DEEP COPY semantics for capture (referenced by §4.2):
  - `fusion.call_inputs.weights = dict(fusion_engine.weights)` —
    shallow copy of dict[str, float] (sufficient since values are
    immutable floats).
  - `risk_engine_state.positions` — DEEP COPY of each PositionRisk
    (PositionRisk is a mutable dataclass per `risk_engine.py:33`;
    no `frozen=True`). The recorder helper does the copy. TC61
    enforces.
- **Beta-10** — Refactor liquidity-floor consumer. Today
  `bot.py:5544` hardcodes `MIN_LIQUIDITY = Decimal("0.02")`. §12
  promotes `LIQUIDITY_FLOOR` to a required env, but for the
  promotion to actually affect verdicts, the production gate at
  `bot.py:5546/5557` must read from `effective_config.liquidity_floor`
  (built in Beta-6) instead of the literal. Beta-10 is the small
  refactor that wires the gate to the effective-config dict. The
  §4.4 `liquidity_floor` row's `inputs.min_liquidity_threshold` is
  populated from the same source. Without Beta-10, sweeping
  `liquidity_floor` in the harness is silently a no-op on the
  production gate (the replayer would use the swept value but live
  parity would diverge). TC23 enforces verdict change on sweep.

Verification:

- Per processor, byte-equal-fixture test asserts `process(...,
  now=fixture_now)` yields the same `TradingSignal` (or None) as the
  prior implementation called at the same wall-clock instant.
- `fuse_signals(..., now=fixture_now)` produces the same FusedSignal
  (or None); `last_fusion_diagnostics()` populated at every return.
- `build_effective_decision_config(...)` returns the same dict for
  the same input twice; bot's `_make_trading_decision_body` produces
  the same verdict before and after the refactor (TC06).
- `risk_engine.state_snapshot(now)` writes ONLY the reset trio
  (`_stats_date`, `_daily_pnl`, `_daily_trades`); never IO; never
  enqueues work; TC46 enforces.
- `state_snapshot(now)` with `now.date() != stats_date` performs
  the idempotent reset internally (does NOT raise; v3's raise
  was retracted in v5 — see Beta-8 body for rationale).
- DecisionInputSnapshot extension preserves every existing reader.

Validation:

- A captured fixture decision replayed at the recorded
  `decision_reference_time` produces same signals, fusion,
  diagnostics, and risk gate verdict.

Test coverage adds: TC11, TC12, TC13, TC14, TC23a, TC42, TC43, TC46,
TC56, TC56b, TC56c, TC57, TC61.

Regression prevention adds:

- RP8 extended: static check forbids `datetime.now()` without
  `timezone.utc` AND forbids `datetime.now(timezone.utc)` as a
  default-value expression in any signature in the touched file set.
- RP9: static check forbids env reads inside
  `_make_trading_decision_body` after the effective config dict is
  built.
- Static check that the fusion-engine `fuse_signals()` does not read
  `_signal_history` / `_fusions_performed`.

Per-phase 3-reviewer gate per §10.

### 6.3 Phase Gamma — Capture-Point Wiring

Aim: wire the recorder at exactly one site inside
`_make_trading_decision_body`, AT THE TOP of the body (BEFORE any
gate fires), so EVERY gate evaluation (including the early ones —
`live_paused_unresolved_settlement`, `quote_stability`,
`history_too_short`, `snapshot_freshness_before_context`) is captured.
This is the v4 fix for the v3 wiring bug where the recorder opened
AFTER `build_effective_decision_config(...)` resolved fusion_engine
and processor_registry, which happened after several early gates had
already fired.

The recorder is the INNER context relative to the existing
`DecisionRecord`. On `__exit__`, the recorder's `__exit__` runs FIRST
(writes raw line OR raises on write failure), then
`DecisionRecord.__exit__` runs (writes `decisions.jsonl` line). This
ordering means a raw-write failure CANNOT lose the compact summary —
the `decisions.jsonl` write still happens in `DecisionRecord.__exit__`
even when the inner `raw_rec.__exit__` raised (Python chains the
exception). G10's join-completeness expectation accounts for this via
`raw_decisions_skipped.jsonl` per §5.6. TC58 asserts the order.

Work items:

- **Gamma-1.6 — Mode resolution helper.** Extract a named function
  `_resolve_observation_mode(strategy_observation_mode: Optional[str],
  is_simulation: bool) -> Literal["live_gate", "shadow_policy",
  "simulation"]` and call it exactly once at the top of
  `_make_trading_decision` after the `DecisionRecord` open. Every
  path into `_make_trading_decision_body` passes the resolved value.
  TC32b asserts the helper returns one of exactly the three values
  for every (strategy_observation_mode, is_simulation) Cartesian-
  product input.
- **Gamma-1** — Restructure `_make_trading_decision_body` so that
  the resolution of `fusion_engine`, `processor_registry`, and
  `effective_config` happens BEFORE the recorder opens — moved up
  to the TOP of the body. The recorder then opens as the INNER
  scope wrapping every gate evaluation including the early ones:

  ```text
  with DecisionRecord(...) as rec:                # OUTER (existing)
      observation_mode = _resolve_observation_mode(...)            # Gamma-1.6
      rec.update(strategy_observation_mode=observation_mode)
      return await self._make_trading_decision_body(
          decision_snapshot, trade_key, is_simulation, rec,
          observation_mode=observation_mode,
      )

  # Inside _make_trading_decision_body(decision_snapshot, trade_key,
  #                                    is_simulation, rec, *,
  #                                    observation_mode):
  observation_only = (observation_mode == "shadow_policy")
  fusion_engine = self._fusion_engine_for_decision(observation_only)
  processor_registry = self._processor_registry_for_decision(
      observation_only
  )
  effective_config = build_effective_decision_config(
      env_reader=os.environ,
      processor_registry=processor_registry,
      fusion_engine=fusion_engine,
      risk_engine=self.risk_engine,
  )
  with RawDecisionSnapshotRecorder(                # INNER
      decision_id=decision_snapshot.decision_id,
      bot_mode=observation_mode,
      snapshot=decision_snapshot,
      effective_config=effective_config,
      decision_record=rec,                          # for FIELD_MAP proxy
  ) as raw_rec:
      # Every gate (starting with live_paused_unresolved_settlement,
      # quote_stability, history_too_short, snapshot_freshness_*) is
      # NOW inside the recorder's with block; every rec.reject or
      # rec.update mirrors via FIELD_MAP at __exit__.
      ...body continues, no further restructure...
  ```

  This restructure satisfies G1 ("recorder writes exactly one raw
  line regardless of which gate the decision exits at, including
  freshness rejects, quote-stability rejects"). It does NOT introduce
  any new computation at body entry — `fusion_engine` /
  `processor_registry` resolution is O(1) attr access; the
  `build_effective_decision_config` call is a single dict
  construction with no IO.
  **Caller enumeration**: every callsite of
  `_make_trading_decision_body` in `bot.py` is updated to pass
  the new keyword-only `observation_mode=` arg. Grep
  `_make_trading_decision_body\b` over `bot.py` enumerates every
  site; the Beta-/Gamma-implementer audits the list in the
  Gamma-1 diff cover letter. TC32b is extended to assert
  `_make_trading_decision_body` raises `TypeError` when called
  without `observation_mode=` (mirrors the M11 required-no-default
  discipline applied elsewhere). TC06 + TC07 byte-equal-fixture assertions
  catch any verdict regression from the restructure.
  Cost verification: TC78 benchmarks `build_effective_decision_config(...)`
  at the body entry on a fixture decision and asserts the call
  completes in &lt; 1 ms (well under the per-decision latency budget;
  the function reads ~30 env vars + ~6 processor `effective_params()`
  dicts + ~10 risk-engine attrs, all in-memory). If the measured
  cost ever exceeds 1 ms, the test fails and the implementer must
  either optimize or move the build inside the recorder's
  `__enter__` (a deferred refactor that would conflict with the
  current Gamma-1 ordering and would itself require explicit
  spec revision).

- **Gamma-1.5 — FIELD_MAP proxy (MAPPED + IGNORED split).** Manual
  mirroring is too error-prone. Introduce `raw_decision_snapshot.FIELD_MAP`
  as TWO disjoint dicts:
  - `FIELD_MAP.MAPPED: dict[str, str]` — keys are `decisions.jsonl`
    field names whose values land in the raw schema. Value is the
    raw-schema dotted path (e.g., `"slug"` → `"market.market_slug"`,
    `"yes_ask"` → `"frozen_quotes.yes_ask"`). NOTE:
    `decision_snapshot_age_seconds` is NOT in MAPPED — the value
    gets overwritten by `rec.update(...)` at each freshness gate
    (before_context, before_signals, before_execution,
    before_intent_persistence), so a single map entry would lose
    the prior values. Instead, the raw recorder exposes
    `raw_rec.record_freshness_age(gate_suffix, age_seconds,
    max_age_seconds)` and the freshness helpers
    `_live_decision_snapshot_is_fresh` / `_live_order_reference_time_is_fresh`
    call it BEFORE their existing `rec.update(...)`.
    Helper signature changes (required `raw_rec` kwarg; no default,
    M11-style):
    - `_live_decision_snapshot_is_fresh(self, decision_snapshot,
       rec, *, gate_suffix, raw_rec)` — updated callers at
       `bot.py:5204, 5217, 5517`.
    - `_live_order_reference_time_is_fresh(self, decision_record,
       reference_time, checked_at, context, *, gate_suffix,
       raw_rec)` — updated callers in the body and at `bot.py:6175`
       (via `_place_real_order`).
    - `_place_real_order(...)` gains required `raw_rec` kwarg so it
       can forward to the freshness helper at 6175. Updated body
       call site at `bot.py:5576` passes `raw_rec=raw_rec`.
    Each call writes into `decision_snapshot_age_at_gate.<suffix>`
    so ALL FOUR values are preserved. TC76 enforces all four gates.
  - `FIELD_MAP.IGNORED: dict[str, str]` — keys with NO raw-schema
    destination. Value is the rationale string. Each rationale is
    REVIEW-VERIFIED: a follow-up reviewer asserts that the raw
    schema actually carries the field needed to reconstruct the
    `decisions.jsonl` value. (Indexing-convention note for this
    cluster, satisfying RP13: every `gates[-N]` reference in the
    rationales below is POST-APPEND per §4.4's canonical
    convention.) Examples:
    - `"decided_direction": "post-v13 (schema_version=1, v16): one-row
      lookup via gates[-1].output.fusion_direction on accept
      (gates[-1] is the trailing final_decision row per §4.4);
      equivalent to depth_replay.selected_side (YES → long,
      NO → short). On reject / exception, the source is null /
      `_unobservable: final_decision_not_accepted` per §4.5."`.
    - `"rejected_at_gate": "post-v13: gates[-1].inputs.failing_gate
      on reject (the trailing final_decision row's failing_gate
      field). Equivalent to gates[-2].name (the gate one before
      the trailing final_decision). On accept, both sources are
      null. On exception, gates[-1].inputs.failing_gate is the
      gate-scope-attributed gate name if available, else null."`.
    - `"rejection_reason": "post-v13: gates[-2].reason (the actually-
      failing gate's reason; gates[-1] is the trailing final_decision
      whose reason mirrors gates[-2].name)."`.
    - `"sizing_mode": "in effective_config (single source of
      truth) AND in gate inputs for position_size_*/balance_guard"`.
    - `"model_signals": "raw signals[] block carries the same
      data with richer fields including signal_id and timestamp"`.
    - `"fused_confidence" / "fused_direction": "post-v13: preferred
      source is gates[-1].output.fusion_confidence /
      gates[-1].output.fusion_direction on accept (one-row lookup,
      reference-copied from fusion.confidence/.direction per §4.4).
      Falls back to fusion.confidence / fusion.direction for replayer
      cross-check (TC83 asserts byte-equality of the two sources)."`.
    - `"resolved_trade_usd": "in gates[name=position_size_*].inputs
      and gates[name=balance_guard].inputs"`.
  The recorder receives the `rec` reference in its constructor and
  on `__exit__` walks MAPPED, copying populated `rec.fields[k]` to
  the corresponding raw path; keys in IGNORED are deliberately not
  copied. Raw-only fields (everything not in MAPPED ∪ IGNORED) are
  populated via explicit `raw_rec.record_gate(...)`,
  `raw_rec.record_signal(...)`, `raw_rec.record_fusion_diagnostics(...)`,
  `raw_rec.record_depth_replay(...)`, `raw_rec.record_pre_state(...)`,
  `raw_rec.record_risk_engine_state(...)` helpers called at the
  appropriate sites in the body. TC66 asserts every
  `rec.update(**kwargs)` kwarg key in `_make_trading_decision_body`
  is in MAPPED ∪ IGNORED; adding a new key without classification
  fails CI. The FIELD_MAP file is checked in and reviewed.
- **Gamma-2** — Recorder population sources:
  - Frozen quotes, market identity, price_history, tick_buffer →
    directly from the extended `DecisionInputSnapshot`.
  - Order books, Fear&Greed, Coinbase spot → from the `metadata`
    dict from `fetch_market_context_for_snapshot`. NO refetch (M7).
  - **Pre-state capture** (replaces v2 Beta-1 ambiguity): inside the
    body, AFTER the recorder is open AND AFTER
    `processor_registry = self._processor_registry_for_decision(observation_only)`
    has selected the correct (live vs shadow) processor instances,
    and BEFORE `_process_signals` runs, the body calls
    `raw_rec.record_pre_state(
        coinbase_spot_history=processor_registry.divergence.spot_history_pre_state_snapshot(),
        deribit_pcr_cache=processor_registry.deribit_pcr.last_fetch_diagnostics(),
    )`.
  - Fusion → restructure `bot.py:5279-5283` so the existing
    `with self._signal_processing_lock:` block wraps BOTH the
    `fuse_signals(...)` call AND the
    `raw_rec.record_fusion_diagnostics(
        fusion_engine.last_fusion_diagnostics(),
        call_inputs={...from effective_config + dict(fusion_engine.weights)},
    )` call. Concretely:

    ```text
    with self._signal_processing_lock:
        fused = fusion_engine.fuse_signals(
            signals,
            min_signals=effective_config.fusion_min_signals,
            min_score=effective_config.fusion_min_score,
            now=decision_snapshot.reference_time,
        )
        raw_rec.record_fusion_diagnostics(
            fusion_engine.last_fusion_diagnostics(),
            call_inputs={...from effective_config
                         + dict(fusion_engine.weights)},
        )
    if not fused:
        rec.reject("fusion_no_consensus", ...)
    ```

    The lock is held across BOTH calls so no other thread can
    overwrite `_last_diagnostics` between them. The subsequent
    `rec.reject(...)` runs OUTSIDE the lock. The production lock is
    `RLock` (`bot.py:949`); the recorder's append lock is the
    SEPARATE `threading.Lock` per §4.1 — the two are unrelated
    surfaces. TC62 verifies the lock-window invariant.
  - Risk engine state → because Beta-8 makes `state_snapshot(now)`
    internally call `_maybe_reset_daily_stats(now=now)` first, the
    recorder calls
    `raw_rec.record_risk_engine_state(
        risk_engine.state_snapshot(now=decision_snapshot.reference_time)
    )` IMMEDIATELY before `validate_new_position(state_override=None)`
    runs. Both share the same `now` so they see the same post-
    reset state. No race; no fail-stop on day rollover.
  - Depth-replay population (required for final_decision accept
    path per §4.4 + Gamma-4): AFTER `_compute_depth_aware_entry_details`
    returns a non-None `DepthAwareEntry` (the production site at
    `bot.py:5477-5487` where the existing
    `rec.update(executable_entry=..., estimated_tokens_filled=...,
    estimated_actual_cost=..., depth_fully_filled=...)` fires),
    the body calls
    `raw_rec.record_depth_replay(
        selected_side=direction,
        selected_token_id=side_token_id,
        selected_order_book_side=("yes" if direction=="long" else "no"),
        top_of_book_entry=top_of_book_entry,
        order_type=effective_config.ORDER_TYPE,
        accepted_limit_price=accepted_limit_price,
        submitted_limit_price=submitted_limit_price,
        limit_order_token_qty=limit_order_token_qty,
        instrument_price_precision=(
            instrument.price_precision
            if effective_config.ORDER_TYPE == ORDER_TYPE_LIMIT_IOC
            else None
        ),
        instrument_size_precision=(
            instrument.size_precision
            if effective_config.ORDER_TYPE == ORDER_TYPE_LIMIT_IOC
            else None
        ),
        estimated_vwap=depth_entry.executable_entry,
        estimated_tokens_filled=depth_entry.tokens_filled,
        estimated_actual_cost=depth_entry.actual_cost,
        depth_fully_filled=depth_entry.fully_filled,
    )`. NAME RESOLUTION at the call site (verified against
    bot.py): `top_of_book_entry` is a LOCAL VARIABLE (set at
    `bot.py:5377` / `bot.py:5388`), NOT an attribute of the
    returned `DepthAwareEntry` (which has only
    `executable_entry`, `tokens_filled`, `actual_cost`,
    `fully_filled` per its dataclass definition at `bot.py:196-201`).
    `direction`, `side_token_id`, `accepted_limit_price`,
    `submitted_limit_price`, `limit_order_token_qty`,
    `top_of_book_entry`, `depth_entry` are local variables in
    scope at the `bot.py:5477-5487` call site.
    **`instrument` MAY BE UNBOUND on the MARKET_IOC path**
    (v16 fix for round-13 reviewer #3 P3): `instrument` is
    assigned only inside the `if order_type ==
    ORDER_TYPE_LIMIT_IOC:` block at `bot.py:5432`. On the
    MARKET_IOC path, the name is unbound and the conditional
    expression `instrument.price_precision if effective_config.ORDER_TYPE
    == ORDER_TYPE_LIMIT_IOC else None` would raise `NameError`
    because Python evaluates the attribute-access lexically
    regardless of the surrounding conditional. FIX (mandatory
    part of Gamma-2): the implementer SPLITS the call into a
    conditional preparation step:
    `instrument_price_precision = (instrument.price_precision
    if effective_config.ORDER_TYPE == ORDER_TYPE_LIMIT_IOC
    else None)` is ONLY evaluated inside the `if order_type
    == ORDER_TYPE_LIMIT_IOC:` branch where `instrument` is
    bound; on the MARKET_IOC branch, `instrument_price_precision
    = None` and `instrument_size_precision = None` are set
    explicitly before the `record_depth_replay(...)` call. The
    `record_depth_replay(...)` call then passes the local
    variables, NEVER a bare `instrument.<attr>` expression.
    TC02i (new in v16) asserts the Gamma-2 implementation
    runs on a MARKET_IOC fixture without `NameError`.
    `ORDER_TYPE_LIMIT_IOC` is the existing module-level string
    constant in `bot.py` (NOT a string literal "LIMIT_IOC" — the
    constant is compared rather than the literal to avoid
    case/spelling drift; Beta-6 pins the
    `effective_config.ORDER_TYPE` field's exact value to be
    bit-equal to `ORDER_TYPE_LIMIT_IOC` when the LIMIT_IOC path
    is selected).
    This is the SOLE production caller of `record_depth_replay`.
    On the reject path (depth_aware_entry fails or any subsequent
    gate rejects), `record_depth_replay` is NEVER called →
    `depth_replay.selected_side is None` remains the recorder's
    default state → Gamma-4 normal-exit step (2) computes
    `outcome="rejected"`. TC83(a) asserts the accept path
    populates the seven §4.4 output-dict keys with byte-equal
    values to the corresponding depth_replay fields.
- **Gamma-2.5 — Risk engine state ordering.** The recorder reads
  `risk_engine.state_snapshot(now=decision_snapshot.reference_time)` and then
  calls `validate_new_position(now=decision_snapshot.reference_time, ...)`
  BACK-TO-BACK with no intervening yields or lock releases. Both
  share the same UTC-aware `now`. Beta-8 makes `state_snapshot(now)`
  internally call `_maybe_reset_daily_stats(now=now)` as its first
  step, then read the post-reset state. `validate_new_position(now=
  now, ...)` then runs the same `_maybe_reset_daily_stats(now=now)`
  again as its first step; the reset is idempotent for the same
  `now` so the gate sees the SAME state the recorder captured.
  `stats_date_source` in the recorded `risk_engine_state` is
  `"captured_pre_reset"` when `now.date() == _stats_date` before
  this call, `"captured_post_reset"` when the snapshot's internal
  reset fired. NO cross-day raise; NO fail-stop regression at UTC
  midnight. TC72 asserts: bot started at 22:00 PST (06:00 UTC next
  day) → first decision's `state_snapshot(now=UTC_reference_time)`
  succeeds, performs reset, and the subsequent
  `validate_new_position` sees the same post-reset state.
- **Gamma-3** — `DecisionRecord` open sites in `bot.py` fall into
  THREE categories, each with its own required source comment:
  - **Truly NOT WIRED** (no raw line ever produced):
    - `_record_decision_snapshot_capture_exception` at `bot.py:4843`
    - `_record_decision_executor_enqueue_exception` at `bot.py:4888`
    Comment: "raw recorder intentionally not wired here; this
    `DecisionRecord` writes a compact summary only — see G1 + G10".
  - **WIRED BY DELEGATION** (raw line produced inside the called
    body, not at this site):
    - The pre-resolution `DecisionRecord(strategy_observation_mode=
      "mode_check_pending", ...)` opened in `_make_trading_decision`
      at `bot.py:5120-5139`. The body delegate at line ~5140 opens
      `RawDecisionSnapshotRecorder` INSIDE
      `_make_trading_decision_body`, which writes one raw line per
      body invocation per G1.
    Comment: "raw recorder wired in `_make_trading_decision_body`
    via delegation; see Gamma-1".
  - **WIRED INLINE**: the `RawDecisionSnapshotRecorder` open is in
    the same function body, immediately after this `DecisionRecord(`
    open. Currently zero such sites; reserved for any future
    pattern where a single function opens both directly.
  TC67 is a HYBRID AST + source-line test (NOT pure AST — Python
  AST strips comments). It (a) AST-walks every `DecisionRecord(`
  call site in `bot.py`; (b) for each site uses
  `inspect.getsource` to read 3 source lines preceding the call;
  (c) asserts ONE of the three category comments appears OR the
  call is followed (within 30 lines, in the same function body) by
  an open of `RawDecisionSnapshotRecorder(` (the WIRED-INLINE
  case). New `DecisionRecord(` call sites must be classified or
  CI fails.
- **Gamma-4a — Gate-scope wrappers in bot.py.** Each in-body gate
  evaluation is wrapped in `with raw_rec.gate_scope("<gate_name>"):`
  so that an exception raised inside the gate body lands in
  `gates[<gate>].inputs._unobservable = gate_exception_<gate_name>`
  (per §4.5) AND a `name="exception"` row is appended at
  `gates[-2]` post-append (the `gates[-1]` slot is always
  `final_decision` per §4.4's universal-trailing invariant; v16
  fix to v14/v15 wording that incorrectly said `gates[-1].name
  = "exception"`). Sites to wrap (one per §4.4 row except
  `executor_returned_false` and `exception`):
    `live_paused_unresolved_settlement` (bot.py:5176 area),
    `quote_stability` (5186), `history_length` (5195),
    `snapshot_freshness_before_context` (5204),
    `snapshot_freshness_before_signals` (5220),
    `snapshot_freshness_before_execution` (5500 area),
    `no_signals` (5255), `fusion` (5279-5283),
    `trend_filter` (5335), `signal_confirmation` (5353),
    `min_signal_confidence` (5363), `side_quote_available` (5372-5390),
    `depth_aware_entry` (5396 + 5659-5770 helper),
    `limit_price` (5415), `limit_token_qty` (5423-5453),
    `ev_gate` (5501), `position_size_below_minimum` (1210 area
    reached via 5296), `position_size_exceeds_max` (1217),
    `balance_guard` (1224), `risk_engine` (5532),
    `liquidity_floor` (5552-5567),
    `snapshot_freshness_before_intent_persistence` (wraps the
    `_live_order_reference_time_is_fresh(...)` call site at
    `bot.py:6175` INSIDE `_place_real_order`, NOT the surrounding
    `_place_real_order` body — so exception attribution is scoped
    to the freshness gate only).
  TC02c is a static AST check: every §4.4 `name` EXCEPT those in
  `raw_decision_snapshot.AUTO_APPENDED_GATE_NAMES ∪ {"executor_returned_false"}`
  appears as a string-literal argument to a `raw_rec.gate_scope(...)`
  call somewhere within `_make_trading_decision_body` OR within
  any function CALLED FROM the body (including helpers like
  `_place_real_order`). The exclusion set therefore is
  `{"executor_returned_false", "exception", "final_decision"}` —
  the two `__exit__`-appended names (`exception`, `final_decision`)
  plus the safety-net `executor_returned_false` row. The reach-from-
  body criterion is determined by static call-graph analysis:
  starting from `_make_trading_decision_body`, every function it
  calls (directly or transitively) within `bot.py` is in scope.
  Missing any name fails CI.
- **Gamma-4** — Recorder `__exit__` semantics. Explicit step ordering
  for both paths.
  - **APPROVED FALLBACK (CLAUDE.md Rule 1; "APPROVE THIS FALLBACK"
    granted by user 2026-05-23 round-13)** — Steps marked
    `[APPROVED-FALLBACK-FMC]` below catch FIELD_MAP-copy failures
    and continue to subsequent steps. Approval is conditional on
    the OBSERVABILITY GUARANTEE: every record where the fallback
    fires MUST be identifiable as such AND must show the failure
    reason. **v16 mechanism (redesigned from v15)**: the recorder
    populates the TOP-LEVEL `recorder_internal_failure` field
    (per §4.2) with `{"exception_type":
    type(field_map_copy_failure).__name__, "exception_str":
    str(field_map_copy_failure), "step": "field_map_copy"}`. The
    field is `null` on the happy path. The v15 design (appending
    a trailing exception row to `gates[]` after `final_decision`)
    is RETRACTED because it broke the universal-trailing
    `gates[-1]==final_decision` invariant and cascaded P0s into
    Delta-7 / TC83 / Zeta-7 / Eta-3. The v16 top-level field
    preserves the gates[] invariant for every record while still
    meeting the user's "identify + show reason" requirement: any
    consumer can detect the fallback fired via
    `record.recorder_internal_failure is not None` and read the
    failure type/message from the same dict. TC83(j) asserts:
    (a) on FIELD_MAP-copy failure,
    `record.recorder_internal_failure["exception_type"]` and
    `["exception_str"]` are both populated with non-empty
    strings; (b) `["step"] == "field_map_copy"`; (c) the trailing
    `final_decision` row is still at `gates[-1]` (G1 invariant
    upheld — no exception of the relaxation needed); (d) on
    happy-path records, `record.recorder_internal_failure is
    None`. Implementer adds a code comment at the try/except
    site: `# APPROVE THIS FALLBACK 2026-05-23 (Rule 1); see §6.3
    Gamma-4 approved-fallback note`.
  - Each step is wrapped in its own try-block where noted, so a
    failure inside one step does NOT skip the trailing
    `final_decision` append (the G1 universal-append invariant
    must hold even when partial-progress steps raise).
  - Normal exit (no in-flight exception) →
    (1) `[APPROVED-FALLBACK-FMC]` Walk FIELD_MAP.MAPPED and copy
        populated `rec.fields[k]` to raw paths. Wrap in try/except:
        on failure, capture the exception as
        `field_map_copy_failure`, mark unset observable-required
        fields with `Unobservable.exception_before_set`, and
        CONTINUE to step (2). The captured failure is recorded as
        a synthesized `name="exception"` gates entry at step (4.5)
        and re-raised AFTER the record write per step (6).
    (2) Compute `outcome` and `failing_gate` from the pre-append
        `gates[]` snapshot (call it `gates_pre`). NOTE: indices
        used in this step are PRE-APPEND; post-append indices in
        §4.4 differ by one (see top-of-§4.4 indexing-convention
        note). Branches (mutually exclusive, evaluated in order):
          - any `g in gates_pre` has `g.passed == false`:
            `outcome="rejected"`,
            `failing_gate = next(g.name for g in gates_pre if not g.passed)`
            (FIRST passed=false; under §4.4's omitted-not-failed
            convention there is at most one — see TC02e). `reason
            = failing_gate`.
          - else if `depth_replay.selected_side is not None`:
            `outcome="accepted"`, `failing_gate=null`,
            `reason="accepted"`. (NOTE: this branch is reachable
            with `gates_pre` empty if NO `gate_scope` writes a
            `passed=true` row; whether empty `gates_pre` is
            reachable on accept depends on Alpha-1's `gate_scope`
            success-exit semantics — see Alpha-1 below. Both
            "gate_scope writes passed=true on success" and
            "gate_scope writes NOTHING on success" are compatible
            with this branch; the §4.4 row + TC83(a) accept-path
            assertions hold either way.)
          - else: `outcome="rejected"`, `failing_gate=null`,
            `reason="missing_selected_side"`. This branch fires
            when every prior gate passed (or none ran) but
            `depth_replay` was never populated (a body bug —
            should not happen with Beta-10/Gamma-2 wiring; TC83(b2)
            asserts zero such fixtures in the 24h corpus AND
            asserts the branch's emitted reason is exactly the
            literal `"missing_selected_side"`).
    (3) Build the `output` dict per §4.4: on `outcome=="accepted"`,
        REFERENCE-COPY the SEVEN slots from §4.2 (`selected_side`,
        `selected_token_id`, `submitted_limit_price`,
        `accepted_limit_price`, `limit_order_token_qty`,
        `fusion_direction`, `fusion_confidence`) into `output`.
        Four of the seven are Decimal-as-str numerics
        (`submitted_limit_price`, `accepted_limit_price`,
        `limit_order_token_qty`, `fusion_confidence`); three are
        bare-string identifiers (`selected_side`,
        `selected_token_id`, `fusion_direction`). NO re-coercion;
        TC83(h) asserts byte-equality of each duplicated value
        against its source slot. On `outcome!="accepted"`,
        `output = {"_unobservable": true, "reason":
        "final_decision_not_accepted"}` per §4.5.
    (4) APPEND the `final_decision` entry to `gates[]`. After this
        step, `gates[-1].name == "final_decision"` is the
        UNIVERSAL post-append invariant TC83(a) checks — held on
        every record, FMC fallback or not (v16 redesign).
    (4.5) `[APPROVED-FALLBACK-FMC observability — v16]` If step
        (1) had captured a `field_map_copy_failure`, populate the
        TOP-LEVEL `record.recorder_internal_failure` field per
        §4.2 with `{"exception_type":
        type(field_map_copy_failure).__name__, "exception_str":
        str(field_map_copy_failure), "step": "field_map_copy"}`.
        DO NOT append to `gates[]` (v15's trailing-row design is
        retracted). Otherwise (step 1 succeeded), leave the field
        `null`. The universal-trailing invariant `gates[-1].name
        == "final_decision"` is RESTORED for every record. The
        observability guarantee (user-conditional approval) is
        met via the top-level field's non-null status + the
        exception type+str fields.
    (5) ENCODE the record in memory; ACQUIRE the §4.1 append lock;
        `os.write` the line in ONE call; `os.fsync`. If `os.write`
        raises, jump to the "write failure" sub-path below.
    (6) If step (1) had captured a `field_map_copy_failure`, raise
        it now (the record HAS been written with the top-level
        `recorder_internal_failure` field populated per step (4.5);
        the implementer sees the original failure during testing
        and in the durable decisions.jsonl line via
        `DecisionRecord.__exit__`'s outer handler). Otherwise
        return normally.
  - Exception escape (`__exit__` invoked with `exc_type != None`) →
    (1) `[APPROVED-FALLBACK-FMC]` Capture the in-flight exception
        as `body_exc`. Walk FIELD_MAP.MAPPED and copy populated
        `rec.fields[k]` to raw paths (try/except — capture failure
        as `field_map_copy_failure` and continue; mark unset
        fields with `Unobservable.exception_before_set`).
    (2) For any observable-required raw field STILL unset,
        substitute `Unobservable.exception_before_set`.
    (3) If `body_exc` was raised INSIDE a specific `gate_scope("G")`
        block (resolvable via `self._scoped_gate_on_exception` — the
        single-slot field populated by gate_scope's exception-path
        exit per Alpha-1's defer-pop discipline; the `_gate_scope_stack`
        is the NORMAL-path stack and is deliberately NOT used here
        because Python's @contextmanager unwinding semantics would
        pop the innermost scope before the recorder's __exit__ runs),
        the recorder synthesizes-or-
        updates the gate's row: if no `gates[]` entry for `G`
        exists yet (typical: gate_scope wraps the gate body and
        the exception aborted before any `.reject()` fired), APPEND
        a synthesized entry `{name: "G", passed: false, reason:
        "gate_exception_G", inputs: {"_unobservable": true,
        "reason": "gate_exception_G"}, output: {"_unobservable":
        true, "reason": "gate_exception_G"}}`. If an entry for `G`
        already exists (e.g., `gate_scope` writes a `passed=true`
        row on successful exit and the exception fired AFTER that
        write — possible if the gate body succeeded but an
        unrelated exception was in flight from a nested scope),
        update its `inputs` and `output` fields to the same
        `_unobservable` sentinels. Remember `G` as `scoped_gate`.
        Otherwise (`body_exc` not inside any `gate_scope`):
        `scoped_gate = None`.
    (4) APPEND a `gates` entry with `name="exception"`,
        `reason=f"{type(body_exc).__name__}: {body_exc}"`,
        `inputs={"exception_type": type(body_exc).__name__,
        "exception_str": str(body_exc)}`.
    (5) Compute `final_decision`:
          - `outcome = "exception"`.
          - `failing_gate`:
              * if `scoped_gate` is not None: `failing_gate =
                scoped_gate` (the gate-name string).
              * elif `gates_pre_len == 0` (no gate ever fired
                before the exception; only the step-(4)
                `"exception"` row exists pre-this-step): `failing_gate
                = {"_unobservable": true, "reason":
                "no_gate_fired_before_exit"}` (per §4.5 enum;
                uses standard §4.5 dict shape — NOT a separate
                `_unobservable_reason` field).
              * else: `failing_gate = null`.
          - `reason = "exception"`.
          - `output = {"_unobservable": true, "reason":
            "final_decision_not_accepted"}`.
        APPEND the `final_decision` entry; `gates[-1].name ==
        "final_decision"` post-append, UNIVERSALLY (v16 redesign;
        no FMC carve-out). TC83 enforces.
    (5.5) `[APPROVED-FALLBACK-FMC observability — v16]` Mirror of
        normal-exit step (4.5): if step (1) had captured a
        `field_map_copy_failure`, populate the TOP-LEVEL
        `record.recorder_internal_failure` field per §4.2 with
        `{"exception_type":
        type(field_map_copy_failure).__name__, "exception_str":
        str(field_map_copy_failure), "step": "field_map_copy"}`.
        DO NOT append to `gates[]` (v15's trailing-row design is
        retracted). The exception-exit path's existing step (4)
        body-exception row at `gates[-2]` (post step-5 append) is
        the only "exception"-named row on this path; the FMC
        failure is reported via the top-level field. TC83(j)
        asserts the top-level field is populated AND the gates[]
        invariant holds.
    (6) ENCODE the record; ACQUIRE the append lock; `os.write` the
        line in ONE call; `os.fsync`. On `os.write` failure, jump
        to the "write failure" sub-path below — and CRUCIALLY,
        the re-raised exception must be `body_exc`, not the write
        failure (so `DecisionRecord.__exit__`'s outer handler sees
        the original body exception in its `exc_val`).
    (7) Re-raise `body_exc`. If step (1) captured a
        `field_map_copy_failure`, attach it via
        `body_exc.__context__ = field_map_copy_failure`. Recorder's
        own validation errors MUST NOT substitute for `body_exc`.
  - Write failure sub-path (applies to both normal-exit step (5) and
    exception-exit step (6)):
    (W1) Write a single line to `raw_decisions_skipped.jsonl` with
         `{decision_id, bot_mode, captured_at, failure_type,
         failure_message, in_flight_body_exception_type}` (the
         last field is `null` on normal-exit, the
         `type(body_exc).__name__` on exception-exit). If THIS
         write raises, capture as `skipped_write_exc` and
         continue.
    (W2) Choose the exception to re-raise:
           - On exception-exit: re-raise `body_exc` with
             `body_exc.__context__ = write_exc` and (if applicable)
             `body_exc.__cause__ = skipped_write_exc`. This
             PRESERVES the original body exception type in
             `DecisionRecord.__exit__`'s view (per
             `decision_log.py:175-193`'s `rejection_reason =
             f"{exc_type.__name__}: {exc_val}"` interpolation),
             fixing the v13-pre-fix corruption where a disk-full
             event would have replaced the body exception with
             `OSError`.
           - On normal-exit: re-raise `write_exc` with
             `write_exc.__cause__ = skipped_write_exc` if
             applicable (no body exception in flight to preserve).
    (W3) `DecisionRecord.__exit__` runs (outer); on
         exception-exit, it sees the original body exception type
         in `exc_val`. On normal-exit, it sees the `write_exc`.
         `decisions.jsonl` is durable.
  - TC47 asserts step (2) populates `exception_before_set` on
    truly unset fields; TC47b asserts step (3) populates
    `gate_exception_<gate.name>` on the gate scoped to the
    exception and step (4) appends a `name="exception"` row at
    post-append `gates[-2]` (with `gates[-1]` being the
    universal `final_decision` row per §4.4); TC65 asserts `raw_decisions_skipped.jsonl`
    is written before re-raise; TC65b (new in v14) asserts that on
    combined `(body exception, disk full)`, the exception re-raised
    out of `__exit__` is `body_exc`-typed (NOT `OSError`-typed)
    so `decisions.jsonl` records the body exception's type and
    string, not the write failure's.
- **Gamma-5** — `bot_mode` enum strictness: recorder constructor
  raises if `bot_mode not in {"live_gate", "shadow_policy",
  "simulation"}`. Because Gamma-1 places the open AFTER mode
  resolution, only the three legal values reach it. TC32 + TC32a
  enforce: TC32 asserts the constructor raises on `"mode_check_pending"`,
  `"snapshot_capture"`, `""`, `None`, etc.; TC32a asserts the static
  AST test (TC67) catches any new `RawDecisionSnapshotRecorder(`
  call site.
- **Gamma-6** — Required `decision_id` at every DecisionRecord site:
  TC68 walks every `DecisionRecord(` constructor call in `bot.py`
  and asserts `decision_id=...` is always passed. Already true today;
  the test prevents regression.

Verification (indexing convention: all `gates[-N]` indices in this
section are POST-APPEND per §4.4's canonical convention; RP13
cluster token):

- Existing test suite passes.
- For each `bot_mode`, synthetic decision body call produces exactly
  one raw line with the expected `bot_mode`.
- Diff `decisions.jsonl` line written for fixed fixture
  before/after: empty (TC06).
- Diff order envelope to exchange mock: empty (TC07).
- Every reject site in §4.4 maps to a raw line whose `gates` ends
  with the trailing `final_decision` entry whose
  `inputs.failing_gate` equals the rejecting gate's name, and whose
  `gates[-2]` is the rejecting gate. TC83 enforces.
- Exception inside body → one raw line with `gates` containing an
  `exception` entry followed by a `final_decision` entry as the
  LAST element with `inputs.outcome="exception"`. TC83 enforces.
- Accept path → one raw line with `gates[-1].name=="final_decision"`,
  `gates[-1].passed==true`, `gates[-1].output` populated with the
  inline depth_replay+fusion duplicate fields per §4.4.
- Each of the three NOT-WIRED sites → zero raw lines, exactly one
  decisions.jsonl line.
- Recorder makes zero new network calls (strict-mock).
- FIELD_MAP covers every kwarg key of every
  `rec.update(**kwargs)` call in the body (TC66).

Validation:

- G10 join-completeness over fixture corpus: every body decision_id
  appears in both files exactly once; every non-body decision_id
  appears only in `decisions.jsonl`. Decision IDs in
  `raw_decisions_skipped.jsonl` are exempt from the raw-corpus side
  of the invariant.

Test coverage adds: TC02, TC02a, TC03, TC04, TC05, TC06, TC07, TC15a,
TC29 (TC29a/b/c), TC30, TC31, TC32, TC32a, TC33, TC38, TC39, TC47,
TC55, TC58, TC62, TC65, TC65b, TC66, TC67, TC68, TC83.

Regression prevention adds:

- Golden-record fixture under `tests/fixtures/raw_corpus/synthetic/`.
- Import-graph check: no `core/`, `data_sources/`, `execution/`
  module imports `raw_decision_snapshot` (only `bot.py`).

Per-phase 3-reviewer gate per §10.

### 6.4 Phase Delta — Offline Loader and Schema Validator

Aim: canonical loader, validator, sidecar resolver, sub_interval
recomputation check, truncated-tail tolerance.

Work items:

- **Delta-1** — `analysis/raw_snapshot_loader.py` (≤500 lines).
  Public API: `iter_records(corpus_dir) -> Iterator`, `validate_record`.
- **Delta-2** — Sidecar resolver builds in-memory sha256 index;
  resolves `_body_ref` on load.
- **Delta-3** — §4.6 forward compatibility.
- **Delta-4** — §4.7 sub_interval recomputation check.
- **Delta-5** — §5.6 truncated trailing-line tolerance.
- **Delta-6** — CLI:
  `python -m analysis.raw_snapshot_loader --validate <dir>`.
- **Delta-7** — Universal corpus invariants (v16 redesigned;
  prior v14/v15 FMC-fallback compound layouts retracted under
  the §4.2 `recorder_internal_failure` top-level field design).
  For EVERY record (no FMC carve-out; no schema_version gating
  — Option A keeps v1 as the sole live version), the validator
  asserts:
    (a) `len(record.gates) >= 1` AND
        `record.gates[-1].name == "final_decision"`. UNIVERSAL —
        no exception layout, no FMC layout. Failure is P0.
    (b) `record.gates[-1].inputs.outcome ∈ {"accepted", "rejected",
        "exception"}`.
    (c) Exactly ONE entry in `record.gates` has
        `name == "final_decision"` (catches double-append bugs).
    (d) On `outcome == "accepted"`:
        `record.gates[-1].output` is a dict containing the seven
        required keys per §4.4 (`selected_side, selected_token_id,
        submitted_limit_price, accepted_limit_price,
        limit_order_token_qty, fusion_direction, fusion_confidence`)
        AND each value byte-equals the corresponding §4.2 source
        slot (the byte-equal check uses string comparison since
        every numeric is Decimal-as-str).
    (e) On `outcome ∈ {"rejected", "exception"}`:
        `record.gates[-1].output == {"_unobservable": true,
        "reason": "final_decision_not_accepted"}` (NOT null).
    (f) On `outcome == "rejected"`:
        `record.gates[-1].inputs.failing_gate ==
        record.gates[-2].name` AND
        `record.gates[-2].passed == false`.
    (g) On `outcome == "exception"`: EXACTLY ONE
        `record.gates[i]` has `name == "exception"`, located at
        post-append `i == -2` (immediately before `final_decision`).
        Gamma-4 exception step (4) unconditionally appends this
        row, and the synthesize-or-update step (3) — when it
        fires — operates on a DIFFERENT row (the
        gate_scope-attributed gate row, with `_unobservable:
        gate_exception_G`, located at `gates[-3]` post-append).
        Therefore there is no "zero exception row" branch; the
        v15 phrasing "potentially zero" is RETRACTED. If
        `record.gates[-1].inputs.failing_gate` is non-null AND
        is a string, it names a §4.4 gate (the scoped gate from
        Gamma-4a); if it is a dict, it matches the standard
        §4.5 sentinel shape `{"_unobservable": true, "reason":
        "no_gate_fired_before_exit"}`.
    (h) Top-level `record.recorder_internal_failure` (v16): if
        non-null, it is a dict with required string-typed keys
        `"exception_type"`, `"exception_str"`, `"step"`. Allowed
        `step` values: closed enum `{"field_map_copy"}` (extensible
        per future amendments). On the happy path, the field is
        `null` (NOT absent — the recorder writes `null` explicitly
        per Decimal-as-str / JSON serializer rules in §5.5).
    (i) Cross-corpus FMC consistency invariant (NORMAL-EXIT
        scope only — v20 fix to v19's over-reach). When
        `record.recorder_internal_failure is not None` AND
        `record.gates[-1].inputs.outcome ∈ {"accepted", "rejected"}`
        (i.e., the FMC fallback fired on a normal-exit path
        per Gamma-4 step (6), which re-raises
        `field_map_copy_failure` directly), the corresponding
        `decisions.jsonl` line for the same `decision_id` MUST
        have `rejected_at_gate == "exception"` AND its
        `rejection_reason` string MUST begin with the same
        `exception_type` substring as
        `record.recorder_internal_failure["exception_type"]`.
        Rationale: on the normal-exit FMC path,
        `DecisionRecord.__exit__`'s outer handler sees
        `exc_val == field_map_copy_failure` (step (6) raised
        it directly) and formats `rejection_reason =
        f"{type(field_map_copy_failure).__name__}: ..."`. The
        substring match is therefore exact.
        EXCEPTION-EXIT FMC (Gamma-4 step (7)) is OUT OF SCOPE
        for this invariant — on that path, step (7) re-raises
        `body_exc` (not the FMC), so the outer handler's
        `rejection_reason` begins with `type(body_exc).__name__`
        instead. TC65b already covers the body_exc preservation
        on that branch. The two-corpus consistency on
        exception-exit FMC is established differently: the
        raw record's `gates[-2].name == "exception"` row
        carries `body_exc`'s type+str in its reason, matching
        the decisions.jsonl line; `recorder_internal_failure`
        carries the FMC failure orthogonally. No silent
        disagreement.
        TC84(i) (new in v20) exercises an FMC-on-accept fixture
        (the round-16 P2 scope) and asserts the two-corpus
        pair agreement. Failure is P0 per §4.6 rule (d),
        classified as a join-completeness violation alongside
        G10's existing `raw_decisions_skipped.jsonl` mechanism.
  Failure of (a)–(i) on any record is P0 per §4.6 rule (d).
  TC84 exercises each (a)–(i) branch with a fixture (TC84(i)
  is the cross-corpus pair test described in (i) above; the
  other sub-cases (a)-(h) cover single-corpus invariants on
  the raw record alone).

Verification:

- Round-trip: writer → loader → writer byte-equal.
- Validator rejects each documented failure mode (one test per).
- Sidecar resolver reconstructs records.

Validation:

- Loader reads 24h simulation corpus from Phase Gamma in CI with zero
  invalid records.

Test coverage adds: TC15, TC16, TC17, TC35, TC37, TC48, TC49, TC84.

Regression prevention adds:

- Validator wired into CI smoke test on every PR touching recorder /
  snapshot / context / bot.py / strategy_brain / data_sources used
  by `decision_context.py`.

Per-phase 3-reviewer gate per §10.

### 6.5 Phase Epsilon — Resolution Joiner

Aim: persist deterministic market-resolution snapshots; reuse Alpha-4
shared Gamma helpers (per-caller policy stays).

Work items:

- **Epsilon-1** — `analysis/resolution_joiner.py` (≤500 lines). CLI:
  `python -m analysis.resolution_joiner --corpus <dir> --out <path>`.
- **Epsilon-2** — Iterates raw corpus, collects distinct
  `condition_id`s, fetches Gamma via `analysis.gamma_resolution`
  helpers (with `closed-only` policy for the joiner — this is a new
  caller of the shared helpers; doesn't change existing callers'
  policies). Appends one resolution line per `(condition_id,
  fetched_at)`. Idempotent re-runs.
- **Epsilon-3** — NEVER edits raw corpus lines.

Verification + Validation + Test coverage: TC18, TC19, TC36, TC50.

Regression prevention: joiner is import-isolated from `bot.py`.

Per-phase 3-reviewer gate per §10.

### 6.6 Phase Zeta — Policy Replayer

Aim: deterministic offline replayer reconstructing the verdict tuple
from a raw record and a config override.

Work items:

- **Zeta-1** — `analysis/policy_replayer.py` (≤500 lines). Public API:
  `replay(record, config_override) -> ReplayResult`.
- **Zeta-2** — Each replay constructs FRESH processor instances and a
  FRESH fusion engine (NOT `get_fusion_engine()` singleton — which
  is deleted in Beta-5 anyway), seeded with override config and
  pre-state. `SignalFusionEngine(weights=override_weights,
  recency_window_seconds=...)` uses Beta-5 constructor args.
  Replayer never branches on `bot_mode` and never imports IO modules.
  - **`name=` slug source for processor construction.** Beta-3
    makes `name: str` a REQUIRED kwarg on every processor subclass
    `__init__` (TypeError without it). Beta-3 uses the EXISTING
    class-default PascalCase slug for BOTH live and shadow
    instances (no `__shadow` suffix — see Beta-3 rationale: the
    fusion weight dict is keyed by the base slug and a suffix
    would break `signal_fusion.py:95` lookup). The replayer
    derives `name=` from a closed
    `PROCESSOR_NAME_BY_CLASS: dict[type, str]` enumeration in
    `analysis/policy_replayer.py`:
    - `SpikeDetectionProcessor` → `"SpikeDetection"`
    - `SentimentProcessor` → `"SentimentAnalysis"`
    - `PriceDivergenceProcessor` → `"PriceDivergence"`
    - `OrderBookImbalanceProcessor` → `"OrderBookImbalance"`
    - `TickVelocityProcessor` → `"TickVelocity"`
    - `DeribitPCRProcessor` → `"DeribitPCR"`
    The slug is independent of `bot_mode` (same for live and
    shadow). Defensive cross-check: the replayer asserts that for
    every captured `signals[]` entry, the
    `signal_id.split(":")[1]` equals the slug it just chose;
    mismatch raises (catches future drift between bot startup
    slug-assignment and replayer slug-derivation).
- **Zeta-3** — `process()` called with `now=record.decision_reference_time`.
  `fuse_signals(now=record.decision_reference_time, ...)`. DeribitPCR's
  `pcr_data_override` set from the record. PriceDivergence's
  `_spot_history` and `_spot_history_ts` pre-seeded from
  `record.coinbase_spot_history_before_process` in insertion order.
- **Zeta-4** — Risk engine constructed with recorded `risk_engine_state`
  injected via the Beta-8 `state_override` kwarg.
- **Zeta-5** — Parity mode:
  `python -m analysis.policy_replayer --corpus <dir> --parity --out <path>`.
  Mismatches emitted as structured diffs. Per G3 + §13, every
  mismatch on an in-scope record is either resolved or annotated
  with `APPROVED_PARITY_DEVIATION` before sign-off.
- **Zeta-6** — Exception-final-gate records excluded from parity
  denominator; reported in `exception_records` column.
- **Zeta-7** — `final_decision` row treatment under parity.
  Parity scope is FIXED to the verdict tuple
  `(decided_direction, rejected_at_gate, rejection_reason)`
  reconstructed from the replay's own gate sequence. The replayer
  does NOT reconstruct the trailing `final_decision` row and does
  NOT include `gates[-1]` (the recorded `final_decision`) in the
  parity diff (gates[-1] is a recorder-side artifact, not a replay
  output). The replayer's reconstruction maps:
    - `decided_direction` ← from the replay's own
      `depth_aware_entry` result (long if `selected_side=="yes"`,
      short if `selected_side=="no"`, null if rejected).
    - `rejected_at_gate` ← from the replay's own `gates_pre_append`
      sequence: FIRST gate where `passed==false`; null on accept.
    - `rejection_reason` ← that gate's `reason` string; null on
      accept.
  The recorder's `final_decision.inputs.failing_gate` is compared
  against the replay's reconstructed `rejected_at_gate` as a
  parity-tuple member; mismatch contributes to the G3 ≥99.9%
  metric. The recorder's `final_decision.reason` is compared
  against the reconstructed `rejection_reason`. The recorder's
  `final_decision.output.fusion_direction` /
  `final_decision.output.fusion_confidence` are compared against
  the replay's own `fused_signal.direction` /
  `fused_signal.confidence` (byte-equal on Decimal-as-str
  representation per the §6.7 Eta-5 determinism rules; TC23a
  asserts).
  TC21 verifies: corpus parity SLO ≥99.9% on in-scope records
  using the explicit FIVE-tuple comparison
  `(decided_direction, rejected_at_gate, rejection_reason,
  fusion_direction, fusion_confidence)`. Each element is a
  separate equality check; record counts as "parity match" iff
  all five elements byte-equal on Decimal-as-str or string
  comparison.
  TC22 (replayer byte-equal re-run) is updated to compare the
  replayer's output (the parity diff CSV), NOT the recorded
  corpus — so the `final_decision` row's presence in the recorded
  corpus does not affect TC22.

Verification:

- Parity over Phase Gamma 24h simulation corpus ≥ 99.9% on in-scope
  records (SLO); 100% required at sign-off (gate).
- Replayer is pure: re-running yields byte-equal output.
- Replayer zero network calls; import-graph forbids IO modules.

Validation:

- Sweeping `MIN_SIGNAL_CONFIDENCE` higher than every recorded
  `fused_confidence` produces all-rejects.
- Sweeping `fusion_min_signals=0` produces never-rejects on fusion.

Test coverage adds: TC20, TC21, TC22, TC23, TC51.

Regression prevention adds:

- Nightly operator-triggered parity job over the most recent 7 days
  of corpus.

Per-phase 3-reviewer gate per §10.

### 6.7 Phase Eta — Brute-Force Harness

Aim: declarative sweep, joined to resolution, deterministic CSV.

Work items:

- **Eta-1** — `analysis/brute_force_harness.py` (≤500 lines). CLI:
  `python -m analysis.brute_force_harness --corpus <dir>
  --resolutions <path> --grid <yaml> --out <csv>`.
- **Eta-2** — Declarative grid expansion.
- **Eta-3** — Per override compute: candidate count, accepted count,
  win/loss/undecided joined by `condition_id`, plus per-window AND
  per-`bot_mode` breakouts. Shadow records short-circuit at the EV
  gate (bot.py:5508-5514) and never reach risk_engine/liquidity_floor/
  executor_returned_false — so risk-engine aggregates EXCLUDE shadow
  records (a `bot_mode_scope` column on each row documents the
  inclusion criterion).
  **Accept classifier — pinned source of truth.** (Indexing-
  convention note for this cluster, satisfying RP13: every
  `r.gates[-N]` reference below is POST-APPEND per §4.4.) The
  harness MUST use the replayer's reconstructed verdict tuple
  (`rejected_at_gate is None`, equivalent to `outcome=="accepted"`
  per Zeta-7) to classify each (record, override) pair as accepted
  or rejected. The recorded `final_decision.passed` is NOT used by
  the harness because under an override the recorded outcome may
  no longer hold (the whole point of the sweep). The recorded
  `final_decision.passed` IS used by TC25 (aggregate parity at
  recorded config) to verify that at the no-override configuration
  the harness's accept count equals
  `sum(1 for r in corpus if r.gates[-1].passed == True)` —
  asserting one-row lookup against the recorded truth. TC74 (the
  liquidity-floor sweep verdict-change test) similarly compares
  the replayer's reconstructed verdict to the recorded
  `final_decision.inputs.outcome` to ensure the sweep actually
  flips at least one record's classification.
  **Exception-records handling.** Records where the REPLAY produces
  `outcome=="exception"` (or where Zeta-6 excluded the record from
  the parity denominator) are EXCLUDED from accept/reject
  aggregates and counted in a separate `policy_replay_exception_records`
  column on each grid row (mirrors Zeta-6's scope discipline). The
  recorded `final_decision.inputs.outcome=="exception"` (a property
  of the captured run, not the replay) does NOT exclude the record
  from harness aggregation — under an override, the same input
  state may NOT raise an exception in the replay path, so the
  record can still contribute. TC52b (new in v15) asserts.
- **Eta-4** — Banner per §9 emitted as FIRST row of CSV (literal text),
  as a comment line, and via `--help`. Every numeric column uses
  `policy_replay_` or `hypothetical_decision_` prefix. No bare `pnl`,
  `profit`, `return`, `alpha`, `roi`, `edge`. TC27b enforces.
- **Eta-5** — Determinism rules:
  - Numeric fields via `_csv_format_number()` Decimal helper (no
    float `repr()`).
  - Override-dict keys lex-sorted before flattening.
  - CSV row order = `sorted(rows, key=(override_key_tuple,
    decision_id))`.
  - Python version pinned in operations doc.
  - File line order in the CORPUS is capture-time order (preserved
    by Phase Delta loader). The HARNESS re-sorts as above for
    deterministic output. TC63 + TC69 enforce both invariants.
- **Eta-6** — `deribit_cache_seconds` sweep guards:
  - When new `cache_seconds < record.deribit_pcr.cache_age_seconds`
    AND record's `used_cached_result=True`: exclude with
    `sweep_excluded_reason="deribit_refetch_required"`.
  - When new `cache_seconds > record.deribit_pcr.cache_age_seconds`
    AND record's `used_cached_result=False`: exclude with
    `sweep_excluded_reason="deribit_cache_extension_insufficient_history"`
    (the longer cache would have kept a prior entry the live process
    discarded; the record lacks the bytes to recompute that branch).
  - Both excluded from win/loss aggregates.

Verification:

- Re-runs byte-equal CSV.
- Single-override grid equal to recorded config: harness accepted-
  count == recorded.
- 2×3 grid → 6 rows with per-window + per-bot_mode breakouts.

Validation:

- Reviewer manually verifies a small grid output before sign-off.

Test coverage adds: TC24, TC25, TC26, TC27, TC27b, TC52, TC69.

Regression prevention adds:

- Property-based test: dominated override appears below dominator.

Per-phase 3-reviewer gate per §10.

### 6.8 Phase Theta — Operator Documentation, Deploy Templates, Capacity Planning

Work items:

- **Theta-1** — New `docs/RAW_DECISION_SNAPSHOT_OPERATIONS.md`
  (≤500 lines): enabling, disabling, expected disk usage (§5.8),
  no-kill-switch rationale (G7), sample replay / harness commands,
  live-equivalence boundary (§9), vendor ToS table (§3.B), pinned
  Python version, `POLYBOT_REQUIREMENTS_LOCK_PATH` env documentation,
  `POLYBOT_GIT_SHA` startup-wrapper recipe for git deploys,
  `POLYBOT_ACKNOWLEDGE_UTC_DAILY_RESET=1` requirement with deploy-
  coordination note pointing at G6 bullet (b) and the local→UTC
  daily-reset boundary shift. Every env var in §12 MUST appear in
  this doc.
- **Theta-2** — Update `deploy/polybot.logrotate` to include
  `${RAW_DECISION_SNAPSHOT_DIR}/**/*.jsonl` with `daily compress
  rotate 30`.
- **Theta-3** — Update `deploy/polybot-ledger-backup.cron` to add a
  daily `aws s3 cp` of the previous day's corpus directory OR
  document the explicit decision NOT to back up.
- **Theta-4** — EDIT (not create) the existing
  `deploy/polybot.service` systemd unit file (1991 bytes, 58 lines;
  verified present alongside README.md, multi_asset_evaluation_template.md,
  polybot-ledger-backup.cron, polybot.logrotate). Specific edits:
  - **Extend `ReadWritePaths`** at line 51 from
    `ReadWritePaths=/opt/polybot/ledger /opt/polybot/logs /run/systemd/ask-password`
    to
    `ReadWritePaths=/opt/polybot/ledger /opt/polybot/raw_decision_snapshots /opt/polybot/logs /run/systemd/ask-password`
    so `${RAW_DECISION_SNAPSHOT_DIR}` (recommended
    `/opt/polybot/raw_decision_snapshots/` — sibling NOT child of
    `/opt/polybot/ledger/`, on a SEPARATE filesystem per Theta-7)
    can be written.
  - **Add `Environment=` lines** after line 31, one per env var
    promoted by Beta-5/Beta-7/Beta-8, including:
    `POLYBOT_ACKNOWLEDGE_UTC_DAILY_RESET=1`,
    `POLYBOT_ACKNOWLEDGE_RISK_ENGINE_ENV_REQUIRED=1`,
    `MAX_POSITION_SIZE=<operator-set>`, `MAX_TOTAL_EXPOSURE=...`,
    `MAX_POSITIONS=...`, `MAX_DRAWDOWN_PCT=...`,
    `MAX_LOSS_PER_DAY=...`, `BALANCE_SAFETY_BUFFER_USD=...`,
    `TREND_UP_THRESHOLD=...`, `TREND_DOWN_THRESHOLD=...`,
    `LIQUIDITY_FLOOR=...`, `FUSION_MIN_SIGNALS=...`,
    `FUSION_MIN_SCORE=...`, `FUSION_RECENCY_WINDOW_SECONDS=...`,
    `DIVERGENCE_SPOT_HISTORY_MAX_LEN=...`,
    `TICK_VELOCITY_TOLERANCE_SECONDS=...`,
    `POLYBOT_GIT_SHA=...` (if capture is enabled and the bot
    isn't in a git checkout), plus the capture opt-in envs
    when enabled.
  - **PRESERVE existing content unchanged**: the
    credentials-vault `systemd-ask-password | runuser` ExecStart
    at line 33, the `Restart=no` rationale block at lines 11-15,
    the existing `Environment=LIVE_TRADE_LEDGER_PATH=...`,
    `Environment=NAUTILUS_LOG_DIR=...`, `Environment=DECISION_LOG_PATH=...`
    at lines 29-31, the `WorkingDirectory`, the security
    hardening directives, and `[Install]` section.
  The §12 Beta-8 audit clause (which references the existing
  `deploy/polybot.service`) is now well-founded: the file exists
  and is auditable before Beta-8 lands.
  At startup, IF AND ONLY IF env
  `POLYBOT_REQUIRE_SEPARATE_RAW_CORPUS_FILESYSTEM=1` is set, the
  recorder `stat`s both `RAW_DECISION_SNAPSHOT_DIR` and
  `LIVE_TRADE_LEDGER_PATH.parent` and raises if they share the
  same `st_dev`. The env gate exists so dev/CI environments where
  both paths legitimately share `st_dev` (e.g., laptop with the
  project_root + `/tmp` both on `/`) are not blocked. Production
  deploys MUST set the env per the operations doc. TC75 verifies
  both branches: with env=1 + shared st_dev → raises; with env
  unset → no st_dev check is performed.
- **Theta-5** — Append a section to `docs/DATA_COLLECTION_INVENTORY.md`
  pointing at this plan and noting new files / env vars / corpus
  directory.
- **Theta-6** — README.md cross-link.
- **Theta-7 — Capacity planning.** Document in
  `RAW_DECISION_SNAPSHOT_OPERATIONS.md`:
  - The capture volume MUST be a separate filesystem from the live
    ledger to ensure a capture-disk-full event cannot corrupt the
    ledger.
  - Minimum free-space alerting at 50% (warn) and 80% (page) of
    projected daily consumption × retention window (per §5.8).
  - Sizing recipe: project daily usage at each opt-in env's enabled
    state; size the volume at 3× the 30-day projection.
  - Monitoring tie-in via the existing Grafana dashboard
    (`grafana/dashboard.json`) — add a free-space panel (deferred to
    a follow-up plan; documented as such).
  - The G7 no-kill-switch rationale: a soft fail-open kill switch
    would be a fallback under M4. The capacity planning makes the
    fail-stop scenario operationally rare; if it occurs, operator
    runbook is to extend the volume, restart the bot, and resume.
  - **Grafana panel timestamp shift — defensive callout only
    (Beta-8).** After Beta-8 lands, the `timestamp` field in
    `get_risk_summary()`'s return dict becomes UTC-aware. The
    CURRENT Grafana consumer at `monitoring/grafana_exporter.py:336`
    reads `risk_summary` and ONLY uses
    `risk_summary['exposure']['utilization_pct']` (lines 339-341);
    the `timestamp` key is NOT surfaced into any Grafana panel
    today. NO existing panel is affected. Documentation is
    DEFENSIVE: any future panel JSON or query that adds
    `risk.timestamp` (e.g., from `exec_stats['risk']['timestamp']`)
    will see the UTC-aware value rather than the prior local-TZ-
    naïve one.

Verification:

- Every env var documented with required/optional, default, sample.
- docs-link checker over new docs.

Validation:

- Run documented commands cold from a clean clone against fixture
  corpus.

Test coverage adds: TC28, TC53, TC54.

Per-phase 3-reviewer gate per §10.

## 7. Brute-Force Parameter Grid

### 7.1 Inventory Walk

Every tunable in `docs/STRATEGY_ALGORITHM_INVENTORY.md` and every
"must-store effective config" parameter is classified below.

**Swept** (harness sweeps; replayer wires through Beta-2/4/5/6/7):

```text
trade_window_label
trend_up_threshold
trend_down_threshold
fusion_min_signals
fusion_min_score
fusion_weights_by_source
MIN_SIGNAL_CONFIDENCE
EV_FEE_BUFFER
EV_SPREAD_BUFFER
LIMIT_REQUIRED_EDGE
QUOTE_STABILITY_REQUIRED            (via record.frozen_quotes.stable_tick_count)
MAX_ACCOUNT_STATE_AGE_SECONDS       (via recorded age field)
MAX_DECISION_SNAPSHOT_AGE_SECONDS   (via recorded age field)
liquidity_floor
POLYMARKET_LIMIT_MIN_TOKENS
REQUIRE_SIGNAL_CONFIRMATION         (binary)
spike_threshold
spike_lookback_periods              (partial: MA-deviation sub-mode
                                     only; velocity sub-mode uses
                                     fixed [-3] — documented in --help)
spike_min_confidence
spike_velocity_threshold
sentiment_extreme_fear_threshold
sentiment_extreme_greed_threshold
sentiment_min_confidence
divergence_min_confidence
divergence_momentum_threshold
divergence_extreme_prob_threshold
divergence_low_prob_threshold
divergence_spot_history_max_len     (constructor arg per Beta-4)
orderbook_imbalance_threshold
orderbook_wall_threshold
orderbook_min_book_volume
orderbook_min_confidence
orderbook_top_levels
tick_velocity_threshold_60s
tick_velocity_threshold_30s
tick_velocity_min_ticks
tick_velocity_min_confidence
tick_velocity_tolerance_seconds     (constructor arg per Beta-4)
deribit_bullish_pcr_threshold
deribit_bearish_pcr_threshold
deribit_max_days_to_expiry          (recomputed via _parse_dte(now=fetched_at))
deribit_min_open_interest
deribit_cache_seconds               (guarded per Eta-6)
deribit_min_confidence
MAX_POSITION_SIZE                   (risk_engine gate; shadow records excluded per §6.7)
MAX_TOTAL_EXPOSURE                  (risk_engine gate; shadow excluded)
MAX_POSITIONS                       (risk_engine gate; shadow excluded)
MAX_DRAWDOWN_PCT                    (risk_engine gate; shadow excluded)
MAX_LOSS_PER_DAY                    (risk_engine gate; shadow excluded)
BALANCE_SAFETY_BUFFER_USD           (balance_guard gate per §4.4)
LIVE_MIN_MARKET_BUY_USD             (position_size_below_minimum gate)
```

**Not swept (justified):**

```text
ORDER_TYPE                          affects live submission, not verdict.
                                    Sweeping requires a fill model
                                    (out of scope; §9).
LIMIT_IOC_FILL_POLICY               same.
MARKET_BUY_USD                      affects size only.
SIZING_MODE                         same.
PCT_OF_FREE_COLLATERAL_PER_TRADE    same.
fusion_recency_window_seconds       structurally inert in single-decision
                                    fusion: signal.timestamp ==
                                    decision_reference_time == fuse_signals
                                    now=, so (now - s.timestamp) == 0 for
                                    every in-decision signal. Sweeping any
                                    value > 0 cannot change the verdict
                                    given the current single-decision
                                    fusion scope. Extending the recorder
                                    to capture a cross-decision signal
                                    window for a meaningful sweep is
                                    deferred to a follow-up plan.
```

**Documented constants intentionally non-tunable** (sweeping requires
algorithm rewrite + new configurable surfaces, out of scope):

```text
spike velocity sub-mode hardcoded historical_prices[-3]
sentiment strength step boundaries (45/55 etc.)
sentiment confidence caps (0.85 etc.)
PCR strength cutoffs (1.40/1.60/0.55/0.45)
PCR confidence cap (0.90)
orderbook wall_bonus (0.05) and confidence cap (0.85)
fusion tie-break: bullish wins on ties (signal_fusion.py:129)
signal_score formula (strength_factor 0.5 + confidence 0.5)
```

Documented in harness `--help`.

## 8. Test Coverage Matrix

```text
ID     Property                                                                       Phase
TC01   Recorder writes exactly one line per body invocation                            Gamma
TC02   Recorder writes one line per §4.4 gate name with fixture                       Gamma
TC02a  Static check: every rec.reject() reason in bot.py has §4.4 mapping             Gamma
TC03   Recorder writes one line for an in-body exception                               Gamma
TC04   Recorder writes zero lines for the three Gamma-3 NOT-WIRED sites                Gamma
TC04a  Serializer raises on naïve datetime (M9)                                        Alpha
TC05   Recorder makes zero external network calls                                      Gamma
TC06   decisions.jsonl line byte-equal before/after Phase Beta + Gamma                 Beta,Gamma
TC07   Order envelope to exchange mock byte-equal before/after Phase Beta + Gamma      Beta,Gamma
TC08   Recorder rejects unknown gate name                                              Alpha
TC09   Recorder rejects null where Unobservable is the only allowed alternative       Alpha
TC10   Decimal precision preserved through writer/loader round-trip                    Alpha,Delta
TC11   Each processor's process() output unchanged after Phase Beta                    Beta
TC12   fuse_signals() output unchanged after Phase Beta                                Beta
TC13   effective_params() includes every §4.3 parameter                                Beta
TC14   Diagnostic methods have no IO imports                                           Beta
TC15   Loader yields one record per line                                               Delta
TC15a  G10 join-completeness invariant (raw ↔ decisions.jsonl, modulo skipped log)    Gamma,Delta
TC16   Validator catches every documented invalid-line mode                            Delta
TC17   Sidecar resolver reconstructs in-memory record                                  Delta
TC18   Joiner is append-only and idempotent                                            Epsilon
TC19   Joiner does not import bot.py                                                   Epsilon
TC20   Parity replay matches recorded verdict for fixture set                          Zeta
TC21   Parity replay over 24h staging corpus matches ≥ 99.9% on in-scope (SLO);       Zeta
       100% required at sign-off (gate)
TC22   Replayer is pure (re-run = byte-equal output)                                   Zeta
TC23   Every sweepable §7 parameter changes verdict on ≥ 1 fixture row                Zeta
TC23a  Every sweepable §7 parameter reachable via effective_decision_config           Beta
TC24   Harness re-run produces byte-equal CSV                                          Eta
TC25   Harness aggregate-parity matches recorded counts at recorded config             Eta
TC26   Harness per-window AND per-bot_mode breakouts present in CSV                    Eta
TC27   Harness emits banner per §9                                                     Eta,Theta
TC27b  Naming-theatre negative test (no bare pnl/profit/return/alpha/roi/edge)         Eta
TC28   Every internal doc link resolves                                                Theta
TC29   Concurrent shadow + live recording never interleaves bytes                      Gamma
TC29a  Two processes against same corpus dir: 2nd fails with lock-contention error     Gamma
TC29b  Recorder does not deadlock under bot.py's _signal_processing_lock —             Gamma
       spec: spawn 2 threads each calling _make_trading_decision_body with distinct
       decision_ids; with 5-second pytest timeout, both threads complete; assert
       lock acquisition order _signal_processing_lock → Recorder.lock at every
       site via instrumented mock (matching §4.1 "recorder.lock is BOTTOM" rule)
TC29c  Recorder lock is threading.Lock not RLock (reentrancy rejected)                 Gamma
TC30   Capture-path write failure surfaces; never swallowed                            Gamma
TC31   Pre-snapshot exception path produces zero raw lines (per G1 scope)              Gamma
TC32   bot_mode constructor rejects unknown values                                     Gamma
TC32a  AST test catches a new RawDecisionSnapshotRecorder( call site                   Gamma
TC33   STRATEGY_VERSION captured per record                                            Alpha,Gamma
TC34   Unobservable enumeration is closed (typos rejected, including                   Alpha
       gate_exception_<typo> suffix)
TC35   Sidecar format reversible; deterministic                                        Alpha,Delta
TC36   Resolution joiner records closed=false rows                                     Epsilon
TC37   Loader handles day-rotated file boundary                                        Delta
TC38   Recorder path resolution matches §5.1                                           Alpha,Gamma
TC39   Recorder file naming matches §5.2                                               Alpha,Gamma
TC40   No production module imports analysis/*                                         Delta,Epsilon,Zeta,Eta
TC41   STRATEGY_VERSION CI check: semantic core/strategy_brain change without bump     Alpha
       fails (comment / blank / type-annotation-only edits are exempt)
TC42   PriceDivergence replay with pre-state matches recorded signal                   Beta
TC43   DeribitPCR replay with pcr_data_override + _parse_dte(now) matches              Beta
TC44   Canonical-bytes hash collision policy (different key orders → same hash)        Alpha
TC45   Gamma resolver shared helpers parity vs prior two implementations               Alpha
TC46   risk_engine.state_snapshot writes only the reset trio (_stats_date,            Beta
       _daily_pnl, _daily_trades); no IO; no enqueue
TC47   Exception-path record has _unobservable: exception_before_set for unset fields  Gamma
TC48   Loader tolerates a truncated trailing line; earlier truncation raises           Delta
TC49   sub_interval recomputation mismatch is P0                                       Delta
TC50   Two resolution rows for one condition_id: harness picks latest closed           Epsilon,Eta
TC51   Replayer constructs fresh engine per call; no singleton state leak              Zeta
TC52   Eta-6 deribit_cache_seconds sweep guards (both directions)                      Eta
TC53   deploy logrotate template includes new path glob                                Theta
TC54   ops doc references logrotate + cron + systemd + capacity planning               Theta
TC55   Negative decision_snapshot_age_seconds raises (M10)                             Gamma
TC56   Static: no datetime.now() without timezone.utc in touched files                 Beta
TC56b  Static: no datetime.now(timezone.utc) as default-value expression in any        Beta
       new signature in touched files (M11)
TC56c  Runtime: process() / fuse_signals() / state_snapshot() / _parse_dte()           Beta
       raise TypeError when called without now=
TC57   Static: no env read inside _make_trading_decision_body after effective config   Beta
       dict is built
TC58   Recorder __exit__ runs INNER-FIRST: raw line attempt happens before             Gamma
       decisions.jsonl write; decisions.jsonl is durable even when raw write raises
TC59   §3.A pre-existing fallbacks: each is removed (test asserts the raise replaces   Beta
       the swallow) or carries the APPROVE THIS FALLBACK comment
TC60   Deribit raw_option_summaries_hash byte-equal across two cache-hit records       Beta
       (proves dedup; sidecar contains one line)
TC61   Deep-copy semantics: post-capture mutation of fusion_engine.weights or          Beta
       risk_engine.positions does not retroactively alter recorded values
TC62   Race window: recorder reads last_fusion_diagnostics() inside the same lock      Gamma
       window as fuse_signals() returns (no cross-decision pollution)
TC63   Capture-order: file lines are in captured_at ascending order within one         Gamma,Delta
       process
TC64   Sidecar dedup across same-day process restarts: capture body, stop bot,         Gamma
       restart, capture same body → sidecar has one matching sha256 line
TC65   raw_decisions_skipped.jsonl is written BEFORE the recorder re-raises on         Gamma
       write failure
TC66   FIELD_MAP coverage: every rec.update(**kwargs) key in body is in MAPPED ∪      Gamma
       IGNORED
TC67   Hybrid AST + source-line check: every DecisionRecord( call site in bot.py     Gamma
       is either (a) followed within 30 lines by RawDecisionSnapshotRecorder(
       open in same function body OR (b) preceded within 3 lines by the comment
       "raw recorder intentionally not wired here; see G1 + G10"
TC68   Static: every DecisionRecord( constructor passes decision_id= kwarg             Gamma
TC69   N×K cardinality + deterministic ordering: for an N-record corpus and K-       Eta
       override grid, harness CSV contains exactly N×K data rows (plus banner);
       row order = sorted([(o, r) for o in grid for r in corpus],
       key=(override_key_tuple, decision_id)); the ordering departure from
       capture-time corpus order is intentional and matches the documented key
TC02a  Static AST: every `<receiver>.reject(GATE_NAME, REASON)` call in bot.py and    Gamma
       decision_log.py is matched by regex `\.reject\(`; GATE_NAME extracted as
       string literal (fails on non-literal); asserted to appear in §4.4 column
       "Gate-name literals"; REASON column not asserted (dynamic f-strings)
TC32b  _resolve_observation_mode helper returns one of exactly three legal values    Gamma
       for every (strategy_observation_mode, is_simulation) Cartesian-product input
TC42b  divergence _spot_history and _spot_history_ts have equal lengths after        Beta
       every process() call across multi-decision sequence including exception path
TC47b  Synthetic exception inside ev_gate produces (post-append, v16):           Gamma
       (a) on the synthesized/updated ev_gate row at gates[-3],
       inputs._unobservable.reason == gate_exception_ev_gate;
       (b) gates[-2].name == "exception" with reason carrying the
       in-flight exception type+str (per Gamma-4 exception step 4);
       (c) gates[-1].name == "final_decision" with inputs.outcome ==
       "exception" and inputs.failing_gate == "ev_gate". This is the
       authoritative test-table row; matches §4.5 enum comment + §6.3
       Gamma-4 exception step (3)/(4)/(5) + Delta-7 (g) post-append
       semantics under the v16 universal-trailing invariant.
TC51b  Replayer produces same signal_id set as recorded record for every fixture     Zeta
       (verifies Beta-3 deterministic scheme)
TC61b  Static grep: set_weight() called only inside SignalFusionEngine.__init__       Beta
       — never elsewhere (post-Beta-5 deletion of _configure_fusion_engine and
       feedback/learning_engine.py:230 optimize_weights)
TC72   Bot started at 22:00 PST → first decision's state_snapshot(now=UTC) succeeds  Gamma
       and performs reset; subsequent validate_new_position sees same post-reset
       state (no fail-stop at UTC midnight)
TC73   POLYBOT_ACKNOWLEDGE_UTC_DAILY_RESET unset after Beta-8 → bot raises at        Beta
       startup with clear error pointing at G6 bullet (b)
TC74   Liquidity floor sweep changes verdict on at least one fixture row (proves     Eta
       Beta-10 wired bot.py:5544 to read from effective_config.liquidity_floor)
TC75   Recorder startup raises when RAW_DECISION_SNAPSHOT_DIR and                    Gamma
       LIVE_TRADE_LEDGER_PATH.parent share the same st_dev (Theta-4 enforcement)
TC76   record_freshness_age helper records all FOUR gate-time ages                   Gamma
       (before_context, before_signals, before_execution,
       before_intent_persistence). Fixture (a): passes the first two pre-body
       gates and fails before_execution → asserts those three are populated and
       before_intent_persistence is _unobservable. Fixture (b): passes all three
       pre-execution gates, enters _place_real_order, and fails
       before_intent_persistence → asserts all four are populated (the first
       three with their captured ages, the fourth with the stale value).
       Together they verify FIELD_MAP overwrite avoidance for every suffix in
       SNAPSHOT_STALE_SUFFIXES.
TC51c  Two instances of the same processor class within one fake decision_id         Beta
       produce no signal_id collision (verifies Beta-3 instance-attr scheme)
TC68b  Static assertion: decision_log.py __exit__ contains exactly one               Alpha
       self.fields["rejected_at_gate"] = <literal> assignment; literal is
       "exception"; literal appears in §4.4 names. Catches gate-string rename.
TC02c  Static AST: every §4.4 `name` (excluding executor_returned_false and          Gamma
       exception) appears as string-literal arg to raw_rec.gate_scope(...) call
       somewhere in _make_trading_decision_body or any function reachable from
       it via static call-graph analysis within bot.py (mirrors Gamma-4a scope)
TC02d  Static: GATE_LITERAL_EXEMPT_SET equals exactly {"snapshot_capture_exception",  Alpha
       "executor_enqueue_exception"}; reduction fails CI, addition requires plan edit
TC77   With POLYBOT_ACKNOWLEDGE_RISK_ENGINE_ENV_REQUIRED=1 set but any of the         Beta
       six risk-engine envs unset, bot startup raises with a clear error naming
       the missing env
TC78   build_effective_decision_config(...) at body entry completes in < 1 ms on     Gamma
       fixture decision (guards Gamma-1 restructure cost)
TC42c  bot.py price_history triple parallel-list invariant: after every body         Beta
       invocation including exception paths, `len(self.price_history) ==
       len(self._price_history_sources) == len(self._price_history_ts)`
       (verifies _append_price_history atomic helper)
TC79   Bot startup succeeds with SignalFusionEngine(weights=..., recency_window_      Beta
       seconds=...) construction; calling SignalFusionEngine() without weights=
       raises TypeError (verifies Beta-5 singleton-removal + required-kwarg)
TC80   risk_engine.add_position / remove_position / record_realized_pnl /             Beta
       restore_daily_stats / validate_new_position called without now= raises
       TypeError (verifies Beta-8 5-site now= propagation)
TC81   Each processor subclass __init__ raises TypeError when called without          Beta
       name= (verifies Beta-3 required-no-default name=)
TC82   get_risk_summary()["timestamp"].tzinfo is not None AND the line-507            Beta
       subtraction does not raise when _alerts pre-populated via UTC-aware
       append (verifies Beta-8 _alerts aware/naïve safety)
TC11b  Determinism-diff: run processor.process(now=T) then process(now=T + 60s)      Beta
       on the same input; assert output differs ONLY by fields that are explicit
       functions of now (catches RP8-clean-but-replay-dirty smuggled datetime.now).
       Includes the deribit cache-validity boundary case: decision at T inside
       cache window, identical input replayed at T+cache_seconds+1 with now=T
       MUST produce identical output regardless of wall-clock advance (catches
       deribit_pcr_processor.py:202 cache-check using wall-clock instead of now=)
TC72b  Rehydrate-filter UTC coherence: pre-populated settlement fixture straddling   Beta
       UTC midnight produces rehydrate daily_pnl sum matching UTC-day window
       exactly (verifies bot.py:1702/1722 local-TZ → UTC conversion)
TC42d  Static grep over core/strategy_brain/signal_processors/** and                 Beta
       decision_context.py for historical_prices\[-?\d*\]; asserts every match is
       followed by .value on same line OR consumed only by length/iteration
       patterns. Catches Beta-1 reader-update misses.
TC83   final_decision trailing gates entry (v14, post-amendment fixes): every       Gamma
       raw record produced by the recorder __exit__ has `gates[-1].name ==
       "final_decision"` (LAST element, even when an `exception` row was
       appended). Fixture coverage spans:
       (a) accept → `passed=true`, `reason="accepted"`, `inputs.outcome=
       "accepted"`, `inputs.failing_gate is None`, `output` populated with
       the seven §4.4-listed depth_replay+fusion duplicate fields AND each
       value byte-equals the §4.2 source slot (reference-copy invariant);
       (b) reject at any §4.4 gate → `passed=false`, `reason==gates[-2].name`,
       `inputs.outcome="rejected"`, `inputs.failing_gate==gates[-2].name`,
       `gates[-2].passed==false`, `output == {"_unobservable": true,
       "reason": "final_decision_not_accepted"}`;
       (b2) "missing_selected_side" guard branch: synthetic fixture where
       every prior gate passed but depth_replay.selected_side is None →
       Gamma-4 normal-exit step (2) emits outcome="rejected" with
       reason="missing_selected_side"; TC83(b2) asserts ZERO such fixtures
       in the captured 24h corpus (RP10) — if this fixture ever appears in
       production capture, that's a body bug;
       (c) in-body exception scoped via gate_scope → `passed=false`,
       `reason="exception"`, `inputs.outcome="exception"`,
       `inputs.failing_gate==<scoped_gate>` (a string; per Gamma-4
       exception step 3 which synthesizes-or-updates a passed=false row
       for the scoped gate), `output == {"_unobservable": true, "reason":
       "final_decision_not_accepted"}`. Post-append, `gates[-1]` is
       `final_decision` and either `gates[-2]` is the synthesized
       passed=false row for the scoped gate (typical synthesize path)
       OR `gates[-2]` is the `"exception"` row from Gamma-4 step (4)
       depending on whether the synthesize-or-update path inserted
       before or after the exception row — Gamma-4 step (3) appends
       BEFORE step (4), so the order is [..., synthesized G,
       exception, final_decision] post-append, meaning `gates[-2].name
       == "exception"` AND `gates[-3].name == "G"` (the scoped gate);
       (c2) empty-prior-gates exception (exception fires before any gate
       scope opens, scoped_gate is None) → gates contains `[exception,
       final_decision]` as its last two entries (or as the entire
       array if no other gate ever fired); post-append `gates[-1].name
       == "final_decision"`, `gates[-2].name == "exception"`;
       `inputs.outcome == "exception"`;
       `inputs.failing_gate == {"_unobservable": true, "reason":
       "no_gate_fired_before_exit"}` (the canonical §4.5 sentinel
       dict shape, NOT a separate `_unobservable_reason` sibling
       field — v16 corrects the v14/v15 spec error).
       ALSO asserts:
       (d) `AUTO_APPENDED_GATE_NAMES == {"final_decision"}` AND
       `CONDITIONAL_TRAILING_GATE_NAMES == {"exception"}` in
       raw_decision_snapshot.py; reduction of either fails CI; addition
       requires plan edit (mirrors TC02d's locked-set discipline);
       (e) On reject (NOT exception), `inputs.failing_gate` is a member of
       the §4.4 gate-name set MINUS `{"final_decision", "exception"}`
       (catches the case where the bot ever writes a `final_decision`
       with a `failing_gate` string that doesn't correspond to a known
       real gate row);
       (f) Exactly-once invariant: `sum(1 for g in record.gates if g.name
       == "final_decision") == 1` AND `sum(1 for g in record.gates if
       g.name in AUTO_APPENDED_GATE_NAMES) == 1` (catches double-append
       bugs);
       (g) Position invariant: NO `g in record.gates[:-1]` has
       `g.name == "final_decision"` (catches misplaced row);
       (h) Byte-equality invariant: on accept, for each of the seven
       output-dict keys, `record.gates[-1].output[k]` byte-equals the
       corresponding §4.2 slot read as a string (`depth_replay.<k>` or
       `fusion.<k>`); mismatch on any key fails (catches re-coercion or
       stale-copy bugs).
TC02j  Reject-then-return static check (new in v16 per round-13 reviewer #2          Gamma
       P1-3). Static AST walk over `_make_trading_decision_body` and every
       function transitively called from it within bot.py: every
       `<receiver>.reject(GATE_NAME, ...)` call MUST be followed within
       ≤2 statements (in the same function body, same syntactic block)
       by `return False`, `return`, `raise <expr>`, or a tail-position
       call that itself returns/raises. Fails on any `.reject(...)` that
       falls through to subsequent statements (which would break the
       §4.4 "Gates not evaluated because an earlier gate failed are
       OMITTED" convention by allowing further gate rows to be appended
       after the reject). The §4.4 omitted-not-failed convention is a
       load-bearing assumption for Gamma-4 step (2)'s "FIRST passed=false"
       disambiguation; this test pins the invariant on the bot.py side.
       Sites walked: every line in §4.4's Sites-walked-by-TC02a list
       (currently 34 sites). All MUST pass; failure of any site fails CI.
TC02h  gate_scope exception-path defer-pop discipline: synthetic fixture where     Alpha
       a nested gate_scope("inner") raises inside gate_scope("outer"). Asserts:
       (a) the recorder's __exit__ reads `_scoped_gate_on_exception` ==
       "inner" (the INNERMOST scope wins attribution under the
       "first-write-wins" rule);
       (b) gate_scope's exception-path EXIT does NOT pop the stack;
       (c) the recorder clears `_scoped_gate_on_exception` to None
       after processing attribution.
TC02i  Gamma-2 record_depth_replay call compiles on MARKET_IOC path: synthetic    Gamma
       MARKET_IOC fixture exercising the body's accept path with order_type
       != ORDER_TYPE_LIMIT_IOC. Asserts: no NameError on `instrument`;
       record_depth_replay is invoked; instrument_price_precision and
       instrument_size_precision are None in the recorded depth_replay.
TC02g  Gamma-4a wrap-site inputs population check (v17-revised — lazy           Gamma
       FIELD_MAP path, not per-site kwarg). For every Gamma-4a wrap site
       whose §4.4 "inputs" column lists scalar keys (NOT "(see X — DO
       NOT duplicate)"), assert: (a) those keys ARE in
       FIELD_MAP.MAPPED's codomain so the recorder can lazily populate
       the success-row inputs dict at __exit__; (b) the rec.fields[k]
       assignments that populate those keys ARE present in bot.py at
       the corresponding production sites. The check is a STATIC
       cross-reference walk between §4.4 + FIELD_MAP.MAPPED +
       rec.update(...) call sites in bot.py. Mismatch fails CI. The
       v16 per-site kwarg literal check is RETRACTED — wrap sites are
       allowed to pass only `gate_scope("G")` without `inputs=`. The
       §4.4 `risk_engine` row's "(see risk_engine_state) + scalar keys"
       hybrid form (round-14 reviewer #2 P1-3) is handled by the
       v17 check explicitly: the cross-referenced block satisfies
       the "(see X)" half, and the scalar keys are checked normally.
TC85   RP13 doc-lint self-test: run the lint script against the current        Gamma
       plan and assert ZERO violations. Verifies the v16 per-cluster
       scope is correctly implemented in the lint AND that the current
       plan satisfies the convention everywhere a gates[-N] cluster
       appears.
TC86   Malformed-Data Drop Principle counter coverage (v21, new). For each       Beta
       DropClass enum member (deribit_fetch_dropped, deribit_instrument_parse_
       dropped, deribit_short_pcr_missing_dropped, orderbook_fetch_dropped,
       orderbook_level_malformed_dropped, orderbook_process_exception_dropped,
       divergence_metadata_missing_dropped, divergence_coinbase_missing_dropped,
       unknown_signal_source_dropped, loader_truncated_trailing_line_dropped):
       (a) synthetic fixture that triggers exactly that drop class produces
       exactly ONE counter increment in record.drop_counters[<class>];
       (b) all OTHER drop_counters remain 0;
       (c) on the happy path with no drops, every drop_counters value is 0
       (NOT null, NOT absent — the field is always present);
       (d) DropClass enum is a CLOSED SET of exactly 10 baseline members
       at Phase-Beta sign-off. Alpha-4 may extend the enum when extracting
       shared helpers from calibration_decision_join.py /
       estimate_decision_results.py (per §3.A rows 16/17); any extension
       MUST update §3.D's enum block, §4.2's drop_counters schema, AND
       this TC86 row in the same diff. Reduction below the documented set
       fails CI. v22 baseline = 10; Alpha-4 anticipated additions
       (documented in the Alpha-4 cover letter when the helpers are
       extracted) may grow the set. TC86 (d) reads the enum membership
       from the source-of-truth source file at test time, so the test
       does not need re-editing when Alpha-4 grows the enum (the test
       asserts the enum matches the documented schema in §4.2 at the
       point in time the test runs).
       Also asserts policy_filter_counters semantics:
       (e) confidence-below-min triggers signal_below_min_confidence_filter
       counter increment; data is well-formed, signal is dropped from fusion;
       (f) degenerate fusion (total_contrib < 0.0001) triggers
       fusion_below_min_contrib_filter increment.
       The §3.D substitution-removal sub-case (B10 divergence Coinbase missing
       no longer falls back to polymarket momentum) is asserted via a
       dedicated fixture (v22-TIGHTENED per round-18 P1): synthetic decision
       with Coinbase spot unavailable → process() returns None at its early
       guard → divergence signal is OMITTED from signals[] (NOT a signal
       with polymarket-derived momentum, NOT a signal from the residual
       SIGNAL 1 fade branch with spot_momentum=0.0) → counter
       drop_counters.divergence_coinbase_missing_dropped == 1 → all OTHER
       drop_counters values remain 0. The test specifically constructs a
       fixture where poly_prob is in the extreme-fade range (e.g., 0.92)
       so that without the early-return, SIGNAL 1 would have fired; the
       assertion that signals[] is empty of divergence entries confirms
       the early-return is correctly placed.
       (g) **Guard-rail #3 static AST check (v22-new per round-18 P2):**
       AST-walk every module reachable from `execution/risk_engine.py` and
       every gate handler downstream of `ev_gate` in the §4.4 wrap list;
       assert ZERO `drop_counters[...]` increment statements (or any
       member of DropClass enum used as a key). Catches a future
       implementer mechanically violating §3.D guard-rail #3 by adding a
       drop in the verdict path. Failure = P0 per §4.6 rule (d).
TC87   fsync retry-once approved-fallback behaviour (v21, new per APPROVE THIS    Gamma
       FALLBACK 2026-05-24 Option A). Fault-injected fsync mock fixture:
       (a) fsync succeeds first attempt: no warning log, no retry, recorder
       continues normally;
       (b) fsync raises OSError once then succeeds on retry: WARNING-level
       log line "fsync failed, retrying once: <e1>" emitted; recorder
       continues normally; bot does NOT fail-stop; TC87 also asserts the
       warning log line contains the e1 type+message verbatim;
       (c) fsync raises OSError twice: chained OSError propagates; the
       outer .__cause__ is the second fsync exception; bot fail-stops via
       DecisionRecord.__exit__'s normal handler; raw_decisions_skipped.jsonl
       is written before the propagate per §5.6 W1 (which TC65 already
       covers; TC87 adds the fsync-twice scenario as a new failure trigger).
TC84   Delta-7 invariant coverage (catalog row added in v20). Exercises every    Delta
       Delta-7 sub-invariant (a)-(i) with a fixture pair:
       - (a)-(h): single-corpus fixture (raw record only) for each invariant.
       - (i): TWO-CORPUS fixture pair: a raw record with non-null
         recorder_internal_failure AND outcome ∈ {"accepted", "rejected"},
         paired with its corresponding decisions.jsonl line, asserting
         (rejected_at_gate=="exception") AND (rejection_reason starts with
         the same exception_type as the raw record's
         recorder_internal_failure["exception_type"]). Out of scope: the
         exception-exit FMC path (covered by TC65b). Failure of any
         sub-invariant fails CI per §4.6 rule (d).
TC65b  Combined-failure exception preservation: synthetic fixture where the         Gamma
       body raises ValueError AND `os.write` for the raw line raises
       OSError (disk full). Asserts: (a) raw_decisions_skipped.jsonl is
       written before re-raise (mirrors TC65); (b) the exception
       re-raised out of recorder.__exit__ is type ValueError (NOT
       OSError) — verifies Gamma-4 write-failure sub-path step (W2)
       preserves body_exc on the exception-exit branch; (c) the OSError
       is reachable via __context__ chain on the re-raised ValueError;
       (d) DecisionRecord.__exit__ (outer) records
       `rejection_reason == "ValueError: <msg>"`, NOT
       `"OSError: disk full"`.
TC02e  gate_scope re-entry forbidden: calling raw_rec.gate_scope("G") inside     Alpha
       a nested raw_rec.gate_scope("G") raises RuntimeError. Verifies the
       Alpha-1 spec: gate names are not re-enterable within a single
       decision body. Also asserts: gate_scope("G")'s normal __exit__
       appends a gates[] entry with name="G", passed=true, reason="ok"
       (the v15 resolution of round-12 reviewer #1 P0).
TC02f  gate_scope four-branch exit semantics: synthetic fixtures for           Alpha
       (a) success (no .reject, no exception) → appends passed=true row;
       (b) .reject("G", ...) fired inside → existing reject's row stands,
       no double-append from gate_scope's success path;
       (c) exception scoped to gate_scope("G") → recorder synthesizes
       passed=false row with _unobservable: gate_exception_G per Gamma-4 step 3;
       (d) exception not scoped (raised outside any gate_scope) → no
       synthesized row; final_decision.inputs.failing_gate is null.
TC52b  Eta-3 exception-records column: synthetic fixture where the              Eta
       replayer raises (e.g., on a malformed override that triggers a
       processor exception). Asserts: the harness CSV row includes a
       non-zero policy_replay_exception_records column for that grid
       cell AND the record is NOT counted in accepted/rejected aggregates.
       Distinguishes recorded-exception (final_decision.inputs.outcome=
       "exception" on the original capture) from replay-exception
       (replay raised under the override) — only the latter triggers
       exclusion.
TC83(j) FMC-fallback observability (v16 redesign — top-level field):              Gamma
       synthetic fixture where FIELD_MAP-copy raises (a deliberately bad
       rec.fields[k] value). Asserts:
       (a) post-append `gates[-1].name == "final_decision"` (UNIVERSAL
       invariant — the v15 trailing-row layout is RETRACTED; FMC
       fallback no longer perturbs gates[]);
       (b) `record.recorder_internal_failure` is a non-null dict with
       keys exception_type, exception_str, step;
       (c) `record.recorder_internal_failure["step"] ==
       "field_map_copy"`;
       (d) `record.recorder_internal_failure["exception_type"]` is a
       non-empty string;
       (e) `record.recorder_internal_failure["exception_str"]` is a
       non-empty string containing the failure message (verifies user's
       "show that it is exception with reason" observability requirement
       per the 2026-05-23 round-13 APPROVE THIS FALLBACK conditional);
       (f) the underlying field_map_copy_failure is re-raised by
       __exit__ at step (6) and reaches DecisionRecord.__exit__ (so the
       decisions.jsonl line records the failure via the existing outer
       handler);
       (g) on the happy-path control fixture (no FMC failure),
       `record.recorder_internal_failure is None` explicitly (NOT
       absent from the JSON — the field is always written).
       Together (a)-(g) close the Rule 1 fallback-approval loop under
       the v16 top-level-field design.
```

## 9. Live-Equivalence Boundary

Governed by CLAUDE.md Rule 3.

Phase Eta computes **policy/decision replay** aggregates. They are
**not** trade simulation because fills, fees, settlement, P&L, ledger,
risk-state evolution, and concurrency are simplified or replayed by
injection rather than modeled.

Banner on every output artifact, doc page, log line:

```text
POLICY/DECISION REPLAY ONLY — NOT LIVE-EQUIVALENT TRADE SIMULATION.
See CLAUDE.md Rule 3.
```

**Column naming convention** (TC27b): every numeric column uses
`policy_replay_` or `hypothetical_decision_` prefix. Bare `pnl`,
`profit`, `return`, `alpha`, `roi`, `edge` are forbidden in any column
header, JSON key, or doc heading.

Promoting the harness to live-equivalent trade simulation is out of
scope and requires a separate plan modeling fills, fees, settlement,
P&L, ledger, risk with live semantics.

## 10. Per-Phase Review Gate

Process: spawn three independent reviewers in parallel each round with
identical brief. They answer Q1–Q5 plus R1–R6. Phase sign-off only
when all three independently report zero concerns at any priority.

Q1 — Phase aim achieved?
Q2 — No regression?
Q3 — Test coverage sufficient?
Q4 — Wiring complete?
Q5 — Leftover from earlier phases?

R1 — Unapproved fallback?
R2 — Unapproved migration?
R3 — Main-worktree edit only?
R4 — File-length discipline?
R5 — No meaningless identifier?
R6 — For Eta/Theta: banner + column naming?

## 11. Regression Prevention Strategy

- **RP1** — Golden record fixture under
  `tests/fixtures/raw_corpus/synthetic/` re-asserted on every PR
  touching: `decision_snapshot.py`, `decision_context.py`,
  `decision_log.py`, `raw_decision_snapshot.py`,
  `effective_decision_config.py`, `strategy_version.py`, `bot.py`,
  any `core/strategy_brain/**`, any `data_sources/**` used by
  `decision_context.py`, `execution/risk_engine.py`.
- **RP2** — Import-graph: production modules never import
  `analysis/`.
- **RP3** — Capture-side no-network test.
- **RP4** — Parity replay test on small fixture corpus in CI.
- **RP5** — Schema validator runs in CI over the checked-in fixture
  corpus on every PR touching the recorder or loader.
- **RP6** — Doc-link checker over §6.8 docs.
- **RP7** — STRATEGY_VERSION CI check per Alpha-3 (semantic-diff
  filtered).
- **RP8** — Static check forbids `datetime.now()` without
  `timezone.utc` AND forbids `datetime.now(timezone.utc)` as a
  default-value expression. Scope is the PRODUCTION-LIVE subset:
  - `core/strategy_brain/signal_processors/**` (all six processors)
  - `core/strategy_brain/fusion_engine/signal_fusion.py`
  - `decision_*.py`
  - `raw_decision_snapshot.py`
  - `analysis/**`
  - `effective_decision_config.py`
  - `execution/risk_engine.py`
  EXCLUDED (with rationale documented inline in the static-check
  config):
  - `core/strategy_brain/fusion_engine/divergence_processor.py` —
    DEAD DUPLICATE of `core/strategy_brain/signal_processors/divergence_processor.py`;
    not imported in production. Beta-2 includes a sub-bullet
    deleting this file outright (preferred to exclusion).
  - `core/strategy_brain/strategies/btc_15min_strategy.py` — not
    imported by `bot.py` (legacy scaffold). Beta-2 either deletes
    it or refactors its four naïve `datetime.now()` sites (lines
    161, 236, 318, 324) to UTC-aware. Decision documented in the
    Beta-2 diff cover letter.
  - `core/strategy_brain/test_strategy.py` — test-only file;
    naïve datetime usage is per-test discretion. Excluded from
    RP8.
  - `execution/execution_engine.py` — dead-at-runtime for the
    `validate_new_position` / `_maybe_reset_daily_stats` paths.
    `bot.py` does NOT import `ExecutionEngine` directly. The file
    IS imported transitively via `monitoring/grafana_exporter.py:31`
    and instantiated at `grafana_exporter.py:197` when
    `enable_grafana=True`, with `get_statistics()` called at
    `grafana_exporter.py:344`. CRITICALLY, `get_statistics()` does
    NOT call `validate_new_position` or `_maybe_reset_daily_stats`,
    so the Beta-8-required `now=` propagation cost is genuinely
    unreached at runtime. Excluded from RP8 with the EXPLICIT
    caveat: if a future `grafana_exporter` refactor reaches
    `validate_new_position` or `_maybe_reset_daily_stats` without
    passing `now=`, the call will raise `TypeError` at runtime — a
    crash, not a silent regression. A follow-up cleanup plan may
    delete `execution_engine.py` entirely (after replacing the
    `get_statistics()` consumer with an equivalent reader on
    `RiskEngine`).
- **RP9** — Static check forbids env reads inside
  `_make_trading_decision_body` after effective config dict is built.
- **RP10** — Captured 24-hour staging corpus fixture under
  `tests/fixtures/raw_corpus/captured_24h_sim/` re-asserted on every
  PR touching the recorder. Coverage by window-label and gate-name
  MUST hit every shadow window + the live window. Fixture provenance:
  GENERATED IN CI from a checked-in "synthesis seed" YAML (~5 KB)
  that names the market metadata, the tick stream pattern, and the
  external-fact payloads — NOT a literal captured corpus. The
  generator script is under `tests/fixtures/raw_corpus/build_captured_24h_sim.py`.
  The generator computes Deribit expiries as `today + relativedelta(
  years=5)` at generation time (so they roll forward automatically).
  A separate sanity test (TC70b) asserts on every CI run that every
  Deribit instrument in the regenerated fixture has DTE > 365 days
  from CI run date; if that assertion fails, the fixture is auto-
  regenerated as part of the test run (consistent with "regenerated
  whenever schema or seed changes"). The captured fixture is
  deterministic for a given seed + CI date (TC70 enforces).
- **RP11** — fcntl-flock test (TC29a) asserts cross-process corpus
  exclusion.
- **RP12** — Atomicity-via-helper static check, covering ALL
  parallel-list groups managed by an atomic helper in this plan.
  The static check forbids BOTH direct assignments AND mutator
  method calls outside the allowlisted helper bodies. The
  forbidden patterns (AST node families):
  - Direct: `Assign` (e.g., `self._spot_history = [...]`),
    `AugAssign` (`+=`, etc.), `Delete` (`del self._spot_history[i]`).
  - Method-call mutators: `Call(func=Attribute(value=<target>,
    attr=<method>))` where method is one of `append`, `extend`,
    `insert`, `pop`, `remove`, `clear`, `sort`, `reverse`,
    `__setitem__`, `__delitem__`.
  - Subscript-write: `Subscript` on the LHS of `Assign` /
    `AugAssign` / `Delete`.
  - Alias patterns: `local_name = self.<list>` followed by any
    mutation on `local_name`; `getattr(self, '<list>*')`;
    `vars(self)[...]` assignment touching either field.
  Coverage scopes:
  - **`divergence_processor.py`**: protected lists
    `self._spot_history` and `self._spot_history_ts`. Allowlisted
    writer: `_append_spot(value, ts)` body only.
  - **`bot.py`** (Beta-1 triple): protected lists
    `self.price_history`, `self._price_history_sources`,
    `self._price_history_ts`. Allowlisted writer:
    `_append_price_history(value, *, source, ts)` body only. The
    existing `self.price_history.pop(0)` at `bot.py:4511` (the
    max-history truncation today) is REMOVED in Beta-1 and the
    equivalent length-bounded truncation moves INTO
    `_append_price_history` as a final step that pops the oldest
    entry from ALL THREE lists atomically when len exceeds
    `max_history`. After Beta-1 lands, no `self.price_history.*`
    method call OR `self._price_history_*.*` method call appears
    in `bot.py` outside the helper.
  - Equivalent allowlisted-helper-only static check for any
    other atomic-helper-managed parallel-list group the plan
    introduces later.
- **RP13** — Indexing-convention static doc check (v16-revised
  per-cluster scope; v15 per-paragraph scope flagged 10+ existing
  paragraphs as false-positives, was retracted). A docs lint
  runs over `docs/RAW_DECISION_SNAPSHOT_PLAN.md` and asserts:
  every `gates[-N]` (negative-indexed) reference is contained
  within a "gates[-N] cluster" — a contiguous run of markdown
  paragraphs each containing at least one `gates[-N]` reference,
  terminated by the first paragraph with NO such reference — AND
  that cluster contains the literal token "post-append" OR
  "gates_pre" SOMEWHERE within it (not necessarily the same
  paragraph as each `gates[-N]` reference). A "paragraph" is
  blank-line-delimited; markdown table cells are atomic units
  (treated as a single paragraph each); markdown code fences are
  atomic units (treated as a single paragraph each, including
  comments inside the fence). Catches v15-style off-by-one bugs
  introduced when the trailing `final_decision` row was added
  but old text still used `gates[-1].name == <failing_gate>`
  semantics. The check is intentionally docs-only; source code's
  `gates[-1]` is unambiguous from context. The lint script lives
  at `tests/lint_indexing_convention_doc.py` and is wired into
  the RP1 PR-trigger. TC85 (new in v16) is a self-test asserting
  the script reports zero violations on the current plan.

## 12. Operational Contracts

**Env vars introduced** (all opt-in, fail-fast on enable):

- `RAW_DECISION_SNAPSHOT_DIR` — explicit corpus directory; enabling
  switch.
- `RAW_DECISION_SNAPSHOT_INCLUDE_POLYMARKET_RAW` — raw order book
  retention.
- `RAW_DECISION_SNAPSHOT_INCLUDE_COINBASE_RAW` — raw Coinbase spot
  retention.
- `RAW_DECISION_SNAPSHOT_INCLUDE_FEAR_GREED_RAW` — raw F&G
  retention.
- `RAW_DECISION_SNAPSHOT_INCLUDE_DERIBIT_RAW` — raw Deribit option
  summaries retention (required for sweeping Deribit instrument-level
  params).
- `POLYBOT_REQUIREMENTS_LOCK_PATH` — optional path override for the
  requirements lock file hashed into `provenance.requirements_lock_sha256`.
  Default is `requirements.txt` at project root. Missing file raises
  at startup.
- `POLYBOT_IMAGE_DIGEST` — container digest captured into
  `provenance.container_digest`. Optional; absent → Unobservable.
- `POLYBOT_GIT_SHA` — single source of truth for `provenance.git_sha`.
  REQUIRED when capture is enabled (`RAW_DECISION_SNAPSHOT_DIR` set);
  missing raises at startup. No `git rev-parse` probe (a probe-then-
  env design would qualify as a fallback under M4). For git-bearing
  deploys, operators set this via a startup wrapper
  (`POLYBOT_GIT_SHA="$(git rev-parse HEAD)" exec polybot ...`).
  Tarball deploys set it from the build manifest. Recipe in
  `docs/RAW_DECISION_SNAPSHOT_OPERATIONS.md`.
- `POLYBOT_ACKNOWLEDGE_UTC_DAILY_RESET` — required env (must be set
  to `"1"`) for the bot to start after Beta-8 lands. Acknowledges
  that the daily-stats reset boundary moves from local-TZ midnight
  to UTC midnight (G6 bullet (b)). Without this env, the bot raises
  at startup with a message pointing at the operations doc. Enforced
  at every startup regardless of whether `RAW_DECISION_SNAPSHOT_DIR`
  is set, because the risk_engine refactor lands as part of Beta
  and applies to all deploys.
- `POLYBOT_ACKNOWLEDGE_RISK_ENGINE_ENV_REQUIRED` — required env
  (must be set to `"1"`) for the bot to start after Beta-8 lands.
  Acknowledges that the risk-engine env defaults (MAX_POSITION_SIZE
  etc.) are now required-no-default; missing any of the six
  promoted envs raises at startup. Same enforcement scope as
  `POLYBOT_ACKNOWLEDGE_UTC_DAILY_RESET`.
- `POLYBOT_REQUIRE_SEPARATE_RAW_CORPUS_FILESYSTEM` — optional env;
  set to `"1"` in production deploys to enable the Theta-4
  startup `st_dev` check (raises if `RAW_DECISION_SNAPSHOT_DIR`
  and the live ledger share a filesystem). Unset in dev/CI where
  both paths legitimately share `st_dev`.

**Promoted-constant env vars** (refactor-only; preserve numeric values
bit-for-bit; required, no default):

- `TREND_UP_THRESHOLD`, `TREND_DOWN_THRESHOLD`
- `LIQUIDITY_FLOOR`
- `FUSION_MIN_SIGNALS`, `FUSION_MIN_SCORE`,
  `FUSION_RECENCY_WINDOW_SECONDS`
- `DIVERGENCE_SPOT_HISTORY_MAX_LEN`
- `TICK_VELOCITY_TOLERANCE_SECONDS`
- Risk engine defaults (per §3.A item 13): `MAX_POSITION_SIZE`,
  `MAX_TOTAL_EXPOSURE`, `MAX_POSITIONS`, `MAX_DRAWDOWN_PCT`,
  `MAX_LOSS_PER_DAY`, `BALANCE_SAFETY_BUFFER_USD` — all promoted to
  required envs. The refactor removes the silent `Decimal(os.getenv(
  ..., "1.0"))` fallback. Because removing the defaults is an
  observable deploy-time fail-stop for any operator who relied on
  the implicit defaults, Beta-8 ALSO gates bot startup on
  `POLYBOT_ACKNOWLEDGE_RISK_ENGINE_ENV_REQUIRED=1` (mirror of
  `POLYBOT_ACKNOWLEDGE_UTC_DAILY_RESET`). Pre-merge requirement:
  Beta-8 implementer audits every checked-in deploy template
  (`.env.example`, `deploy/polybot.service`) to confirm all six
  envs are already set, AND records the audit result in the Beta-8
  diff cover letter. TC77 asserts that with any of the six envs
  unset (plus the acknowledgement env), the bot startup raises
  with a clear error naming the missing env.

Every required env is validated at startup; missing required vars
fail-stop with a clear error naming the var.

**CLI commands introduced**:

- `python -m analysis.raw_snapshot_loader --validate <dir>`
- `python -m analysis.resolution_joiner --corpus <dir> --out <path>`
- `python -m analysis.policy_replayer --corpus <dir> --parity --out <path>`
- `python -m analysis.policy_replayer --corpus <dir> --override <yaml>
   --report <path>`
- `python -m analysis.brute_force_harness --corpus <dir>
   --resolutions <path> --grid <yaml> --out <csv>`

## 13. Sign-Off Criteria

Complete when ALL hold:

- All phases Alpha–Theta pass the §10 review gate (zero remaining
  concerns from all three reviewers at any priority).
- All TC IDs in §8 are green in CI.
- All RP IDs in §11 are wired and green in CI.
- §9 banner + column-naming convention enforced; TC27b passes.
- A 24-hour staging corpus has been captured with no operator
  intervention, validated by Phase Delta with zero invalid records.
- The parity replay (G3) over the 24-hour staging corpus reports
  100% verdict-tuple match on in-scope records; OR every remaining
  mismatch carries an `APPROVED_PARITY_DEVIATION` annotation in a
  checked-in exception list signed off by the implementer with
  per-mismatch root cause.
- All §3.A pre-existing fallbacks dispositioned (removed or
  `APPROVE THIS FALLBACK` comment).
- No unapproved fallback (M4); no unapproved migration (M3).
- §3.B vendor ToS table filled in.
- §6.8 Theta-7 capacity-planning guidance documented and the deploy
  template edits are in place.

When all criteria hold, this plan is marked complete in
`docs/DATA_COLLECTION_INVENTORY.md` with a one-line cross-link and
the snapshot date.
