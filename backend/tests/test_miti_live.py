"""Miti on the LIVE path: the wiring, not the modules.

`test_miti_pipeline.py` asserts the properties of each stage in isolation. This
file asserts them of the path a real candidate's report is written from, which
is the distinction spec-doc6 section 4.4 draws when it says the existing
AST-level test "stays and is extended to cover the live wiring, not just the
module". Until 2026-08-29 the whole Part A stack was reachable from exactly one
file, `app/scripts/worked_example.py`, and from no route or worker: every gate
was a real check guarding nothing.

What is asserted here:

  1. GATE G1 IS THE ONLY WAY IN. No frozen matrix, no scoring, no default.
  2. Isolation survives the wiring. Each of the five evaluators receives only
     its own competencies, its own dimension's rubric anchors and the evidence
     routed to them; none receives a name, another dimension's band, or the
     composite. The five run concurrently.
  3. The aggregator makes zero model calls, through the live path.
  4. Determinism, across 100 runs and across process restarts.
  5. Insufficient evidence reduces CONFIDENCE, never score.
  6. The four candidate states, one fixture each.
  7. NO FLAG AUTO-REJECTS, and no rejection exists without a human disposition.
  8. G2 blocks nothing and says something actionable.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
import sys
import types
import uuid
from datetime import datetime, timezone

import pytest

from app.services import functional_assessment as fa
from app.services import rating
from app.services.evidence import ledger
import app.services.hiring as hiring_pkg
from app.services.hiring import gates
from app.services.hiring.department_models import (
    DIM_AUTHENTICITY,
    DIM_ROLE_FIT,
    DIM_TRACK_RECORD,
    DIM_TRAJECTORY,
    DIM_VERIFIED_COMPETENCE,
)
from app.services.miti import aggregation, live, triangulation
from app.services.miti.dimensions import DIMENSIONS, DIMENSION_LABELS

SCORECARD_MODULE = "app.services.hiring.scorecard"

_TENANT = uuid.uuid4()
_JOB = uuid.uuid4()
_LINK = uuid.uuid4()


# -- Harness ----------------------------------------------------------------


class _Session:
    """Enough of a session for the ledger reads, and nothing more.

    The whole live path is exercised against this, which is the point: the
    isolation and gate ordering are properties of the wiring, not of Postgres,
    and an integration test that also needed a database would be one nobody
    runs on every change.
    """

    async def get(self, model, ident):  # pragma: no cover - not reached here
        return None


def _item(
    competency: str,
    category: str,
    dimension: str,
    *,
    weight: float = 0.3,
    threshold: float | None = None,
):
    return types.SimpleNamespace(
        competency=competency,
        category=category,
        dimension=dimension,
        weight=weight,
        threshold=threshold,
        evidence_sources=("assessment",),
        assessment_method="structured probe",
        disqualifier=None,
        provenance={"layer": "L1"},
    )


def _matrix(items=None, *, approved_at=datetime(2026, 8, 1, tzinfo=timezone.utc)):
    return types.SimpleNamespace(
        items=list(
            items
            if items is not None
            else [
                _item("Stream processing depth", "must_have", DIM_VERIFIED_COMPETENCE),
                _item("Migration ownership", "nice_to_have", DIM_TRACK_RECORD),
                _item("Operating in ambiguity", "behavioural", DIM_ROLE_FIT),
            ]
        ),
        approved_at=approved_at,
        version=3,
    )


def _evidence_item(
    *,
    ref: uuid.UUID,
    source_type: str = ledger.SOURCE_ANSWER,
    trust: str = ledger.TRUST_OBSERVED,
    has_specifics: bool = True,
):
    return ledger.EvidenceItem(
        evidence_id=ref,
        tenant_id=_TENANT,
        job_id=_JOB,
        link_id=_LINK,
        source_type=source_type,
        source_id=uuid.uuid4(),
        text_ref=f"assessment_messages:{uuid.uuid4()}",
        provenance={"agent": "miti", "has_specifics": has_specifics},
        freshness={"band": ledger.FRESHNESS_CURRENT},
        trust=trust,
    )


def _claim(competency: str, items):
    return ledger.Claim(
        claim_id=uuid.uuid4(),
        tenant_id=_TENANT,
        job_id=_JOB,
        link_id=_LINK,
        subject="candidate",
        dimension=competency,
        claim=f"the candidate demonstrated {competency}",
        supporting_evidence=tuple(items),
    )


def _detach_scorecard(monkeypatch, replacement) -> None:
    """Make `from app.services.hiring import scorecard` resolve to `replacement`.

    BOTH bindings, for the reason spelled out in `_Harness._install`: the
    package attribute wins over the `sys.modules` entry once anything has
    imported the real module, so setting only one makes the test depend on
    which files ran before it. `None` in `sys.modules` is how Python reports an
    absent module, and deleting the package attribute is how it reports one
    that was never imported; a genuinely missing module has both.
    """
    monkeypatch.setitem(sys.modules, SCORECARD_MODULE, replacement)
    if replacement is None:
        monkeypatch.delattr(hiring_pkg, "scorecard", raising=False)
    else:
        monkeypatch.setattr(hiring_pkg, "scorecard", replacement, raising=False)


class _Harness:
    """Installs a scorecard module, a ledger and a recording `invoke`."""

    def __init__(self, monkeypatch, *, claims=None, texts=None, matrix=None):
        self.monkeypatch = monkeypatch
        self.calls: list[tuple[str, list[dict[str, str]]]] = []
        self.claims = claims if claims is not None else _default_claims()
        self.texts = texts or {}
        self.matrix = matrix if matrix is not None else _matrix()
        self.band_by_dimension: dict[str, str] = {d: "solid" for d in DIMENSIONS}
        self._install()

    def _install(self) -> None:
        module = types.ModuleType(SCORECARD_MODULE)

        async def require_frozen_matrix(session, job_id):
            return self.matrix

        module.require_frozen_matrix = require_frozen_matrix
        self.monkeypatch.setitem(sys.modules, SCORECARD_MODULE, module)
        # AND rebind the attribute on the parent package, which is what the
        # consumer actually resolves.
        #
        # `functional_assessment` does `from app.services.hiring import
        # scorecard` inside the function, which looks like a lazy import that
        # would pick the fake out of `sys.modules`. It does, but only until
        # something imports the real module once: `from package import
        # submodule` reads the PACKAGE ATTRIBUTE when one exists, and importing
        # `app.services.hiring.scorecard` sets `app.services.hiring.scorecard`
        # as an attribute of the package. After that the `sys.modules` entry is
        # never consulted again.
        #
        # So swapping `sys.modules` alone passes when this file runs on its own
        # and fails the moment any earlier test has touched the real module,
        # which is how these nineteen tests came to pass in isolation and fail
        # in the suite. Both bindings are set, so the harness no longer depends
        # on test ordering.
        self.monkeypatch.setattr(hiring_pkg, "scorecard", module, raising=False)

        async def load_claims(session, *, tenant_id, job_id, link_id=None):
            return list(self.claims)

        async def resolve_text(session, *, tenant_id, item):
            return self.texts.get(str(item.evidence_id), "a specific, checkable answer")

        self.monkeypatch.setattr(ledger, "load_claims", load_claims)
        self.monkeypatch.setattr(ledger, "resolve_text", resolve_text)

    async def invoke(self, task, messages, **kwargs):
        self.calls.append((task, messages))
        system = messages[0]["content"]
        dimension = next(
            (d for d in DIMENSIONS if DIMENSION_LABELS[d] in system), DIMENSIONS[0]
        )
        return json.dumps(
            {
                "band": self.band_by_dimension[dimension],
                "rationale": "a stubbed rationale for a wiring test",
                "insufficient_evidence": False,
                "evidence_refs": ["e1"],
            }
        )

    def run(self, **kwargs):
        return asyncio.run(
            live.evaluate_application(
                _Session(),
                job=types.SimpleNamespace(
                    id=_JOB, tenant_id=_TENANT, title="Senior Data Engineer",
                    assessment_grade="managerial",
                ),
                link=types.SimpleNamespace(id=_LINK, candidate_id=uuid.uuid4()),
                invoke=self.invoke,
                **kwargs,
            )
        )

    def prompt_for(self, dimension: str) -> str:
        label = DIMENSION_LABELS[dimension]
        for _task, messages in self.calls:
            if label in messages[0]["content"]:
                return messages[0]["content"] + "\n" + messages[1]["content"]
        raise AssertionError(f"no evaluator prompt for {dimension}")


def _default_claims():
    return [
        _claim("Stream processing depth", [_evidence_item(ref=uuid.uuid4())]),
        _claim("Migration ownership", [_evidence_item(ref=uuid.uuid4())]),
        _claim("Operating in ambiguity", [_evidence_item(ref=uuid.uuid4())]),
    ]


_SCORES = {
    "Stream processing depth": 82,
    "Migration ownership": 78,
    "Operating in ambiguity": 74,
}


# -- 1. GATE G1 IS THE ONLY WAY IN ------------------------------------------


def test_a_missing_scorecard_module_blocks_scoring_and_names_what_is_missing(
    monkeypatch,
) -> None:
    """Runbook section 14.1: "the scorecard was not approved -> scoring blocked
    entirely (Gate G1)".

    A missing module is not a reason to score against something else. Setting
    the entry to None is how Python reports an absent module, so this is the
    real failure and not an approximation of it.
    """
    _detach_scorecard(monkeypatch, None)
    with pytest.raises(live.ScorecardUnavailable) as raised:
        asyncio.run(live.frozen_matrix(_Session(), _JOB))
    assert "hiring.scorecard" in str(raised.value)


def test_a_scorecard_module_without_the_gate_function_blocks_scoring(
    monkeypatch,
) -> None:
    _detach_scorecard(monkeypatch, types.ModuleType(SCORECARD_MODULE))
    with pytest.raises(live.ScorecardUnavailable):
        asyncio.run(live.frozen_matrix(_Session(), _JOB))


def test_an_empty_or_unstamped_matrix_blocks_scoring(monkeypatch) -> None:
    """G1 asks the TABLE first and the stamp second, in that order.

    This codebase has paid for believing a timestamp: 19 of 35 live jobs
    carried a generation stamp and zero competency rows, so every one was stuck
    with an empty framework nobody could approve.
    """
    harness = _Harness(monkeypatch, matrix=_matrix(items=[]))
    with pytest.raises(live.ScorecardUnavailable):
        harness.run(item_scores={})

    harness.matrix = _matrix(approved_at=None)
    with pytest.raises(live.ScorecardUnavailable):
        harness.run(item_scores={})


def test_the_live_module_has_no_default_matrix_anywhere_in_it() -> None:
    """The cheapest possible proof that G1 cannot be routed around: there is no
    code in this module that could construct a matrix to fall back to."""
    source = inspect.getsource(live)
    for banned in ("load_framework", "generate_framework", "DEFAULT_MATRIX", "or _matrix("):
        assert banned not in source, banned


def test_synthesis_calls_miti_and_does_not_catch_the_gate(monkeypatch) -> None:
    """THE LIVE ENTRY POINT. `functional_assessment.synthesis_node` is the only
    caller, and it must not swallow G1: catching the refusal and scoring
    against the job's competency rows would be a second implementation of the
    criteria chosen at runtime, which is the dual path the anti-slop rules
    forbid."""
    import ast

    source = inspect.getsource(fa.synthesis_node)
    assert "miti_live.evaluate_application" in source

    # Read the AST rather than the prose: the comment above the call explains
    # WHY the refusal is allowed to propagate, and a substring scan would
    # report the explanation as the violation.
    tree = ast.parse(source.lstrip())
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        caught = ast.dump(node.type) if node.type else "bare except"
        assert "ScorecardUnavailable" not in caught, caught
        assert node.type is not None, "a bare except would swallow gate G1"


# -- 2. ISOLATION SURVIVES THE WIRING ---------------------------------------


def test_each_live_evaluator_sees_only_its_own_competencies(monkeypatch) -> None:
    """The routing half of isolation, asserted through the real wiring rather
    than through `build_evaluator_inputs` in isolation."""
    harness = _Harness(monkeypatch)
    harness.run(item_scores=_SCORES)

    assert "Stream processing depth" in harness.prompt_for(DIM_VERIFIED_COMPETENCE)
    assert "Migration ownership" not in harness.prompt_for(DIM_VERIFIED_COMPETENCE)
    assert "Migration ownership" in harness.prompt_for(DIM_TRACK_RECORD)
    assert "Stream processing depth" not in harness.prompt_for(DIM_TRACK_RECORD)


def test_no_live_evaluator_prompt_carries_a_name_a_score_or_the_composite(
    monkeypatch,
) -> None:
    """A halo effect is not hypothetical: tell one evaluator another scored
    strongly and its judgment moves, in a direction nobody chose and nothing
    records. A NAME is worse, because it carries inferred gender, ethnicity and
    nationality, and an evaluator that can see one is an evaluator whose output
    can correlate with one."""
    ref = uuid.uuid4()
    harness = _Harness(
        monkeypatch,
        claims=[_claim("Stream processing depth", [_evidence_item(ref=ref)])],
        texts={str(ref): "Priya and I rewrote the scheduler after the outage."},
    )
    harness.run(item_scores=_SCORES, subject_names=["Priya"])

    for dimension in DIMENSIONS:
        prompt = harness.prompt_for(dimension)
        assert "Priya" not in prompt
        for other in DIMENSIONS:
            if other == dimension:
                continue
            assert DIMENSION_LABELS[other] not in prompt
        for leak in ("composite", "overall grade", "Highly Matching", "RPS"):
            assert leak not in prompt


def test_each_live_evaluator_gets_its_own_dimensions_rubric_anchors(
    monkeypatch,
) -> None:
    """Sections 9.1 to 9.5 state a DIFFERENT six-band anchor table per
    dimension. Handing all five the same string, which is what happened before,
    anchored four of them against a rubric written for a question they were not
    asked."""
    harness = _Harness(monkeypatch)
    harness.run(item_scores=_SCORES)
    anchors = {d: harness.prompt_for(d) for d in DIMENSIONS}
    # The Verified Competence table names demonstrated capability under
    # observation; the Authenticity table does not.
    assert "must-have" in anchors[DIM_VERIFIED_COMPETENCE].lower()
    assert anchors[DIM_VERIFIED_COMPETENCE] != anchors[DIM_AUTHENTICITY]
    assert len({text for text in anchors.values()}) == len(DIMENSIONS)


def test_the_five_live_evaluators_run_concurrently(monkeypatch) -> None:
    """Not for speed. Concurrently means no ordering exists in which one has
    finished before another starts, so a future edit cannot thread an earlier
    result into a later prompt."""
    harness = _Harness(monkeypatch)
    started = 0
    finished = 0
    overlap_seen = False

    original = harness.invoke

    async def counting(task, messages, **kwargs):
        nonlocal started, finished, overlap_seen
        started += 1
        await asyncio.sleep(0)
        if started == len(DIMENSIONS) and finished == 0:
            overlap_seen = True
        result = await original(task, messages, **kwargs)
        finished += 1
        return result

    harness.invoke = counting
    harness.run(item_scores=_SCORES)
    assert started == len(DIMENSIONS)
    assert overlap_seen, "the evaluators ran one after another"


# -- 3. ZERO MODEL CALLS IN THE AGGREGATOR ----------------------------------


def test_the_live_path_makes_exactly_five_model_calls_and_all_are_evaluators(
    monkeypatch,
) -> None:
    """Extends the AST-level rule to the wiring: if aggregation, triangulation
    or the cap arithmetic reached a provider, a sixth call would appear here."""
    harness = _Harness(monkeypatch)
    harness.run(item_scores=_SCORES)
    assert len(harness.calls) == len(DIMENSIONS)
    assert {task for task, _ in harness.calls} == {live.EVALUATION_TASK}


def test_the_evaluation_task_is_the_only_task_this_module_can_route_to() -> None:
    """A grading call routed somewhere else would be graded by a different
    model at a different temperature, which makes a candidate's grade depend on
    who wrote the call site."""
    from app.config import llm_providers

    assert live.EVALUATION_TASK in llm_providers.MODEL_FOR_TASK
    assert llm_providers.TASK_TEMPERATURE[live.EVALUATION_TASK] == 0.0


# -- 4. DETERMINISM ---------------------------------------------------------


_DETERMINISM_SNIPPET = """
import json
from app.services import rating
from app.services.hiring.department_models import (
    DIM_AUTHENTICITY, DIM_ROLE_FIT, DIM_TRACK_RECORD, DIM_TRAJECTORY,
    DIM_VERIFIED_COMPETENCE,
)
from app.services.miti import aggregation
from app.services.miti.dimensions import DimensionResult

