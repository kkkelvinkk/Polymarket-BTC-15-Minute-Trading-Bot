# Strategy Algorithm Inventory

Snapshot date: 2026-05-21

This document describes the active decision path in `bot.py` for
`IntegratedBTCStrategy`. It separates external observations from tunable
strategy variables so a later analyzer can replay raw decisions and brute-force
parameter sets without changing market facts.

This is not a profitability report. Decision logs and shadow observations are
not live-equivalent trade simulation unless fills, settlement, fees, PnL, risk,
and ledger behavior are modeled with the same semantics as live trading.

## Active Decision Pipeline

1. Quote ticks arrive for the active YES instrument.
2. The bot computes the YES mid price:

   ```text
   current_price = (yes_bid + yes_ask) / 2
   ```

3. The bot appends that mid price to `price_history`.
4. The bot appends `{"ts": now_utc, "price": current_price}` to `tick_buffer`.
5. The bot waits for `QUOTE_STABILITY_REQUIRED` valid YES quote ticks after a
   market switch or reset.
6. The bot computes market-relative timing:

   ```text
   elapsed_secs = now_utc.timestamp() - market_start_timestamp
   sub_interval = int(elapsed_secs // 900)
   seconds_into_sub_interval = elapsed_secs % 900
   trade_key = (market_start_timestamp, sub_interval)
   ```

7. Live trading is considered only in the late baseline window:

   ```text
   780 <= seconds_into_sub_interval < 840
   ```

   That is minute 13:00 through 13:59 of each 15-minute market interval.

8. Shadow policy observations use these non-live windows:

   ```text
   06_09       = 360 <= seconds < 540
   09_11       = 540 <= seconds < 660
   11_13       = 660 <= seconds < 780
   14_15_late  = 840 <= seconds < 900
   ```

9. When a live or shadow candidate is found, the bot freezes one
   `DecisionInputSnapshot`. All local signal inputs for that decision come from
   this snapshot, not from later shared mutable state.
10. The bot builds market context from the frozen snapshot plus external APIs.
11. The six processors generate zero or more `TradingSignal` objects.
12. The fusion engine combines the generated signals.
13. The trend filter maps the current YES price into candidate YES/NO direction.
14. If signal confirmation is enabled, fused direction must match the trend
   direction and fused confidence must pass `MIN_SIGNAL_CONFIDENCE`.
15. The EV/depth gate checks executable book depth against fused confidence.
16. The live path rechecks freshness, risk, liquidity, active market identity,
   and order envelope before placing an order.

## Timing And Scheduling Conditions

Live scheduling requires all of these:

```text
same current market as trigger tick
trigger snapshot exists
780 <= seconds_into_sub_interval < 840
trade_key != last_trade_time
not _decision_in_progress
not _shadow_decision_in_progress
not _restart_in_progress
market is not waiting to open
stable_tick_count >= QUOTE_STABILITY_REQUIRED
```

Shadow scheduling is similar, but it uses the shadow windows listed above and
deduplicates by `(market_start_timestamp, sub_interval, trade_window_label)`.

Live market switches and auto-restart are postponed while a live decision is in
progress. Live scheduling is also blocked while a shadow observation is running.

## Frozen Market Context

`decision_context.py` builds the processor metadata from the frozen snapshot.
The local context stats are:

```text
recent_prices = last 20 values from snapshot.price_history
sma20 = average(recent_prices)
context_sma20_deviation = (current_price - sma20) / sma20
context_momentum = (current_price - snapshot.price_history[-5]) / snapshot.price_history[-5]
context_volatility = sqrt(mean((price - sma20)^2 for price in recent_prices))
```

Context also fetches:

```text
yes_order_book from Polymarket CLOB
no_order_book from Polymarket CLOB, when no_token_id exists
Fear & Greed value/classification from NewsSocialDataSource
Coinbase BTC spot price
```

The context builder fails closed if required values are missing: fewer than
20 prices, missing YES token id, mismatched cached YES token id, missing book,
missing Fear & Greed value/classification, or missing Coinbase spot.

`context_sma20_deviation` is recorded for analysis. It is not itself a direct
trade trigger. SpikeDetection computes its own MA deviation from the same
snapshot history.

## Signal Object Score

Each processor returns a `TradingSignal` with `strength` and `confidence`.
The generic signal score is:

```text
strength_factor = strength.value / 4
signal_score = (strength_factor * 0.5 + confidence * 0.5) * 100
```

