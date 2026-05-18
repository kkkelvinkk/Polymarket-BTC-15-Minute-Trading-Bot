# Polymarket BTC 15-Min Bot — Execution Plan

This document lays out the full sequence of fixes and enhancements before the next live run. Each phase has a clear purpose, scope, exit criteria, and effort estimate. Phases are ordered by criticality, not by ambition.

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

```
report.avg_px was None
Generated inferred OrderFilled ... last_qty=17.460316, last_px=0.00
Order overfill rejected ... quantity=17.460300
```

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

### Fix order — corrected per reviewer feedback (resolves prior ordering contradictions)

Earlier drafts had two ordering problems:
- 0.3 (dust normalization with callback) depended on VWAP from 0.2.
- 0.4 (zero-price guard) was listed first, but the guard's durable-unknown call requires `_create_durable_settlement_unknown_from_actual_fill` which is introduced in 0.3a's scaffold.

The guard is no longer a "standalone 10-line first commit" — it depends on the durable helper. Corrected sequence:

1. **0.3a (now first): scaffold** — `register_actual_fill_handler`, `_dispatch_actual_fill`, the strategy-side handler, AND `_create_durable_settlement_unknown_from_actual_fill`. The full callback + durable-unknown machinery, with no adapter integration yet. The callback signature is `(client_order_id: str, payload: dict)`. Includes the ledger-schema migration for `pending_actual_fills`. Includes `on_stop` unregister. Sized at ~1 day.
2. **0.4: zero-price ledger guard.** Now depends on 0.3a's `_create_durable_settlement_unknown_from_actual_fill`. The guard's call site uses the helper to create a durable record when a non-positive `fill_price` reaches the recorder.
3. **0.2:** `get_trades`-based VWAP injection in the adapter report normalization (`PolymarketExecutionClient.generate_order_status_report` per 0.2 above). The adapter dispatches VWAP through the callback wired in 0.3a.
4. **0.3b:** dust normalization in the same adapter patch. Adapter clips for Nautilus but uses the callback (already wired in 0.3a, populated in 0.2) to carry the actual filled units through to the ledger.
5. **0.5:** regression tests covering all of the above (zero-price guard, scaffold, get_trades injection, dust normalization, pending_actual_fills schema migration).
6. **0.1 (last):** manual recovery of the lost `$11` trade. By this point all adapter normalization is in place and tests are green. Running the admin tool last means no further bad fills can corrupt the ledger during the recovery window.

**Why this order works:** 0.3a builds the seam (callback + durable helper + schema migration) that everything else uses. 0.4 (the zero-price guard) is no longer "ship first as a 10-line commit" — it's ship-second-and-still-small, because the durable helper it calls must exist first. 0.2 and 0.3b are independent commits that both produce events through the seam.

**Reviewer-flagged earlier mistake:** the previous draft promised "0.4 ships first as ~10 lines" but the actual implementation needs `_create_durable_settlement_unknown_from_actual_fill` to be defined. Either we ship 0.4 with only the process-local block (no durable record — defeats the point), or we ship the helper first. The corrected ordering above does the latter.

#### 0.1 — Recover the lost trade (done last, after 0.2-0.5 are merged)

**Operational prerequisites:**

- **Stop the bot first.** Run `kill -TERM <pid>` against both the wrapper and `bot.py`. Confirm no `python bot.py` process remains. The admin tool acquires the `live_trades.json.lock` and refuses to run while the bot holds it.
- **Use the venv Python.** Always `venv/bin/python mark_settlement_resolved.py`, never system `python`. The admin tool depends on the same `Decimal` discipline as the bot.
- **Pass `--ledger` if the bot was running with a custom `LIVE_TRADE_LEDGER_PATH`.** The tool defaults to `LIVE_TRADE_LEDGER_PATH` env var, then `./live_trades.json`. If the operator ran the bot with `LIVE_TRADE_LEDGER_PATH=/var/lib/bot/live_trades.json`, the tool needs the same path.

**Command (operator must fill in real values, not placeholders):**

```bash
venv/bin/python mark_settlement_resolved.py \
  --ledger /path/to/live_trades.json \
  --create-unknown-from-external-order 'BTC-15MIN-$11-1779093783343' \
  --confirm-external-order \
  --external-size 11.00 \
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

Note: the validation will **pass** because `0.63 * 17.460316 ≈ 11.00000` and `--external-size 11.00` differ by `≈ 0.0000001`, well within the dual tolerance.

**Live-trading pause expectation:** creating a `SETTLEMENT_UNKNOWN` record activates the live-trading pause gate ([bot.py:2289](bot.py:2289)). Live trading remains paused until the second step (`--order-id ... --payout ...`) resolves the unknown with a verified payout. This is intentional — Phase 0 does not "unblock" live trading by itself; it only prepares the ledger for a clean second-step resolution.

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

#### 0.2 — Patch order-status report to populate avg_px (single deterministic path)

File: `patch_market_orders.py` (or a new patch module).

**Exact adapter hook point (no more "patch order-status report normalization" ambiguity):**

The hook target is `nautilus_trader.adapters.polymarket.execution.PolymarketExecutionClient.generate_order_status_report` (the method that reads a `py_clob_client` order response and converts it to a Nautilus `OrderStatusReport`). The implementer must:

1. Confirm against the installed Nautilus version that this is the method that produces the missing-`avg_px` report. If the actual method name differs (e.g., `parse_to_order_status_report`, `_normalize_order_response`), the plan must be amended with the verified name before implementation.
2. The monkey patch wraps this method: it calls the original, then if `report.avg_px is None and report.status == FILLED`, it runs the `get_trades` lookup, computes VWAP, and rewrites the report's `avg_px` field before returning.
3. Inside the patch the implementer has access to `self._cache` (the strategy's order cache) for the `venue_order_id → client_order_id` mapping. This is the prerequisite the `cache.client_order_id(venue_order_id)` call in 0.3 depends on.

**`order_submit_ts` source (explicit, no inferred fallback):**

The `get_trades` time window in 0.2's step 1 uses `order_submit_ts - 5s` as the lower bound. The source is **the `ts_event` recorded by Nautilus when `submit_order` was originally called** — accessible via `order.ts_init` on the order object retrieved from `self._cache.order(client_order_id)`. This is a real, recorded timestamp, not an estimate.

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
   - **`LIMIT_IOC` (Phase 3, future):** match where `t.taker_order_id == venue_order_id`. IOC limit orders that fill immediately are also takers — they don't rest, so they cannot be the maker side of a match.
   - **If we ever add `LIMIT_GTC` or resting orders (out of scope):** match where `t.maker_order_id == venue_order_id`. Not in scope today; document only.
   - **Fail closed:** if no trade matches the expected predicate, dispatch `{"status": "failed", "reason": "no_matching_trade"}` — do NOT fall back to the other predicate. The role is determined by order type at submission and is unambiguous.
3. Compute size-weighted VWAP across matched trades: `vwap = sum(t.price * t.size) / sum(t.size)`.
4. Inject the VWAP as `avg_px` on the order-status report before Nautilus generates the inferred fill.

**Fail-closed when match unavailable (one explicit behavior, not "or"):**

If `get_trades` returns nothing matching the venue order id within the time window, the adapter takes exactly this path:

1. Do **not** infer, do not use the order's submitted `price` (undefined for market orders), do not fabricate.
2. Dispatch a **structured failure payload** through the actual-fill callback (defined in 0.3a). The payload **must include the following fields at minimum** so the durable SETTLEMENT_UNKNOWN handler has enough context for manual reconciliation:
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
3. The strategy's handler routes any payload with `status == "failed"` to **both**: (a) `_block_live_settlement_ledger(reason)` for the process-local pause, AND (b) `_create_durable_settlement_unknown_from_actual_fill(...)` for a durable SETTLEMENT_UNKNOWN entry that survives restart. `_record_live_order_fill` is **not** called — the only ledger write is the SETTLEMENT_UNKNOWN record (see 0.3 strategy handler below).
4. Operator manually reconciles via `mark_settlement_resolved.py --create-unknown-from-external-order` once they verify the trade externally on Polymarket.

This is one path, not two. No magic-value sentinels (the previous draft used `vwap=Decimal("0")` as a failure signal — replaced here with an explicit `status` field). Satisfies the `AGENTS.md` no-silent-fallback rule.

**Acceptance:** a market BUY that fills at 0.63 must produce a Nautilus fill event with `last_px=0.63`, derived from the matched-trade VWAP, OR the adapter dispatches a `status=failed` callback and the ledger blocks. The bot never writes a ledger entry with inferred or fabricated price data.

#### 0.3 — Normalize token-dust in Polymarket order-status report (with explicit actual-units side-channel)

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
        _dispatch_actual_fill(client_oid_or_venue_id, {
            "status": "failed",
            "reason": "real_overfill_rejected",
            "order_qty": str(original_size),
            "matched_qty": str(size_matched),
            "overfill_tokens": str(overfill),
        })
        # Then let Nautilus reject the fill event normally.
```

**Critical: actual-units side-channel (resolves the prior internal contradiction)**

