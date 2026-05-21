# Phase 8 — Linux deployment

These files are the operator-runnable templates referenced in
[../EXECUTION_PLAN.md](../EXECUTION_PLAN.md) Phase 8. They are NOT applied
automatically — the operator copies them into place on the target server.

## File layout on the server

```
/opt/polybot/
├── venv/                                       # Python venv (chmod 750 polybot:polybot)
├── Polymarket-BTC-15-Minute-Trading-Bot/       # git clone, owner polybot
│   ├── bot.py
│   ├── mark_settlement_resolved.py
│   ├── ...
│   ├── credentials/
│   │   └── encrypted_credentials.json          # encrypted live vault, mode 0600
│   └── ...                                    # .env contains non-secret runtime settings only
├── ledger/
│   ├── live_trades.json                        # mode 0640 polybot:polybot
│   ├── live_trades.json.lock                   # fcntl lock file
│   ├── decisions.jsonl                         # Phase 2.4 observation log
│   └── archive/                                # rotated decisions.jsonl
└── logs/
    ├── bot.log                                 # logrotated
    └── nautilus/                               # Nautilus TradingNode logs
```

The ledger directory **MUST** be on the same filesystem as the temp-file write
target so `os.replace` is atomic. Do not put `live_trades.json` on NFS or a
separate mount.

## One-shot install

```bash
# As root
useradd --system --home /opt/polybot --shell /usr/sbin/nologin polybot
install -d -o polybot -g polybot -m 750 /opt/polybot
install -d -o polybot -g polybot -m 750 /opt/polybot/ledger /opt/polybot/logs

# As polybot
sudo -u polybot bash -c '
  cd /opt/polybot
  git clone <your-fork-url> Polymarket-BTC-15-Minute-Trading-Bot
  cd Polymarket-BTC-15-Minute-Trading-Bot
  python3 -m venv ../venv
  ../venv/bin/pip install --upgrade pip
  ../venv/bin/pip install -r requirements.txt
  cp .env.example .env
  chmod 600 .env
  $EDITOR .env
'

# Create encrypted live credentials (do NOT commit; mode 0600)
sudo -u polybot bash -c '
  cd /opt/polybot/Polymarket-BTC-15-Minute-Trading-Bot
  /opt/polybot/venv/bin/python setup_vault.py
'

# Install systemd unit + logrotate + backup cron
sudo cp deploy/polybot.service /etc/systemd/system/polybot.service
sudo cp deploy/polybot.logrotate /etc/logrotate.d/polybot
sudo cp deploy/polybot-ledger-backup.cron /etc/cron.d/polybot-ledger-backup
sudo systemctl daemon-reload
sudo systemctl enable polybot
```

## Pre-flight before first `systemctl start`

1. **Verify ledger schema.** Either start with no `live_trades.json`, OR
   ensure the existing file is already exact schema v3 (Phase 0.1 contract).
   No application code migrates old shapes — startup fails closed.
2. **Verify Polymarket free collateral.**
   `sudo -u polybot /opt/polybot/venv/bin/python /opt/polybot/Polymarket-BTC-15-Minute-Trading-Bot/check_polymarket_balance.py --sync`
3. **Phase 0.7 manual recovery.** If the bot was previously running and lost
   any fills, follow EXECUTION_PLAN.md Phase 0.7 to reconcile via
   `mark_settlement_resolved.py` BEFORE starting the service. The
   service will refuse to place new orders while any unresolved ledger
   state exists.
4. **Verify credentials vault.**
   `/opt/polybot/Polymarket-BTC-15-Minute-Trading-Bot/credentials/encrypted_credentials.json`
   exists and has mode `0600 polybot:polybot`.
5. **Verify env values.** `/opt/polybot/Polymarket-BTC-15-Minute-Trading-Bot/.env`
   contains only non-secret runtime settings. `bot.py` loads it after checking
   that it does not contain `POLYMARKET_*` or `POLYGON_RPC_URL`. `SIZING_MODE`,
   `MAX_ACCOUNT_STATE_AGE_SECONDS`,
   `MAX_DECISION_SNAPSHOT_AGE_SECONDS`, `BALANCE_SAFETY_BUFFER_USD`, and
   `NAUTILUS_LOG_DIR` must be explicit. For fixed sizing,
   `MARKET_BUY_USD > 5.50` strictly. `--confirm-live` is set in the unit file's
   `ExecStart` so the bot doesn't prompt for `LIVE` at startup.

