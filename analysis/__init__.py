"""Offline analysis package — loader, validator, resolution joiner, replayer,
brute-force harness.

This package is import-isolated from the live trading bot. RP2 (per
``docs/RAW_DECISION_SNAPSHOT_PLAN.md``) statically forbids any module under
``core/``, ``data_sources/``, ``execution/`` from importing ``analysis``.

Outputs from this package describe POLICY / DECISION REPLAY only, never
live-equivalent trade simulation (CLAUDE.md Rule 3 / plan §9).
"""

from __future__ import annotations
