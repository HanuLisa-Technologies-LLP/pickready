"""Coding results in WORDS. No digit ever leaves this module.

A recruiter reads what a final coding answer did, and a candidate reads what
their Run did; both are under the rule that no number reaches a client. CONTRACT
v2 rules how a report states hidden-test results: counts SPELLED OUT ("passed
seven of the ten hidden tests"), the proctoring report's precedent, never a
digit, a percentage or a pass rate. The pass rate itself stays internal.

A FIXED CATALOGUE, NEVER A MODEL. Every sentence here is assembled from the
stored outcome words by code, so the sentence cannot claim a result the table
does not hold, and it is the same sentence on the report, on the transcript
and in the PDF. `tests/test_coding_scoring_and_evidence.py` sweeps every shape
for digits, the em dash and `siddhi.numbers.scan_text`.

Nothing here can name a hidden test's content: its inputs are counts and
outcome words, and that is all a sentence can be built from.
"""
from __future__ import annotations

from typing import Mapping

from app.models.coding import HIDDEN_TESTS_MAX
from app.services.coding_assessment.execution import (
    TEST_COMPILE_ERROR,
    TEST_MEMORY_LIMIT,
    TEST_OUTPUT_LIMIT,
    TEST_PASSED,
    TEST_RUNTIME_ERROR,
    TEST_TIME_LIMIT,
    TEST_WRONG_ANSWER,
)

__all__ = [
    "RESULT_WORDS",
    "NOT_ANSWERED_SENTENCE",
    "PENDING_SENTENCE",
    "UNAVAILABLE_SENTENCE",
    "number_word",
    "result_word",
    "outcome_sentence",
]

_NUMBER_WORDS: tuple[str, ...] = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "twenty-one",
    "twenty-two", "twenty-three", "twenty-four", "twenty-five", "twenty-six",
    "twenty-seven", "twenty-eight", "twenty-nine", "thirty",
)
if len(_NUMBER_WORDS) - 1 < HIDDEN_TESTS_MAX:
    raise RuntimeError("phrasing cannot spell every count the database allows")

#: What one test's outcome is called where a person reads it (the Run panel).
RESULT_WORDS: dict[str, str] = {
    TEST_PASSED: "Passed",
    TEST_WRONG_ANSWER: "Wrong answer",
    TEST_COMPILE_ERROR: "Did not compile",
    TEST_RUNTIME_ERROR: "Runtime error",
    TEST_TIME_LIMIT: "Time limit exceeded",
    TEST_MEMORY_LIMIT: "Memory limit exceeded",
    TEST_OUTPUT_LIMIT: "Output limit exceeded",
}

#: How a failure is described inside a sentence, singular then plural.
_FAILURE_CLAUSES: dict[str, tuple[str, str]] = {
    TEST_COMPILE_ERROR: ("did not compile", "did not compile"),
    TEST_WRONG_ANSWER: ("gave a wrong answer", "gave wrong answers"),
    TEST_RUNTIME_ERROR: ("stopped with a runtime error", "stopped with runtime errors"),
    TEST_TIME_LIMIT: ("exceeded the time limit", "exceeded the time limit"),
    TEST_MEMORY_LIMIT: ("exceeded the memory limit", "exceeded the memory limit"),
    TEST_OUTPUT_LIMIT: ("printed more output than allowed", "printed more output than allowed"),
}

NOT_ANSWERED_SENTENCE = "No code was submitted for this question."
PENDING_SENTENCE = "The submitted code is still being checked."
UNAVAILABLE_SENTENCE = (
    "The code runner was unavailable, so this answer was not assessed."
)


def number_word(count: int) -> str:
    """A count as a word. Raises for a count the product can never hold."""
    if not 0 <= count < len(_NUMBER_WORDS):
        raise ValueError("count is outside what a coding result can hold")
    return _NUMBER_WORDS[count]


def result_word(outcome: str) -> str:
    """The word for one test's outcome. KeyError for an unknown outcome: a
    word invented for a state nobody defined would be a label with no meaning."""
    return RESULT_WORDS[outcome]


def _failure_clause(counts: Mapping[str, int], *, tests_total: int) -> str:
    """How the failing tests failed: "two exceeded the time limit and one gave
    a wrong answer". When every test failed the same way it says "all ten".
    Raises when the counts name no failure, because a sentence explaining
    failures nobody recorded would be invented."""
    present = [(outcome, counts[outcome]) for outcome in _FAILURE_CLAUSES if counts.get(outcome)]
    if not present:
        raise ValueError("failing hidden tests carry no recorded failure outcome")
    if len(present) == 1 and present[0][1] == tests_total and tests_total > 1:
        outcome, count = present[0]
        return f"all {number_word(count)} {_FAILURE_CLAUSES[outcome][1]}"
    parts = [
        f"{number_word(count)} {_FAILURE_CLAUSES[outcome][0 if count == 1 else 1]}"
        for outcome, count in present
    ]
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _only_failure(counts: Mapping[str, int]) -> str:
    for outcome, (singular, _plural) in _FAILURE_CLAUSES.items():
        if counts.get(outcome):
            return singular
    raise ValueError("the failing hidden test carries no recorded failure outcome")


def outcome_sentence(
    *,
    state: str,
    tests_total: int | None = None,
    tests_passed: int | None = None,
    outcome_counts: Mapping[str, int] | None = None,
) -> str:
    """One sentence stating what a final coding answer did.

    `state` is an `evidence` state word. Only `complete` needs the counts; the
    other states have one fixed sentence each.
    """
    from app.services.coding_assessment import evidence

    if state == evidence.STATE_NOT_ANSWERED:
        return NOT_ANSWERED_SENTENCE
    if state == evidence.STATE_PENDING:
        return PENDING_SENTENCE
    if state == evidence.STATE_UNAVAILABLE:
        return UNAVAILABLE_SENTENCE
    if state != evidence.STATE_COMPLETE:
        raise ValueError(f"unknown coding evidence state {state!r}")
    if tests_total is None or tests_passed is None or not 1 <= tests_total or not 0 <= tests_passed <= tests_total:
        raise ValueError("a complete coding result needs consistent hidden-test counts")
    counts = dict(outcome_counts or {})
    failed = tests_total - tests_passed

    if counts.get(TEST_COMPILE_ERROR, 0) == tests_total:
        return "The code did not compile, so no hidden test could pass."
    if tests_total == 1:
        if tests_passed == 1:
            return "Passed the only hidden test."
        return f"Did not pass the only hidden test: it {_only_failure(counts)}."
    total = number_word(tests_total)
    if failed == 0:
        return f"Passed all {total} hidden tests."
    clause = _failure_clause(counts, tests_total=tests_total)
    if tests_passed == 0:
        return f"Passed none of the {total} hidden tests: {clause}."
    return f"Passed {number_word(tests_passed)} of the {total} hidden tests; {clause}."