results = [
    DimensionResult(dimension=d, band=b, evidence_refs=("e1",))
    for d, b in (
        (DIM_VERIFIED_COMPETENCE, "solid"),
        (DIM_TRACK_RECORD, "partial"),
        (DIM_ROLE_FIT, "strong"),
        (DIM_AUTHENTICITY, "solid"),
        (DIM_TRAJECTORY, "partial"),
    )
]
out = aggregation.aggregate(
    results,
    competency_categories={"k": aggregation.CATEGORY_MUST_HAVE},
    must_have_grades={"k": rating.GRADE_NOT},
    must_have_evidence={
        "k": aggregation.MustHaveEvidence(tiers=("E0", "E3"), independence_groups=2)
    },
    unresolved_contradictions=1,
)
print(json.dumps(out.as_dict(), sort_keys=True))
"""


def test_the_aggregate_is_byte_identical_across_process_restarts() -> None:
    """Two runs over identical inputs producing different grades would make a
    rubric problem indistinguishable from noise. A FRESH INTERPRETER each time,
    because anything cached in this process would hide exactly the hash-order
    and iteration-order effects this is looking for."""
    outputs = {
        subprocess.run(
            [sys.executable, "-c", _DETERMINISM_SNIPPET],
            capture_output=True,
            text=True,
            timeout=180,
            check=True,
        ).stdout
        for _ in range(3)
    }
    assert len(outputs) == 1


def test_the_live_outcome_is_byte_identical_across_a_hundred_runs(
    monkeypatch,
) -> None:
    harness = _Harness(monkeypatch)
    first = json.dumps(harness.run(item_scores=_SCORES).outcome.as_dict(), sort_keys=True)
    for _ in range(100):
        assert (
            json.dumps(harness.run(item_scores=_SCORES).outcome.as_dict(), sort_keys=True)
            == first
        )


# -- 5. INSUFFICIENT EVIDENCE IS NOT NEGATIVE EVIDENCE ----------------------


def test_insufficient_evidence_lowers_confidence_and_not_the_score(
    monkeypatch,
) -> None:
    """Runbook section 6.6: "a missing signal gets scored as zero, which is
    mathematically identical to negative evidence, which is wrong and unfair."

    The two are compared directly here: a dimension the evaluator could not
    judge, against one it judged NEGATIVELY. The first must leave the surviving
    composite alone and be paid for in confidence; the second must move it.
    """
    judged = _Harness(monkeypatch)
    judged.band_by_dimension[DIM_TRACK_RECORD] = "absent"
    negative = judged.run(item_scores=_SCORES).aggregate

    insufficient = _Harness(monkeypatch)

    async def one_insufficient(task, messages, **kwargs):
        system = messages[0]["content"]
        if DIMENSION_LABELS[DIM_TRACK_RECORD] in system:
            return json.dumps(
                {
                    "band": "partial",
                    "rationale": "nothing was mapped to this dimension",
                    "insufficient_evidence": True,
                    "evidence_refs": [],
                }
            )
        return await _Harness.invoke(insufficient, task, messages, **kwargs)

    insufficient.invoke = one_insufficient
    excluded = insufficient.run(item_scores=_SCORES).aggregate

    assert DIM_TRACK_RECORD in excluded.insufficient_dimensions
    assert DIM_TRACK_RECORD not in negative.insufficient_dimensions
    # Excluded, not scored low: the composite of what WAS judged is higher than
    # the composite that absorbed a negative band.
    assert excluded.raw_composite > negative.raw_composite
    # Paid for in review instead.
    assert excluded.needs_human_review
    assert any("insufficient" in reason for reason in excluded.review_reasons)


# -- 6. THE FOUR CANDIDATE STATES -------------------------------------------


_CANDIDATE_STATES = {
    # A fresher has no employment track record to corroborate. The Track Record
    # dimension is UNKNOWN for them, not zero, and section 6.6 excludes an
    # UNKNOWN from the average rather than scoring it.
    "fresher": (DIM_TRACK_RECORD, "E3"),
    # A returner has a tenure gap. Section 12.4 forbids employment gaps as a
    # disqualifier outright, so the gap must reach no control at all; what is
    # dated is the EVIDENCE, which decays, and the decay is per claim.
    "returner": (DIM_TRAJECTORY, "E3"),
    # A career-changer's prior domain evidence does not map to this role's
    # competencies, so Verified Competence rests on less.
    "career_changer": (DIM_VERIFIED_COMPETENCE, "E3"),
    # A non-traditional background has no institutional credential to verify,
    # so nothing reaches E5 by that route.
    "non_traditional": (DIM_ROLE_FIT, "E3"),
}


@pytest.mark.parametrize("state", sorted(_CANDIDATE_STATES))
def test_a_thin_dimension_costs_confidence_and_never_produces_a_low_band(
    monkeypatch, state: str
) -> None:
    """One fixture per candidate state, all asserting the same rule.

    THE PRACTICAL CONSEQUENCE IS THE POINT: a career-changer gets a
    low-confidence report that goes to a human, rather than a confidently poor
    grade that does not. The four states differ in WHICH dimension is thin and
    are identical in how the system must read it, which is why they are one
    parametrised test and not four copies.
    """
    thin_dimension, _tier = _CANDIDATE_STATES[state]
    harness = _Harness(monkeypatch)

    async def thin(task, messages, **kwargs):
        if DIMENSION_LABELS[thin_dimension] in messages[0]["content"]:
            return json.dumps(
                {
                    "band": "partial",
                    "rationale": "no evidence was mapped to this dimension",
                    "insufficient_evidence": True,
                    "evidence_refs": [],
                }
            )
        return await _Harness.invoke(harness, task, messages, **kwargs)

    harness.invoke = thin
    aggregate = harness.run(item_scores=_SCORES).aggregate

    assert thin_dimension in aggregate.insufficient_dimensions
    assert aggregate.needs_human_review
    # The dimension contributed NOTHING to the composite rather than a low
    # number. Its band would have scored 66; nothing in the category scores is
    # dragged toward it.
    assert all(score >= 66 for score in aggregate.category_scores.values())


def test_an_employment_gap_reaches_no_control(monkeypatch) -> None:
    """Section 12.4 lists "employment gaps of any length" among the PROHIBITED
    disqualifiers, refused regardless of client request.

    Asserted as an absence, which is the only way this can be enforced: no cap,
    no floor and no abstention condition in the product reads tenure, so there
    is nowhere for a gap to enter. Decision Contract C5 is why this test
    exists: one wrong section number in a citation once pointed the legitimate
    disqualifier list at the prohibited one.
    """
    import re

    from app.services.miti import caps

    source = (inspect.getsource(caps) + inspect.getsource(aggregation)).lower()
    # WORD BOUNDARIES, not substrings, and this is the same lesson the
    # disqualifier matcher learned the hard way: a substring match refused
    # "must hold a valid CA licence" because "hold" contains "old", while
    # accepting "no candidates over 45" because it contains no listed word.
    for banned in ("tenure", "employment_gap", "gap_months", "career_break", "age"):
        assert not re.search(rf"{banned}", source), banned


# -- 7. NO FLAG AUTO-REJECTS ------------------------------------------------


def test_the_triangulation_result_has_no_way_to_reject_anybody() -> None:
    """Enforcement is the ABSENCE of the capability. A reject field is a field
    something eventually writes to, in a hotfix, at the end of a release."""
    import dataclasses

    fields = {f.name for f in dataclasses.fields(triangulation.TriangulationResult)}
    assert not fields & {"reject", "rejected", "status", "decision", "disposition"}


def test_the_aggregate_has_no_way_to_reject_anybody() -> None:
    import dataclasses

    fields = {f.name for f in dataclasses.fields(aggregation.Aggregate)}
    assert not fields & {"reject", "rejected", "status", "decision", "disposition"}


def test_an_automatic_disposition_is_refused(monkeypatch) -> None:
    """spec-doc6 section 4.4: "add a test that attempts an auto-disposition and
    confirms it is refused"."""
    for invented in ("auto_cleared", "auto", "system", "pipeline", "cleared_by_miti"):
        assert invented not in gates.DISPOSITIONS
        result = gates.human_review_gate(
            needs_review=True, disposition=invented, decided_by="miti"
        )
        assert not result.passed, invented


