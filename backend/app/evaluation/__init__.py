"""Measuring the agent framework: metrics, labelled datasets, regression cases.

The honest division, stated once here because it governs how every number in
this package should be read:

  Structural metrics need no labels and are available today: word ranges,
  generic-language rate, evidence sourcing, behavioural phrasing.

  Quality metrics need ground truth a recruiting expert produced. Until that
  exists they are reported as UNAVAILABLE rather than as zero, because zero
  reads as a failing score and unavailable is the truth.

THE THREE GOLDEN SETS (RPN-AI-UP-001 W7.1)
--------------------------------------------
`golden.py` adds the disentangled sets: retrieval (judge free, populated),
reasoning and decision (both need human labels, both empty and both saying so).
`retrieval_eval.py` scores a run against the retrieval set offline;
`judges/` holds the jury interface and its reporting contract.

NOTHING IN THIS PACKAGE MAY BE IMPORTED BY `app/services/`, AND NOTHING UNDER
`judges/` MAY BE REACHABLE FROM `app/api/` OR `app/workers/`. Both directions
are asserted by AST in `backend/tests/test_judge_isolation.py`. The reason is in
`judges/jury.py`: `MODEL_FOR_TASK` being a closed mapping onto exactly two ids
is what guarantees a candidate's grade cannot be produced by an unreviewed
model, and a judge reachable from a product request would destroy that.
"""
from __future__ import annotations

from app.evaluation import dataset, golden, metrics, regression, reporting, retrieval_eval
from app.evaluation.dataset import EvaluationCase, load, stratification_report
from app.evaluation.golden import GOLDEN_VERSION, GoldenSetError, SetAvailability
from app.evaluation.metrics import InsufficientData, Interval, Measurement
from app.evaluation.regression import RegressionResult, run_all, summary
from app.evaluation.reporting import EvalReport

__all__ = [
    "EvalReport",
    "EvaluationCase",
    "GOLDEN_VERSION",
    "GoldenSetError",
    "InsufficientData",
    "Interval",
    "Measurement",
    "RegressionResult",
    "SetAvailability",
    "dataset",
    "golden",
    "load",
    "metrics",
    "regression",
    "reporting",
    "retrieval_eval",
    "run_all",
    "stratification_report",
    "summary",
]
