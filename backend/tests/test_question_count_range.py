"""Sutra sets the RANGE, Vaada decides where inside it the conversation ends.

The change this pins (2026-08-23). Setup used to resolve ONE number and the
conversation asked exactly that many questions whatever the candidate said. The
specification splits the decision: Sutra "sets the total question-count range
for the role's candidate assessment", agent-decided with no manual override, and
Vaada decides "the actual count ... dynamically during the conversation itself,
based on answer depth and completeness".

The dangerous direction is the dynamic half becoming a way to shorten an
assessment. Two candidates on one job must stay comparable, so the FLOOR and the
substantive-answer requirement are the two properties most of this file is
about.
"""
import pytest

from app.services import ppi


GRADES = ("non_managerial", "managerial", "leadership", "cxo")


# ── Sutra's half: the range ─────────────────────────────────────────────────


@pytest.mark.parametrize("grade", GRADES)
def test_the_range_is_never_wider_than_the_grades_own_band(grade):
    """The band is the client's own table and a job may not escape it."""
    low, high = ppi.resolve_question_range(grade, 500)
    band_low, band_high = ppi.GRADE_QUESTION_RANGES[grade]
    assert low >= band_low
    assert high <= band_high


@pytest.mark.parametrize("grade", GRADES)
def test_a_small_matrix_still_asks_the_grade_minimum(grade):
    """A four-item matrix must not become a four-question interview: the report
    grades every item, and a thin matrix is a reason to probe each item harder,
    not a reason to stop early."""
    low, high = ppi.resolve_question_range(grade, 1)
    assert low == ppi.min_questions(grade)
    assert high >= low


@pytest.mark.parametrize("grade", GRADES)
def test_a_large_matrix_asks_one_question_per_item_up_to_the_ceiling(grade):
    """Every item the report grades should actually have been probed."""
    ceiling = ppi.max_questions(grade)
    _, high = ppi.resolve_question_range(grade, ceiling)
    assert high == ceiling


def test_the_range_is_a_pure_function_of_grade_and_matrix_size():
    """No manual override, and no per-candidate input. Two candidates on one job
    must be offered the same range or their reports are not comparable."""
    assert ppi.resolve_question_range("managerial", 18) == ppi.resolve_question_range(
        "managerial", 18
    )


def test_the_legacy_target_is_the_ceiling_of_the_range():
    """`question_target` is a persisted column and a shipped API field, so it
    keeps its name. What changed is its MEANING: the most the conversation may
    ask, rather than the number it will ask."""
    for grade in GRADES:
        for size in (1, 5, 12, 40):
            assert ppi.resolve_question_target(grade, size) == (
                ppi.resolve_question_range(grade, size)[1]
            )


def test_questions_are_written_to_the_ceiling_not_the_floor():
    """Generation happens once, before the candidate starts. Writing only the
    floor would leave a conversation that legitimately needs more evidence with
    no further prompt to reach for."""
    low, high = ppi.resolve_question_range("non_managerial", 40)
    assert ppi.resolve_question_target("non_managerial", 40) == high
    assert high > low


# ── Vaada's half: the stopping rule ─────────────────────────────────────────


def _close(**overrides):
    kwargs = dict(
        grade="non_managerial",
        asked=ppi.min_questions("non_managerial"),
        total_written=ppi.max_questions("non_managerial"),
        covered_dimensions=8,
        total_dimensions=8,
    )
    kwargs.update(overrides)
    return ppi.conversation_may_close(**kwargs)


def test_it_may_close_once_every_dimension_has_evidence_and_the_floor_is_met():
    assert _close() is True


@pytest.mark.parametrize("grade", GRADES)
def test_it_never_closes_below_the_grade_floor(grade):
    """THE load-bearing assertion. Without the floor a fluent candidate is
    assessed on fewer criteria than a hesitant one, and two reports on the same
    job stop being comparable, which is the one property the matrix exists to
    give."""
    floor = ppi.min_questions(grade)
    assert (
        ppi.conversation_may_close(
            grade=grade,
            asked=floor - 1,
            total_written=ppi.max_questions(grade),
            covered_dimensions=6,
            total_dimensions=6,
        )
        is False
    )


def test_it_does_not_close_while_a_dimension_is_unprobed():
    """A dimension with no evidence is not a dimension that scored badly, it is
    one nobody asked about. The report must never present those as the same."""
    assert _close(covered_dimensions=7, total_dimensions=8) is False


def test_an_empty_matrix_never_closes_early():
    """Zero dimensions means the coverage question is unanswerable, and the safe
    direction on an unanswerable question is to keep going."""
    assert _close(covered_dimensions=0, total_dimensions=0) is False


def test_running_out_of_questions_is_not_this_functions_decision():
    """`asked >= total_written` is ordinary completion, handled by the caller.
    This function answers the EARLY-stop question only, so it must not also
    claim the last question as an early stop."""
    total = ppi.max_questions("non_managerial")
    assert _close(asked=total, total_written=total) is False


def test_the_stopping_rule_calls_no_model():
    """Same reason every guard here is deterministic: the moment it matters most
    is the moment the provider is down. A model asked "have you gathered
    enough?" during an outage returns nothing, and the safe answer to nothing
    must be "keep asking", not "stop"."""
    import inspect

    source = inspect.getsource(ppi.conversation_may_close)
    for forbidden in ("invoke_llm", "chat_completion", "llm_router", "await "):
        assert forbidden not in source, forbidden


def test_a_closed_conversation_stays_inside_sutras_range():
    """The property the two halves exist to give jointly: whatever the candidate
    says, the number of base questions lands in the job's range."""
    for grade in GRADES:
        low, high = ppi.resolve_question_range(grade, 40)
        for asked in range(0, high + 2):
            if ppi.conversation_may_close(
                grade=grade,
                asked=asked,
                total_written=high,
                covered_dimensions=5,
                total_dimensions=5,
            ):
                assert low <= asked < high, (grade, asked, low, high)
