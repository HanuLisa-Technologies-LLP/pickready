"""The v2 coding payload: what a candidate may see of a coding question.

WHAT CHANGED FROM v1, AND WHY THE VERSION IS IN THE PAYLOAD
-----------------------------------------------------------
A v1 coding payload (`assessment_formats.types.CodingPayload`) was ONE
language, ONE starter string and an `expected_approach` for a reviewer who
READ the code; nothing ever ran it. A v2 question is executed: it offers every
configured language with a starter program for each, publishes the sample
tests the candidate can Run against, and freezes the per-language execution
limits it was validated under. `payload_version: 2` is how a reader tells the
two apart, and v1 rows stay readable exactly as they were written.

CANDIDATE-SAFE BY CONSTRUCTION
------------------------------
Every field of `CodingPayloadV2` is something the candidate is meant to see,
and the model FORBIDS extra keys. So the answer key (hidden tests, the
reference solution, the reviewer's approach notes) cannot be stored in a valid
v2 payload at all: it lives in `coding_question_keys`, and the database
refuses a coding payload that carries it
(`ck_candidate_questions_coding_key_private`, migration 0124).
`candidate_projection` is still an explicit field list, so a field added to
the model later is absent from the candidate's view until somebody names it.

THE STATEMENT IS NOT IN THE PAYLOAD. It is `candidate_questions.prompt`, the
column every surface already renders as the question, so there is one copy of
the text the candidate read.

LIMITS ARE FROZEN AT GENERATION. The reference solution was validated under
these exact limits, and a later settings change must not move them under a
question that has already been issued: the Run and Submit paths build their
`ExecutionLimits` from here (`limits_for`), never from the live settings.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import CODE_EXECUTION_LANGUAGE_KEYS
from app.services.code_execution import ExecutionLimits, TestInput

__all__ = [
    "PAYLOAD_VERSION",
    "IO_STDIN_STDOUT",
    "CODING_REVIEW_CRITERIA",
    "TITLE_MAX_CHARS",
    "FORMAT_MAX_CHARS",
    "STARTER_MAX_CHARS",
    "TEST_STDIN_MAX_CHARS",
    "EXPLANATION_MAX_CHARS",
    "VISIBLE_TESTS_CEILING",
    "CANDIDATE_FIELDS",
    "VisibleTest",
    "LanguageLimits",
    "CodingPayloadV2",
    "is_v2",
    "parse",
    "candidate_projection",
    "limits_for",
    "visible_inputs",
    "visible_test_key",
    "hidden_test_key",
]

PAYLOAD_VERSION = 2
#: The only problem shape the product executes: the program reads standard
#: input and prints to standard output. A per-language function harness would
#: be a second implementation of "run the candidate's code" per language.
IO_STDIN_STDOUT = "stdin_stdout"

#: What the code-quality review judges, beside the hidden tests. Stored on the
#: question's `rubric_json` so a review written today still says what it was
#: written against if the list changes.
CODING_REVIEW_CRITERIA: tuple[str, ...] = (
    "code_quality",
    "edge_case_handling",
    "efficiency_awareness",
    "idiomatic_use",
)

TITLE_MAX_CHARS = 120
FORMAT_MAX_CHARS = 2000
STARTER_MAX_CHARS = 4000
#: One test's standard input. Large enough for a meaningful case, small
#: enough that a test is a check rather than a benchmark.
TEST_STDIN_MAX_CHARS = 8000
EXPLANATION_MAX_CHARS = 300
#: The model's ceiling; `coding_visible_tests_max` sits at or under it.
VISIBLE_TESTS_CEILING = 5

#: Exactly what `candidate_projection` returns, in order. Pinned by a test.
CANDIDATE_FIELDS: tuple[str, ...] = (
    "payload_version",
    "title",
    "io",
    "input_format",
    "output_format",
    "constraints",
    "languages",
    "starter_code",
    "visible_tests",
    "limits",
)

_VISIBLE_KEY = re.compile(r"^v([1-9][0-9]?)$")


def visible_test_key(position: int) -> str:
    """The opaque key of the Nth visible test, counting from one."""
    return f"v{position}"


def hidden_test_key(position: int) -> str:
    """The opaque key of the Nth hidden test, counting from one."""
    return f"h{position}"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VisibleTest(_Strict):
    """One sample test. Public: the candidate reads it and may Run against it."""

    id: str
    stdin: str = Field(max_length=TEST_STDIN_MAX_CHARS)
    expected_stdout: str = Field(min_length=1)
    explanation: str = Field(default="", max_length=EXPLANATION_MAX_CHARS)

    @field_validator("id")
    @classmethod
    def _visible_key(cls, value: str) -> str:
        if not _VISIBLE_KEY.match(value):
            raise ValueError(f"a visible test id must look like v1, v2, ...; got {value!r}")
        return value

    @field_validator("expected_stdout")
    @classmethod
    def _prints_something(cls, value: str) -> str:
        # A test that expects no output is passed by a program that does
        # nothing, which is what every starter program does.
        if not value.strip():
            raise ValueError("a visible test must expect some output")
        return value


class LanguageLimits(_Strict):
    """`ExecutionLimits` as stored. Field for field, so `limits_for` is exact."""

    cpu_seconds: float = Field(gt=0)
    cpu_extra_seconds: float = Field(ge=0)
    wall_seconds: float = Field(gt=0)
    memory_kb: int = Field(gt=0)
    stack_kb: int = Field(gt=0)
    max_processes: int = Field(gt=0)
    max_file_kb: int = Field(gt=0)
    max_output_chars: int = Field(gt=0)

    @classmethod
    def of(cls, limits: ExecutionLimits) -> "LanguageLimits":
        return cls(
            cpu_seconds=limits.cpu_seconds,
            cpu_extra_seconds=limits.cpu_extra_seconds,
            wall_seconds=limits.wall_seconds,
            memory_kb=limits.memory_kb,
            stack_kb=limits.stack_kb,
            max_processes=limits.max_processes,
            max_file_kb=limits.max_file_kb,
            max_output_chars=limits.max_output_chars,
        )


class CodingPayloadV2(_Strict):
    """The v2 coding payload. Every field is candidate-facing."""

    payload_version: Literal[2]
    title: str = Field(min_length=1, max_length=TITLE_MAX_CHARS)
    io: Literal["stdin_stdout"]
    input_format: str = Field(min_length=1, max_length=FORMAT_MAX_CHARS)
    output_format: str = Field(min_length=1, max_length=FORMAT_MAX_CHARS)
    constraints: str = Field(default="", max_length=FORMAT_MAX_CHARS)
    languages: list[str] = Field(min_length=1, max_length=len(CODE_EXECUTION_LANGUAGE_KEYS))
    starter_code: dict[str, str]
    visible_tests: list[VisibleTest] = Field(min_length=1, max_length=VISIBLE_TESTS_CEILING)
    limits: dict[str, LanguageLimits]

    @model_validator(mode="after")
    def _consistent(self) -> "CodingPayloadV2":
        # Against the product's language REGISTRY, not this deployment's
        # configured list: a question issued while a language was offered must
        # stay readable after a deployment stops offering it.
        unknown = [key for key in self.languages if key not in CODE_EXECUTION_LANGUAGE_KEYS]
        if unknown:
            raise ValueError(f"unknown coding languages {unknown}")
        if len(set(self.languages)) != len(self.languages):
            raise ValueError("languages must be unique")
        if set(self.starter_code) != set(self.languages):
            raise ValueError("starter_code must have exactly one entry per language")
        for language, starter in self.starter_code.items():
            if not starter.strip():
                raise ValueError(f"the {language} starter code is empty")
            if len(starter) > STARTER_MAX_CHARS:
                raise ValueError(f"the {language} starter code exceeds {STARTER_MAX_CHARS} characters")
        if set(self.limits) != set(self.languages):
            raise ValueError("limits must have exactly one entry per language")
        expected_ids = [visible_test_key(n) for n in range(1, len(self.visible_tests) + 1)]
        if [test.id for test in self.visible_tests] != expected_ids:
            raise ValueError(f"visible test ids must be {expected_ids} in order")
        return self


def is_v2(payload: dict[str, Any] | None) -> bool:
    """Whether a stored coding payload is the executed (v2) shape."""
    return bool(payload) and payload.get("payload_version") == PAYLOAD_VERSION


def parse(payload: dict[str, Any] | None) -> CodingPayloadV2:
    """The typed v2 payload, or a ValueError naming what is wrong with it."""
    return CodingPayloadV2.model_validate(payload or {})


def candidate_projection(payload: dict[str, Any] | None) -> dict[str, Any]:
    """What the candidate may see of a v2 coding payload. An explicit list.

    Every v2 field is candidate-facing today, so this is the whole payload; it
    is still written as a list of names so that a field added to the model
    later stays out of the candidate's view until somebody decides it belongs
    there.
    """
    dumped = parse(payload).model_dump()
    return {name: dumped[name] for name in CANDIDATE_FIELDS}


def limits_for(payload: CodingPayloadV2, language: str) -> ExecutionLimits:
    """The limits this question was validated under, for one of its languages.

    Raises `KeyError` naming the language when the question does not offer it:
    running a program under limits nobody validated would be reporting a
    result for a question that was never asked.
    """
    if language not in payload.limits:
        raise KeyError(f"this coding question does not offer {language!r}")
    stored = payload.limits[language]
    return ExecutionLimits(
        cpu_seconds=stored.cpu_seconds,
        cpu_extra_seconds=stored.cpu_extra_seconds,
        wall_seconds=stored.wall_seconds,
        memory_kb=stored.memory_kb,
        stack_kb=stored.stack_kb,
        max_processes=stored.max_processes,
        max_file_kb=stored.max_file_kb,
        max_output_chars=stored.max_output_chars,
    )


def visible_inputs(payload: CodingPayloadV2) -> tuple[TestInput, ...]:
    """The visible tests as sandbox inputs. The expected outputs stay here."""
    return tuple(TestInput(key=test.id, stdin=test.stdin) for test in payload.visible_tests)
