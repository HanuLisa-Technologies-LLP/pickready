"""Coding question generation: nothing is accepted that a sandbox did not prove.

The model and the sandbox are both scripted at their boundaries: the router
through `llm_router.invoke_llm`, the sandbox through the port's own
`override_provider` with the scripted double, which never executes anything.
Everything between them is the real code.

Sentinels are planted in every part of the answer key (a hidden stdin, a
hidden expected output, the reference solution). They must never appear in
the candidate-safe payload, the statement, the rubric, the validation record,
a repr, or ANY log record at DEBUG, which is where a reason quoting a test
would land first.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import logging
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import llm_providers
from app.prompts import registry
from app.services import generation_sufficiency as gs
from app.services import llm_router
from app.services.assessment_contract import ContractSkill
from app.services.assessment_formats import coding_generation as gen
from app.services.code_execution import ExecutionOutcome, override_provider
from app.services.code_execution.fake import FakeProvider, ScriptedRun
from app.services.coding_assessment import payload as coding_payload

HIDDEN_STDIN = "zqhiddenstdinsentinel"
HIDDEN_OUT = "zqhiddenexpectedsentinel"
REFERENCE = "zqreferencesentinel"
SENTINELS = (HIDDEN_STDIN, HIDDEN_OUT, REFERENCE)

LANGUAGES = ("python", "java", "cpp", "javascript")
STARTERS = {
    "python": "import sys\n\ndef main():\n    data = sys.stdin.read().split()\n    print()\n\nmain()\n",
    "java": "import java.util.*;\n\npublic class Main {\n    public static void main(String[] a) {\n        System.out.println();\n    }\n}\n",
    "cpp": "#include <iostream>\nint main() {\n    std::cout << std::endl;\n    return 0;\n}\n",
    "javascript": "const data = require('fs').readFileSync(0, 'utf8');\nconsole.log();\n",
}


def _question(**overrides: Any) -> dict[str, Any]:
    """A model answer that passes every deterministic check."""
    question = {
        "title": "Busiest failing endpoint",
        "statement": (
            "A service writes one line per request: the endpoint path and the HTTP status "
            "code. Print the endpoint that returned the most server errors, breaking ties "
            "alphabetically, or NONE when no request failed."
        ),
        "input_format": "The first line holds n. Each of the next n lines holds a path and a status.",
        "output_format": "One line: the path, or NONE.",
        "constraints": "0 <= n <= 10000.",
        "starter_code": dict(STARTERS),
        "visible_tests": [
            {"stdin": "1\n/a 500\n", "expected_stdout": "/a\n", "explanation": "One failure on /a."},
            {"stdin": "0\n", "expected_stdout": "NONE\n", "explanation": "Nothing failed."},
        ],
        "hidden_tests": [
            {"stdin": f"1\n/{HIDDEN_STDIN}{i} 500\n", "expected_stdout": f"/{HIDDEN_OUT}{i}\n"}
            for i in range(1, 6)
        ],
        "reference_solution": f"# {REFERENCE}\nimport sys\nprint(sys.stdin.read())\n",
        "expected_approach": "Count server errors per path and break ties alphabetically.",
    }
    question.update(overrides)
    return question


class Router:
    """Scripted `invoke_llm`: returns the queued answers in order, records calls."""

    def __init__(self, *answers: dict[str, Any] | str) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    async def __call__(self, task_type: str, messages: list[dict[str, str]], **kwargs: Any) -> str:
        self.calls.append((task_type, copy.deepcopy(messages)))
        answer = self.answers.pop(0)
        return answer if isinstance(answer, str) else json.dumps(answer)


def _script(provider: FakeProvider, question: dict[str, Any], *, reference_stdout: dict[int, str] | None = None,
            starter: dict[str, ScriptedRun] | None = None, starter_solves: str | None = None) -> None:
    """Script the sandbox for one model answer.

    The reference prints each test's expected output unless `reference_stdout`
    overrides a hidden test (1-based). Each starter prints nothing on the
    hidden probe unless `starter` overrides one language's outcome, or
    `starter_solves` names a language whose starter solves every hidden test.
    """
    reference = question["reference_solution"]
    for test in question["visible_tests"]:
        provider.script(reference, test["stdin"], ScriptedRun(stdout=test["expected_stdout"]))
    for position, test in enumerate(question["hidden_tests"], start=1):
        stdout = (reference_stdout or {}).get(position, test["expected_stdout"])
        provider.script(reference, test["stdin"], ScriptedRun(stdout=stdout))
    for language, source in question["starter_code"].items():
        for test in question["hidden_tests"]:
            if language == starter_solves:
                run = ScriptedRun(stdout=test["expected_stdout"])
            else:
                run = (starter or {}).get(language, ScriptedRun(stdout="\n"))
            provider.script(source, test["stdin"], run)


def _job(**overrides: Any) -> SimpleNamespace:
    job = SimpleNamespace(
        title="Site Reliability Engineer",
        experience_min_years=3,
        experience_max_years=6,
        compensation_json={"ctc_min": 1837000, "ctc_max": 2641000, "currency": "INR"},
        jd_markdown="Salary band 1837000 to 2641000.",
    )
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


def _skill(**overrides: Any) -> ContractSkill:
    values = dict(
        id=uuid.uuid4(),
        name="Log analysis and incident triage",
        bucket="must_have",
        priority=1,
        evidence_line="Has traced a production incident from logs to its root cause.",
    )
    values.update(overrides)
    return ContractSkill(**values)


async def _write(**overrides: Any) -> gen.CodingGeneration:
    kwargs = dict(
        job=_job(),
        contract_skill=_skill(),
        role_summary="Keeps the payments platform running and leads its incident response.",
        grade="managerial",
    )
    kwargs.update(overrides)
    return await gen.write_coding_question(None, **kwargs)


def _bounds() -> gen._Bounds:
    return gen._Bounds(visible_min=2, visible_max=3, hidden_min=5, hidden_max=10, max_expected_chars=4000)


def _assert_no_sentinel(text: str, where: str) -> None:
    for sentinel in SENTINELS:
        assert sentinel not in text, f"{sentinel} leaked into {where}"


# ── Refusals before any model call ───────────────────────────────────────────


async def test_disabled_execution_refuses_before_any_model_call(monkeypatch) -> None:
    router = Router()
    monkeypatch.setattr(llm_router, "invoke_llm", router)
    result = await _write()
    assert result.refusal == gen.REFUSAL_EXECUTION_DISABLED
    assert result.draft is None and result.degraded
    assert result.model_calls == 0 and router.calls == []


@pytest.mark.parametrize("field", ["evidence_line", "name"])
async def test_a_skill_with_nothing_to_write_from_refuses_before_any_model_call(monkeypatch, field) -> None:
    router = Router()
    monkeypatch.setattr(llm_router, "invoke_llm", router)
    with override_provider(FakeProvider()):
        result = await _write(contract_skill=_skill(**{field: "   "}))
    assert result.refusal == gen.REFUSAL_INSUFFICIENT_INPUT
    assert router.calls == []


async def test_a_behavioural_skill_is_a_caller_defect_not_a_degradation() -> None:
    with override_provider(FakeProvider()):
        with pytest.raises(ValueError, match="behavioural"):
            await _write(contract_skill=_skill(bucket="behavioural"))


async def test_an_unconfigured_language_is_a_caller_defect(monkeypatch) -> None:
    monkeypatch.setattr(llm_router, "invoke_llm", Router())
    with override_provider(FakeProvider()):
        with pytest.raises(ValueError, match="not configured"):
            await _write(languages=["python", "go"])


# ── The accepted path ────────────────────────────────────────────────────────


async def test_a_question_is_accepted_only_after_the_sandbox_proves_it(monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    question = _question()
    router = Router(question)
    monkeypatch.setattr(llm_router, "invoke_llm", router)
    provider = FakeProvider()
    _script(provider, question)
    with override_provider(provider):
        result = await _write()

    assert result.refusal is None and result.model_calls == 1
    draft = result.draft
    assert draft is not None
    assert router.calls[0][0] == gen.TASK_TYPE

    # The reference ran over EVERY test, visible and hidden, in its language;
    # each starter ran on the probe only, because none of them solved it.
    reference_run = provider.submissions[0]
    assert reference_run.language == "python"
    assert reference_run.keys == ("v1", "v2", "h1", "h2", "h3", "h4", "h5")
    starter_runs = {s.language: s.keys for s in provider.submissions[1:]}
    assert starter_runs == {language: ("h1", "h2") for language in LANGUAGES}

    # Candidate-safe by construction, and it round-trips through the model.
    projected = coding_payload.candidate_projection(draft.payload)
    assert tuple(projected) == coding_payload.CANDIDATE_FIELDS
    assert projected["languages"] == list(LANGUAGES)
    assert [t["id"] for t in projected["visible_tests"]] == ["v1", "v2"]
    assert projected["limits"]["java"]["cpu_seconds"] > projected["limits"]["python"]["cpu_seconds"]
    assert draft.statement == question["statement"]
    assert draft.rubric == {"criteria": list(coding_payload.CODING_REVIEW_CRITERIA), "payload_version": 2}

    # The key travels in its own fields, keyed h1..h5 in order.
    assert [t.key for t in draft.hidden_tests] == ["h1", "h2", "h3", "h4", "h5"]
    assert draft.hidden_tests[0].stdin == question["hidden_tests"][0]["stdin"]
    assert REFERENCE in draft.reference_source and draft.reference_language == "python"

    # The validation record is outcome classes and provenance, never content.
    validation = draft.validation
    assert validation["provider"] == "fake"
    assert validation["reference"]["outcomes"] == {k: "passed" for k in reference_run.keys}
    assert validation["starters"]["java"]["outcomes"] == {"h1": "wrong_answer", "h2": "wrong_answer"}
    assert validation["generation"] == {
        "task_type": gen.TASK_TYPE,
        "model_id": llm_providers.MODEL_TERRA,
        "prompt_version": registry.version(gen.PROMPT_NAME),
        "model_calls": 1,
    }
    assert validation["hidden_digest"] == gen.coding_keys.hidden_digest(draft.hidden_tests)

    for where, text in (
        ("payload", json.dumps(draft.payload)),
        ("statement", draft.statement),
        ("rubric", json.dumps(draft.rubric)),
        ("validation", json.dumps(validation)),
        ("repr", repr(draft)),
        ("result repr", repr(result)),
    ):
        _assert_no_sentinel(text, where)
    for record in caplog.records:
        _assert_no_sentinel(record.getMessage(), f"log record {record.name}")


async def test_a_reference_that_fails_its_own_test_is_fed_back_and_fixed(monkeypatch, caplog) -> None:
    """The reason names the test by key only; what the model needs to fix it
    (its own reference's stdout) travels in the next request and nowhere else."""
    caplog.set_level(logging.DEBUG)
    broken = _question()
    fixed = _question(reference_solution=f"# {REFERENCE} fixed\nprint(1)\n")
    router = Router(broken, fixed)
    monkeypatch.setattr(llm_router, "invoke_llm", router)
    provider = FakeProvider()
    wrong = f"/{HIDDEN_STDIN}-wrong\n"
    _script(provider, broken, reference_stdout={2: wrong})
    _script(provider, fixed)
    with override_provider(provider):
        result = await _write()

    assert result.draft is not None and result.model_calls == 2
    assert result.draft.validation["generation"]["model_calls"] == 2
    second = router.calls[1][1]
    assert second[-2] == {"role": "assistant", "content": json.dumps(broken)}
    follow_up = second[-1]["content"]
    assert "hidden test 2 (h2)" in follow_up and "different answer" in follow_up
    assert "the reference solution printed:\n" + wrong in follow_up
    for record in caplog.records:
        _assert_no_sentinel(record.getMessage(), f"log record {record.name}")


async def test_a_starter_that_solves_every_hidden_test_is_rejected(monkeypatch) -> None:
    question = _question()
    monkeypatch.setattr(llm_router, "invoke_llm", Router(question, question, question))
    provider = FakeProvider()
    _script(provider, question, starter_solves="javascript")
    with override_provider(provider):
        result = await _write()
    assert result.refusal == gen.REFUSAL_GENERATION_FAILED
    assert result.model_calls == 3
    assert any("javascript starter code already solves the problem" in r for r in result.reasons)
    # It passed the probe, so it was run against the rest before the verdict.
    js_runs = [s.keys for s in provider.submissions if s.language == "javascript"]
    assert ("h1", "h2") in js_runs and ("h3", "h4", "h5") in js_runs


async def test_a_starter_that_does_not_compile_is_rejected_with_its_message(monkeypatch) -> None:
    question = _question()
    good = _question(starter_code=dict(STARTERS, java=STARTERS["java"] + "// fixed\n"))
    router = Router(question, good)
    monkeypatch.setattr(llm_router, "invoke_llm", router)
    provider = FakeProvider()
    broken = ScriptedRun(outcome=ExecutionOutcome.COMPILE_ERROR, compile_output="Main.java:3: error: ';' expected")
    _script(provider, question, starter={"java": broken})
    _script(provider, good)
    with override_provider(provider):
        result = await _write()
    assert result.draft is not None and result.model_calls == 2
    follow_up = router.calls[1][1][-1]["content"]
    assert "the java starter code failed to compile" in follow_up
    assert "Main.java:3: error" in follow_up


async def test_a_starter_that_crashes_on_the_probe_is_rejected(monkeypatch) -> None:
    question = _question()
    monkeypatch.setattr(llm_router, "invoke_llm", Router(question, question, question))
    provider = FakeProvider()
    _script(provider, question, starter={"python": ScriptedRun(outcome=ExecutionOutcome.RUNTIME_ERROR)})
    with override_provider(provider):
        result = await _write()
    assert result.refusal == gen.REFUSAL_GENERATION_FAILED
    assert any("python starter code crashed" in r for r in result.reasons)


class _Timed:
    """Delegates to a scripted double and reports a fixed CPU time per test."""

    def __init__(self, inner: FakeProvider, cpu_ms: int) -> None:
        self.inner, self.cpu_ms, self.name = inner, cpu_ms, inner.name

    async def run(self, **kwargs: Any):
        return [dataclasses.replace(r, cpu_ms=self.cpu_ms) for r in await self.inner.run(**kwargs)]

    async def submit(self, **kwargs: Any):
        return await self.inner.submit(**kwargs)

    async def collect(self, ticket):
        return await self.inner.collect(ticket)

    async def discard(self, ticket):
        return await self.inner.discard(ticket)

    async def health(self):
        return await self.inner.health()


async def test_a_reference_too_close_to_the_time_limit_is_rejected(monkeypatch) -> None:
    """Headroom 0.5 of a 2 second CPU limit is 1000 ms; 1500 ms is too slow."""
    question = _question()
    monkeypatch.setattr(llm_router, "invoke_llm", Router(question, question, question))
    provider = FakeProvider()
    _script(provider, question)
    with override_provider(_Timed(provider, cpu_ms=1500)):
        result = await _write()
    assert result.refusal == gen.REFUSAL_GENERATION_FAILED
    assert any("too much of the time limit" in r for r in result.reasons)

    fast = FakeProvider()
    _script(fast, question)
    monkeypatch.setattr(llm_router, "invoke_llm", Router(question))
    with override_provider(_Timed(fast, cpu_ms=300)):
        accepted = await _write()
    assert accepted.draft is not None
    assert accepted.draft.validation["reference"]["max_cpu_ms"] == 300


# ── A sandbox that cannot answer ends the loop ───────────────────────────────


async def test_a_sandbox_outage_refuses_without_a_retry_storm(monkeypatch) -> None:
    question = _question()
    router = Router(question, question, question)
    monkeypatch.setattr(llm_router, "invoke_llm", router)
    provider = FakeProvider(unavailable="queue_full")
    with override_provider(provider):
        result = await _write()
    assert result.refusal == gen.REFUSAL_EXECUTION_UNAVAILABLE
    # Three loop attempts were available; one model call was spent.
    assert result.model_calls == 1 and len(router.calls) == 1


async def test_a_sandbox_fault_on_one_test_is_an_outage_not_a_bad_question(monkeypatch) -> None:
    question = _question()
    router = Router(question, question, question)
    monkeypatch.setattr(llm_router, "invoke_llm", router)
    provider = FakeProvider()
    _script(provider, question)
    provider.script(
        question["reference_solution"],
        question["hidden_tests"][3]["stdin"],
        ScriptedRun(outcome=ExecutionOutcome.INTERNAL_ERROR),
    )
    with override_provider(provider):
        result = await _write()
    assert result.refusal == gen.REFUSAL_EXECUTION_UNAVAILABLE
    assert len(router.calls) == 1


async def test_a_deterministic_defect_costs_no_sandbox_run(monkeypatch) -> None:
    bad = _question(starter_code=dict(STARTERS, java="class Solution {}\n"))
    good = _question()
    router = Router(bad, good)
    monkeypatch.setattr(llm_router, "invoke_llm", router)
    provider = FakeProvider()
    _script(provider, good)
    with override_provider(provider):
        result = await _write()
    assert result.draft is not None
    assert "public class Main" in router.calls[1][1][-1]["content"]
    # Every sandbox run belongs to the accepted answer: none was spent on the bad one.
    assert provider.submissions[0].keys[0] == "v1" and len(provider.submissions) == 1 + len(LANGUAGES)


# ── The deterministic checks, one defect at a time ──────────────────────────


def _parse(question: dict[str, Any], *, avoid: tuple[str, ...] = ()) -> list[str]:
    parsed, reasons = gen._parse(
        json.dumps(question), languages=LANGUAGES, reference_language="python",
        bounds=_bounds(), avoid_titles=avoid,
    )
    assert (parsed is None) == bool(reasons)
    return reasons


def test_a_valid_answer_passes_every_deterministic_check() -> None:
    assert _parse(_question()) == []


def _hidden(n: int) -> list[dict[str, str]]:
    return [{"stdin": f"case {i}\n", "expected_stdout": f"{i}\n"} for i in range(1, n + 1)]


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"starter_code": dict(STARTERS, java="class Solution {}")}, "java starter code must declare public class Main"),
        ({"starter_code": {k: v for k, v in STARTERS.items() if k != "cpp"}}, "write starter code for cpp"),
        ({"starter_code": dict(STARTERS, go="package main")}, "remove go"),
        ({"hidden_tests": _hidden(4)}, "between 5 and 10 hidden tests"),
        ({"hidden_tests": _hidden(11)}, "between 5 and 10 hidden tests"),
        ({"visible_tests": [{"stdin": "0\n", "expected_stdout": "NONE\n"}]}, "between 2 and 3 visible tests"),
        (
            {"hidden_tests": _hidden(4) + [{"stdin": "0\n", "expected_stdout": "x\n"}]},
            "repeats the input of visible test 2 (v2)",
        ),
        (
            {"hidden_tests": [{"stdin": f"{i}\n", "expected_stdout": "same\n"} for i in range(5)]},
            "all expect the same output",
        ),
        (
            {"hidden_tests": _hidden(4) + [{"stdin": "", "expected_stdout": " \n"}]},
            "hidden test 5 (h5) must expect some output",
        ),
        (
            {"hidden_tests": _hidden(4) + [{"stdin": "x" * 9000, "expected_stdout": "1\n"}]},
            "stdin of hidden test 5 (h5) exceeds",
        ),
        (
            {"hidden_tests": _hidden(4) + [{"stdin": "9\n", "expected_stdout": "y" * 4001}]},
            "expected output of hidden test 5 (h5) exceeds 4000",
        ),
        ({"title": ""}, "title of 1 to"),
        ({"statement": "Too short."}, "statement of 80 to"),
        ({"output_format": ""}, "output_format of 1 to"),
        ({"reference_solution": "  "}, "complete reference_solution in python"),
        ({"expected_approach": ""}, "expected_approach of 1 to"),
        ({"title": "Busy endpoint " + chr(8212) + " triage"}, "em dash from the title"),
        (
            {"statement": _question()["statement"] + " You will score 7/10 if half the tests pass."},
            "rephrase the statement",
        ),
        (
            {"statement": "Based on the information available, " + _question()["statement"]},
            "in the statement",
        ),
    ],
)
def test_a_deterministic_defect_is_named_as_an_instruction(overrides: dict[str, Any], expected: str) -> None:
    reasons = _parse(_question(**overrides))
    assert any(expected in reason for reason in reasons), reasons


