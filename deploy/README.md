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
│   └── .env                                    # mode 0600 polybot:polybot (or use SOPS, see below)
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
'

# Place credentials (do NOT commit; mode 0600)
sudo -u polybot install -m 0600 /path/to/your/real/.env \
  /opt/polybot/Polymarket-BTC-15-Minute-Trading-Bot/.env

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
4. **Verify env values.** `MARKET_BUY_USD > 5.50` strictly. `--confirm-live`
   is set in the unit file's `ExecStart` so the bot doesn't prompt for `LIVE`
   at startup.

## Operate

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

- `.env` (or `.env.sops.yaml`) mode `0600 polybot:polybot`. Never commit
  real credentials.
- Bot user has no shell (`/usr/sbin/nologin`). Operator interacts via
  `systemctl`, `journalctl`, and `mark_settlement_resolved.py` over SSH only.
- Firewall: outbound to `clob.polymarket.com:443`,
  `gamma-api.polymarket.com:443`, `data-api.polymarket.com:443`, Polygon RPC
  endpoint, Redis (if remote). No inbound except SSH (operator) and Grafana
  port if remote-monitored.
- The `mark_settlement_resolved.py` tool requires shell access to the
  server. Restrict SSH accordingly.

## SOPS (Phase 6, when adopted)

If the team adopts SOPS for credential management:

1. Encrypt `.env` as `/opt/polybot/secrets/.env.sops.yaml` with your team's
   key (age, gcpkms, awskms, etc. — see `sops --help`).
2. Edit `/etc/systemd/system/polybot.service`: comment out the
   `EnvironmentFile=` line and uncomment the `sops exec-env` ExecStart line.
3. Reload and restart: `sudo systemctl daemon-reload && sudo systemctl restart polybot`.

See `deploy/.env.sops.yaml.example` for the encrypted-credentials shape.

## What this phase does NOT include

- Container orchestration (Kubernetes / Nomad). Out of scope. The bot is a
  long-running single process; a single systemd service on one VM is
  sufficient for the operator's described scale.
- Blue-green or canary deploys. Same reason — single process.
- Database. The ledger is JSON on local disk. No SQLite, no Postgres. Adding
  a database is a future enhancement, not a deployment requirement.
- High availability. The bot is single-instance; fcntl locking prevents two
  instances from running simultaneously. HA would be a redesign.
