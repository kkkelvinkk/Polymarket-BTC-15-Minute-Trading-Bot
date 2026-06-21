# Data Collection And Storage Inventory

Snapshot date: 2026-05-21

This document audits what this repository already collects, what it persists,
where it persists it, and whether the stored shape is sufficient for offline
parameter replay. It is an inventory, not an implementation plan.

Terminology:

- **Collected + stored** means the fact is written to a durable artifact.
- **Collected + partially stored** means a summary, derived value, or final
  result is written, but the raw replay input is not.
- **Collected + not stored** means the bot reads or computes the value only in
  process memory.
- **Configured + stored** means the value is operator/runtime configuration,
  not market evidence.

Replay boundary:

- Current durable logs can support decision-observation analysis.
- Current outcome joins fetch Polymarket Gamma resolution data live. The repo
  does not currently persist deterministic market-resolution snapshots for
  later replay.
- Current durable logs cannot support deterministic brute-force replay of
  most signal thresholds and weights because full raw decision inputs are not
  persisted.
- Any offline analyzer built from the current artifacts must be called
  **decision/policy replay** unless live-equivalent fills, fees, settlement,
  P&L, risk checks, and ledger effects are modeled with the same semantics as
  live mode.

## Current Local Artifacts

Observed in this worktree at the snapshot date. Presence and record counts are
volatile because local runs can keep appending or rewriting these files:

```text
decisions.jsonl                         present, append-only JSONL decision observations
live_trades.json                        present, schema v3 live settlement ledger
paper_trades.json                       present, JSON array of paper observations
credentials/encrypted_credentials.json  present, encrypted credentials vault
.env                                    present, ignored local runtime configuration
.env.example                            present, tracked runtime configuration template
dump.rdb                                present, ignored Redis persistence snapshot
live_trades.json.lock                   present, runtime ledger lock file
console_logs/err.log                    present, local process stderr capture
grafana/dashboard.json                  present, tracked Grafana dashboard config
```

The schemas below are the important part for future collection/replay work.
At this audit point, the local `decisions.jsonl` first line timestamp is
`2026-05-19T07:18:27.057555+00:00` and the last observed line timestamp is
`2026-05-21T15:21:00.019352+00:00`; this span is volatile and should not be
hard-coded into tooling.

## Durable Artifacts

