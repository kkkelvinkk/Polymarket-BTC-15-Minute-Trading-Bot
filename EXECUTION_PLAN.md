# Polymarket BTC 15-Min Bot — Execution Plan

This document lays out the full sequence of fixes and enhancements before the next live run. Each phase has a clear purpose, scope, exit criteria, and effort estimate. Phases are ordered by criticality, not by ambition.

## Implementation status snapshot

| Phase | Status | Notes |
| --- | --- | --- |
| 0.1 actual-fill scaffold + durable unknown + pre-submit intent audit | **SHIPPED** | `bot.py`, `mark_settlement_resolved.py`, `patch_market_orders.py` |
| 0.2 zero-price ledger guard | **SHIPPED** | `bot.py` `_record_live_order_fill` |
| 0.3 live startup gates (`MARKET_BUY_USD > 5.50` + `--confirm-live`) | **SHIPPED** | `bot.py` `enforce_live_market_buy_usd_gate` + `_prompt_for_live_confirmation` |
| 0.3 VWAP `avg_px` injection | **RESOLVED upstream** | `nautilus_trader==1.227.0` ships `_weighted_average_price` |
| 0.4 token-dust normalization | **RESOLVED upstream** | 1.227.0 ships `_fill_tracker.snap_fill_qty` |
| 0.4 UUID4 client-id fallback removal at 3 sites | **SHIPPED in-tree** | `patch_market_orders.apply_uuid_fallback_guard_patch` (pinned to 1.227.0) |
| 0.5 `quote_quantity=True` units mismatch | **RESOLVED upstream** | 1.227.0 ships `base_quantity = takerAmount/1e6` |
| 0.5a Nautilus 1.227.0 clean-env audit + upgrade | **SHIPPED** | `requirements.txt`, `bot.py` `instrument_config` rename |
| 0.6 regression tests | **SHIPPED for 0.1-0.4 + 0.5a** | 290 passing tests |
| 0.7 manual recovery of the lost `$11` trade | **operator action** | Awaiting operator + Polymarket UI verification |
| 1.1 wire/remove `STOP_LOSS_PCT` / `TAKE_PROFIT_PCT` / `SPIKE_THRESHOLD` / `DIVERGENCE_THRESHOLD` | **SHIPPED — Option B** | Removed from README/.env.example; remain code-owned constants |
| 1.2 README env-section docs | **SHIPPED** | New "Env vars that are NOT wired" + "Live startup gates" sections |
| 1.3 `.env.example` | **SHIPPED** | With warning banner; `.gitignore` exception added |
| 2.4 structured `decisions.jsonl` writer | **SHIPPED + WIRED** | `decision_log.py:DecisionRecord` + 10 tests; wiring landed in `bot.py` `_make_trading_decision_body` with `rec.reject(gate, reason)` at every early-return and `rec.decided(direction=...)` at the positive path |
| 2.5 dynamic sizing + balance freshness | **VALIDATORS SHIPPED** | `bot.py:get_sizing_mode_for_live` + `get_pct_of_free_collateral_per_trade` + 6 tests; AccountState freshness hook + sizing-mode integration in `_make_trading_decision` still pending |
| 3 ORDER_TYPE (market_ioc / limit_ioc) + quote stability | **MANDATORY LIVE-RESUME BLOCKER - PARTIAL SHIP** | `bot.py:get_order_type_for_live`, `get_validated_limit_required_edge`, `compute_limit_price`, `compute_limit_order_token_qty` + 15 tests shipped. LIMIT order-factory branch in `_place_real_order`, startup/runtime `ORDER_TYPE` enforcement, configurable `QUOTE_STABILITY_REQUIRED`, wire-format tests, and live smoke verification now block live resume. |
| 4 calibration analysis | **SCRIPT SHIPPED, DATA PENDING** | `analyze_calibration.py` + 16 tests; needs `n>=100` settled live trades to decide |
| 4.5 strategy timing / price-band evaluation | **OBSERVABILITY FIELDS SHIPPED** | `bot.py:trade_window_label_for_seconds_into_sub_interval` + `trend_price_band_for` populate `seconds_into_sub_interval`, `trade_window_label`, `trend_price_band` on every `decisions.jsonl` record. Full shadow-policy observation mode (run candidate windows without submitting) deferred — needs accumulated data and a separate review cycle |
| 5A market-depth estimator helpers + EV-gate WIRING | **SHIPPED + WIRED** | `depth_estimator.py` + 22 tests; EV-gate now uses `_compute_depth_aware_entry` to compute VWAP via `estimate_market_ioc_fill` on the SELECTED token's asks (YES book for long, NO book for short). Fail-closed on missing token id, fetch error, empty asks, invalid book level, or book too thin. 5 new wiring tests covering each fail-closed branch. |
| 5B LIMIT_IOC depth integration | **MANDATORY LIVE-RESUME BLOCKER - PARTIAL SHIP** | `depth_estimator.estimate_fill_for_order_type` dispatches by ORDER_TYPE + 7 tests shipped; EV-gate caller wiring in `_make_trading_decision` is mandatory before `ORDER_TYPE=limit_ioc` can be used for live trading. |
| 6 SOPS credential management | **GUARD MODULE + TEMPLATES SHIPPED** | `phase_6_sops_check.py:refuse_plaintext_env_in_live_mode` (Pattern A check, 9 tests) + `deploy/.env.sops.yaml.example` + `deploy/polybot.service` SOPS variant + `deploy/README.md` SOPS section. Wiring into `bot.py` is a one-line operator opt-in (per plan: "Operator must approve exactly one implementation before Phase 6 work starts") |
| 7 live env reload | **DECIDED — Option C** | "Don't implement"; restart-driven config workflow documented in `README.md` + `deploy/README.md`. Effort: 0 days per plan |
| 7.5 multi-asset evaluation | **EVALUATION TEMPLATE SHIPPED** | `deploy/PHASE_7_5_multi_asset_evaluation_template.md` — operator fills in asset selection + topology + per-asset calibration decision; follow-up implementation effort sized in the template by topology choice |
| 8 Linux deployment | **TEMPLATES SHIPPED** | `deploy/polybot.service` + `deploy/polybot.logrotate` + `deploy/polybot-ledger-backup.cron` + `deploy/README.md` (install runbook + monitoring + security checklist). Operator copies into place on the target server |

**Live mode is not approved for resume yet.** All Phase 0 defects that would have blocked or corrupted live trading are now fixed (either upstream via 1.227.0 or in-tree via the Phase 0.4 UUID guard patch), but operator must still complete Phase 0.7 manual recovery of the lost `$11` trade and the mandatory Phase 3 + Phase 5B limit-price order path before resuming live trading.



---

## No-Fallback Policy For This Plan

This plan contains no approved fallback implementation path. A fallback path means substituting alternate trading data, synthetic identifiers, default policy, default configuration, default price/size, or alternate control flow and then continuing normal operation.

Allowed fail-closed outcomes are not fallback paths: reject the trade, block live settlement, create a durable `SETTLEMENT_UNKNOWN` with real identifiers or `null`, fail startup, or fail-stop the process. Those outcomes stop normal trading and require explicit operator reconciliation.

Any future fallback proposal must be added as an explicit open decision with the exact code path, exact substituted data, every trigger condition, and the operational reason it is unavoidable. Without that approval, the implementation must fail closed.

Additional Phase 0 prohibitions:
- Admin tools must require an explicit `--ledger` path for every read or write. No `LIVE_TRADE_LEDGER_PATH`, repo-root, current-directory, or other default ledger path is approved for admin tooling.
- Missing settlement accounting fields must never be coerced into usable values. Missing `size`, `filled_qty`, `entry_price`, `payout`, or `pnl` must either be repaired from externally verified data through an explicit admin command or keep the record unresolved.
- Submitted or intended spend must never become accounting cost. `submitted_size`, `spend_amount`, `estimated_tokens`, and `estimated_price` are audit-only unless and until actual fill accounting verifies cost and units.
- Terminal submitted-order events must preserve explicit audit records. Deleting the submitted intent is not an approved terminal path.
- Stale-flag repair may only clear `needs_reconciliation` after the same sanity checks used by normal reconciliation pass: finite non-negative cost basis, known positive filled units, no overpayout unless a prior explicit overpayout marker exists, and exact `pnl == payout - size`.

## No-Migration Policy For This Plan

This project is treated as a new app deployment. No existing user ledger, configuration, schema, or runtime state needs to be upgraded in place.

Do **not** implement any migration, backfill, upgrade-on-read, upgrade-on-write, key rename, value rewrite, version transform, or compatibility shim for older ledger shapes. If an existing file is not in the current required shape, the app and admin tools must fail closed with a clear error. The operator may replace that file with a freshly initialized current-shape ledger outside the application; application code must not transform old state into new state.

---

## Phase 0 — Lost Live Fill Reconciliation

**Status:** Critical for ledger accuracy, but **not confirmed to block all trades.**

### Important scope caveat

The bot has been observed placing real orders successfully — the trading flow is functional in general. The fill-reconciliation bug described below was discovered by reviewing a console log, not by observing the bot stop trading. Other trades may have settled correctly through `auto_redeem` without exposing this code path. We do not yet have a full reproduction or evidence on what fraction of fills hit this defect.

Therefore Phase 0 is required because:
- **The specific `BTC-15MIN-$11-...` trade is real on Polymarket and missing from the bot's ledger.** That must be reconciled regardless of how often the bug occurs.
- **The defect can cause silent ledger desync.** Even if rare, a missing fill produces a phantom on-chain position with no risk-engine reservation, which compounds across runs.

Phase 0 is **not** a claim that all live trading is currently broken. It is a claim that we have one confirmed lost fill, and we should not place more live orders until the reconciliation tools and detection guards are in place.

### Symptoms observed in production log

The sanitized excerpts below are the canonical incident evidence in this plan. The original local console log is intentionally not linked because `console_logs/` is ignored and not tracked; linking to it would create a broken evidence reference for anyone reviewing the committed plan. Two distinct fill-failure incidents are visible in the captured excerpts:

**Incident A — original $11 trade (the lost fill that triggered this plan):**

```
report.avg_px was None
Generated inferred OrderFilled ... last_qty=17.460316, last_px=0.00
Order overfill rejected ... quantity=17.460300
```

**Incident B — operator's $55 trade (second symptom class, exposes a units-mismatch bug):**

```
10:43:38.26 ERROR: Order overfill rejected ... potential_overfill=21.388885,
                   last_qty=76.388885, quantity=55.00            ← units mismatch (tokens vs USDC)
10:43:43.83 ERROR: Order overfill rejected ... potential_overfill=0.000085,
                   last_qty=76.388885, quantity=76.388800        ← dust after OrderUpdated
```

Both incidents are addressed across Phase 0 sub-steps below — Incident A by 0.3 (avg_px) and 0.4 (dust); Incident B by 0.5 (units mismatch) and 0.4 (dust).

### Root cause

Two separate problems compounded:

1. **Missing avg_px in order-status report.** Polymarket's order-status payload did not include `avg_px`. Nautilus's inferred fill used a default of `0.00`, producing a structurally invalid fill event.
2. **Token-dust overfill rejection.** `size_matched = 17.460316`, `original_size = 17.460300`. Difference of `0.000016` tokens — well within precision noise — was rejected as a true overfill.

The combination meant Nautilus dropped the fill event entirely. The bot's `on_order_filled` callback never fired. The ledger has no record of this position.

### Trade details needing manual reconciliation

```
client_order_id:   BTC-15MIN-$11-1779093783343
venue_order_id:    0x0638dd3348f45dedf81306e6070d32f2d0b7bfc2b3b3bd6a84063aae51b31205
side:              NO (DOWN)
entry_price:       0.63
filled_qty:        17.460316
cost (notional):   10.99999908 (~$11.00)
condition_id:      0xd55ee02c5080428bab05c89cf861e347c3a94306d8e0c999b87068f3145b7b2b
token_id:          13493549868196599328875182057608566347488679426659046670938216865177784647005
```

### Fix order — canonical Phase 0 sequence

Earlier drafts used lettered dependency labels and then tried to execute them out of numeric order. That made the plan hard to implement safely. Phase 0 is now renumbered in the exact order it must ship:

1. **0.1: actual-fill callback scaffold + durable unknown helper + pre-submit intent audit.** Adds `register_actual_fill_handler`, `_dispatch_actual_fill`, the strategy-side handler, `_create_durable_settlement_unknown_from_actual_fill`, a fresh v3 ledger schema contract, first-class `venue_order_id` admin-tool support, `pending_actual_fills`, durable `submitted_order_intents`, and `on_stop` unregister. No adapter integration yet.
2. **0.2: zero-price ledger guard.** Depends on 0.1 because it calls `_create_durable_settlement_unknown_from_actual_fill`.
3. **0.3: avg_px / VWAP injection.** Uses `get_trades` and dispatches through the callback from 0.1.
4. **0.4: token-dust normalization.** Clips only adapter-local dust for Nautilus while preserving actual filled units through the side channel.
5. **0.5: `quote_quantity=True` units-mismatch overfill patch.** Fixes the first, larger overfill in the $55 production log before the 0.4 dust tolerance can apply.
6. **0.5a: Nautilus 1.227.0 dependency audit for one-sided quote drops.** Ultra-critical because current-instrument quote drops can reduce trade frequency by starving the strategy before `on_quote_tick`.
7. **0.6: regression tests.** Covers scaffold, guard, VWAP injection, dust, units mismatch, UUID fallback removal, 1.227.0 audit decisions, and fresh-schema validation.
8. **0.7: manual recovery of the lost `$11` trade.** Done last, after code and tests are in place.

**Why 0.5 is critical for the operator's target trade sizes:** the spurious overfill at the unit-mismatch stage scales with token count. At `MARKET_BUY_USD=55` and ask=$0.72, the matched size is ~76 tokens, producing a spurious overfill of ~21. The dust-tolerance check in 0.4 is `<=0.001 tokens` — it cannot rescue this. Without 0.5, every $55 trade in the operator's intended sizing config will hit the same lost-fill class as the original $11 incident. Phase 0 is incomplete without 0.5. Phase 0 is also incomplete without 0.5a because current-instrument one-sided quote drops can suppress trade decisions.

#### 0.1 — Actual-fill callback scaffold and durable unknown helper

**Scope:** create the callback and durable-ledger machinery that later Phase 0 steps call. This ships first with no adapter integration.

Required pieces:

- `register_actual_fill_handler`, `unregister_actual_fill_handler`, and `_dispatch_actual_fill(client_order_id, payload)`.
- Strategy-side handler for `payload["status"] in {"ok", "failed"}` plus an explicit unknown-status branch.
- `_create_durable_settlement_unknown_from_actual_fill(...)` with first-class `venue_order_id` reconciliation support and no UUID/synthetic-id fallback.
- Fresh v3 ledger schema contract with load/save support, transactional rollback support, startup unresolved-state detection, and live-blocking behavior. No migration, backfill, compatibility upgrade, startup conversion, or old-ledger rewrite is allowed.
- Durable pre-submit `submitted_order_intents` persistence so YES/NO intent is on disk before `self.submit_order(order)`.
- `mark_settlement_resolved.py` support for identifying/listing `pending_actual_fills` as unresolved state and resolving records by `--venue-order-id`.
- `on_stop` unregister behavior that logs and raises on unregister failure.

The detailed side-channel contract is expanded in the 0.4 section below because 0.3/0.4 consume it, but the scaffold, durable unknown helper, fresh `pending_actual_fills` ledger section, durable `submitted_order_intents`, admin-tool visibility, first-class `venue_order_id` reconciliation, and UUID-fallback guard are 0.1 deliverables. Implement these before any adapter patch or guard calls them.

##### Pre-submit YES/NO order-intent persistence

Current code derives `trade_label = "YES (UP)"` / `"NO (DOWN)"` before order construction and keeps that metadata in `_submitted_positions` until a fill path succeeds. That is not durable. If the process crashes after exchange submission but before a fill reaches `_record_live_order_fill`, the operator may lose the YES/NO side, token id, and submitted spend context needed for reconciliation.

Phase 0.1 must add a top-level ledger section named `submitted_order_intents`. The strategy must write one record to this section immediately before `self.submit_order(order)`. If the intent write fails, the bot must reject/fail closed before exchange submission. This is audit persistence, not a fallback path: it records submitted intent only, and it must never be used to invent fills, settlement, P&L, or profitability.

Required persisted fields:

```json
{
  "submitted_order_intents": {
    "BTC-15MIN-$55-1779...": {
      "client_order_id": "BTC-15MIN-$55-1779...",
      "trade_label": "YES (UP)",
      "outcome_side": "YES",
      "direction": "long",
      "order_side": "BUY",
      "order_type": "market_ioc",
      "quote_quantity": true,
      "spend_amount": "55.00",
      "estimated_tokens": "76.388885",
      "estimated_price": "0.72",
      "price_source": "YES ask",
      "instrument_id": "<Nautilus instrument id>",
      "token_id": "<CLOB token id>",
      "slug": "<market slug>",
      "condition_id": "<condition id>",
      "market_start_time": "<ISO timestamp>",
      "market_end_time": "<ISO timestamp>",
      "submitted_at": "<UTC ISO timestamp>",
      "signal_score": "<numeric score>",
      "signal_confidence": "<numeric confidence>",
      "status": "INTENT_PERSISTED"
    }
  }
}
```

For NO orders, the same schema is used with `"trade_label": "NO (DOWN)"`, `"outcome_side": "NO"`, and `"direction": "short"`.

Lifecycle rules:
- Write `submitted_order_intents[client_order_id]` before calling `self.submit_order(order)`.
- If `submit_order` raises, leave the intent record durable and mark it as requiring operator review; do not assume the exchange did or did not receive the order.
- When a fill is recorded into `open` or `settled`, copy the persisted intent fields into that trade record and remove the matching `submitted_order_intents` entry in the same atomic ledger write.
- Terminal no-fill events must be persisted as audit, not deleted. If Nautilus/exchange emits `OrderDenied`, `OrderRejected`, `OrderCanceled`, or `OrderExpired` with verified zero fill, update the matching intent in `submitted_order_intents` to a terminal non-blocking audit status such as `ORDER_DENIED_NO_FILL`, `ORDER_REJECTED_NO_FILL`, `ORDER_CANCELED_NO_FILL`, or `ORDER_EXPIRED_NO_FILL`, set `needs_reconciliation=false`, and preserve the raw event/report plus any real `venue_order_id` and reason. Do not create P&L and do not remove the audit record.
- If a terminal order event has any fill quantity, average price, trade id, or ambiguous fill state, it is not a no-fill event. Route it through the normal fill/actual-fill path when fill details are valid; otherwise keep the intent unresolved and block live trading until admin reconciliation. Do not mark it terminal no-fill from status name alone.
- On startup, any active `submitted_order_intents` entry whose status is not an approved terminal no-fill status is unresolved state. Live trading must stay blocked until the operator lists and resolves the intent through an admin command. Manual JSON inspection is not an accepted resolution path.
- The admin tool must support listing submitted intents and converting one explicit intent into SETTLEMENT_UNKNOWN only after the operator verifies an exchange fill exists. If the operator verifies no exchange order/fill exists, the admin tool must mark that intent `SUBMISSION_NOT_SEEN` without creating P&L; that terminal status remains in the intent audit section but does not block live trading.
- `estimated_tokens` is audit-only order intent. It is not a verified fill unit count and must not be used for payout validation, auto-redeem matching, settlement accounting, or manual reconciliation.

Acceptance criteria:
- [ ] A live order cannot call `self.submit_order(order)` unless the YES/NO intent record has already been durably written.
- [ ] The persisted record distinguishes YES from NO with both `trade_label` and `outcome_side`.
- [ ] A save failure before submit rejects/fail-stops before any exchange order submission.
- [ ] A successful fill consumes the intent exactly once in the same atomic ledger write that records the fill.
- [ ] `OrderDenied` / `OrderRejected` / `OrderCanceled` / `OrderExpired` with verified zero fill preserve a terminal no-fill audit entry and do not block live trading.
- [ ] Ambiguous terminal events with missing/uncertain fill state remain unresolved and block live trading.
- [ ] Startup with any unresolved submitted intent blocks live trading and provides an admin-tool listing/resolution path; startup with only terminal no-fill audit intents does not block.

#### 0.2 — Defensive hard guard in `_record_live_order_fill`

File: `bot.py`, method `_record_live_order_fill`. Avoid exact line-number references in implementation notes; this file is changing rapidly during Phase 0.

Insert after the existing blocked-ledger check. The guard must (a) block process-local state AND (b) create a durable SETTLEMENT_UNKNOWN with enough identifiers to be operator-reconcilable across restart:

```python
if fill_price <= Decimal("0"):
    # Process-local block
    self._block_live_settlement_ledger(
        f"refused fill for {order_id}: fill_price={fill_price} is non-positive; "
        "Polymarket fill event has invalid avg_px"
    )
    logger.error(
        f"LIVE FILL REJECTED: non-positive fill_price={fill_price} for {order_id}; "
        "ledger blocked to prevent zero-notional bookkeeping"
    )

    # Durable unknown so the pause survives restart. Do not look up or invent
    # missing venue metadata here; this guard only records the invalid Nautilus
    # fill fields it was explicitly given.
    payload = {
        "status": "failed",
        "reason": "non_positive_fill_price_from_nautilus",
        "fill_price": str(fill_price),
        "fill_qty": str(fill_qty),
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    self._create_durable_settlement_unknown_from_actual_fill(
        client_order_id=order_id,
        payload=payload,
        reason=payload["reason"],
    )
    return False
```

This resolves the previous-draft contradiction. The earlier text said "stay process-blocked AND propagate" but also "Process is blocked; manual intervention required before restart" — which silently relied on the operator noticing the LOG before restarting. The corrected behavior is:

- **Durable write succeeds:** process-local block + durable SETTLEMENT_UNKNOWN. Restart preserves the pause via the durable record. Operator resolves via admin tool.
- **Durable write fails:** **fail-stop the process** via `raise SettlementLedgerError`. The bot exits abnormally. Operator MUST inspect ledger and reconcile before any restart. There is no scenario where a restart silently clears the pause.
- **Order already exists in `open`:** the durable write must atomically convert that existing open trade into one SETTLEMENT_UNKNOWN record by removing the order from the candidate `open` map and appending the unknown to the candidate `settled` list in the same ledger save. For direct-fill failure payloads such as zero price, blocked ledger, invalid direction, or invalid accounting, mark the raw payload `requires_external_fill_repair=true` and do **not** promote the open trade's accounting fields into top-level `size`, `filled_qty`, `entry_price`, or `filled_notional`. The operator must repair those values from verified external data before payout resolution. Do not persist duplicate `open` + `settled` state for the same order. If the conversion cannot be written durably, fail-stop without mutating in-memory state.

This is belt-and-suspenders: even if 0.3 and 0.4 fail, a bad fill cannot corrupt the ledger, AND a restart cannot silently clear the resulting pause.

##### Zero-price guard scope and limits

The zero-price guard in `_record_live_order_fill` rejects any fill that reaches the strategy's recorder with `fill_price <= 0`. This is necessary but **not sufficient**:

- It catches fills that pass through Nautilus and reach the strategy with bad data.
- It does **NOT** catch the case in the observed production log, where Nautilus rejected the fill at the overfill-check stage and never invoked `on_order_filled` at all — the fill simply vanished.

The complete protection requires the adapter-side callback and normalization series (0.1 + 0.3 + 0.4 + 0.5 + 0.5a, plus the installed Nautilus UUID-fallback guard). The zero-price guard is the last line of defense; the adapter callback is the primary one. Ship `0.1` first so `_create_durable_settlement_unknown_from_actual_fill` exists, then ship `0.2`. Do not ship a process-local-only guard.

#### 0.3 — Patch order-status report to populate avg_px (single deterministic path)

File: `patch_market_orders.py` (or a new patch module).

##### Live startup safety gates added to Phase 0.3

Phase 0.3 must also tighten live-start control before any adapter-patched live order path can run.

**Current implementation status after Phase 0.1-0.2:** these live-start gates are intentionally still pending. Do not mark Phase 0.3 complete until the `MARKET_BUY_USD > 5.50` enforcement and `--confirm-live` parser behavior below are implemented in `bot.py` and covered by tests.

1. **Minimum live trade size gate.** Direct `--live` startup must fail closed unless `MARKET_BUY_USD > 5.50`. The comparison is strict: `5.50` is blocked, `5.51` is allowed. Parse the env value as `Decimal`; missing, malformed, non-finite, zero, negative, or `<= 5.50` values must abort startup before the Nautilus node starts.

   Required operator-facing message shape:

   ```text
   LIVE STARTUP BLOCKED: MARKET_BUY_USD must be greater than 5.50 USDC for live mode.
   Current MARKET_BUY_USD=<value>. Increase it to at least 5.51 or run without --live.
   ```

   This check must not be bypassed by Redis mode switching inside a live-enabled process or by any nonstandard live-submission call path. A process launched without live execution enabled remains simulation-only; every path that can submit a live order must still run the same `MARKET_BUY_USD > 5.50` gate before the first live order. Do not add an alternate size default or automatic resize.

2. **Explicit non-interactive live confirmation flag.** Keep the current manual `LIVE` typing requirement for `--live` by default. Add exactly one command-line-only argument, `--confirm-live`, that means "the operator intentionally accepts live startup for this invocation" and skips the interactive prompt.

   Required behavior:
   - `--live` without `--confirm-live`: prompt exactly as today and require the operator to type `LIVE`.
   - `--live --confirm-live`: do not prompt; log a clear line that live confirmation was provided by explicit CLI flag.
   - `--confirm-live` without `--live`: fail argument parsing; the flag is only valid for live startup.
   - No env var, config file key, or default may replace `--confirm-live`. A persistent confirmation source would be a hidden approval path under `AGENTS.md`.
   - `--confirm-live` does not bypass any other live startup gate, including Redis control seeding, credentials, ledger checks, Nautilus UUID fallback guard, or the `MARKET_BUY_USD > 5.50` check above.