Strength values:

```text
WEAK        = 1
MODERATE    = 2
STRONG      = 3
VERY_STRONG = 4
```

The fusion engine does not directly use `signal_score`. Fusion uses confidence,
strength factor, and source weight.

## Fusion Engine

Active weights are configured in `IntegratedBTCStrategy._configure_fusion_engine`:

```text
OrderBookImbalance = 0.30
TickVelocity       = 0.25
PriceDivergence    = 0.18
SpikeDetection     = 0.12
DeribitPCR         = 0.10
SentimentAnalysis  = 0.05
```

Fusion call:

```text
fuse_signals(signals, min_signals=2, min_score=55.0)
```

Fusion keeps only signals less than 5 minutes old. For each recent signal:

```text
source_weight = configured weight for signal.source
strength_factor = signal.strength.value / 4
confidence = clamp(signal.confidence, 0, 1)
contribution = source_weight * confidence * strength_factor
```

Bullish and bearish contributions are summed separately. The fused direction is
the larger side:

```text
total_contrib = bullish_contrib + bearish_contrib
dominant = max(bullish_contrib, bearish_contrib)
fused_score = dominant / total_contrib * 100
fused_confidence = average(signal.confidence for recent_signals)
```

Fusion rejects when:

```text
len(signals) < 2
len(recent_signals) < 2
total_contrib < 0.0001
fused_score < 55.0
```

Tie behavior: if bullish contribution equals bearish contribution, fused
direction is bullish.

## Processor 1: SpikeDetection

Active constructor values:

```text
spike_threshold = 0.05
lookback_periods = 20
min_confidence = 0.55
velocity_threshold = 0.03
source weight = 0.12
```

Inputs:

```text
current_price = frozen YES mid
historical_prices = frozen price_history
```

Shared calculations:

```text
ma = average(last 20 historical_prices)
deviation = (current_price - ma) / ma
deviation_abs = abs(deviation)
velocity = (current_price - historical_prices[-3]) / historical_prices[-3]
```

Mode A: MA deviation spike.

Fires when:

```text
deviation_abs >= 0.05
```

Direction:

```text
deviation > 0  -> BEARISH, fade high YES price
deviation < 0  -> BULLISH, fade low YES price
```

Strength:

```text
deviation_abs >= 0.12 -> VERY_STRONG
deviation_abs >= 0.08 -> STRONG
deviation_abs >= 0.05 -> MODERATE
else                  -> WEAK
```

Confidence:

```text
confidence = min(0.90, 0.50 + (deviation_abs - 0.05) * 3.0)
reject if confidence < 0.55
```

Mode B: velocity spike.

Fires only if MA deviation mode did not fire and:

```text
abs(velocity) >= 0.03
deviation_abs < 0.05 * 0.6
```

Direction:

```text
velocity > 0 -> BULLISH
velocity < 0 -> BEARISH
```

Velocity strength and confidence:

```text
vel_strength = abs(velocity) / 0.03

vel_strength >= 3 -> MODERATE, confidence 0.65
vel_strength >= 2 -> WEAK,     confidence 0.60
else              -> WEAK,     confidence 0.57
```

## Processor 2: SentimentAnalysis

Active constructor values:

```text
extreme_fear_threshold = 25
extreme_greed_threshold = 75
min_confidence = 0.50
source weight = 0.05
```

Input:

```text
sentiment_score = Fear & Greed score, 0 to 100
sentiment_classification = external label
```

Signal logic:

```text
sentiment_score <= 25 -> BULLISH, contrarian buy fear
sentiment_score >= 75 -> BEARISH, contrarian fade greed
25 < score < 45       -> BULLISH, WEAK, confidence 0.55
55 < score < 75       -> BEARISH, WEAK, confidence 0.55
45 <= score <= 55     -> no signal
```

Extreme fear:

```text
extremeness = (25 - score) / 25
extremeness >= 0.8 -> VERY_STRONG, confidence 0.85
extremeness >= 0.5 -> STRONG,      confidence 0.75
else               -> MODERATE,    confidence 0.65
```

Extreme greed:

```text
extremeness = (score - 75) / 25
extremeness >= 0.8 -> VERY_STRONG, confidence 0.85
extremeness >= 0.5 -> STRONG,      confidence 0.75
else               -> MODERATE,    confidence 0.65
```

## Processor 3: PriceDivergence

Active constructor values:

