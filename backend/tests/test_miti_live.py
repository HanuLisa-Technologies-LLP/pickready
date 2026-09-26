"""Miti on the LIVE path: the wiring, not the modules.

`test_miti_pipeline.py` asserts the properties of each stage in isolation. This
file asserts them of the path a real candidate's report is written from, which
is the distinction spec-doc6 section 4.4 draws when it says the existing
AST-level test "stays and is extended to cover the live wiring, not just the
module". Until 2026-08-29 the whole Part A stack was reachable from exactly one
file, `app/scripts/worked_example.py`, and from no route or worker: every gate
was a real check guarding nothing.

What is asserted here:

  1. GATE G1 IS THE ONLY WAY IN. No locked contract, no scoring, no default.
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

import pytest

from app.services import assessment_contract
from app.services import functional_assessment as fa
from app.services import rating
from app.services.evidence import ledger
from app.services.hiring import gates
from app.services.miti import aggregation, items, live, triangulation
from app.services.miti.dimensions import (
    DIM_AUTHENTICITY,
    DIM_ROLE_FIT,
    DIM_TRACK_RECORD,
    DIM_TRAJECTORY,
    DIM_VERIFIED_COMPETENCE,
    DIMENSIONS,
    DIMENSION_LABELS,
)
from tests import miti_fixtures as mf

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


#: The contract every harness run grades against unless a test says otherwise:
#: one skill per bucket, locked, as `load_contract_for_conversation` returns it.
_SKILLS = (
    ("Stream processing depth", "must_have"),
    ("Migration ownership", "nice_to_have"),
    ("Operating in ambiguity", "behavioural"),
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


class _Harness:
    """Installs a contract, the item grades, a ledger and a recording `invoke`.

    The contract is what `load_contract_for_conversation` returns and the item
    grades are what `items.evaluate_skills` returns; both are stubbed at the
    function the live module calls, so G1's gate, Miti's digest line and the
    whole evaluator wiring still run for real. The item stage has its own
    tests (`test_miti_items.py`); this file is about the wiring after it.
    """

    def __init__(self, monkeypatch, *, claims=None, texts=None, contract=None):
        self.monkeypatch = monkeypatch
        self.calls: list[tuple[str, list[dict[str, str]]]] = []
        self.claims = claims if claims is not None else _default_claims()
        self.texts = texts or {}
        self.contract = contract if contract is not None else mf.contract(_SKILLS)
        self.band_by_dimension: dict[str, str] = {d: "solid" for d in DIMENSIONS}
        self.disposition: tuple[str | None, object] = (None, None)
        self.item_stage_runs = 0
        self._install()

    def _install(self) -> None:
        async def load_contract_for_conversation(session, conversation_id):
            return self.contract

        self.monkeypatch.setattr(
            assessment_contract,
            "load_contract_for_conversation",
            load_contract_for_conversation,
        )

        async def latest_disposition(session, link_id):
            return self.disposition

        self.monkeypatch.setattr(live, "latest_disposition", latest_disposition)

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

    def run(
        self,
        *,
        item_scores=None,
        review_disposition=None,
        review_decided_by=None,
        **kwargs,
    ):
        scores = dict(_SCORES if item_scores is None else item_scores)
        self.disposition = (review_disposition, review_decided_by)

        async def evaluate_skills(session, *, context, skills, **_ignored):
            self.item_stage_runs += 1
            return tuple(
                mf.skill(skill.name, skill.bucket, scores.get(skill.name), priority=skill.priority)
                for skill in skills
            )

        self.monkeypatch.setattr(items, "evaluate_skills", evaluate_skills)
        return asyncio.run(
            live.evaluate_application(
                _Session(),
                job=types.SimpleNamespace(
                    id=_JOB, tenant_id=_TENANT, title="Senior Data Engineer",
                    assessment_grade="managerial",
                ),
                link=types.SimpleNamespace(id=_LINK, candidate_id=uuid.uuid4()),
                conversation_id=uuid.uuid4(),
                questions=[],
                answers={},
                locators={},
                structured={},
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


def test_the_result_carries_the_contract_it_graded_against(monkeypatch) -> None:
    """An evaluation is a permanent record of the criteria it was run against,
    so the result names the contract (version and digest) rather than leaving
    the writer to look the job up again later."""
    harness = _Harness(monkeypatch)

    result = harness.run(item_scores=_SCORES)

    assert result.aggregate is not None
    assert result.contract is harness.contract
    assert result.contract_version == harness.contract.version
    assert result.contract_digest == harness.contract.digest
    assert [grade.name for grade in result.skills] == [name for name, _ in _SKILLS]


# -- 1. GATE G1 IS THE ONLY WAY IN ------------------------------------------


@pytest.mark.parametrize(
    "refusal",
    [assessment_contract.ContractIntegrityError, assessment_contract.ContractNotBound],
)
def test_a_contract_nobody_can_prove_blocks_scoring_before_any_model_call(
    monkeypatch, refusal
) -> None:
    """A digest mismatch between the conversation and its snapshot, or a started
    conversation with no binding, is refused by the one read API and surfaces
    as G1: `ScorecardUnavailable`, before the item stage and before any
    evaluator. Falling back to the job's live skills would grade a candidate
    against criteria that may have moved since they were asked."""
    harness = _Harness(monkeypatch)

    async def refuse(session, conversation_id):
        raise refusal("the stored digest does not match the snapshot")

    monkeypatch.setattr(assessment_contract, "load_contract_for_conversation", refuse)
    with pytest.raises(live.ScorecardUnavailable):
        harness.run(item_scores=_SCORES)
    assert harness.calls == []
    assert harness.item_stage_runs == 0


@pytest.mark.parametrize(
    "contract",
    [
        mf.contract(_SKILLS, locked=False),
        mf.contract(()),
        mf.contract((("Stream processing depth", "must_have"),)),
        mf.contract((("Operating in ambiguity", "behavioural"),)),
    ],
    ids=["unlocked", "empty", "no_behavioural", "no_must_have"],
)
def test_a_contract_g1_would_not_pass_blocks_scoring(monkeypatch, contract) -> None:
    """G1 asks the CONTRACT: locked, non-empty, at least one Must-have and one
    Behavioural skill. A stamp is not evidence that work happened."""
    harness = _Harness(monkeypatch, contract=contract)
    with pytest.raises(live.ScorecardUnavailable):
        harness.run(item_scores=_SCORES)
    assert harness.item_stage_runs == 0
    assert harness.calls == []


def test_miti_logs_its_digest_line_for_the_conversation(monkeypatch, caplog) -> None:
    """The Miti half of "Vaada and Miti read the same contract": the one
    structured line, `stage=miti`, carrying the contract digest."""
    import logging

    harness = _Harness(monkeypatch)
    with caplog.at_level(logging.INFO, logger="app.services.assessment_contract"):
        harness.run(item_scores=_SCORES)
    lines = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("assessment_contract.digest")
    ]
    assert len(lines) == 1
    assert "stage=miti" in lines[0]
    assert f"contract_digest={harness.contract.digest}" in lines[0]


def test_the_live_module_has_no_default_matrix_anywhere_in_it() -> None:
    """The cheapest possible proof that G1 cannot be routed around: there is no
    code in this module that could construct a matrix to fall back to."""
    source = inspect.getsource(live)
    for banned in (
        "load_framework",
        "generate_framework",
        "DEFAULT_MATRIX",
        "require_frozen_matrix(",
        "assessment_contract.load_contract(",
    ):
        assert banned not in source, banned


def test_scoring_calls_miti_once_and_does_not_catch_the_gate(monkeypatch) -> None:
    """THE LIVE ENTRY POINT. `assessment_pipeline.grading.grade` is the only
    caller (PLAN-p5 WP5-D), the orchestrator calls it once and composition
    reads its result rather than running Miti a second time, and none of them
    may swallow G1: catching the refusal and scoring against the job's live
    skills would be a second implementation of the criteria chosen at
    runtime, which is the dual path the anti-slop rules forbid."""
    import ast

    from app.services.assessment_pipeline import composition, grading

    assert "miti_live.evaluate_application" in inspect.getsource(grading.grade)
    assert "evaluate_application" not in inspect.getsource(composition)
    assert inspect.getsource(fa.run_assessment).count("grading.grade(") == 1

    # Read the AST rather than the prose: a comment explaining WHY the refusal
    # propagates must not read as the violation.
    for function in (grading.grade, fa.run_assessment):
        tree = ast.parse(inspect.getsource(function).lstrip())
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

    # Must-have and Nice-to-have skills are Verified Competence's; the
    # Behavioural skill is Role and Context Fit's.
    assert "Stream processing depth" in harness.prompt_for(DIM_VERIFIED_COMPETENCE)
    assert "Migration ownership" in harness.prompt_for(DIM_VERIFIED_COMPETENCE)
    assert "Operating in ambiguity" not in harness.prompt_for(DIM_VERIFIED_COMPETENCE)
    assert "Operating in ambiguity" in harness.prompt_for(DIM_ROLE_FIT)
    assert "Stream processing depth" not in harness.prompt_for(DIM_ROLE_FIT)
    # Track Record is cross-cutting and reads every skill.
    for name, _bucket in _SKILLS:
        assert name in harness.prompt_for(DIM_TRACK_RECORD)


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
from app.services.miti import aggregation
from app.services.miti.dimensions import (
    DIM_AUTHENTICITY, DIM_ROLE_FIT, DIM_TRACK_RECORD, DIM_TRAJECTORY,
    DIM_VERIFIED_COMPETENCE, DimensionResult,
)
from tests import miti_fixtures as mf

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
    skill_grades=(
        mf.skill("k", "must_have", 40),
        mf.skill("j", "nice_to_have", 88, priority=1),
        mf.skill("b", "behavioural", 79),
    ),
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
    judge, against one it judged NEGATIVELY. Since WP5-B the composite is Miti's
    skill grades alone, so an evaluator moves the DELIVERED score only through
    the controls it feeds: a negative Verified Competence breaches section
    12.2's D1 floor and caps; an insufficient one is excluded and caps nothing.
    """
    judged = _Harness(monkeypatch)
    judged.band_by_dimension[DIM_VERIFIED_COMPETENCE] = "absent"
    negative = judged.run(item_scores=_SCORES).aggregate

    insufficient = _Harness(monkeypatch)

    async def one_insufficient(task, messages, **kwargs):
        system = messages[0]["content"]
        if DIMENSION_LABELS[DIM_VERIFIED_COMPETENCE] in system:
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

    assert DIM_VERIFIED_COMPETENCE in excluded.insufficient_dimensions
    assert DIM_VERIFIED_COMPETENCE not in negative.insufficient_dimensions
    # The evaluators grade nothing: the composite is the skill grades in both.
    assert excluded.raw_composite == negative.raw_composite
    # Excluded, not scored low: only the negative band trips the floor.
    assert excluded.delivered_score > negative.delivered_score
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
    import ast
    import re

    from app.services.miti import caps

    # CODE, not prose: every identifier and every non-docstring string
    # literal. A comment recording that a citation once "authorised filtering
    # on age" is the history of this rule, not a control reading age. Until
    # 2026-09-26 the pattern below carried literal backspace characters where
    # the word boundaries belonged, so it matched nothing and passed on any
    # source at all.
    words: list[str] = []
    for module in (caps, aggregation):
        tree = ast.parse(inspect.getsource(module))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                words.append(node.id)
            elif isinstance(node, ast.Attribute):
                words.append(node.attr)
            elif isinstance(node, ast.arg):
                words.append(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                words.append(node.arg)
            elif isinstance(node, ast.alias):
                words.extend(name for name in (node.name, node.asname) if name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                words.append(node.name)
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                words.append(node.value)
    # An underscore separates words here. `\b` counts it as a word character,
    # so without this `tenure_months` or `max_age` would slip past a boundary
    # match on "tenure" or "age".
    source = "\n".join(words).lower().replace("_", " ")
    # WORD BOUNDARIES, not substrings, and this is the same lesson the
    # disqualifier matcher learned the hard way: a substring match refused
    # "must hold a valid CA licence" because "hold" contains "old", while
    # accepting "no candidates over 45" because it contains no listed word.
    for banned in ("tenure", "employment_gap", "gap_months", "career_break", "age"):
        phrase = banned.replace("_", " ")
        assert not re.search(rf"\b{phrase}\b", source), banned


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
