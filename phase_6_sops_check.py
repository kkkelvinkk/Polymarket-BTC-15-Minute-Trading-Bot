"""
Phase 6 — SOPS credential management guard (Pattern A).

This module ships the "refuse plaintext .env in live mode" check from
EXECUTION_PLAN.md Phase 6 as a standalone callable. It is NOT wired into
``bot.py`` import-time yet — the plan explicitly requires operator approval
before changing live-mode env-loading behavior, since current operators run
with plaintext ``.env`` files and switching them off without warning would
break their workflow.

When the operator decides to adopt SOPS, the integration is a one-liner near
the top of ``bot.py``, BEFORE ``load_dotenv()`` runs::

    # === Phase 6 SOPS guard (operator-opted-in via repo edit) ===
    from phase_6_sops_check import refuse_plaintext_env_in_live_mode
    refuse_plaintext_env_in_live_mode(repo_root=Path(__file__).parent)

The plan's "Pattern A" is implemented here: check ``--live`` argument
presence at module-import time, before ``load_dotenv()``. If both
``--live`` and a plaintext ``.env`` are present at the repo root, refuse to
start. The operator-supplied alternative is to inject credentials via
``sops exec-env`` so the process environment already has the values when
``load_dotenv()`` is skipped.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable


_DEFAULT_LIVE_FLAGS = ("--live",)


def _is_live_invocation(argv: Iterable[str] | None = None) -> bool:
    """Return True if the process is being launched in live mode.

    Reads from ``sys.argv`` by default. The operator can also set
    ``BOT_LIVE_MODE=1`` in the process environment to declare live mode
    without ``--live`` on the command line (some supervisors pass args via
    env). Both mechanisms are equally trusted; either one means "the next
    step will start placing real orders."
    """
    args = list(argv) if argv is not None else list(sys.argv)
    if any(flag in args for flag in _DEFAULT_LIVE_FLAGS):
        return True
    if os.getenv("BOT_LIVE_MODE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return False


def refuse_plaintext_env_in_live_mode(
    repo_root: Path,
    argv: Iterable[str] | None = None,
) -> None:
    """Phase 6 Pattern A — refuse to start in live mode if a plaintext
    ``.env`` is present at the repo root.

    Call this BEFORE ``load_dotenv()`` runs. After the operator opts in to
    SOPS, this guard catches the regression where someone leaves the
    plaintext file behind: the bot fail-stops instead of silently reading
    real credentials from disk.

    Raises ``RuntimeError`` if the conditions are met. The operator can
    move ``.env`` outside the repo (or delete it after migrating to
    encrypted form) to clear the block.

    Does NOT inspect simulation-mode invocations; plaintext ``.env`` is
    still acceptable for ``--test-mode`` and default simulation.
    """
    if not _is_live_invocation(argv):
        return

    env_path = repo_root / ".env"
    if env_path.exists():
        raise RuntimeError(
            "Live mode refuses to start with plaintext .env present at "
            f"{env_path}. Use `sops exec-env /path/to/.env.sops.yaml` to "
            "inject credentials into the process environment, or move .env "
            "outside the repo. See EXECUTION_PLAN.md Phase 6 and "
            "deploy/README.md SOPS section."
        )