```text
divergence_threshold = 0.05, passed but unused by current processor logic
min_confidence = 0.55
momentum_threshold = 0.003
extreme_prob_threshold = 0.68
low_prob_threshold = 0.32
spot history length = 10 readings
source weight = 0.18
```

Inputs:

```text
poly_prob = current YES probability
spot_price = Coinbase BTC spot price
poly_momentum = context_momentum
```

The processor appends `spot_price` to its own rolling spot history. If at least
3 spot readings exist:

```text
spot_momentum = (latest_spot - spot_history[-3]) / spot_history[-3]
```

If spot price is absent, the processor can use `poly_momentum`. In the current
context path, Coinbase spot is required, so absence should be treated as a
context failure before this processor runs.

Mode A: extreme high YES probability fade.

Fires when:

```text
poly_prob >= 0.68
spot_momentum <= 0.001
```

Output:

```text
direction = BEARISH
extremeness = (poly_prob - 0.68) / (1.0 - 0.68)
confidence = min(0.80, 0.55 + extremeness * 0.25)
strength = STRONG if extremeness > 0.5 else MODERATE
```

Mode B: extreme low YES probability fade.

Fires when:

```text
poly_prob <= 0.32
spot_momentum >= -0.001
```

Output:

```text
direction = BULLISH
extremeness = (0.32 - poly_prob) / 0.32
confidence = min(0.80, 0.55 + extremeness * 0.25)
strength = STRONG if extremeness > 0.5 else MODERATE
```

Mode C: momentum mispricing.

Fires when:

```text
0.35 <= poly_prob <= 0.65
abs(spot_momentum) >= 0.003
```

Output:

```text
direction = BULLISH if spot_momentum > 0 else BEARISH
momentum_strength = abs(spot_momentum) / 0.003
confidence = min(0.78, 0.55 + min(momentum_strength - 1, 2) * 0.08)

momentum_strength >= 3 -> STRONG
momentum_strength >= 2 -> MODERATE
else                   -> WEAK
```

## Processor 4: OrderBookImbalance

Active constructor values:

```text
imbalance_threshold = 0.30
wall_threshold = 0.20
min_book_volume = 50.0
min_confidence = 0.55
top_levels = 10
source weight = 0.30
```

Input:

```text
yes_order_book = frozen decision-cycle CLOB book for YES token
```

Book volume calculation uses the top 10 levels:

```text
level_usd = price * size
bid_volume = sum(level_usd for bids)
ask_volume = sum(level_usd for asks)
total_volume = bid_volume + ask_volume
```

Reject/no signal when:

```text
total_volume < 50.0
abs(imbalance) < 0.30
```

Imbalance:

```text
imbalance = (bid_volume - ask_volume) / total_volume
imbalance > 0 -> BULLISH
imbalance < 0 -> BEARISH
```

Strength:

```text
abs(imbalance) >= 0.70 -> VERY_STRONG
abs(imbalance) >= 0.50 -> STRONG
abs(imbalance) >= 0.35 -> MODERATE
else                   -> WEAK
```

Confidence:

```text
confidence = min(0.85, 0.55 + abs(imbalance) * 0.40)
```

Wall bonus:

```text
wall exists when a single top-level order has level_usd / total_volume >= 0.20
if wall is on the signal side:
    confidence = min(0.90, confidence + 0.05)
```

## Processor 5: TickVelocity

Active constructor values:

```text
velocity_threshold_60s = 0.015
velocity_threshold_30s = 0.010
min_ticks = 5
min_confidence = 0.55
source weight = 0.25
```

Inputs:

```text
current_price = frozen YES mid
tick_buffer = frozen tick buffer with timezone-aware timestamps
decision_reference_time = frozen trigger tick time
```

The processor finds the tick closest to 60 seconds and 30 seconds before
`decision_reference_time`. A historical tick is usable only if it is within
15 seconds of the target time.

Velocity:

```text
vel_60s = (current_price - price_60s_ago) / price_60s_ago
vel_30s = (current_price - price_30s_ago) / price_30s_ago
```

Acceleration:

```text
vel_first_30s = vel_60s - vel_30s
acceleration = vel_30s - vel_first_30s
             = 2 * vel_30s - vel_60s
```

Primary velocity:

```text
primary_vel = vel_30s if available else vel_60s
threshold = 0.010 if vel_30s is available else 0.015
```

Reject/no signal when:

```text
len(tick_buffer) < 5
both 30s and 60s historical ticks unavailable
abs(primary_vel) < threshold
```

Direction:

```text
primary_vel > 0 -> BULLISH
primary_vel < 0 -> BEARISH
```

Strength:

```text
abs(primary_vel) >= 0.040 -> VERY_STRONG
abs(primary_vel) >= 0.025 -> STRONG
abs(primary_vel) >= 0.015 -> MODERATE
else                      -> WEAK
```

Confidence:

```text
confidence = min(0.82, 0.55 + (abs(primary_vel) / threshold - 1) * 0.12)
```

Acceleration bonus:

```text
same direction acceleration and abs(acceleration) > 0.005:
    confidence = min(0.88, confidence + 0.06)
```

Velocity reversal penalty:

```text
if vel_60s and vel_30s have opposite signs:
    confidence = confidence * 0.80
```

Reject if final confidence is below 0.55.

## Processor 6: DeribitPCR

Active constructor values:

```text
bullish_pcr_threshold = 1.20
bearish_pcr_threshold = 0.70
max_days_to_expiry = 2
min_open_interest = 100.0
cache_seconds = 300
min_confidence = 0.55
source weight = 0.10
```

External API:

```text
https://www.deribit.com/api/v2/public/get_book_summary_by_currency
params: currency=BTC, kind=option
```

PCR calculation:

```text
ignore option contracts with open_interest < 100
overall_pcr = total_put_open_interest / total_call_open_interest
short_pcr = short_dated_put_open_interest / short_dated_call_open_interest
pcr = short_pcr if present else overall_pcr
```

Short-dated means parsed days-to-expiry <= 2.

High PCR signal:

```text
pcr >= 1.20 -> BULLISH, contrarian fear
extremeness = (pcr - 1.20) / 1.20
confidence = min(0.80, 0.57 + extremeness * 0.15)

pcr >= 1.60 -> VERY_STRONG
pcr >= 1.40 -> STRONG
else        -> MODERATE
```

Low PCR signal:

```text
pcr <= 0.70 -> BEARISH, contrarian greed
extremeness = (0.70 - pcr) / 0.70
confidence = min(0.80, 0.57 + extremeness * 0.15)

pcr <= 0.45 -> VERY_STRONG
pcr <= 0.55 -> STRONG
else        -> MODERATE
```

Balanced PCR:

```text
0.70 < pcr < 1.20 -> no signal
```

## Trend Filter And Signal Confirmation

The trend filter uses the frozen YES mid price:

```text
YES price > 0.60 -> direction = long, buy YES
YES price < 0.40 -> direction = short, buy NO
0.40 <= YES price <= 0.60 -> reject as neutral
```

The trend price band recorded for analysis is more granular:

```text
YES >= 0.70       -> yes_extreme_ge_0.70
YES >= 0.60       -> yes_strong_0.60_0.70
YES >= 0.52       -> yes_moderate_0.52_0.60
YES > 0.48        -> neutral_0.48_0.52
YES > 0.40        -> no_moderate_0.40_0.48
YES > 0.30        -> no_strong_0.30_0.40
else              -> no_extreme_le_0.30
```

If `REQUIRE_SIGNAL_CONFIRMATION=true`, which is the example config:

```text
trend long  requires fused_direction == bullish
trend short requires fused_direction == bearish
fused_confidence >= MIN_SIGNAL_CONFIDENCE
```

Example `MIN_SIGNAL_CONFIDENCE` is `0.70`.

## EV And Depth Gate

After trend confirmation, the bot picks the executable side:

```text
long  -> buy YES at YES ask, use yes_order_book
short -> buy NO at NO ask, use no_order_book
```

For both order types, depth-aware executable entry is computed from the frozen
book snapshot. The EV gate is:

```text
min_required_confidence = executable_entry + EV_FEE_BUFFER + EV_SPREAD_BUFFER
pass when fused_confidence >= min_required_confidence
```

Example config:

```text
EV_FEE_BUFFER = 0.005
EV_SPREAD_BUFFER = 0.01
```

This is a heuristic confidence filter, not a calibrated expected value model.

### Market IOC Depth

For `ORDER_TYPE=market_ioc`, the estimator walks ask levels until the USD budget
is spent:

```text
level_usd_capacity = price * size_tokens
vwap = total_cost / total_tokens
fully_filled = remaining_usd <= 0
```

The trade is rejected if the book cannot fill the full USD budget.

### Limit IOC Depth