def test_a_hidden_input_shown_in_the_statement_is_refused() -> None:
    hidden = _hidden(4) + [{"stdin": "3\n/payments 503\n/login 200\n", "expected_stdout": "/payments\n"}]
    statement = _question()["statement"] + " For example: 3 /payments 503 /login 200."
    reasons = _parse(_question(hidden_tests=hidden, statement=statement))
    assert any("hidden test 5 (h5) appears in the statement" in r for r in reasons), reasons


def test_a_title_already_used_on_the_job_is_refused() -> None:
    reasons = _parse(_question(), avoid=("busiest   failing endpoint",))
    assert any("already in use" in r for r in reasons)


@pytest.mark.parametrize("raw", ["not json", "[1, 2]"])
def test_an_answer_that_is_not_one_object_is_refused(raw: str) -> None:
    parsed, reasons = gen._parse(
        raw, languages=LANGUAGES, reference_language="python", bounds=_bounds(), avoid_titles=()
    )
    assert parsed is None and reasons == ["return one JSON object in exactly the shape asked for"]


def test_no_deterministic_reason_quotes_a_test() -> None:
    """Reasons are logged by the loop, so they may name a test and never show one."""
    hidden = [{"stdin": f"{HIDDEN_STDIN}{i}", "expected_stdout": HIDDEN_OUT} for i in range(5)]
    hidden.append({"stdin": "x" * 9000 + HIDDEN_STDIN, "expected_stdout": HIDDEN_OUT * 400})
    reasons = _parse(_question(hidden_tests=hidden))
    assert reasons
    for reason in reasons:
        _assert_no_sentinel(reason, "a reason")


