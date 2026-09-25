"""Turning sandbox results into what the product stores. No I/O.

Two shapes, because the two kinds of test have opposite disclosure rules:

  VISIBLE (a Run, or the sample tests run beside a final submission). The
  candidate wrote the program and read the tests, so stdout, stderr and the
  compiler's output may be kept, truncated, and shown back to them.

  HIDDEN (a final submission). Only the OUTCOME WORD and the resource figures
  of each test are kept. Stdout and stderr are dropped here, before anything
  is stored or logged, because a candidate program can echo its stdin and a
  hidden test's stdin is part of the answer key. The comparison with the
  expected output happens inside `keys.AnswerKey.passed`, so this module never
  holds an expected hidden output either.

A SANDBOX FAULT IS NOT A FAILED TEST. `INTERNAL_ERROR` on any test, or a
result set that does not cover every input, raises `SandboxFault`: the run
tells us nothing about the candidate, so it is retried, never graded.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from app.models.coding import CODING_OUTPUT_EXCERPT_MAX_CHARS
from app.services.code_execution import ExecutionOutcome, TestExecution, outputs_match
from app.services.coding_assessment.keys import AnswerKey

__all__ = [
    "TEST_PASSED",
    "TEST_WRONG_ANSWER",
    "TEST_COMPILE_ERROR",
    "TEST_RUNTIME_ERROR",
    "TEST_TIME_LIMIT",
    "TEST_MEMORY_LIMIT",
    "TEST_OUTPUT_LIMIT",
    "TEST_OUTCOMES",
    "FAILURE_OUTCOMES",
    "SandboxFault",
    "HiddenSummary",
    "excerpt",
    "outcome_counts",
    "outcome_word",
    "summarise_hidden",
    "visible_results",
    "first_visible_error",
]

#: The per-test outcome vocabulary the product stores. `passed` and
#: `wrong_answer` are decided by comparison; the rest are how the run ended.
TEST_PASSED = "passed"
TEST_WRONG_ANSWER = "wrong_answer"
TEST_COMPILE_ERROR = ExecutionOutcome.COMPILE_ERROR.value
TEST_RUNTIME_ERROR = ExecutionOutcome.RUNTIME_ERROR.value
TEST_TIME_LIMIT = ExecutionOutcome.TIME_LIMIT.value
TEST_MEMORY_LIMIT = ExecutionOutcome.MEMORY_LIMIT.value
TEST_OUTPUT_LIMIT = ExecutionOutcome.OUTPUT_LIMIT.value
TEST_OUTCOMES: tuple[str, ...] = (
    TEST_PASSED,
    TEST_WRONG_ANSWER,
    TEST_COMPILE_ERROR,
    TEST_RUNTIME_ERROR,
    TEST_TIME_LIMIT,
    TEST_MEMORY_LIMIT,
    TEST_OUTPUT_LIMIT,
)
#: Every outcome that is not a pass, in the order a sentence names them.
FAILURE_OUTCOMES: tuple[str, ...] = TEST_OUTCOMES[1:]


class SandboxFault(RuntimeError):
    """The sandbox's answer says nothing about the candidate. Retry it.

    `reason` is a short machine word, safe to store and log.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def excerpt(text: str | None, limit: int = CODING_OUTPUT_EXCERPT_MAX_CHARS) -> str:
    """At most `limit` characters, cut at the end. Never None."""
    text = text or ""
    return text if len(text) <= limit else text[:limit]


def _by_key(results: Iterable[TestExecution], expected_keys: Sequence[str]) -> dict[str, TestExecution]:
    by_key = {result.key: result for result in results}
    if set(by_key) != set(expected_keys):
        raise SandboxFault("incomplete_results")
    for result in by_key.values():
        if result.outcome is ExecutionOutcome.INTERNAL_ERROR:
            raise SandboxFault("internal_error")
    return by_key


def outcome_word(result: TestExecution, passed: bool) -> str:
    """The stored word for one run. `passed` is the comparison's verdict and
    only matters when the program finished normally."""
    if result.outcome is ExecutionOutcome.OK:
        return TEST_PASSED if passed else TEST_WRONG_ANSWER
    return result.outcome.value



@dataclass(frozen=True)
class HiddenSummary:
    """What a final submission's hidden tests did. Content-free by type.

    `results` rows are `{key, outcome, passed, cpu_ms, wall_ms, memory_kb}`:
    no stdin, no stdout, no stderr. `compile_output` is the compiler's own
    message, which is derived from the candidate's code alone (compilation
    precedes any input), so it cannot quote a hidden test.
    """

    tests_total: int
    tests_passed: int
    results: tuple[dict[str, Any], ...]
    compile_output: str | None

    @property
    def outcome_counts(self) -> dict[str, int]:
        return outcome_counts(self.results)


def outcome_counts(results: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """How many tests ended each way, keyed by outcome word, zeros omitted."""
    counts: dict[str, int] = {}
    for row in results:
        word = str(row.get("outcome"))
        counts[word] = counts.get(word, 0) + 1
    return counts


def summarise_hidden(results: Iterable[TestExecution], key: AnswerKey) -> HiddenSummary:
    """Judge every hidden test against the answer key, keeping no output."""
    by_key = _by_key(results, key.keys)
    rows: list[dict[str, Any]] = []
    passed_count = 0
    compile_output: str | None = None
    for test_key in key.keys:
        result = by_key[test_key]
        passed = result.outcome is ExecutionOutcome.OK and key.passed(test_key, result.stdout)
        passed_count += int(passed)
        if result.outcome is ExecutionOutcome.COMPILE_ERROR and compile_output is None:
            compile_output = excerpt(result.compile_output)
        rows.append(
            {
                "key": test_key,
                "outcome": outcome_word(result, passed),
                "passed": passed,
                "cpu_ms": result.cpu_ms,
                "wall_ms": result.wall_ms,
                "memory_kb": result.memory_kb,
            }
        )
    return HiddenSummary(
        tests_total=len(rows),
        tests_passed=passed_count,
        results=tuple(rows),
        compile_output=compile_output,
    )


def visible_results(
    results: Iterable[TestExecution], expected: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Per VISIBLE test, in `expected` order: the outcome word and the
    program's own output, truncated. Public tests, so the output may be kept.
    Timings are deliberately not kept: a candidate sees words, not numbers."""
    by_key = _by_key(results, list(expected))
    rows: list[dict[str, Any]] = []
    for test_key, expected_stdout in expected.items():
        result = by_key[test_key]
        passed = result.outcome is ExecutionOutcome.OK and outputs_match(result.stdout, expected_stdout)
        rows.append(
            {
                "key": test_key,
                "outcome": outcome_word(result, passed),
                "passed": passed,
                "stdout": excerpt(result.stdout),
                "stderr": excerpt(result.stderr),
                "compile_output": excerpt(result.compile_output),
            }
        )
    return rows


def first_visible_error(rows: Sequence[Mapping[str, Any]]) -> str | None:
    """The stderr of the first visible test that ended in a runtime error.

    The one error message a recruiter may read beside a final submission: it
    comes from a PUBLIC input, so it cannot carry a hidden test.
    """
    for row in rows:
        if row.get("outcome") == TEST_RUNTIME_ERROR and row.get("stderr"):
            return excerpt(str(row["stderr"]))
    return None
