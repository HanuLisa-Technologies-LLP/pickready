"""Writing one EXECUTED coding question, and proving it before anybody sees it.

WHAT A CODING QUESTION IS NOW
-----------------------------
A problem statement, a starter program in every configured language, sample
tests the candidate can Run, hidden tests the final answer is graded against,
and a reference solution. The reference solution exists for one reason: to
prove the tests. It is RUN against every visible and hidden test in the
sandbox, through `services/code_execution` (the port; this module never knows
which sandbox answers), and the question is rejected if it fails even one.
The model wrote both the tests and the solution, so either can be wrong; a
question whose expected outputs were never produced by a working program would
grade correct candidates as wrong.

Each starter program is run too, on a probe of the hidden tests: it must run
cleanly (a candidate's first Run must not crash on the scaffold they were
given) and it must NOT already solve the problem (a question solved by its
own scaffold measures nothing). The reference must also finish well inside
the CPU limit (`coding_reference_cpu_headroom`), because a reference that
nearly times out turns a correct candidate's answer into a time-limit failure
on a busier moment of the same host.

THE LOOP, AND WHAT THE MODEL IS TOLD
------------------------------------
One `agent_loop.run_loop`, background-bounded, with deterministic criteria
(shape, counts, sizes, duplicates, the Java class name, the candidate-facing
guards) checked BEFORE any sandbox run and the sandbox verdict after. A
rejected attempt is re-asked with the previous JSON and the reasons.

Reasons are CONTENT-FREE: they name tests by key ("hidden test 3 (h3)") and
outcomes by word, because `agent_loop` logs every reason and a hidden test
must never appear in a log line. What the model needs to FIX a failed test
(its own reference's stdout, a compiler message) travels in a separate note
appended to the next request only, never to a reason and never to a log. The
model wrote those tests; showing them back to it is not a disclosure.

A SANDBOX OUTAGE ENDS THE LOOP, IT DOES NOT RETRY INTO IT
--------------------------------------------------------
`ExecutionUnavailable`, a lost ticket, a sandbox fault or a refused request is
not a badly written question, so asking the model again would spend a model
call per attempt against a sandbox that cannot answer. The first one latches:
later loop iterations make NO model call, and the result is the refusal
`code_execution_unavailable`, which the composer turns into a prose slot and
records. Disabled execution is refused BEFORE any model call
(`code_execution_disabled`).

WHAT LEAVES THIS MODULE
-----------------------
`CodingGeneration` carries either a `CodingQuestionDraft` or a refusal word
from `REFUSALS`, never both. `persist_coding_question` writes the candidate-
safe v2 payload onto the question row and hands the hidden tests, the
reference and the reviewer's notes to `coding_assessment.keys`, the one module
that may write them, in the caller's transaction. Every text field of the
draft that holds part of the answer key is `repr=False`.

NEVER IN THE PROMPT: compensation of any kind, the candidate's resume, or
anything else a candidate wrote. The problem is about the ROLE and the SKILL,
so one candidate's text cannot steer the question another candidate is asked.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import llm_providers
from app.core.config import get_settings
from app.models.assessment import CandidateQuestion
from app.models.coding import CODE_SOURCE_MAX_CHARS
from app.prompts import registry
from app.services import agent_loop, conversation_guardrails, llm_router
from app.services import code_execution
from app.services.assessment_contract import ContractSkill
from app.services.assessment_formats import types
from app.services.code_execution import (
    CodeExecutionProvider,
    ExecutionLimits,
    ExecutionNotConfigured,
    ExecutionOutcome,
    ExecutionRejected,
    ExecutionTicketLost,
    ExecutionUnavailable,
    TestExecution,
    TestInput,
    outputs_match,
)
from app.services.code_execution import limits as execution_limits
from app.services.code_execution.languages import LANGUAGE_SPECS, configured_languages
from app.services.coding_assessment import keys as coding_keys
from app.services.coding_assessment.payload import (
    CODING_REVIEW_CRITERIA,
    EXPLANATION_MAX_CHARS,
    FORMAT_MAX_CHARS,
    IO_STDIN_STDOUT,
    PAYLOAD_VERSION,
    STARTER_MAX_CHARS,
    TEST_STDIN_MAX_CHARS,
    TITLE_MAX_CHARS,
    CodingPayloadV2,
    LanguageLimits,
    VisibleTest,
    hidden_test_key,
    visible_test_key,
)
from app.services.generation_sufficiency import meta_commentary_defects

logger = logging.getLogger(__name__)

__all__ = [
    "TASK_TYPE",
    "PROMPT_NAME",
    "REFUSAL_EXECUTION_DISABLED",
    "REFUSAL_EXECUTION_UNAVAILABLE",
    "REFUSAL_INSUFFICIENT_INPUT",
    "REFUSAL_GENERATION_FAILED",
    "REFUSALS",
    "CodingQuestionDraft",
    "CodingGeneration",
    "build_messages",
    "write_coding_question",
    "persist_coding_question",
    "issued_titles",
]

TASK_TYPE = "coding_question_generation"
PROMPT_NAME = "coding_question_generation"

#: Deterministic, before any model call: this deployment has no sandbox.
REFUSAL_EXECUTION_DISABLED = "code_execution_disabled"
#: The sandbox could not validate the question (outage, fault, lost ticket).
REFUSAL_EXECUTION_UNAVAILABLE = "code_execution_unavailable"
#: The skill carries nothing to write a problem from (no name or no evidence
#: line), decided before any model call.
REFUSAL_INSUFFICIENT_INPUT = "insufficient_input"
#: Every attempt the loop could afford was rejected.
REFUSAL_GENERATION_FAILED = "generation_failed"
#: The closed vocabulary the composer records as a degradation reason.
REFUSALS: tuple[str, ...] = (
    REFUSAL_EXECUTION_DISABLED,
    REFUSAL_EXECUTION_UNAVAILABLE,
    REFUSAL_INSUFFICIENT_INPUT,
    REFUSAL_GENERATION_FAILED,
)

#: A statement shorter than this is a title, not a problem.
STATEMENT_MIN_CHARS = 80
STATEMENT_MAX_CHARS = 6000
EXPECTED_APPROACH_MAX_CHARS = 4000
#: The role summary is Sutra's paragraph; this is a ceiling, not a target.
ROLE_SUMMARY_MAX_CHARS = 1500
#: A hidden input this long that appears in the statement has been shown to
#: the candidate. Shorter inputs ("3", "0") appear in any statement by chance.
HIDDEN_INPUT_ECHO_MIN_CHARS = 12
#: How many hidden tests a starter program is probed with first. Failing any
#: of them proves it does not solve the problem; only a starter that passes
#: all of them is run against the rest, which keeps the sandbox load of a
#: validation at a few runs per language rather than the whole hidden set.
STARTER_PROBE_TESTS = 2
#: How much of a failing program's output the next attempt is shown.
FEEDBACK_EXCERPT_CHARS = 600
#: The reference language when it is configured: the one a model writes most
#: reliably, which is what a reference exists to be.
PREFERRED_REFERENCE_LANGUAGE = "python"

_JAVA_MAIN = re.compile(r"\bpublic\s+(?:final\s+)?class\s+Main\b")
_EM_DASH = chr(8212)

_OUTCOME_WORDS: dict[ExecutionOutcome, str] = {
    ExecutionOutcome.OK: "ran",
    ExecutionOutcome.COMPILE_ERROR: "failed to compile",
    ExecutionOutcome.RUNTIME_ERROR: "crashed",
    ExecutionOutcome.TIME_LIMIT: "ran out of time",
    ExecutionOutcome.MEMORY_LIMIT: "ran out of memory",
    ExecutionOutcome.OUTPUT_LIMIT: "printed more output than the limit allows",
    ExecutionOutcome.INTERNAL_ERROR: "hit a sandbox fault",
}

#: Recorded per test in the validation record: an outcome class, never output.
_PASSED = "passed"
_WRONG_ANSWER = "wrong_answer"


# ── What comes out ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CodingQuestionDraft:
    """A validated coding question, ready for `persist_coding_question`.

    `statement` becomes `candidate_questions.prompt`; `payload` is the v2
    payload, candidate-safe by construction. The answer-key fields never
    appear in a repr.
    """

    title: str
    statement: str
    payload: dict[str, Any]
    rubric: dict[str, Any]
    reference_language: str
    validation: dict[str, Any]
    generated_at: datetime
    hidden_tests: tuple[coding_keys.HiddenTest, ...] = field(repr=False)
    reference_source: str = field(repr=False)
    expected_approach: str = field(repr=False)


@dataclass(frozen=True)
class CodingGeneration:
    """The outcome of one `write_coding_question`: a draft, or a refusal.

    `reasons` is operator detail (the last rejection, content-free); the
    composer records `refusal`, which is one of `REFUSALS`. `model_calls` is
    how many model calls were actually made, which is what a disabled or
    insufficient refusal proves is zero.
    """

    draft: CodingQuestionDraft | None
    refusal: str | None
    reasons: tuple[str, ...] = ()
    model_calls: int = 0
    elapsed_ms: int = 0

    def __post_init__(self) -> None:
        if (self.draft is None) == (self.refusal is None):
            raise ValueError("a coding generation carries a draft or a refusal, never both")
        if self.refusal is not None and self.refusal not in REFUSALS:
            raise ValueError(f"unknown coding generation refusal {self.refusal!r}")

    @property
    def degraded(self) -> bool:
        return self.draft is None


def _refused(refusal: str, *reasons: str, model_calls: int = 0, elapsed_ms: int = 0) -> CodingGeneration:
    return CodingGeneration(
        draft=None,
        refusal=refusal,
        reasons=tuple(reasons),
        model_calls=model_calls,
        elapsed_ms=elapsed_ms,
    )


# ── The request ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Bounds:
    visible_min: int
    visible_max: int
    hidden_min: int
    hidden_max: int
    max_expected_chars: int

    @classmethod
    def from_settings(cls, limits: dict[str, ExecutionLimits]) -> "_Bounds":
        settings = get_settings()
        return cls(
            visible_min=settings.coding_visible_tests_min,
            visible_max=settings.coding_visible_tests_max,
            hidden_min=settings.coding_hidden_tests_min,
            hidden_max=settings.coding_hidden_tests_max,
            # The sandbox keeps this much stdout. An expected output longer
            # than what is kept could never be matched, by any program.
            max_expected_chars=min(limit.max_output_chars for limit in limits.values()),
        )


def _experience(job: Any) -> str | None:
    low = getattr(job, "experience_min_years", None)
    high = getattr(job, "experience_max_years", None)
    if low is None and high is None:
        return None
    if low is not None and high is not None:
        return f"{low} to {high} years"
    return f"at least {low} years" if low is not None else f"up to {high} years"


def build_messages(
    *,
    job: Any,
    skill: ContractSkill,
    role_summary: str,
    grade: str,
    languages: Sequence[str],
    reference_language: str,
    limits: dict[str, ExecutionLimits],
    avoid_titles: Sequence[str] = (),
) -> list[dict[str, str]]:
    """The system prompt and the JSON the model is given. PURE.

    This is the ONE place that decides what the model sees about a job, so a
    reviewer reads it here: the title, the grade, the experience band, Sutra's
    role summary, the skill with its evidence line, and the execution bounds.
    Never compensation, never a resume, never a candidate's words.
    """
    bounds = _Bounds.from_settings(limits)
    request: dict[str, Any] = {
        "job_title": str(getattr(job, "title", "") or "").strip(),
        "grade": grade,
        "role_summary": (role_summary or "").strip()[:ROLE_SUMMARY_MAX_CHARS],
        "skill": {
            "name": skill.name,
            "bucket": skill.bucket,
            "evidence_line": skill.evidence_line,
        },
        "languages": [
            {
                "key": key,
                "label": LANGUAGE_SPECS[key].label,
                "how_the_program_is_run": LANGUAGE_SPECS[key].source_hint,
            }
            for key in languages
        ],
        "reference_language": reference_language,
        "visible_tests": {"min": bounds.visible_min, "max": bounds.visible_max},
        "hidden_tests": {"min": bounds.hidden_min, "max": bounds.hidden_max},
        "cpu_seconds_per_test": limits[reference_language].cpu_seconds,
        "max_stdin_characters": TEST_STDIN_MAX_CHARS,
        "max_expected_output_characters": bounds.max_expected_chars,
        "titles_to_avoid": [title for title in avoid_titles if title],
    }
    experience = _experience(job)
    if experience is not None:
        request["experience"] = experience
    return [
        {"role": "system", "content": registry.render(PROMPT_NAME)},
        {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
    ]


# ── Deterministic checks ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Parsed:
    title: str
    statement: str
    input_format: str
    output_format: str
    constraints: str
    starter_code: dict[str, str] = field(repr=False)
    visible: tuple[VisibleTest, ...] = field(repr=False)
    hidden: tuple[coding_keys.HiddenTest, ...] = field(repr=False)
    reference_source: str = field(repr=False)
    expected_approach: str = field(repr=False)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _label(key: str) -> str:
    kind = "visible" if key.startswith("v") else "hidden"
    return f"{kind} test {key[1:]} ({key})"


def _pydantic_reasons(exc: ValidationError) -> list[str]:
    # `msg` never carries the input value, so no test content reaches a reason.
    return [
        f"the question is malformed at {'.'.join(str(part) for part in err['loc']) or 'the top level'}: {err['msg']}"
        for err in exc.errors()
    ]


def _candidate_facing_reasons(parts: dict[str, str]) -> list[str]:
    """The guards a candidate-facing string must pass, as instructions."""
    reasons: list[str] = []
    for name, value in parts.items():
        if not value:
            continue
        if _EM_DASH in value:
            reasons.append(f"remove every em dash from the {name}")
        if conversation_guardrails.inspect_agent_output(value) != value.strip():
            reasons.append(
                f"rephrase the {name}: it must not frame a value as a score, grade, "
                "mark or ranking, or mention how answers are judged or other candidates"
            )
        for defect in meta_commentary_defects(value, location=name):
            reasons.append(f"in the {name}, {defect.detail}")
    return reasons


def _tests(
    raw: Any, *, kind: str, low: int, high: int, max_expected: int
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """(stdin, expected, explanation) per test, and the reasons it is wrong."""
    if not isinstance(raw, list):
        return [], [f"'{kind}_tests' must be a list of tests"]
    reasons: list[str] = []
    if not low <= len(raw) <= high:
        reasons.append(f"write between {low} and {high} {kind} tests; the previous attempt had {len(raw)}")
    parsed: list[tuple[str, str, str]] = []
    for position, item in enumerate(raw, start=1):
        key = visible_test_key(position) if kind == "visible" else hidden_test_key(position)
        if not isinstance(item, dict):
            reasons.append(f"{_label(key)} must be an object")
            continue
        stdin = item.get("stdin")
        expected = item.get("expected_stdout")
        explanation = _text(item.get("explanation"))
        if not isinstance(stdin, str) or not isinstance(expected, str):
            reasons.append(f"{_label(key)} needs a string stdin and a string expected_stdout")
            continue
        if len(stdin) > TEST_STDIN_MAX_CHARS:
            reasons.append(f"the stdin of {_label(key)} exceeds {TEST_STDIN_MAX_CHARS} characters")
        if not expected.strip():
            reasons.append(f"{_label(key)} must expect some output")
        elif len(expected) > max_expected:
            reasons.append(f"the expected output of {_label(key)} exceeds {max_expected} characters")
        if kind == "visible" and len(explanation) > EXPLANATION_MAX_CHARS:
            reasons.append(f"keep the explanation of {_label(key)} under {EXPLANATION_MAX_CHARS} characters")
        parsed.append((stdin, expected, explanation))
    return parsed, reasons


def _parse(
    raw: str,
    *,
    languages: Sequence[str],
    reference_language: str,
    bounds: _Bounds,
    avoid_titles: Sequence[str],
) -> tuple[_Parsed | None, list[str]]:
    """The model's JSON as a question, or the reasons it is not one yet."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, ["return one JSON object in exactly the shape asked for"]
    if not isinstance(data, dict):
        return None, ["return one JSON object in exactly the shape asked for"]

    reasons: list[str] = []
    title = _text(data.get("title"))
    statement = _text(data.get("statement"))
    input_format = _text(data.get("input_format"))
    output_format = _text(data.get("output_format"))
    constraints = _text(data.get("constraints"))
    approach = _text(data.get("expected_approach"))
    reference = data.get("reference_solution")
    reference = reference if isinstance(reference, str) else ""

    if not 1 <= len(title) <= TITLE_MAX_CHARS:
        reasons.append(f"give the problem a title of 1 to {TITLE_MAX_CHARS} characters")
    if _collapse(title).casefold() in {_collapse(t).casefold() for t in avoid_titles if t}:
        reasons.append("choose a different problem: that title is already in use for this role")
    if not STATEMENT_MIN_CHARS <= len(statement) <= STATEMENT_MAX_CHARS:
        reasons.append(
            f"write a statement of {STATEMENT_MIN_CHARS} to {STATEMENT_MAX_CHARS} characters; "
            f"the previous one had {len(statement)}"
        )
    for name, value in (("input_format", input_format), ("output_format", output_format)):
        if not 1 <= len(value) <= FORMAT_MAX_CHARS:
            reasons.append(f"write an {name} of 1 to {FORMAT_MAX_CHARS} characters")
    if len(constraints) > FORMAT_MAX_CHARS:
        reasons.append(f"keep the constraints under {FORMAT_MAX_CHARS} characters")
    if not 1 <= len(approach) <= EXPECTED_APPROACH_MAX_CHARS:
        reasons.append(f"write an expected_approach of 1 to {EXPECTED_APPROACH_MAX_CHARS} characters")

    starter = data.get("starter_code")
    starter_code: dict[str, str] = {}
    if not isinstance(starter, dict):
        reasons.append("'starter_code' must map each language key to a program")
    else:
        extra = sorted(set(starter) - set(languages))
        missing = [key for key in languages if key not in starter]
        if extra:
            reasons.append(f"write starter code only for {', '.join(languages)}; remove {', '.join(extra)}")
        if missing:
            reasons.append(f"write starter code for {', '.join(missing)}")
        for key in languages:
            program = starter.get(key)
            if not isinstance(program, str) or not program.strip():
                continue
            if len(program) > STARTER_MAX_CHARS:
                reasons.append(f"keep the {key} starter code under {STARTER_MAX_CHARS} characters")
            if key == "java" and not _JAVA_MAIN.search(program):
                reasons.append("the java starter code must declare public class Main")
            starter_code[key] = program

    if not reference.strip() or len(reference) > CODE_SOURCE_MAX_CHARS:
        reasons.append(f"write a complete reference_solution in {reference_language}")
    elif reference_language == "java" and not _JAVA_MAIN.search(reference):
        reasons.append("the java reference_solution must declare public class Main")

    visible_raw, visible_reasons = _tests(
        data.get("visible_tests"), kind="visible",
        low=bounds.visible_min, high=bounds.visible_max, max_expected=bounds.max_expected_chars,
    )
    hidden_raw, hidden_reasons = _tests(
        data.get("hidden_tests"), kind="hidden",
        low=bounds.hidden_min, high=bounds.hidden_max, max_expected=bounds.max_expected_chars,
    )
    reasons.extend(visible_reasons)
    reasons.extend(hidden_reasons)

    # Duplicates and leaks, compared on collapsed whitespace so a trailing
    # newline does not make two identical inputs look different.
    seen: dict[str, str] = {}
    all_keyed = [(visible_test_key(i), t) for i, t in enumerate(visible_raw, start=1)] + [
        (hidden_test_key(i), t) for i, t in enumerate(hidden_raw, start=1)
    ]
    for key, (stdin, _expected, _explanation) in all_keyed:
        normal = _collapse(stdin)
        if normal in seen:
            reasons.append(f"{_label(key)} repeats the input of {_label(seen[normal])}; every test needs its own input")
        else:
            seen[normal] = key
    if len(hidden_raw) >= 2 and len({_collapse(expected) for _s, expected, _e in hidden_raw}) == 1:
        reasons.append("the hidden tests all expect the same output, so a program printing a constant would pass; vary them")
    public_text = _collapse(
        " ".join([statement, input_format, output_format, constraints]
                 + [explanation for _s, _x, explanation in visible_raw])
    )
    for position, (stdin, _expected, _explanation) in enumerate(hidden_raw, start=1):
        normal = _collapse(stdin)
        if len(normal) >= HIDDEN_INPUT_ECHO_MIN_CHARS and normal in public_text:
            reasons.append(f"the input of {_label(hidden_test_key(position))} appears in the statement; hidden inputs must never be shown")

    reasons.extend(
        _candidate_facing_reasons(
            {
                "title": title,
                "statement": statement,
                "input_format": input_format,
                "output_format": output_format,
                "constraints": constraints,
                **{
                    f"explanation of {_label(visible_test_key(i))}": explanation
                    for i, (_s, _x, explanation) in enumerate(visible_raw, start=1)
                },
            }
        )
    )
    for key, program in starter_code.items():
        if _EM_DASH in program:
            reasons.append(f"remove every em dash from the {key} starter code")

    if reasons:
        return None, reasons
    return (
        _Parsed(
            title=title,
            statement=statement,
            input_format=input_format,
            output_format=output_format,
            constraints=constraints,
            starter_code=starter_code,
            visible=tuple(
                VisibleTest(id=visible_test_key(i), stdin=stdin, expected_stdout=expected, explanation=explanation)
                for i, (stdin, expected, explanation) in enumerate(visible_raw, start=1)
            ),
            hidden=tuple(
                coding_keys.HiddenTest(key=hidden_test_key(i), stdin=stdin, expected_stdout=expected)
                for i, (stdin, expected, _explanation) in enumerate(hidden_raw, start=1)
            ),
            reference_source=reference,
            expected_approach=approach,
        ),
        [],
    )