| Artifact | Status | Storage path | Format | Write timing | Timeframe / retention | Replay value |
| --- | --- | --- | --- | --- | --- | --- |
| Decision observation log | Collected + partially stored | `DECISION_LOG_PATH`; if unset, sibling of `LIVE_TRADE_LEDGER_PATH`; otherwise `decisions.jsonl` in current working directory. Local/dev runs usually use the repo root; deploy service sets `WorkingDirectory` and an absolute env path. | JSON Lines, one JSON object per decision attempt | Exactly once when `DecisionRecord` exits | Append-only until external rotation. Deploy logrotate template, if installed, rotates daily and keeps 30 compressed rotations. Deploy cron template, if installed, backs up `decisions.jsonl` once daily at 03:17 UTC. | Useful for decision outcome joins and gate analysis. Not enough for raw signal replay. |
| Live settlement ledger | Collected + stored for live execution/settlement, partially stored for strategy replay | `LIVE_TRADE_LEDGER_PATH`, default local `live_trades.json`; deploy path `/opt/polybot/ledger/live_trades.json` | JSON object, `ledger_schema_version == 3` | Atomic full-file rewrite after intent, fill, settlement, pending actual-fill, pending redeem, terminal no-fill intent audit, or manual reconciliation changes | Durable across restarts. No in-code pruning of settled records. Deploy cron template, if installed, backs it up hourly. | Useful for accepted live orders, actual fills, settlement, and P&L. Does not contain rejected decision raw inputs. |
| Paper decision observations | Collected + partially stored, best-effort | Repo working directory `paper_trades.json` | JSON array | Best-effort full-file rewrite after every positive simulation decision observation; write failures are logged | Current process memory list only; file is overwritten from in-memory list and is not loaded on startup | Records positive decision-only observations. Not guaranteed durable, not live-equivalent, and not enough for replay. |
| Runtime configuration | Configured + stored | `.env` ignored local file, `.env.example` tracked template, process environment, deploy service `Environment=` lines | dotenv/process environment key-value settings | `load_dotenv()` runs once at startup. Env helper functions may read process env per decision/event, but editing `.env` while the bot runs does not reload the file. | Durable until operator edits local/deploy config. Effective runtime config is not written into each decision record. | Major adjustable-variable source: sizing, risk caps, order type, quote stability, limit edge, min confidence, EV buffers, settlement/reconciliation settings. Exact replay needs the effective config captured per decision. |
| Credentials vault | Configured + stored | `credentials/encrypted_credentials.json` | Encrypted JSON payload managed by `vault_store.py` / `vault_crypto.py` | Created or updated by vault tools | Durable until operator replaces it | Not replay data. Contains live credential material and Polygon RPC URL. |
| Redis simulation mode flag | Configured + conditionally stored | Redis key `btc_trading:simulation_mode` in `REDIS_DB`, default DB 2. Redis may also persist `dump.rdb`; a local ignored `dump.rdb` is present in this worktree. | String `"1"` for simulation, `"0"` for live | Set at bot startup when Redis is reachable; can be changed by `redis_control.py`. Live mode requires Redis. Simulation mode can continue without a durable Redis write if Redis is unavailable. | Redis retention depends on Redis deployment. No TTL is set by repo code. `dump.rdb` retention depends on local Redis configuration. | Not replay data. Controls live/simulation mode. |
| Nautilus logs | Collected + stored by Nautilus/logging layer | `NAUTILUS_LOG_DIR`, required env; deploy path `/opt/polybot/logs/nautilus` | Nautilus text/log files | Runtime logging | Retention controlled by deployment/log setup, not repo code | Useful for debugging. Not structured enough for deterministic replay. |
| Bot process logs / journal | Collected + stored by operator environment | stderr in local runs, local `console_logs/err.log` when captured by the operator, journald in deploy service, optional `/opt/polybot/logs/bot.log` if operator redirects | Text logs | Runtime logging | journald/logrotate/operator policy. Deploy logrotate template, if installed, includes `/opt/polybot/logs/bot.log` daily, rotate 30 | Debug only. Logs contain some values but should not be treated as replay source of truth. |
| Prometheus/Grafana metrics | Collected + not stored by repo | HTTP `/metrics` on `GRAFANA_HOST:GRAFANA_PORT`, default `0.0.0.0:8000` | Prometheus text exposition | Updated every 5 seconds while exporter runs | In-repo exporter keeps in-memory metrics only. Durable history requires external Prometheus/Grafana scrape storage. | Operational monitoring only. Not decision replay data. |
| Static monitoring configuration | Configured + stored | `grafana/dashboard.json` | Grafana dashboard JSON | Edited manually in repo | Tracked with the repo | Visualization config only. It does not store metric samples, market facts, or decision replay inputs. |

## `decisions.jsonl` Shape

Each record is one decision attempt, including early rejects and exceptions.
The writer appends one JSON object per line and fsyncs the append.

Important schema behavior:

- There is no `schema_version` field in `decisions.jsonl`.
- `DecisionRecord` initializes a base set of fields, but callers can add more
  keys through `rec.update()` and `rec.decided()`.
- Older lines simply lack newer fields. A replay/analyzer reader must treat a
  missing key, an explicit `null`, and a present value as different states.
- Existing local files can contain mixed shapes because the log is append-only
  across code changes and runtime branches.

Path resolution:

```text
1. DECISION_LOG_PATH if set
2. dirname(resolve(LIVE_TRADE_LEDGER_PATH)) / decisions.jsonl if LIVE_TRADE_LEDGER_PATH is set
3. resolve("decisions.jsonl") in current working directory
```