Acceptance criteria:
- [ ] `venv/bin/python bot.py --live` with `MARKET_BUY_USD=5.50` exits before node startup and prints the clear blocked message.
- [ ] `venv/bin/python bot.py --live` with `MARKET_BUY_USD=5.51` still requires typing `LIVE`.
- [ ] `venv/bin/python bot.py --live --confirm-live` with `MARKET_BUY_USD=5.51` starts without the interactive prompt.
- [ ] `venv/bin/python bot.py --confirm-live` fails argument parsing because `--live` was not supplied.
- [ ] Any live-enabled Redis mode change, environment change after startup, or nonstandard live-submission path enforces the same minimum trade-size gate before submitting a live order.

**Exact adapter hook point (no more "patch order-status report normalization" ambiguity):**

The hook targets are every installed Nautilus path that creates a FILLED `OrderStatusReport`, not only the singular helper:

- `PolymarketExecutionClient.generate_order_status_report(...)`, which reads `py_clob_client.get_order(...)` and converts one order response.
- `PolymarketExecutionClient.generate_order_status_reports(...)`, active-order loop, which calls `polymarket_order.parse_to_order_status_report(...)` for each order returned by `get_orders(...)`.
- `PolymarketExecutionClient.generate_order_status_reports(... generate_order_history_from_trades ...)`, which builds a FILLED `OrderStatusReport` from fill reports.

If any of these paths creates a FILLED report with `avg_px is None`, it must run the same deterministic VWAP injection or dispatch the same fail-closed callback. Patching only `generate_order_status_report(...)` is insufficient because plural reconciliation can still emit a missing-price filled status report.

The implementer must:

