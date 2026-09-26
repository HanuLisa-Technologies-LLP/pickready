"""The three golden sets, their stratification, and what each one may be used for.

THE METHODOLOGICAL IDEA (RPN-AI-UP-001 W7.1)
---------------------------------------------
Taken from BrowseComp-Plus: hold the corpus fixed and vary ONE component, so a
retrieval regression is distinguishable from a model regression. One blended
"is the system good" number cannot do that, because every component moves under
it at once and the number cannot say which.

    Retrieval   did we find the right evidence      query -> chunk ids   judge free
    Reasoning   did the model interpret it right    pack -> band         needs a judge
    Decision    did the system reach the outcome    case -> grade        needs a judge

WHAT IS POPULATED TODAY, AND WHY THE OTHER TWO ARE NOT
-------------------------------------------------------
The RETRIEVAL set is populated. A query paired with a relevant chunk id is
objectively checkable, so W7.1 permits building it without an expert rating
each case, and a person verifies a chunk id cheaply.

The REASONING and DECISION sets are EMPTY and report `unavailable` with the
reason, in both directions:

  1. They must be human labelled. Ground truth produced by the same class of
     model being evaluated measures agreement with that model, not quality.
     This is the rule `dataset.py` and `eval_agents.py` already live by.
  2. There is nothing to sample. The only deployed environment holds zero
     candidates, profiles, applications, reports and evaluations
     (`docs/verification/AI_UPGRADE_BASELINE.md`, 2026-09-09), so W7.1's "60%
     stratified production sample" has no population to stratify.

Neither of those is worked around here. An empty set is reported as empty.

THE VERSION IS STAMPED ON EVERY RESULT
---------------------------------------
`GOLDEN_VERSION` travels with each set and `reporting.EvalReport` refuses to be
built without it. A score that drops has three possible causes -- the model
regressed, the rubric changed, or the SET changed -- and without the stamp on
the result the third is indistinguishable from the first two.

AN ABSENT FILE RAISES HERE, UNLIKE `dataset.load`
--------------------------------------------------
`dataset.load` treats a missing file as an empty set on purpose: its labels are
human work that may never have been done, and CI must run on a fresh checkout
regardless. These files SHIP IN THE TREE. A missing one is a packaging defect
-- the failure mode `seed_resume_corpus` had, where a fixture outside the
Docker build context never reached the image and the caller logged that it
found nothing and exited zero -- so it raises and names the path.

Provenance: RPN-AI-UP-001 W7.1.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import Any, Mapping

#: Bumped when the CASES change, never when the code that reads them changes.
GOLDEN_VERSION = "2026.Q3.2"

DATASETS_ROOT = pathlib.Path(__file__).resolve().parent / "datasets"

SET_RETRIEVAL = "retrieval"
SET_REASONING = "reasoning"
SET_DECISION = "decision"
SET_NAMES: tuple[str, ...] = (SET_RETRIEVAL, SET_REASONING, SET_DECISION)


# ── The three stratification axes ────────────────────────────────────────────
#
# Written out here rather than imported from the product enums on purpose. The
# eval layer must not import `app/services` (see W7.4 and
# `tests/test_judge_isolation.py`), and a golden set whose axis silently
# followed a product enum would change shape under a product change, which is
# the one thing a version-stamped ground truth must not do.

#: `jobs.assessment_grade`, transcribed. Never null in the product; legacy rows
#: read `non_managerial`.
JOB_GRADES: tuple[str, ...] = ("non_managerial", "managerial", "leadership", "cxo")

#: The Tatva Assessment's three dimensions, transcribed from `services/ppi.py`.
DIMENSION_CATEGORIES: tuple[str, ...] = ("must_have", "nice_to_have", "behavioural")

#: The Runbook's six-tier evidence hierarchy, transcribed from
#: `services/evidence/tiers.py`. THIS IS THE AXIS THAT EARNS ITS PLACE. Runbook
#: 14.1's unassessed Must-have case, which `claude.md` records as passing today,
#: is invisible on the other two axes: a fabricated Must-have resting on one
#: weakest-tier resume bullet scores high, grades Matching and trips no
#: score-based cap, because section 10.2's competency score puts evidence
#: strength in both numerator and denominator and the terms cancel. Only a
#: breakdown BY TIER shows that a Must-have's whole support sits at E0.
EVIDENCE_TIERS: tuple[str, ...] = ("E0", "E1", "E2", "E3", "E4", "E5")

AXES: dict[str, tuple[str, ...]] = {
    "job_grade": JOB_GRADES,
    "dimension_category": DIMENSION_CATEGORIES,
    "evidence_tier": EVIDENCE_TIERS,
}


# ── Composition targets ──────────────────────────────────────────────────────

#: W7.1's own targets: 300 at the floor, 500 working, 1,000 mature, per set.
FLOOR_CASES = 300
WORKING_CASES = 500
MATURE_CASES = 1000

#: W7.1's composition. `hand_authored` is NOT one of the four and carries a
#: target of zero deliberately: a fixture written to exercise the harness is not
#: a sample of anything, and giving it a target would let it satisfy one.
TARGET_PROVENANCE: dict[str, float] = {
    "production_sample": 0.60,
    "adversarial": 0.15,
    "expert_edge_case": 0.15,
    "failure_replay": 0.10,
    "hand_authored": 0.0,
}
PROVENANCE_VALUES: tuple[str, ...] = tuple(TARGET_PROVENANCE)

#: Provenance values a run file may declare. A `reference_fixture` run was hand
#: authored to exercise the metrics; a `recorded` run came out of a real
#: retrieval execution. Only the second can carry a quality claim.
RUN_PROVENANCE_VALUES: tuple[str, ...] = ("reference_fixture", "recorded")

#: W7.1: "Rebalance binary rubrics to roughly 40/60 internally, then project
#: back to production ratios on dashboards." This is the internal rate, and it
#: is not cosmetic. On a rubric where almost every item is "met", which is
#: exactly the Must-have shape (W7.1 illustrates it at 95%), chance agreement
#: is already near one, so a judge can post high accuracy and near-zero kappa.
#: That is the kappa paradox: it makes a good judge look useless and a useless
#: judge look fine. `metrics.reweight_to_prevalence` is the projection back, and
#: it takes the production prevalence as an ARGUMENT rather than reading a
#: constant, because this codebase has never measured that rate.
BALANCED_POSITIVE_RATE = 0.40


class GoldenSetError(RuntimeError):
    """A golden set on disk is missing, unreadable or internally inconsistent.

    Raised rather than degraded. These files ship in the tree; an absent or
    malformed one is a packaging or authoring defect, and a loader that
    substituted an empty set for it would report "nothing to measure" in the
    exact voice it uses for the honest empty case.
    """


# ── Case and corpus types ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Strata:
    """One case's position on the three axes. Values are validated, not trusted.

    An unrecognised value RAISES. A typo silently creating a fourth job grade
    would produce a stratification report with an extra row that always reads
    as under-covered, and the fix would look like adding cases rather than
    fixing a typo.
    """

    job_grade: str
    dimension_category: str
    evidence_tier: str

    def __post_init__(self) -> None:
        for axis, value in (
            ("job_grade", self.job_grade),
            ("dimension_category", self.dimension_category),
            ("evidence_tier", self.evidence_tier),
        ):
            if value not in AXES[axis]:
                raise GoldenSetError(
                    f"{value!r} is not a {axis}; permitted: {', '.join(AXES[axis])}"
                )

    @property
    def cell(self) -> tuple[str, str, str]:
        return (self.job_grade, self.dimension_category, self.evidence_tier)

    def as_dict(self) -> dict[str, str]:
        return {
            "job_grade": self.job_grade,
            "dimension_category": self.dimension_category,
            "evidence_tier": self.evidence_tier,
        }


@dataclass(frozen=True)
class Chunk:
    """One retrievable unit of the fixed corpus."""

    chunk_id: str
    text: str
    source_kind: str
    job_grade: str
    evidence_tier: str


@dataclass(frozen=True)
class RetrievalCase:
    """One query and the chunk ids that answer it, with graded relevance.

    `qrels` maps chunk id to gain. A chunk absent from `qrels` has gain zero;
    it is not listed, because listing every irrelevant chunk would make the file
    quadratic in the corpus and would tempt somebody to shrink the corpus.
    """

    query_id: str
    text: str
    strata: Strata
    provenance: str
    human_verified: bool
    qrels: dict[str, float]
    notes: str = ""

    @property
    def relevant_ids(self) -> tuple[str, ...]:
        return tuple(sorted(key for key, gain in self.qrels.items() if gain > 0))


@dataclass(frozen=True)
class JudgedCase:
    """One case in the reasoning or decision set: an input and an expert label.

    Both sets are empty today, and this type exists anyway so the loader, the
    stratification report and the availability answer are the SAME code for all
    three sets. A set whose emptiness is handled by a special case is a set
    nobody notices has stopped being empty.
    """

    case_id: str
    strata: Strata
    provenance: str
    human_verified: bool
    expected_label: str
    payload_ref: str
    notes: str = ""


@dataclass(frozen=True)
class RetrievalRun:
    """A ranked list per query, and where it came from.

    `expected_metrics` is the harness self check: the values this run's
    rankings produce under a reviewed metric implementation. `eval_retrieval
    --gate` recomputes and compares, so a change to the discount, the cutoff
    handling or the gain scale goes red instead of quietly moving every number
    the programme reports. A `recorded` run carries no expectation, because
    there is nothing to pin: its numbers are the measurement.
    """

    run_id: str
    provenance: str
    produced_by: str
    depth: int
    results: dict[str, tuple[str, ...]]
    expected_metrics: dict[str, float] = field(default_factory=dict)
    notes: str = ""

    @property
    def gate_eligible(self) -> bool:
        """Whether this run may carry a retrieval QUALITY claim.

        A hand-authored fixture may not. Its numbers measure this harness and
        say nothing about the product retriever, and a green quality gate
        resting on one would be the "the pipeline passed" failure this codebase
        already has a section about.
        """
        return self.provenance == "recorded"


@dataclass(frozen=True)
class RetrievalSet:
    version: str
    corpus: tuple[Chunk, ...]
    cases: tuple[RetrievalCase, ...]

    @property
    def corpus_ids(self) -> frozenset[str]:
        return frozenset(chunk.chunk_id for chunk in self.corpus)


@dataclass(frozen=True)
class JudgedSet:
    name: str
    version: str
    cases: tuple[JudgedCase, ...]
    why_empty: str = ""


@dataclass(frozen=True)
class SetAvailability:
    """Whether a set may be measured, and whether it may gate a release.

    TWO ANSWERS, NOT ONE, and that is the whole design. A set can be perfectly
    usable for exercising the harness while being useless as evidence about the
    product. Collapsing them into a single boolean forces a choice between a
    gate that never runs and a gate that certifies a fixture.
    """

    status: str
    reason: str
    gate_eligible: bool
    gate_block_reason: str = ""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "status": self.status,
            "reason": self.reason,
            "gate_eligible": self.gate_eligible,
        }
        if self.gate_block_reason:
            body["gate_block_reason"] = self.gate_block_reason
        return body


# ── Loading ──────────────────────────────────────────────────────────────────


def _read(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        raise GoldenSetError(
            f"golden set artefact missing from the tree: {path}. These files "
            "ship with the package; an absent one is a packaging defect, not "
            "an empty set."
        )
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise GoldenSetError(f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise GoldenSetError(f"{path} must hold a JSON object")
    return body


def _check_version(body: Mapping[str, Any], path: pathlib.Path, version: str) -> None:
    declared = body.get("version")
    if declared != version:
        raise GoldenSetError(
            f"{path} declares version {declared!r}, expected {version!r}. A "
            "result stamped with one version and computed from another is the "
            "attribution failure the stamp exists to prevent."
        )


def _strata(raw: Any, where: str) -> Strata:
    if not isinstance(raw, dict):
        raise GoldenSetError(f"{where} carries no strata object")
    missing = [axis for axis in AXES if axis not in raw]
    if missing:
        raise GoldenSetError(f"{where} is missing strata axes: {', '.join(missing)}")
    return Strata(
        job_grade=str(raw["job_grade"]),
        dimension_category=str(raw["dimension_category"]),
        evidence_tier=str(raw["evidence_tier"]),
    )


def _provenance(raw: Any, where: str) -> str:
    value = str(raw or "")
    if value not in PROVENANCE_VALUES:
        raise GoldenSetError(
            f"{where} declares provenance {value!r}; permitted: "
            + ", ".join(PROVENANCE_VALUES)
        )
    return value


def set_path(name: str, version: str = GOLDEN_VERSION) -> pathlib.Path:
    if name not in SET_NAMES:
        raise GoldenSetError(f"{name!r} is not a golden set; expected one of {SET_NAMES}")
    return DATASETS_ROOT / name / version


def load_retrieval_set(version: str = GOLDEN_VERSION) -> RetrievalSet:
    """Corpus and queries, cross-validated against each other.

    A qrel naming a chunk id that is not in the corpus RAISES. That is the one
    authoring error a retrieval ground truth can carry that nothing downstream
    detects: recall over an unreachable id is permanently below one, which
    reads as a retrieval failure for as long as nobody checks the file.
    """
    root = set_path(SET_RETRIEVAL, version)
    corpus_body = _read(root / "corpus.json")
    queries_body = _read(root / "queries.json")
    _check_version(corpus_body, root / "corpus.json", version)
    _check_version(queries_body, root / "queries.json", version)

    chunks: list[Chunk] = []
    seen_chunks: set[str] = set()
    for entry in corpus_body.get("chunks", []):
        chunk_id = str(entry["chunk_id"])
        if chunk_id in seen_chunks:
            raise GoldenSetError(f"duplicate chunk id {chunk_id!r} in the corpus")
        seen_chunks.add(chunk_id)
        tier = str(entry["evidence_tier"])
        if tier not in EVIDENCE_TIERS:
            raise GoldenSetError(f"chunk {chunk_id} declares evidence tier {tier!r}")
        grade = str(entry["job_grade"])
        if grade not in JOB_GRADES:
            raise GoldenSetError(f"chunk {chunk_id} declares job grade {grade!r}")
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                text=str(entry["text"]),
                source_kind=str(entry["source_kind"]),
                job_grade=grade,
                evidence_tier=tier,
            )
        )
    if not chunks:
        raise GoldenSetError("the retrieval corpus is empty; nothing can be retrieved")

    cases: list[RetrievalCase] = []
    seen_queries: set[str] = set()
    for entry in queries_body.get("queries", []):
        query_id = str(entry["query_id"])
        if query_id in seen_queries:
            raise GoldenSetError(f"duplicate query id {query_id!r}")
        seen_queries.add(query_id)
        qrels = {str(key): float(value) for key, value in (entry.get("qrels") or {}).items()}
        if not qrels:
            raise GoldenSetError(f"query {query_id} names no relevant chunk")
        unknown = sorted(set(qrels) - seen_chunks)
        if unknown:
            raise GoldenSetError(
                f"query {query_id} grades chunk ids absent from the corpus: "
                + ", ".join(unknown)
            )
        cases.append(
            RetrievalCase(
                query_id=query_id,
                text=str(entry["text"]),
                strata=_strata(entry.get("strata"), f"query {query_id}"),
                provenance=_provenance(entry.get("provenance"), f"query {query_id}"),
                human_verified=bool(entry.get("human_verified", False)),
                qrels=qrels,
                notes=str(entry.get("notes") or ""),
            )
        )
    if not cases:
        raise GoldenSetError("the retrieval query set is empty")
    return RetrievalSet(version, tuple(chunks), tuple(cases))


def load_judged_set(name: str, version: str = GOLDEN_VERSION) -> JudgedSet:
    """The reasoning or decision set. Empty today, and honest about it."""
    if name not in (SET_REASONING, SET_DECISION):
        raise GoldenSetError(f"{name!r} is not a judged set")
    path = set_path(name, version) / "cases.json"
    body = _read(path)
    _check_version(body, path, version)
    cases: list[JudgedCase] = []
    for entry in body.get("cases", []):
        case_id = str(entry["case_id"])
        cases.append(
            JudgedCase(
                case_id=case_id,
                strata=_strata(entry.get("strata"), f"case {case_id}"),
                provenance=_provenance(entry.get("provenance"), f"case {case_id}"),
                human_verified=bool(entry.get("human_verified", False)),
                expected_label=str(entry["expected_label"]),
                payload_ref=str(entry["payload_ref"]),
                notes=str(entry.get("notes") or ""),
            )
        )
    return JudgedSet(name, version, tuple(cases), str(body.get("why_empty") or ""))


def load_run(run_id: str, version: str = GOLDEN_VERSION) -> RetrievalRun:
    path = set_path(SET_RETRIEVAL, version) / "runs" / f"{run_id}.json"
    body = _read(path)
    _check_version(body, path, version)
    provenance = str(body.get("provenance") or "")
    if provenance not in RUN_PROVENANCE_VALUES:
        raise GoldenSetError(
            f"run {run_id} declares provenance {provenance!r}; permitted: "
            + ", ".join(RUN_PROVENANCE_VALUES)
        )
    results_raw = body.get("results") or {}
    if not results_raw:
        raise GoldenSetError(f"run {run_id} ranks nothing")
    results: dict[str, tuple[str, ...]] = {}
    for query_id, ranked in results_raw.items():
        ids = tuple(str(item) for item in ranked)
        if len(set(ids)) != len(ids):
            raise GoldenSetError(
                f"run {run_id} ranks a chunk twice for {query_id}; a duplicate "
                "inflates recall at every cutoff below it"
            )
        results[str(query_id)] = ids
    expected = {
        str(key): float(value)
        for key, value in (body.get("expected_metrics") or {}).items()
        if isinstance(value, (int, float))
    }
    return RetrievalRun(
        run_id=run_id,
        provenance=provenance,
        produced_by=str(body.get("produced_by") or ""),
        depth=int(body.get("depth") or 0),
        results=results,
        expected_metrics=expected,
        notes=str(body.get("what_this_is") or ""),
    )


def available_runs(version: str = GOLDEN_VERSION) -> tuple[str, ...]:
    directory = set_path(SET_RETRIEVAL, version) / "runs"
    if not directory.is_dir():
        return ()
    return tuple(sorted(path.stem for path in directory.glob("*.json")))


# ── Stratification and availability ──────────────────────────────────────────


def stratification(cases: tuple[Any, ...]) -> dict[str, Any]:
    """Observed composition against target, plus the cells nobody covered.

    THE UNPOPULATED CELLS ARE THE POINT. A per-axis histogram makes a set look
    balanced when every leadership case happens to be Must-have and every CXO
    case happens to be Behavioural. The cell list is what a reader has to see
    before treating any per-stratum number as general.
    """
    report: dict[str, Any] = {"cases": len(cases)}
    for axis, values in AXES.items():
        counts = {value: 0 for value in values}
        for case in cases:
            counts[getattr(case.strata, axis)] += 1
        report[axis] = counts

    provenance_counts = {value: 0 for value in PROVENANCE_VALUES}
    for case in cases:
        provenance_counts[case.provenance] += 1
    total = len(cases) or 1
    report["provenance"] = {
        "observed": {
            key: round(value / total, 4) for key, value in provenance_counts.items()
        },
        "counts": provenance_counts,
        "target": dict(TARGET_PROVENANCE),
    }

    populated = {tuple(case.strata.cell) for case in cases}
    all_cells = [
        (grade, category, tier)
        for grade in JOB_GRADES
        for category in DIMENSION_CATEGORIES
        for tier in EVIDENCE_TIERS
    ]
    report["cells"] = {
        "total": len(all_cells),
        "populated": len(populated),
        "unpopulated": ["/".join(cell) for cell in all_cells if cell not in populated],
    }
    report["human_verified"] = sum(1 for case in cases if case.human_verified)
    report["size_targets"] = {
        "floor": FLOOR_CASES,
        "working": WORKING_CASES,
        "mature": MATURE_CASES,
        "meets_floor": len(cases) >= FLOOR_CASES,
    }
    return report


def availability(name: str, cases: tuple[Any, ...], why_empty: str = "") -> SetAvailability:
    """Whether the set can be measured, and whether it can gate.

    The two blocks on gating are stated separately because they are lifted by
    different work: human verification is cheap reading, and a production sample
    needs a deployed environment with candidates in it.
    """
    if not cases:
        return SetAvailability(
            status=SetAvailability.UNAVAILABLE,
            reason=why_empty
            or f"the {name} golden set holds no cases, so nothing can be measured",
            gate_eligible=False,
            gate_block_reason="an empty set cannot gate anything",
        )

    blocks: list[str] = []
    unverified = [case for case in cases if not case.human_verified]
    if unverified:
        blocks.append(
            f"{len(unverified)} of {len(cases)} cases are not human verified"
        )
    production = sum(1 for case in cases if case.provenance == "production_sample")
    if production == 0:
        blocks.append(
            "no case is drawn from a production sample, against a 60% target; "
            "the only deployed environment holds zero candidates"
        )
    if len(cases) < FLOOR_CASES:
        blocks.append(f"{len(cases)} cases is below the floor of {FLOOR_CASES}")

    return SetAvailability(
        status=SetAvailability.AVAILABLE,
        reason=f"{len(cases)} cases loaded at version {GOLDEN_VERSION}",
        gate_eligible=not blocks,
        gate_block_reason="; ".join(blocks),
    )
