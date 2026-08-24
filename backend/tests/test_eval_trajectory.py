"""The trajectory eval is a GATE, and these are the properties that make it one.

A gate that cannot fail is not a gate. The expensive failure here is not a
missed anti-pattern; it is an eval that prints a clean report whatever the code
does, because then every future change is "verified" by something that was never
looking. So the tests below spend most of their effort proving the negative
direction: a mislabelled trajectory fails, a broken probe fails, and a quality
figure nobody can measure is never printed as a number.
"""
from __future__ import annotations

import json

import pytest

from app.scripts import eval_trajectory as et
from app.services import agent_loop
from app.services.reasoning import planner
from app.services.reliability import budget as budgeting
from app.services.tools import permissions


def _run(capsys) -> tuple[int, dict]:
    code = et.main()
    return code, json.loads(capsys.readouterr().out)


def test_the_eval_passes_against_the_current_codebase(capsys) -> None:
    """Exit zero today, or the number below is aspirational rather than defended.

    The same rule `eval_interview`'s thresholds are written under: a rate
    allowed to fail silently on the day it lands is a rate nobody is defending.
    """
    code, report = _run(capsys)
    assert code == 0, report["trajectory_rules"]["mismatches"]
    assert report["trajectory_rules"]["rate"] == 1.0
    assert report["live_probes"]["ok"] is True


def test_two_runs_on_unchanged_code_print_identical_output(capsys) -> None:
    """Determinism is what makes a moving rate mean the CODE changed.

    An eval whose output wobbles cannot distinguish a regression from noise, and
    the first time it wobbles somebody starts ignoring it.
    """
    et.main()
    first = capsys.readouterr().out
    et.main()
    second = capsys.readouterr().out
    assert first == second


def test_a_mislabelled_trajectory_makes_the_gate_fail(capsys, monkeypatch) -> None:
    """The negative direction, proved rather than assumed.

    A trajectory declaring itself clean while regenerating the identical answer
    twice must be caught. If this passes, every other assertion in this file is
    measuring nothing.
    """
    def _poisoned() -> list[et.Trajectory]:
        return [
            et.Trajectory(
                name="claims_to_be_clean",
                task_type="ppi_report",
                agent=permissions.AGENT_PPI_REPORT,
                steps=(
                    et.Step(
                        stage=et.STAGE_EXECUTE,
                        agent=permissions.AGENT_PPI_REPORT,
                        output_digest="same",
                    ),
                    et.Step(
                        stage=et.STAGE_EXECUTE,
                        agent=permissions.AGENT_PPI_REPORT,
                        start_offset_s=0.5,
                        output_digest="same",
                    ),
                ),
                expected=frozenset(),
            )
        ]

    monkeypatch.setattr(et, "corpus", _poisoned)
    code, report = _run(capsys)
    assert code == 1
    assert report["trajectory_rules"]["mismatches"]


def test_a_broken_live_probe_makes_the_gate_fail(capsys, monkeypatch) -> None:
    """The probes are load-bearing too, not decoration beside the corpus."""
    monkeypatch.setattr(
        et, "_probe_tool_surface_is_read_only", lambda: ["a write tool is reachable"]
    )
    code, report = _run(capsys)
    assert code == 1
    assert "tool_surface_is_read_only" in report["live_probes"]["problems"]


@pytest.mark.parametrize(
    "code",
    [code for code, _ in et.RULES],
)
def test_every_rule_detects_the_trajectory_built_to_trip_it(code: str) -> None:
    """One case per rule, and the case must be caught by THAT rule.

    Parameterised so a rule that stopped detecting its own defect names itself
    in the failure, rather than hiding inside one aggregate rate.
    """
    labelled = [run for run in et.corpus() if code in run.expected]
    assert labelled, f"no trajectory exercises {code}"
    for run in labelled:
        assert code in et.evaluate(run), f"{run.name} was not caught by {code}"


def test_a_healthy_run_trips_nothing() -> None:
    """The false-positive direction. A rule that flags clean runs gets muted,
    and a muted rule protects nothing."""
    for run in et.corpus():
        if run.expected:
            continue
        assert et.evaluate(run) == {}, f"{run.name} was falsely flagged"