1. Confirm against the installed Nautilus version that this is the method that produces the missing-`avg_px` report. If the actual method name differs (e.g., `parse_to_order_status_report`, `_normalize_order_response`), the plan must be amended with the verified name before implementation.
2. Patch/copy the body of each status-report creation path, not just wrap the returned `OrderStatusReport`. The raw Polymarket response is only available inside the adapter method body; a wrapper that calls the original first has already lost `raw_status_report`, `condition_id`, `token_id`, and lookup-window context needed by the durable-unknown path.
3. The condition is `report.avg_px is None and report.order_status == OrderStatus.FILLED`. `OrderStatusReport` uses `order_status`, not `status`.
4. Confirm whether installed Nautilus `OrderStatusReport.avg_px` is mutable. If it is mutable, set `report.avg_px = instrument.make_price(vwap)` before returning. If it is immutable, construct and return a replacement `OrderStatusReport` with every original field preserved except `avg_px`. Do not rely on mutation until this is verified in a test.
5. Inside the patch the implementer has access to `self._cache` (the strategy's order cache) for the `venue_order_id → client_order_id` mapping. This is the prerequisite the `cache.client_order_id(venue_order_id)` call in 0.3 and 0.4 depends on.

**`order_submit_ts` source (explicit, no inferred fallback):**

The `get_trades` time window in 0.3's step 1 uses `order_submit_ts - 5s` as the lower bound. The source is **the `ts_event` recorded by Nautilus when `submit_order` was originally called** — accessible via `order.ts_init` on the order object retrieved from `self._cache.order(client_order_id)`. This is a real, recorded timestamp, not an estimate.

If `order` is `None` or `ts_init` is `None` (rare; would indicate the order was created outside the normal flow), the patch fails closed: dispatch `status=failed, reason=order_submit_ts_unavailable` and let the durable-unknown path handle it. Do NOT invent a fallback window (e.g., `now - 60s`) — that risks matching trades from other orders within the time window.

**Correct `py_clob_client` API:**

The reviewer-flagged correction: `py_clob_client` does **not** expose `get_trade_history`. It exposes:

```python
client.get_trades(TradeParams(market=condition_id, asset_id=token_id, after=..., before=...))
```

Trade payloads have `id`, `taker_order_id`, `maker_order_id`, `price`, `size`, `side`, `match_time`, etc. They do **not** know our Nautilus `client_order_id`. The match path must use the **venue order id** (which Nautilus records when the order is acknowledged).

**Deterministic price-source rule (single path, no fallback chain):**

For a filled order-status report where `avg_px is None`:

1. Fetch `client.get_trades(TradeParams(market=condition_id, asset_id=token_id, after=<order_submit_ts - 5s>, before=<now + 1s>))`.
2. Filter the returned trades by **exact predicate per order type** (no "as appropriate" guessing):
   - **`MARKET_IOC` (current production path):** match where `t.taker_order_id == venue_order_id`. The bot crosses the spread, so it is always the taker.
   - **`LIMIT_IOC` (mandatory Phase 3 path):** match where `t.taker_order_id == venue_order_id`. IOC limit orders that fill immediately are also takers — they don't rest, so they cannot be the maker side of a match.
   - **If we ever add `LIMIT_GTC` or resting orders (out of scope):** match where `t.maker_order_id == venue_order_id`. Not in scope today; document only.
   - **Fail closed:** if no trade matches the expected predicate, dispatch `{"status": "failed", "reason": "no_matching_trade"}` — do NOT fall back to the other predicate. The role is determined by order type at submission and is unambiguous.
3. Compute size-weighted VWAP across matched trades: `vwap = sum(t.price * t.size) / sum(t.size)`.
4. Inject the VWAP as `avg_px` on the order-status report before Nautilus generates the inferred fill.

**Fail-closed when match unavailable (one explicit behavior, not "or"):**

If `get_trades` returns nothing matching the venue order id within the time window, the adapter takes exactly this path:

1. Do **not** infer, do not use the order's submitted `price` (undefined for market orders), do not fabricate.
2. Dispatch a **structured failure payload** through the actual-fill callback (defined in 0.1). The payload **must include the following fields at minimum** so the durable SETTLEMENT_UNKNOWN handler has enough context for manual reconciliation. `reason` is required; a missing/empty `reason` is a malformed payload and must fail closed instead of defaulting to another reason.
   ```python
   _dispatch_actual_fill(client_order_id, payload={
       # Required: classification
       "status": "failed",
       "reason": "no_matching_trade",  # or "unmapped_venue_order_id", "real_overfill_rejected", etc.

       # Required: identifiers
       "venue_order_id": venue_order_id,
       "condition_id": condition_id_from_status_report,
       "token_id": token_id_from_status_report,

       # Required: trade context (what the bot intended)
       "side": side_from_status_report,        # "BUY" / "SELL"
       "submitted_size": submitted_size,       # original order size

       # Required: timing for forensics
       "submitted_at": order_submit_ts,
       "lookup_window_after": after_ts,
       "lookup_window_before": before_ts,
       "report_received_at": <UTC ISO timestamp>,

       # Required: raw status report so reconciliation can replay exactly what we saw
       "raw_status_report": <verbatim dict from Polymarket>,
   })
   ```

   The durable SETTLEMENT_UNKNOWN helper copies these fields directly into the ledger entry. If any required field is missing in the underlying status report (e.g., `condition_id`), set it to `null` in the payload — never fabricate.
3. The strategy's handler validates the failure payload before writing a durable record. If `status == "failed"` and `reason` is present/non-empty, it routes to **both**: (a) `_block_live_settlement_ledger(reason)` for the process-local pause, AND (b) `_create_durable_settlement_unknown_from_actual_fill(...)` for a durable SETTLEMENT_UNKNOWN entry that survives restart. If `reason` is missing/empty, the handler raises `SettlementLedgerError("malformed actual-fill payload: missing reason")`; it does not invent a default reason. `_record_live_order_fill` is **not** called — the only ledger write for a valid failed payload is the SETTLEMENT_UNKNOWN record (see the 0.4 strategy handler details below).
4. Exact return behavior after the dispatch:
   - If the callback returns successfully, the patched adapter returns `None` from `generate_order_status_report` or skips appending a report in `generate_order_status_reports(...)`. It does not return a non-filled report and does not let Nautilus generate an inferred fill.
   - If the durable write fails inside the strategy callback, the callback raises; `_dispatch_actual_fill` re-raises; the adapter method propagates the exception so the bot fail-stops.
5. Operator manually reconciles via `mark_settlement_resolved.py --create-unknown-from-external-order` once they verify the trade externally on Polymarket.

This is one path, not two. No magic-value sentinels (the previous draft used `vwap=Decimal("0")` as a failure signal — replaced here with an explicit `status` field). Satisfies the `AGENTS.md` no-silent-fallback rule.

**Acceptance:** a market BUY that fills at 0.63 must produce a Nautilus fill event with `last_px=0.63`, derived from the matched-trade VWAP, OR the adapter dispatches a `status=failed` callback and the ledger blocks. The bot never writes a ledger entry with inferred or fabricated price data.

**Risk cleanup rule:** cleanup paths that release reserved/open exposure without final settlement accounting must require `risk_engine.release_position`. They must not call `risk_engine.remove_position(...)` as a substitute because `remove_position` books realized P&L. If `release_position` is unavailable or fails, block live settlement and raise `SettlementLedgerError`.

#### 0.4 — Normalize token-dust in Polymarket order-status report (with explicit actual-units side-channel)

**Scope:** patch the **Polymarket adapter's report normalization**, not Nautilus' global overfill checker. Do NOT set `allow_overfills=True` globally — that would weaken overfill protection for every other order.

**Tolerance rule (both conditions must pass — stricter):**

```python
OVERFILL_ABSOLUTE_TOLERANCE = Decimal("0.001")    # tokens
OVERFILL_RELATIVE_TOLERANCE = Decimal("0.001")    # 0.1%

if size_matched > original_size:
    overfill = size_matched - original_size
    relative = overfill / original_size
    if overfill <= OVERFILL_ABSOLUTE_TOLERANCE and relative <= OVERFILL_RELATIVE_TOLERANCE:
        # Dust: clip the report's matched_size for Nautilus' overfill check.
        # The actual filled units MUST be preserved via the side-channel below.
        ...
    else:
        # Real overfill: Nautilus rejects the fill event downstream, which means
        # the strategy never sees on_order_filled. BUT a real fill DID happen on
        # Polymarket — so the adapter must still dispatch a `status=failed,
        # reason=real_overfill_rejected` payload through the actual-fill callback
        # so the strategy creates a durable SETTLEMENT_UNKNOWN. Otherwise the same
        # silent-desync class that we are fixing reappears: real position on chain,
        # zero tracking in the bot.
        _dispatch_actual_fill(client_oid, {
            "status": "failed",
            "reason": "real_overfill_rejected",
            "venue_order_id": str(venue_order_id),
            "order_qty": str(original_size),
            "matched_qty": str(size_matched),
            "overfill_tokens": str(overfill),
        })
        # Then let Nautilus reject the fill event normally.
```

**Critical: actual-units side-channel (resolves the prior internal contradiction)**

The reviewer-flagged gap: if the adapter clips `last_qty` so Nautilus accepts the fill, then `on_order_filled()` receives the **clipped** quantity — not the actual Polymarket filled units. The plan previously asserted "the ledger preserves 17.460316" without specifying how. That assertion is only true with an explicit side-channel. Here's the bridge (scaffolded in 0.1, populated in 0.3 and 0.4):

1. **`venue_order_id ↔ client_order_id` mapping (use existing Nautilus cache, no invented helpers):**

   The adapter processes a status report keyed by `venue_order_id` (a Polymarket order hash). The strategy keys its tracking by `client_order_id` (e.g., `BTC-15MIN-$5-1779...`). Nautilus' cache exposes this mapping directly:

   ```python
   # Inside the patched report-normalization path:
   client_oid = self._cache.client_order_id(venue_order_id)
   ```

   The earlier draft referenced a `_lookup_order_by_venue_id` helper — that helper does not exist in Nautilus and must not be invented. Use only the existing cache API. If `client_oid` is `None`, fail closed by dispatching `client_order_id=None` with `status=failed, reason=unmapped_venue_order_id` and a real `venue_order_id` field in the payload. No silent inference, no synthesized lookup paths, and no `venue:<...>` or raw `0x...` value in the `client_order_id` parameter.

2. **Strategy-registered callback (structured payload, no magic sentinels):**

   Mirror the existing `register_auto_redeem_handler` pattern. Add:

   ```python
   _polymarket_actual_fill_handlers: list = []

   def register_actual_fill_handler(handler):
       """
       Register a callback that receives (client_order_id: str, payload: dict).
       payload["status"] is one of: "ok", "failed".
       For "ok": payload also has "filled_qty" (Decimal) and "vwap" (Decimal).
       For "failed": payload also has "reason" (str) and diagnostic fields.
       """
       if handler not in _polymarket_actual_fill_handlers:
           _polymarket_actual_fill_handlers.append(handler)

   def _dispatch_actual_fill(client_order_id, payload):
       handlers = list(_polymarket_actual_fill_handlers)
       if not handlers:
           raise RuntimeError("actual_fill dispatch has no registered handler")
       normalized_client_order_id = None if client_order_id in (None, "") else str(client_order_id)
       for handler in handlers:
           try:
               handler(normalized_client_order_id, dict(payload))
           except Exception as exc:
               logger.exception("actual_fill handler failed: %s", exc)
               raise
   ```

3. **Adapter patch invokes the callback at report-normalization time:**

   - **Success path:** after computing VWAP from `get_trades` and detecting any dust overfill, the adapter calls `_dispatch_actual_fill(client_oid, payload)` where `payload` carries **the same reconciliation identifiers as the failure payload**, so the success handler can downgrade to a durable unknown if needed (see below):

     ```python
     payload = {
         "status": "ok",
         "filled_qty": Decimal("17.460316"),
         "vwap": Decimal("0.63"),

         # Same identifiers as the failure payload (used if downgraded to unknown)
         "venue_order_id": venue_order_id,
         "condition_id": condition_id_from_status_report,
         "token_id": token_id_from_status_report,
         "side": side_from_status_report,
         "submitted_size": submitted_size,
         "submitted_at": order_submit_ts,
         "report_received_at": <UTC ISO timestamp>,
         "raw_status_report": <verbatim>,
     }
     ```

     This invocation happens BEFORE clipping for Nautilus.

   - **Failure path:** if `get_trades` finds no match and `client_oid` is known, the adapter calls `_dispatch_actual_fill(client_oid, {"status": "failed", "reason": "no_matching_trade", ...})` and does NOT proceed to generate a fill event.
   - **Unmapped venue path:** if `client_oid` cannot be resolved, the adapter calls `_dispatch_actual_fill(None, {"status": "failed", "reason": "unmapped_venue_order_id", "venue_order_id": ...})` and does NOT proceed to generate a fill event.

4. **Bridge mechanism — strategy handler (durable fail-closed, explicit else):**

   `IntegratedBTCStrategy.on_start` registers a handler that branches on `payload["status"]` with **no silent paths** — every branch either records actual values or creates a durable SETTLEMENT_UNKNOWN entry:

   ```python
   def _handle_actual_fill(self, client_order_id, payload):
       status = payload.get("status")
       if status == "ok":
           with self._settlement_lock:
               meta = (
                   self._submitted_positions.get(client_order_id)
                   or self._open_live_trades.get(client_order_id)
               )
               if meta is not None:
                   meta["_actual_filled_qty"] = payload["filled_qty"]
                   meta["_actual_fill_vwap"] = payload["vwap"]
               else:
                   # No matching local tracking. Status was "ok" but we cannot
                   # write back actual units anywhere. This means a fill happened
                   # for an order the bot has no record of (e.g., after a restart
                   # that lost _submitted_positions). Create a durable unknown.
                   self._create_durable_settlement_unknown_from_actual_fill(
                       client_order_id=client_order_id,
                       payload=payload,
                       reason="actual_fill_ok_but_no_local_tracking",
                   )
                   self._block_live_settlement_ledger(
                       f"actual fill received for untracked {client_order_id}; "
                       "SETTLEMENT_UNKNOWN created for manual reconciliation"
                   )
       elif status == "failed":
           reason = payload.get("reason")
           if not reason:
               self._block_live_settlement_ledger(
                   f"malformed actual-fill callback for {client_order_id}: missing reason"
               )
               raise SettlementLedgerError(
                   f"malformed actual-fill callback for {client_order_id}: missing reason"
               )
           # Failed reconciliation. process-local block + durable unknown so
           # the pause persists across restart.
           self._create_durable_settlement_unknown_from_actual_fill(
               client_order_id=client_order_id,
               payload=payload,
               reason=reason,
           )
           self._block_live_settlement_ledger(
               f"actual-fill callback failed for {client_order_id}: "
               f"{reason}; SETTLEMENT_UNKNOWN created"
           )
       else:
           # Unknown status value — never silent. Durable + process block.
           self._create_durable_settlement_unknown_from_actual_fill(
               client_order_id=client_order_id,
               payload=payload,
               reason=f"unknown_status:{status!r}",
           )
           self._block_live_settlement_ledger(
               f"actual-fill callback for {client_order_id} had unknown status "
               f"{status!r}; SETTLEMENT_UNKNOWN created"
           )
   ```

**Why durable not just process-local:**

`_block_live_settlement_ledger` is **process-local state** in `bot.py` — a restart clears it. Without a durable SETTLEMENT_UNKNOWN record, a bot restart after a failed reconciliation would silently resume live trading with phantom exposure. By creating a SETTLEMENT_UNKNOWN entry, the existing live-trading pause gate in `_unresolved_settlement_unknowns` keeps the bot paused after restart until the operator explicitly resolves it.

**Required schema for `_create_durable_settlement_unknown_from_actual_fill` (no synthesized placeholders):**

The durable record must use only verified-or-explicitly-unknown fields. No "looks like a real trade" placeholders. The exact required schema:

```python
{
    # Mandatory marker fields — these alone trigger the live-trade pause gate
    "settlement_source": "SETTLEMENT_UNKNOWN",
    "needs_reconciliation": True,
    "payout": "UNKNOWN",          # string literal "UNKNOWN", not 0
    "pnl": "UNKNOWN",             # string literal "UNKNOWN", not 0

    # Identifiers — preserve real values where available, omit (don't fabricate) otherwise
    "order_id": client_order_id_or_None,
    "venue_order_id": payload.get("venue_order_id"),
    "condition_id": <from order metadata if known, else null>,
    "token_id": <from order metadata if known, else null>,
    "slug": <from order metadata if known, else null>,
    "direction": <from order metadata if known, else null>,
    "trade_label": <from order metadata if known, else null>,
    "submitted_at": <from order metadata if known, else null>,
    "size": <verified accounting cost, preserved open-trade accounting cost, or null>,
    "submitted_size": <original intended/submitted size if known, else omitted>,

    # Diagnostic — preserve the raw failure payload verbatim for forensics
    "unknown_reason": required_reason,  # required by caller; no payload default
    "raw_callback_payload": payload,    # the entire dict, as-is
    "created_at": <UTC ISO timestamp>,
}
```

Rules:
- **Never** synthesize `payout` or `pnl` from inferred values. Always literal string `"UNKNOWN"`.
- **Never** populate submitted-size fields from price × qty or from an accounting `size` field. `submitted_size` is only `payload["submitted_size"]` or a real `source_meta["submitted_size"]`; if neither exists, omit it.
- If the durable unknown contains validated positive actual fill data (`filled_qty > 0` and `vwap > 0`), top-level `size` is the accounting cost for the filled exposure: `filled_qty * vwap`, and `filled_notional` must equal the same value. This is verified fill accounting, not a submitted-size fallback. Keep intended/submitted spend only in `submitted_size` audit metadata.
- If creating SETTLEMENT_UNKNOWN from a direct-fill failure or any payload marked `requires_external_fill_repair=true`, do **not** preserve or promote open-trade accounting fields. The normal payout-only reconciliation path must reject the record until the operator repairs `size`, `filled_qty`, `entry_price`, and `filled_notional` from verified external data.
- If no validated positive actual fill data exists and no already-recorded open-trade accounting cost exists, top-level `size` MUST be `null`. Never use original/intended/submitted size as accounting size. Submitted/intended spend belongs only in `submitted_size` audit metadata.
- **Always** include `raw_callback_payload` verbatim. The operator's manual reconciliation will join on this.

**Reconciliation selectors (CRITICAL — no synthetic `order_id` fallback):**

Durable unknowns must be reconcilable, but the plan must not convert `venue_order_id` into a synthetic `order_id`. `mark_settlement_resolved.py` must gain first-class `--venue-order-id` support. A record may have `order_id = None` if the bot cannot resolve a real client order id, as long as `venue_order_id` is present and unique. Resolution uses exactly one selector: `--order-id <client_order_id>` OR `--venue-order-id <venue_order_id>`.

The strategy-side handler must reject venue-like values in the `client_order_id` parameter. If `client_order_id` case-insensitively starts with `venue:` or raw `0x`, or equals the payload's `venue_order_id`, the handler blocks and raises `SettlementLedgerError`; it must not persist that value as `order_id`.

```python
if client_order_id_resolved is not None:
    order_id = client_order_id_resolved          # normal case: "BTC-15MIN-$5-1779..."
elif venue_order_id is not None:
    order_id = None                               # no synthetic id
    # venue_order_id is persisted as its own field and resolved with:
    # mark_settlement_resolved.py --venue-order-id 0x0638...
else:
    # Both identifiers missing. Fail closed — do NOT generate uuid, synthetic,
    # or placeholder identifiers. The operator must investigate the raw payload
    # before restarting.
    raise SettlementLedgerError(
        f"actual-fill callback has neither client_order_id nor venue_order_id; "
        f"raw payload: {payload!r}. Cannot create a reconcilable durable record. "
        f"Bot will fail-stop. Operator must inspect logs and reconcile manually "
        f"before restart."
)
```

Admin-tool changes ARE required in Phase 0.1:
- `mark_settlement_resolved.py --venue-order-id <venue_order_id>` must resolve exactly one durable unknown whose `venue_order_id` matches and whose `order_id` may be null.
- `mark_settlement_resolved.py --list-pending-actual-fills` must identify pending actual fills as unresolved state. Manual JSON inspection is not an accepted alternate path.
- `mark_settlement_resolved.py --list-submitted-order-intents` must identify durable pre-submit YES/NO intents. The operator must resolve one explicit intent through `--convert-submitted-intent <order_id>` with verified fill details, or `--resolve-submitted-intent-no-order <order_id> --confirm-no-exchange-order` after verifying no exchange order exists.

The earlier draft included `unknown:{uuid4()}` and `venue:<hash>` synthetic order-id paths. Both are removed. UUIDs are externally unreconcilable, and `venue:<hash>` is a fallback from the normal client-order-id path. `venue_order_id` remains a real exchange identifier, but it must be modeled as its own field and admin selector, not disguised as an `order_id`.

**Installed Nautilus UUID fallback removal (REQUIRED, same P0 class):**

Installed Nautilus currently synthesizes `ClientOrderId(str(UUID4()))` when `self._cache.client_order_id(venue_order_id)` is missing in multiple report paths. That creates an unreconcilable client id even if the new durable-unknown helper never generates UUIDs. Phase 0 must remove/guard every installed fallback in `venv/lib/python3.14/site-packages/nautilus_trader/adapters/polymarket/execution.py`.

Do **not** rely on hand-editing `venv/` as the durable fix. The repo must provide either:

- a live-startup-applied monkey patch in tracked repo code that replaces/guards these adapter methods before live execution starts, OR
- a live-startup verification that inspects the installed adapter behavior and fail-stops live mode if any UUID fallback path remains reachable.

The implementation PR must choose exactly one of those two durability mechanisms and document the choice. Do not implement a runtime fallback from one mechanism to the other.

Decision/test-mode startup should not be blocked by this live adapter guard, because no live execution client is allowed to submit orders in those modes. Live mode must still fail before any live-enabled process can submit orders if the UUID guard detects a reachable fallback path.

Because `pip install -r requirements.txt`, venv recreation, or Nautilus upgrades can erase direct site-packages edits, Phase 0 is not complete until this guard is applied from tracked repo code or verified at startup.

Required UUID-generation sites to patch/verify:

- `generate_order_status_reports(...)` active-order loop.
- `generate_order_status_reports(... generate_order_history_from_trades ...)` when it builds a synthetic filled `OrderStatusReport` from fill reports.
- `_parse_trades_response_object(...)` fill-report path.

Required behavior at all three sites:

```python
client_order_id = self._cache.client_order_id(venue_order_id)
if client_order_id is None:
    _dispatch_actual_fill(None, {
        "status": "failed",
        "reason": "unmapped_venue_order_id",
        "venue_order_id": str(venue_order_id),
        "raw_status_report": <verbatim order/fill payload>,
        "report_source": "<exact adapter method name>",
        "report_received_at": <UTC ISO timestamp>,
    })
    return None  # or continue/skip append in list-producing methods
```

Do **not** generate a UUID. Do **not** return a Nautilus report with a synthetic client id. Do **not** synthesize `venue:<hash>`. If the callback's durable write fails, let the exception propagate and fail-stop. The durable record is resolved by first-class `venue_order_id`.

**Conflict resolution with `--create-unknown-from-external-order`:**

If a durable SETTLEMENT_UNKNOWN already exists (created at runtime by the bot), the operator does NOT run `--create-unknown-from-external-order` again — that command refuses to overwrite an existing record. If that existing unknown already has verified `filled_qty`, `entry_price`, and accounting `size`, the operator runs `--order-id <existing-order-id> --payout <verified>` directly. If the existing unknown is missing fill accounting fields because the callback was malformed/failed before the actual fill details were available, the operator must use the explicit repair-and-resolve path in one locked admin update:

```bash
venv/bin/python mark_settlement_resolved.py \
  --ledger /path/to/live_trades.json \
  --order-id '<existing-order-id>' \
  --repair-unknown-fill-accounting \
  --confirm-external-order \
  --external-size '<verified filled notional>' \
  --external-entry-price '<verified avg fill price>' \
  --external-filled-qty '<verified filled token units>' \
  --payout '<verified payout>' \
  --reason 'Verified fill accounting and payout from Polymarket/exchange records'
```

The repair path must validate `external_size == external_entry_price * external_filled_qty` within the same tolerance as external-order reconstruction, must preserve the previous fill-accounting fields under `external_fill_repair_previous_state`, and must then resolve the unknown by applying the payout. This is an explicit operator repair path, not a fallback: no values are inferred from estimates or submitted intent. The `--create-unknown-from-external-order` command is reserved for the case where the bot has zero record of the trade (e.g., a fill that bypassed the callback entirely).

**Inconsistent settlement flags repair:** live startup pauses on either unresolved marker: `needs_reconciliation is true` OR `settlement_source == "SETTLEMENT_UNKNOWN"`. The normal payout/repair resolver is intentionally stricter and only mutates records where both markers are present. To avoid manual JSON edits for half-updated records, `mark_settlement_resolved.py` must include an explicit confirmed repair command:

```bash
venv/bin/python mark_settlement_resolved.py \
  --ledger /path/to/live_trades.json \
  --order-id '<existing-order-id>' \
  --repair-inconsistent-settlement-flags \
  --confirm-inconsistent-settlement-flags \
  --reason 'Verified one-flag settlement inconsistency'
```

Rules:

- If `settlement_source == "SETTLEMENT_UNKNOWN"` and `needs_reconciliation` is not true, set `needs_reconciliation=true` so the record is consistently unresolved and can go through the normal payout/repair path.
- If `needs_reconciliation is true` and `settlement_source` is a resolved terminal source (`manual_reconciliation`, `auto_redeem`, or `late_auto_redeem`), clear `needs_reconciliation` only when `payout`, `size`, and `pnl` are finite, `payout >= 0`, `size >= 0`, `pnl == payout - size`, filled units are known and positive, and `payout <= filled_qty` unless the existing record already carries an explicit prior overpayout marker from a confirmed reconciliation.
- If the record has `needs_reconciliation=true` but no known terminal settlement source, no resolved payout/P&L, missing/invalid cost basis, missing/invalid fill units, or an unapproved overpayout, set `settlement_source="SETTLEMENT_UNKNOWN"`, reset active `payout`/`pnl` to `"UNKNOWN"`, and leave `needs_reconciliation=true` so the normal payout/repair path can resolve it. Do not infer that it is resolved.
- Always preserve the previous flag state under `settlement_flag_repair_previous_state`.

The earlier draft's "placeholder values" phrasing is replaced by this strict schema: real-or-`null`-or-literal-`"UNKNOWN"`, never a synthesized lookalike. `order_id` is either a real client order id or `null`; `venue_order_id` is stored separately and resolved by `--venue-order-id`.

**Transactional requirement:** the durable write must succeed before the actual-fill event is considered handled. If the durable write fails, the bot **fail-stops the process**:

```python
# Pseudocode for the strategy handler's failure branch
try:
    self._create_durable_settlement_unknown_from_actual_fill(...)
except Exception as e:
    # Durable write failed. The ledger cannot be trusted. Fail-stop the bot —
    # propagating the exception back through _dispatch_actual_fill makes the
    # websocket dispatcher surface the error, and the strategy thread will
    # terminate abnormally. The operator MUST inspect and reconcile manually
    # before any restart. Do NOT continue with only process-local block.
    logger.critical(
        f"durable SETTLEMENT_UNKNOWN write failed: {e}. Bot fail-stopping. "
        "Operator must reconcile manually before restart."
    )
    self._block_live_settlement_ledger("durable SETTLEMENT_UNKNOWN write failed")
    raise   # propagate to _dispatch_actual_fill which re-raises to Nautilus
self._block_live_settlement_ledger(...)
```

This matches the settlement-hardening style used elsewhere (`auto_redeem` settlement is also transactional with rollback on save failure). The earlier draft was contradictory — it said the bot stays "process-blocked" if the durable write fails but also said "restart cannot silently clear the pause." Those are inconsistent unless the bot fail-stops. The corrected behavior is unambiguous: durable-write failure = fail-stop = no restart until operator reconciles.

5. **`_record_live_order_fill` prefers the stashed actual values:**

   ```python
   actual_qty = source_meta.get("_actual_filled_qty")
   actual_px = source_meta.get("_actual_fill_vwap")
   if actual_qty is not None and actual_px is not None:
       fill_qty = actual_qty   # 17.460316, not 17.460300
       fill_price = actual_px
   ```

6. **Memory-only gap between `status="ok"` dispatch and `on_order_filled` (reviewer-flagged P0):**

   The callback writes actual values into `_submitted_positions[order_id]` in memory. `_record_live_order_fill` reads them later when Nautilus delivers `on_order_filled`. If the process crashes BETWEEN these two steps — or if Nautilus rejects the (clipped) fill event for any reason and `on_order_filled` is never called — the actual fill data is lost. The on-chain position remains real but the bot has no record.

   **Fix: durable pending-actual-fill record.**

   When the strategy handler receives `status="ok"`, in addition to stashing in `_submitted_positions`, also write a **pending-fill entry to the durable ledger** in a new `_pending_actual_fills` section:

	   ```json
	   {
	       "pending_actual_fills": {
	           "BTC-15MIN-$5-1779...": {
	               "venue_order_id": "0x0638...",
	               "condition_id": "0xd55ee02c...",
	               "token_id": "13493...",
	               "total_filled_qty": "17.460316",
	               "total_filled_notional": "10.99999908",
	               "vwap": "0.63",
	               "fills": [
	                 {
	                   "fill_key": "<real venue trade/match id>",
	                   "filled_qty": "17.460316",
	                   "price": "0.63",
	                   "notional": "10.99999908",
	                   "raw_status_report": {...},
	                   "received_at": "2026-05-18T07:43:05+00:00"
	                 }
	               ]
	           }
	       }
	   }
	   ```

	   The strategy handler MUST write this durable entry BEFORE the in-memory stash. If the disk write fails, fail-stop with `SettlementLedgerError`; do not route to an alternate callback path or continue with only memory state.
	   `submitted_size` in this pending entry follows the same no-fallback rule as durable unknowns: persist it only from `payload["submitted_size"]` or a real `source_meta["submitted_size"]`; never copy `source_meta["size"]`, because open-trade `size` is accounting cost.
	   Phase 0.1 must write the ordered per-fill structure above from the start: one `pending_actual_fills[order_id]` aggregate containing append-only `fills[]`, plus aggregate `total_filled_qty`, `total_filled_notional`, and `vwap = total_filled_notional / total_filled_qty`. A scalar single-fill pending record is not a valid persisted ledger shape; startup/admin validation must reject it fail-closed rather than treating it as an interim shape.
	   Each append MUST use a real venue fill/trade/match identifier or another verified unique event key from the adapter payload. If no unique real fill key is available, fail closed and create/keep durable unresolved state for operator reconciliation; do not synthesize a sequence-only identifier to make the append look unique. If an append would duplicate an existing fill key, fail closed before writing anything. Do not overwrite the existing pending entry because it loses previously verified fill data.

   When `on_order_filled` later arrives and `_record_live_order_fill` consumes the actual values, it also copies the real `venue_order_id` from the pending entry onto the durable open trade, removes the matching entry from `pending_actual_fills`, and persists the cleanup in the same atomic ledger write. This keeps future `--venue-order-id` reconciliation available if that open trade later becomes SETTLEMENT_UNKNOWN.

   On startup, the bot must not convert or remove any existing `pending_actual_fills` entry. A pending actual fill in the ledger means the previous process stopped before `on_order_filled` consumed it; do not wait for an age threshold and do not rewrite it automatically. The live-pause gate must treat every pending actual fill as unresolved and keep live trading blocked until the operator explicitly chooses one pending entry and runs the admin conversion command.

   The explicit admin conversion command turns one operator-selected `pending_actual_fills` aggregate into a durable SETTLEMENT_UNKNOWN record and removes only that selected pending entry in the same atomic ledger write. The pending aggregate must contain non-empty `fills[]`, positive `total_filled_qty`, positive `total_filled_notional`, positive `vwap`, and exact consistency that `total_filled_notional == total_filled_qty * vwap` within the settlement accounting tolerance. The durable unknown must promote those aggregate values into top-level `filled_qty`, `entry_price`, and `filled_notional` fields so the normal `--order-id` / `--venue-order-id --payout` reconciliation path can resolve it. For this actual-fill recovery path, top-level `size` is the actual accounting cost `total_filled_notional`; any callback/order intended spend is preserved only as `submitted_size` audit metadata when present and positive. A partial fill must not book the full intended spend as cost.

   This closes the memory-only gap without migration behavior. Either the fill flows through normally and the pending entry is removed cleanly, OR startup detects the durable pending entry, blocks live trading, and requires explicit operator/admin conversion. No silent loss and no automatic startup state transformation.

   **Fresh ledger schema contract (NO MIGRATION):**

   This is a new-app deployment. Do not support older ledger writer shapes in application code. Do not transform, backfill, upgrade, rename, or rewrite old ledger files. Phase 0.1's canonical ledger schema is `"ledger_schema_version": 3`, where version 3 requires both top-level keys `pending_actual_fills` and `submitted_order_intents`.

   1. **Fresh-schema validation before normal startup**: if `live_trades.json` exists, it must already be schema v3 and must already contain every required top-level section with the required JSON type. Any missing section, wrong `ledger_schema_version`, wrong section type, or malformed entry value is corrupt input and must fail startup/admin tooling with a clear error. Do not add missing sections, do not upgrade a version marker, do not create a backup as part of a transform, and do not silently treat missing keys as `{}` at read time. If no ledger exists, the app may start with empty in-memory ledger state and the first ledger write must create the current v3 shape directly.
   2. **`_save_live_trade_ledger`**: serialize `self._pending_actual_fills` and `self._submitted_order_intents` under the new keys.
   3. **Snapshot/rollback in `_handle_auto_redeem_event`, `_record_live_order_fill`, and order submission**: when computing a candidate ledger state for transactional save, include `pending_actual_fills` and `submitted_order_intents` in the snapshot AND in the rollback restore path. Otherwise a save failure could leave inconsistent in-memory state.
   4. **`mark_settlement_resolved.py`**: when reading the ledger to find unresolved records, treat `pending_actual_fills` entries as another class of unresolved state. Add mandatory `--list-pending-actual-fills` visibility and `--convert-pending-actual-fill <order_id>` so an operator can turn one pending aggregate into SETTLEMENT_UNKNOWN without editing JSON manually. The admin conversion must reject duplicate `venue_order_id` values, resolved or unresolved, so `--venue-order-id` never becomes ambiguous. It must fail closed unless non-empty `fills[]`, positive `total_filled_qty`, positive `total_filled_notional`, and positive `vwap` are present and cost-consistent. It must set accounting `size = total_filled_notional` while preserving `submitted_size` only as audit metadata when present and positive.
   5. **Atomic JSON write**: the same temp-file + os.replace pattern already used must include the new key in the JSON payload.

   Acceptance criteria for this fresh-schema contract:
   - [ ] No application or admin-tool code migrates, backfills, upgrades, renames, or rewrites old ledger shapes.
   - [ ] Any existing `live_trades.json` that is not already exact schema v3 fails startup/admin tooling before normal operation or rewrite.
   - [ ] Any `ledger_schema_version` other than `3` fails startup/admin tooling; a `ledger_schema_version=3` file missing any required key also fails as corrupt.
   - [ ] A round-trip save/load preserves `pending_actual_fills` and `submitted_order_intents` byte-identical (modulo dict key ordering).
   - [ ] Snapshot/rollback test: simulate a save failure mid-transaction; assert `pending_actual_fills` and `submitted_order_intents` state is restored to pre-mutation values.
   - [ ] The admin tool can identify orders that have `pending_actual_fills` entries and convert one explicit pending entry into SETTLEMENT_UNKNOWN.
   - [ ] Startup with any `pending_actual_fills` entry keeps live trading blocked and preserves the pending entry unchanged.
   - [ ] An admin-converted pending actual fill aggregate with positive `total_filled_qty`, positive `total_filled_notional`, positive `vwap`, and non-empty `fills[]` can be resolved through the normal `--venue-order-id --payout` flow without manual JSON edits.
   - [ ] Admin conversion refuses duplicate `venue_order_id` records.
   - [ ] Admin conversion refuses pending actual fills with missing/non-positive `total_filled_qty`, missing/non-positive `total_filled_notional`, missing/non-positive `vwap`, empty `fills[]`, inconsistent aggregate math, or scalar-only `filled_qty` records.
   - [ ] Admin pending-actual-fill conversion books actual partial-fill cost as `total_filled_notional`, not full intended/submitted spend.
   - [ ] Multi-partial actual fills append to ordered `fills[]` with real unique fill keys and update aggregate totals atomically; scalar overwrite is forbidden.
   - [ ] A duplicate or keyless `status="ok"` actual-fill callback for an order with an unconsumed `pending_actual_fills` entry blocks/fails, preserves the existing `fills[]` aggregate unchanged, and durably marks that pending entry `requires_external_fill_repair=true` with the duplicate/keyless raw callback evidence.
   - [ ] The admin tool can identify submitted YES/NO intents and resolve exactly one explicit intent after operator verification.

7. **Lifecycle: unregister handler on `on_stop`.**

   Mirror the existing `auto_redeem` pattern: register in `on_start`, unregister in `on_stop`. Without this, multiple test runs or restarts in the same process can stack callbacks, causing duplicate dispatches.

   ```python
   def on_stop(self):
       # ... existing cleanup ...
       try:
           from patch_market_orders import unregister_actual_fill_handler
           unregister_actual_fill_handler(self._actual_fill_handler)
       except Exception:
           logger.exception("failed to unregister actual-fill handler")
           raise
   ```

   Add a regression test that calls `on_start`, then `on_stop`, then asserts the handler registry no longer contains the strategy handler. Duplicate handler registration across restart/test cycles is a hard failure, not log noise.

**Ledger preservation:** the bot's durable ledger records the **actual Polymarket filled units** (`17.460316` in the production case), guaranteed by the side-channel above. The clipping is local to the adapter/report path so Nautilus' downstream overfill arithmetic is satisfied. Without the side-channel, the ledger would silently record the clipped value — that contradiction is now resolved by explicit data flow.

#### 0.5 — `quote_quantity=True` units-mismatch overfill (REQUIRED for $5+ trades)

**Reviewer-flagged from the sanitized production-log excerpt:** Phase 0.4 above handles tiny token-dust overfills (`0.000085`-class). But a second overfill error fires several seconds **earlier** for the same trade, with a much larger `potential_overfill` value. From the operator's $55 trade in that log:

```
10:43:38.26 ERROR: potential_overfill=21.388885, last_qty=76.388885, quantity=55.00
```

Root cause: the order was submitted with `quote_quantity=True, quantity=55.00` (meaning "spend $55 USDC"). Nautilus stores `quantity=55.00` in the order object as base units. When the trade event arrives reporting `size=76.388885 tokens` ($55 ÷ $0.72), Nautilus' overfill check compares `last_qty (76.388885 tokens) > order.quantity (55.00 USDC)` and computes a spurious overfill of `21.388885`. **The two values are in different units.** Not a real overfill.

This fires BEFORE the venue's `OrderAccepted` / `OrderUpdated` events arrive and correct `quantity` to the token amount. The dust-tolerance check in 0.4 can't help because `21.388885 >> 0.001`.

**Why this blocks larger trades specifically:** the magnitude of the spurious overfill scales with token quantity. At `MARKET_BUY_USD=1` and ask=$0.72, `tokens = 1.39`, overfill = 0.39 — still might trigger but small. At `MARKET_BUY_USD=55` and ask=$0.72, `tokens = 76.39`, overfill = 21.39 — large and guaranteed to trip the check. Lower target prices make it worse (more tokens per dollar). This is why the lost-fill class shows up at the larger trade sizes the operator is now using.

**Fix (single deterministic path, no global flag, no fallback path):**

Patch the Polymarket adapter's websocket trade-event path so that, before any overfill check compares `last_qty` to `order.quantity`, the order's quantity field has been translated from USDC to tokens.

Exact installed Nautilus hook: patch around `PolymarketExecutionClient._handle_user_trade_in_ws_trade_msg(...)`, immediately before it calls `self.generate_order_filled(...)` with `last_qty=instrument.make_qty(msg.last_qty(order_id))`. Do not patch only the later status-report path; the first `$55` failure occurs in this websocket fill path before later order-status reconciliation can repair it.

When a `MATCHED` trade event arrives for an order submitted with `quote_quantity=True`, the patch checks whether Nautilus' cached order still has `quote_quantity=True` and `quantity` matching the original USDC value. If so, it computes the token-denominated order quantity from a **cumulative** source, not just the first fill's `last_qty`, and dispatches an `OrderUpdated` event to Nautilus FIRST, updating `quantity` to that cumulative token amount, BEFORE `generate_order_filled(...)` runs.

The cumulative quantity source must be one of these exact verified sources, chosen during implementation after reading the installed websocket message schema:

1. A total matched / order-size field in the same websocket trade message, if present and verified to mean cumulative token quantity for that venue order id.
2. The cumulative sum of all unprocessed plus already-processed websocket fills for that `venue_order_id`, maintained in a tracked per-order map keyed by venue order id, and updated before each `generate_order_filled(...)` call.

The implementation PR must choose exactly one source and document the verified message fields or state map. Do not implement a runtime fallback between sources.

Do not use the first fill's `last_qty` as the translated order quantity. A market order can fill across multiple trades before the venue's delayed `OrderUpdated` arrives; setting quantity to the first partial fill would recreate the overfill on the second partial fill.

In practice this means hooking the trade-event handler in the Polymarket adapter so that for quote-quantity orders, every pre-`OrderUpdated` trade event refreshes the cached order quantity to the cumulative matched token quantity before fill processing. The venue already produces an `OrderUpdated` event with the correct token quantity — the issue is that it can arrive seconds after the first trade event, AFTER the overfill check has already rejected the fill. The patch must reorder so the quantity translation happens first.

If this event-reordering path cannot be implemented safely, Phase 0.5 is blocked and the plan must be amended with one exact replacement design for operator approval. Do not add a per-order overfill bypass, delayed reconciliation path, or any other alternative implementation without explicit approval for that exact code path.

**Interaction with 0.4:** after the order's quantity has been translated to tokens via the event-reordering patch, the dust check from 0.4 becomes the operative protection. Any tiny mismatch between `size_matched` (76.388885) and the translated `order.quantity` (76.388800 from the venue) gets clipped per 0.4. The two phases are complementary: 0.5 fixes the units mismatch BEFORE the first overfill check; 0.4 fixes the residual dust AFTER the venue's quantity update.

**Tests (added to Phase 0.6):**

- Submit market BUY with `quote_quantity=True, quantity=$55`. Simulate matched trade `size=76.388885 tokens`. Assert NO overfill error fires; the trade is recorded with `fill_qty=76.388885` via the actual-fill callback.
- Multi-partial fill: submit market BUY with `quote_quantity=True, quantity=$55`. Simulate two `MATCHED` websocket events before any venue `OrderUpdated`: first `30` tokens, then `46.388885` tokens. Assert no overfill fires on either event, the cached translated quantity is cumulative before each `generate_order_filled(...)` call, and the ledger records cumulative actual units `76.388885`.
- Submit market BUY with `quote_quantity=True, quantity=$100`. Simulate matched trade at a different price (e.g., `size=200 tokens` at $0.50). Same assertion — no overfill error, correct token quantity in ledger.
- Edge case: submit market BUY with `quote_quantity=False, quantity=10 tokens`. Verify the existing token-quantity path is unaffected by the new patch.

**Effort:** 1 day.

#### 0.5a — ULTRA CRITICAL: Nautilus 1.227.0 dependency audit for one-sided quote drops

The repeated `DataClient-POLYMARKET: Dropping QuoteTick ... bid_price=0.99, ask_price=None` / `bid_price=None, ask_price=0.01` warnings are no longer treated as harmless log noise. If they are only for rolled-over old instruments, they are operationally noisy but not trade-blocking. If they are for the current active 15-minute YES/NO instruments, the adapter drops the quote before `on_quote_tick`, which can starve `price_history`, YES/NO cache updates, and late-window trade decisions. That can reduce trade frequency and therefore belongs on the Phase 0.5 critical path.

Current repo pin: `nautilus_trader==1.222.0`.

Audit target: `nautilus_trader==1.227.0` (released May 18, 2026; beta-labeled release). Do not describe this as "stable." This upgrade may introduce new dependencies or API changes, so it is not a blind requirements bump. It must be tested in an isolated branch/env and any code updates must be explicit. Re-check the latest available release at implementation time before changing the pin.

Required audit before Phase 0.5 is marked complete:

1. Install/test `nautilus_trader==1.227.0` in a clean environment without modifying the production venv in place.
2. Record dependency/API changes needed by this repo (`TradingNode`, Polymarket config/factories, order factory calls, execution/data adapter signatures).
3. Verify whether 1.227.0 still contains any `ClientOrderId(str(UUID4()))` fallback in Polymarket execution paths.
4. Verify whether 1.227.0 still has the `quote_quantity=True` websocket trade-event units mismatch that Phase 0.5 patches.
5. Verify whether FILLED status-report paths can still emit `avg_px is None`.
6. Verify whether Polymarket unsubscribe support is now present and usable for old 15-minute instruments.
7. Measure whether one-sided quote drops occur for the **current active** BTC 15-minute instruments, not only rolled-over instruments.
8. Confirm whether current Polymarket websocket subscription-update behavior changed relative to the installed adapter.

Do **not** set `drop_quotes_missing_side=False` as a quick fix. That substitutes boundary prices with zero volume and is a data-substitution fallback under `AGENTS.md`; it requires a separate exact operator approval if ever proposed. Do not suppress the warning as a fix unless metrics prove it only affects non-current instruments and no current-instrument quote starvation occurs.

Decision after audit:

- If Nautilus 1.227.0 removes the UUID fallback, fixes quote-quantity units handling, and supports unsubscribe cleanly, prefer an explicit dependency upgrade plus required code changes over local adapter patching.
- If 1.227.0 does not fix the Phase 0.5 bug, keep the tracked Phase 0.5 patch plan and add a separate current-instrument one-sided-quote handling task.
- If 1.227.0 introduces dependency/API churn that blocks quick adoption, document the blockers and keep Phase 0.5 local patching on the critical path.

Acceptance criteria:

- [ ] A clean-env Nautilus 1.227.0 smoke test report is added to the plan or a tracked incident note.
- [ ] The report explicitly answers UUID fallback, quote-quantity units mismatch, missing `avg_px`, unsubscribe support, and current-instrument one-sided quote-drop behavior.
- [ ] Any required code changes for 1.227.0 are listed before implementation begins.
- [ ] No fallback quote synthesis, warning suppression, or dependency upgrade is shipped without tests proving equivalent or stricter behavior.

**Effort:** TBD after clean-env dependency resolution.

##### Installed adapter baseline (Nautilus 1.222.0)

Probed at implementation time against the repo's installed `nautilus_trader==1.222.0`:

- All four Phase 0 target methods are present on `PolymarketExecutionClient`:
  `generate_order_status_report`, `generate_order_status_reports`,
  `_handle_user_trade_in_ws_trade_msg`, `_parse_trades_response_object`.
- `ClientOrderId(str(UUID4()))` client-id fallback occurrences in installed
  source (note: `UUID4()` is also used in installed Nautilus for
  `command_id=` and `report_id=` — those are NOT client-id fallbacks and are
  not in scope for Phase 0.4):
  - `generate_order_status_reports`: **2 sites** — the active-order loop
    (line ~420 in installed source) and the
    `generate_order_history_from_trades` branch (line ~522).
  - `_parse_trades_response_object`: **1 site** (line ~741).
  - `generate_order_status_report` and `_handle_user_trade_in_ws_trade_msg`:
    none observed in the method bodies (the venue→client lookup is upstream).
  - **Total Phase 0.4 patch sites: 3**, matching Phase 0.4's required UUID
    guard count.
- No method body inserts a default `avg_px` itself; the missing-`avg_px` defect
  observed in production comes from upstream Nautilus inferring fills from a
  FILLED report whose `avg_px` is `None`.

This baseline confirms Phase 0.4's three required UUID-guard sites and Phase
0.3's hook points. It does **not** substitute for the clean-env 1.227.0 audit
required above; it only records what the installed 1.222.0 adapter looks like
today so the patch implementer has a precise starting point.

##### Clean-env audit findings — Nautilus 1.227.0 (operator-requested upgrade)

Audit performed against the 1.227.0 wheel downloaded into an isolated
`/tmp/nautilus-1227-audit/extracted/` directory (production venv untouched, per
the plan's "clean environment" requirement).

| Phase 0 defect | 1.222.0 | 1.227.0 | Source evidence (1.227.0) |
| --- | --- | --- | --- |
| 0.3: `avg_px = None` on FILLED report | Present | **FIXED** | `generate_order_status_report` line 698 calls `_weighted_average_price(fills, total_filled)` (imported from `nautilus_trader.adapters.polymarket.schemas.user`). When `not fills`, the status becomes `CANCELED` with `avg_px = None` — no inferred fill triggered. |
| 0.4: token-dust overfill rejection | Present | **FIXED** | `_handle_user_trade_in_ws_trade_msg` calls `self._fill_tracker.snap_fill_qty(venue_order_id, raw_last_qty)` using `DUST_SNAP_THRESHOLD_DEC`. Same snap also applied to the cached-order quantity in `generate_order_status_report` via `_snap_filled_qty_to_quantity`. |
| 0.5: `quote_quantity=True` units mismatch | Present | **FIXED** | `_submit_market_order` (around line 1779) for BUY quote-quantity orders computes `base_quantity = signed_order.takerAmount / 1e6` and passes `base_quantity=` + `expected_venue_order_id=` through `_post_signed_order`. The adapter now knows the cached order's base-token quantity at submission time, so the websocket trade-event overfill check compares matching units. |
| 0.4: `ClientOrderId(str(UUID4()))` client-id fallback | 3 sites | **STILL PRESENT in 1.227.0; PATCHED in tracked code** | Sites unchanged in installed source (lines 431, 534, 879). Tracked Phase 0.4 in-tree patch ``apply_uuid_fallback_guard_patch`` in ``patch_market_orders.py`` replaces all 3 sites with ``_dispatch_actual_fill(None, {status: failed, reason: unmapped_venue_order_id, ...})`` plus a `continue`. ``verify_no_nautilus_client_order_id_uuid_fallback`` then passes on the patched method source. Live startup is unblocked. Patch is pinned to nautilus_trader 1.227.0 and refuses to apply on any other version. |
| 0.5a: Polymarket unsubscribe support | Limited | **PRESENT** | `_unsubscribe_order_book_deltas`, `_unsubscribe_quote_ticks`, `_unsubscribe_trade_ticks`, `_unsubscribe_bars` defined in `data.py` lines 484–509. |
| 0.5a: one-sided quote drops on current instruments | Present | **Unchanged** | `data.py` lines 648, 746 keep the same `drop_quotes_missing_side` behavior. Mitigation: keep the config default `True` for now; operator can observe metrics under 1.227.0 before flipping. |

API/dependency changes required to upgrade this repo to 1.227.0:

1. **`requirements.txt`** — bump `nautilus_trader==1.222.0` → `nautilus_trader==1.227.0`. `py-clob-client-v2==1.0.1` is already pinned and remains required (1.227.0 declares it via `Requires-Dist: py-clob-client-v2 (>=1.0.1,<2.0.0); extra == "polymarket"`). The old `py_clob_client==0.34.5` is kept because admin scripts
   (`check_polymarket_balance.py`, `derive_polymarket_api_creds.py`,
   `approve_polymarket_clob.py`) still import from it.
2. **`bot.py` config field rename** — `PolymarketDataClientConfig` and
   `PolymarketExecClientConfig` both renamed `instrument_provider` →
   `instrument_config`. Update both call sites in `run_integrated_bot`.
3. **`polymarket_v2_compat.py`** — the existing compat shim was written to make
   1.222.0's adapter use `py-clob-client-v2`. 1.227.0 natively imports from
   `py_clob_client_v2.client`, so the shim's main purpose disappears. Keep the
   shim's call site for now but treat its `apply_polymarket_v2_patch()` as a
   no-op-or-already-applied path under 1.227.0.
4. **`patch_market_orders.py`** — the `_submit_market_order` monkey patch was
   the workaround for the missing-`base_quantity` defect. 1.227.0 fixes this
   natively. The patch is still wired to `auto_redeem` dispatch and the actual
   fill handler scaffold, which remain valuable. Consider trimming the
   `_submit_market_order` replacement after live smoke testing confirms the
   1.227.0 native path works.
5. **`verify_no_nautilus_client_order_id_uuid_fallback`** — still required.
   1.227.0 retains the 3 UUID4 client-id fallback sites. Live startup remains
   blocked until those are patched/guarded or upstream removes them.

Plan decision (per the section's "Decision after audit" rubric):
- 1.227.0 fixes Phase 0.3, 0.4 (dust), and 0.5 natively → **dependency
  upgrade plus the compatibility update steps above is SHIPPED** in
  ``requirements.txt`` (`nautilus_trader==1.227.0`) and ``bot.py``
  (`instrument_provider` → `instrument_config` rename). This is dependency/API
  compatibility work, not a ledger, config, or runtime-state migration.
- 1.227.0 does NOT remove the UUID fallback → **in-tree patch SHIPPED** as
  ``apply_uuid_fallback_guard_patch`` in ``patch_market_orders.py`` (replaces
  the 3 UUID4 client-id synthesis sites with `_dispatch_actual_fill` +
  `continue`). ``verify_no_nautilus_client_order_id_uuid_fallback`` passes on
  the patched class. The patch is pinned to ``nautilus_trader==1.227.0`` so
  any future version bump triggers a fresh Phase 0.5a audit instead of
  silently re-using stale verbatim method bodies. Phase 0.6 regression
  coverage: ``test_zz_uuid_fallback_guard_patch_lets_verify_pass`` and
  ``test_zz_patched_parse_trades_dispatches_failure_on_unmapped_venue``.
- 1.227.0 does NOT change `drop_quotes_missing_side` → **no flag flip; observe
  metrics on the upgraded build before any change to current-instrument quote
  handling**.

Two intentional deviations from upstream 1.227.0 source in
``apply_uuid_fallback_guard_patch``:
- **Site 2 cache lookup.** Upstream unconditionally synthesizes a UUID in the
  ``generate_order_history_from_trades`` branch; the patch consults
  ``self._cache.client_order_id(venue_order_id)`` first and reuses the real
  client_order_id when available. When the cache is empty, the patch fails
  closed via ``_dispatch_actual_fill`` instead of synthesizing. Strictly
  safer than upstream and consistent with the No-Fallback policy.
- **`command` shadowing preserved.** The patch uses the same
  ``command = GenerateOrderStatusReport(...)`` reassignment inside the loop
  as upstream, so the subsequent
  ``fill_command = GenerateFillReports(instrument_id=command.instrument_id, ...)``
  references the last sub-command's instrument_id (or the outer command's
  when the loop runs zero iterations). This matches upstream semantics
  exactly.

#### 0.6 — Regression tests

Add to `tests/test_simulation_mode_safety.py` (or a new `tests/test_polymarket_fill_normalization.py`):

1. **Test: dust overfill is accepted, ledger preserves actual filled units.** Simulate an order for 17.460300 tokens and a report with `size_matched=17.460316`. Mock `client.get_trades(...)` to return one matching trade `[{taker_order_id: <venue_id>, price: "0.63", size: "17.460316"}]`. Assert the actual-fill callback fires with `status="ok"`, `filled_qty=Decimal("17.460316")`, `vwap=Decimal("0.63")`. Assert `_record_live_order_fill` is called with `fill_qty=17.460316` (the actual Polymarket filled units, preserved via the side-channel).
2. **Test: real overfill is rejected AND dispatches durable failure.** Submit for 17.460300, simulate fill of 18.0, assert the fill is rejected by Nautilus' normal path AND the actual-fill callback fires with `status="failed"`, `reason="real_overfill_rejected"`. Assert a durable SETTLEMENT_UNKNOWN entry is created for the venue order id so the bot stays paused across restart.
3. **Test: missing avg_px with `get_trades` match succeeds in every status-report path.** Simulate a filled report with `avg_px=None` and mock `client.get_trades(...)` returning a matched trade. Cover both `generate_order_status_report(...)` and `generate_order_status_reports(...)` FILLED report creation paths. Assert the actual-fill callback fires with `status="ok"` and the correct VWAP. Assert the inferred fill carries the matched price.
4. **Test: missing avg_px with no `get_trades` match blocks ledger.** Simulate the same condition but with `client.get_trades(...)` returning no match. Assert the actual-fill callback fires with `status="failed"`, `reason="no_matching_trade"`. Assert `_settlement_ledger_blocked_reason` is set and no fill is committed.
5. **Test: zero fill_price guard.** Call `_record_live_order_fill(order_id, fill_price=Decimal("0"), fill_qty=Decimal("17"))` directly, assert returns False, asserts `_settlement_ledger_blocked_reason` is set. This test exercises the last-line-of-defense guard independently of the adapter callback.
6. **Test: installed Nautilus UUID fallback is disabled.** Simulate missing `self._cache.client_order_id(venue_order_id)` in each patched report path and assert no `ClientOrderId(str(UUID4()))` and no synthetic `venue:<hash>` order id is produced. Assert the adapter dispatches `client_order_id=None`, `status="failed"`, `reason="unmapped_venue_order_id"`, includes the real `venue_order_id`, and returns `None` / skips append.
7. **Test: quote-quantity units mismatch is normalized before overfill check.** Simulate a market BUY with `quote_quantity=True, quantity=$55` and first matched trade `size=76.388885 tokens`. Assert the quantity-translation update happens before fill processing, no overfill error fires, and the ledger records the actual token quantity via the side channel.
8. **Test: quote-quantity multi-partial fill is cumulative.** Simulate a market BUY with `quote_quantity=True, quantity=$55` and two websocket `MATCHED` events before venue `OrderUpdated`: `30` tokens then `46.388885` tokens. Assert both `generate_order_filled(...)` calls avoid overfill, `pending_actual_fills[order_id].fills[]` contains two ordered real-keyed fill entries before consumption, aggregate actual units total `76.388885`, and the final ledger record preserves the aggregate actual units and VWAP.

All tests mock `client.get_trades(TradeParams(...))`, **NOT** `get_trade_history` (that method does not exist).

#### 0.7 — Recover the lost trade (done last, after 0.1-0.6 are merged)

**Operational prerequisites:**

- **Stop the bot first.** Run `kill -TERM <pid>` against both the wrapper and `bot.py`. Confirm no `python bot.py` process remains. The admin tool acquires the `live_trades.json.lock` and refuses to run while the bot holds it.
- **Use the venv Python.** Always `venv/bin/python mark_settlement_resolved.py`, never system `python`. The admin tool depends on the same `Decimal` discipline as the bot.
- **Pass `--ledger` explicitly on every admin-tool invocation.** The plan no longer approves the `LIVE_TRADE_LEDGER_PATH` / `./live_trades.json` default chain for reconciliation tooling. Any mutating action must fail argument parsing if `--ledger` is omitted, so an operator cannot accidentally mutate the wrong ledger. Read-only list actions must also require `--ledger`, so the inspected file and mutated file are always the same explicit path.

**Command (operator must fill in real values, not placeholders):**

```bash
venv/bin/python mark_settlement_resolved.py \
  --ledger /path/to/live_trades.json \
  --create-unknown-from-external-order 'BTC-15MIN-$11-1779093783343' \
  --confirm-external-order \
  --external-size 10.99999908 \
  --external-entry-price 0.63 \
  --external-filled-qty 17.460316 \
  --external-direction short \
  --external-trade-label "NO (DOWN)" \
  --external-instrument-id "0xd55ee02c5080428bab05c89cf861e347c3a94306d8e0c999b87068f3145b7b2b-13493549868196599328875182057608566347488679426659046670938216865177784647005.POLYMARKET" \
  --external-token-id 13493549868196599328875182057608566347488679426659046670938216865177784647005 \
  --external-slug "<derive from Polymarket order/condition record for condition_id 0xd55ee02c... — do NOT guess>" \
  --external-condition-id 0xd55ee02c5080428bab05c89cf861e347c3a94306d8e0c999b87068f3145b7b2b \
  --external-submitted-at "<actual ISO timestamp with tz, e.g. 2026-05-18T07:43:03+00:00>" \
  --external-filled-at "<actual ISO timestamp with tz, e.g. 2026-05-18T07:43:05+00:00>" \
  --external-market-end-time "<actual ISO timestamp with tz, e.g. 2026-05-18T07:45:00+00:00>" \
  --reason "Nautilus dropped fill event due to missing avg_px and 0.000016 token dust overfill"
```

**Operator must replace every `<...>` placeholder with the real value from Polymarket UI / bot logs.** Do not run the command as written — the slug, instrument-id full form, and timestamps must come from the actual trade.

Note: the validation requires exact cost consistency within the settlement accounting tolerance. Do not round the notional to `11.00`. With the observed values above, `0.63 * 17.460316 = 10.99999908`, so `--external-size` must be `10.99999908`. If the verified notional from Polymarket is exactly `11.00`, derive and use the precise average entry price as `11.00 / 17.460316` instead of using rounded `0.63`.

**Live-trading pause expectation:** creating a `SETTLEMENT_UNKNOWN` record activates the live-trading pause gate in `bot.py` (`_unresolved_settlement_unknowns` and the live-start guard). Live trading remains paused until the second step (`--order-id ... --payout ...`) resolves the unknown with a verified payout. This is intentional — Phase 0 does not "unblock" live trading by itself; it only prepares the ledger for a clean second-step resolution.

**Do NOT wait for `auto_redeem` on a market that has already resolved.** The lost trade's market has already ended in production. Polymarket's websocket does not replay missed `auto_redeem` events on reconnect. If the bot was down or rejected the event when it fired, it is gone. The operator must:

1. Open the Polymarket UI (or query the on-chain redemption record) and verify the actual payout for the affected token holding.
2. Resolve the SETTLEMENT_UNKNOWN record immediately with the verified payout — do not wait for the grace timeout to fire and book a $0 loss.

```bash
venv/bin/python mark_settlement_resolved.py \
  --ledger /path/to/live_trades.json \
  --order-id 'BTC-15MIN-$11-1779093783343' \
  --payout <verified-payout-from-Polymarket-UI> \
  --reason "Verified from Polymarket UI after lost-fill incident"
```

Use the same `venv/bin/python` and the same `--ledger` path as the prerequisite above. Do not use system `python`.

### Exit criteria

- [ ] Lost trade reconstructed in `live_trades.json` via `--create-unknown-from-external-order`.
- [ ] **Lost trade fully resolved.** Operator has run `--order-id BTC-15MIN-\$11-... --payout <verified> --reason ...` with the externally verified payout. The ledger entry now has `settlement_source: "manual_reconciliation"` and `needs_reconciliation: false`.
- [ ] **No remaining live-blocking unresolved state for this order.** Reconstruction alone is not sufficient: the live-trading pause gate checks settled-record OR semantics (`needs_reconciliation is True` or `settlement_source == "SETTLEMENT_UNKNOWN"`) plus `pending_actual_fills`, unresolved `submitted_order_intents`, and `LEDGER_BLOCKED`. Confirm this order has no matching unresolved settled record, pending actual fill, or submitted intent before live smoke testing.
- [ ] avg_px present in fill events for new live `LIMIT_IOC` BUYs via the deterministic `get_trades` path (verify with one minimum allowed live smoke trade, e.g. `$5.51`, only after the lost trade is fully resolved and mandatory Phase 3 + Phase 5B are complete).
- [ ] Token-dust overfill within tolerance no longer rejected; ledger preserves actual filled units via the side-channel.
- [ ] Real overfill (outside tolerance) dispatches `status=failed, reason=real_overfill_rejected` and creates a durable SETTLEMENT_UNKNOWN entry that survives restart.
- [ ] `_record_live_order_fill` refuses non-positive `fill_price`.
- [ ] All Phase 0 regression tests pass, including the installed Nautilus UUID-fallback guard and quote-quantity units-mismatch case.
- [ ] One deliberately tiny allowed live `LIMIT_IOC` smoke trade (`ORDER_TYPE=limit_ioc`, `MARKET_BUY_USD=5.51`, `QUOTE_STABILITY_REQUIRED=3`, `LIMIT_IOC_FILL_POLICY=partial_ok`) has been placed and observed to flow cleanly through: order submitted → actual-fill callback fires with `status="ok"` and matching VWAP → ledger records actual filled units → `auto_redeem` resolves → final ledger entry shows correct payout and P&L.

### Effort

3 days plus the Phase 0.5a clean-env Nautilus 1.227.0 audit. The Polymarket adapter patch (0.3, 0.4, 0.5) is the longest code piece because it requires reading Nautilus's internal report path and crafting a minimal monkey patch. The 0.5a audit may reduce or reshape that patch if 1.227.0 fixes adapter behavior, but it must be verified before live trading resumes.

---

## Phase 1 — Environment Variable Audit & Documentation

**Status:** High. Several env vars listed in README / current env docs don't actually do anything. Operator may be tuning values that have zero effect. (`.env.example` now exists; Phase 3 must update it for mandatory `ORDER_TYPE`, `QUOTE_STABILITY_REQUIRED`, and `LIMIT_IOC_FILL_POLICY` semantics.)

### Currently wired (read from `os.environ` per decision/tick)

Important distinction: these are read **from the running process's environment** each time, not re-read from the `.env` file. Editing `.env` on disk after the bot starts does **not** propagate. To change these at runtime you must restart the process (which re-loads `.env`) or modify `os.environ` from inside the process.

| Env var | Where read | Reload behavior |
|---|---|---|
| `MARKET_BUY_USD` | `get_market_buy_usd()` at every trade decision | Per-decision `os.getenv` (process env, not disk) |
| `MIN_SIGNAL_CONFIDENCE` | `get_min_signal_confidence()` per decision | Per-decision `os.getenv` |
| `REQUIRE_SIGNAL_CONFIRMATION` | `get_env_bool` per decision | Per-decision `os.getenv` |
| `REQUIRE_AUTO_REDEEM_TOKEN_HINT` | per `_handle_auto_redeem_event` | Per-event `os.getenv` |
| `EV_FEE_BUFFER`, `EV_SPREAD_BUFFER` | per decision | Per-decision `os.getenv` |
| `LIVE_SETTLEMENT_GRACE_SECONDS` | per timer tick | Per-tick `os.getenv` |

### Currently wired (read once at startup)

| Env var | Where read | Reload behavior |
|---|---|---|
| `MAX_POSITION_SIZE` | `RiskEngine.__init__` | Restart required |
| `MAX_TOTAL_EXPOSURE` | `RiskEngine.__init__` | Restart required |
| `MAX_POSITIONS` | `RiskEngine.__init__` | Restart required |
| `MAX_DRAWDOWN_PCT` | `RiskEngine.__init__` | Restart required |
| `MAX_LOSS_PER_DAY` | `RiskEngine.__init__` | Restart required |
| `POLYMARKET_*` | once during node construction | Restart required |
| `REDIS_*` | `init_redis()` | Restart required |
| `POLYGON_RPC_URL` | only by `approve_polymarket_clob.py` | N/A to bot |

### Listed in docs but NOT wired

| Env var | Documented as | Actual behavior |
|---|---|---|
| `STOP_LOSS_PCT` | "30% stop loss" in README | **Not read.** Risk engine has `_check_stop_loss` but is never called in live flow. |
| `TAKE_PROFIT_PCT` | "20% take profit" in README | **Not read.** Same. |
| `SPIKE_THRESHOLD` | spike detector threshold | **Hardcoded in `bot.py` near the strategy configuration constants to 0.05.** Env value ignored. |
| `DIVERGENCE_THRESHOLD` | divergence detector threshold | **Hardcoded in `bot.py` to 0.05.** Env value ignored. |

**Current-env operational warning:** if the live `.env` currently contains `STOP_LOSS_PCT`, `TAKE_PROFIT_PCT`, `SPIKE_THRESHOLD=0.15`, or `DIVERGENCE_THRESHOLD=0.05`, those lines do not change runtime behavior today. `STOP_LOSS_PCT` and `TAKE_PROFIT_PCT` do nothing; `SPIKE_THRESHOLD=0.15` is ignored and the code uses `0.05`; `DIVERGENCE_THRESHOLD=0.05` happens to match the hardcoded value but is still ignored. Do not treat those env values as active risk controls until Phase 1.1 either wires them as required env vars or removes them from docs/env files.

### Fix scope

#### 1.1 — Decision: wire or remove

For each unwired var, choose:

- **Wire it.** Read in the relevant `__init__` or `process()` call. Add validation (range, type).
- **Remove it.** Strike from README. Add a comment in code where it would have been wired.

Recommendation:
- `STOP_LOSS_PCT` / `TAKE_PROFIT_PCT`: **remove from README**. The bot has no per-position monitoring loop and Polymarket doesn't support stop orders. Wiring these would require building a polling exit loop — out of scope.
- `SPIKE_THRESHOLD` / `DIVERGENCE_THRESHOLD`: **wire** OR keep code-owned — operator decision required, no in-between. The plan must NOT use `os.getenv("SPIKE_THRESHOLD", "0.05")`-style implicit defaults. Two acceptable options:
  - **Option A (wire as required env):** Read via `_get_required_env_decimal("SPIKE_THRESHOLD")` at strategy init. Missing or invalid value raises and the bot refuses to start. Operator must set it explicitly in `.env`.
  - **Option B (keep code-owned constant):** Remove the env var from docs entirely. The value stays as a constant in `IntegratedBTCStrategy.__init__`. Operator changes it by code edit + restart, not by env.
  - **Pick one per variable.** Do not ship a hybrid where the env var works if set but a hardcoded default kicks in if missing. That is exactly the silent-fallback pattern `AGENTS.md` prohibits.

#### 1.2 — Env-read documentation in README

Add a clear table in README distinguishing per-decision-read vs startup-read env vars. Operator must know which require restart and that **editing `.env` while running has no effect** — `load_dotenv()` runs once at startup.

#### 1.3 — Create `.env.example`

**Note:** `.env.example` exists in the repo. Keep it current as live-order semantics change. It should include:
- All env vars actually wired (per the tables above)
- Comments next to each var marking per-decision vs startup
- Recommended default values where appropriate
- Required-vs-optional marking

### Exit criteria

- [ ] `.env.example` exists in the repo root with all wired env vars documented and labeled.
- [ ] README has a "Environment Variables" section with per-decision-read vs startup-read clearly marked.
- [ ] README explicitly states that editing `.env` does not propagate at runtime — restart required.
- [ ] `SPIKE_THRESHOLD` / `DIVERGENCE_THRESHOLD` wired or struck from docs.
- [x] `STOP_LOSS_PCT` / `TAKE_PROFIT_PCT` struck from docs with a note explaining why they're not implemented.

### Effort

0.5 day.

---

## Phase 2 — Suggested Live Trade Sizing Config

**Status:** Operational. Not a code change — a config recommendation for the operator.

### For $385 active-exposure cap (operator-confirmed targets)

```env
MARKET_BUY_USD=55.00
MAX_POSITION_SIZE=55.00
MAX_TOTAL_EXPOSURE=385.00
MAX_POSITIONS=7
MAX_LOSS_PER_DAY=110.00
MAX_DRAWDOWN_PCT=0.15
```

Behavior:
- Each trade spends exactly $55.00 (fixed mode — see Phase 2.5 for dynamic percent-of-balance mode).
- Up to 7 concurrent unsettled positions (7 × $55 = $385 maximum concurrent exposure).
- Stops new trades after $110 realized daily loss (~2 full position-size losses).
- Drawdown limit relative to bot's internal $1000 starting balance — 15% = ~$150. With $110 daily loss limit, daily cap will trip first under normal loss patterns.

### Smoke-test-only config

```env
MARKET_BUY_USD=5.51
MAX_POSITION_SIZE=5.51
MAX_TOTAL_EXPOSURE=22.04
MAX_POSITIONS=4
MAX_LOSS_PER_DAY=11.02
MAX_DRAWDOWN_PCT=0.15
```

Same shape, slightly above the strict `MARKET_BUY_USD > 5.50` live gate and ~1/10 the normal exposure. This is smoke-test-only: use it for the first live `LIMIT_IOC` smoke trade only after Phase 0.7 recovery, mandatory Phase 3, and Phase 5B are complete. Smoke uses `ORDER_TYPE=limit_ioc` unless the operator explicitly approves a separate `market_ioc` risk test; then scale to the intended normal `$55 / $385 / 7 positions / $110 daily loss` config after the smoke trade settles cleanly.

### Important constraints

- `MAX_TOTAL_EXPOSURE` is open exposure, **not** a daily loss cap. The daily loss cap is `MAX_LOSS_PER_DAY`.
- `MAX_DRAWDOWN_PCT` uses internal $1000 base — operator should treat as a loose secondary guard, not a primary loss limit.
- The risk engine's `_current_balance` is process-local. On restart, daily P&L is rehydrated from same-day settled trades but cumulative balance resets to $1000.

### Pre-flight: Polymarket CLOB free collateral

**Scope: documentation + operator-side check only.** The current bot does NOT include a live pre-submit free-collateral guard. A balance shortfall during live trading will surface as Polymarket order rejections, not as a defensive bot-side skip.

Before resuming live trading, the operator must verify CLOB free collateral is sufficient. The bot does **not** dynamically size trades based on available balance — `MARKET_BUY_USD` is a fixed per-trade spend regardless of remaining balance.

```bash
venv/bin/python check_polymarket_balance.py --sync
```

Verify:
- Free collateral ≥ `MARKET_BUY_USD` (enough for one trade at minimum).
- Preferably free collateral ≥ `MAX_TOTAL_EXPOSURE + 2 × MARKET_BUY_USD` buffer (enough for the full exposure cap plus a safety margin for in-flight orders).

The bot receives Nautilus `AccountState` updates from Polymarket, but those updates are not currently wired into trade-size decisions.

### Optional future work: live pre-submit balance guard (out of scope for Phase 2)

If the operator wants the bot to refuse trades when free collateral is missing, stale, or below `MARKET_BUY_USD`, this is covered by Phase 2.5. Requirements:

- Hook the Nautilus `AccountState` update path to keep `self._latest_free_collateral` and `self._latest_account_state_ts` current.
- In `_make_trading_decision` (or `_place_real_order`), before order construction:
  - Fail closed if `self._latest_account_state_ts` is older than e.g. 30 seconds (stale state).
  - Fail closed if `self._latest_free_collateral < MARKET_BUY_USD + safety_buffer`.
- Log the decision to `decisions.jsonl` with `rejected_at_gate="balance_guard"`.
- No fallback. If the account state can't be obtained, refuse to trade, do not estimate.

This is intentionally not on the Phase 0/1/2 critical path. Document as a future enhancement; ship only if operator decides the existing rejection-as-feedback approach is insufficient.

### Effort

5 minutes for the balance check. The Nautilus 1.227.0 audit is tracked in Phase 0.5a above and must be estimated after clean-env dependency resolution.

---

## Phase 2.4 — Structured Decision Writer (prerequisite for Phase 2.5 and Phase 4)

**Status:** Code prerequisite. Promoted ahead of Phase 2.5 because dynamic sizing rejection tests need structured `rejected_at_gate` records, and Phase 4 calibration needs the same decision ledger.

Add a structured `decisions.jsonl` writer in `_make_trading_decision` that emits one JSON object per decision with all join keys:

```python
{
  "decision_id": "<unique id>",
  "ts": "<ISO UTC timestamp>",
  "slug": "<market slug>",
  "condition_id": "<condition id>",
  "yes_token_id": "...",
  "no_token_id": "...",
  "market_end_time": "<ISO UTC>",
  "seconds_into_sub_interval": 812.3,
  "trade_window_label": "13_14_current",
  "trend_price_band": "yes_gt_0.60",
  "strategy_observation_mode": "live_gate",
  "fused_confidence": 0.7755,
  "fused_direction": "bearish",
  "decided_direction": "short",   # null if rejected
  "rejected_at_gate": null,       # or "trend_filter" | "signal_confirmation" | "ev_gate" | "liquidity" | etc.
  "rejection_reason": null,
  "executable_entry": "0.998",
  "yes_ask": "...",
  "no_ask": "...",
  "model_signals": {...},
}
```

Use the finalizer-style helper described in Phase 4.1 so every early return emits exactly one record. The time-in-market and price-band fields are required because Phase 4.5 evaluates whether the current late-window/extreme-band policy has lost its edge. Phase 2.5 may then add sizing-specific fields and rejection gates to this existing writer instead of inventing a second logging path.

### Exit criteria

- [ ] Every `_make_trading_decision` invocation appends exactly one `decisions.jsonl` record.
- [ ] Early-return tests cover representative gates and assert the expected `rejected_at_gate`.
- [ ] Records contain the join keys Phase 4 needs: `slug`, `condition_id`, token ids, market end time, confidence, direction, executable entry, and processor diagnostics.
- [ ] Records contain the strategy-policy fields Phase 4.5 needs: `seconds_into_sub_interval`, `trade_window_label`, `trend_price_band`, and `strategy_observation_mode`.

### Effort

0.5 day. This effort was previously nested under Phase 4; it is now pulled earlier because Phase 2.5 depends on it.

---

## Phase 2.5 — Dynamic Trade Sizing (NEW)

**Status:** Operator-requested. Adds a second sizing mode where the per-trade USD amount is derived from current free collateral instead of a fixed env value. Ships AFTER Phase 0 and BEFORE Phase 3 (the ORDER_TYPE / LIMIT_IOC work). Independent of order type.

### Motivation

Current bot uses `MARKET_BUY_USD` as a fixed per-trade spend. Operator wants the option to size trades as a percentage of available balance, so that:

- The position-size auto-scales as the account grows (or shrinks) without manual env changes.
- Operator can express "use 5% of my account per trade" instead of "use $55 per trade."

### Design — two explicit modes, no implicit default

Mode selection via required env var (no implicit default in live mode, matching the `ORDER_TYPE` pattern):

```env
# Required in live mode. Allowed values: fixed | percent
SIZING_MODE=
```

Migration note for the current operator-confirmed fixed-size config: after Phase 2.5 ships, the normal `$55 / $385 / 7 positions / $110 daily loss` env must add:

```env
SIZING_MODE=fixed
```

Until Phase 2.5 ships, the current env lacking `SIZING_MODE` is expected and does not change today's fixed `MARKET_BUY_USD=55.00` behavior.

Validation in `run_integrated_bot` when `simulation=False`:

```python
sizing_mode = get_required_env("SIZING_MODE", "Set to 'fixed' or 'percent' for live trading.")
if sizing_mode not in {"fixed", "percent"}:
    raise RuntimeError(f"SIZING_MODE must be 'fixed' or 'percent', got {sizing_mode!r}")
```

Per-mode required env vars:

- **`SIZING_MODE=fixed`:** uses existing `MARKET_BUY_USD` as today. No new env required.

- **`SIZING_MODE=percent`:** requires `PCT_OF_FREE_COLLATERAL_PER_TRADE` (a `Decimal` strictly between 0 and 1, e.g. `0.05` for 5%). No implicit default.

```python
def _get_pct_of_free_collateral() -> Decimal:
    raw = os.getenv("PCT_OF_FREE_COLLATERAL_PER_TRADE")
    if raw is None:
        raise RuntimeError("PCT_OF_FREE_COLLATERAL_PER_TRADE must be set when SIZING_MODE=percent")
    try:
        value = Decimal(raw)
    except Exception as exc:
        raise RuntimeError(f"PCT_OF_FREE_COLLATERAL_PER_TRADE must be a decimal, got {raw!r}") from exc
    if value <= Decimal("0") or value >= Decimal("1"):
        raise RuntimeError(
            f"PCT_OF_FREE_COLLATERAL_PER_TRADE must be in (0, 1) — got {value}"
        )
    return value
```

### Live account-balance freshness (required for BOTH modes)

Phase 2.5 is also the phase that keeps the bot's view of CLOB free collateral fresh. This is required even when `SIZING_MODE=fixed`: a fixed `$55` order can still fail if previous orders consumed free collateral, and claimed/redeemed markets can increase free collateral before the next decision.

Required account-state cache:

```python
self._latest_free_collateral: Decimal | None
self._latest_account_state_ts: datetime | None
self._account_state_sequence: int
```

The bot must update this cache from every Nautilus `AccountState` event emitted by the Polymarket adapter. It must store the available/free USDC collateral field that the exchange uses for new CLOB orders, not the bot's internal `_current_balance` risk-accounting number.

Freshness triggers:

1. **Startup:** do not allow live order submission until at least one `AccountState` has been received, unless the operator has explicitly decided that Phase 2.5 is not enabled yet. After Phase 2.5 ships, missing account state is a trade reject, not an exchange-order attempt.
2. **Before every order:** check that `account_state_age_seconds <= MAX_ACCOUNT_STATE_AGE_SECONDS` and `free_collateral >= per_trade_usd + BALANCE_SAFETY_BUFFER_USD`. This applies to fixed and percent modes.
3. **After order submission/fill:** mark the cached balance as potentially stale until a newer `AccountState` arrives. If the next decision arrives before the refresh, reject with `rejected_at_gate="stale_balance_after_order"` rather than guessing remaining collateral.
4. **After `auto_redeem` / late redeem / manual settlement that can release or add funds:** mark the cached balance as potentially stale until a newer `AccountState` arrives. If the next decision arrives before the refresh, reject with `rejected_at_gate="stale_balance_after_redeem"`.

No fallback: do not estimate free collateral from `live_trades.json`, submitted order size, previous balance minus order size, or redemption payout. Those are not the exchange's current CLOB free collateral and can diverge because of partial fills, fees, pending orders, manual trades, or delayed account updates.

If the installed adapter does not emit a fresh `AccountState` after order/fill/redeem, Phase 2.5 must add exactly one explicit refresh mechanism and document it before implementation, for example a known py-clob-client balance endpoint called after those events. Do not implement multiple refresh paths with fallback ordering.

### Computing the per-trade size at decision time

**For `SIZING_MODE=fixed`:** size is the same as today, but the live pre-submit balance freshness guard above still applies.

```python
per_trade_usd = get_market_buy_usd()   # reads MARKET_BUY_USD
```

**For `SIZING_MODE=percent`:** depends on live `AccountState`.

```python
if self._latest_account_state_ts is None:
    return False  # fail closed; never sized without a known balance
age = (datetime.now(timezone.utc) - self._latest_account_state_ts).total_seconds()
if age > MAX_ACCOUNT_STATE_AGE_SECONDS:   # required env, e.g. 30
    return False  # stale balance, fail closed

free_collateral = self._latest_free_collateral
if free_collateral is None or free_collateral <= 0:
    return False  # no usable balance

per_trade_usd = (free_collateral * pct_of_free_collateral_per_trade).quantize(
    Decimal("0.01"), rounding=ROUND_DOWN
)
```

The percent-mode computation uses the same account-state cache as the fixed-mode balance guard. The `AccountState` hook is mandatory infrastructure, not optional future work.

### Hard constraints that apply to BOTH modes

After computing `per_trade_usd`, ALL of the following must pass or the trade is rejected:

1. `per_trade_usd >= MARKET_MINIMUM_USD` (Polymarket: $1 for market orders, or $5 × `submitted_limit_price` worth of tokens for limit orders — see Phase 3 5-token-minimum logic).
2. `per_trade_usd <= MAX_POSITION_SIZE` — operator-enforced ceiling. Computed amount exceeding this is **rejected**, NOT clamped. Clamping would silently shrink the operator's intended size.
3. `current_exposure + per_trade_usd <= MAX_TOTAL_EXPOSURE` — pre-trade exposure check using risk engine.
4. `current_position_count < MAX_POSITIONS` — pre-trade count check.

The `per_trade_usd > MAX_POSITION_SIZE` rejection is important. If the operator sets `SIZING_MODE=percent`, `PCT_OF_FREE_COLLATERAL_PER_TRADE=0.10`, and the account grows to $1000 free collateral, the computed per-trade size is $100. If `MAX_POSITION_SIZE=55`, the trade is rejected (with a clear log) rather than silently capped at $55. The operator must consciously raise `MAX_POSITION_SIZE` or lower the percent to scale up.

### `decisions.jsonl` integration

The decision record from Phase 2.4 gains these fields:
- `sizing_mode`: `"fixed"` or `"percent"`
- `free_collateral_at_decision`: the snapshotted exchange free collateral used for the guard/sizing computation
- `account_state_age_seconds`: age of the balance snapshot
- `account_state_sequence`: monotonically increasing local sequence for observed `AccountState` events
- `balance_stale_reason`: null, `"startup"`, `"after_order"`, `"after_redeem"`, or `"too_old"`

These allow Phase 4 calibration to detect whether bigger-vs-smaller positions perform differently — useful for tuning the percent value over time.

### Tests

- [ ] `SIZING_MODE=fixed` produces the same per-trade size as today (regression).
- [ ] `SIZING_MODE=percent` with $1000 free collateral and `PCT=0.05` produces `per_trade_usd=$50.00`.
- [ ] `SIZING_MODE=percent` with stale account state (>30s old) rejects the trade and logs to `decisions.jsonl` with `rejected_at_gate="stale_balance"`.
- [ ] `SIZING_MODE=percent` with no account state yet rejects with `rejected_at_gate="no_balance"`.
- [ ] `SIZING_MODE=fixed` still checks fresh free collateral before submitting a `$55` order.
- [ ] After a submitted order/fill marks balance stale, the next decision rejects with `rejected_at_gate="stale_balance_after_order"` until a newer `AccountState` arrives.
- [ ] After `auto_redeem`, late redeem, or manual settlement marks balance stale, the next decision rejects with `rejected_at_gate="stale_balance_after_redeem"` until a newer `AccountState` arrives.
- [ ] Computed size exceeding `MAX_POSITION_SIZE` rejects (does not clamp) with `rejected_at_gate="size_exceeds_max_position_size"`.
- [ ] Missing `SIZING_MODE` in live mode raises at startup.
- [ ] Invalid `SIZING_MODE` value raises at startup.
- [ ] `SIZING_MODE=percent` with missing or out-of-range `PCT_OF_FREE_COLLATERAL_PER_TRADE` raises at startup.

### Effort

2 days. Most of the work is the `AccountState` hook, stale-after-order/redeem invalidation, and the rejection-vs-clamp logic for `MAX_POSITION_SIZE`.

### Exit criteria

- [ ] Both modes work via env var, no implicit default in live mode.
- [ ] Operator can switch modes by env edit + restart (same constraint as other risk-engine env vars).
- [ ] Fresh exchange free collateral is required before live submission in both fixed and percent modes.
- [ ] All four hard constraints (minimum, max position, max exposure, max positions) apply to both modes.
- [ ] All listed tests pass.
- [ ] README documents both modes with example env values and the rejection-not-clamp semantics for `MAX_POSITION_SIZE`.

---

## Phase 3 — Configurable Order Type (`MARKET_IOC` vs `LIMIT_IOC`) + Quote Stability

**Status:** Mandatory live-resume blocker. The current market-IOC-only live path can sweep worse book levels than the EV gate accepted, so configurable limit-price order support is now required before any live resume. The quote-stability gate is part of the same safety boundary: the bot must not compute and submit a limit price from an insufficiently stable quote stream. Phase 0 fill reconciliation and Phase 5A selected-token depth estimation must remain intact. Phase 4 calibration and Phase 4.5 timing/price-band evaluation remain required before scaling or changing strategy policy, but they no longer defer the price-cap safety work. Phase 5B limit-depth wiring depends on this Phase 3 order-type scaffold and must ship before `ORDER_TYPE=limit_ioc` is enabled for live trading.

### Motivation

Current behavior: bot submits market IOC orders. A $1 (or $5) budget sweeps the book until exhausted. Average fill price can be substantially worse than the top-of-book ask or VWAP snapshot the EV gate evaluated.

Mandatory behavior: live order construction is selected explicitly by configuration:
- **`MARKET_IOC`** (current behavior): immediate fill at whatever price is available, up to budget.
- **`LIMIT_IOC`**: immediate fill at or better than the EV-accepted price cap, then cancel any unfilled remainder. No resting state.

Both are explicit operator choices. This is not a silent fallback - the operator must set `ORDER_TYPE` explicitly (no default). The implementation work for `LIMIT_IOC` is mandatory, not a later enhancement. `MARKET_IOC` may remain available only as an explicit operator-selected mode; it must never be the implicit default and must never be used to claim that a price-accepted decision was protected by a limit price.

The current hardcoded `QUOTE_STABILITY_REQUIRED = 3` must move into Phase 3 live configuration. Normal live configuration should set `QUOTE_STABILITY_REQUIRED=3`. The code must validate it explicitly in live mode instead of silently relying on a module constant.

### 3.0 — Adoption decision from `polymarket-trading-bot`

Review of `/Users/kkkelvinkk/AppDev/AppSrc/CyberSecThreat/polymarket-trading-bot` confirms the correct CLOB limit-order shape:
- Direct CLOB limit orders sign an order with `token_id`, `price`, `size`, and `side`, then submit `post_order(..., order_type)`.
- For BUY orders, signed maker amount is `size * price` USDC and taker amount is token `size`; this matches the conservative sizing rule in this plan.
- Its direct signer/client stack should not be copied into this Nautilus bot. This repo must preserve the Nautilus execution lifecycle, fill events, settlement handling, risk tracking, and ledger accounting. Adopt the semantics, not the transport stack.

Implementation target in this repo:
- Use `self.order_factory.limit(...)` for `ORDER_TYPE=limit_ioc`.
- Use `quote_quantity=False`; quantity is token count, not USDC budget.
- Derive one `submitted_limit_price` from the EV-accepted cap by rounding BUY prices down to the instrument's allowed tick/precision. Never round a BUY limit above the accepted cap. Use this same submitted price for depth estimation, token sizing, risk/free-collateral checks, pre-submit intent, and `Price.from_str`.
- Use `TimeInForce.IOC`, which installed Nautilus 1.227.0 maps to Polymarket `FAK`; verify this with tests and document the observed mapping.
- Reject if the rounded token quantity is below Polymarket's 5-token minimum.

### 3.0a — Decide partial-fill vs all-or-nothing semantics BEFORE the wire-format verification

This is a strategy decision, not a Nautilus question. The live-resume-ready Phase 3 implementation is blocked until the operator explicitly selects one policy. There is no plan default.

**Option FAK (fill-and-kill / partial fill OK):**
- "Buy up to N tokens at price ≤ cap; whatever fills, fills. Cancel the rest."
- The bot may receive a partial fill (e.g., 4 out of 10 tokens) and accept it.
- Existing partial-fill handling in `_record_live_order_fill` already accumulates partials.
- Recommended for liquidity-sensitive small trades where any fill at a good price is better than no fill.

**Option FOK (fill-or-kill / all-or-nothing):**
- "Buy exactly N tokens at price ≤ cap, or cancel entirely."
- The bot only books a position if the full intended size fills.
- Simpler accounting (no partial-fill state).
- Recommended when the strategy's edge depends on a full position; partial fills would distort risk.

Implementation requirement: add a required live-mode env var such as `LIMIT_IOC_FILL_POLICY`, allowed values `partial_ok` or `all_or_nothing`, and fail startup if `ORDER_TYPE=limit_ioc` and the policy is missing/invalid. Do not silently infer this policy from Nautilus' `FAK/FOK` mapping.

Compatibility rule: current workspace verification shows `LIMIT + IOC` maps to Polymarket `FAK`. Under `FAK`, the exchange may partially fill if liquidity changes between the strategy-side depth check and order arrival. Therefore:
- `LIMIT_IOC_FILL_POLICY=partial_ok` is compatible with the verified `FAK` wire behavior; the existing partial-fill ledger path remains valid.
- `LIMIT_IOC_FILL_POLICY=all_or_nothing` requires true exchange-enforced `FOK` wire behavior. With the current `IOC -> FAK` mapping, this policy must fail closed at startup until a deliberate FOK submission path is implemented and wire-format tested. A pre-submit depth check alone is not sufficient to enforce all-or-nothing.

### 3.0b — HARD PREREQUISITE: verify Nautilus wire format for `OrderType.LIMIT + TimeInForce.IOC`

Before completing the Phase 3 live-order branch, verify experimentally that `self.order_factory.limit(... time_in_force=TimeInForce.IOC ...)` actually produces the wire format we expect when it reaches `py_clob_client`. Three things could go wrong, all of which require **deliberate** action — not fallback:

1. **Nautilus may map `LIMIT + IOC` to Polymarket's `FAK` order type.** Proceed only with `LIMIT_IOC_FILL_POLICY=partial_ok`. If the operator selects `all_or_nothing`, block startup until a deliberate FOK submission path is implemented and wire-format tested; a strategy-side pre-submit depth check alone cannot enforce all-or-nothing under `FAK`.
2. **Nautilus may map `LIMIT + IOC` to `FOK`** (fill-or-kill, all-or-nothing). Proceed only if the operator-selected `LIMIT_IOC_FILL_POLICY` is compatible with that wire behavior. If not, revise the limit-order submission design deliberately before live resume.
3. **Nautilus may reject the combination outright** and require a different `TimeInForce` value (`GTD`, `FAK` explicit, etc.). If so, choose the Polymarket order type deliberately by reading `py_clob_client` source — do NOT pick whichever value happens to make the rejection go away.

Current workspace verification on 2026-05-20 showed installed Nautilus 1.227.0 maps `TimeInForce.IOC` to Polymarket `FAK`, and its limit submit path calls `create_order(...)` followed by `post_order(..., order_type)`. Keep the unit test anyway because the mapping is internal to Nautilus and can change between versions.

**Verification method (NOT simulation):**

Write a unit test that constructs the limit order via `order_factory.limit`, intercepts the patched execution path, and asserts the exact CLOB calls. Installed Nautilus builds `OrderArgs` without an `order_type`; it passes the Polymarket order type later as the second argument to `post_order(...)`. Specifically assert:
- `create_order(...)` receives `OrderArgs` with `price`, `size`, `side`, `token_id`, and `expiration` populated correctly.
- `OrderArgs` does **not** carry `order_type`; do not write a test expecting that field.
- The signing call is `create_order(...)`, NOT `create_market_order(...)`.
- `post_order(signed_order, <order_type>)` receives the expected mapping (`"FAK"`, `"FOK"`, or `"GTC"`, whichever Nautilus maps from `TimeInForce.IOC`).

Document the actual mapping in the Phase 3 implementation PR. If the mapping doesn't match the intent ("fill at limit or cancel"), file an issue and **block Phase 3** until resolved. Do not ship a workaround.

**Why this is a hard prerequisite:** the plan currently assumes `LIMIT + IOC = "fill at price or better, otherwise cancel"`. If Nautilus' mapping produces something different (e.g., FOK which requires the entire order to fill atomically), the price-discipline strategy doesn't work as designed and the bot will silently behave differently from operator expectations.

### 3.1 — Hard practical constraint: 5-token minimum

Polymarket limit orders require ≥5 tokens. At various target prices with various budget sizes:

| Submitted limit price | $1 budget → tokens | $5.51 smoke budget → tokens | $11 budget → tokens |
|---|---|---|---|
| $0.20 | 5.0 ✅ | 27.55 ✅ | 55.0 ✅ |
| $0.30 | 3.3 ❌ | 18.37 ✅ | 36.7 ✅ |
| $0.50 | 2.0 ❌ | 11.02 ✅ | 22.0 ✅ |
| $0.62 | 1.6 ❌ | 8.89 ✅ | 17.7 ✅ |
| $0.80 | 1.25 ❌ | 6.89 ✅ | 13.75 ✅ |

**Implication:** `LIMIT_IOC` is only usable when `MARKET_BUY_USD / submitted_limit_price ≥ 5`. At Phase 2's smoke budget (`5.51`, the minimum allowed live size), this works for prices ≤ $1.00 (i.e., all valid prices). At $1 budget, only ≤ $0.20 trades qualify.

### 3.2 — `quote_quantity` correctness per order type

| Order type | `quote_quantity` | `quantity` semantics |
|---|---|---|
| Market BUY (current patched path) | `True` | USD amount to spend |
| Limit BUY | `False` | Token count (per the existing limit-order integration path in `nautilus_polymarket_integration.py`) |
| Market SELL | `False` | Token count |
| Limit SELL | `False` | Token count |

The current `patch_market_orders.py` only handles the market BUY USD-amount path. Adding limit-order support means:
- Compute `token_qty = MARKET_BUY_USD / submitted_limit_price`
- Reject if `token_qty < 5` (Polymarket minimum)
- Submit with `quote_quantity=False` and the **decimal token quantity rounded down to `instrument.size_precision`** (e.g., 6 decimal places for Polymarket)
- Submit `price` as a separate field

### 3.3 — Implementation

**Env vars (required for order-capable runs, no implicit defaults):**

```env
# Required for order-capable runs. No implicit default in code.
# Allowed values: market_ioc | limit_ioc
ORDER_TYPE=limit_ioc

# Required for order-capable runs. No implicit default in code. Normal value: 3.
# Number of consecutive valid quote ticks required before order decisions proceed.
QUOTE_STABILITY_REQUIRED=3

# Required when ORDER_TYPE=limit_ioc. No implicit default.
LIMIT_REQUIRED_EDGE=0.05

# Required when ORDER_TYPE=limit_ioc. No implicit default.
# Allowed values: partial_ok | all_or_nothing
# all_or_nothing requires verified exchange-enforced FOK behavior.
# Current verified FAK path requires the operator to set partial_ok for routine resume.
LIMIT_IOC_FILL_POLICY=partial_ok
```

Operational policy: after Phase 3 + Phase 5B ship, routine live operation should use `ORDER_TYPE=limit_ioc` so an accepted decision is protected by an exchange-enforced price cap. `ORDER_TYPE=market_ioc` remains a deliberate operator mode for smoke tests, comparison, or emergency operation, but selecting it means the operator explicitly accepts book-sweep price risk.

Quote-stability policy: `QUOTE_STABILITY_REQUIRED` is tied to the limit-price safety path. The bot may only call `_place_real_order` after the current market has produced at least this many consecutive valid quote ticks after the latest market switch or quote-stability reset. For `ORDER_TYPE=limit_ioc`, this prevents submitting a price cap that was computed from a one-off or just-reset quote stream. For `ORDER_TYPE=market_ioc`, this preserves the existing protection against trading immediately after a market switch.

**Validation in `run_integrated_bot` at startup AND at every order decision/live trade attempt:**

```python
def _validate_order_type_for_live() -> str:
    order_type = os.getenv("ORDER_TYPE")
    if not order_type:
        raise RuntimeError("ORDER_TYPE must be set to 'market_ioc' or 'limit_ioc'")
    if order_type not in {"market_ioc", "limit_ioc"}:
        raise RuntimeError(f"ORDER_TYPE must be 'market_ioc' or 'limit_ioc', got {order_type!r}")
    return order_type

def _validate_quote_stability_required_for_live() -> int:
    raw = os.getenv("QUOTE_STABILITY_REQUIRED")
    if not raw:
        raise RuntimeError("QUOTE_STABILITY_REQUIRED must be set to a positive integer")
    try:
        required = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"QUOTE_STABILITY_REQUIRED must be a positive integer, got {raw!r}") from exc
    if required <= 0:
        raise RuntimeError(f"QUOTE_STABILITY_REQUIRED must be > 0, got {required}")
    return required