Base fields currently initialized by `DecisionRecord`:

```text
decision_id
ts
current_price
slug
condition_id
yes_token_id
no_token_id
market_end_time
decision_snapshot_at
decision_reference_time
decision_price_history_len
decision_tick_buffer_len
decision_market_timestamp
decision_sub_interval
context_sma20_deviation
context_momentum
context_volatility
seconds_into_sub_interval
trade_window_label
trend_price_band
strategy_observation_mode
fused_confidence
fused_direction
decided_direction
rejected_at_gate
rejection_reason
executable_entry
estimated_tokens_filled
estimated_actual_cost
depth_fully_filled
yes_ask
no_ask
model_signals
sizing_mode
resolved_trade_usd
free_collateral_at_decision
account_state_age_seconds
account_state_sequence
balance_stale_reason
```

Known conditional/dynamic fields added outside the base initializer:

```text
decision_snapshot_age_seconds
max_decision_snapshot_age_seconds
limit_price
submitted_limit_price
limit_order_token_qty
```

`model_signals` is either null or a list of generated signals:

```text
source
direction
score
confidence
metadata
```

`model_signals` does not include every field from the in-memory
`TradingSignal`. Missing fields needed for exact fusion replay include:

```text
timestamp
signal_type
strength
current_price
target_price
stop_loss
```

Important gaps for replay:

- Full `price_history` is not stored; only `decision_price_history_len` is.
- Full `tick_buffer` is not stored; only `decision_tick_buffer_len` is.
- YES bid, NO bid, and full YES/NO bid/ask quote snapshots are not always
  stored. `current_price` is the YES mid, `yes_ask`/`no_ask` are populated only
  after the path reaches executable side selection.
- YES/NO order books fetched for the decision are not stored.
- Fear & Greed raw value/classification are not stored unless a generated
  sentiment signal embeds them in `model_signals`.
- Coinbase raw spot and fetch timestamp are not stored. PriceDivergence may
  store derived metadata such as `poly_prob` and `spot_momentum` only when a
  divergence signal fires.
- Deribit PCR raw/cache data is not stored unless a generated PCR signal embeds
  derived PCR metadata.
- Fusion contribution metadata is not stored. The log stores
  `fused_confidence` and `fused_direction`, but not fused score,
  bullish/bearish contributions, total contribution, signal timestamps,
  signal strengths, source weights used, or min-score/min-signal inputs.
- Rejected decisions before signal generation have no generated-signal details.
- The record does not persist the full runtime configuration used for that
  decision; only a subset appears through gates and sizing fields.

Decision timing coverage:

```text
Live candidate window:
13_14_current  780 <= seconds_into_sub_interval < 840

Shadow policy observation windows:
06_09       360 <= seconds < 540
09_11       540 <= seconds < 660
11_13       660 <= seconds < 780
14_15_late  840 <= seconds < 900
```

## `live_trades.json` Shape

The root object is current schema v3:

```text
ledger_schema_version: 3
open: object keyed by client order id
settled: list of settled or unknown settlement records
seen_auto_redeem_events: list of auto-redeem dedupe keys
pending_auto_redeem_events: object keyed by redeem event key
pending_actual_fills: object keyed by client order id
submitted_order_intents: object keyed by client order id
```

Storage behavior:

- The bot holds an fcntl lock at `live_trades.json.lock` while running.
- Writes are full-file rewrites through `live_trades.json.tmp`, followed by
  `os.replace` and parent-directory fsync.
- Existing ledger files must already be schema v3. The bot does not migrate old
  ledger shapes.
- Terminal no-fill order events are also persisted on the submitted intent as
  audit evidence instead of deleting the original intent.

`submitted_order_intents` records are written before exchange submission.
Typical fields:

```text
client_order_id
order_id
status
order_side
order_type
quote_quantity
quantity_mode
direction
outcome_side
trade_label
spend_amount
size
estimated_tokens
estimated_price
entry_price
limit_ioc_fill_policy
accepted_limit_price
submitted_limit_price
price_source
instrument_id
token_id
slug
condition_id
market_start_time
market_end_time
submitted_at
intent_persisted_at
signal_score
signal_confidence
terminal_no_fill_at
terminal_no_fill_reason
terminal_no_fill_event
terminal_no_fill_zero_quantity_evidence
needs_reconciliation
```

`open` records represent filled live trades waiting for settlement. They are
based on submitted metadata plus fill details, typically:

```text
order_id
entry_price
filled_qty
filled_notional
size
filled_at
direction
trade_label
estimated_tokens
order_type
quote_quantity
quantity_mode
limit_ioc_fill_policy
accepted_limit_price
submitted_limit_price
instrument_id
token_id
slug
condition_id
market_start_time
market_end_time
submitted_at
signal_score
signal_confidence
submitted_order_intent
venue_order_id / normalized fill identity fields when available
```

`pending_actual_fills` records preserve actual-fill callback evidence that has
not yet been safely consumed by the live fill path. The current schema requires
aggregate fill evidence:

```text
fills: list of {
  fill_key
  filled_qty
  price
  notional
  raw_callback_payload
  received_at
}
total_filled_qty
total_filled_notional
vwap
venue_order_id
condition_id
token_id
slug
direction
trade_label
submitted_at
raw_status_report
raw_callback_payload
received_at / last_received_at
submitted_size
requires_external_fill_repair
external_fill_repair_reason
external_fill_repair_evidence
```

`pending_auto_redeem_events` preserves unmatched settlement/redeem payloads:

```text
raw auto_redeem payload fields
_pending_since
_pending_reason
```

`settled` records contain final or unresolved settlement accounting. Normal
settlement fields include:

```text
order_id
settled_at
settlement_source: auto_redeem | late_auto_redeem | manual_reconciliation
payout
pnl
exit_price
needs_reconciliation: false
auto_redeem
auto_redeem_event_key
all carried order/fill metadata from the open trade
```

Unknown settlement records use:

```text
settlement_source: SETTLEMENT_UNKNOWN
needs_reconciliation: true
payout: UNKNOWN
pnl: UNKNOWN
unknown_reason
raw_callback_payload / submitted_order_intent when available
created_at when created from actual-fill callback evidence
```

Replay value:

- Strong source for actual accepted live order outcomes and settlement P&L.
- Weak source for signal brute force because it contains only accepted live
  trades, not all candidate/rejected/shadow decisions and not raw signal inputs.

## `paper_trades.json` Shape

`paper_trades.json` is written by `_save_paper_trades()` after a positive
simulation decision observation. It is not appended; the current in-memory
`paper_trades` list is dumped as the full file.

Record fields:

```text
timestamp
direction
size_usd
price
signal_score
signal_confidence
outcome
```

Important constraints:

- The bot initializes `paper_trades` as an empty list at startup.
- Existing `paper_trades.json` is not loaded into memory at startup.
- A new process's first paper trade can overwrite the file with only that new
  process's in-memory observations.
- `_save_paper_trades()` logs write failures instead of making this file an
  authoritative durable ledger.
- This is explicitly decision-only observation. It does not model fills,
  settlement, fees, P&L, ledger effects, or live failure behavior.

## In-Memory Decision Inputs

These inputs are collected and used by the active decision path, but are not
fully durable today.