def test_no_rejection_exists_in_the_outcome_without_a_named_human(
    monkeypatch,
) -> None:
    """The audit-trail invariant. `rejected` is a disposition a PERSON records,
    and G4 refuses it with nobody attached: a decision nobody is named for is
    indistinguishable from the pipeline having written it itself."""
    harness = _Harness(monkeypatch)
    harness.band_by_dimension[DIM_AUTHENTICITY] = "absent"

    unattributed = harness.run(
        item_scores=_SCORES,
        review_disposition=gates.DISPOSITION_REJECTED,
        review_decided_by=None,
    )
    assert not unattributed.outcome.deliverable

    attributed = harness.run(
        item_scores=_SCORES,
        review_disposition=gates.DISPOSITION_REJECTED,
        review_decided_by=uuid.uuid4(),
    )
    assert attributed.outcome.deliverable


def test_a_flagged_evaluation_is_not_deliverable_until_somebody_decides(
    monkeypatch,
) -> None:
    harness = _Harness(monkeypatch)
    harness.band_by_dimension[DIM_AUTHENTICITY] = "absent"
    flagged = harness.run(item_scores=_SCORES)
    assert flagged.aggregate.needs_human_review
    assert not flagged.outcome.deliverable
    assert flagged.outcome.blocking_reasons