The reviewer-flagged gap: if the adapter clips `last_qty` so Nautilus accepts the fill, then `on_order_filled()` receives the **clipped** quantity — not the actual Polymarket filled units. The plan previously asserted "the ledger preserves 17.460316" without specifying how. That assertion is only true with an explicit side-channel. Here's the bridge (scaffolded in 0.3a, populated in 0.2 and 0.3b):

1. **`venue_order_id ↔ client_order_id` mapping (use existing Nautilus cache, no invented helpers):**

   The adapter processes a status report keyed by `venue_order_id` (a Polymarket order hash). The strategy keys its tracking by `client_order_id` (e.g., `BTC-15MIN-$5-1779...`). Nautilus' cache exposes this mapping directly:

   ```python
   # Inside the patched report-normalization path:
   client_oid = self._cache.client_order_id(venue_order_id)
   ```

   The earlier draft referenced a `_lookup_order_by_venue_id` helper — that helper does not exist in Nautilus and must not be invented. Use only the existing cache API. If `client_oid` is `None`, fail closed: dispatch `status=failed, reason=unmapped_venue_order_id` and the strategy blocks. No silent inference, no synthesized lookup paths.

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
       for handler in list(_polymarket_actual_fill_handlers):
           try:
               handler(str(client_order_id), dict(payload))
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

   - **Failure path:** if `get_trades` finds no match (or `client_oid` cannot be resolved), the adapter calls `_dispatch_actual_fill(client_oid_or_venue_id, {"status": "failed", "reason": "no_matching_trade", ...})` and does NOT proceed to generate a fill event.

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
           # Failed reconciliation. process-local block + durable unknown so
           # the pause persists across restart.
           self._create_durable_settlement_unknown_from_actual_fill(
               client_order_id=client_order_id,
               payload=payload,
               reason=payload.get("reason", "actual_fill_failed"),
           )
           self._block_live_settlement_ledger(
               f"actual-fill callback failed for {client_order_id}: "
               f"{payload.get('reason')}; SETTLEMENT_UNKNOWN created"
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

`_block_live_settlement_ledger` is **process-local state** ([bot.py:529](bot.py:529)) — a restart clears it. Without a durable SETTLEMENT_UNKNOWN record, a bot restart after a failed reconciliation would silently resume live trading with phantom exposure. By creating a SETTLEMENT_UNKNOWN entry, the existing live-trading pause gate ([bot.py:784](bot.py:784)) keeps the bot paused after restart until the operator explicitly resolves it.

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
    "size": <SUBMITTED size from order, NOT a fabricated estimate>,

    # Diagnostic — preserve the raw failure payload verbatim for forensics
    "unknown_reason": payload.get("reason", "unknown"),
    "raw_callback_payload": payload,    # the entire dict, as-is
    "created_at": <UTC ISO timestamp>,
}
```

Rules:
- **Never** synthesize `payout` or `pnl` from inferred values. Always literal string `"UNKNOWN"`.
- **Never** fabricate `size` from price × qty estimates. Use the original submitted size (which the bot logged when the order went out) or `null` if even that is unavailable.
- **Always** include `raw_callback_payload` verbatim. The operator's manual reconciliation will join on this.

**Reconciliation key (CRITICAL — fixes earlier unreconcilable-null gap):**

`mark_settlement_resolved.py` currently selects records only by `--order-id`. A null `order_id` would make the pause impossible to clear without manual JSON editing. The plan must therefore guarantee a non-null, stable `order_id` for every durable unknown:

```python
if client_order_id_resolved is not None:
    order_id = client_order_id_resolved          # normal case: "BTC-15MIN-$5-1779..."
elif venue_order_id is not None:
    order_id = f"venue:{venue_order_id}"          # synthetic but stable: "venue:0x0638..."
else:
    # Both identifiers missing. Fail closed — do NOT generate a uuid fallback.
    # A uuid would be unreconcilable externally (operator has no way to map it
    # back to a Polymarket order). Instead, raise hard so the bot fail-stops and
    # the operator must investigate the raw payload before restarting.
    raise SettlementLedgerError(
        f"actual-fill callback has neither client_order_id nor venue_order_id; "
        f"raw payload: {payload!r}. Cannot create a reconcilable durable record. "
        f"Bot will fail-stop. Operator must inspect logs and reconcile manually "
        f"before restart."
    )