| Data | Status | In-memory location | Timeframe in memory | Durable representation today | Replay impact |
| --- | --- | --- | --- | --- | --- |
| Polymarket market metadata | Collected + partially stored | `all_btc_instruments`, current market metadata, `DecisionInputSnapshot.market_metadata()` | Current bot process; markets loaded for current/near-future slugs generated at startup | `decisions.jsonl` stores slug, condition id, token ids, market end. `live_trades.json` stores order market metadata for live orders. | Mostly enough for outcome join by slug/condition, but not a complete market snapshot. |
| YES quote ticks | Collected + partially stored | `on_quote_tick`, `_last_bid_ask`, `price_history`, `_tick_buffer` | `price_history` max 100 mids; `_tick_buffer` max 2000 entries; neither is cleared on every market switch in current code | Decision log stores current YES mid as `current_price`, history length, tick-buffer length, and sometimes `yes_ask`. Full ticks are not stored. | Major blocker for replaying SpikeDetection and TickVelocity exactly. |
| NO quote ticks | Collected + partially stored | `_last_no_bid_ask` | Latest NO bid/ask only | Decision log stores `no_ask` only after the selected side reaches the NO executable path. NO bid is not stored. | Blocks exact short-side liquidity and quote replay. |
| Frozen decision snapshot | Collected + partially stored | `DecisionInputSnapshot` | Immutable object only for active decision execution | Summary fields written to `decisions.jsonl`; full snapshot is not written | This is the correct capture point for a future raw recorder. |
| Full `price_history` | Collected + not stored | `self.price_history` | Max 100 YES mids; synthetic startup history may be inserted if fewer than 20 values exist | Only length is stored | Required for replaying SpikeDetection and context momentum/volatility. |
| Full `tick_buffer` | Collected + not stored | `self._tick_buffer` | Max 2000 YES mid ticks with timezone-aware timestamps | Only length is stored | Required for replaying TickVelocity. |
| YES/NO order books | Collected + not stored | `metadata["yes_order_book"]`, `metadata["no_order_book"]` during context build | Decision-local metadata only | Only selected depth estimates are stored: executable entry, tokens, cost, fully filled | Required for replaying OrderBookImbalance and EV/depth gates under new thresholds. |
| Quote stability count | Collected + not stored, partially inferable on failures | `DecisionInputSnapshot.stable_tick_count` from `_stable_tick_count` | Frozen for the active decision snapshot | Not stored as its own field. Failed quote-stability gates embed the value in `rejection_reason`; passing decisions do not persist it. | Alternate `QUOTE_STABILITY_REQUIRED` replay is low confidence because passing decision values are absent. |
| Fear & Greed | Collected + partially stored | `metadata["sentiment_score"]`, `metadata["sentiment_classification"]` | Decision-local metadata only | Stored only if SentimentAnalysis emits a signal and metadata appears in `model_signals` | Required for replaying sentiment thresholds, including no-signal cases. |
| Coinbase BTC spot | Collected + not stored for raw spot; partially stored for derived signal metadata | `metadata["spot_price"]` | Decision-local metadata only | Raw spot price and fetch timestamp are not stored. PriceDivergence may store derived values such as `poly_prob` and `spot_momentum` when it emits a signal. | Required for replaying PriceDivergence. Current derived metadata is not enough because raw spot, fetch time, and processor state also matter. |
| Coinbase spot history used by PriceDivergence | Collected + not stored | `PriceDivergenceProcessor._spot_history` | Last 10 spot reads per processor instance | Not stored | Major blocker for exact PriceDivergence replay. |
| Deribit PCR data | Collected + partially stored | `DeribitPCRProcessor._cached_result`, `_cache_time` | Cached for `cache_seconds`, currently 300 seconds | Stored only if DeribitPCR emits a signal with derived metadata; cache timestamp is not fully logged | Major blocker for exact PCR threshold/cache replay. |
| Generated signals | Collected + partially stored for signals that exist | Local `signals` list, then `model_signals` | Current decision only | `model_signals` stores generated signal source, direction, score, confidence, metadata. It omits timestamp, signal type, strength, current price, target price, and stop loss. | Useful for approximate reweighting of existing generated signals. Not enough for exact fusion replay or changing thresholds that decide whether a signal exists. |
| Fusion output | Collected + partially stored | `fused` local variable and fusion engine history | Fusion engine keeps last 100 fusions in memory | Decision log stores fused direction/confidence only | Existing generated signals can be reweighted partially, but missing fused score/contribution/source-weight data reduces auditability. |
| Fusion history | Collected + not stored | `SignalFusionEngine._signal_history` | Last 100 fusions per engine instance | Not stored | Not required for current fusion calculation, but useful for diagnostics. |
| Learning-engine state | Initialized + not stored; not active in scheduled decision flow today | `LearningEngine._signal_performance`, `_weight_adjustments` | Current process only if learning APIs are invoked | Not stored | Weight-optimization state is relevant to future adaptive tuning, but current active bot decisions do not persist or replay it. |
| Trend filter side | Derived + partially stored | Local branch in `_make_trading_decision_body` | Current decision only | `trend_price_band`, `decided_direction`, and rejection gate/reason imply outcome | Threshold brute force needs current price, which is stored; full quote context is not. |
| Depth-aware entry estimate | Derived + stored after depth gate | `DepthAwareEntry` | Current decision only | `executable_entry`, `estimated_tokens_filled`, `estimated_actual_cost`, `depth_fully_filled` | Useful for current config. Not enough to recompute under alternate order-book thresholds/caps without raw book. |
| AccountState free collateral | Collected + partially stored | `AccountBalanceTracker` | Latest exchange-reported AccountState only | Decision log stores free collateral, age, sequence, stale reason at decision; live ledger stores order sizing once order is submitted | Useful for decision audit. Raw AccountState event is not stored. |
| Risk engine state | Collected + partially stored | `RiskEngine._positions`, daily counters | Current process; rehydrated from live ledger on startup for open/settled risk | Live ledger stores live open/settled orders; no separate risk-state file | Enough for live accounting, not enough for replaying rejected historical risk checks unless decision records include the gate. |
| Performance tracker trades/metrics | Collected + not stored by repo | `PerformanceTracker._trades`, `_metrics_history` | `_trades` max 1000; metrics history max 10000 snapshots | Metrics served over HTTP only unless external scraper stores them | Operational, not replay source. |

