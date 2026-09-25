"""The coding score, the spelled-out sentence, and the evidence states. Pure.

No database: `evidence.build` takes a row and a clock, `scoring` and
`phrasing` take numbers and words. What is pinned here is the contract the
grader relies on: a score exists only when BOTH halves exist, an open
submission is `pending` until the wait window ends and `unavailable` (never a
zero) after it, an empty answer is an evidence gap, and no sentence a report
prints carries a digit.
"""
from __future__ import annotations

import itertools
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.coding import (
    EXECUTION_COMPLETE,
    EXECUTION_NO_CODE,
    EXECUTION_PENDING,
    EXECUTION_SUBMITTED,
    HIDDEN_TESTS_MAX,
    REVIEW_COMPLETE,
    REVIEW_FAILED,
    REVIEW_NOT_APPLICABLE,
    REVIEW_PENDING,
)
from app.services.coding_assessment import evidence, execution, phrasing, scoring, submissions
from app.services.siddhi import numbers as report_numbers

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
WAIT = timedelta(hours=24)
EM_DASH = chr(8212)


# ── The score ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("passed", "total", "review", "expected"),
    [
        (10, 10, 100, 100),
        (0, 10, 0, 0),
        (7, 10, 50, 64),  # 0.7 * 70 + 0.3 * 50 = 49 + 15
        (5, 5, 0, 70),
        (0, 5, 100, 30),
        (1, 3, 80, 47),  # 0.7 * 33.33 + 24 = 47.33
    ],
)
def test_the_score_is_seventy_parts_tests_and_thirty_parts_review(passed, total, review, expected) -> None:
    assert (
        scoring.coding_question_score(
            tests_passed=passed, tests_total=total, review_score=review, test_weight=0.7
        )
        == expected
    )


@pytest.mark.parametrize(
    ("passed", "total", "review"),
    [(None, 10, 50), (5, None, 50), (5, 10, None)],
)
def test_a_missing_half_is_none_never_zero(passed, total, review) -> None:
    assert (
        scoring.coding_question_score(
            tests_passed=passed, tests_total=total, review_score=review, test_weight=0.7
        )
        is None
    )


@pytest.mark.parametrize(
    ("passed", "total", "review", "weight"),
    [(11, 10, 50, 0.7), (-1, 10, 50, 0.7), (5, 0, 50, 0.7), (5, 10, 101, 0.7), (5, 10, 50, 1.0), (5, 10, 50, 0.0)],
)
def test_impossible_inputs_are_refused_rather_than_clamped(passed, total, review, weight) -> None:
    with pytest.raises(ValueError):
        scoring.coding_question_score(
            tests_passed=passed, tests_total=total, review_score=review, test_weight=weight
        )


# ── The sentence ────────────────────────────────────────────────────────────