## Operate

Start the password agent first in one root shell:

```bash
sudo systemd-tty-ask-password-agent --watch
```

Then start and monitor the service from another root shell:

```bash
sudo systemctl start polybot
sudo journalctl -u polybot -f          # watch startup; verify no SettlementLedgerError
sudo systemctl status polybot          # should be 'active (running)'

# Stop cleanly
sudo systemctl stop polybot            # SIGTERM; on_stop releases the fcntl lock

# Inspect ledger / decisions
sudo -u polybot jq . /opt/polybot/ledger/live_trades.json
sudo -u polybot tail -f /opt/polybot/ledger/decisions.jsonl
```

The unit emits the password request from its root-owned launcher, then drops to
the `polybot` user with `runuser` for the Python process. The service sandbox
allow-lists `/run/systemd/ask-password` so the password query can be published
while `ProtectSystem=strict` is active.

## Monitoring & alerting (P1)

Set up at minimum:

- **Process up.** Alert if `polybot.service` enters a `failed` state for
  >5 minutes.
- **Live-trading paused.** Every 5 minutes, run a `jq` query against
  `live_trades.json` and alert if any of these are true:
  - A `settled` record has `settlement_source == "SETTLEMENT_UNKNOWN"` or
    `needs_reconciliation == true`.
  - `pending_actual_fills` is non-empty.
  - `submitted_order_intents` has any entry whose `status` is not one of the
    approved terminal no-fill statuses.
  The bot also surfaces these in its process log; if your monitoring scrapes
  journalctl, alert on `LIVE TRADING PAUSED` lines.
- **Daily P&L.** Tail recent settled trades each day and report realized P&L.
- **Disk space on the ledger filesystem.** Standard `node_exporter`
  filesystem alert.

The bot exposes Prometheus metrics on port 8000 (see `grafana_exporter.py`).
Wire that into your Grafana instance if available.

## Security checklist

- `credentials/encrypted_credentials.json` mode `0600 polybot:polybot`.
  Never commit real credentials or the vault password.
- Repo-root `.env` files must contain non-secret runtime settings only.
- Bot user has no shell (`/usr/sbin/nologin`). Operator interacts via
  `systemctl`, `journalctl`, and `mark_settlement_resolved.py` over SSH only.
- Firewall: outbound to `clob.polymarket.com:443`,
  `gamma-api.polymarket.com:443`, `data-api.polymarket.com:443`, Polygon RPC
  endpoint, Redis (if remote). No inbound except SSH (operator) and Grafana
  port if remote-monitored.
- The `mark_settlement_resolved.py` tool requires shell access to the
  server. Restrict SSH accordingly.

## Encrypted Credentials Vault

Live mode loads Polymarket credentials from:

```text
credentials/encrypted_credentials.json
```

Create it with:

```bash
python setup_vault.py
```

The vault stores `POLYMARKET_PK`, `POLYMARKET_FUNDER`,
`POLYMARKET_SIGNATURE_TYPE`, wallet-derived CLOB API credentials, and
`POLYGON_RPC_URL`. Non-secret runtime settings remain in `.env`, the systemd
unit, or the operator shell. Live startup rejects `POLYMARKET_*` keys and
`POLYGON_RPC_URL` in both `.env` and the inherited process environment.
During setup, choose `create` for a new Polymarket CLOB API credential set or
`derive` for an existing wallet-backed credential set.

The shipped systemd unit asks for the vault password via
`systemd-ask-password` before running the bot as `polybot`. When starting the
service over SSH, keep a root shell open with:

```bash
sudo systemd-tty-ask-password-agent --watch
```

## What this phase does NOT include

- Container orchestration (Kubernetes / Nomad). Out of scope. The bot is a
  long-running single process; a single systemd service on one VM is
  sufficient for the operator's described scale.
- Blue-green or canary deploys. Same reason — single process.
- Database. The ledger is JSON on local disk. No SQLite, no Postgres. Adding
  a database is a future enhancement, not a deployment requirement.
- High availability. The bot is single-instance; fcntl locking prevents two
  instances from running simultaneously. HA would be a redesign.
