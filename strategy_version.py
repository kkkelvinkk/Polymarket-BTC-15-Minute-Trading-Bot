"""Strategy version identifier captured into every raw decision snapshot.

Bumped by hand whenever a semantic change lands under
``core/strategy_brain/**``. The Alpha-3 CI check (per
``docs/RAW_DECISION_SNAPSHOT_PLAN.md``) fails any PR that modifies
strategy code without also bumping ``STRATEGY_VERSION`` here.

Comment-only, blank-line-only, and type-annotation-only diffs are exempt
from the bump requirement; see the CI script for the exact filter
(``git diff -G '^[^#]'``).
"""

from __future__ import annotations

STRATEGY_VERSION: str = "1.0.0"
