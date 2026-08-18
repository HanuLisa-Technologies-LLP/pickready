"""Measuring the agent framework: metrics, a labelled dataset, regression cases.

The honest division, stated once here because it governs how every number in
this package should be read:

  Structural metrics need no labels and are available today: word ranges,
  generic-language rate, evidence sourcing, behavioural phrasing.

  Quality metrics need ground truth a recruiting expert produced. Until that
  exists they are reported as UNAVAILABLE rather than as zero, because zero
  reads as a failing score and unavailable is the truth.
"""
from __future__ import annotations

from app.evaluation import dataset, metrics, regression
from app.evaluation.dataset import EvaluationCase, load, stratification_report
from app.evaluation.regression import RegressionResult, run_all, summary

__all__ = [
    "EvaluationCase",
    "RegressionResult",
    "dataset",
    "load",
    "metrics",
    "regression",
    "run_all",
    "stratification_report",
    "summary",
]