```

The synthetic `venue:<hash>` form is reconcilable via the existing `--order-id "venue:0x..."` flow. **The operator must explicitly approve this synthetic-id format before implementation begins** — it is the only deviation from "use only real identifiers" in the durable schema, and although `venue:<hash>` is fully traceable to a Polymarket transaction (not synthesized noise), it is still a constructed string and falls under the no-fallback rule's approval requirement.

No admin-tool changes required for this Phase 0 work. A future Phase can optionally add `--venue-order-id` as a convenience alias, but it is not on the critical path. The earlier draft included a `unknown:{uuid4()}` last-resort path — that has been removed because uuids are not externally reconcilable.

**Conflict resolution with `--create-unknown-from-external-order`:**

If a durable SETTLEMENT_UNKNOWN already exists (created at runtime by the bot), the operator does NOT run `--create-unknown-from-external-order` again — that command refuses to overwrite an existing record. The operator instead runs `--order-id <existing-order-id> --payout <verified>` directly, since the bot already produced the unknown record. The `--create-unknown-from-external-order` command is reserved for the case where the bot has zero record of the trade (e.g., a fill that bypassed the callback entirely).

The earlier draft's "placeholder values" phrasing is replaced by this strict schema: real-or-`null`-or-literal-`"UNKNOWN"`, never a synthesized lookalike — except `order_id`, which uses a synthetic `venue:<hash>` form when `client_order_id` is unresolvable, so the existing admin tool can still resolve it.

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
               "filled_qty": "17.460316",
               "vwap": "0.63",
               "venue_order_id": "0x0638...",
               "condition_id": "0xd55ee02c...",
               "token_id": "13493...",
               "raw_status_report": {...},
               "received_at": "2026-05-18T07:43:05+00:00"
           }
       }
   }
   ```

   The strategy handler MUST write this durable entry BEFORE the in-memory stash. If the disk write fails, treat the entire callback as failed (route to the `status=failed` durable-unknown path).

   When `on_order_filled` later arrives and `_record_live_order_fill` consumes the actual values, it also removes the matching entry from `pending_actual_fills` and persists the cleanup in the same atomic ledger write.

   On startup, the bot scans `pending_actual_fills` for any entries that have aged beyond a short threshold (e.g., 60 seconds — long enough for normal `on_order_filled` delivery, short enough that a true crash is detectable). For each aged entry, it creates a durable SETTLEMENT_UNKNOWN record with reason `pending_actual_fill_orphaned_at_restart` and removes it from `pending_actual_fills`. The live-pause gate then keeps the bot blocked until the operator resolves.

   This closes the memory-only gap. Either the fill flows through normally and the pending entry is removed cleanly, OR the bot restarts mid-flow and the pending entry is converted to a durable unknown for manual reconciliation. No silent loss.

   **Ledger schema migration (REQUIRED — current writer doesn't preserve this):**

   The current ledger writer at `_save_live_trade_ledger` ([bot.py:624](bot.py:624)) only serializes `open`, `settled`, `seen_auto_redeem_events`, and `pending_auto_redeem_events`. Adding `pending_actual_fills` as a new top-level section requires updating **every** ledger-touching code path:

   1. **`_save_live_trade_ledger`**: serialize `self._pending_actual_fills` under the new key.
   2. **`_load_live_trade_ledger`**: read the new key with default `{}` if missing (backwards-compatible for existing ledgers from before this change).
   3. **Snapshot/rollback in `_handle_auto_redeem_event` and `_record_live_order_fill`**: when computing a candidate ledger state for transactional save, include `pending_actual_fills` in the snapshot AND in the rollback restore path. Otherwise a save failure could leave inconsistent in-memory state.
   4. **`mark_settlement_resolved.py`**: when reading the ledger to find unresolved records, treat `pending_actual_fills` entries as another class of unresolved state. Add a `--list-pending-actual-fills` flag (or document that the operator inspects the JSON directly).
   5. **Atomic JSON write**: the same temp-file + os.replace pattern already used must include the new key in the JSON payload.

   Acceptance criteria for this schema migration:
   - [ ] An existing `live_trades.json` (without `pending_actual_fills`) loads cleanly.
   - [ ] A round-trip save/load preserves `pending_actual_fills` byte-identical (modulo dict key ordering).
   - [ ] Snapshot/rollback test: simulate a save failure mid-transaction; assert `pending_actual_fills` state is restored to pre-mutation values.
   - [ ] The admin tool can identify orders that have `pending_actual_fills` entries.

7. **Lifecycle: unregister handler on `on_stop`.**

   Mirror the existing `auto_redeem` pattern: register in `on_start`, unregister in `on_stop`. Without this, multiple test runs or restarts in the same process can stack callbacks, causing duplicate dispatches.

   ```python
   def on_stop(self):
       # ... existing cleanup ...
       try:
           from patch_market_orders import unregister_actual_fill_handler
           unregister_actual_fill_handler(self._actual_fill_handler)
       except Exception:
           pass
   ```

**Ledger preservation:** the bot's durable ledger records the **actual Polymarket filled units** (`17.460316` in the production case), guaranteed by the side-channel above. The clipping is local to the adapter/report path so Nautilus' downstream overfill arithmetic is satisfied. Without the side-channel, the ledger would silently record the clipped value — that contradiction is now resolved by explicit data flow.

#### 0.4 — Defensive hard guard in `_record_live_order_fill`

File: `bot.py:2789` (`_record_live_order_fill`).

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

    # Durable unknown so the pause survives restart. Pull whatever identifiers
    # are available from the order metadata cache. Uses synthetic order_id form
    # if client_oid is unresolvable (see 0.3 reconciliation-key rules).
    venue_oid = None
    venue_lookup_error = None
    try:
        order = self.cache.order(ClientOrderId(order_id)) if order_id else None
        if order is not None and order.venue_order_id is not None:
            venue_oid = str(order.venue_order_id)
    except Exception as e:
        # Record the lookup failure in the durable payload; do NOT swallow silently.
        venue_lookup_error = f"{type(e).__name__}: {e}"

    payload = {
        "status": "failed",
        "reason": "non_positive_fill_price_from_nautilus",
        "fill_price": str(fill_price),
        "fill_qty": str(fill_qty),
        "venue_order_id": venue_oid,
        "venue_lookup_error": venue_lookup_error,   # null if lookup succeeded
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        self._create_durable_settlement_unknown_from_actual_fill(
            client_order_id=order_id,
            payload=payload,
            reason=payload["reason"],
        )
    except Exception as e:
        # If even the durable write fails, the ledger cannot be trusted. Fail-stop
        # the bot — do NOT continue with only process-local state, because that
        # state clears on restart and the operator could unknowingly resume live.
        logger.critical(
            f"Could not create durable SETTLEMENT_UNKNOWN for {order_id}: {e}. "
            "Bot fail-stopping. Operator must inspect the ledger and reconcile "
            "the fill manually BEFORE any restart."
        )
        raise SettlementLedgerError(
            f"durable SETTLEMENT_UNKNOWN write failed for {order_id}: {e}"
        ) from e
    return False
```

This resolves the previous-draft contradiction. The earlier text said "stay process-blocked AND propagate" but also "Process is blocked; manual intervention required before restart" — which silently relied on the operator noticing the LOG before restarting. The corrected behavior is:

- **Durable write succeeds:** process-local block + durable SETTLEMENT_UNKNOWN. Restart preserves the pause via the durable record. Operator resolves via admin tool.
- **Durable write fails:** **fail-stop the process** via `raise SettlementLedgerError`. The bot exits abnormally. Operator MUST inspect ledger and reconcile before any restart. There is no scenario where a restart silently clears the pause.

This is belt-and-suspenders: even if 0.2 and 0.3 fail, a bad fill cannot corrupt the ledger, AND a restart cannot silently clear the resulting pause.

#### 0.4 (the zero-price guard) — scope and limits

The zero-price guard in `_record_live_order_fill` rejects any fill that reaches the strategy's recorder with `fill_price <= 0`. This is necessary but **not sufficient**:

- It catches fills that pass through Nautilus and reach the strategy with bad data.
- It does **NOT** catch the case in the observed production log, where Nautilus rejected the fill at the overfill-check stage and never invoked `on_order_filled` at all — the fill simply vanished.

The complete protection requires the adapter-side callback (0.3a + 0.2 + 0.3b). The zero-price guard is the last line of defense; the adapter callback is the primary one. Ship the guard first because it's a 10-line commit and protects the strategy thread regardless of what the adapter does, then ship the adapter work.

#### 0.5 — Regression tests

Add to `tests/test_simulation_mode_safety.py` (or a new `tests/test_polymarket_fill_normalization.py`):

1. **Test: dust overfill is accepted, ledger preserves actual filled units.** Simulate an order for 17.460300 tokens and a report with `size_matched=17.460316`. Mock `client.get_trades(...)` to return one matching trade `[{taker_order_id: <venue_id>, price: "0.63", size: "17.460316"}]`. Assert the actual-fill callback fires with `status="ok"`, `filled_qty=Decimal("17.460316")`, `vwap=Decimal("0.63")`. Assert `_record_live_order_fill` is called with `fill_qty=17.460316` (the actual Polymarket filled units, preserved via the side-channel).
2. **Test: real overfill is rejected AND dispatches durable failure.** Submit for 17.460300, simulate fill of 18.0, assert the fill is rejected by Nautilus' normal path AND the actual-fill callback fires with `status="failed"`, `reason="real_overfill_rejected"`. Assert a durable SETTLEMENT_UNKNOWN entry is created for the venue order id so the bot stays paused across restart.
3. **Test: missing avg_px with `get_trades` match succeeds.** Simulate a filled report with `avg_px=None` and mock `client.get_trades(...)` returning a matched trade. Assert the actual-fill callback fires with `status="ok"` and the correct VWAP. Assert the inferred fill carries the matched price.
4. **Test: missing avg_px with no `get_trades` match blocks ledger.** Simulate the same condition but with `client.get_trades(...)` returning no match. Assert the actual-fill callback fires with `status="failed"`, `reason="no_matching_trade"`. Assert `_settlement_ledger_blocked_reason` is set and no fill is committed.
5. **Test: zero fill_price guard.** Call `_record_live_order_fill(order_id, fill_price=Decimal("0"), fill_qty=Decimal("17"))` directly, assert returns False, asserts `_settlement_ledger_blocked_reason` is set. This test exercises the last-line-of-defense guard independently of the adapter callback.

All tests mock `client.get_trades(TradeParams(...))`, **NOT** `get_trade_history` (that method does not exist).

### Exit criteria

- [ ] Lost trade reconstructed in `live_trades.json` via `--create-unknown-from-external-order`.
- [ ] **Lost trade fully resolved.** Operator has run `--order-id BTC-15MIN-\$11-... --payout <verified> --reason ...` with the externally verified payout. The ledger entry now has `settlement_source: "manual_reconciliation"` and `needs_reconciliation: false`.
- [ ] **No remaining `SETTLEMENT_UNKNOWN` records with `needs_reconciliation: true` for this order.** Reconstruction alone is not sufficient — the live-trading pause gate keys off unresolved unknowns, so a reconstructed-but-unresolved entry would keep the bot paused indefinitely.
- [ ] avg_px present in fill events for new live market BUYs via the deterministic `get_trades` path (verify with one $5 live smoke trade after the lost trade is fully resolved).
- [ ] Token-dust overfill within tolerance no longer rejected; ledger preserves actual filled units via the side-channel.
- [ ] Real overfill (outside tolerance) dispatches `status=failed, reason=real_overfill_rejected` and creates a durable SETTLEMENT_UNKNOWN entry that survives restart.
- [ ] `_record_live_order_fill` refuses non-positive `fill_price`.
- [ ] All five regression tests pass.
- [ ] One deliberately tiny `$5` live smoke trade has been placed and observed to flow cleanly through: order submitted → actual-fill callback fires with `status="ok"` and matching VWAP → ledger records actual filled units → `auto_redeem` resolves → final ledger entry shows correct payout and P&L.

### Effort

2 days. The Polymarket adapter patch (0.2, 0.3) is the longest piece because it requires reading Nautilus's internal report path and crafting a minimal monkey patch.

---

## Phase 1 — Environment Variable Audit & Documentation

**Status:** High. Several env vars listed in README / current env docs don't actually do anything. Operator may be tuning values that have zero effect. (`.env.example` does not yet exist — Phase 1.3 creates it. Until then, the env reference surface is the README and inline code comments only.)

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
| `SPIKE_THRESHOLD` | spike detector threshold | **Hardcoded in `bot.py:376` to 0.05.** Env value ignored. |
| `DIVERGENCE_THRESHOLD` | divergence detector threshold | **Hardcoded in `bot.py` to 0.05.** Env value ignored. |

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

**Note:** `.env.example` does **not** currently exist in the repo. This phase must **create** it (not "clean up"). It should include:
- All env vars actually wired (per the tables above)
- Comments next to each var marking per-decision vs startup
- Recommended default values where appropriate
- Required-vs-optional marking

### Exit criteria

- [ ] `.env.example` exists in the repo root with all wired env vars documented and labeled.
- [ ] README has a "Environment Variables" section with per-decision-read vs startup-read clearly marked.
- [ ] README explicitly states that editing `.env` does not propagate at runtime — restart required.
- [ ] `SPIKE_THRESHOLD` / `DIVERGENCE_THRESHOLD` wired or struck from docs.
- [ ] `STOP_LOSS_PCT` / `TAKE_PROFIT_PCT` struck from docs with a note explaining why they're not implemented.

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

### Smaller starting config (if operator wants to start more conservatively)

```env
MARKET_BUY_USD=5.00
MAX_POSITION_SIZE=5.00
MAX_TOTAL_EXPOSURE=20.00
MAX_POSITIONS=4
MAX_LOSS_PER_DAY=10.00
MAX_DRAWDOWN_PCT=0.15
```

Same shape, ~1/11 the exposure. Use this for the first live smoke trade after Phase 0 completes, then scale to the $385 cap after the smoke trade settles cleanly.

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

If the operator wants the bot to refuse trades when free collateral is missing, stale, or below `MARKET_BUY_USD`, this would be a new sub-phase (call it Phase 2.5). Requirements:

- Hook the Nautilus `AccountState` update path to keep `self._latest_free_collateral` and `self._latest_account_state_ts` current.
- In `_make_trading_decision` (or `_place_real_order`), before order construction:
  - Fail closed if `self._latest_account_state_ts` is older than e.g. 30 seconds (stale state).
  - Fail closed if `self._latest_free_collateral < MARKET_BUY_USD + safety_buffer`.
- Log the decision to `decisions.jsonl` with `rejected_at_gate="balance_guard"`.
- No fallback. If the account state can't be obtained, refuse to trade, do not estimate.

This is intentionally not on the Phase 0/1/2 critical path. Document as a future enhancement; ship only if operator decides the existing rejection-as-feedback approach is insufficient.

### Optional non-issue: rollover quote warnings

When the bot rolls from one 15-minute market to the next, the previous instrument may emit a few one-sided `QuoteTick` events that the strategy logs as `Dropping QuoteTick`. These are harmless: the strategy ignores non-current instruments. The Polymarket adapter does not currently support unsubscribe for closed markets, so this log noise will continue briefly after each rollover.

**No urgent code fix.** Optional future cleanup only (would require an adapter-level unsubscribe or an instrument-id filter at the message-receive boundary).

### Effort

5 minutes (operator sets env file + runs the balance check).

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

### Computing the per-trade size at decision time

**For `SIZING_MODE=fixed`:** same as today.

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

The percent-mode computation requires the bot to hook the Nautilus `AccountState` update path (same hook that Phase 2.5-balance-guard would have used). This is now mandatory infrastructure, not optional future work.

### Hard constraints that apply to BOTH modes

After computing `per_trade_usd`, ALL of the following must pass or the trade is rejected:

1. `per_trade_usd >= MARKET_MINIMUM_USD` (Polymarket: $1 for market orders, or $5 × `limit_price` worth of tokens for limit orders — see Phase 3 5-token-minimum logic).
2. `per_trade_usd <= MAX_POSITION_SIZE` — operator-enforced ceiling. Computed amount exceeding this is **rejected**, NOT clamped. Clamping would silently shrink the operator's intended size.
3. `current_exposure + per_trade_usd <= MAX_TOTAL_EXPOSURE` — pre-trade exposure check using risk engine.
4. `current_position_count < MAX_POSITIONS` — pre-trade count check.

The `per_trade_usd > MAX_POSITION_SIZE` rejection is important. If the operator sets `SIZING_MODE=percent`, `PCT_OF_FREE_COLLATERAL_PER_TRADE=0.10`, and the account grows to $1000 free collateral, the computed per-trade size is $100. If `MAX_POSITION_SIZE=55`, the trade is rejected (with a clear log) rather than silently capped at $55. The operator must consciously raise `MAX_POSITION_SIZE` or lower the percent to scale up.

### `decisions.jsonl` integration

The decision record (per Phase 4) gains two fields when `SIZING_MODE=percent` is active:
- `sizing_mode`: `"fixed"` or `"percent"`
- `free_collateral_at_decision`: the snapshotted balance used for the computation (null for fixed mode)
- `account_state_age_seconds`: age of the balance snapshot (null for fixed mode)

These allow Phase 4 calibration to detect whether bigger-vs-smaller positions perform differently — useful for tuning the percent value over time.

### Tests

- [ ] `SIZING_MODE=fixed` produces the same per-trade size as today (regression).
- [ ] `SIZING_MODE=percent` with $1000 free collateral and `PCT=0.05` produces `per_trade_usd=$50.00`.
- [ ] `SIZING_MODE=percent` with stale account state (>30s old) rejects the trade and logs to `decisions.jsonl` with `rejected_at_gate="stale_balance"`.
- [ ] `SIZING_MODE=percent` with no account state yet rejects with `rejected_at_gate="no_balance"`.
- [ ] Computed size exceeding `MAX_POSITION_SIZE` rejects (does not clamp) with `rejected_at_gate="size_exceeds_max_position_size"`.
- [ ] Missing `SIZING_MODE` in live mode raises at startup.
- [ ] Invalid `SIZING_MODE` value raises at startup.
- [ ] `SIZING_MODE=percent` with missing or out-of-range `PCT_OF_FREE_COLLATERAL_PER_TRADE` raises at startup.

### Effort

1.5 days. Most of the work is the `AccountState` hook (which Phase 2.5-balance-guard would have needed anyway) and the rejection-vs-clamp logic for `MAX_POSITION_SIZE`.

### Exit criteria

- [ ] Both modes work via env var, no implicit default in live mode.
- [ ] Operator can switch modes by env edit + restart (same constraint as other risk-engine env vars).
- [ ] All four hard constraints (minimum, max position, max exposure, max positions) apply to both modes.
- [ ] All listed tests pass.
- [ ] README documents both modes with example env values and the rejection-not-clamp semantics for `MAX_POSITION_SIZE`.

---

## Phase 3 — Configurable Order Type (`MARKET_IOC` vs `LIMIT_IOC`)

**Status:** Strategic. Major change. Depends on Phase 0 (fill reconciliation must work first), Phase 4 (calibration validation), and Phase 5 (depth-aware fill estimator).

### Motivation

Current behavior: bot submits market IOC orders. A $1 (or $5) budget sweeps the book until exhausted. Average fill price can be substantially worse than the top-of-book ask the EV gate evaluated.

Desired behavior: operator selects between:
- **`MARKET_IOC`** (current behavior): immediate fill at whatever price is available, up to budget.
- **`LIMIT_IOC`**: immediate fill at the EV-accepted price or cancel. No resting state.

Both are explicit operator choices. This is not a silent fallback — the operator must set `ORDER_TYPE` explicitly (no default).

### 3.0a — Decide partial-fill vs all-or-nothing semantics BEFORE the wire-format verification

This is a strategy decision, not a Nautilus question. The plan must pick one explicitly before Phase 3 ships:

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

**Plan default: FAK.** Rationale:
- Existing partial-fill machinery in `_record_live_order_fill` is already built and tested.
- Polymarket book depth at $5 trade sizes is usually thin enough that requiring all-or-nothing would skip a substantial fraction of otherwise profitable trades.
- The depth estimator in Phase 5 already exposes how many tokens would fill at the limit cap; if the operator wants FOK behavior they can implement it as a strategy-side check (`if tokens_filled < target_token_qty: return False`) without changing the order type.

If the operator wants FOK behavior explicitly, that becomes a Phase 3 sub-flag (`LIMIT_IOC_REQUIRE_FULL_FILL=true`, no default), gating the strategy-side check rather than changing the Polymarket order type. Keep one wire format; layer policy on top.

### 3.0b — HARD PREREQUISITE: verify Nautilus wire format for `OrderType.LIMIT + TimeInForce.IOC`

Before any of the Phase 3 implementation begins, verify experimentally that `self.order_factory.limit(... time_in_force=TimeInForce.IOC ...)` actually produces the wire format we expect when it reaches `py_clob_client`. Three things could go wrong, all of which require **deliberate** action — not fallback:

1. **Nautilus may map `LIMIT + IOC` to Polymarket's `FAK` order type.** This matches the Plan-default FAK strategy choice in 3.0a. Confirm and proceed.
2. **Nautilus may map `LIMIT + IOC` to `FOK`** (fill-or-kill, all-or-nothing). This **contradicts** the Plan-default FAK choice. If Nautilus maps to FOK, the strategy must either (a) change the default to FOK and revisit 3.0a, or (b) submit a different `TimeInForce` value that maps to FAK. Do not silently accept the mismatch.
3. **Nautilus may reject the combination outright** and require a different `TimeInForce` value (`GTD`, `FAK` explicit, etc.). If so, choose the Polymarket order type deliberately by reading `py_clob_client` source — do NOT pick whichever value happens to make the rejection go away.

A locally-installed Nautilus reviewer sanity-check during plan review showed `TimeInForce.IOC → FAK`, so the Plan-default is consistent with the current Nautilus mapping. Verification step remains required because the mapping is internal to Nautilus and can change between versions.

**Verification method (NOT simulation):**

Write a unit test that constructs the limit order via `order_factory.limit`, intercepts the patched execution path, and asserts the exact `OrderArgs` or `MarketOrderArgs` shape that `py_clob_client.create_order` is invoked with. Specifically assert:
- `order_args.order_type` is one of: `"FAK"`, `"FOK"`, `"GTC"` (whichever Nautilus maps to).
- `order_args.price`, `order_args.size`, `order_args.side`, `order_args.token_id` are populated correctly.
- The call is `create_order(...)`, NOT `create_market_order(...)`.

Document the actual mapping in the Phase 3 implementation PR. If the mapping doesn't match the intent ("fill at limit or cancel"), file an issue and **block Phase 3** until resolved. Do not ship a workaround.

**Why this is a hard prerequisite:** the plan currently assumes `LIMIT + IOC = "fill at price or better, otherwise cancel"`. If Nautilus' mapping produces something different (e.g., FOK which requires the entire order to fill atomically), the price-discipline strategy doesn't work as designed and the bot will silently behave differently from operator expectations.

### 3.1 — Hard practical constraint: 5-token minimum

Polymarket limit orders require ≥5 tokens. At various target prices with various budget sizes:

| Limit price | $1 budget → tokens | $5 budget → tokens | $11 budget → tokens |
|---|---|---|---|
| $0.20 | 5.0 ✅ | 25.0 ✅ | 55.0 ✅ |
| $0.30 | 3.3 ❌ | 16.7 ✅ | 36.7 ✅ |
| $0.50 | 2.0 ❌ | 10.0 ✅ | 22.0 ✅ |
| $0.62 | 1.6 ❌ | 8.1 ✅ | 17.7 ✅ |
| $0.80 | 1.25 ❌ | 6.25 ✅ | 13.75 ✅ |

**Implication:** `LIMIT_IOC` is only usable when `MARKET_BUY_USD / limit_price ≥ 5`. At Phase 2's recommended $5 budget, this works for prices ≤ $1.00 (i.e., all valid prices). At $1 budget, only ≤ $0.20 trades qualify.

### 3.2 — `quote_quantity` correctness per order type

| Order type | `quote_quantity` | `quantity` semantics |
|---|---|---|
| Market BUY (current patched path) | `True` | USD amount to spend |
| Limit BUY | `False` | Token count (per `nautilus_polymarket_integration.py:427`) |
| Market SELL | `False` | Token count |
| Limit SELL | `False` | Token count |

The current `patch_market_orders.py` only handles the market BUY USD-amount path. Adding limit-order support means:
- Compute `token_qty = MARKET_BUY_USD / limit_price`
- Reject if `token_qty < 5` (Polymarket minimum)
- Submit with `quote_quantity=False` and the **decimal token quantity rounded down to `instrument.size_precision`** (e.g., 6 decimal places for Polymarket)
- Submit `price` as a separate field

### 3.3 — Implementation

**Env var (required in live mode, no default):**

```env
# Required in live mode. No implicit default. Bot refuses to start in live mode without this.
# Allowed values: market_ioc | limit_ioc
ORDER_TYPE=
```

**Validation in `run_integrated_bot` at startup AND at every live trade decision:**

```python
def _validate_order_type_for_live() -> str:
    order_type = os.getenv("ORDER_TYPE")
    if not order_type:
        raise RuntimeError("ORDER_TYPE must be set to 'market_ioc' or 'limit_ioc' for live trading")
    if order_type not in {"market_ioc", "limit_ioc"}:
        raise RuntimeError(f"ORDER_TYPE must be 'market_ioc' or 'limit_ioc', got {order_type!r}")
    return order_type
```

- **Startup check:** `run_integrated_bot` calls this when `simulation=False`.
- **Runtime check:** `_place_real_order` ALSO calls this on every live trade. This is required because the bot supports Redis-based mode switching (operator can flip sim→live at runtime). If `ORDER_TYPE` was missing at startup in simulation mode but the Redis flag flips to live, the runtime check must refuse the trade.

**No default in any mode.** The previous draft suggested simulation/test mode could default to `market_ioc` — that is a fallback and is now removed. If no live order will be placed, `ORDER_TYPE` is simply unused. Any test that exercises order construction must set `ORDER_TYPE` explicitly.

**Branch in `_place_real_order`:**

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
    # limit_price was computed once during _make_trading_decision and passed
    # through to this method as a parameter. Do NOT recompute it here — that
    # would risk diverging from the value the EV gate accepted.
    if limit_price is None:
        return False  # _make_trading_decision already logged the no-edge reason

    # Token quantity at the WORST case (limit price). If fill price improves,
    # we spend less than max_usd_amount. The conservative sizing means we
    # never spend more than the budget; we may spend less.
    raw_token_qty = Decimal(str(max_usd_amount)) / limit_price
    size_precision = instrument.size_precision
    token_qty = raw_token_qty.quantize(
        Decimal(10) ** -size_precision,
        rounding=ROUND_DOWN,
    )
    if token_qty < Decimal("5"):
        logger.warning(
            f"LIMIT_IOC requires ≥5 tokens; "
            f"budget=${max_usd_amount} / price={limit_price} = {token_qty} tokens "
            "after rounding to instrument size precision. "
            "Increase MARKET_BUY_USD or skip this trade."
        )
        return False
    # Use Nautilus' from_str constructors, matching the existing convention in
    # the codebase. Direct Price(value, precision=...) / Quantity(value, precision=...)
    # constructors have not been verified to handle Decimal precision the same way.
    qty_str = format(token_qty, f".{size_precision}f")
    price_str = format(limit_price, f".{instrument.price_precision}f")
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

**Sizing semantics (documented):** `token_qty = budget / limit_price` is conservative. The worst-case fill cost equals the budget. If actual fills happen at prices better than the limit, the spend will be less than the budget. This is intentional: never exceed the budget, but accept under-spending. If the operator wants "spend as close to $5 as possible," that's a different sizing rule (post-fill top-up) and is out of scope.

**Token quantity is decimal, not integer.** Use `instrument.size_precision` from Nautilus' instrument metadata. Polymarket fills are decimal token quantities (e.g., `17.460316`). Any earlier wording in this document referring to "integer token count" is wrong and should be ignored — the correct phrasing is "decimal token quantity rounded down to `instrument.size_precision`."

**Limit-price ownership:** `limit_price` is computed exactly **once** during `_make_trading_decision` (after the signal-confirmation gate, before the depth-aware EV gate that uses it). It is then passed as a parameter to `_place_real_order`. This avoids the previous-draft inconsistency where Phase 5 read `limit_price` inside `_make_trading_decision` while Phase 3 computed it again inside `_place_real_order` — same expression, but two independent computations risk divergence under refactoring. One owner, one value.

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

Called once during `run_integrated_bot` when `ORDER_TYPE=limit_ioc`, and the resolved `Decimal` is stored on the strategy as `self._limit_required_edge`.

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

These are **not** the same number and they are **not** double-counted. `LIMIT_REQUIRED_EDGE` is gross edge above order-placement cap; EV buffers are applied to the actual executable entry separately. The operator should set `LIMIT_REQUIRED_EDGE ≥ EV_FEE_BUFFER + EV_SPREAD_BUFFER` so that orders that pass the limit step also have a reasonable chance of passing the EV gate, but the two checks are independent gates and the bot enforces both.

Example with `fused.confidence = 0.78`, `LIMIT_REQUIRED_EDGE = 0.05`, `EV_FEE_BUFFER = 0.005`, `EV_SPREAD_BUFFER = 0.01`:
- `limit_price = 0.78 - 0.05 = 0.73` → order placed at price ≤ 0.73
- Suppose fill VWAP comes back at 0.71 → `executable_entry = 0.71`
- `breakeven_confidence = 0.71 + 0.015 = 0.725` → check `0.78 < 0.725`? No. Trade proceeds.

Same example but VWAP comes back at 0.73 (worst-case at the cap):
- `breakeven_confidence = 0.73 + 0.015 = 0.745` → check `0.78 < 0.745`? No. Trade proceeds.

Same example but `LIMIT_REQUIRED_EDGE = 0.01` (tighter):
- `limit_price = 0.78 - 0.01 = 0.77`. Suppose fill VWAP at 0.77.
- `breakeven_confidence = 0.77 + 0.015 = 0.785` → check `0.78 < 0.785`? Yes. **EV gate rejects.**

So setting `LIMIT_REQUIRED_EDGE < EV_FEE_BUFFER + EV_SPREAD_BUFFER` produces orders that pass the limit step but get rejected by the EV gate every time. Operator should configure with `LIMIT_REQUIRED_EDGE ≥ EV_FEE_BUFFER + EV_SPREAD_BUFFER` for the two to be self-consistent. The plan does not enforce this at startup (the operator may intentionally want the asymmetry for diagnostic purposes), but the README should document the relationship.

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

1. **Unit test with a mocked `py_clob_client`.** Build a test that constructs the limit order through `order_factory.limit(...)`, intercepts the patched execution path, and asserts that the mocked client's `create_order` (not `create_market_order`) is called with the expected `OrderArgs(token_id=..., price=Decimal("0.50"), size=Decimal("10"), side="BUY")` shape.
2. **One deliberately tiny live smoke trade** after Phase 0 is closed: place one $5 limit BUY at a moderate price, observe the actual Polymarket order record matches what we submitted (token quantity, limit price, IOC TIF).

Do not use simulation mode for this verification. Decision-only simulation cannot exercise the wire format.

### 3.5 — Tests

- Test: `ORDER_TYPE=market_ioc` builds market order with `quote_quantity=True`.
- Test: `ORDER_TYPE=limit_ioc` with sufficient budget builds limit order with `quote_quantity=False` and correct `price`.
- Test: `ORDER_TYPE=limit_ioc` with insufficient budget (token_qty < 5) returns False without submitting.
- Test: missing `ORDER_TYPE` env var raises `RuntimeError` at startup.
- Test: invalid `ORDER_TYPE` value raises `RuntimeError`.

### Exit criteria

- [ ] `ORDER_TYPE` required env var validated at startup.
- [ ] Both code paths exist and are exercised by tests.
- [ ] 5-token minimum guard prevents impossible limit orders.
- [ ] One $5 live smoke trade with each order type confirms the wire format is right.
- [ ] README documents both modes and their trade-offs.

### Effort

3 days. Most of the cost is verifying the limit-order CLOB submission format against Nautilus and py-clob-client.

---

## Phase 4 — Calibration Validation (gate for Phase 3's edge claim)

**Status:** Strategic. Prerequisite for trusting `LIMIT_IOC` to capture edge.

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

Reviewer-flagged: the current decision-observation flow ([bot.py:2475](bot.py:2475)) only emits log lines; it does not write a structured ledger with the join keys Path B needs. Without that ledger, joining against Polymarket historical resolutions is fragile parsing of free-text log lines.

**New sub-phase (do this before running the calibration script):**

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

**Effort:** 0.5 day inside Phase 4 (add the writer + rotation). Must be merged and running long enough to accumulate decisions before Path B can produce results.

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
    "sum_entry_price": Decimal("0"),
    "sum_pnl_usd": Decimal("0"),
    "sum_size_usd": Decimal("0"),
    "trades": 0,
})

for trade in ledger["settled"]:
    if trade.get("settlement_source") not in ("auto_redeem", "late_auto_redeem", "manual_reconciliation"):
        continue
    conf = float(trade.get("signal_confidence", 0))
    payout = Decimal(trade.get("payout", "0")) if trade.get("payout") not in ("UNKNOWN", None) else Decimal("0")
    pnl = Decimal(trade.get("pnl", "0")) if trade.get("pnl") not in ("UNKNOWN", None) else Decimal("0")
    size = Decimal(trade.get("size", "0"))
    entry = Decimal(trade.get("entry_price", "0"))
    bucket = round(conf * 10) / 10  # 0.5, 0.6, 0.7, 0.8, 0.9
    b = buckets[bucket]
    if payout > 0:
        b["outcome_wins"] += 1
    else:
        b["outcome_losses"] += 1
    b["sum_entry_price"] += entry
    b["sum_pnl_usd"] += pnl
    b["sum_size_usd"] += size
    b["trades"] += 1

print(f"{'conf':>6} {'n':>5} {'win_rate':>10} {'avg_entry':>10} {'avg_pnl_per_$':>14} {'edge_vs_entry':>14}")
for bucket in sorted(buckets):
    b = buckets[bucket]
    n = b["trades"]
    if n == 0:
        continue
    win_rate = b["outcome_wins"] / n
    avg_entry = b["sum_entry_price"] / n
    avg_pnl_per_dollar = b["sum_pnl_usd"] / b["sum_size_usd"] if b["sum_size_usd"] > 0 else Decimal("0")
    edge_vs_entry = Decimal(str(win_rate)) - avg_entry
    print(f"{bucket:>6.1f} {n:>5} {win_rate:>10.1%} {float(avg_entry):>10.4f} {float(avg_pnl_per_dollar):>+14.4f} {float(edge_vs_entry):>+14.4f}")
```

Add Brier score `mean((conf - outcome)**2)` and log-loss `-mean(outcome*log(conf) + (1-outcome)*log(1-conf))` if sample size permits.

#### 4.3 — Decision gate

A trade is +EV when **`win_rate > avg_entry_price + fee_buffer + spread_buffer`** (per dollar — fees/spread must be included; ignoring them gives apparent edge where there is none). So:

**Three gates must all pass to clear Phase 3:**

1. **Point estimate of edge is positive.** `win_rate - avg_entry_price > (fee_buffer + spread_buffer)` in at least one well-sampled bucket. Use the same buffer values as the EV gate (`EV_FEE_BUFFER + EV_SPREAD_BUFFER`, default 1.5%).

2. **Lower bound of edge confidence interval is also positive.** Compute a Wilson 95% CI for the win rate at each bucket. The CI lower bound minus `avg_entry_price + buffers` must still be `> 0`. A single positive bucket at n=50 can easily be noise; the CI guards against that. For n=50 and observed 70% win rate, Wilson 95% lower bound is ~56% — so the bucket has to be much better than break-even pointwise to clear the CI test.

3. **Edge persists out-of-sample.** Split the data into two halves (chronologically — first half vs second half). The edge must be positive (point estimate) in BOTH halves. This catches the case where the model fit a transient pattern that doesn't continue.

**Decision:**

- All three gates pass in ≥1 bucket with n ≥ 100: **proceed to Phase 3.**
- Point estimate passes but CI lower bound is negative: **collect more data.** The signal might be real but n is too small to confirm.
- Point estimate passes but out-of-sample half is flat or negative: **no shippable edge.** The apparent edge is overfit. Don't ship `LIMIT_IOC` — the strategy needs signal-processor work.
- Point estimate is at or below buffers across buckets: **no edge.** Same conclusion.
- `win_rate < avg_entry_price` across buckets: **negative edge.** Stop the strategy and investigate.

The win-rate-alone gate (e.g., "70% confidence → 65% win rate") from earlier drafts is insufficient because it ignores entry price AND ignores noise. A 70% win rate at $0.80 entry is a loser; at $0.50 entry it's a winner — but only if the sample is large enough to trust.

### Exit criteria

**Both paths required (Path A alone has selection bias and cannot answer the calibration question):**

- [ ] **`decisions.jsonl` writer is shipped and producing structured records** with all join keys (slug, condition_id, market_end_time, fused_confidence, decided_direction, rejected_at_gate, executable_entry, per-processor scores). Verified by reading several days of `decisions.jsonl` output and confirming each record has the documented fields.
- [ ] Calibration script exists and runs against **both** `live_trades.json` (Path A) **and** `decisions.jsonl` joined to Polymarket historical resolutions (Path B). Two separate analysis outputs, not one.
- [ ] **Path A:** sample size **≥100** settled live trades in at least one confidence bucket. Reports `win_rate`, `avg_entry_price`, `avg_pnl_per_dollar`, `win_rate - avg_entry_price`. (This is the same `n ≥ 100` threshold used by the three-gate rule in 4.3 — unified across the whole document.)
- [ ] **Path B:** sample size ≥200 decision observations (including rejected) in at least one confidence bucket. Reports the same metrics computed from market resolutions, plus Brier score and log-loss across the full set.
- [ ] **Decision documented with the numbers from both paths.** If Path A shows positive `win_rate - avg_entry_price` but Path B shows the same confidence buckets are uncorrelated with outcomes (i.e., the gates are filtering on noise that happens to correlate with profitable trades), Phase 3 is NOT cleared to ship — the apparent edge is in the gates, not the signal.

### Effort

4 hours of analysis once data is collected. Data collection: 0.5 day to ship the `decisions.jsonl` writer, then days-to-weeks of elapsed time for it to accumulate ≥200 decisions plus ≥100 settled trades.

---

## Phase 5 — Depth-Aware Fill Estimator

**Status:** Improvement. Useful for both `MARKET_IOC` and `LIMIT_IOC`.

### Motivation

Current EV gate uses top-of-book ask. Real market IOC fills sweep multiple book levels. Without depth-aware estimation, the gate filters on a price the trade never actually pays.

### Scope

#### 5.1 — Fill price estimator

Add to `core/strategy_brain/signal_processors/orderbook_processor.py` (or a new utility module).

**Book level units:** each level's `price` is in [0,1] (Polymarket binary token price), `size` is in **tokens**. USD capacity at a level is `price × size`.

```python
class InvalidBookLevelError(ValueError):
    """Raised when a CLOB book level has impossible values."""

def estimate_fill_price(
    levels: list[dict],
    usd_to_spend: Decimal,
    max_price: Optional[Decimal] = None,
) -> tuple[Decimal, Decimal, bool]:
    """
    Walk levels (ascending price for buy-side asks).
    Each level: {"price": <token price in (0, 1]>, "size": <token quantity, > 0>}.

    If max_price is None: walks the entire book (suitable for MARKET_IOC pre-trade analysis).
    If max_price is set:  stops at the first level where level.price > max_price
                          (suitable for LIMIT_IOC; tells you "can a limit at max_price fill this budget").

    Raises InvalidBookLevelError on any level with non-positive price/size, price > 1, or
    non-numeric values. Fail closed; do NOT silently skip bad levels — a corrupt book is
    an actionable error, not noise to be ignored.

    Return (volume_weighted_avg_price, total_tokens_filled, fully_filled).
    """
    remaining = usd_to_spend
    total_tokens = Decimal("0")
    total_cost = Decimal("0")
    for idx, level in enumerate(levels):
        # Validation: fail-closed on bad data, no silent skip
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

        if max_price is not None and price > max_price:
            break   # for LIMIT_IOC: cannot fill above the cap
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
    if remaining > 0:
        return (
            total_cost / total_tokens if total_tokens > 0 else Decimal("0"),
            total_tokens,
            False,
        )
    return total_cost / total_tokens, total_tokens, True
```

**Caller behavior:** in `_make_trading_decision`, wrap the call in `try/except InvalidBookLevelError` and on error: log the error, refuse the trade (return False), and optionally trigger a `_block_live_settlement_ledger` if corruption persists. Never fall back to a partial book or a default price.

**Usage per order type (different semantics — DO NOT use one estimator for both):**

The previous draft proposed a single `estimate_fill_price(levels, usd_to_spend, max_price=...)` for both modes. That is wrong for `LIMIT_IOC`. Market orders are budget-driven (spend up to $X, take whatever tokens that buys). Limit orders are token-quantity-driven (acquire up to N tokens at price ≤ cap, spend whatever that costs — usually less than the worst-case budget).

The two estimators have different inputs and different "fully filled" semantics:

```python
def estimate_market_ioc_fill(levels, usd_to_spend) -> tuple[vwap, tokens_filled, fully_filled]:
    """For MARKET_IOC: how much does it cost to spend $X across the book."""
    # Existing logic: walk levels, accumulate cost until usd_to_spend exhausted.
    # fully_filled = (we spent the entire usd_to_spend).

def estimate_limit_ioc_fill(levels, target_token_qty, max_price) -> tuple[vwap, tokens_filled, actual_cost, fully_filled]:
    """For LIMIT_IOC: how many tokens can we buy at price <= max_price."""
    remaining_tokens = target_token_qty
    total_cost = Decimal("0")
    total_tokens = Decimal("0")
    for level in levels:
        # ... same validation as before (InvalidBookLevelError on bad data) ...
        if price > max_price:
            break    # stop at the cap
        tokens_to_take = min(remaining_tokens, size_tokens)
        total_tokens += tokens_to_take
        total_cost += tokens_to_take * price
        remaining_tokens -= tokens_to_take
        if remaining_tokens <= 0:
            break
    fully_filled = (remaining_tokens <= 0)
    vwap = total_cost / total_tokens if total_tokens > 0 else Decimal("0")
    return vwap, total_tokens, total_cost, fully_filled
```

**Why the distinction matters (reviewer-flagged P0 scenario):**

Suppose the operator wants `LIMIT_IOC` BUY at price ≤ $0.50 with budget $5. The bot computes `target_token_qty = $5 / $0.50 = 10 tokens`. The book has `10 tokens @ $0.40`.

- The proposed unified estimator (`usd_to_spend=$5, max_price=$0.50`): walks the book at $0.40, sees only `$0.40 × 10 = $4` available, returns `fully_filled=False`. **The bot skips a perfectly fillable trade.**
- The correct token-driven estimator (`target_token_qty=10, max_price=$0.50`): walks the book at $0.40, accumulates 10 tokens, returns `fully_filled=True, vwap=$0.40, actual_cost=$4`. **The bot takes the trade and spends less than the budget.**

`MARKET_IOC` keeps the USD-budget semantics (operator wants to spend $X regardless of what tokens that buys). `LIMIT_IOC` uses token-qty semantics (operator wants up to N tokens, will spend whatever that costs ≤ budget).

#### 5.2 — Wire into EV gate — use the SELECTED token's book

**Critical:** the existing metadata only passes `yes_token_id` into the orderbook processor ([bot.py:2232](bot.py:2232)). For a NO trade the fill estimator must walk the **NO** asks, not the YES asks. Using the YES book for a NO trade evaluates the wrong executable market.

Required changes to `_fetch_market_context`:
1. Fetch both YES and NO books once per decision.
2. Store on the strategy as `self._latest_yes_book` and `self._latest_no_book`.
3. The `OrderBookImbalanceProcessor` continues to use the YES book for its imbalance metric (unchanged signal logic).
4. The EV gate selects the book matching the trade direction.

In `_make_trading_decision` — branch by order type because the estimators take different inputs:

```python
if direction == "long":
    side_levels = self._latest_yes_book["asks"]
else:
    side_levels = self._latest_no_book["asks"]

side_label = "NO" if direction == "short" else "YES"

if order_type == "market_ioc":
    estimated_avg, tokens_filled, fully_filled = estimate_market_ioc_fill(
        side_levels, POSITION_SIZE_USD
    )
    if not fully_filled:
        logger.warning(
            f"MARKET_IOC: book too thin for full ${POSITION_SIZE_USD} {side_label} sweep — "
            f"only ${tokens_filled * estimated_avg:.2f} available"
        )
        return False
    executable_entry = estimated_avg
    # Use existing $1-budget semantics; nothing else changes for market.

else:  # order_type == "limit_ioc"
    # limit_price was computed once earlier in _make_trading_decision.
    # See Phase 3.1 sizing math for the target_token_qty derivation.
    target_token_qty = Decimal(str(POSITION_SIZE_USD)) / limit_price
    estimated_avg, tokens_filled, actual_cost, fully_filled = estimate_limit_ioc_fill(
        side_levels, target_token_qty, max_price=limit_price
    )
    if not fully_filled:
        logger.warning(
            f"LIMIT_IOC: insufficient {side_label} liquidity at price ≤ {limit_price} "
            f"for {target_token_qty} target tokens — only {tokens_filled} fillable "
            f"(actual cost would be ${actual_cost:.2f})"
        )
        return False
    executable_entry = estimated_avg   # VWAP — may be strictly below limit_price
```

The LIMIT_IOC path uses `target_token_qty` and reports the actual cost (which may be less than the budget) in the log. The EV gate downstream uses `executable_entry` (VWAP), which is the same field name as before — only the upstream computation differs by order type.

#### 5.3 — Cache the book once per decision

Current state: `OrderBookImbalanceProcessor` fetches the book during signal processing (YES only). Refactor so:
- `_fetch_market_context` fetches **both YES and NO books** once and stores on the strategy.
- The imbalance processor reads YES from the cache (unchanged behavior).
- The EV gate reads whichever side matches the trade direction.

### Exit criteria

- [ ] **Two estimators** exist with unit tests, NOT one unified helper: `estimate_market_ioc_fill(levels, usd_to_spend)` (budget-driven) and `estimate_limit_ioc_fill(levels, target_token_qty, max_price)` (token-driven). Book level units documented as token-quantity in both.
- [ ] EV gate uses estimated avg price from the **selected token's book** (YES for long, NO for short), not top-of-book.
- [ ] Single YES book fetch and single NO book fetch per decision (no duplicate HTTP per side).
- [ ] Test: synthetic book `[{"price": "0.62", "size": "10"}, {"price": "0.70", "size": "15"}]` (i.e., 10 tokens at $0.62 and 15 tokens at $0.70) with $10 budget. First level USD capacity = `0.62 × 10 = $6.20`, second level capacity needed = `$3.80 / 0.70 ≈ 5.43 tokens`. VWAP ≈ `$10 / (10 + 5.43) ≈ 0.6481`. Assert returned avg matches.
- [ ] Test: empty book returns `fully_filled=False`.
- [ ] Test: book with only YES depth available, NO book empty, attempting a NO trade returns `fully_filled=False` and does not fall back to the YES book.
- [ ] Test (market): `estimate_market_ioc_fill(asks, usd_to_spend=$10)` with book `[{"price": "0.62", "size": "10"}, {"price": "0.70", "size": "15"}]` returns VWAP ≈ 0.6481, `tokens_filled ≈ 15.43`, `fully_filled=True` — sweeps through both levels.
- [ ] Test (limit, sufficient liquidity): `estimate_limit_ioc_fill(asks, target_token_qty=10, max_price=$0.50)` with book `[{"price": "0.40", "size": "10"}]` returns VWAP=$0.40, `tokens_filled=10`, `actual_cost=$4.00`, `fully_filled=True` — confirms the reviewer-flagged correctness case where USD budget would have under-counted.
- [ ] Test (limit, price cap): `estimate_limit_ioc_fill(asks, target_token_qty=20, max_price=$0.62)` with book `[{"price": "0.62", "size": "10"}, {"price": "0.70", "size": "15"}]` returns `tokens_filled=10`, `fully_filled=False` (stops at the cap, doesn't sweep through to $0.70).

### Effort

1 day.

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

   **Implementation note (corrected from earlier draft):** the current code calls `load_dotenv()` at module import time ([bot.py:83](bot.py:83)), before `run_integrated_bot` runs. If the plaintext-refusal check is placed inside `run_integrated_bot`, the plaintext `.env` has already been read into `os.environ` by then — the refusal is too late to prevent the leak.

   Two acceptable implementation patterns:

   **Pattern A (preferred): move `load_dotenv` behind a conditional.**

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

   **Pattern B (alternative): always skip `load_dotenv` in live mode regardless of .env presence.**

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

Strict dependency order:

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
                                       Phase 2.5 (dynamic sizing: fixed | percent) ── NEW, operator-requested
                                                      │
                                                      ▼
                                       Phase 5 (depth estimator, per-side book) ── benefits both order types
                                                      │
                                                      ▼
                                       Phase 4 (calibration analysis)
                                                      │
                                                      ├── if calibration passes
                                                      ▼
                                       Phase 3 (ORDER_TYPE configurable: LIMIT_IOC)
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

Phase 2.5 is independent of code-correctness concerns but is the right place in the sequence: after Phase 2 confirms the operator's fixed-mode sizing target, Phase 2.5 adds the percent-mode option. Phase 7.5 is operator-driven evaluation only (no code in this phase) and gates whether Phase 8 deploys for BTC alone or for a wider asset set. Phase 8 follows Phase 7.5 so the deployment is built around the operator's final asset selection.

### Critical path

- **Must ship before next live run:** Phase 0 (lost trade reconciled, fill guards in place).
- **Phase 1 status:** Phase 1.1 (wire `SPIKE_THRESHOLD` / `DIVERGENCE_THRESHOLD` to env) is **runtime-behavior** code and DOES block live if the operator intends to tune via env. Phase 1.2 / 1.3 (README + `.env.example`) are documentation-only and do NOT block live. Effort table updated accordingly: Phase 1.1 blocks, 1.2/1.3 do not.
- **Should ship before scaling trade size:** Phase 5 (depth estimator).
- **Strategic decision gate:** Phase 4 must satisfy **all three** of the gates in 4.3 (point estimate, Wilson 95% CI lower bound, out-of-sample persistence) in at least one bucket with **n ≥ 100** Path A trades AND ≥200 Path B observations. A single positive bucket at small n is noise. The earlier "n ≥ 50" wording was inconsistent and is replaced everywhere with n ≥ 100. If calibration fails, the fix is to improve signal processors, not to change order type.

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
    └── bot.log                    # rotated by logrotate
```

The ledger directory MUST be on the same filesystem as the temp-file write target so `os.replace` is atomic. Do not put `live_trades.json` on NFS or a separate mount from the bot's working directory.

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
ExecStart=/opt/polybot/venv/bin/python bot.py --live

Restart=on-failure
RestartSec=30
# Avoid restart storms if the bot fail-stops (e.g., durable-write failure).
# Operator must investigate before restart — limit auto-restarts to 3 per hour.
StartLimitInterval=3600
StartLimitBurst=3

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

- **`Restart=on-failure` with `StartLimitBurst=3`.** The bot's Phase 0 design fail-stops on durable-write failure. systemd should NOT silently restart it more than 3 times in an hour — if the bot fail-stops repeatedly, the operator must investigate, not let systemd paper over the problem.
- **No `Restart=always`.** That would clear the fail-stop guarantee.
- **`User=polybot`, not root.** Containment in case of a compromise.
- **`ProtectSystem=strict` + `ReadWritePaths`.** The bot can only write to ledger and logs directories.
- **`MemoryMax=1G`.** The bot's working set is small; a runaway memory leak would be killed before exhausting the host.

### Redis dependency

The bot requires Redis for simulation/live mode switching. Either:

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
- **Live-trading paused.** Alert if `live_trades.json` contains any record with `settlement_source == "SETTLEMENT_UNKNOWN"` or `needs_reconciliation == true` (see Phase 0 OR-vs-AND rule). A cron that `jq` queries the ledger every 5 minutes and sends an alert if non-empty.
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
       → cp /opt/polybot/ledger/live_trades.json /opt/polybot/ledger/backup/...  (manual ledger backup before any code change touching the ledger schema)
       → systemctl start polybot
       → journalctl -u polybot -f  (watch startup; verify no SettlementLedgerError)
```

**Critical:** before any code change that touches the ledger schema (e.g., Phase 0.3a's `pending_actual_fills` migration), back up `live_trades.json` outside the working directory. The bot's atomic-write pattern protects against partial writes during normal operation, but a botched deploy that runs a new version against an old ledger file can still corrupt state.

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

1 day for the operator to set up the server, systemd unit, logrotate, backups, and basic monitoring. The bot code itself does not need changes for Phase 8 — only the operator's deployment scaffolding.

### Exit criteria

- [ ] `systemctl status polybot` shows `active (running)` after a normal start.
- [ ] `systemctl stop polybot` cleanly stops the bot; the fcntl lock is released; journalctl shows the `on_stop` cleanup running.
- [ ] After a deliberate fail-stop (e.g., write-protect the ledger filesystem to trigger a SettlementLedgerError), systemd does NOT restart-loop more than 3 times.
- [ ] Hourly ledger backup script runs and produces dated artifacts.
- [ ] Alerting rule for unresolved SETTLEMENT_UNKNOWN fires in a test scenario.

---

## Total Effort Estimate

| Phase | Effort | Type | Blocks live? |
|---|---|---|---|
| 0. P0 fill bug | 2 days | Code + manual recovery | **Yes** |
| 1.1. Env wiring (`SPIKE_THRESHOLD`/`DIVERGENCE_THRESHOLD`) | 0.25 day | Runtime code | **Yes** (only if operator wants env-tunable values; otherwise document constants as code-owned) |
| 1.2-1.3. Env audit docs + `.env.example` | 0.25 day | Documentation | No (cleanup) |
| 2. Sizing config + balance pre-flight | 10 min | Operator action | No (operator decision) |
| **2.5. Dynamic trade sizing (NEW)** | **1.5 days** | **Code (AccountState hook + sizing modes)** | **No (feature, operator-requested)** |
| 5. Depth estimator | 1 day | Code | No (improvement) |
| 4. Calibration (incl. `decisions.jsonl` writer) | 4h + elapsed time for data | Code + analysis | No (decision gate for Phase 3) |
| 3. ORDER_TYPE | 3 days | Code | No (feature, blocked by Phase 4) |
| **7.5. Multi-asset evaluation (NEW)** | **Operator-driven, no code** | **Decision** | **No (gates deployment scope)** |
| 6. SOPS | 0.5 day | Code + ops | No (future) |
| **8. Linux deployment (NEW)** | **1 day operator** | **Ops scaffolding** | **No (production deploy)** |
| 7. Live reload | 0 days (Option C) | Docs only | No |

**Minimum to resume live trading safely:** 2.5 days (Phases 0 + 1).

**Recommended before scaling:** 4–5 days (add Phases 4 + 5).

**Full enhancement (limit orders + everything):** ~7 days plus calibration window.

---

## Open Decisions Required From Operator

1. **Trade size for live operation.** Phase 2 recommendation is $5 per trade with $20 cap. Confirm or adjust.
2. **Wire or remove `STOP_LOSS_PCT` / `TAKE_PROFIT_PCT` / `SPIKE_THRESHOLD` / `DIVERGENCE_THRESHOLD`.** Recommendation: remove the first two, wire the latter two.
3. **Calibration data source.** Are there ≥100 settled live trades available? If not, what's the data collection plan?
4. **`ORDER_TYPE` for live mode.** Live mode requires an explicit value. No implicit default in code. `.env.example` should leave it blank or commented with both allowed values shown, so the operator must consciously choose `market_ioc` or `limit_ioc`. This matches the `AGENTS.md` no-silent-fallback rule.
5. **SOPS adoption timeline.** Phase 6 is ready when the team's key management approach is decided.

---

## Lost Trade Recovery — Action Items (single canonical sequence)

The trade `BTC-15MIN-$11-1779093783343` is real on Polymarket but missing from the bot's ledger. Earlier drafts contained contradictory ordering — some sections said recover first, others said recover last. **This is the single canonical sequence:**

1. **Stop live trading now.** Kill the bot process and the wrapper. Do not place any new live orders.
2. **Ship Phase 0.4 (zero-price guard).** ~10-line commit, lands first, prevents further bad fills from corrupting the ledger during the rest of Phase 0.
3. **Ship Phase 0.3a + 0.2 + 0.3b + 0.5 (adapter scaffold + VWAP injection + dust normalization + tests).** Single adapter-patch series with full test coverage.
4. **Verify externally.** Open the Polymarket UI (or query on-chain) to obtain the actual payout for the lost order `BTC-15MIN-$11-1779093783343` and the exact submitted/filled/market-end timestamps from logs or Polymarket records.
5. **Reconstruct the unknown record.** Run `venv/bin/python mark_settlement_resolved.py --create-unknown-from-external-order ...` per the Phase 0.1 command, using actual values (no placeholders).
6. **Immediately resolve with verified payout.** Run `venv/bin/python mark_settlement_resolved.py --order-id ... --payout <verified> ...` **without waiting** for `auto_redeem` or the grace timeout. Polymarket does not replay missed websocket events; the market has already resolved; the bot will not receive any future `auto_redeem` for this trade.
7. **Confirm no remaining unresolved entries.** The live-trading pause gate keys off unresolved unknowns. The actual code at [bot.py:784](bot.py:784) uses **OR**, not AND: any settled record with `needs_reconciliation is True` **OR** `settlement_source == "SETTLEMENT_UNKNOWN"` triggers the pause. Inspect `live_trades.json` and confirm zero entries match **either** condition. A reconstructed-but-unresolved entry (which has both flags set) will still pause live; a partially-resolved entry that updated only one flag will also still pause live. Resolution must set `settlement_source` to a real value (e.g., `"manual_reconciliation"`) AND set `needs_reconciliation` to `false`.
8. **Smoke-test before resuming.** Start the bot in `--test-mode` (no live orders) and verify the new adapter callback path fires correctly on a synthetic fill. Then run one deliberately tiny `$5` live trade and confirm the actual-fill side channel + ledger record + `auto_redeem` settlement all complete cleanly.
9. **Then, and only then, resume normal live trading.**

**Earlier contradictions resolved:**
- The "recover before any other work" wording is dropped. The zero-price guard is the only thing that must ship before recovery. The full adapter normalization must also ship before recovery to prevent the next bad fill from re‑creating the same problem during reconciliation.
- The "wait for `auto_redeem` or grace timeout" wording is dropped everywhere. The lost market has already resolved; waiting accomplishes nothing and risks a $0 grace-timeout booking before the operator's manual resolution.

This sequence must complete before resuming live trading. The risk engine has a phantom ~$11 exposure missing from its tracking until step 6 finishes.