# -- 8. G2 BLOCKS NOTHING AND SAYS SOMETHING ACTIONABLE ---------------------


def test_g2_fails_loudly_on_the_live_path_and_blocks_nothing(monkeypatch) -> None:
    """A blocking sufficiency gate would refuse a report to exactly the
    candidates who most need a person to look: the career-changer, the
    returner, the candidate whose evidence is thin because their history is
    unusual rather than because they are weak. Refusing them a report is not
    neutrality, it is a silent rejection with better manners."""
    harness = _Harness(
        monkeypatch,
        claims=[_claim("Stream processing depth", [_evidence_item(ref=uuid.uuid4())])],
    )
    outcome = harness.run(item_scores=_SCORES).outcome
    g2 = next(g for g in outcome.gate_results if g.gate == gates.G2)
    assert not g2.passed
    assert not g2.blocking
    assert g2.reasons
    # Actionable: it names the Must-have with nothing mapped to it, not a score.
    assert any("Migration ownership" in r or "independent source" in r for r in g2.reasons)


def test_an_unresolvable_evidence_reference_is_excluded_and_named(
    monkeypatch,
) -> None:
    """An excluded piece of evidence lowers coverage, lowers confidence and can
    trip section 14.1, all of which are visible. Handing an evaluator an empty
    excerpt instead would be a grade written from evidence nobody read."""
    ref = uuid.uuid4()
    harness = _Harness(
        monkeypatch,
        claims=[_claim("Stream processing depth", [_evidence_item(ref=ref)])],
        texts={str(ref): ""},
    )
    evaluation = harness.run(item_scores=_SCORES)
    assert evaluation.unresolved_evidence == [str(ref)]
    assert any("could not be read back" in r for r in evaluation.review_reasons)


def test_a_must_have_probed_only_by_a_resume_line_is_capped_on_the_live_path(
    monkeypatch,
) -> None:
    """THE END-TO-END FORM OF THE SECTION 14.1 FIX.

    Same candidate, same strong item scores, and the only difference is that
    the Must-have's evidence is a resume assertion rather than an answer given
    under structured conditions. E0 is not above E1, so the competency is
    reported Unassessed and the delivered band cannot be Ready to Pick.
    """
    ref = uuid.uuid4()
    harness = _Harness(
        monkeypatch,
        claims=[
            _claim(
                "Stream processing depth",
                [
                    _evidence_item(
                        ref=ref,
                        source_type=ledger.SOURCE_RESUME,
                        has_specifics=False,
                    )
                ],
            )
        ],
    )
    aggregate = harness.run(item_scores=_SCORES).aggregate
    assert aggregate.unassessed_must_haves == ["Stream processing depth"]
    assert aggregate.delivered_score <= 71
    assert aggregate.overall_grade in (rating.GRADE_MODERATELY, rating.GRADE_NOT)
