# 🤖 Polymarket BTC 15-Minute Trading Bot

[![Python 3.14+](https://img.shields.io/badge/python-3.14+-blue.svg)](https://www.python.org/downloads/)
[![NautilusTrader](https://img.shields.io/badge/nautilus-1.222.0-green.svg)](https://nautilustrader.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Polymarket](https://img.shields.io/badge/Polymarket-CLOB-purple)](https://polymarket.com)
[![Redis](https://img.shields.io/badge/Redis-powered-red.svg)](https://redis.io/)
[![Grafana](https://img.shields.io/badge/Grafana-dashboard-orange)](https://grafana.com/)

A production-grade algorithmic trading bot for **Polymarket's 15-minute BTC price prediction markets**. Built with a 7-phase architecture combining multiple signal sources, professional risk management, and self-learning capabilities.


---

## 📋 **Table of Contents**
- [Features](#features)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Running the Bot](#running-the-bot)
- [Monitoring](#monitoring)
- [Trading Modes](#trading-modes)
- [Project Structure](#project-structure)
- [Testing](#testing)
- [Contributing](#contributing)
- [FAQ](#faq)
- [License](#license)
- [Disclaimer](#disclaimer)

---

## ✨ **Features**

| Feature | Description |
|---------|-------------|
| **7-Phase Architecture** | Modular, testable, production-ready design |
| **Multi-Signal Intelligence** | Spike Detection, Sentiment Analysis, Price Divergence |
| **Risk-First Design** | $1 max per trade, 30% stop loss, 20% take profit |
| **Explicit Mode Operation** | Simulation observes decisions; live startup requires Redis control |
| **Real-Time Monitoring** | Grafana dashboards + Prometheus metrics |
| **Self-Learning** | Automatically optimizes signal weights based on performance |
| **Auto-Recovery** | WebSocket auto-reconnection, rate limiting, data validation |
| **Decision-Only Simulation** | Records candidate decisions only; no live-equivalent fills or P&L |

---

## 🏗️ **Architecture**

### **7-Phase Overview**

```mermaid
 flowchart LR
    subgraph Input[INPUT]
        D[External Data<br/>Coinbase, Binance, News, Solana]
    end
    
    subgraph Process[PROCESSING]
        I[Ingestion<br/>Unify & Validate]
        N[Nautilus Core<br/>Trading Framework]
        S[Signal Processors<br/>Spike, Sentiment, Divergence]
        F[Fusion Engine<br/>Weighted Voting]
    end
    
    subgraph Output[OUTPUT]
        R[Risk Management<br/>$1 Max, Stop Loss]
        E[Execution<br/>Polymarket Orders]
        M[Monitoring<br/>Grafana Dashboard]
        L[Learning<br/>Weight Optimization]
    end
    
    D --> I --> N --> S --> F --> R --> E --> M --> L
    L -.-> F
```
## Prerequisites
- Python 3.14+ (Download)

- Redis (Download) - for mode switching

- Polymarket Account with API credentials
- Git

## 🚀 Quick Start

## 1. Clone the Repository

```bash
git clone https://github.com/yourusername/polymarket-btc-15m-bot.git
cd polymarket-btc-15m-bot
```
## 2. Set Up Virtual Environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python -m venv venv
source venv/bin/activate
```
## 3. Install Dependencies

```
bash
pip install -r requirements.txt
```
## 4. Configure Environment Variables

Create or edit `.env` with your credentials:

```env
# Polymarket API Credentials
POLYMARKET_PK=your_private_key_here
POLYMARKET_FUNDER=your_deposit_or_wallet_address_here
POLYMARKET_SIGNATURE_TYPE=3
POLYMARKET_API_KEY=wallet_derived_api_key_here
POLYMARKET_API_SECRET=wallet_derived_api_secret_here
POLYMARKET_PASSPHRASE=wallet_derived_passphrase_here

# Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=2

# Trading Parameters
MARKET_BUY_USD=1.00
MAX_POSITION_SIZE=1.0
MAX_TOTAL_EXPOSURE=10.0
MAX_POSITIONS=5
MAX_DRAWDOWN_PCT=0.15
MAX_LOSS_PER_DAY=5.0
REQUIRE_SIGNAL_CONFIRMATION=true
MIN_SIGNAL_CONFIDENCE=0.70
EV_FEE_BUFFER=0.005
EV_SPREAD_BUFFER=0.01
LIVE_SETTLEMENT_GRACE_SECONDS=3600
REQUIRE_AUTO_REDEEM_TOKEN_HINT=true
LIVE_TRADE_LEDGER_PATH=live_trades.json
POLYGON_RPC_URL=https://your-polygon-rpc.example
STOP_LOSS_PCT=0.30
TAKE_PROFIT_PCT=0.20
SPIKE_THRESHOLD=0.15
DIVERGENCE_THRESHOLD=0.05
```

Boolean flags accept only explicit values: `true`, `false`, `1`, `0`, `yes`,
`no`, `on`, or `off`. Invalid values abort instead of being silently coerced.

`live_trades.json` stores filled live trades until Polymarket sends `auto_redeem`.
If no payout event arrives after `LIVE_SETTLEMENT_GRACE_SECONDS`, the bot marks
the trade as `SETTLEMENT_UNKNOWN` instead of fabricating a $0 loss. A delayed
`auto_redeem` can still correct the record and update realized P&L. Live submit
requires valid market settlement metadata; missing `market_end_time` is rejected
instead of using a secondary timeout. Live trading pauses while any
`SETTLEMENT_UNKNOWN` record still has `needs_reconciliation=true`.

Manual reconciliation is explicit and auditable. Stop the bot first, verify the
actual settlement externally, resolve exactly one unknown order, then restart the
bot so it reloads the ledger:

```bash
python mark_settlement_resolved.py \
  --order-id ORDER_ID \
  --payout 0 \
  --reason "Verified no redeemable payout in Polymarket UI on 2026-05-18"
```

The tool takes the same `live_trades.json.lock` used by the bot and refuses to
run while the bot is running. It resolves the ledger path from
`LIVE_TRADE_LEDGER_PATH` by default and prints the exact resolved ledger and lock
paths before writing. It changes `settlement_source` to `manual_reconciliation`,
records the previous unknown state, computes P&L from the verified payout, and
clears `needs_reconciliation`. It does not add an automatic override or hidden
resume path.

If an old open trade is stuck and cannot be timed out because its metadata is
incomplete, stop the bot and move exactly one open order into
`SETTLEMENT_UNKNOWN` before reconciling it:

```bash
python mark_settlement_resolved.py \
  --migrate-open-to-unknown ORDER_ID \
  --confirm-open-migration \
  --reason "Open trade missing market_end_time after schema migration"
```

If `live_trades.json` is corrupt or unreadable, startup fails closed. Repair the
JSON manually from a known-good copy before starting live mode. Keep external
snapshots of this file, for example with a cron job that copies it to a dated
backup path outside the repo.

If the live ledger blocks during a fill callback, later fill callbacks are
ignored until the process is repaired and restarted. This prevents the bot from
committing a partial local view as if it were complete. During recovery, verify
the order state from Polymarket exchange/order records; do not rely only on
`live_trades.json`. If a filled order is missing from the ledger entirely,
create a `SETTLEMENT_UNKNOWN` record from the external order details, then
resolve it with the verified payout:

```bash
python mark_settlement_resolved.py \
  --create-unknown-from-external-order ORDER_ID \
  --confirm-external-order \
  --external-size 2.00 \
  --external-entry-price 0.50 \
  --external-filled-qty 4 \
  --external-direction long \
  --external-trade-label "YES (UP)" \
  --external-instrument-id "cond-token.POLYMARKET" \
  --external-token-id TOKEN_ID \
  --external-slug MARKET_SLUG \
  --external-condition-id CONDITION_ID \
  --external-submitted-at 2026-05-18T12:00:00Z \
  --external-filled-at 2026-05-18T12:00:02Z \
  --external-market-end-time 2026-05-18T12:15:00Z \
  --reason "Rebuilt from Polymarket order records after ledger write failure"
```

The reconstruction command rejects inconsistent fill math unless
`--external-size` matches `--external-entry-price * --external-filled-qty`
within the smaller of $0.01 or 0.5%, rejects entry prices outside
`0 < price <= 1`, and rejects impossible ordering unless
`submitted_at <= filled_at <= market_end_time`. After creating the unknown
record, resolve it with the verified payout using `--order-id ORDER_ID --payout
PAYOUT --reason "..."`.

`REQUIRE_AUTO_REDEEM_TOKEN_HINT=true` avoids assigning wallet-level redeem
payouts to bot trades when the event does not identify the token or outcome.
Those events are kept in the ledger as pending retry/reconciliation items, with
a 7-day retention limit and 500-event cap. Before relying on live settlement,
capture and inspect at least one real Polymarket `auto_redeem` log line and
confirm the payload includes one of the supported token/outcome fields:
`asset_id`, `assetId`, `token_id`, `tokenId`, `clobTokenId`, `clob_token_id`,
`outcome`, `redeemed_outcome`, `redeemedOutcome`, `winning_outcome`, or
`winningOutcome`. A `side` field is accepted only when its value is a normalized
outcome (`yes`, `up`, `no`, or `down`); execution-side values such as `BUY` do
not unlock settlement matching.

Also verify that the real `auto_redeem` payload includes a parseable `timestamp`
in seconds or milliseconds. Missing or invalid timestamps are left pending for
manual review; the bot does not fabricate settlement time.

`EV_FEE_BUFFER` and `EV_SPREAD_BUFFER` are heuristic confidence buffers. The
current fused signal confidence is not a calibrated settlement probability, so
these values filter low-confidence entries but are not a true mathematical EV
model.

Simulation records decision observations only. These records do not go through
live-equivalent order submission, fill tracking, settlement ledger writes,
`auto_redeem`, position/P&L accounting, or live failure behavior. They remain
`PENDING` and must not be used as a win-rate or P&L source until a separate
live-equivalent paper execution engine is implemented.

For Polymarket's current deposit-wallet flow, `POLYMARKET_PK` is the private key for the signer wallet and `POLYMARKET_FUNDER` is the Polymarket deposit wallet address. Do not guess the funder address from MetaMask; discover it from Polymarket:

```bash
venv/bin/python configure_polymarket_deposit_wallet.py
```

Then derive and record the wallet-derived CLOB API credentials:

```bash
venv/bin/python derive_polymarket_api_creds.py
```

Do not use Builder or Relayer API keys for `POLYMARKET_API_KEY`; the bot expects wallet-derived CLOB API credentials.

Check the CLOB balance seen by the bot. This is the number the live trading adapter will use:

```bash
venv/bin/python check_polymarket_balance.py --sync
```

For older direct MetaMask/EOA trading only, set `POLYMARKET_FUNDER` to the same public address shown in MetaMask and `POLYMARKET_SIGNATURE_TYPE=0`. If that wallet has Polygon USDC.e but CLOB allowances are `0`, approve the spender contracts from the MetaMask EOA wallet:

```bash
POLYGON_RPC_URL=https://your-polygon-rpc.example venv/bin/python approve_polymarket_clob.py
POLYGON_RPC_URL=https://your-polygon-rpc.example venv/bin/python approve_polymarket_clob.py --execute
venv/bin/python check_polymarket_balance.py --sync
```
## 5. Start Redis
```
bash
# Windows (download from redis.io)
redis-server

# macOS
brew install redis
redis-server

# Linux
sudo apt install redis-server
redis-server
```
## 6. Run the Bot
```
bash
# Test mode (decision observations every minute - for quick signal checks)
python bot.py --test-mode

# Live trading mode (REAL MONEY!)
python 15m_bot_runner.py --live
```
## ⚙️ Configuration Options
Argument	Description	Default
--test-mode	Decision observations every minute	False
--live	Enable live trading (real money)	False
--no-grafana	Disable Grafana metrics	False
## View Decision Observations
```
bash
python view_paper_trades.py
```
## Trading Modes
Switch Modes Without Restarting (Redis)

# Switch to simulation mode (safe)
```
python redis_control.py sim -- not stable yet
```
# Switch to live trading mode (REAL MONEY!)
```
python redis_control.py live --not stable yet
``` 
## 📁 Project Structure

```text
polymarket-btc-15m-bot/
├── core/                        # Core business logic
│   ├── ingestion/               # Phase 2: Data ingestion
│   │   ├── adapters/            # Unified adapter interface
│   │   ├── managers/            # Rate limiter, WebSocket manager, etc.
│   │   └── validators/          # Data validation & schema checks
│   ├── nautilus_core/           # Phase 3: NautilusTrader integration
│   │   ├── data_engine/         # Nautilus data engine wrapper
│   │   ├── event_dispatcher/    # Event handling & dispatching
│   │   ├── instruments/         # BTC/USDT instrument definitions
│   │   └── providers/           # Custom live/historical data providers
│   └── strategy_brain/          # Phase 4: Signal generation & processing
│       ├── fusion_engine/       # Multi-signal combination logic
│       ├── signal_processors/   # Individual detectors (spike, divergence, sentiment…)
│       └── strategies/          # Main 15-minute BTC trading strategy
│
├── data_sources/                # Phase 1: External market & sentiment data
│   ├── binance/                 # Binance WebSocket client
│   ├── coinbase/                # Coinbase REST API client
│   ├── news_social/             # Fear & Greed Index + social sentiment
│   └── solana/                  # Solana RPC (optional / experimental)
│
├── execution/                   # Phase 5: Order placement & risk control
│   ├── execution_engine.py      # Main order execution coordinator
│   ├── polymarket_client.py     # Polymarket API wrapper & order logic
│   └── risk_engine.py           # Position sizing, SL/TP, exposure limits
│
├── monitoring/                  # Phase 6: Performance tracking & metrics
│   ├── grafana_exporter.py      # Prometheus metrics exporter
│   └── performance_tracker.py   # Trade logging & statistics
│
├── feedback/                    # Phase 7: Future learning / optimization
│   └── learning_engine.py       # Placeholder for ML feedback loop
│
├── grafana/                     # Grafana dashboard & configuration
│   ├── dashboard.json           # Pre-built dashboard definition
│   ├── grafana.ini              # Grafana server config (optional)
│   └── import_dashboard.py      # Script to import dashboard automatically
│
├── scripts/                     # Development & testing utilities
│   ├── test_data_sources.py
│   ├── test_ingestion.py
│   ├── test_nautilus.py
│   ├── test_strategy.py
│   └── test_execution.py
│
├── .env.example                 # Template for environment variables
├── .gitignore
├── patch_gamma_markets.py       # Temporary patch/fix for Polymarket API
├── redis_control.py             # Switch trading mode (sim/live/test)
├── requirements.txt             # Python dependencies
├── bot.py                       # Main bot entry point
├── 15m_bot_runner.py            # Auto-restart wrapper for bot.py
├── view_paper_trades.py         # View simulation decision observations
└── README.md                    # This file
```
Testing
Run tests for each phase independently:

# Test individual phases
```
python scripts/test_data_sources.py
python scripts/test_ingestion.py
python scripts/test_nautilus.py
python scripts/test_strategy.py
python scripts/test_execution.py
```
🤝 Contributing
Contributions are welcome! Here's how you can help:

 - Fork the repository

 - Create a feature branch: git checkout -b feature

 -Commit your changes: git commit -m 'Added feature'

- Push to the branch: git push origin feature/added-feature

Open a Pull Request

## Ideas for Contributions
- Add derivatives data (funding rates, open interest)

- Implement more signal processors

- Add Telegram/Discord alerts

- Create web UI for management


- Support for ETH/SOL markets

- Machine learning optimization

## ❓ FAQ

**Q: How much money do I need to start?**  
**A:** The bot caps each trade at $1, so you can start with as little as $10–20.

**Q: Is this profitable?**  
**A:** No claim is made from simulation records. Current simulation is
decision-only observation, not live-equivalent execution or settlement.

**Q: Do I need programming experience?**  
**A:** Basic Python knowledge is helpful (e.g. understanding how to run scripts and edit config files), but the bot is designed to run with just a few simple commands — no coding required for normal use.

**Q: Can I run this 24/7?**  
**A:** Yes! The bot is built for continuous operation and includes basic auto-recovery features in case of temporary connection issues.

**Q: What's the difference between test mode and normal mode?**  
**A:**  
- **Test mode** — decision observations every minute (great for quick signal/debug checks)
- **Normal mode** — trades every 15 minutes (matches the intended 15-minute strategy timeframe)

 
## Disclaimer
TRADING CRYPTOCURRENCIES CARRIES SIGNIFICANT RISK.

This bot is for educational purposes

Past performance does not guarantee future results

Always understand the risks before trading with real money

The developers are not responsible for any financial losses

Use simulation only to observe decisions; use small live size only after checking live settlement behavior.

## Acknowledgments
NautilusTrader - Professional trading framework

Polymarket - Prediction market platform


All contributors and users of this project

## Contact & Community
GitHub Issues: For bugs and feature requests

Twitter: @Kator07

##Discord: Join our community
- https://discord.gg/tafKjBnPEQ

## ⭐ Show Your Support
If you find this project useful, please star the GitHub repo! It helps others discover it.

## contact me on telegram 
 [![Telegram](https://img.shields.io/badge/Telegram-%230088cc.svg?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/Bigg_O7)