def _validate_limit_ioc_fill_policy_for_live(order_type: str) -> str | None:
    if order_type != "limit_ioc":
        return None
    policy = os.getenv("LIMIT_IOC_FILL_POLICY")
    if policy not in {"partial_ok", "all_or_nothing"}:
        raise RuntimeError(
            "LIMIT_IOC_FILL_POLICY must be set to 'partial_ok' or 'all_or_nothing' "
            "when ORDER_TYPE=limit_ioc"
        )
    if policy == "all_or_nothing":
        raise RuntimeError(
            "LIMIT_IOC_FILL_POLICY=all_or_nothing requires verified FOK wire behavior; "
            "current Nautilus LIMIT+IOC maps to FAK"
        )
    return policy
```

- **Startup check:** `run_integrated_bot` calls this for every bot run that processes quote-driven order decisions, including simulation/decision-observation runs. This keeps the pre-submit decision gates using the same order-type, quote-stability, limit-edge, and EV-buffer validation as live.
- **Runtime check:** `_place_real_order` ALSO calls these validators on every live trade. This is defense-in-depth against environment changes after startup or nonstandard call paths reaching live order submission. A process launched without live execution enabled is simulation-only and cannot flip live through Redis, but any live submission path must still refuse missing or invalid `ORDER_TYPE`, `QUOTE_STABILITY_REQUIRED`, or `LIMIT_IOC_FILL_POLICY` before an order is submitted.

**No default in any mode.** The previous draft suggested simulation/test mode could default to `market_ioc` — that is a fallback and is now removed. Any bot run or test that exercises quote-driven order decisions, live order construction, or quote-stability gating must set the relevant env vars explicitly. Pure helper/unit tests that do not construct the strategy or process order decisions may leave them unset.

Non-live behavior must be explicit: quote tick counting may still update in simulation/test mode, but the gate must not read an uninitialized `_quote_stability_required` value. Simulation/decision-observation paths that process quote-driven decisions construct the strategy with a validated threshold and order type, but they remain decision/paper-observation only: they do not model fills, settlement, ledger accounting, or P&L, and must not be represented as live-equivalent trade simulation.

Quote-stability wiring requirement: the validated value must be stored on the strategy (for example `self._quote_stability_required`) and the quote handler must compare `self._stable_tick_count >= self._quote_stability_required`. Runtime validation inside `_place_real_order` is not enough, because `_market_stable` may have been set earlier under a different threshold. `_place_real_order` must also fail closed if `self._stable_tick_count` is below the validated live threshold at submission time.

**Submitted limit price helper (owned by `_make_trading_decision` before Phase 5B depth estimation):**

```python
def derive_submitted_limit_price(accepted_limit_price: Decimal, instrument) -> Decimal:
    price_precision = instrument.price_precision
    price_quantum = Decimal(10) ** -price_precision
    submitted_limit_price = Decimal(str(accepted_limit_price)).quantize(
        price_quantum,
        rounding=ROUND_DOWN,
    )
    if submitted_limit_price <= 0 or submitted_limit_price > accepted_limit_price:
        raise RuntimeError(
            f"safe submitted limit price cannot be derived from cap {accepted_limit_price}"
        )
    return submitted_limit_price
