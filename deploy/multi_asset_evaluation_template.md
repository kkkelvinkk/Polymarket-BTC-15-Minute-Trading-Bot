# Phase 7.5 — Multi-Asset Market Evaluation (operator template)

Per EXECUTION_PLAN.md Phase 7.5, this phase is a decision-making exercise the
operator runs **before** committing to a Phase 8 production deployment. There
is no code in this phase. The operator fills out this template, decides which
assets (if any) to expand to, and the deployment configuration in
[deploy/](.) is scoped to that final asset set.

Phase 7.5 must be completed before Phase 8 deployment if the operator intends
to run anything other than BTC 15-min markets.

## Candidate assets to evaluate

Polymarket lists at least these 15-minute crypto markets:

- [ ] BTC 15-min (already supported; this is the current bot focus)
- [ ] ETH 15-min
- [ ] SOL 15-min
- [ ] XRP 15-min
- [ ] (other liquid crypto markets — list here)

## Questions to answer (operator fills in)

### 1. Which additional assets, if any, to add?

> _Operator answer:_

### 2. Per-asset signal-processor tuning?

Does each asset use the same signal-processor mix (spike, divergence,
orderbook, sentiment, tick velocity) as BTC, or does an asset need
different processor weights / thresholds?

> _Operator answer:_

### 3. Per-asset risk caps?

Does each asset share the same `MAX_POSITION_SIZE`, `MAX_TOTAL_EXPOSURE`,
`MAX_POSITIONS`, and `MAX_LOSS_PER_DAY`, or does it need per-asset caps?

> _Operator answer:_

### 4. Process topology?

- [ ] One process trading multiple assets (single Nautilus node, multiple
      Polymarket subscriptions).
- [ ] One process per asset (separate systemd units, separate ledgers).
- [ ] Other (describe).

> _Operator answer:_

### 5. Per-asset calibration?

Does Phase 4 calibration (n ≥ 100 settled trades in at least one
confidence bucket, three-gate pass) need to be repeated independently for
each asset before that asset's live trading is unblocked?

Recommendation: **yes** — confidence calibration is unlikely to transfer
between assets without evidence. The current Phase 4 script reads a single
ledger; for multi-asset, run `analyze_calibration.py --ledger <per-asset>`
against each asset's separate ledger.

> _Operator answer:_

## Implementation effort estimate

After the operator fills in the questions above, the follow-up implementation
effort is sized per the topology choice:

| Topology | Estimated effort |
| --- | --- |
| Multi-asset, single process | 3-5 days (instrument provider config, per-asset risk caps, per-asset decision logs) |
| One process per asset | 1-2 days (parametrize systemd units; mostly ops work) |

Both topologies require:
- Per-asset slug generation in `run_integrated_bot` (currently hardcoded `btc-updown-15m-`)
- Per-asset Grafana dashboards
- Per-asset alerting rules in the deployment

## Exit criteria

- [ ] Operator documents the decision (add / don't add / partial) above.
- [ ] If any assets are approved, follow-up phase(s) are scoped before Phase 8 deployment begins.
- [ ] If no additional assets are approved, mark Phase 7.5 closed and proceed to Phase 8 BTC-only deployment.
