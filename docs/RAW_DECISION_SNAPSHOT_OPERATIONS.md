# Raw Decision Snapshot — Operations Guide

This document covers the operational requirements for the raw decision
snapshot capture infrastructure introduced by
[`RAW_DECISION_SNAPSHOT_PLAN.md`](RAW_DECISION_SNAPSHOT_PLAN.md).
Read alongside that plan; this document is the operator-facing layer.

---

## 1. Enabling capture

Capture is **opt-in via exactly one switch** (M8): the env
`RAW_DECISION_SNAPSHOT_DIR`. With the env unset the recorder's
`__exit__` no-ops the disk write and the bot logs `raw decision
snapshot capture disabled` at startup.

To enable, set:

```
RAW_DECISION_SNAPSHOT_DIR=/opt/polybot/raw_decision_snapshots
POLYBOT_GIT_SHA="$(git rev-parse HEAD)"
POLYBOT_REQUIRE_SEPARATE_RAW_CORPUS_FILESYSTEM=1   # production only
```

Per-vendor raw-payload opt-in (default is hash-only; per §3.B):

```
RAW_DECISION_SNAPSHOT_INCLUDE_DERIBIT_RAW=1   # default off
```

## 2. Disabling capture

There is **no live kill switch** (G7). The only way to disable capture
in production is to restart the bot with `RAW_DECISION_SNAPSHOT_DIR`
unset. A kill switch that silently disabled capture on disk-full would
be a fallback under M4 and is explicitly out of scope.

## 3. Required envs

The following envs are REQUIRED at startup. Missing values raise
`KeyError` at module import (the §12 "promoted-constant" convention)
or at construction (M11 required-no-default kwargs).

| Env | Purpose | Notes |
|---|---|---|
| `POLYBOT_ACKNOWLEDGE_UTC_DAILY_RESET` | Operator acknowledgement of UTC daily-reset boundary | MUST be `"1"` (Beta-8 / G6 bullet b) |
| `MAX_POSITION_SIZE` | Risk limit (Decimal USD) | §12 |
| `MAX_TOTAL_EXPOSURE` | Risk limit (Decimal USD) | §12 |
| `MAX_POSITIONS` | Max concurrent positions | §12 |
| `MAX_DRAWDOWN_PCT` | Max drawdown (float, e.g. `0.15`) | §12 |
| `MAX_LOSS_PER_DAY` | Daily loss cap (Decimal USD) | §12 |
| `LIQUIDITY_FLOOR` | Min liquidity for entry (Decimal) | Beta-10 / §12 |
| `FUSION_RECENCY_WINDOW_SECONDS` | Fusion recency window (int) | Beta-5 / §12 |
| `DIVERGENCE_SPOT_HISTORY_MAX_LEN` | Spot-history cap (int) | Beta-4 / §12 |
| `TICK_VELOCITY_TOLERANCE_SECONDS` | Tick-window tolerance (int) | Beta-4 / §12 |
| `FUSION_MIN_SIGNALS` | Min signals for fusion (int) | §12 |
| `FUSION_MIN_SCORE` | Min fusion consensus score (float) | §12 |
| `TREND_UP_THRESHOLD` | Trend-up entry threshold (float) | §12 |
| `TREND_DOWN_THRESHOLD` | Trend-down entry threshold (float) | §12 |
| `POLYBOT_GIT_SHA` | Git SHA for provenance block | Required ONLY when `RAW_DECISION_SNAPSHOT_DIR` is set |
| `POLYBOT_REQUIREMENTS_LOCK_PATH` | Path to requirements lockfile | Optional; defaults to `requirements.txt` |
| `POLYBOT_IMAGE_DIGEST` | Container digest (optional) | Recorded as Unobservable when unset |

## 4. UTC daily-reset boundary

Beta-8 moves the daily-stats reset boundary from local-TZ to UTC. This
is an observable trading behaviour change: the `max_loss_per_day`
window rolls over at UTC midnight rather than local midnight.

The bot **refuses to start** without
`POLYBOT_ACKNOWLEDGE_UTC_DAILY_RESET=1`. Coordinate with operations
before flipping the env.

## 5. Sample commands

```
# Validate a corpus directory
python -m analysis.raw_snapshot_loader --validate /opt/polybot/raw_decision_snapshots

# Build the resolution-join sibling file
python -m analysis.resolution_joiner \
    --corpus /opt/polybot/raw_decision_snapshots \
    --out    /opt/polybot/raw_decision_snapshots/resolutions.jsonl

# Replay corpus in parity mode and write a diff log
python -m analysis.policy_replayer \
    --corpus /opt/polybot/raw_decision_snapshots \
    --parity \
    --out    /tmp/parity.jsonl

# Brute-force harness over a grid
python -m analysis.brute_force_harness \
    --corpus      /opt/polybot/raw_decision_snapshots \
    --resolutions /opt/polybot/raw_decision_snapshots/resolutions.jsonl \
    --grid        sweep_grid.yaml \
    --out         /tmp/sweep.csv
```

