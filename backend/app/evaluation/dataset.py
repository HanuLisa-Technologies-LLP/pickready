"""The evaluation dataset: its schema, its loader, and what it cannot fake.

WHAT THIS FILE PROVIDES AND WHAT IT DOES NOT
---------------------------------------------
It provides the schema, the loader, the stratification check and the empty-set
behaviour. It does NOT provide ground truth, and it will not generate any.

The specification asks for 50-100 job/candidate pairs whose ideal ranking, PPI
quality, email quality and probe quality were rated by a recruiting expert. That
is human work and it cannot be synthesised: a ground truth produced by the same
class of model being evaluated measures agreement with that model, not quality,
and every subsequent metric built on it would move for reasons nobody could
interpret. `load` returns an empty set until a person supplies one, and the
metrics that need labels report as unavailable rather than as zero -- because
zero looks like a failing score and unavailable is the truth.

STRATIFICATION IS CHECKED, NOT ASSUMED
---------------------------------------
A set that is 90% individual-contributor engineering roles will produce a
precision figure that says nothing about the CXO path. `stratification_report`
makes the composition visible so a metric is read with its coverage in mind.
"""
from __future__ import annotations

import json
import logging
import pathlib
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Ships in the image beside the demo resume corpus, for the same reason: a
#: fixture that lives outside the Docker build context never reaches production
#: and fails silently when something asks for it.
DATASET_PATH = pathlib.Path(__file__).resolve().parent / "fixtures" / "evaluation_cases.json"

#: Target composition. Not enforced -- a partial set is better than none -- but
#: reported, so a number is never read as more general than its inputs.
TARGET_STRATA: dict[str, dict[str, float]] = {
    "role_type": {"individual_contributor": 0.4, "manager": 0.4, "executive": 0.2},
    "seniority": {"junior": 0.3, "mid": 0.4, "senior": 0.2, "lead": 0.1},
    "complexity": {"simple": 0.3, "moderate": 0.5, "complex": 0.2},
}

MIN_USEFUL_CASES = 30


@dataclass(frozen=True)
class EvaluationCase:
    """One labelled job/candidate scenario.

    Every `truth_` field is optional. A case contributing only structural
    coverage is still worth having, and a schema that demanded a full expert
    rating would mean no cases at all until somebody had done all of it.
    """

    case_id: str
    job_id: str | None = None
    candidate_ids: tuple[str, ...] = ()
    #: Candidate ids in the expert's preferred order.
    truth_ranking: tuple[str, ...] = ()
    #: candidate id -> star rating, for NDCG.
    truth_relevance: dict[str, float] = field(default_factory=dict)
    truth_ppi_quality: int | None = None
    truth_email_quality: int | None = None
    truth_probe_quality: int | None = None
    strata: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    @property
    def has_ranking_truth(self) -> bool:
        return bool(self.truth_ranking)


def load(path: pathlib.Path | None = None) -> list[EvaluationCase]:
    """Read the dataset. An absent file is an empty set, never an error.

    The evaluation harness must run in CI on a fresh checkout, where no expert
    labels exist. Raising here would make the whole harness undeployable until
    the human work is finished, which is exactly backwards -- the structural
    metrics are useful immediately.
    """
    target = path or DATASET_PATH
    if not target.exists():
        logger.info("evaluation.dataset_absent path=%s", target)
        return []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("evaluation.dataset_unreadable err=%s", type(exc).__name__)
        return []

    cases: list[EvaluationCase] = []
    for entry in raw if isinstance(raw, list) else raw.get("cases", []):
        try:
            cases.append(
                EvaluationCase(
                    case_id=str(entry["case_id"]),
                    job_id=entry.get("job_id"),
                    candidate_ids=tuple(entry.get("candidate_ids") or ()),
                    truth_ranking=tuple(entry.get("truth_ranking") or ()),
                    truth_relevance={
                        str(k): float(v)
                        for k, v in (entry.get("truth_relevance") or {}).items()
                    },
                    truth_ppi_quality=entry.get("truth_ppi_quality"),
                    truth_email_quality=entry.get("truth_email_quality"),
                    truth_probe_quality=entry.get("truth_probe_quality"),
                    strata=dict(entry.get("strata") or {}),
                    notes=str(entry.get("notes") or ""),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            # One malformed case must not discard the rest, the same posture as
            # the databank upload: partial success beats an all-or-nothing read.
            logger.warning("evaluation.case_skipped err=%s", type(exc).__name__)
    return cases


def stratification_report(cases: list[EvaluationCase]) -> dict[str, Any]:
    """Actual composition against target, so coverage is read with the metric."""
    report: dict[str, Any] = {"cases": len(cases), "strata": {}}
    for dimension, targets in TARGET_STRATA.items():
        counts: dict[str, int] = {}
        for case in cases:
            value = case.strata.get(dimension)
            if value:
                counts[value] = counts.get(value, 0) + 1
        total = sum(counts.values()) or 1
        report["strata"][dimension] = {
            "observed": {key: round(value / total, 3) for key, value in counts.items()},
            "target": targets,
            "labelled": sum(counts.values()),
        }
    report["ranking_truth_cases"] = sum(1 for case in cases if case.has_ranking_truth)
    report["sufficient"] = len(cases) >= MIN_USEFUL_CASES
    return report
