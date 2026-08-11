"""The report and matching evaluation, run as a test so CI gates on it.

`app/scripts/eval_report.py` measures the two paths whose bad output is most
expensive and least visible: the AI matching that decides reading order, and
the PPI Assessment Report that is the deliverable. It measures SHAPE -- grade
boundaries, word bands in every branch including the outage fallbacks, no
number reaching a client, no third-party instrument named, culture refused,
question counts by grade -- because shape is the part CI can defend. Whether a
remark is INSIGHTFUL needs a live model and a human, and pretending otherwise
would be the same false confidence these evals exist to remove.

THE THRESHOLDS ARE WHERE THEY ARE TODAY, NOT WHERE ANYONE WISHES THEY WERE.
All of them are 100% because every measured behaviour is deterministic. If one
ever needs to be lowered to go green, that is a product decision and belongs in
a commit message, not in a quiet edit here.
"""
from __future__ import annotations

import pytest

from app.scripts import eval_report


@pytest.mark.asyncio
async def test_the_report_evaluation_passes_in_full() -> None:
    results = await eval_report.run()
    assert results, "the eval measured nothing at all"

    failing = [item for item in results if item.rate < 1.0]
    assert not failing, eval_report.report(results)


@pytest.mark.asyncio
async def test_the_eval_actually_exercises_the_agents() -> None:
    """Guards the eval itself.

    An eval that silently measures zero cases passes forever and protects
    nothing. Named measurements, with a floor on the case count, so a refactor
    that empties a labelled set fails here rather than going quietly green.
    """
    results = await eval_report.run()
    by_name = {item.name: item for item in results}

    for name in (
        "grade_boundaries",
        "one_rating_scale",
        "ranking_order",
        "no_weightage_table",
        "matching_remark_words",
        "ppi_remark_words",
        "no_third_party_instrument",
        "no_numbers_to_a_client",
        "radar_has_no_visible_numbers",
        "culture_competency_refused",
        "question_counts_by_grade",
        "unanswered_is_not_matching",
        "probe_anchors",
        "no_report_reuse",
    ):
        assert name in by_name, f"the {name} measurement disappeared"
        assert by_name[name].total > 0, f"{name} measured zero cases"

    total = sum(item.total for item in results)
    assert total >= 100, f"the labelled set shrank to {total} cases"


@pytest.mark.asyncio
async def test_the_banned_instrument_check_works_in_both_directions() -> None:
    """The detector is worthless if it only ever says no.

    A sweep that fires on "discuss" or "oceanic" would reject legitimate copy,
    and the second time that happens someone turns it off. Both directions are
    measured inside the eval; this asserts the helper directly so a change to
    the regex fails with a readable message.
    """
    assert eval_report._banned_in("Scored against the MBTI profile") == "MBTI"
    assert eval_report._banned_in("A Hogan-style inventory") == "Hogan"
    assert eval_report._banned_in("We discussed the trade-offs") is None
    assert eval_report._banned_in("Oceanic freight experience") is None


@pytest.mark.asyncio
async def test_the_number_check_does_not_mangle_technical_content() -> None:
    """`no numbers to a client` is about SCORES, not about arithmetic.

    "How did you bring p99 latency under 200ms?" is an ordinary interview
    question. The hard part is the distinction, not the detection, and a guard
    that flags a real question fails invisibly.
    """
    assert eval_report.SCORE_SHAPED.search("scored 87%")
    assert eval_report.SCORE_SHAPED.search("rated 7/10")
    assert eval_report.SCORE_SHAPED.search("placed in band 3")
    assert not eval_report.SCORE_SHAPED.search(
        "Brought p99 latency under 200ms across 12 services"
    )
    assert not eval_report.SCORE_SHAPED.search("Led a team of 6 engineers")