```

**Branch in `_place_real_order`:**

`_place_real_order` receives both `accepted_limit_price` and `submitted_limit_price` for `ORDER_TYPE=limit_ioc`. It must not receive only the raw EV inputs and re-derive or re-round the submitted price locally; the submitted price was already rounded down once before Phase 5B depth estimation and must be passed through unchanged. The submit boundary only verifies `submitted_limit_price <= accepted_limit_price`, exact venue precision, and worst-case notional.

```python
order_type = os.getenv("ORDER_TYPE")
if order_type == "market_ioc":
    # Existing path: quote_quantity=True, IOC, patched to send USD via create_market_order
    order = self.order_factory.market(
        instrument_id=trade_instrument_id,
        order_side=OrderSide.BUY,
        quantity=Quantity.from_str(f"{max_usd_amount:.2f}"),
        client_order_id=ClientOrderId(unique_id),
        quote_quantity=True,
        time_in_force=TimeInForce.IOC,
    )
elif order_type == "limit_ioc":
    # submitted_limit_price was derived once before depth estimation and passed
    # through unchanged. Do NOT recompute or re-round it here.
    if submitted_limit_price is None or submitted_limit_price <= 0:
        raise RuntimeError(
            f"submitted_limit_price must be a positive Decimal for LIMIT_IOC, got {submitted_limit_price}"
        )

    # Token quantity at the WORST case (submitted limit price). If fill price
    # improves, we spend less than max_usd_amount. The conservative sizing
    # means we never spend more than the budget; we may spend less.
    raw_token_qty = Decimal(str(max_usd_amount)) / submitted_limit_price
    size_precision = instrument.size_precision
    token_qty = raw_token_qty.quantize(
        Decimal(10) ** -size_precision,
        rounding=ROUND_DOWN,
    )
    if token_qty < Decimal("5"):
        logger.warning(
            f"LIMIT_IOC requires ≥5 tokens; "
            f"budget=${max_usd_amount} / price={submitted_limit_price} = {token_qty} tokens "
            "after rounding to instrument size precision. "
            "Increase MARKET_BUY_USD or skip this trade."
        )
        return False
    # Use Nautilus' from_str constructors, matching the existing convention in
    # the codebase. Direct Price(value, precision=...) / Quantity(value, precision=...)
    # constructors have not been verified to handle Decimal precision the same way.
    qty_str = format(token_qty, f".{size_precision}f")
    price_precision = instrument.price_precision
    price_str = format(submitted_limit_price, f".{price_precision}f")
    order = self.order_factory.limit(
        instrument_id=trade_instrument_id,
        order_side=OrderSide.BUY,
        quantity=Quantity.from_str(qty_str),
        price=Price.from_str(price_str),
        client_order_id=ClientOrderId(unique_id),
        quote_quantity=False,
        time_in_force=TimeInForce.IOC,
    )