For `ORDER_TYPE=limit_ioc`:

```text
accepted_limit_price = fused_confidence - LIMIT_REQUIRED_EDGE
submitted_limit_price = accepted_limit_price rounded down to venue precision
limit_order_token_qty = floor_to_size_precision(budget_usd / submitted_limit_price)
```

Reject when:

```text
accepted_limit_price not in (0, 1)
limit_order_token_qty < 5 tokens
no executable liquidity at submitted_limit_price
LIMIT_IOC_FILL_POLICY != partial_ok
```

Example config:

```text
LIMIT_REQUIRED_EDGE = 0.05
LIMIT_IOC_FILL_POLICY = partial_ok
```

`all_or_nothing` is currently rejected because the wire behavior is IOC/FAK, not
verified FOK.

## Live-Only Freshness, Risk, And Order Guards

Live mode adds these fail-closed gates:

```text
no unresolved settlement reconciliation
decision snapshot age <= MAX_DECISION_SNAPSHOT_AGE_SECONDS
fresh AccountState age <= MAX_ACCOUNT_STATE_AGE_SECONDS
free collateral >= resolved_trade_usd + BALANCE_SAFETY_BUFFER_USD
risk_engine.validate_new_position passes
YES/NO executable ask > 0.02
active market condition_id still matches decision snapshot
active token id still matches decision snapshot
instrument condition_id/token_id match decision snapshot
market has not expired before submit and before intent persistence
runtime ORDER_TYPE still matches decision ORDER_TYPE
runtime sizing still matches decision sizing
```

Example live freshness and sizing config:

```text
MAX_DECISION_SNAPSHOT_AGE_SECONDS = 10
MAX_ACCOUNT_STATE_AGE_SECONDS = 30
BALANCE_SAFETY_BUFFER_USD = 0.00
SIZING_MODE = fixed
MARKET_BUY_USD = 5.51
```

Risk engine environment variables:

```text
MAX_POSITION_SIZE
MAX_TOTAL_EXPOSURE
MAX_POSITIONS
MAX_DRAWDOWN_PCT
MAX_LOSS_PER_DAY
```

Risk engine rejects a new position if size, position count, total exposure,
daily loss, or drawdown limits are breached.

## Tunable Variables

These are strategy variables that can be brute-forced in a replay harness.
Some are currently code-owned constants and require code changes to adjust in
the live bot; a separate analyzer can still vary them offline.

### Timing

```text
live trade window start/end: 780, 840
shadow windows: 06_09, 09_11, 11_13, 14_15_late
market interval length: 900 seconds
QUOTE_STABILITY_REQUIRED
MAX_DECISION_SNAPSHOT_AGE_SECONDS
```

### Fusion

```text
source weights:
  OrderBookImbalance
  TickVelocity
  PriceDivergence
  SpikeDetection
  DeribitPCR
  SentimentAnalysis
min_signals
min_score
signal recency limit: 5 minutes
tie behavior
```

### Signal Confirmation And Trend

```text
TREND_UP_THRESHOLD = 0.60
TREND_DOWN_THRESHOLD = 0.40
REQUIRE_SIGNAL_CONFIRMATION
MIN_SIGNAL_CONFIDENCE
trend price bands
```

### SpikeDetection

```text
spike_threshold
lookback_periods
min_confidence
velocity_threshold
deviation strength cutoffs
deviation confidence formula
velocity strength cutoffs
velocity confidence constants
```

### SentimentAnalysis

```text
extreme_fear_threshold
extreme_greed_threshold
moderate fear/greed boundaries: 45 and 55
min_confidence
extreme confidence levels: 0.65, 0.75, 0.85
weak confidence: 0.55
```

### PriceDivergence

```text
min_confidence
momentum_threshold
extreme_prob_threshold
low_prob_threshold
spot history length
momentum confirmation cutoffs: +0.001, -0.001
momentum mispricing probability band: 0.35 to 0.65
confidence caps: 0.80 and 0.78
```

### OrderBookImbalance

```text
imbalance_threshold
wall_threshold
min_book_volume
min_confidence
top_levels
strength cutoffs: 0.35, 0.50, 0.70
confidence formula and wall bonus
```

### TickVelocity

```text
velocity_threshold_60s
velocity_threshold_30s
min_ticks
min_confidence
target windows: 60s and 30s
target tolerance: 15s
strength cutoffs: 0.015, 0.025, 0.040
confidence formula
acceleration bonus threshold: 0.005
velocity reversal penalty: 0.80
```