# ── What the model is given ──────────────────────────────────────────────────


def _messages() -> list[dict[str, str]]:
    from app.services.code_execution import limits

    return gen.build_messages(
        job=_job(),
        skill=_skill(),
        role_summary="Keeps the payments platform running.",
        grade="managerial",
        languages=list(LANGUAGES),
        reference_language="python",
        limits={key: limits.for_language(key) for key in LANGUAGES},
        avoid_titles=("Earlier problem",),
    )


def test_compensation_and_the_jd_never_reach_the_prompt() -> None:
    serialised = json.dumps(_messages()).lower()
    for needle in ("1837000", "2641000", "ctc", "compensation", "salary", "inr", "resume"):
        assert needle not in serialised, needle


def test_the_request_carries_the_skill_the_bounds_and_the_titles_to_avoid() -> None:
    system, user = _messages()
    assert system == {"role": "system", "content": registry.render(gen.PROMPT_NAME)}
    request = json.loads(user["content"])
    assert request["skill"]["evidence_line"].startswith("Has traced a production incident")
    assert request["experience"] == "3 to 6 years"
    assert [lang["key"] for lang in request["languages"]] == list(LANGUAGES)
    assert request["reference_language"] == "python"
    assert request["hidden_tests"] == {"min": 5, "max": 10}
    assert request["titles_to_avoid"] == ["Earlier problem"]


