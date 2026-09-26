"""The v2 coding payload: candidate-safe by construction, exact on limits.

Pure. The database half of "the answer key is never in the payload" is in
`tests/test_coding_tables.py`; this is the model half, which is the one a
writer meets first.
"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from app.core.config import CODE_EXECUTION_LANGUAGE_KEYS
from app.services.code_execution import ExecutionLimits, TestInput
from app.services.coding_assessment import payload as coding_payload

_LIMITS = {
    "cpu_seconds": 2.0,
    "cpu_extra_seconds": 0.5,
    "wall_seconds": 5.0,
    "memory_kb": 262144,
    "stack_kb": 65536,
    "max_processes": 60,
    "max_file_kb": 1024,
    "max_output_chars": 4000,
}


def _payload(**overrides) -> dict:
    base = {
        "payload_version": 2,
        "title": "Busiest failing endpoint",
        "io": "stdin_stdout",
        "input_format": "n, then n lines of path and status.",
        "output_format": "One path, or NONE.",
        "constraints": "0 <= n <= 10000",
        "languages": ["python", "java"],
        "starter_code": {"python": "print()\n", "java": "public class Main {}\n"},
        "visible_tests": [
            {"id": "v1", "stdin": "1\n/a 500\n", "expected_stdout": "/a\n", "explanation": "One failure."},
            {"id": "v2", "stdin": "0\n", "expected_stdout": "NONE\n", "explanation": ""},
        ],
        "limits": {"python": dict(_LIMITS), "java": dict(_LIMITS, cpu_seconds=4.0)},
    }
    base.update(overrides)
    return base


def test_the_candidate_projection_is_exactly_the_named_fields() -> None:
    projected = coding_payload.candidate_projection(_payload())
    assert tuple(projected) == coding_payload.CANDIDATE_FIELDS
    assert projected["visible_tests"][0] == {
        "id": "v1", "stdin": "1\n/a 500\n", "expected_stdout": "/a\n", "explanation": "One failure."
    }


@pytest.mark.parametrize(
    "key", ["hidden_tests", "reference_solution", "reference_source", "expected_approach", "notes"]
)
def test_a_payload_cannot_carry_anything_that_is_not_candidate_facing(key: str) -> None:
    """The answer key cannot be stored in a valid v2 payload at all, and a
    field nobody reviewed is refused rather than stored."""
    with pytest.raises(ValidationError):
        coding_payload.parse(_payload(**{key: [{"stdin": "x", "expected_stdout": "y"}]}))


def test_a_visible_test_cannot_carry_an_extra_field_either() -> None:
    bad = _payload()
    bad["visible_tests"][0]["hidden"] = True
    with pytest.raises(ValidationError):
        coding_payload.parse(bad)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda p: p["starter_code"].pop("java"), "starter_code"),
        (lambda p: p["starter_code"].__setitem__("cpp", "int main(){}"), "starter_code"),
        (lambda p: p["starter_code"].__setitem__("java", "   "), "empty"),
        (lambda p: p["limits"].pop("java"), "limits"),
        (lambda p: p.__setitem__("languages", ["python", "python"]), "unique"),
        (lambda p: p.__setitem__("languages", ["cobol"]), "unknown"),
        (lambda p: p["visible_tests"][1].__setitem__("id", "v3"), "visible test ids"),
        (lambda p: p["visible_tests"][0].__setitem__("expected_stdout", "  \n"), "expect some output"),
        (lambda p: p.__setitem__("io", "function"), "io"),
        (lambda p: p.__setitem__("payload_version", 1), "payload_version"),
    ],
)
def test_an_inconsistent_payload_is_refused(mutate, message: str) -> None:
    bad = copy.deepcopy(_payload())
    mutate(bad)
    with pytest.raises(ValidationError) as raised:
        coding_payload.parse(bad)
    assert message in str(raised.value)


def test_a_language_the_registry_knows_but_this_deployment_does_not_offer_stays_readable(monkeypatch) -> None:
    """A stored question outlives a configuration change; the registry, not
    the configured list, decides what is readable."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "code_execution_languages", "python")
    parsed = coding_payload.parse(_payload())
    assert parsed.languages == ["python", "java"]
    assert set(parsed.languages) <= set(CODE_EXECUTION_LANGUAGE_KEYS)


def test_limits_round_trip_exactly_and_are_per_language() -> None:
    parsed = coding_payload.parse(_payload())
    java = coding_payload.limits_for(parsed, "java")
    assert java == ExecutionLimits(**dict(_LIMITS, cpu_seconds=4.0))
    stored = coding_payload.LanguageLimits.of(java)
    assert coding_payload.limits_for(
        coding_payload.parse(_payload(limits={"python": dict(_LIMITS), "java": stored.model_dump()})), "java"
    ) == java


def test_limits_for_a_language_the_question_does_not_offer_raises() -> None:
    with pytest.raises(KeyError, match="cpp"):
        coding_payload.limits_for(coding_payload.parse(_payload()), "cpp")


def test_visible_inputs_carry_no_expected_output() -> None:
    inputs = coding_payload.visible_inputs(coding_payload.parse(_payload()))
    assert inputs == (TestInput(key="v1", stdin="1\n/a 500\n"), TestInput(key="v2", stdin="0\n"))


def test_is_v2_reads_the_version_and_nothing_else() -> None:
    assert coding_payload.is_v2(_payload())
    assert not coding_payload.is_v2({"language": "python", "expected_approach": "x"})
    assert not coding_payload.is_v2(None)
    assert not coding_payload.is_v2({"payload_version": "2"})


def test_test_keys_are_opaque_positions() -> None:
    assert coding_payload.visible_test_key(1) == "v1"
    assert coding_payload.hidden_test_key(12) == "h12"