def test_the_healthy_trajectory_is_built_from_the_real_planner() -> None:
    """A hand-written fixture would agree with a planner that had started
    declaring its dependencies wrongly, and the disagreement is the only thing
    worth detecting."""
    healthy = et._healthy()
    expected = planner.plan("ppi_report", permissions.AGENT_PPI_REPORT).order
    assert tuple(step.stage for step in healthy.steps)[: len(expected)] == expected


def test_the_ceilings_are_read_from_the_modules_that_own_them() -> None:
    """A second copy of a limit is a limit that eventually disagrees.

    Raising MAX_REPLANS in `reliability.budget` must raise it here; lowering it
    must fail the corpus rather than being quietly reproduced.
    """
    reflections = et.Trajectory(
        name="probe",
        task_type="ppi_report",
        agent=permissions.AGENT_PPI_REPORT,
        steps=tuple(
            et.Step(stage=et.STAGE_REFLECT, agent=permissions.AGENT_PPI_REPORT)
            for _ in range(budgeting.MAX_REPLANS)
        ),
    )
    assert et.INFINITE_SELF_CRITIQUE not in et.evaluate(reflections)
    one_more = et.Trajectory(
        name="probe",
        task_type="ppi_report",
        agent=permissions.AGENT_PPI_REPORT,
        steps=reflections.steps
        + (et.Step(stage=et.STAGE_REFLECT, agent=permissions.AGENT_PPI_REPORT),),
    )
    assert et.INFINITE_SELF_CRITIQUE in et.evaluate(one_more)


def test_the_deadline_rule_predicts_rather_than_observes() -> None:
    """The codebase's own scar, asserted at the trajectory level.

    Two 24s attempts under a 26s deadline: the second one passes `elapsed >=
    deadline` and must still be a violation, because it could not FINISH inside
    the budget.
    """
    run = et.Trajectory(
        name="probe",
        task_type="interviewer",
        agent=permissions.AGENT_INTERVIEWER,
        steps=(
            et.Step(stage=et.STAGE_EXECUTE, start_offset_s=0.0, duration_s=24.0),
            et.Step(stage=et.STAGE_EXECUTE, start_offset_s=24.0, duration_s=24.0),
        ),
        deadline_seconds=agent_loop.INTERACTIVE_DEADLINE,
    )
    assert et.DEADLINE_OBSERVED_NOT_PREDICTED in et.evaluate(run)


def test_quality_metrics_are_the_literal_unavailable_string(capsys) -> None:
    """Never a number, not even zero.

    An unmeasurable quality figure reported as 0.0 is a number that means
    nothing and looks like something, and the first person to build a dashboard
    on it will plot it as if it did.
    """
    _code, report = _run(capsys)
    section = report["human_quality"]
    assert section["status"] == "UNAVAILABLE"
    assert "never be synthesised" in section["explanation"]
    assert section["dimensions"], "the dimensions must be enumerable, not implied"
    for name, value in section["dimensions"].items():
        assert value == "UNAVAILABLE", name
        assert not isinstance(value, (int, float)), name


def test_cost_and_latency_counters_are_reported_and_never_thresholded(capsys) -> None:
    """Spec 43: the targets come from live instrumentation, not from a guess
    made before anything was measured."""
    _code, report = _run(capsys)
    counters = report["counters"]
    assert counters["model_calls"] >= 1
    assert "threshold" not in counters
    assert "never thresholded" in counters["note"]


def test_a_missing_optional_module_reports_unavailable_and_does_not_fail(
    capsys, monkeypatch
) -> None:
    """Absent is neither a pass nor a crash.

    A check that quietly vanished on an ImportError is a check that has stopped
    protecting anything while still appearing in the list, so it says so out
    loud. It is also not a regression in the trajectory rules, so it does not
    fail the gate.
    """
    monkeypatch.setattr(et, "_envelope", None)
    code, report = _run(capsys)
    assert code == 0
    optional = report["optional_checks"]["workflow_run_budget"]
    assert optional["status"] == "unavailable"
    assert optional["notes"] and optional["notes"][0].startswith("UNAVAILABLE:")


def test_no_trajectory_record_carries_content() -> None:
    """A digest stands in for the output and never IS the output.

    Same rule `agent_execution_traces` follows: a detail can quote a candidate,
    and this file is read far more widely than a transcript.
    """
    for run in et.corpus():
        for step in run.steps:
            for value in (step.input_digest, step.output_digest, step.query_digest):
                assert len(value) < 64, f"{run.name}: a digest field grew into content"
