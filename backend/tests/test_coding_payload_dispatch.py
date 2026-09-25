"""The one format vocabulary reads BOTH coding shapes, and projects each safely.

`assessment_formats.types` stays the only place a stored payload becomes what
a candidate sees. A v2 (executed) coding payload dispatches to
`coding_assessment.payload`; a v1 payload keeps today's projection, so every
coding row written before Phase 4 stays readable exactly as it was.
"""
from __future__ import annotations

import uuid

import pytest

from app.services.assessment_formats import types
from app.services.code_execution import limits as execution_limits
from app.services.coding_assessment import payload as coding_payload


def _v2() -> dict:
    return coding_payload.CodingPayloadV2(
        payload_version=2,
        title="Busiest failing endpoint",
        io="stdin_stdout",
        input_format="n, then n lines of path and status.",
        output_format="One line: the path, or NONE.",
        constraints="0 <= n <= 10000",
        languages=["python", "java"],
        starter_code={
            "python": "import sys\nprint()\n",
            "java": "public class Main { public static void main(String[] a) { System.out.println(); } }\n",
        },
        visible_tests=[{"id": "v1", "stdin": "1\n/a 500\n", "expected_stdout": "/a\n"}],
        limits={
            key: coding_payload.LanguageLimits.of(execution_limits.for_language(key))
            for key in ("python", "java")
        },
    ).model_dump()


V1 = {
    "language": "python",
    "starter_code": "def solve():\n    pass\n",
    "constraints": "",
    "expected_approach": "Count per path.",
    "language_options": [],
}


def test_a_v2_payload_parses_as_the_executed_shape() -> None:
    parsed = types.parse_payload(types.CODING, _v2())
    assert isinstance(parsed, coding_payload.CodingPayloadV2)


def test_a_v1_payload_still_parses_as_the_legacy_shape() -> None:
    assert isinstance(types.parse_payload(types.CODING, V1), types.CodingPayload)


def test_the_candidate_view_of_v2_is_exactly_the_named_fields() -> None:
    view = types.candidate_view(uuid.uuid4(), types.CODING, _v2())
    assert tuple(view) == coding_payload.CANDIDATE_FIELDS


def test_the_candidate_view_of_v1_never_carries_the_approach() -> None:
    view = types.candidate_view(uuid.uuid4(), types.CODING, V1)
    assert "expected_approach" not in view
    assert view["language"] == "python"


def test_a_v2_answer_must_use_one_of_the_questions_languages() -> None:
    payload = _v2()
    parsed = types.parse_answer(types.CODING, payload, {"language": "java", "code": "x"})
    assert parsed.language == "java"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="not permitted"):
        types.parse_answer(types.CODING, payload, {"language": "cpp", "code": "x"})


def test_a_v2_payload_carrying_a_hidden_test_is_refused_by_the_view() -> None:
    payload = {**_v2(), "hidden_tests": [{"stdin": "1", "expected_stdout": "2"}]}
    with pytest.raises(ValueError):
        types.candidate_view(uuid.uuid4(), types.CODING, payload)
