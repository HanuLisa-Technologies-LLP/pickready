"""The judge jury, structurally outside the product's closed model mapping.

Nothing in this package may be reachable from `app/api/` or `app/workers/`, and
nothing under `app/services/` may import `app/evaluation/`. Both directions are
asserted by AST in `backend/tests/test_judge_isolation.py`. The reason is stated
once, in `jury.py`: the value of `MODEL_FOR_TASK` being a closed mapping onto
two ids is that a candidate's GRADE cannot be produced by an unreviewed model,
and a judge that could serve a product request would destroy that property.

Provenance: RPN-AI-UP-001 W7.4, W7.5.
"""
from __future__ import annotations

from app.evaluation.judges.jury import (
    ABSTAIN,
    INVALID,
    Juror,
    JuryUnavailable,
    PooledVerdict,
    configured_jurors,
    judge_set,
    pool,
    unavailable_reason,
)
from app.evaluation.judges.protocol import (
    ABSTENTION_HANDLING,
    AGGREGATION,
    MIN_USABLE_SHARE,
    POSITION_HANDLING,
    JudgeProtocol,
    JudgeReportingError,
    JudgeResult,
    build_result,
)

__all__ = [
    "ABSTAIN",
    "ABSTENTION_HANDLING",
    "AGGREGATION",
    "INVALID",
    "MIN_USABLE_SHARE",
    "POSITION_HANDLING",
    "JudgeProtocol",
    "JudgeReportingError",
    "JudgeResult",
    "Juror",
    "JuryUnavailable",
    "PooledVerdict",
    "build_result",
    "configured_jurors",
    "judge_set",
    "pool",
    "unavailable_reason",
]