```

**Sizing semantics (documented):** `token_qty = budget / submitted_limit_price` is conservative. The submitted price is rounded down from the accepted cap so the exchange order can never pay more than the price the EV path accepted. The worst-case fill cost equals the budget. If actual fills happen at prices better than the submitted limit, the spend will be less than the budget. This is intentional: never exceed the budget, but accept under-spending. If the operator wants "spend as close to $5 as possible," that's a different sizing rule (post-fill top-up) and is out of scope.

**Token quantity is decimal, not integer.** Use `instrument.size_precision` from Nautilus' instrument metadata. Polymarket fills are decimal token quantities (e.g., `17.460316`). Any earlier wording in this document referring to "integer token count" is wrong and should be ignored — the correct phrasing is "decimal token quantity rounded down to `instrument.size_precision`."

**Limit-price ownership:** `limit_price` is computed exactly **once** during `_make_trading_decision` (after the signal-confirmation gate). The code then derives `submitted_limit_price` exactly once by rounding that BUY cap down to the allowed price precision before Phase 5B depth estimation. The depth-aware EV gate, token sizing, risk/free-collateral checks, pre-submit intent, and `_place_real_order` must all use that same submitted price. `_place_real_order` must not recompute or re-round it. This avoids the previous-draft inconsistency where Phase 5 read one limit price while Phase 3 submitted another — even a one-cent rounding-up difference can violate the accepted cap. One accepted cap, one safely rounded submitted price.

**`LIMIT_REQUIRED_EDGE` validation at startup (strict range, no clamping):**

The previous draft used `max(Decimal("0.01"), ...)` and `Decimal("1")` clamps inside `_compute_limit_price`. Both are silent fallbacks: they convert invalid inputs into valid-looking outputs. Replace with **strict validation at startup** so impossible values fail fast, not silently:

```python
def _get_validated_limit_required_edge() -> Decimal:
    raw = os.getenv("LIMIT_REQUIRED_EDGE")
    if raw is None:
        raise RuntimeError("LIMIT_REQUIRED_EDGE must be set when ORDER_TYPE=limit_ioc")
    try:
        value = Decimal(raw)
    except Exception as exc:
        raise RuntimeError(f"LIMIT_REQUIRED_EDGE must be a decimal, got {raw!r}") from exc
    # Strict range: must leave a usable price window in (0, 1) for binary tokens
    if value <= Decimal("0") or value >= Decimal("1"):
        raise RuntimeError(
            f"LIMIT_REQUIRED_EDGE must be in (0, 1) — got {value}. "
            f"A value outside this range cannot produce a usable limit price for "
            f"a Polymarket binary outcome token."
        )
    return value
```

Validated during order-config validation when `ORDER_TYPE=limit_ioc`; the resolved `Decimal` feeds the decision path that computes `accepted_limit_price`.

**`_compute_limit_price` helper (returns Optional, rejects without clamping):**

```python
def _compute_limit_price(self, fused_confidence: float) -> Optional[Decimal]:
    """
    Compute a price cap that captures edge, or None if no edge available.

    fused.confidence represents confidence in the selected fused direction (BULLISH
    or BEARISH). It is NOT a YES-probability output. For both long (buy YES) and
    short (buy NO), the cap is:
        cap = fused_confidence - self._limit_required_edge

    Returns None when cap is outside (0, 1). The caller must reject the trade and
    log the reason. No clamping, no defaulting.
    """
    conf = Decimal(str(fused_confidence))
    cap = conf - self._limit_required_edge
    if cap <= Decimal("0") or cap >= Decimal("1"):
        return None
    return cap
```

And the caller (NOTE: caller logs using `self._limit_required_edge`, not a non-existent local):

```python
limit_price = self._compute_limit_price(fused.confidence)
if limit_price is None:
    logger.info(
        f"SKIP: fused confidence {fused.confidence:.2%} minus LIMIT_REQUIRED_EDGE "
        f"({float(self._limit_required_edge):.2%}) produces no usable limit price "
        f"in (0, 1); no positive-edge trade exists at any Polymarket price."
    )
    return False
```

**Critical formula correction.** An earlier draft used `(1 - conf) - edge` for the NO direction. That is wrong because `fused.confidence` already represents confidence in the chosen direction. Computing `1 - conf` would invert the meaning. Both paths use `conf - edge`.

**`LIMIT_REQUIRED_EDGE` is a required env var when `ORDER_TYPE=limit_ioc`. No implicit default.**

**Semantics: `LIMIT_REQUIRED_EDGE` is GROSS edge (before EV-gate buffers).**

The flow is:

1. `limit_price = fused_confidence - LIMIT_REQUIRED_EDGE` — produces the price cap for order placement.
2. Order is submitted with that limit price.
3. Depth estimator computes `executable_entry` (the VWAP at or below the limit).
4. EV gate then applies its own buffers: `breakeven_confidence = executable_entry + EV_FEE_BUFFER + EV_SPREAD_BUFFER`. If `fused.confidence < breakeven_confidence`, the trade is rejected.

In effect:
- `LIMIT_REQUIRED_EDGE` reserves room *above* what we're willing to pay at the order level.
- `EV_FEE_BUFFER + EV_SPREAD_BUFFER` add the realized-cost buffer that the entry price plus fees must still leave room under confidence.

These are **not** the same number and they are **not** double-counted. `LIMIT_REQUIRED_EDGE` is gross edge above order-placement cap; EV buffers are applied to the actual executable entry separately. The bot enforces `LIMIT_REQUIRED_EDGE ≥ EV_FEE_BUFFER + EV_SPREAD_BUFFER` so that the limit cap cannot drift below the EV-gate buffer requirement.

Example with `fused.confidence = 0.78`, `LIMIT_REQUIRED_EDGE = 0.05`, `EV_FEE_BUFFER = 0.005`, `EV_SPREAD_BUFFER = 0.01`:
- `limit_price = 0.78 - 0.05 = 0.73` → order placed at price ≤ 0.73
- Suppose fill VWAP comes back at 0.71 → `executable_entry = 0.71`
- `breakeven_confidence = 0.71 + 0.015 = 0.725` → check `0.78 < 0.725`? No. Trade proceeds.

Same example but VWAP comes back at 0.73 (worst-case at the cap):
- `breakeven_confidence = 0.73 + 0.015 = 0.745` → check `0.78 < 0.745`? No. Trade proceeds.

Same example but `LIMIT_REQUIRED_EDGE = 0.01` (tighter):
- `limit_price = 0.78 - 0.01 = 0.77`. Suppose fill VWAP at 0.77.
- `breakeven_confidence = 0.77 + 0.015 = 0.785` → check `0.78 < 0.785`? Yes. **EV gate rejects.**

So setting `LIMIT_REQUIRED_EDGE < EV_FEE_BUFFER + EV_SPREAD_BUFFER` produces orders that pass the limit step but get rejected by the EV gate every time. The bot must enforce `LIMIT_REQUIRED_EDGE ≥ EV_FEE_BUFFER + EV_SPREAD_BUFFER` at startup and at runtime for `ORDER_TYPE=limit_ioc`; diagnostic asymmetry is not allowed on an order-capable path.

```python
def _get_required_env_decimal(name: str) -> Decimal:
    raw = os.getenv(name)
    if raw is None:
        raise RuntimeError(f"{name} must be set when ORDER_TYPE=limit_ioc")
    try:
        return Decimal(raw)
    except Exception as e:
        raise RuntimeError(f"{name} must be a decimal, got {raw!r}") from e
```

**Caveat:** the formula still depends on `fused.confidence` being a calibrated probability. See Phase 4. If calibration shows confidence is uncorrelated with win rate, the limit-price formula has no edge regardless of how the math is wired.

### 3.4 — patch_market_orders.py extension

The current patch intercepts market BUY orders only. Limit orders need to flow through Nautilus's standard CLOB limit-order path — no patching needed if Nautilus's adapter handles `OrderType.LIMIT + TimeInForce.IOC` correctly.

**Wire-format verification (NOT via simulation):**

Per the repo's simulation rule, decision-only simulation/test_mode **cannot prove live-equivalent order submission**. To verify the wire format of limit orders before any live run:

1. **Unit test with a mocked `py_clob_client`.** Build a test that constructs the limit order through `order_factory.limit(...)`, intercepts the patched execution path, and asserts that the mocked client's `create_order` (not `create_market_order`) is called with the expected `OrderArgs(token_id=..., price=Decimal("0.50"), size=Decimal("10"), side="BUY", expiration=...)` shape. Separately assert `post_order(signed_order, <mapped_order_type>)` receives the expected `FAK/FOK/GTC` mapping.
2. **One deliberately tiny live smoke trade** after Phase 0.7 recovery, mandatory Phase 3, and Phase 5B are closed: place one minimum allowed `$5.51` `ORDER_TYPE=limit_ioc` BUY at a moderate price, observe the actual Polymarket order record matches what we submitted (token quantity, limit price, IOC TIF).

Do not use simulation mode for this verification. Decision-only simulation cannot exercise the wire format.

### 3.5 — Tests

- Test: `ORDER_TYPE=market_ioc` builds market order with `quote_quantity=True`.
- Test: `ORDER_TYPE=limit_ioc` with sufficient budget builds limit order with `quote_quantity=False` and correct `price`.
- Test: `ORDER_TYPE=limit_ioc` with an EV-accepted BUY cap that is not exactly representable at instrument precision rounds the submitted price down, never up (e.g., cap `0.626` at 2 decimals submits `0.62`, not `0.63`), and depth/risk/pre-submit intent plus `_place_real_order` all use the same submitted price without local recomputation.
- Test: `ORDER_TYPE=limit_ioc` with insufficient budget (token_qty < 5) returns False without submitting.
- Test: missing `ORDER_TYPE` env var raises `RuntimeError` at startup.
- Test: invalid `ORDER_TYPE` value raises `RuntimeError`.
- Test: missing or invalid `ORDER_TYPE` raises `RuntimeError` at runtime for live-enabled Redis mode changes, environment changes after startup, or nonstandard live-submission call paths.
- Test: missing, non-integer, zero, or negative `QUOTE_STABILITY_REQUIRED` raises `RuntimeError` in startup live validation and runtime live-order validation.
- Test: configured `QUOTE_STABILITY_REQUIRED` values `1`, `2`, `3`, and `4` each require exactly that many consecutive valid quote ticks after market switch or quote-stability reset before live order placement can proceed.
- Test: missing `LIMIT_IOC_FILL_POLICY` when `ORDER_TYPE=limit_ioc` raises `RuntimeError`.
- Test: invalid `LIMIT_IOC_FILL_POLICY` when `ORDER_TYPE=limit_ioc` raises `RuntimeError`.
- Test: `LIMIT_IOC_FILL_POLICY=partial_ok` is accepted with the verified `IOC -> FAK` wire behavior.
- Test: `LIMIT_IOC_FILL_POLICY=all_or_nothing` fails closed while the verified wire behavior is `IOC -> FAK`; it can only pass after a deliberate FOK submission path is implemented and wire-format tested.

### Exit criteria

- [ ] `ORDER_TYPE` required env var validated at startup.
- [ ] `ORDER_TYPE` required env var validated again at every live order attempt, so live-enabled Redis mode changes, environment changes after startup, or nonstandard live-submission call paths cannot submit with missing or invalid order type.
- [ ] `QUOTE_STABILITY_REQUIRED` required env var validated at startup and every live order attempt; normal live config sets it to `3`.
- [ ] Hardcoded `QUOTE_STABILITY_REQUIRED = 3` is removed or replaced by a validated runtime value without adding an implicit default.
- [ ] Quote-stability gate uses the configured threshold in actual live-order gating, including market-switch and quote-reset paths.
- [ ] `LIMIT_IOC_FILL_POLICY` is required and enforced for `ORDER_TYPE=limit_ioc`; `partial_ok` is accepted under `FAK`, and `all_or_nothing` blocks startup until a verified FOK path exists.
- [ ] Both code paths exist and are exercised by tests.
- [ ] 5-token minimum guard prevents impossible limit orders.
- [ ] `ORDER_TYPE=limit_ioc` is implemented end-to-end and is the intended routine live configuration after Phase 5B limit-depth wiring is complete.
- [ ] `ORDER_TYPE=market_ioc` remains available only through explicit configuration and is documented as accepting book-sweep price risk.
- [ ] One minimum allowed `$5.51` live smoke trade with `ORDER_TYPE=limit_ioc` confirms the mandatory live-resume wire format is right. Any `market_ioc` smoke is a separate explicit operator-approved risk test, not a routine resume requirement.
- [ ] README documents both modes and their trade-offs.
- [ ] `.env.example` sets the routine explicit resume example to `ORDER_TYPE=limit_ioc`, includes `QUOTE_STABILITY_REQUIRED=3` and `LIMIT_REQUIRED_EDGE=0.05`, and documents `LIMIT_IOC_FILL_POLICY=partial_ok` as the current FAK-compatible routine resume value. `market_ioc` remains documented only as an explicit operator-selected risk mode.

### Effort

3 days. Most of the cost is verifying the limit-order CLOB submission format against Nautilus and py-clob-client.

---

## Phase 4 — Calibration Validation (gate before scaling and strategy-policy changes)

**Status:** Strategic. Required before trusting profitability, scaling live size, or changing strategy timing/price-band policy. It is not a blocker for mandatory Phase 3 price-cap safety or Phase 5B limit-depth wiring.

### Why this matters

`fused.confidence = 0.75` is a heuristic-weighted average of per-processor confidence formulas. None of these have been calibrated against actual win rates. If 75% confidence trades win 55% of the time, then setting a limit price at `0.75 - 0.05 = 0.70` will systematically buy at prices where the true win rate is below the buy price → guaranteed losses.

### Method

#### 4.1 — Collect data (two distinct paths, both required)

**Path A: Settled live trades only** (`live_trades.json`).
- Captures actual fills + actual settlement.
- **Has selection bias.** Only includes trades that passed every gate (trend filter, signal confirmation, EV gate, etc.). Cannot tell you whether *rejected* decisions had real edge.
- Use as the EV-validation source.

**Path B: Decision-log join against Polymarket historical resolutions** (was "OR" — now **required alongside Path A**).
- Pull every decision the bot logged (including ones it rejected).
- For each decision, look up the actual market resolution from Polymarket's historical data.
- Build a calibration set of (fused_confidence, actual_outcome) pairs that is NOT filtered by the bot's current gates.
- This is the only way to know whether `fused.confidence` predicts outcomes in general, not just on trades the bot decided to take.

**Path B prerequisite: structured decision-observation ledger.**

Reviewer-flagged: the current decision-observation flow in `bot.py` only emits log lines; it does not write a structured ledger with the join keys Path B needs. Without that ledger, joining against Polymarket historical resolutions is fragile parsing of free-text log lines.

**Implemented earlier in Phase 2.4 (must exist before running the calibration script):**

Add a structured `decisions.jsonl` writer in `_make_trading_decision` that emits one JSON object per decision with all the join keys:

```python
{
  "decision_id": "<unique id>",
  "ts": "<ISO UTC timestamp>",
  "slug": "<market slug>",
  "condition_id": "<condition id>",
  "yes_token_id": "...",
  "no_token_id": "...",
  "market_end_time": "<ISO UTC>",
  "seconds_into_sub_interval": 812.3,
  "trade_window_label": "13_14_current",
  "trend_price_band": "yes_gt_0.60",
  "strategy_observation_mode": "live_gate",
  "fused_confidence": 0.7755,
  "fused_direction": "bearish",
  "decided_direction": "short",   // null if rejected
  "rejected_at_gate": null,       // or "trend_filter" | "signal_confirmation" | "ev_gate" | "liquidity" | etc.
  "rejection_reason": null,       // free text if rejected
  "executable_entry": "0.998",    // top-of-book or VWAP at decision time
  "yes_ask": "...",
  "no_ask": "...",
  "model_signals": {...},         // per-processor scores/confidence for diagnostics
}
```

One line per decision, appended to `decisions.jsonl` in the same directory as `live_trades.json`. Rotated daily or by size cap. The calibration script in 4.2 joins by `condition_id` + market resolution lookup to produce the unbiased calibration set.

**Implementation pattern: finalizer-style helper (every early-return must emit one record).**

`_make_trading_decision` has many early-return branches (`history < 20`, `not signals`, `not fused`, trend-neutral, signal-confirmation-mismatch, low-confidence, EV-gate, risk-engine, liquidity, ...). A naïve "log at each return" approach will miss branches. Use a context-manager-style finalizer:

```python
class _DecisionRecord:
    def __init__(self, strategy, current_price):
        self.strategy = strategy
        self.fields = {
            "decision_id": str(uuid4()),
            "ts": datetime.now(timezone.utc).isoformat(),
            "current_price": str(current_price),
            "decided_direction": None,
            "rejected_at_gate": None,
            "rejection_reason": None,
            # ... all other join keys, initially null
        }

    def update(self, **kwargs):
        self.fields.update(kwargs)

    def reject(self, gate, reason):
        self.fields["rejected_at_gate"] = gate
        self.fields["rejection_reason"] = reason

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Always write — even if an exception escaped the with-block.
        if exc_type is not None:
            self.fields["rejected_at_gate"] = "exception"
            self.fields["rejection_reason"] = f"{exc_type.__name__}: {exc_val}"
        self.strategy._append_decision_record(self.fields)
        return False   # don't suppress exceptions
```

`_make_trading_decision` wraps its entire body in `with _DecisionRecord(self, current_price) as record:`. Each gate updates the record before returning. The `__exit__` guarantees exactly one record per call — no missed branches.

**Test:** for each early-return branch in `_make_trading_decision`, write a test that drives the strategy down that branch and asserts `decisions.jsonl` gained exactly one new line with the expected `rejected_at_gate` value.

**Effort:** writer implementation is accounted for in Phase 2.4. Phase 4 still requires elapsed time for the already-shipped writer to accumulate decisions before Path B can produce results.

**Both paths are required.** Path A alone has selection bias that hides whether the gate thresholds are correct. Path B alone doesn't measure realized P&L (no actual fills). Together they answer:
- "Is `fused.confidence` a calibrated probability?" (Path B)
- "Does executing on calibrated confidence actually make money after fees/slippage?" (Path A)

**Do NOT** use simulation mode for either. Simulation is decision-only — it doesn't model fills, slippage, or settlement.

#### 4.2 — Bucket and measure (outcome correctness AND EV)

**Reviewer-flagged correction:** the previous draft used `pnl > 0` to bucket "wins." That conflates outcome correctness with P&L sign. P&L can be negative even when the predicted outcome was right (if entry price was too high) and positive when the outcome was wrong (rare, but possible with fee/dust artifacts).

For **probability calibration**, win = "the selected outcome actually won," derived from the **settled ledger record's `payout` field**, NOT from "auto_redeem event was received":

- `payout > 0` → bought side won (received a redemption).
- `payout == 0` (literal, not the string "UNKNOWN") → bought side lost (settled with zero payout via the grace timeout or explicit operator resolution).
- `payout == "UNKNOWN"` or record not yet resolved → exclude from calibration. Missing `auto_redeem` events happen (websocket reconnect gaps, etc.); using "auto_redeem event exists" as the win signal would under-count winners.

This matches the operator-trusted source of truth (verified payout in the ledger), not the transient event delivery (which can miss). The bucketing script must exclude SETTLEMENT_UNKNOWN and `payout == "UNKNOWN"` entries from both numerator and denominator.

For **EV validation**, also bucket realized P&L and entry price.

Script `analyze_calibration.py`:

```python
import json
from collections import defaultdict
from decimal import Decimal

with open("live_trades.json") as f:
    ledger = json.load(f)

buckets = defaultdict(lambda: {
    "outcome_wins": 0,        # bought side won (auto_redeem paid > 0)
    "outcome_losses": 0,      # bought side lost (auto_redeem paid 0 or no payout)
    "sum_entry_price_weighted": Decimal("0"),
    "sum_pnl_usd": Decimal("0"),
    "sum_size_usd": Decimal("0"),
    "trades": 0,
})

for trade in ledger["settled"]:
    if trade.get("settlement_source") not in ("auto_redeem", "late_auto_redeem", "manual_reconciliation"):
        continue
    if trade.get("payout") in ("UNKNOWN", None) or trade.get("pnl") in ("UNKNOWN", None):
        continue  # unresolved; exclude from numerator and denominator
    conf = float(trade["signal_confidence"])
    payout = Decimal(str(trade["payout"]))
    pnl = Decimal(str(trade["pnl"]))
    size = Decimal(str(trade["size"]))
    entry = Decimal(str(trade["entry_price"]))
    if size <= 0:
        raise ValueError(f"settled trade has non-positive size: {trade!r}")
    bucket = round(conf * 10) / 10  # 0.5, 0.6, 0.7, 0.8, 0.9
    b = buckets[bucket]
    if payout > 0:
        b["outcome_wins"] += 1
    else:
        b["outcome_losses"] += 1
    b["sum_entry_price_weighted"] += entry * size
    b["sum_pnl_usd"] += pnl
    b["sum_size_usd"] += size
    b["trades"] += 1

print(f"{'conf':>6} {'n':>5} {'win_rate':>10} {'w_avg_entry':>12} {'realized_return':>16} {'prob_edge':>12}")
for bucket in sorted(buckets):
    b = buckets[bucket]
    n = b["trades"]
    if n == 0:
        continue
    if b["sum_size_usd"] <= 0:
        raise ValueError(f"bucket {bucket} has no positive traded size")
    win_rate = b["outcome_wins"] / n
    weighted_avg_entry = b["sum_entry_price_weighted"] / b["sum_size_usd"]
    realized_return = b["sum_pnl_usd"] / b["sum_size_usd"]
    probability_edge = Decimal(str(win_rate)) - weighted_avg_entry
    print(f"{bucket:>6.1f} {n:>5} {win_rate:>10.1%} {float(weighted_avg_entry):>12.4f} {float(realized_return):>+16.4f} {float(probability_edge):>+12.4f}")