## External Sources Currently Read

| Source | Active decision use | Stored today | Notes |
| --- | --- | --- | --- |
| Polymarket Nautilus market/quote stream | Yes | Partially | Drives market list, quote ticks, AccountState, order/fill events. Raw stream is not stored. |
| Polymarket CLOB `/book` | Yes | No raw storage | YES and NO books are fetched per decision context. Only depth estimates may be stored. |
| Alternative.me Fear & Greed | Yes | Partial signal metadata only | No raw per-decision storage when no SentimentAnalysis signal fires. |
| Coinbase Exchange ticker | Yes | Derived `PriceDivergence` signal metadata only when emitted | Raw Coinbase spot, fetch timestamp, and per-decision spot input are not stored. |
| Deribit BTC options summary | Yes | Partial signal metadata only | PCR computed/cached inside processor; raw option summaries are not stored. |
| Polymarket auto-redeem callbacks | Yes, live settlement | Stored in live ledger | Stored as `auto_redeem` on settled records or pending payloads when unmatched. |
| Polymarket actual-fill callbacks / Nautilus fill events | Yes, live fill accounting | Stored in live ledger when needed | Accepted fills enter `open`; pending/repair/unknown evidence is durable. |
| Polymarket Gamma market resolution | Used by offline analyzers, not by live decision path | Not stored by repo | `estimate_decision_results.py` and `calibration_decision_join.py` fetch Gamma by slug and parse `closed`, `outcomes`, and `outcomePrices`. Deterministic replay needs persisted resolution snapshots. |
| Binance WebSocket adapter | Not active in `bot.py` decision path | Not stored | Present in repo/legacy ingestion modules. It caches latest values in memory only if separately used. |
| UnifiedDataAdapter / Solana adapter | Not active in `bot.py` decision path | Not stored | Present in repo but not part of the active BTC 15-minute decision path. |

## Replay Readiness By Tunable

