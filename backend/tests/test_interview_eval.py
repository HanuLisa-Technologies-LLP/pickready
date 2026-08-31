"""The interview agent's evaluation, run as a test so CI gates on it.

`app/scripts/eval_interview.py` measures the agent's JUDGEMENT across a labelled
set: does it catch a non-answer, does it leave a real answer alone, does it
degrade to the product's previous behaviour when the model is down, does it keep
a rubric-bound question intact, does it avoid repeating itself, does it strip
praise, is its budget reproducible.

Every one of those is a promise the product makes. A promise with no threshold
is a promise nobody is defending, which is exactly how the canned
acknowledgments and the four unchallenged keyboard-mash answers reached
production with a green pipeline.

THE THRESHOLDS ARE WHERE THEY ARE TODAY, NOT WHERE ANYONE WISHES THEY WERE.
All of them are 100% because every measured behaviour is deterministic given a
stubbed model. If one of them ever needs to be lowered to go green, that is a
product decision and belongs in a commit message, not in a quiet edit here.
"""
from __future__ import annotations

import pytest

from app.scripts import eval_interview


@pytest.mark.asyncio
async def test_the_agent_evaluation_passes_in_full() -> None:
    results = await eval_interview.run()
    assert results, "the eval measured nothing at all"

    failing = [item for item in results if item.rate < 1.0]
    assert not failing, eval_interview.report(results)


@pytest.mark.asyncio
async def test_the_eval_actually_exercises_the_agent() -> None:
    """Guards the eval itself.

    An eval that silently measures zero cases passes forever and protects
    nothing. This is the check that would have caught a refactor quietly
    emptying the labelled set.
    """
    results = await eval_interview.run()
    by_name = {item.name: item for item in results}

    for name in (
        "non_answer_detection",
        "real_answer_not_challenged",
        "degrades_safely",
        "question_integrity",
        "no_praise",
        "deterministic_budget",
        # Added 2026-08-29. `evidence_graph.py` had ZERO importers, so the
        # department graph was present in the codebase and reachable by nothing
        # while every unit test on the module passed. This measurement is the
        # number that moves if that happens again.
        "department_evidence_graph",
        "specificity_gradient",
    ):
        assert name in by_name, f"the {name} measurement disappeared"
        assert by_name[name].total > 0, f"{name} measured zero cases"

    # A role from every one of Part VI's fifteen departments would be ideal and
    # is not what this asserts: what it asserts is that the set cannot quietly
    # shrink to one easy case.
    assert len(eval_interview.ROLES_BY_DEPARTMENT) >= 12
    assert len(eval_interview.ROLES_OUTSIDE_PART_VI) >= 3

    # The production strings that started all of this must stay in the set.
    assert by_name["non_answer_detection"].total >= len(eval_interview.NON_ANSWERS)
    for mash in ("fsjdemd", "xdshfjg,uyytrs"):
        assert mash in eval_interview.NON_ANSWERS


@pytest.mark.asyncio
async def test_the_eval_restores_the_model_it_stubbed() -> None:
    """A stub left installed would silently break every test that ran after
    this one in the same process, and the failure would look like a bug in
    whatever ran next."""
    from app.services import answer_classification, interviewer

    before = (
        answer_classification.llm_router.invoke_llm,
        interviewer.llm_router.invoke_llm,
    )
    await eval_interview.run()
    assert (
        answer_classification.llm_router.invoke_llm,
        interviewer.llm_router.invoke_llm,
    ) == before