### DeribitPCR

```text
bullish_pcr_threshold
bearish_pcr_threshold
max_days_to_expiry
min_open_interest
cache_seconds
min_confidence
strength cutoffs: 1.40, 1.60, 0.55, 0.45
confidence formula and cap
```

### Order And EV

```text
ORDER_TYPE
EV_FEE_BUFFER
EV_SPREAD_BUFFER
LIMIT_REQUIRED_EDGE
LIMIT_IOC_FILL_POLICY
POLYMARKET_LIMIT_MIN_TOKENS = 5
MIN_LIQUIDITY = 0.02
depth fill requirements
```

### Sizing And Risk

```text
SIZING_MODE
MARKET_BUY_USD
PCT_OF_FREE_COLLATERAL_PER_TRADE
MAX_POSITION_SIZE
MAX_TOTAL_EXPOSURE
MAX_POSITIONS
MAX_DRAWDOWN_PCT
MAX_LOSS_PER_DAY
BALANCE_SAFETY_BUFFER_USD
MAX_ACCOUNT_STATE_AGE_SECONDS
```

## External Observations To Treat As Fixed Facts

These should be collected as raw inputs and not optimized directly:

```text
Polymarket market slug, condition_id, token ids, start/end times
YES/NO quote ticks: bid, ask, mid, timestamp
frozen price_history at decision time
frozen tick_buffer at decision time
YES and NO CLOB order books at decision time
Fear & Greed score and classification
Coinbase BTC spot price
Deribit option summaries or computed PCR fields
AccountState free collateral and timestamp
actual order fills, fees, settlement result, redemption result
```

The analyzer can optimize how the bot reacts to these facts, but it should not
rewrite the facts themselves.

## Raw Dataset Needed For Brute Force

For each candidate decision point, collect:

```text
decision_id
decision_reference_time
market slug and condition_id
market_start_timestamp
sub_interval
seconds_into_sub_interval
trade_window_label
YES bid, ask, mid
NO bid, ask
full or sufficient price_history
full tick_buffer with timestamps
context_sma20_deviation
context_momentum
context_volatility
YES order book snapshot
NO order book snapshot
Fear & Greed score/classification
Coinbase spot price
Deribit PCR data used, including cache timestamp if applicable
all generated signal metadata
fused bullish/bearish contributions
fused score and confidence
trend direction and trend band
EV/depth estimate fields
rejection gate or submitted order details
market outcome: did YES settle true or false
if live-equivalent analysis: actual fill, fee, settlement, PnL, ledger effects
```

For brute-force reliability, store enough raw data to recompute signals from
scratch. Storing only the final generated signal is not enough, because changing
thresholds changes whether a signal exists at all.

## Current Example Config Values

From `.env.example`:

```text
ORDER_TYPE=limit_ioc
QUOTE_STABILITY_REQUIRED=3
LIMIT_REQUIRED_EDGE=0.05
LIMIT_IOC_FILL_POLICY=partial_ok
SIZING_MODE=fixed
MARKET_BUY_USD=5.51
MAX_POSITION_SIZE=5.51
MAX_TOTAL_EXPOSURE=22.04
MAX_POSITIONS=4
MAX_DRAWDOWN_PCT=0.15
MAX_LOSS_PER_DAY=11.02
MAX_ACCOUNT_STATE_AGE_SECONDS=30
MAX_DECISION_SNAPSHOT_AGE_SECONDS=10
BALANCE_SAFETY_BUFFER_USD=0.00
REQUIRE_SIGNAL_CONFIRMATION=true
MIN_SIGNAL_CONFIDENCE=0.70
EV_FEE_BUFFER=0.005
EV_SPREAD_BUFFER=0.01
```

## Source Map

Primary source files:

```text
bot.py
decision_context.py
decision_snapshot.py
depth_estimator.py
execution/risk_engine.py
core/strategy_brain/fusion_engine/signal_fusion.py
core/strategy_brain/signal_processors/base_processor.py
core/strategy_brain/signal_processors/spike_detector.py
core/strategy_brain/signal_processors/sentiment_processor.py
core/strategy_brain/signal_processors/divergence_processor.py
core/strategy_brain/signal_processors/orderbook_processor.py
core/strategy_brain/signal_processors/tick_velocity_processor.py
core/strategy_brain/signal_processors/deribit_pcr_processor.py
```