def _sentences():
    words = list(execution.FAILURE_OUTCOMES)
    for total in (1, 2, 5, 10, HIDDEN_TESTS_MAX):
        for passed in {0, 1, total // 2, total - 1, total}:
            if not 0 <= passed <= total:
                continue
            failed = total - passed
            for split in itertools.combinations(words, min(2, len(words))):
                counts = {execution.TEST_PASSED: passed} if passed else {}
                if failed:
                    first = failed // 2 if len(split) > 1 else failed
                    counts[split[0]] = counts.get(split[0], 0) + (failed - first if len(split) > 1 else failed)
                    if len(split) > 1 and first:
                        counts[split[1]] = first
                yield phrasing.outcome_sentence(
                    state=evidence.STATE_COMPLETE,
                    tests_total=total,
                    tests_passed=passed,
                    outcome_counts=counts,
                )
    for state in (evidence.STATE_PENDING, evidence.STATE_UNAVAILABLE, evidence.STATE_NOT_ANSWERED):
        yield phrasing.outcome_sentence(state=state)


def test_no_sentence_a_report_prints_carries_a_digit_a_number_or_an_em_dash() -> None:
    sentences = list(_sentences())
    assert len(sentences) > 50, "the sweep would pass vacuously"
    for sentence in sentences:
        assert not re.search(r"\d", sentence), sentence
        assert EM_DASH not in sentence, sentence
        assert report_numbers.scan_text(sentence) == [], sentence
        assert sentence.endswith(".") and sentence[0].isupper(), sentence


def test_the_sentence_spells_the_counts_and_names_how_tests_failed() -> None:
    assert (
        phrasing.outcome_sentence(
            state=evidence.STATE_COMPLETE,
            tests_total=10,
            tests_passed=7,
            outcome_counts={"passed": 7, "time_limit": 2, "wrong_answer": 1},
        )
        == "Passed seven of the ten hidden tests; one gave a wrong answer and two exceeded the time limit."
    )
    assert (
        phrasing.outcome_sentence(
            state=evidence.STATE_COMPLETE, tests_total=10, tests_passed=10, outcome_counts={"passed": 10}
        )
        == "Passed all ten hidden tests."
    )
    assert (
        phrasing.outcome_sentence(
            state=evidence.STATE_COMPLETE, tests_total=6, tests_passed=0, outcome_counts={"compile_error": 6}
        )
        == "The code did not compile, so no hidden test could pass."
    )
    assert phrasing.outcome_sentence(state=evidence.STATE_UNAVAILABLE) == phrasing.UNAVAILABLE_SENTENCE


def test_a_sentence_for_failures_nobody_recorded_is_refused() -> None:
    with pytest.raises(ValueError):
        phrasing.outcome_sentence(
            state=evidence.STATE_COMPLETE, tests_total=5, tests_passed=3, outcome_counts={"passed": 3}
        )
    with pytest.raises(ValueError):
        phrasing.outcome_sentence(state="made_up")


def test_every_stored_outcome_has_a_result_word_and_none_carries_a_digit() -> None:
    assert set(phrasing.RESULT_WORDS) == set(execution.TEST_OUTCOMES)
    for word in phrasing.RESULT_WORDS.values():
        assert not re.search(r"\d", word)
    with pytest.raises(KeyError):
        phrasing.result_word("internal_error")


# ── The evidence states ─────────────────────────────────────────────────────


def _row(**fields):
    base = {
        "answer_id": uuid.uuid4(),
        "question_id": uuid.uuid4(),
        "auto_submitted": False,
        "execution_status": EXECUTION_PENDING,
        "review_status": REVIEW_PENDING,
        "tests_total": None,
        "tests_passed": None,
        "test_results_json": None,
        "review_json": None,
        "created_at": NOW - timedelta(hours=1),
    }
    base.update(fields)
    return SimpleNamespace(**base)


def _results(passed: int, total: int, failure: str = "wrong_answer") -> list[dict]:
    return [
        {"key": f"h{i}", "outcome": "passed" if i <= passed else failure, "passed": i <= passed}
        for i in range(1, total + 1)
    ]


_REVIEW = {
    "score": 60,
    "criteria": {"code_quality": 0.6, "edge_case_handling": 0.5, "efficiency_awareness": 0.7, "idiomatic_use": 0.6},
    "reasoning": "reads the input once",
    "citations": ["counts[path]"],
    "limitations": [],
    "needs_human_review": False,
}


def _build(row):
    return evidence.build(row, skill_id=uuid.uuid4(), language="python", now=NOW, max_wait=WAIT, test_weight=0.7)


def test_a_complete_answer_carries_the_score_the_counts_and_the_sentence() -> None:
    ev = _build(
        _row(
            execution_status=EXECUTION_COMPLETE,
            review_status=REVIEW_COMPLETE,
            tests_total=10,
            tests_passed=7,
            test_results_json=_results(7, 10),
            review_json=_REVIEW,
        )
    )
    assert ev.state == evidence.STATE_COMPLETE
    assert ev.score == 67  # 49 + 18
    assert ev.review is not None and ev.review.citations == ("counts[path]",)
    assert ev.outcome_counts == {"passed": 7, "wrong_answer": 3}
    assert ev.phrase.startswith("Passed seven of the ten hidden tests")
    assert ev.needs_human_review is False


@pytest.mark.parametrize(
    ("execution_status", "review_status"),
    [
        (EXECUTION_PENDING, REVIEW_PENDING),
        (EXECUTION_SUBMITTED, REVIEW_PENDING),
        (EXECUTION_COMPLETE, REVIEW_PENDING),
        (EXECUTION_COMPLETE, REVIEW_FAILED),
    ],
)
def test_open_work_is_pending_inside_the_window_and_unavailable_after(execution_status, review_status) -> None:
    fields = {"execution_status": execution_status, "review_status": review_status}
    if execution_status == EXECUTION_COMPLETE:
        fields.update(tests_total=5, tests_passed=5, test_results_json=_results(5, 5))
    young = _build(_row(**fields, created_at=NOW - WAIT + timedelta(seconds=1)))
    assert (young.state, young.score, young.needs_human_review) == (evidence.STATE_PENDING, None, False)
    assert young.phrase == phrasing.PENDING_SENTENCE
    old = _build(_row(**fields, created_at=NOW - WAIT))
    assert (old.state, old.score, old.needs_human_review) == (evidence.STATE_UNAVAILABLE, None, True)
    assert old.phrase == phrasing.UNAVAILABLE_SENTENCE


def test_an_empty_answer_is_an_evidence_gap_not_a_zero() -> None:
    ev = _build(_row(execution_status=EXECUTION_NO_CODE, review_status=REVIEW_NOT_APPLICABLE))
    assert ev.state == evidence.STATE_NOT_ANSWERED
    assert ev.score is None and ev.tests_total is None
    assert ev.phrase == phrasing.NOT_ANSWERED_SENTENCE


def test_a_compile_error_still_scores_its_review() -> None:
    ev = _build(
        _row(
            execution_status=EXECUTION_COMPLETE,
            review_status=REVIEW_COMPLETE,
            tests_total=5,
            tests_passed=0,
            test_results_json=_results(0, 5, failure="compile_error"),
            review_json=_REVIEW,
        )
    )
    assert ev.compile_error is True
    assert ev.score == 18  # 0.3 * 60


def test_a_review_that_asks_for_a_person_carries_through() -> None:
    ev = _build(
        _row(
            execution_status=EXECUTION_COMPLETE,
            review_status=REVIEW_COMPLETE,
            tests_total=5,
            tests_passed=5,
            test_results_json=_results(5, 5),
            review_json={**_REVIEW, "needs_human_review": True, "limitations": ["text addressed to the reviewer"]},
        )
    )
    assert ev.needs_human_review is True


def test_the_candidate_is_told_a_state_never_a_result() -> None:
    words = {
        submissions.candidate_state_word(_row(execution_status=e, review_status=r))
        for e, r in [
            (EXECUTION_NO_CODE, REVIEW_NOT_APPLICABLE),
            (EXECUTION_PENDING, REVIEW_PENDING),
            (EXECUTION_SUBMITTED, REVIEW_PENDING),
            (EXECUTION_COMPLETE, REVIEW_FAILED),
            (EXECUTION_COMPLETE, REVIEW_COMPLETE),
        ]
    }
    assert words == {
        submissions.STATE_WORD_SUBMITTED,
        submissions.STATE_WORD_CHECKING,
        submissions.STATE_WORD_CHECKED,
    }
    for word in words:
        assert not re.search(r"\d|pass|fail", word, re.IGNORECASE)