| Tunable family | Current durable replay readiness | Why |
| --- | --- | --- |
| Trade window labels and timing | Medium | Decision records include sub-interval seconds and window label only for captured candidates. They do not include every tick outside captured windows. |
| `QUOTE_STABILITY_REQUIRED` | Low | The snapshot captures `stable_tick_count`, but `decisions.jsonl` only preserves it in rejection text for failed quote-stability gates. Passing decision values are not stored. |
| Trend threshold relabeling | Medium | Current YES mid is stored, so alternate neutral/up/down labels can be tested for captured candidates only. This is label analysis, not full alternate-policy replay. |
| Full alternate-policy replay after trend changes | Low | Neutral rejects can return before executable side quotes and depth context are persisted, so the log cannot reconstruct what later gates would have seen. |
| `MIN_SIGNAL_CONFIDENCE` | Medium | Stored fused confidence supports rechecking the final current fused output only. It cannot recompute altered upstream signal existence. |
| Fusion weights / `min_signals` / `min_score` | Low to medium, approximate only | Existing `model_signals` can support approximate re-fusion of signals that already fired. It is missing signal timestamp, strength, signal type, fusion score/contributions, and the exact weights used, and it cannot discover signals suppressed by current processor thresholds. |
| EV buffers / `LIMIT_REQUIRED_EDGE` | Low | Current executable estimate is stored, but alternate limit caps/depth fills require raw selected order book. |
| SpikeDetection thresholds | Low | Requires full `price_history`, not stored. |
| TickVelocity thresholds | Low | Requires full tick buffer with timestamps, not stored. |
| OrderBookImbalance thresholds | Low | Requires raw YES order book, not stored. |
| Sentiment thresholds | Low | Requires Fear & Greed raw score for every decision, including no-signal cases. Not reliably stored. |
| PriceDivergence thresholds | Low | Requires current spot plus processor spot history. Not reliably stored. |
| DeribitPCR thresholds/cache | Low | Requires PCR data/cache timestamp or raw Deribit summaries. Not reliably stored. |
| Risk/sizing parameters | Medium for live accepted orders, low for all candidates | Live ledger stores submitted/filled sizing. Rejected candidate risk context is only partially in decision records. |

## Central Collection Engine Implications

A central collection engine should treat market observations as immutable facts
and separate them from tunable policy decisions.

Minimum raw fact payload per candidate decision should include:

```text
decision_id
bot_instance_id / repo_name / strategy_version / git_commit
decision_reference_time
captured_at
market slug, condition_id, start/end timestamps
YES token id, NO token id
YES bid, YES ask, YES mid, quote timestamp
NO bid, NO ask, quote timestamp
stable_tick_count
full price_history with values and synthetic-history marker if generated
full tick_buffer with timestamps and prices
YES order book snapshot
NO order book snapshot
Fear & Greed raw value, classification, source timestamp, fetch timestamp
Coinbase spot raw value and fetch timestamp
Coinbase spot history used by PriceDivergence
Deribit computed PCR data, cache timestamp, fetch timestamp
Deribit raw option summaries if processor thresholds will change
all generated signals with timestamp, source, signal_type, direction, strength,
  confidence, current_price, target_price, stop_loss, and metadata
fusion inputs, source weights, contributions, score, confidence, min_signals, min_score
effective runtime config used by the decision
trend thresholds and resulting trend side
order config used by gates
depth/EV gate inputs and outputs
decision gate/result
later market-resolution snapshot: Gamma closed status, outcomes, outcomePrices,
  winning outcome, fetch timestamp, and source payload id/hash
live-only: submitted order, actual fills, fees, settlement, P&L, ledger effects
```

The existing `DecisionInputSnapshot` plus the fetched market-context metadata is
the natural source for this future raw payload. The current `decisions.jsonl`
should remain a compact observation/gate log; raw replay payloads should be
stored separately to avoid making every summary decision line large and hard to
operate.