# ── The prompt and the task type ─────────────────────────────────────────────


def test_the_prompt_meets_the_gated_prompt_contract() -> None:
    """The checks `tests/test_no_meta_commentary.py` runs over GATED_PROMPTS,
    applied here until the orchestrator adds this prompt to that list."""
    text = registry.render(gen.PROMPT_NAME)
    for marker in (gs.EXAMPLES_HEADING, "GOOD EXAMPLE", gs.BAD_EXAMPLE_OPEN, gs.BAD_EXAMPLE_CLOSE, "EDGE CASE"):
        assert marker in text, marker
    start = text.index(gs.BAD_EXAMPLE_OPEN)
    fenced = text[start:text.index(gs.BAD_EXAMPLE_CLOSE, start)]
    assert gs.meta_commentary_defects(fenced), "the bad example demonstrates nothing"
    assert not gs.meta_commentary_defects(gs.strip_bad_examples(text), location=gen.PROMPT_NAME)
    assert chr(8212) not in text and not text.startswith("#")
    declared, _, digest = registry.version(gen.PROMPT_NAME).partition("+")
    assert declared.isdigit() and len(digest) == 8


def test_the_task_type_writes_on_the_reasoning_tier_and_is_bounded_twice() -> None:
    task = gen.TASK_TYPE
    assert llm_providers.model_for(task) == llm_providers.MODEL_TERRA
    assert llm_providers.temperature_for(task) == 0.4
    timeout, budget = llm_providers.timeout_for(task), llm_providers.total_budget_for(task)
    assert timeout < budget <= timeout * llm_providers.retry_budget_for(task) + 1
    assert llm_providers.max_tokens_for(task) == 8192


def test_a_generation_is_a_draft_or_a_refusal_never_both() -> None:
    with pytest.raises(ValueError):
        gen.CodingGeneration(draft=None, refusal=None)
    with pytest.raises(ValueError, match="unknown"):
        gen.CodingGeneration(draft=None, refusal="something_else")