# ── The sandbox verdict ──────────────────────────────────────────────────────


class _SandboxFailed(Exception):
    """The sandbox could not give a verdict. Never the model's fault."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class _Verdict:
    record: dict[str, Any] | None
    reasons: list[str]
    notes: list[str]


def _excerpt(text: str) -> str:
    text = text or ""
    if len(text) <= FEEDBACK_EXCERPT_CHARS:
        return text
    return text[:FEEDBACK_EXCERPT_CHARS] + "\n[truncated]"


async def _run(
    provider: CodeExecutionProvider,
    *,
    language: str,
    source: str,
    tests: Sequence[TestInput],
    limits: ExecutionLimits,
) -> dict[str, TestExecution]:
    """One sandbox run, keyed by test. Any provider failure becomes
    `_SandboxFailed`, and so does an incomplete answer: a provider that did
    not report on every input has not validated anything."""
    try:
        results = await provider.run(
            language=language,
            source=source,
            tests=tests,
            limits=limits,
            deadline_seconds=get_settings().coding_validation_deadline_seconds,
        )
    except (ExecutionUnavailable, ExecutionTicketLost, ExecutionNotConfigured) as exc:
        raise _SandboxFailed(exc.reason) from exc
    except ExecutionRejected as exc:
        # Our request, refused. A defect on this side, never retried into.
        logger.error(
            "coding_generation.sandbox_rejected language=%s reason=%s", language, exc.reason
        )
        raise _SandboxFailed("rejected") from exc
    by_key = {result.key: result for result in results}
    if set(by_key) != {test.key for test in tests}:
        raise _SandboxFailed("incomplete_results")
    for result in results:
        if result.outcome is ExecutionOutcome.INTERNAL_ERROR:
            raise _SandboxFailed("internal_error")
    return by_key


def _verdict_word(result: TestExecution, expected: str) -> str:
    if result.outcome is not ExecutionOutcome.OK:
        return result.outcome.value
    return _PASSED if outputs_match(result.stdout, expected) else _WRONG_ANSWER


async def _validate(
    provider: CodeExecutionProvider,
    parsed: _Parsed,
    *,
    languages: Sequence[str],
    reference_language: str,
    limits: dict[str, ExecutionLimits],
) -> _Verdict:
    """Run the reference over every test, then probe every starter program."""
    expected = {test.id: test.expected_stdout for test in parsed.visible}
    expected.update({test.key: test.expected_stdout for test in parsed.hidden})
    inputs = [TestInput(key=test.id, stdin=test.stdin) for test in parsed.visible]
    inputs += [TestInput(key=test.key, stdin=test.stdin) for test in parsed.hidden]

    reasons: list[str] = []
    notes: list[str] = []
    reference_limits = limits[reference_language]
    results = await _run(
        provider,
        language=reference_language,
        source=parsed.reference_source,
        tests=inputs,
        limits=reference_limits,
    )
    headroom_ms = get_settings().coding_reference_cpu_headroom * reference_limits.cpu_seconds * 1000
    reference_outcomes: dict[str, str] = {}
    cpu_seen: list[int] = []
    for test in inputs:
        result = results[test.key]
        word = _verdict_word(result, expected[test.key])
        reference_outcomes[test.key] = word
        if result.cpu_ms is not None:
            cpu_seen.append(result.cpu_ms)
        if result.outcome is not ExecutionOutcome.OK:
            reasons.append(
                f"the reference solution {_OUTCOME_WORDS[result.outcome]} on {_label(test.key)}; "
                "fix the reference or that test"
            )
            detail = result.compile_output or result.stderr
            if detail:
                notes.append(f"For {_label(test.key)} the reference solution reported:\n{_excerpt(detail)}")
        elif word == _WRONG_ANSWER:
            reasons.append(
                f"the reference solution printed a different answer from the expected output of "
                f"{_label(test.key)}; one of the two is wrong, fix it"
            )
            notes.append(f"For {_label(test.key)} the reference solution printed:\n{_excerpt(result.stdout)}")
        elif result.cpu_ms is not None and result.cpu_ms > headroom_ms:
            reasons.append(
                f"the reference solution needs too much of the time limit on {_label(test.key)}; "
                "make that input smaller"
            )
    if reasons:
        return _Verdict(record=None, reasons=reasons, notes=notes)

    probe = [TestInput(key=test.key, stdin=test.stdin) for test in parsed.hidden[:STARTER_PROBE_TESTS]]
    rest = [TestInput(key=test.key, stdin=test.stdin) for test in parsed.hidden[STARTER_PROBE_TESTS:]]

    async def probe_starter(language: str) -> tuple[str, dict[str, str], list[str], list[str]]:
        found: list[str] = []
        found_notes: list[str] = []
        outcome = await _run(
            provider,
            language=language,
            source=parsed.starter_code[language],
            tests=probe,
            limits=limits[language],
        )
        words = {key: _verdict_word(result, expected[key]) for key, result in outcome.items()}
        failed = [result for result in outcome.values() if result.outcome is not ExecutionOutcome.OK]
        if failed:
            found.append(
                f"the {language} starter code {_OUTCOME_WORDS[failed[0].outcome]}; it must "
                "run as given and only mark where the solution goes"
            )
            detail = failed[0].compile_output or failed[0].stderr
            if detail:
                found_notes.append(f"The {language} starter code reported:\n{_excerpt(detail)}")
        elif all(word == _PASSED for word in words.values()):
            remaining = await _run(
                provider,
                language=language,
                source=parsed.starter_code[language],
                tests=rest,
                limits=limits[language],
            ) if rest else {}
            words.update({key: _verdict_word(result, expected[key]) for key, result in remaining.items()})
            if all(word == _PASSED for word in words.values()):
                found.append(
                    f"the {language} starter code already solves the problem; it must only "
                    "read the input and mark where the solution goes"
                )
        return language, words, found, found_notes

    probed = await asyncio.gather(*(probe_starter(language) for language in languages))
    starters: dict[str, Any] = {}
    for language, words, found, found_notes in probed:
        starters[language] = {"outcomes": words}
        reasons.extend(found)
        notes.extend(found_notes)
    if reasons:
        return _Verdict(record=None, reasons=reasons, notes=notes)

    record = {
        "provider": provider.name,
        "reference": {
            "language": reference_language,
            "outcomes": reference_outcomes,
            "max_cpu_ms": max(cpu_seen) if cpu_seen else None,
        },
        "starters": starters,
        "hidden_digest": coding_keys.hidden_digest(parsed.hidden),
        "counts": {"visible": len(parsed.visible), "hidden": len(parsed.hidden)},
    }
    return _Verdict(record=record, reasons=[], notes=[])


# ── The loop ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Attempt:
    draft: CodingQuestionDraft | None
    reasons: tuple[str, ...]


@dataclass
class _State:
    model_calls: int = 0
    last_raw: str = field(default="", repr=False)
    notes: tuple[str, ...] = field(default=(), repr=False)
    sandbox_failure: str | None = None


def _configured(languages: Sequence[str] | None) -> list[str]:
    configured = [spec.key for spec in configured_languages()]
    if languages is None:
        return configured
    chosen = list(dict.fromkeys(languages))
    unknown = [key for key in chosen if key not in configured]
    if unknown or not chosen:
        raise ValueError(f"coding languages {unknown or chosen} are not configured for this deployment")
    return chosen


async def write_coding_question(
    session: AsyncSession | None,
    *,
    job: Any,
    contract_skill: ContractSkill,
    role_summary: str,
    grade: str,
    languages: Sequence[str] | None = None,
    avoid_titles: Sequence[str] = (),
) -> CodingGeneration:
    """Write one coding question for one skill, validated in the sandbox.

    Never raises for an outage or a bad model answer: those are refusals. It
    DOES raise `ValueError` for a caller defect (a behavioural skill, an
    unconfigured language), because a coding question for a behavioural skill
    is a composition bug, not a degradation.
    """
    if contract_skill.bucket == "behavioural":
        raise ValueError("a behavioural skill is never assessed by a coding question")
    started = datetime.now(timezone.utc)

    def elapsed_ms() -> int:
        return int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

    # ── Sufficiency and configuration: all before any model call ─────────
    if not code_execution.is_enabled():
        logger.info("coding_generation.refused reason=%s skill_id=%s", REFUSAL_EXECUTION_DISABLED, contract_skill.id)
        return _refused(REFUSAL_EXECUTION_DISABLED, "code execution is not enabled in this deployment")
    chosen = _configured(languages)
    try:
        limits = {key: execution_limits.for_language(key) for key in chosen}
        provider = code_execution.get_provider()
    except ExecutionNotConfigured as exc:
        logger.info("coding_generation.refused reason=%s detail=%s", REFUSAL_EXECUTION_DISABLED, exc.reason)
        return _refused(REFUSAL_EXECUTION_DISABLED, f"code execution is not usable: {exc.reason}")
    if not contract_skill.name.strip() or not contract_skill.evidence_line.strip():
        logger.info("coding_generation.refused reason=%s skill_id=%s", REFUSAL_INSUFFICIENT_INPUT, contract_skill.id)
        return _refused(REFUSAL_INSUFFICIENT_INPUT, "the skill has no name or no evidence line to write a problem from")
    if not str(getattr(job, "title", "") or "").strip():
        return _refused(REFUSAL_INSUFFICIENT_INPUT, "the job has no title")

    reference_language = (
        PREFERRED_REFERENCE_LANGUAGE if PREFERRED_REFERENCE_LANGUAGE in chosen else chosen[0]
    )
    bounds = _Bounds.from_settings(limits)
    base_messages = build_messages(
        job=job,
        skill=contract_skill,
        role_summary=role_summary,
        grade=grade,
        languages=chosen,
        reference_language=reference_language,
        limits=limits,
        avoid_titles=avoid_titles,
    )
    prompt_version = registry.version(PROMPT_NAME)
    state = _State()
    unavailable = ("the code runner was unavailable, so the question could not be validated",)

    async def execute(reflection: str) -> _Attempt:
        if state.sandbox_failure is not None:
            # Latched: no model call against a sandbox that cannot answer.
            return _Attempt(draft=None, reasons=unavailable)
        messages = list(base_messages)
        # An empty reflection follows an attempt that RAISED (the loop has
        # nothing to reflect on), so the next request starts clean rather than
        # re-sending an answer nobody commented on.
        if state.last_raw and reflection:
            follow_up = reflection
            if state.notes:
                follow_up += "\n\nDetails from running your programs:\n" + "\n\n".join(state.notes)
            messages += [
                {"role": "assistant", "content": state.last_raw},
                {"role": "user", "content": follow_up},
            ]
        raw = await llm_router.invoke_llm(TASK_TYPE, messages, response_format_json=True, session=session)
        state.model_calls += 1
        state.last_raw, state.notes = raw, ()
        parsed, reasons = _parse(
            raw,
            languages=chosen,
            reference_language=reference_language,
            bounds=bounds,
            avoid_titles=avoid_titles,
        )
        if parsed is None:
            return _Attempt(draft=None, reasons=tuple(reasons))
        try:
            payload = CodingPayloadV2(
                payload_version=PAYLOAD_VERSION,
                title=parsed.title,
                io=IO_STDIN_STDOUT,
                input_format=parsed.input_format,
                output_format=parsed.output_format,
                constraints=parsed.constraints,
                languages=list(chosen),
                starter_code=dict(parsed.starter_code),
                visible_tests=list(parsed.visible),
                limits={key: LanguageLimits.of(limits[key]) for key in chosen},
            ).model_dump()
        except ValidationError as exc:
            return _Attempt(draft=None, reasons=tuple(_pydantic_reasons(exc)))
        try:
            verdict = await _validate(
                provider, parsed, languages=chosen, reference_language=reference_language, limits=limits
            )
        except _SandboxFailed as exc:
            state.sandbox_failure = exc.reason
            logger.warning(
                "coding_generation.sandbox_unavailable reason=%s skill_id=%s", exc.reason, contract_skill.id
            )
            return _Attempt(draft=None, reasons=unavailable)
        if verdict.record is None:
            state.notes = tuple(verdict.notes)
            return _Attempt(draft=None, reasons=tuple(verdict.reasons))
        validation = {
            **verdict.record,
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "generation": {
                "task_type": TASK_TYPE,
                "model_id": llm_providers.model_for(TASK_TYPE),
                "prompt_version": prompt_version,
                "model_calls": state.model_calls,
            },
        }
        return _Attempt(
            draft=CodingQuestionDraft(
                title=parsed.title,
                statement=parsed.statement,
                payload=payload,
                rubric={"criteria": list(CODING_REVIEW_CRITERIA), "payload_version": PAYLOAD_VERSION},
                reference_language=reference_language,
                validation=validation,
                generated_at=datetime.now(timezone.utc),
                hidden_tests=parsed.hidden,
                reference_source=parsed.reference_source,
                expected_approach=parsed.expected_approach,
            ),
            reasons=(),
        )

    def evaluate(attempt: _Attempt) -> agent_loop.Critique:
        return agent_loop.ok() if attempt.draft is not None else agent_loop.reject(*attempt.reasons)

    settings = get_settings()
    max_attempts = agent_loop.BACKGROUND_ATTEMPTS
    result = await agent_loop.run_loop(
        name="coding_question",
        execute=execute,
        evaluate=evaluate,
        fallback=_Attempt(draft=None, reasons=()),
        max_attempts=max_attempts,
        deadline_seconds=settings.coding_generation_deadline_seconds,
        # The loop estimates output from the value's repr, and every answer-key
        # field is repr-hidden, so the estimate undercounts. The router's own
        # per-call ceiling is the real bound; this states it for the loop.
        max_generated_tokens=llm_providers.max_tokens_for(TASK_TYPE) * max_attempts,
    )
    draft = result.value.draft if not result.degraded else None
    if draft is not None:
        logger.info(
            "coding_generation.accepted skill_id=%s model_calls=%d hidden_tests=%d languages=%d",
            contract_skill.id, state.model_calls, len(draft.hidden_tests), len(chosen),
        )
        return CodingGeneration(
            draft=draft, refusal=None, model_calls=state.model_calls, elapsed_ms=elapsed_ms()
        )
    refusal = (
        REFUSAL_EXECUTION_UNAVAILABLE if state.sandbox_failure is not None else REFUSAL_GENERATION_FAILED
    )
    logger.info(
        "coding_generation.refused reason=%s skill_id=%s model_calls=%d sandbox=%s",
        refusal, contract_skill.id, state.model_calls, state.sandbox_failure,
    )
    return _refused(
        refusal, *result.reasons, model_calls=state.model_calls, elapsed_ms=elapsed_ms()
    )


# ── Persisting ───────────────────────────────────────────────────────────────


async def persist_coding_question(
    session: AsyncSession, question: CandidateQuestion, draft: CodingQuestionDraft
) -> None:
    """Write a validated draft onto its question row and stage its answer key.

    Runs in the CALLER's transaction, so the question and its key commit or
    roll back together: a coding question with no key could not be graded,
    and a key with no question is a secret nobody can reach. The question row
    must carry its tenant; its id is assigned here when the caller has not.
    The payload is validated again, because a draft can be built by hand and
    the model forbidding extra keys is what keeps the key out of it.
    """
    if question.tenant_id is None:
        raise ValueError("a coding question must carry its tenant before it is persisted")
    payload = CodingPayloadV2.model_validate(draft.payload).model_dump()
    if question.id is None:
        question.id = uuid.uuid4()
    question.question_type = types.CODING
    question.prompt = draft.statement
    question.payload_json = payload
    question.rubric_json = dict(draft.rubric)
    question.generated_at = draft.generated_at
    session.add(question)
    await session.flush()
    coding_keys.stage_key(
        session,
        question_id=question.id,
        tenant_id=question.tenant_id,
        tests=draft.hidden_tests,
        reference_language=draft.reference_language,
        reference_source=draft.reference_source,
        expected_approach=draft.expected_approach,
        validation=draft.validation,
    )
    await session.flush()


async def issued_titles(session: AsyncSession, *, job_id: uuid.UUID, limit: int = 20) -> tuple[str, ...]:
    """Titles of the v2 coding questions already issued on a job, newest first.

    Passed back as `avoid_titles`, so the candidates on one job are not all
    handed the same problem, which is what makes a question worth sharing.
    Reads through the caller's session, so RLS bounds it to the tenant.
    """
    rows = (
        await session.execute(
            select(CandidateQuestion.payload_json["title"].astext)
            .where(
                CandidateQuestion.job_id == job_id,
                CandidateQuestion.question_type == types.CODING,
                CandidateQuestion.payload_json["payload_version"].astext == str(PAYLOAD_VERSION),
            )
            .order_by(CandidateQuestion.created_at.desc())
            .limit(limit)
        )
    ).scalars()
    return tuple(dict.fromkeys(title for title in rows if title))