```

Add Brier score `mean((conf - outcome)**2)` and log-loss `-mean(outcome*log(conf) + (1-outcome)*log(1-conf))` if sample size permits.

#### 4.3 — Decision gate

A trade cohort is +EV when realized net P&L is positive per dollar actually risked. With Phase 2.5 dynamic sizing, the primary Path A gate is therefore **dollar-weighted realized return**:

```python
realized_return = sum_pnl_usd / sum_size_usd
```

`win_rate - weighted_avg_entry_price` remains a probability-calibration diagnostic, but it is not the primary pass/fail metric for live profitability. Unweighted `win_rate` and simple average entry can be misleading once position sizes vary.

**Three gates must all pass before scaling live size or changing strategy timing/price-band policy:**

1. **Dollar-weighted realized return is positive.** `sum_pnl_usd / sum_size_usd > 0` in at least one well-sampled bucket using net realized P&L. If the ledger's P&L calculation is later found not to include any fee component, add the explicit fee cost to the numerator before computing this metric; do not compare gross P&L to a net-profit gate.

2. **Probability edge cross-check is positive.** Compute a size-weighted average entry price for each bucket: `sum(entry_price * size_usd) / sum(size_usd)`. The Wilson 95% CI lower bound for win rate minus `weighted_avg_entry_price + fee_buffer + spread_buffer` must still be `> 0`. This keeps the probability-calibration claim honest while the realized-return gate remains primary.

3. **Dollar-weighted return persists out-of-sample.** Split the data into two halves (chronologically — first half vs second half). `sum_pnl_usd / sum_size_usd` must be positive in BOTH halves. This catches the case where the model fit a transient pattern that doesn't continue.

**Decision:**

- All three gates pass in ≥1 bucket with n ≥ 100: **proceed to Phase 4.5.** Mandatory Phase 3/5B price-cap safety can ship independently, but scaling and strategy-policy changes remain blocked until Phase 4.5 also clears the timing/price-band policy.
- Realized-return gate passes but probability-edge CI lower bound is negative: **collect more data or investigate sizing effects.** The strategy may be profitable due to sizing/gating rather than calibrated confidence.
- Realized-return gate passes but out-of-sample half is flat or negative: **no shippable edge for scaling or policy loosening.** The apparent edge is overfit. Keep mandatory price-cap safety, but improve signal processors before increasing exposure or broadening eligibility.
- Realized return is at or below zero across buckets: **no realized edge.** Same conclusion.
- `win_rate < weighted_avg_entry_price` across buckets: **negative probability edge.** Stop the strategy and investigate even if a small realized-return sample looks positive.

The win-rate-alone gate (e.g., "70% confidence → 65% win rate") from earlier drafts is insufficient because it ignores entry price AND ignores noise. A 70% win rate at $0.80 entry is a loser; at $0.50 entry it's a winner — but only if the sample is large enough to trust.

### Exit criteria

**Both paths required (Path A alone has selection bias and cannot answer the calibration question):**

- [ ] **`decisions.jsonl` writer is shipped and producing structured records** with all join keys (slug, condition_id, market_end_time, fused_confidence, decided_direction, rejected_at_gate, executable_entry, per-processor scores). Verified by reading several days of `decisions.jsonl` output and confirming each record has the documented fields.
- [ ] Calibration script exists and runs against **both** `live_trades.json` (Path A) **and** `decisions.jsonl` joined to Polymarket historical resolutions (Path B). Two separate analysis outputs, not one.
- [ ] **Path A:** sample size **≥100** settled live trades in at least one confidence bucket. Reports `win_rate`, `weighted_avg_entry_price`, `realized_return = sum_pnl_usd / sum_size_usd`, and `win_rate - weighted_avg_entry_price`. (This is the same `n ≥ 100` threshold used by the three-gate rule in 4.3 — unified across the whole document.)
- [ ] **Path B:** sample size ≥200 decision observations (including rejected) in at least one confidence bucket. Reports the same metrics computed from market resolutions, plus Brier score and log-loss across the full set.
- [ ] **Decision documented with the numbers from both paths.** If Path A shows positive realized return but Path B shows the same confidence buckets are uncorrelated with outcomes (i.e., the gates are filtering on noise that happens to correlate with profitable trades), scaling and strategy-policy changes are NOT cleared — the apparent edge is in the gates or sizing, not the signal. Mandatory Phase 3/5B price-cap safety remains required for live resume.

### Effort

4 hours of analysis once data is collected. Data collection requires Phase 2.4 to have shipped first, then days-to-weeks of elapsed time for it to accumulate ≥200 decisions plus ≥100 settled trades.

---

## Phase 4.5 — Strategy Timing and Price-Band Evaluation (no EV-gate loosening)

**Status:** Strategic. Addresses the "late-window edge is gone" concern before changing strategy timing/price-band policy or expanding live trade size.

### Current issue

Current live code has two hard-coded strategy gates:

- Trade timing is only minute 13-14 of each 15-minute market: `TRADE_WINDOW_START = 780`, `TRADE_WINDOW_END = 840` in `bot.py`.
- Trend price bands are only `YES > 0.60` or `YES < 0.40`; anything more extreme than those thresholds can proceed to signal confirmation and the EV gate in `bot.py`.

The EV gate is still required, but it does not by itself answer whether the late window still has edge, or whether very extreme prices (`YES >= 0.70`, `YES <= 0.30`) are worse than moderate continuation bands. The plan must evaluate that policy explicitly instead of assuming "later + more extreme = better."

### Scope

This phase evaluates candidate timing windows and price bands while keeping the EV gate unchanged.

1. **Add decision-observation records for candidate windows.** These records are not trade simulation and must not be described as live-equivalent profitability. They are decision observations: what the unchanged decision stack would have seen at a timestamp, with no order submission, no ledger write, no risk reservation, and no position accounting.
2. **Compare timing buckets against the current baseline.** Minimum buckets:
   - `06_09`: 6:00-8:59 into the 15-minute market.
   - `09_11`: 9:00-10:59.
   - `11_13`: 11:00-12:59.
   - `13_14`: current baseline.
3. **Compare price bands instead of treating all extremes as one class.** Minimum buckets:
   - YES-side moderate: `0.52 <= yes_price < 0.60`.
   - YES-side strong: `0.60 <= yes_price < 0.70`.
   - YES-side extreme: `yes_price >= 0.70`.
   - NO-side moderate: `0.40 < yes_price <= 0.48`.
   - NO-side strong: `0.30 < yes_price <= 0.40`.
   - NO-side extreme: `yes_price <= 0.30`.
4. **Keep the EV gate intact.** Do not lower `EV_FEE_BUFFER`, `EV_SPREAD_BUFFER`, `MIN_SIGNAL_CONFIDENCE`, or the calibrated-confidence requirement to make earlier windows or moderate bands pass. This phase only changes which observations are evaluated before the existing signal-confirmation and EV gates.
5. **No implicit live-policy fallback.** If Phase 4.5 later changes live timing/bands, either keep the chosen values as explicit code-owned constants with tests, or replace them with required env values that fail startup when missing/invalid. Do not add optional env vars that silently default back to `780-840` or `0.60/0.40`.

### Analysis method

Use the Phase 2.4 `decisions.jsonl` writer plus the Phase 5A selected-token depth estimator. Each observation must include:

- `seconds_into_sub_interval`
- `trade_window_label`
- `trend_price_band`
- `strategy_observation_mode` (`live_gate` for the current production call path, `shadow_policy` for non-submitting candidate windows)
- selected token, executable entry, depth-estimated VWAP if available, fused confidence, signal direction, and EV-gate threshold

For candidate windows outside the current live window, the bot may compute the same signal/EV diagnostics, but must stop before any order submission path. This is decision-observation mode only. It cannot be used to claim live-equivalent realized P&L because it does not model fill lifecycle, settlement, ledger writes, or risk accounting.

### Decision rule

Do not change the live trade window or price bands unless the Phase 4.5 report shows:

- The candidate cohort has at least `n >= 200` decision observations joined to market outcomes.
- The candidate cohort passes the same calibrated-confidence and EV-gate logic used by the baseline.
- The candidate cohort's counterfactual edge after executable-entry and fee/spread buffers is better than the current `13_14` baseline.
- A live canary plan is documented before routine use. Counterfactual observations can justify a tiny canary; they do not by themselves prove live profitability.

If no candidate beats the baseline, keep the current `13_14` and `0.60/0.40` policy and focus on signal processors rather than loosening the EV gate.

### Exit criteria

- [ ] `decisions.jsonl` contains timing and price-band fields for current live-gate decisions.
- [ ] Shadow decision-observation mode records candidate windows without submitting orders, reserving risk, or writing live trade ledger entries.
- [ ] Analysis report compares baseline vs earlier windows and moderate/strong/extreme price bands using the unchanged EV gate.
- [ ] Operator explicitly approves any live timing/band change before implementation.
- [ ] Tests prove that any selected timing/band change only changes candidate eligibility; the EV gate thresholds and buffers remain unchanged.

### Effort

0.5-1 day for observation wiring and analysis script changes, plus elapsed time to collect enough candidate observations.

---

## Phase 5 — Depth-Aware Fill Estimator (split into 5A / 5B)

**Status:** Phase 5A shipped; Phase 5B is now a mandatory live-resume blocker because `ORDER_TYPE=limit_ioc` must evaluate executable liquidity at the same price cap it submits.

- **Phase 5A:** market-depth estimator + selected-token book cache. Ships before Phase 3 and only wires the current `MARKET_IOC` path.
- **Phase 5B:** limit-depth integration. Ships after Phase 3 introduces `ORDER_TYPE`, `limit_price`, and target-token sizing. It must be complete before live trading resumes with `ORDER_TYPE=limit_ioc`.

### Motivation

Current EV gate uses top-of-book ask. Real market IOC fills sweep multiple book levels. Without depth-aware estimation, the gate filters on a price the trade never actually pays.

### Scope

#### 5.1 — Fill estimators

Add to `core/strategy_brain/signal_processors/orderbook_processor.py` (or a new utility module).

**Book level units:** each level's `price` is in [0,1] (Polymarket binary token price), `size` is in **tokens**. USD capacity at a level is `price × size`.

```python
class InvalidBookLevelError(ValueError):
    """Raised when a CLOB book level has impossible values."""

def _parse_book_level(level: dict, idx: int) -> tuple[Decimal, Decimal]:
    """Return (price, size_tokens), or raise on impossible book data."""
    try:
        price = Decimal(str(level["price"]))
        size_tokens = Decimal(str(level["size"]))
    except (KeyError, TypeError, ValueError, InvalidOperation) as e:
        raise InvalidBookLevelError(
            f"book level {idx} has non-numeric or missing price/size: {level!r}"
        ) from e
    if price <= 0 or price > 1:
        raise InvalidBookLevelError(
            f"book level {idx} price={price} is outside (0, 1]; refusing to compute"
        )
    if size_tokens <= 0:
        raise InvalidBookLevelError(
            f"book level {idx} size={size_tokens} is non-positive; refusing to compute"
        )
    return price, size_tokens

def estimate_market_ioc_fill(
    levels: list[dict],
    usd_to_spend: Decimal,
) -> tuple[Decimal | None, Decimal, bool]:
    """
    MARKET_IOC estimator: spend up to a USD budget across asks.

    Raises InvalidBookLevelError on any level with non-positive price/size, price > 1, or
    non-numeric values. Fail closed; do NOT silently skip bad levels — a corrupt book is
    an actionable error, not noise to be ignored.

    Return (vwap_or_none, total_tokens_filled, fully_filled). vwap_or_none is
    None when no tokens fill; never return Decimal("0") as a no-fill sentinel.
    """
    if usd_to_spend <= 0:
        raise ValueError(f"usd_to_spend must be positive, got {usd_to_spend}")
    remaining = usd_to_spend
    total_tokens = Decimal("0")
    total_cost = Decimal("0")
    for idx, level in enumerate(levels):
        price, size_tokens = _parse_book_level(level, idx)
        level_usd_capacity = price * size_tokens
        if remaining >= level_usd_capacity:
            total_tokens += size_tokens
            total_cost += level_usd_capacity
            remaining -= level_usd_capacity
        else:
            tokens_at_level = remaining / price
            total_tokens += tokens_at_level
            total_cost += remaining
            remaining = Decimal("0")
            break
    if total_tokens <= 0:
        return None, total_tokens, False
    vwap = total_cost / total_tokens
    return vwap, total_tokens, remaining <= 0

def estimate_limit_ioc_fill(
    levels: list[dict],
    target_token_qty: Decimal,
    max_price: Decimal,
) -> tuple[Decimal | None, Decimal, Decimal, bool]:
    """
    LIMIT_IOC estimator: acquire up to a token quantity at price <= max_price.

    Return (vwap_or_none, total_tokens_filled, actual_cost, fully_filled).
    vwap_or_none is None when no tokens fill; never return Decimal("0") as a
    no-fill sentinel.
    """
    if target_token_qty <= 0:
        raise ValueError(f"target_token_qty must be positive, got {target_token_qty}")
    if max_price <= 0 or max_price > 1:
        raise ValueError(f"max_price must be in (0, 1], got {max_price}")
    remaining_tokens = target_token_qty
    total_cost = Decimal("0")
    total_tokens = Decimal("0")
    for idx, level in enumerate(levels):
        price, size_tokens = _parse_book_level(level, idx)
        if price > max_price:
            break
        tokens_to_take = min(remaining_tokens, size_tokens)
        total_tokens += tokens_to_take
        total_cost += tokens_to_take * price
        remaining_tokens -= tokens_to_take
        if remaining_tokens <= 0:
            break
    if total_tokens <= 0:
        return None, total_tokens, total_cost, False
    vwap = total_cost / total_tokens
    return vwap, total_tokens, total_cost, remaining_tokens <= 0
```

**Caller behavior:** in `_make_trading_decision`, wrap the call in `try/except InvalidBookLevelError` and on error: log the error and refuse the trade (return False) with rejection reason `depth_aware_invalid_book_level`. Do not block the settlement ledger for bad market-data book input; settlement ledger blocks are reserved for submitted/fill/ledger state. Never fall back to a partial book or a default price.

**Usage per order type (different semantics — DO NOT use one estimator for both):**

Market orders are budget-driven (spend up to $X, take whatever tokens that buys). Limit orders are token-quantity-driven (acquire up to N tokens at price ≤ cap, spend whatever that costs — usually less than the worst-case budget). The two estimators have different inputs and different "fully filled" semantics.

**Why the distinction matters (reviewer-flagged P0 scenario):**

Suppose the operator wants `LIMIT_IOC` BUY at price ≤ $0.50 with budget $5. The bot computes `target_token_qty = $5 / $0.50 = 10 tokens`. The book has `10 tokens @ $0.40`.

- The proposed unified estimator (`usd_to_spend=$5, max_price=$0.50`): walks the book at $0.40, sees only `$0.40 × 10 = $4` available, returns `fully_filled=False`. **The bot skips a perfectly fillable trade.**
- The correct token-driven estimator (`target_token_qty=10, max_price=$0.50`): walks the book at $0.40, accumulates 10 tokens, returns `fully_filled=True, vwap=$0.40, actual_cost=$4`. **The bot takes the trade and spends less than the budget.**

`MARKET_IOC` keeps the USD-budget semantics (operator wants to spend $X regardless of what tokens that buys). `LIMIT_IOC` uses token-qty semantics (operator wants up to N tokens, will spend whatever that costs ≤ budget).

#### 5.2 — Wire into EV gate — use the SELECTED token's book

**Critical:** the existing metadata only passes `yes_token_id` into the orderbook processor in `bot.py`. For a NO trade the fill estimator must walk the **NO** asks, not the YES asks. Using the YES book for a NO trade evaluates the wrong executable market.

Required changes to `_fetch_market_context`:
1. Fetch both YES and NO books once per decision.
2. Store on the strategy as `self._latest_yes_book` and `self._latest_no_book`.
3. The `OrderBookImbalanceProcessor` continues to use the YES book for its imbalance metric (unchanged signal logic).
4. The EV gate selects the book matching the trade direction.

**Phase 5A wiring (before Phase 3):** apply the market-depth estimator to the current market-IOC path only. This avoids reading `ORDER_TYPE` or `limit_price` before Phase 3 owns those concepts.

```python
if direction == "long":
    side_levels = self._latest_yes_book["asks"]
else:
    side_levels = self._latest_no_book["asks"]

side_label = "NO" if direction == "short" else "YES"
resolved_trade_usd = self._resolved_trade_usd_for_this_decision

estimated_avg, tokens_filled, fully_filled = estimate_market_ioc_fill(
    side_levels, resolved_trade_usd
)
if estimated_avg is None or tokens_filled <= 0:
    logger.warning(f"MARKET_IOC: no executable {side_label} liquidity")
    return False
if not fully_filled:
    logger.warning(
        f"MARKET_IOC: book too thin for full ${resolved_trade_usd} {side_label} sweep — "
        f"only ${tokens_filled * estimated_avg:.2f} available"
    )
    return False
executable_entry = estimated_avg
```

**Phase 5B wiring (after Phase 3):** once Phase 3 introduces `ORDER_TYPE`, the EV-accepted `limit_price`, the safely rounded `submitted_limit_price`, and target-token sizing, replace the Phase 5A market-only call with an explicit order-type branch:

`resolved_trade_usd` in this pseudocode is the exact per-decision budget passed to `_place_real_order` after fixed/percent sizing and risk checks. Do not re-read a module-level position-size constant inside the depth gate; otherwise Phase 2.5 percent sizing and Phase 5B liquidity checks can disagree about the order being evaluated.

```python
if order_type == "market_ioc":
    estimated_avg, tokens_filled, fully_filled = estimate_market_ioc_fill(
        side_levels, resolved_trade_usd
    )
    if estimated_avg is None or tokens_filled <= 0:
        logger.warning(f"MARKET_IOC: no executable {side_label} liquidity")
        return False
    if not fully_filled:
        logger.warning(
            f"MARKET_IOC: book too thin for full ${resolved_trade_usd} {side_label} sweep — "
            f"only ${tokens_filled * estimated_avg:.2f} available"
        )
        return False
    executable_entry = estimated_avg
elif order_type == "limit_ioc":
    target_token_qty = Decimal(str(resolved_trade_usd)) / submitted_limit_price
    worst_case_submitted_notional = target_token_qty * submitted_limit_price
    estimated_avg, tokens_filled, actual_cost, fully_filled = estimate_limit_ioc_fill(
        side_levels, target_token_qty, max_price=submitted_limit_price
    )
    if estimated_avg is None or tokens_filled <= 0:
        logger.warning(
            f"LIMIT_IOC: no executable {side_label} liquidity at price <= {submitted_limit_price}"
        )
        return False
    if limit_ioc_fill_policy == "all_or_nothing":
        raise RuntimeError(
            "LIMIT_IOC_FILL_POLICY=all_or_nothing requires verified FOK wire behavior; "
            "current LIMIT+IOC wire behavior is FAK"
        )
    if limit_ioc_fill_policy == "partial_ok" and not fully_filled:
        logger.warning(
            f"LIMIT_IOC partial_ok: only {tokens_filled} of {target_token_qty} "
            f"{side_label} tokens fillable at price <= {submitted_limit_price} "
            f"(actual cost would be ${actual_cost:.2f})"
        )
    executable_entry = estimated_avg
    executable_cost = actual_cost
    risk_notional = worst_case_submitted_notional
else:
    raise RuntimeError(f"unexpected ORDER_TYPE after validation: {order_type!r}")
