"""THE ONLY module that reads or writes `coding_question_keys`.

The hidden tests and the reference solution of a coding question are its
answer key. Everything that touches them goes through here, so "who can see a
hidden test" has one answer that a reviewer can read in one place, and
`tests/test_coding_key_confinement.py` fails the build when any other module
under `app/` names the table or its model.

WHAT LEAVES THIS MODULE
-----------------------
* `AnswerKey.test_inputs()`: key and stdin per hidden test, for the sandbox.
  Never an expected output; the sandbox does not grade.
* `AnswerKey.passed(key, stdout)`: whether one program output answers one
  hidden test. The expected output is compared HERE and never handed out, so
  the executor that asks the question never holds the answer.
* `review_notes(...)`: the reviewer's approach notes. They describe the
  solution, never a test.

The reference solution is written (it is how the question was validated, and
keeping it makes the validation reproducible) and is never read back by the
application. Nothing here logs content: log lines carry question ids and
counts only. Every text field of the dataclasses below is `repr=False`, so an
exception message or a debugging print of an `AnswerKey` cannot quote a test.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.coding import CODE_SOURCE_MAX_CHARS, HIDDEN_TESTS_MAX, CodingQuestionKey
from app.services.code_execution import TestInput, outputs_match
from app.services.coding_assessment.payload import TEST_STDIN_MAX_CHARS, hidden_test_key

logger = logging.getLogger(__name__)

__all__ = [
    "HiddenTest",
    "AnswerKey",
    "KeyNotFound",
    "KeyIntegrityError",
    "hidden_digest",
    "stage_key",
    "load_answer_key",
    "review_notes",
]

_HIDDEN_KEY = re.compile(r"^h[1-9][0-9]?$")


class KeyNotFound(LookupError):
    """The question has no answer key: it is not a v2 coding question."""


class KeyIntegrityError(RuntimeError):
    """The stored hidden tests no longer match the digest they were validated
    under. Grading against them would grade against a key nobody validated."""


@dataclass(frozen=True)
class HiddenTest:
    """One hidden test. Its content never appears in a repr."""

    __test__ = False

    key: str
    stdin: str = field(repr=False)
    expected_stdout: str = field(repr=False)

    def __post_init__(self) -> None:
        if not _HIDDEN_KEY.match(self.key):
            raise ValueError(f"a hidden test key must look like h1, h2, ...; got {self.key!r}")
        if len(self.stdin) > TEST_STDIN_MAX_CHARS:
            raise ValueError(f"hidden test {self.key} has an oversized stdin")
        if not self.expected_stdout.strip():
            raise ValueError(f"hidden test {self.key} expects no output")


def hidden_digest(tests: Iterable[HiddenTest]) -> str:
    """sha256 over a canonical encoding of the hidden tests, in order.

    Stored in `validation_json` when the key is written and recomputed on every
    read, so a key that changed after it was validated is refused rather than
    graded against.
    """
    canonical = json.dumps(
        [[test.key, test.stdin, test.expected_stdout] for test in tests],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AnswerKey:
    """The hidden tests of one question, able to judge but not to disclose."""

    question_id: uuid.UUID
    tests: tuple[HiddenTest, ...] = field(repr=False)

    @property
    def count(self) -> int:
        return len(self.tests)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(test.key for test in self.tests)

    def test_inputs(self) -> tuple[TestInput, ...]:
        """What the sandbox receives: key and stdin. No expected output."""
        return tuple(TestInput(key=test.key, stdin=test.stdin) for test in self.tests)

    def passed(self, key: str, stdout: str) -> bool:
        """Whether `stdout` answers hidden test `key`. Raises KeyError for an
        unknown key: a result for a test this question does not have is a
        defect upstream, not a failed test."""
        for test in self.tests:
            if test.key == key:
                return outputs_match(stdout, test.expected_stdout)
        raise KeyError(f"question {self.question_id} has no hidden test {key!r}")


def _encode(tests: tuple[HiddenTest, ...]) -> list[dict[str, str]]:
    return [
        {"key": test.key, "stdin": test.stdin, "expected_stdout": test.expected_stdout}
        for test in tests
    ]


def _decode(rows: Any, *, question_id: uuid.UUID) -> tuple[HiddenTest, ...]:
    if not isinstance(rows, list):
        raise KeyIntegrityError(f"question {question_id} has a malformed hidden test list")
    try:
        return tuple(
            HiddenTest(key=row["key"], stdin=row["stdin"], expected_stdout=row["expected_stdout"])
            for row in rows
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise KeyIntegrityError(
            f"question {question_id} has a malformed hidden test ({type(exc).__name__})"
        ) from exc


def stage_key(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    tenant_id: uuid.UUID,
    tests: tuple[HiddenTest, ...],
    reference_language: str,
    reference_source: str,
    expected_approach: str,
    validation: dict[str, Any],
) -> CodingQuestionKey:
    """Add the answer key of one question to the session. The caller flushes.

    Refuses a key that could never be graded against: keys out of order, too
    many tests, an empty or oversized reference, or a validation record whose
    hidden digest does not describe these tests. Every check here is also a
    database CHECK or a generation rule; repeating them means a caller that
    built a key by hand fails with a sentence instead of a constraint name.
    """
    if not 1 <= len(tests) <= HIDDEN_TESTS_MAX:
        raise ValueError(f"a coding question needs 1 to {HIDDEN_TESTS_MAX} hidden tests")
    expected_keys = [hidden_test_key(position) for position in range(1, len(tests) + 1)]
    if [test.key for test in tests] != expected_keys:
        raise ValueError(f"hidden test keys must be {expected_keys} in order")
    if not reference_source.strip() or len(reference_source) > CODE_SOURCE_MAX_CHARS:
        raise ValueError("the reference solution is empty or oversized")
    if not reference_language.strip():
        raise ValueError("the reference solution has no language")
    if not expected_approach.strip():
        raise ValueError("the reviewer's approach notes are empty")
    if validation.get("hidden_digest") != hidden_digest(tests):
        raise ValueError("the validation record does not describe these hidden tests")
    row = CodingQuestionKey(
        question_id=question_id,
        tenant_id=tenant_id,
        hidden_tests_json=_encode(tests),
        reference_language=reference_language,
        reference_source=reference_source,
        expected_approach=expected_approach,
        validation_json=validation,
    )
    session.add(row)
    logger.info(
        "coding_keys.staged question_id=%s hidden_tests=%d", question_id, len(tests)
    )
    return row


async def _row(session: AsyncSession, question_id: uuid.UUID) -> CodingQuestionKey:
    row = (
        await session.execute(
            select(CodingQuestionKey).where(CodingQuestionKey.question_id == question_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise KeyNotFound(f"question {question_id} has no coding answer key")
    return row


async def load_answer_key(session: AsyncSession, question_id: uuid.UUID) -> AnswerKey:
    """The hidden tests of one question, verified against their digest.

    Reads through the caller's session, so the tenant boundary is the RLS
    policy on `coding_question_keys`, exactly as for every other table.
    """
    row = await _row(session, question_id)
    tests = _decode(row.hidden_tests_json, question_id=question_id)
    stored_digest = (row.validation_json or {}).get("hidden_digest")
    if stored_digest != hidden_digest(tests):
        logger.error("coding_keys.digest_mismatch question_id=%s", question_id)
        raise KeyIntegrityError(
            f"the hidden tests of question {question_id} do not match the digest "
            "they were validated under"
        )
    return AnswerKey(question_id=question_id, tests=tests)


async def review_notes(session: AsyncSession, question_id: uuid.UUID) -> str:
    """The approach notes the code-quality reviewer reads. Never a test."""
    return (await _row(session, question_id)).expected_approach