## 6. Live-equivalence boundary

All harness output is **policy/decision replay**, never trade
simulation, per CLAUDE.md Rule 3 / §9. Numeric columns are prefixed
`policy_replay_` or `hypothetical_decision_`. There is no realized
P&L computed by the harness.

## 7. Vendor ToS table (§3.B)

| Vendor | Raw retention permitted | Default |
|---|---|---|
| Polymarket CLOB | Yes (own user data) | hash-only |
| Coinbase Exchange | Hash-only by default | hash-only |
| Alternative.me | Yes | hash-only |
| Deribit | Yes (public API) | hash-only; raw via `RAW_DECISION_SNAPSHOT_INCLUDE_DERIBIT_RAW=1` |

## 8. Capacity planning (Theta-7)

- The capture volume MUST be on a **separate filesystem** from the
  live ledger. A capture disk-full event must not corrupt the
  ledger. The bot enforces this via the
  `POLYBOT_REQUIRE_SEPARATE_RAW_CORPUS_FILESYSTEM=1` env (production
  deploys MUST set this; dev/CI may leave it unset).
- Sizing recipe: project daily usage at each opt-in env's enabled
  state; size the volume at **3× the 30-day projection**.
- Minimum free-space alerting at **50% (warn) and 80% (page)** of
  projected daily consumption × retention window.
- Logrotate: see [`deploy/polybot.logrotate`](../deploy/polybot.logrotate).
- Monitoring tie-in: add a free-space panel to
  [`grafana/dashboard.json`](../grafana/dashboard.json) (deferred to
  a follow-up plan).

## 9. Pinned Python version

The brute-force harness output is deterministic per
`(corpus, grid, code SHA, Python version)`. Pin Python `3.11.6` for
production deploys; CI runs may use a different version provided the
harness CSV is regenerated.

## 10. Grafana panel timestamp shift (Beta-8 defensive callout)

After Beta-8, the `timestamp` field returned by
`RiskEngine.get_risk_summary()` is UTC-aware. The current
`monitoring/grafana_exporter.py` consumer reads only
`risk_summary['exposure']['utilization_pct']`; no existing Grafana
panel is affected. If a future panel JSON adds a query reading
`risk.timestamp`, it will see the UTC-aware value rather than the
prior local-TZ-naïve one.

## 11. No automated weight tuning

Beta-5 deleted `feedback.learning_engine.optimize_weights` (the only
`set_weight` callsite outside `SignalFusionEngine.__init__`).
Automated weight tuning is deferred to a follow-up plan. The
production `PRODUCTION_DEFAULT_WEIGHTS` dict in `bot.py` is the
single source of truth for fusion weights.

---

## 12. Minimal-Gamma deferral notice

The current Phase Gamma wiring is a MINIMAL-VIABLE implementation:

- The bot's `_make_trading_decision_body` opens the raw recorder and
  mirrors every `DecisionRecord.reject(...)` into the recorder via
  `RawDecisionSnapshotRecorder.mirror_reject(...)`, but does NOT wrap
  each gate evaluation in a `gate_scope(...)` block (§6.3 Gamma-4a is
  deferred).
- The captured `final_decision.output` on accept is the §4.4 7-key dict
  populated from `set_final_accept_output(...)`, but the per-gate input
  / output records (Gamma-2 `record_pre_state` / `record_signal` /
  `record_fusion_diagnostics` / `record_depth_replay` /
  `record_risk_engine_state` / `record_freshness_age` helpers) are NOT
  yet wired — those record sub-blocks will be `null` or absent in
  captured records until the full Gamma-2 implementation lands.
- The §6.6 policy replayer is a minimal skeleton: it derives the
  parity tuple from the recorded gates and does NOT re-run processors
  against an override. Harness sweeps over per-processor params will
  not move the replayed verdict until Zeta-2/3/4 land.
- The §6.7 brute-force harness aggregates win/loss correctly but per-
  window and per-`bot_mode` breakouts (Eta-3) and `deribit_cache_seconds`
  sweep guards (Eta-6) are deferred.

These deferrals do not block capture (records are valid per §6.4
Delta-7 invariants a-c, e-h, and partially f); they limit the
analytical depth of the offline harness. A follow-up plan will close
them.

---

Last updated: 2026-05-24 (initial release, minimal-Gamma).