```

The `LIMIT_IOC` path uses `target_token_qty` and reports the estimated executable cost (which may be less than the budget) in the log. Under `partial_ok`, positive executable liquidity may proceed to the EV gate using the estimated VWAP and estimated executable cost. Risk checks, free-collateral checks, pre-submit intent, and exposure reservation must still use the worst-case submitted notional (`target_token_qty * submitted_limit_price`, equivalent to `resolved_trade_usd` before quantization) because the exchange can fill the full submitted IOC limit quantity if liquidity changes before arrival. If quantization makes the worst-case submitted notional exceed the resolved budget, reject before submission. Under `all_or_nothing`, the bot must fail closed until true exchange-enforced FOK behavior exists. The EV gate downstream uses `executable_entry` (VWAP), which is the same field name as before — only the upstream computation differs by order type.

#### 5.3 — Cache the book once per decision

Current state: `OrderBookImbalanceProcessor` fetches the book during signal processing (YES only). Refactor so:
- `_fetch_market_context` fetches **both YES and NO books** once and stores on the strategy.
- The imbalance processor reads YES from the cache (unchanged behavior).
- The EV gate reads whichever side matches the trade direction.

### Exit criteria

- [ ] **Two estimators** exist with unit tests, NOT one unified helper: `estimate_market_ioc_fill(levels, usd_to_spend)` (budget-driven) and `estimate_limit_ioc_fill(levels, target_token_qty, max_price)` (token-driven). Book level units documented as token-quantity in both.
- [ ] Phase 5A: EV gate uses estimated avg price from the **selected token's book** (YES for long, NO for short), not top-of-book, for `MARKET_IOC`.
- [ ] Phase 5B: after Phase 3, `LIMIT_IOC` uses `estimate_limit_ioc_fill(...)`; live resume is blocked until this is wired and tested.
- [ ] Single YES book fetch and single NO book fetch per decision (no duplicate HTTP per side).
- [ ] Test: synthetic book `[{"price": "0.62", "size": "10"}, {"price": "0.70", "size": "15"}]` (i.e., 10 tokens at $0.62 and 15 tokens at $0.70) with $10 budget. First level USD capacity = `0.62 × 10 = $6.20`, second level capacity needed = `$3.80 / 0.70 ≈ 5.43 tokens`. VWAP ≈ `$10 / (10 + 5.43) ≈ 0.6481`. Assert returned avg matches.
- [ ] Test: empty book returns `fully_filled=False`.
- [ ] Test: book with only YES depth available, NO book empty, attempting a NO trade returns `fully_filled=False` and does not fall back to the YES book.
- [ ] Test (market): `estimate_market_ioc_fill(asks, usd_to_spend=$10)` with book `[{"price": "0.62", "size": "10"}, {"price": "0.70", "size": "15"}]` returns VWAP ≈ 0.6481, `tokens_filled ≈ 15.43`, `fully_filled=True` — sweeps through both levels.
- [ ] Test (limit, sufficient liquidity): `estimate_limit_ioc_fill(asks, target_token_qty=10, max_price=$0.50)` with book `[{"price": "0.40", "size": "10"}]` returns VWAP=$0.40, `tokens_filled=10`, `actual_cost=$4.00`, `fully_filled=True` — confirms the reviewer-flagged correctness case where USD budget would have under-counted.
- [ ] Test (limit, price cap): `estimate_limit_ioc_fill(asks, target_token_qty=20, max_price=$0.62)` with book `[{"price": "0.62", "size": "10"}, {"price": "0.70", "size": "15"}]` returns `tokens_filled=10`, `fully_filled=False` (stops at the cap, doesn't sweep through to $0.70).
- [ ] Test (limit submitted-price precision): an EV-accepted BUY cap such as `0.626` at 2-decimal price precision derives `submitted_limit_price=0.62`, and the depth estimator, token sizing, risk/free-collateral checks, pre-submit intent, and submitted Nautilus `Price.from_str` all use `0.62`.
- [ ] Test (Phase 5B caller, `partial_ok`): with `ORDER_TYPE=limit_ioc`, `LIMIT_IOC_FILL_POLICY=partial_ok`, target 20 tokens, and only 10 tokens executable under the cap, the depth gate proceeds to EV evaluation using `estimated_avg` and `actual_cost`, while risk/free-collateral checks, pre-submit intent, and exposure reservation use worst-case submitted notional (`target_token_qty * submitted_limit_price` / `resolved_trade_usd`).
- [ ] Test (Phase 5B caller, no executable liquidity): with `ORDER_TYPE=limit_ioc` and no asks at or below the limit price, the depth gate rejects before order submission.
- [ ] Test (Phase 5B caller, `all_or_nothing` under current FAK mapping): startup/runtime validation fails closed before order submission.

### Effort

1 day total: 0.5 day for Phase 5A market-depth wiring and shared tests; 0.5 day for Phase 5B limit-depth integration after Phase 3.

---

## Phase 6 — SOPS Credential Management (Future)

**Status:** Not urgent. Tracking only.

### Motivation

Plaintext `.env` is a known risk. SOPS-encrypted credentials decrypted into process environment at runtime is the standard pattern.

### Approach

```bash
sops exec-env .env.sops.yaml 'venv/bin/python bot.py --live'
```

### Code changes required

If we adopt this:

1. **Refuse plaintext `.env` in live mode — the check MUST run before `load_dotenv()`.**

   **Implementation note (corrected from earlier draft):** the current code calls `load_dotenv()` at module import time in `bot.py`, before `run_integrated_bot` runs. If the plaintext-refusal check is placed inside `run_integrated_bot`, the plaintext `.env` has already been read into `os.environ` by then — the refusal is too late to prevent the leak.

   Operator must approve exactly one implementation before Phase 6 work starts. Do not implement both and do not add runtime fallback between them:

   **Pattern A: move `load_dotenv` behind a conditional.**

   ```python
   # At module-level import path in bot.py, BEFORE load_dotenv()
   import sys
   _is_live_invocation = "--live" in sys.argv  # or check env var BOT_LIVE_MODE=1
   _plaintext_env_present = Path(__file__).parent.joinpath(".env").exists()

   if _is_live_invocation and _plaintext_env_present:
       raise RuntimeError(
           "Live mode refuses to start with plaintext .env present. "
           "Use sops exec-env to inject credentials, or move .env outside the repo."
       )

   if not _is_live_invocation:
       load_dotenv()    # simulation/test still uses plaintext .env normally
   # In live mode, load_dotenv is skipped; credentials must come from
   # the parent process environment (typically `sops exec-env`).
   ```

   **Pattern B: always skip `load_dotenv` in live mode regardless of .env presence.**

   Same effect, no `.env`-file inspection. Operator is responsible for ensuring credentials are in the process environment via `sops exec-env` or systemd or shell `export`. This is the simpler invariant but breaks any operator who relied on plaintext `.env` for live mode (intentionally).

   Both patterns must check `--live` (or equivalent) at module-import time, before `load_dotenv` is invoked. The earlier draft's `Path(".env").exists()` check inside `run_integrated_bot` would not have prevented the plaintext from being read into the process environment.

2. **Document the SOPS workflow in README.** Include the wrapper invocation and an example `.env.sops.yaml` shape.
3. **Add `.env.sops.yaml.example`** showing structure of encrypted credentials.
4. **Update the wrapper script** (if used) to invoke `sops exec-env` in live mode.

### Effort

0.5 day, but only after team decides on a SOPS key management approach.

---

## Phase 7 — Live Env Reload (Future, possibly out of scope)

**Status:** Operational request, harder than it looks.

### Current behavior

- `load_dotenv()` at startup loads `.env` into `os.environ`. Once loaded, edits to `.env` don't propagate.
- Some vars (`MARKET_BUY_USD`, EV buffers, etc.) are read fresh per decision via `os.getenv` — these *do* pick up environment changes, but only if the environment itself changes (e.g., from outside the process).
- Risk engine vars are read once at `__init__`.

### What "live reload" would require

Option A — **External signal:**
- Operator runs `kill -HUP <pid>` or similar.
- Bot's signal handler calls `load_dotenv(override=True)` and rebuilds the risk engine.
- Risk: changing `MAX_POSITION_SIZE` mid-flight with positions open is hard to make consistent.

Option B — **Redis-backed config:**
- Risk limits and other tunable config live in Redis hashes.
- Bot polls Redis every N seconds or subscribes via pub-sub.
- Already have Redis infrastructure for mode switching.
- Cleaner separation from credentials (which stay in env).

Option C — **Don't implement.** Operator restarts the bot to change risk limits. Document this clearly.

### Recommendation

**Option C for now.** The bot has 90-minute auto-restart anyway. No code change needed.

**Operator workflow depends on which credential mode is active:**

- **Before Phase 6 (plaintext `.env`):** operator edits `.env`, kills the bot, waits <90s for the wrapper to restart it, new values are live.
- **After Phase 6 (SOPS adopted):** operator edits the encrypted `.env.sops.yaml`, kills the bot, the wrapper restart re-invokes `sops exec-env` which decrypts fresh values into the process environment. **Plaintext `.env` editing no longer applies** — Phase 6 refuses to start in live mode with plaintext `.env` present.

This resolves the prior Phase 6/7 wording conflict. Phase 7's "edit and restart" workflow always refers to the configured credential source, whether plaintext or SOPS.

If operator wants finer-grained control later, Option B (Redis-backed risk config) is the right design — same pattern as the existing simulation_mode switch.

### Effort

0 days (Option C, document only) or 2 days (Option B, future).

---

## Implementation Order

Strict dependency order for the mandatory live-resume path:

```
Phase 0 (lost-fill reconciliation + fill guards) ─────┐
                                                      │
                                                      ▼
                                       Phase 1 (env audit + .env.example)
                                                      │
                                                      ▼
                                       Phase 2 (operator config + balance pre-flight)
                                                      │
                                                      ▼
                                       Phase 2.4 (structured decisions.jsonl writer)
                                                      │
                                                      ▼
                                       Phase 2.5 (dynamic sizing: fixed | percent) ── NEW, operator-requested
                                                      │
                                                      ▼
                                       Phase 5A (market-depth estimator + per-side book)
                                                      │
                                                      ▼
                                       Phase 3 (ORDER_TYPE + quote stability)
                                                      │
                                                      ▼
                                       Phase 5B (LIMIT_IOC depth integration)
                                                      │
                                                      ▼
                                       Mandatory live-resume safety gate
                                                      │
                                                      ├── before scaling or strategy-policy changes
                                                      ▼
                                       Phase 4 (calibration analysis)
                                                      │
                                                      ▼
                                       Phase 4.5 (strategy timing + price-band evaluation)
                                                      │
                                                      ▼
                                       Phase 7.5 (multi-asset evaluation: ETH/SOL/XRP/...) ── NEW, operator-driven
                                                      │
                                                      ▼
                                       Phase 8 (Linux deployment: systemd, backups, monitoring) ── NEW
                                                      │
                                                      ▼
                                       Phase 6 (SOPS, future, fits into Phase 8's env-injection slot)
                                       Phase 7 (live reload — recommend: don't implement)
```

Phase 2.4 must ship before Phase 2.5 because the dynamic sizing rejection gates are recorded through `decisions.jsonl`. Phase 2.5 then adds the percent-mode option after Phase 2 confirms the operator's fixed-mode sizing target. Phase 3 and Phase 5B are now on the mandatory live-resume path because live trading must support explicit `ORDER_TYPE=limit_ioc` with configured quote stability before routine use. Phase 4.5 runs after baseline calibration because timing/price-band changes must be measured against the existing `13_14` and `0.60/0.40` policy without loosening the EV gate; those strategic phases gate scaling and policy changes, not the mandatory price-cap safety implementation. Phase 7.5 is operator-driven evaluation only (no code in this phase) and gates whether Phase 8 deploys for BTC alone or for a wider asset set. Phase 8 follows Phase 7.5 so the deployment is built around the operator's final asset selection.

### Critical path

- **Must ship before next live run:** Phase 0.7 recovery fully resolved, Phase 0 fill guards in place, Phase 0.5a audit completed, Phase 5A selected-token depth available, mandatory Phase 3 `ORDER_TYPE` + `QUOTE_STABILITY_REQUIRED` wiring complete, Phase 5B `LIMIT_IOC` depth integration complete, and the live config explicitly set by the operator. For the current verified FAK path, routine resume requires the operator to set `ORDER_TYPE=limit_ioc`, `QUOTE_STABILITY_REQUIRED=3`, and `LIMIT_IOC_FILL_POLICY=partial_ok`.
- **Phase 1 status:** Phase 1.1 (wire `SPIKE_THRESHOLD` / `DIVERGENCE_THRESHOLD` to env) is **runtime-behavior** code and DOES block live if the operator intends to tune via env. Phase 1.2 / 1.3 (README + `.env.example`) are documentation-only and do NOT block live. Effort table updated accordingly: Phase 1.1 blocks, 1.2/1.3 do not.
- **Should ship before scaling trade size:** Phase 4 calibration and Phase 4.5 timing/price-band evaluation.
- **Strategic decision gate:** Phase 4 must satisfy **all three** of the gates in 4.3 (dollar-weighted realized return, probability-edge CI cross-check, out-of-sample persistence) in at least one bucket with **n ≥ 100** Path A trades AND ≥200 Path B observations, and Phase 4.5 must show that the selected timing/price-band policy is at least as good as the current late-window baseline. A single positive bucket at small n is noise. The earlier "n ≥ 50" wording was inconsistent and is replaced everywhere with n ≥ 100. If calibration or Phase 4.5 fails, the fix is to improve signal processors or policy selection, not to change order type or loosen the EV gate. Mandatory Phase 3/5B price-cap safety still remains part of live resume.

### Important caveat

Phase 0 does **not** by itself "unblock" live trading. Reconciling the lost trade creates a `SETTLEMENT_UNKNOWN` record which keeps live trading paused until the operator resolves it with a verified payout (`--order-id ... --payout ...`). Live trading resumes only after that second-step resolution.

---

## Phase 7.5 — Multi-Asset Market Evaluation (placeholder, operator-driven)

**Status:** Evaluation only. No implementation in this phase. Operator will decide the details.

**Purpose:** Before committing to a production deployment focused on BTC 15-min markets, evaluate whether to expand the bot to other Polymarket 15-min asset markets (ETH, SOL, XRP, and any other liquid crypto markets Polymarket lists).

**Scope of this phase:** decision-making, not coding. The operator drives the evaluation; the plan reserves a sequence slot for it between Phase 7 (live reload, deferred) and Phase 8 (Linux deployment) so that whatever asset set the operator chooses is reflected in the deployment configuration.

Open questions to be answered by the operator during this phase (no details required from the plan now):

- Which additional assets, if any, to add.
- Whether each asset uses the same signal-processor mix or requires asset-specific tuning.
- Whether each asset uses the same risk-engine caps or per-asset caps.
- Whether the bot runs as one process trading multiple assets, or one process per asset.
- Whether calibration (Phase 4) needs to pass independently per asset before live trading on it.

### Effort

Operator-driven evaluation. No bot-code effort estimated until the operator returns decisions; implementation effort will be planned in a follow-up phase if any assets are approved.

### Exit criteria

- [ ] Operator documents the decision (add / don't add / partial) with reasoning.
- [ ] If any assets are approved, follow-up phase(s) are scoped before Phase 8 deployment begins.

---

## Phase 8 — Linux Deployment (high-level, operator-facing)

**Status:** Operator-requested. Documents the production deployment shape. Independent of code changes. Most of this is operator/sysadmin work, not bot work.

### Target environment

- Linux server (Ubuntu LTS, Debian stable, or RHEL-derivative — operator's choice).
- Python 3.10+ in a dedicated virtualenv at `/opt/polybot/venv` (path configurable).
- Bot source at `/opt/polybot/Polymarket-BTC-15-Minute-Trading-Bot/` (or `/srv/polybot`, etc. — operator choice).
- Dedicated unprivileged service user `polybot` owning the source tree and ledger.

### File layout

```
/opt/polybot/
├── venv/                          # Python venv (chmod 750 polybot:polybot)
├── Polymarket-BTC-15-Minute-Trading-Bot/  # git clone, owner polybot
│   ├── bot.py
│   ├── mark_settlement_resolved.py
│   ├── ... (rest of the repo)
├── ledger/
│   ├── live_trades.json           # primary ledger (chmod 640 polybot:polybot)
│   ├── live_trades.json.lock      # fcntl lock file (chmod 640)
│   ├── decisions.jsonl            # Phase 4 decision log
│   └── archive/                   # rotated decisions.jsonl files
└── logs/
    ├── bot.log                    # rotated by logrotate
    └── nautilus/                  # Nautilus TradingNode logs
```

The ledger directory MUST be on the same filesystem as the temp-file write target so `os.replace` is atomic. Do not put `live_trades.json` on NFS or a separate mount from `/opt/polybot/ledger`.

**Current-code path mismatch to fix before enabling this unit:** `bot.py` currently defaults the live ledger to repo-root `live_trades.json` and Nautilus logs to `./logs/nautilus` under the working directory. With `ProtectSystem=strict`, those defaults conflict with the service sandbox below. Phase 8 must set `LIVE_TRADE_LEDGER_PATH=/opt/polybot/ledger/live_trades.json` and must change the Nautilus `LoggingConfig.log_directory` wiring to use a required live-mode env var such as `NAUTILUS_LOG_DIR=/opt/polybot/logs/nautilus`. Do not rely on repo-root write access.

### systemd service file

`/etc/systemd/system/polybot.service`:

```ini
[Unit]
Description=Polymarket BTC 15-Min Trading Bot
After=network-online.target redis.service
Wants=network-online.target

[Service]
Type=simple
User=polybot
Group=polybot
WorkingDirectory=/opt/polybot/Polymarket-BTC-15-Minute-Trading-Bot

# Env loading — pick ONE based on Phase 6 SOPS adoption
# Pre-SOPS (plaintext .env in repo, NOT for production credentials):
#   EnvironmentFile=/opt/polybot/Polymarket-BTC-15-Minute-Trading-Bot/.env
# Post-SOPS (preferred for production):
#   ExecStart=/usr/local/bin/sops exec-env /opt/polybot/secrets/.env.sops.yaml '/opt/polybot/venv/bin/python bot.py --live'

# Pre-SOPS form:
EnvironmentFile=/opt/polybot/Polymarket-BTC-15-Minute-Trading-Bot/.env
Environment=LIVE_TRADE_LEDGER_PATH=/opt/polybot/ledger/live_trades.json
Environment=NAUTILUS_LOG_DIR=/opt/polybot/logs/nautilus
ExecStart=/opt/polybot/venv/bin/python bot.py --live

Restart=no

# Resource limits — modest, the bot is mostly I/O bound
MemoryMax=1G
TasksMax=200

# Logging to journald (paired with logrotate / journalctl --vacuum-size)
StandardOutput=journal
StandardError=journal
SyslogIdentifier=polybot

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/polybot/ledger /opt/polybot/logs
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
```

Critical notes:

- **`Restart=no`.** The bot's Phase 0 design fail-stops on durable-write failure. systemd must not automatically restart after fail-stop because restart would weaken the manual-reconciliation guarantee. If the operator later wants automatic restart for non-ledger crashes, add exact exit-code separation and get explicit approval before changing this unit.
- **No `Restart=always` or `Restart=on-failure`.** Those would clear or weaken the fail-stop guarantee.
- **`User=polybot`, not root.** Containment in case of a compromise.
- **`ProtectSystem=strict` + `ReadWritePaths`.** The bot can only write to ledger and logs directories. This requires the `LIVE_TRADE_LEDGER_PATH` and `NAUTILUS_LOG_DIR` wiring above; otherwise current repo-root defaults will fail under the sandbox.
- **`MemoryMax=1G`.** The bot's working set is small; a runaway memory leak would be killed before exhausting the host.

### Redis dependency

Live-enabled deployments require Redis for runtime pause/resume mode control. Either:

- Install `redis-server` from distro packages and set `After=redis.service` in the unit file (shown above).
- Or run Redis in Docker / on a different host; remove the `After=redis.service` line and set `REDIS_HOST` / `REDIS_PORT` env vars accordingly.

### logrotate

`/etc/logrotate.d/polybot`:

```
/opt/polybot/logs/bot.log /opt/polybot/ledger/decisions.jsonl {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0640 polybot polybot
    sharedscripts
}
```

The bot does not natively rotate `decisions.jsonl`; the Phase 4 implementation should either rotate based on size/date inside the bot OR rely on this external logrotate config. Document the choice.

### Backups (P0 for production)

`live_trades.json` is the source of truth for settled trades. Hourly backup to a separate volume / S3 bucket / off-host location:

```cron
# /etc/cron.d/polybot-ledger-backup
0 * * * * polybot /usr/local/bin/aws s3 cp /opt/polybot/ledger/live_trades.json s3://polybot-backups/live_trades-$(date -u +\%Y\%m\%dT\%H\%M).json
```

(Or rsync to a backup host, or `cp` to a different filesystem — operator choice.)

### Monitoring + alerting (P1)

Minimum:

- **Process up.** systemd handles restart; alert if `polybot.service` enters a `failed` state for >5 minutes (Prometheus node_exporter + alertmanager, or Datadog, or simple cron + email).
- **Live-trading paused.** Alert if `live_trades.json` contains any unresolved ledger state: a settled record with `settlement_source == "SETTLEMENT_UNKNOWN"` or `needs_reconciliation == true`, any `pending_actual_fills` entry, any unresolved `submitted_order_intents` entry, or a `LEDGER_BLOCKED` state reported by the bot. A cron that `jq` queries the durable ledger every 5 minutes can cover the file-backed cases; the process log/metrics must cover the in-memory ledger-blocked marker.
- **Daily P&L.** A separate cron job tails recent settled trades and reports the day's realized P&L.
- **Disk space on the ledger filesystem.** Standard `node_exporter` filesystem alert.

The bot also exposes Prometheus metrics on port 8000 (see existing `grafana_exporter.py`). Set up Grafana dashboards for those metrics if a Grafana instance is available.

### Deployment workflow

```
operator → ssh server
       → cd /opt/polybot/Polymarket-BTC-15-Minute-Trading-Bot
       → git pull
       → /opt/polybot/venv/bin/pip install -r requirements.txt  (if changed)
       → systemctl stop polybot
       → verify /opt/polybot/ledger/live_trades.json is already current schema v3, or start with no existing ledger for a fresh deployment
       → systemctl start polybot
       → journalctl -u polybot -f  (watch startup; verify no SettlementLedgerError)
```

**Critical:** this plan does not include any ledger migration. For a fresh deployment, either start with no existing ledger or provide a ledger that is already exact current schema v3. If an old or malformed ledger is present, startup/admin tooling must fail closed; the operator must replace it outside the application rather than relying on application code to transform it.

### Security checklist

- Polymarket private key in `.env` (or `.env.sops.yaml` after Phase 6). Never commit to git.
- `.env` file mode `0600 polybot:polybot`.
- Bot user has no shell (`/usr/sbin/nologin` in `/etc/passwd`) — operator interacts via systemctl, journalctl, and the admin tool only.
- Firewall: outbound to `clob.polymarket.com:443`, `gamma-api.polymarket.com:443`, `data-api.polymarket.com:443`, Polygon RPC endpoint, Redis (if remote). No inbound except SSH (operator) and Grafana port if remote-monitored.
- The `mark_settlement_resolved.py` tool requires shell access to the server. Restrict ssh accordingly.

### What this phase does NOT include

- Container orchestration (Kubernetes / Nomad). Out of scope. The bot is a long-running single process; a single systemd service on one VM is sufficient for the operator's described scale.
- Blue-green / canary deploys. Same reason — single process.
- Database. The ledger is JSON on local disk. No SQLite, no Postgres. Adding a database is a future enhancement, not a P0/P1 deployment requirement.
- High availability. The bot is single-instance; fcntl locking prevents two instances from running simultaneously. If the operator wants HA, that's a redesign.

### Effort

1 day for the operator to set up the server, systemd unit, logrotate, backups, and basic monitoring, plus a small bot config patch for `NAUTILUS_LOG_DIR` if it has not already landed. Do not deploy the systemd sandbox until the path wiring is verified.

### Exit criteria

- [ ] `systemctl status polybot` shows `active (running)` after a normal start.
- [ ] Startup logs show `LIVE_TRADE_LEDGER_PATH=/opt/polybot/ledger/live_trades.json` and Nautilus log directory `/opt/polybot/logs/nautilus`.
- [ ] `systemctl stop polybot` cleanly stops the bot; the fcntl lock is released; journalctl shows the `on_stop` cleanup running.
- [ ] After a deliberate fail-stop (e.g., write-protect the ledger filesystem to trigger a SettlementLedgerError), systemd does not restart the service at all; `Restart=no` leaves it stopped until explicit operator action.
- [ ] Hourly ledger backup script runs and produces dated artifacts.
- [ ] Alerting rule for every live-blocking unresolved class fires in test scenarios: settled `SETTLEMENT_UNKNOWN` / `needs_reconciliation`, `pending_actual_fills`, unresolved `submitted_order_intents`, and `LEDGER_BLOCKED`.

---

## Total Effort Estimate

| Phase | Effort | Type | Blocks live? |
|---|---|---|---|
| 0. P0 fill bug (incl. 0.5 units-mismatch patch + 0.5a Nautilus 1.227.0 audit) | 3 days + 0.5a audit TBD | Code + dependency audit + manual recovery | **Yes** |
| 1.1. Env wiring (`SPIKE_THRESHOLD`/`DIVERGENCE_THRESHOLD`) | 0.25 day | Runtime code | **Yes** (only if operator wants env-tunable values; otherwise document constants as code-owned) |
| 1.2-1.3. Env audit docs + `.env.example` | 0.25 day | Documentation | No (cleanup) |
| 2. Sizing config + balance pre-flight | 10 min | Operator action | No (operator decision) |
| 2.4. Structured `decisions.jsonl` writer | 0.5 day | Code | No (prerequisite for 2.5 + Phase 4 data) |
| **2.5. Dynamic trade sizing + balance freshness (NEW)** | **2 days** | **Code (AccountState hook + sizing modes + stale-balance invalidation)** | **No (operator-requested sizing enhancement)** |
| 5A. Market-depth estimator + selected-token book cache | 0.5 day | Code | Yes - prerequisite for mandatory Phase 5B live safety |
| 4. Calibration | 4h + elapsed time for data | Analysis | No (decision gate before scaling and strategy-policy changes) |
| 4.5. Strategy timing + price-band evaluation | 0.5-1 day + elapsed observations | Code + analysis | No (decision gate; no live-policy change without approval) |
| 3. ORDER_TYPE + quote stability | 3 days | Code | Yes - mandatory live-resume blocker |
| 5B. LIMIT_IOC depth integration | 0.5 day | Code | Yes - mandatory before live `limit_ioc` |
| **7.5. Multi-asset evaluation (NEW)** | **Operator-driven, no code** | **Decision** | **No (gates deployment scope)** |
| 6. SOPS | 0.5 day | Code + ops | No (future) |
| **8. Linux deployment (NEW)** | **1 day operator** | **Ops scaffolding** | **No (production deploy)** |
| 7. Live reload | 0 days (Option C) | Docs only | No |

**Minimum to resume live trading safely:** Phase 0.7 recovery + Phase 0 fill guards + Phase 0.5a audit + Phase 5A selected-token depth + mandatory Phase 3 + Phase 5B limit-price order path + operator-explicit live config. For the current verified FAK path, routine resume requires `ORDER_TYPE=limit_ioc`, `QUOTE_STABILITY_REQUIRED=3`, and `LIMIT_IOC_FILL_POLICY=partial_ok`. Market-IOC-only live trading is no longer an approved resume state.

**Recommended before scaling:** add Phases 2.4 + 4 + 4.5 after the mandatory live-resume path. Phase 5A is already in the minimum live-safety path, not a scaling-only add-on.

**Full enhancement (everything beyond mandatory limit-price safety):** remaining calibration/observation windows plus deployment hardening.

---

## Open Decisions Required From Operator

1. **Trade size for live operation.** The intended normal config is `$55 / $385 / 7 positions / $110 daily loss`. The `$5.51 / $22.04` config is smoke-test-only after Phase 0.7 recovery, mandatory Phase 3, and Phase 5B, not the normal target.
2. **Signal threshold env wiring.** `STOP_LOSS_PCT` / `TAKE_PROFIT_PCT` are resolved as not implemented as active controls and removed from operator config; remaining decision is whether to wire or remove `SPIKE_THRESHOLD` / `DIVERGENCE_THRESHOLD`.
3. **Calibration data source.** Are there ≥100 settled live trades available? If not, what's the data collection plan?
4. **`ORDER_TYPE` for live mode.** Live mode requires an explicit value. No implicit default in code. `.env.example` now shows the routine explicit resume example `ORDER_TYPE=limit_ioc`; choosing `market_ioc` means the operator explicitly accepts book-sweep price risk. This matches the `AGENTS.md` no-silent-fallback rule.
5. **`QUOTE_STABILITY_REQUIRED` for live mode.** Live mode requires an explicit positive integer. Normal live config is `QUOTE_STABILITY_REQUIRED=3`; changing it is an operator decision because it directly affects whether a submitted limit price was computed from a stable quote stream.
6. **`LIMIT_IOC` partial-fill policy.** Live resume is blocked until operator explicitly chooses `partial_ok` or `all_or_nothing`; there is no plan default. This decision is mandatory before live resume because `limit_ioc` is now part of the minimum safety path.
7. **Strategy timing/price-band policy.** After Phase 4.5 reports the baseline vs candidate windows/bands, operator must explicitly approve either "keep current `13_14` + `0.60/0.40`" or one exact replacement policy. No automatic switch based on analysis output.
8. **SOPS adoption timeline.** Phase 6 is ready when the team's key management approach is decided.

---

## Lost Trade Recovery — Action Items (single canonical sequence)

The trade `BTC-15MIN-$11-1779093783343` is real on Polymarket but missing from the bot's ledger. Earlier drafts contained contradictory ordering — some sections said recover first, others said recover last. **This is the single canonical sequence:**

1. **Stop live trading now.** Kill the bot process and the wrapper. Do not place any new live orders.
2. **Ship Phase 0.1 (callback scaffold + durable unknown helper + pre-submit intent audit + admin selectors).** This must land before the zero-price guard because the guard calls `_create_durable_settlement_unknown_from_actual_fill`. It must include first-class `--venue-order-id` admin-tool support and `submitted_order_intents` admin resolution, not a synthetic `venue:<hash>` order-id fallback.
3. **Verify fresh v3 ledger shape.** Do not run any schema migration. Start with no existing ledger, or provide a `live_trades.json` that is already exact current schema v3 with `pending_actual_fills` and `submitted_order_intents`. If an old/interim/malformed ledger exists, startup/admin tooling must fail closed and the operator must replace it outside the application.
4. **Ship Phase 0.2 (zero-price guard).** It must create durable unknowns, not only process-local blocks.
5. **Ship Phase 0.3 + 0.4 + 0.5 + 0.5a + 0.6 (VWAP injection + dust normalization + quote-quantity units fix + Nautilus 1.227.0 audit + tests).** Single adapter/dependency series with full test coverage. Do not resume live trading until 0.5a answers whether current-instrument one-sided quote drops are reducing trade decisions.
6. **Verify externally.** Open the Polymarket UI (or query on-chain) to obtain the actual payout for the lost order `BTC-15MIN-$11-1779093783343` and the exact submitted/filled/market-end timestamps from logs or Polymarket records.
7. **Reconstruct the unknown record.** Run `venv/bin/python mark_settlement_resolved.py --create-unknown-from-external-order ...` per the Phase 0.7 command, using actual values (no placeholders).
8. **Immediately resolve with verified payout.** Run `venv/bin/python mark_settlement_resolved.py --order-id ... --payout <verified> ...` **without waiting** for `auto_redeem` or the grace timeout. Polymarket does not replay missed websocket events; the market has already resolved; the bot will not receive any future `auto_redeem` for this trade.
9. **Confirm no remaining unresolved entries.** The live-trading pause gate in `bot.py` uses **OR**, not AND, across all unresolved classes: any settled record with `needs_reconciliation is True` **OR** `settlement_source == "SETTLEMENT_UNKNOWN"`, any `pending_actual_fills` entry, any unresolved `submitted_order_intents` entry, or the in-memory `LEDGER_BLOCKED` marker. Inspect `live_trades.json` and process logs/metrics and confirm zero live-blocking unresolved states remain. A reconstructed-but-unresolved entry (which has both settled flags set) will still pause live; a partially-resolved entry that updated only one flag will also still pause live. Resolution must set `settlement_source` to a real value (e.g., `"manual_reconciliation"`) AND set `needs_reconciliation` to `false`, and all pending actual fills/submitted intents must be explicitly converted or resolved.
10. **Smoke-test before resuming.** Start the bot in `--test-mode` to verify decision/test startup is not blocked by the live-only adapter guard. Verify the new adapter callback path with the Phase 0 unit/integration harness. After mandatory Phase 3 and Phase 5B are complete, run one deliberately tiny allowed `$5.51` live smoke trade using `ORDER_TYPE=limit_ioc`, `QUOTE_STABILITY_REQUIRED=3`, and `LIMIT_IOC_FILL_POLICY=partial_ok`; confirm the actual-fill side channel + ledger record + `auto_redeem` settlement all complete cleanly. Any `market_ioc` smoke trade is a separate explicit operator-approved risk test and is not part of routine live resume.
11. **Then, and only then, resume normal live trading with the `$55 / $385 / 7 positions / $110 daily loss` config and routine `ORDER_TYPE=limit_ioc`.**

**Earlier contradictions resolved:**
- The "recover before any other work" wording is dropped. The scaffold must ship before the zero-price guard, and the full adapter normalization must ship before recovery to prevent the next bad fill from re-creating the same problem during reconciliation.
- The "wait for `auto_redeem` or grace timeout" wording is dropped everywhere. The lost market has already resolved; waiting accomplishes nothing and risks a $0 grace-timeout booking before the operator's manual resolution.

This sequence must complete before resuming live trading. The risk engine has a phantom ~$11 exposure missing from its tracking until step 8 finishes.
