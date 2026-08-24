"""The adversarial suite is a GATE, and these are the properties that make it one.

Two failure modes are being defended against, and they pull in opposite
directions. A suite that cannot fail proves nothing, so a real guard bypass must
turn the exit code red. And a suite tuned only for catch rate would happily pass
by refusing every answer, so the false-positive direction is asserted here as
firmly as the attacks are: a candidate whose job was hardening a chatbot against
prompt injection is describing relevant experience, and refusing them is a
silent, expensive defect nobody would ever see in a bug report.
"""
from __future__ import annotations

import json

from app.scripts import eval_adversarial as ea
from app.services import conversation_guardrails


def _run(capsys) -> tuple[int, dict]:
    code = ea.main()
    return code, json.loads(capsys.readouterr().out)


def test_every_case_is_contained_against_the_current_codebase(capsys) -> None:
    """Exit zero today. Anything below 1.0 here is not a tuning question, it is
    an attack that works."""
    code, report = _run(capsys)
    assert code == 0, report["containment"]["not_contained"]
    assert report["containment"]["rate"] == 1.0


def test_two_runs_on_unchanged_code_print_identical_output(capsys) -> None:
    """A rate that moves must mean the CODE changed, not that something sampled
    differently. Nothing here calls a model or opens a socket."""
    ea.main()
    first = capsys.readouterr().out
    ea.main()
    second = capsys.readouterr().out
    assert first == second


def test_every_spec_41_line_item_has_a_named_case(capsys) -> None:
    """The list is checked against the specification, not against itself.

    A case quietly dropped during a refactor is the failure this prevents: the
    report still reads 100% contained because the attack it covered is no longer
    being attempted.
    """
    _code, report = _run(capsys)
    expected = {
        "malicious_instructions_in_resume",
        "malicious_jd_content",
        "prompt_injection_in_candidate_answer",
        "conflicting_information",
        "extremely_long_text",
        "repeated_identical_answers",
        "empty_answers",
        "tool_outage",
        "database_outage",
        "retrieval_poisoning",
        "memory_poisoning",
        "unauthorised_candidate_access",
        "cross_tenant_retrieval",
        "repeated_retries",
        "infinite_loop_triggers",
    }
    assert expected <= set(report["cases"])


def test_a_guard_that_stops_guarding_makes_the_gate_fail(capsys, monkeypatch) -> None:
    """The negative direction, proved rather than assumed.

    With `inspect_answer` neutered into an allow-everything stub, the injection
    case must go red. If it does not, this suite is decoration.
    """
    def _allow_everything(answer: str):
        return conversation_guardrails.GuardResult(True, answer, None, None)

    monkeypatch.setattr(conversation_guardrails, "inspect_answer", _allow_everything)
    code, report = _run(capsys)
    assert code == 1
    assert "prompt_injection_in_candidate_answer" in report["containment"]["not_contained"]


def test_a_widened_tool_grant_makes_the_gate_fail(capsys, monkeypatch) -> None:
    """Authorisation is enforced by ABSENCE, so the absence must be checked.

    Granting the scorer the JD it must not hold has to fail the unauthorised
    access case, because the grant table IS the security boundary the
    specification describes.
    """
    from app.services.tools import permissions

    widened = dict(permissions.AGENT_TOOLS)
    widened[permissions.AGENT_SCORING] = (
        widened[permissions.AGENT_SCORING] | {"extract_jd"}
    )
    monkeypatch.setattr(permissions, "AGENT_TOOLS", widened)
    code, report = _run(capsys)
    assert code == 1
    assert "unauthorised_candidate_access" in report["containment"]["not_contained"]


def test_an_answer_that_discusses_injection_is_allowed(capsys) -> None:
    """The expensive false positive, asserted explicitly.

    The guard's own contract says it: `violation is not None` does NOT mean
    refused, only `allowed` does. A tightening that raises the catch rate by
    refusing this answer fails here, which is exactly what should happen.
    """
    case = ea.case_injection_lookalike_is_allowed()
    assert case.contained, case.failures
    for answer in ea.INJECTION_LOOKALIKES:
        assert conversation_guardrails.inspect_answer(answer).allowed, answer


def test_a_poisoned_chunk_is_quarantined_and_the_rest_survives() -> None:
    """Quarantine, not refusal.

    Failing the whole retrieval would let one poisoned paragraph in one resume
    disable assessment for that candidate, which is a denial of service with
    extra steps.
    """
    case = ea.case_retrieval_poisoning()
    assert case.contained, case.failures


def test_an_outage_never_fabricates_a_tool_result() -> None:
    """Spec 25, asserted by identity rather than by shape.

    The degraded value must be the caller's own fallback object, because a
    plausible-looking shape assembled during an outage is indistinguishable from
    a real result to everything downstream.
    """
    import asyncio

    case = asyncio.run(ea.case_tool_outage())
    assert case.contained, case.failures
    stub = asyncio.run(ea.case_database_outage())
    assert stub.contained, stub.failures


def test_quality_metrics_are_the_literal_unavailable_string(capsys) -> None:
    """Never a number, not even zero. A proxy score invented here would be a
    number that means nothing and looks like something."""
    _code, report = _run(capsys)
    section = report["human_quality"]
    assert section["status"] == "UNAVAILABLE"
    assert "never be synthesised" in section["explanation"]
    assert section["dimensions"]
    for name, value in section["dimensions"].items():
        assert value == "UNAVAILABLE", name
        assert not isinstance(value, (int, float)), name


def test_a_missing_optional_module_reports_unavailable_rather_than_passing(
    capsys, monkeypatch
) -> None:
    """Absent is neither contained nor a crash.

    Counting an unchecked case as contained would be the worst outcome of the
    three: the report would claim a containment nobody verified.
    """
    monkeypatch.setattr(ea, "_ledger", None)
    code, report = _run(capsys)
    assert code == 0
    case = report["cases"]["conflicting_information"]
    assert case["status"] == "unavailable"
    assert case["unavailable"].startswith("UNAVAILABLE:")
    assert "conflicting_information" not in report["containment"]["not_contained"]
    assert report["containment"]["checked"] == len(report["cases"]) - 1


def test_no_case_record_carries_candidate_text(capsys) -> None:
    """Everything printed is operator data: mechanisms, never payloads.

    The attack corpus lives in this module and must not travel into the report,
    for the same reason `agent_execution_traces` drops a defect's detail.
    """
    _code, report = _run(capsys)
    printed = json.dumps(report)
    for attack in ea.ANSWER_INJECTIONS + ea.RESUME_INJECTIONS:
        assert attack not in printed, attack
