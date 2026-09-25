"""The coding sweeps and tasks: reconcile asks the table, the probe reports,
the verification is green only when every check ran and passed.
"""
from __future__ import annotations

import json
import logging
import uuid

import pytest
from sqlalchemy import text

from app.core.db import superadmin_scope
from app.services import deletion_requests
from app.services.code_execution import ExecutionOutcome
from app.services.code_execution.fake import FakeProvider, ScriptedRun
from app.services.coding_assessment import sweeps
from app.workers import coding_tasks, registry, schedule
from app.workers.registry import Route
from app.workers.runtime import PermanentTaskFailure, worker_session
from tests.test_coding_submit_flow import _submitted
from tests.test_coding_tables import (  # noqa: F401  (fixtures)
    _one,
    _persist,
    factory,
    world,
)


async def _age(factory, submission_id, *, minutes: int, **columns) -> None:
    sets = ", ".join(f"{name} = :{name}" for name in columns)
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text(
                    "UPDATE coding_submissions SET created_at = now() - make_interval(mins => :m)"
                    + (f", {sets}" if sets else "")
                    + " WHERE id = :i"
                ),
                {"m": minutes, "i": submission_id, **columns},
            )
            await session.commit()


async def _complete_conversation(factory, world, *, minutes_ago: int) -> None:
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text(
                    "UPDATE assessment_conversations SET status = 'completed', "
                    "completed_at = now() - make_interval(mins => :m) WHERE id = :c"
                ),
                {"m": minutes_ago, "c": world.conversation},
            )
            await session.commit()


async def _plan() -> sweeps.ReconcilePlan:
    async with worker_session() as session:
        return await sweeps.reconcile(session)


# ── Reconcile ───────────────────────────────────────────────────────────────


async def test_reconcile_redispatches_old_open_work_and_leaves_young_work_alone(factory, world) -> None:
    old, _, _ = await _submitted(factory, world, ordinal=0)
    young, _, _ = await _submitted(factory, world, ordinal=1)
    review_owed, _, _ = await _submitted(factory, world, ordinal=2)
    await _age(factory, old, minutes=10)
    await _age(factory, review_owed, minutes=30)
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text(
                    "UPDATE coding_submissions SET execution_status = 'complete', review_status = 'failed', "
                    "tests_total = 1, tests_passed = 1, executed_at = now(), "
                    "test_results_json = CAST(:r AS jsonb) WHERE id = :i"
                ),
                {"i": review_owed, "r": json.dumps([{"key": "h1", "outcome": "passed", "passed": True}])},
            )
            await session.commit()
    plan = await _plan()
    assert str(old) in plan.redispatch
    assert str(review_owed) in plan.redispatch
    assert str(young) not in plan.redispatch


async def test_a_row_past_the_alarm_threshold_is_stuck_and_still_redispatched(factory, world) -> None:
    submission_id, _, _ = await _submitted(factory, world)
    await _age(factory, submission_id, minutes=10, execution_attempts=sweeps.ATTEMPTS_BEFORE_ALARM)
    plan = await _plan()
    assert str(submission_id) in plan.stuck
    assert str(submission_id) in plan.redispatch


def test_the_alarm_threshold_is_the_deletion_requests_one() -> None:
    assert sweeps.ATTEMPTS_BEFORE_ALARM == deletion_requests.ATTEMPTS_BEFORE_ALARM


async def test_a_completed_conversation_goes_to_scoring_when_its_coding_work_is_done_or_expired(factory, world) -> None:
    submission_id, _, _ = await _submitted(factory, world)
    await _complete_conversation(factory, world, minutes_ago=30)
    # Open and young: scoring keeps waiting.
    assert str(world.link) not in (await _plan()).score_links
    # Open and past the wait window: scoring proceeds and reads "Not assessed".
    await _age(factory, submission_id, minutes=25 * 60)
    assert str(world.link) in (await _plan()).score_links


async def test_a_just_completed_conversation_is_left_to_the_normal_path(factory, world) -> None:
    submission_id, _, _ = await _submitted(factory, world)
    await _age(factory, submission_id, minutes=25 * 60)
    await _complete_conversation(factory, world, minutes_ago=1)
    assert str(world.link) not in (await _plan()).score_links


async def test_a_conversation_with_no_coding_is_not_this_sweeps_business(factory, world) -> None:
    await _complete_conversation(factory, world, minutes_ago=30)
    assert str(world.link) not in (await _plan()).score_links


# ── Probe ───────────────────────────────────────────────────────────────────


async def test_the_probe_says_disabled_when_execution_is_disabled(caplog) -> None:
    caplog.set_level(logging.INFO)
    assert await sweeps.probe() == {"status": "disabled"}
    assert any("code_execution.probe status=disabled" in r.getMessage() for r in caplog.records)


async def test_the_probe_passes_a_working_sandbox_and_logs_no_content(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    provider = FakeProvider()
    provider.script(sweeps.CANARY_PROGRAMS["python"], sweeps.CANARY_STDIN, ScriptedRun(stdout="5\n"))
    result = await sweeps.probe(provider)
    assert result["status"] == "ok"
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "code_execution.probe status=ok" in messages
    assert "input().split()" not in messages


@pytest.mark.parametrize(
    ("provider", "reason"),
    [
        (FakeProvider(unavailable="network"), "network"),
        (None, "wrong_result"),
    ],
)
async def test_the_probe_reports_a_failing_sandbox_for_the_alarm(caplog, provider, reason) -> None:
    caplog.set_level(logging.ERROR)
    if provider is None:
        provider = FakeProvider()
        provider.script(sweeps.CANARY_PROGRAMS["python"], sweeps.CANARY_STDIN, ScriptedRun(stdout="6\n"))
    result = await sweeps.probe(provider)
    assert (result["status"], result["reason"]) == ("failed", reason)
    assert any(
        r.levelno == logging.ERROR and "code_execution.probe status=failed" in r.getMessage()
        for r in caplog.records
    )


# ── Verify ──────────────────────────────────────────────────────────────────


def _contained_sandbox(**overrides: ScriptedRun) -> FakeProvider:
    provider = FakeProvider()
    for language, program in sweeps.CANARY_PROGRAMS.items():
        provider.script(program, sweeps.CANARY_STDIN, overrides.get(f"canary_{language}", ScriptedRun(stdout="5\n")))
    contained = {
        "infinite_loop": ScriptedRun(outcome=ExecutionOutcome.TIME_LIMIT),
        "memory_hog": ScriptedRun(outcome=ExecutionOutcome.MEMORY_LIMIT),
        "output_flood": ScriptedRun(outcome=ExecutionOutcome.OUTPUT_LIMIT),
        "process_bomb": ScriptedRun(outcome=ExecutionOutcome.RUNTIME_ERROR),
    }
    for name, _language, source, _ok in sweeps._HOSTILE:
        provider.script(source, "", overrides.get(name, contained[name]))
    provider.script(sweeps._NETWORK_PROGRAM, "", overrides.get("network_attempt", ScriptedRun(stdout="blocked\n")))
    return provider


class _WithSelfChecks(FakeProvider):
    async def self_checks(self):
        return {name: (True, "ok") for name in sweeps.ADAPTER_CHECK_NAMES}


async def test_a_contained_sandbox_with_its_adapter_checks_is_green() -> None:
    provider = _contained_sandbox()
    checked = _WithSelfChecks()
    checked._scripts = provider._scripts
    verdict = await sweeps.verify_sandbox(checked)
    assert verdict.green, verdict.as_dict()
    names = {check.name for check in verdict.checks}
    assert {"canary_python", "canary_java", "canary_cpp", "canary_javascript", "infinite_loop",
            "memory_hog", "output_flood", "process_bomb", "network_attempt",
            "health_after_hostile_runs", *sweeps.ADAPTER_CHECK_NAMES} <= names


async def test_a_skipped_adapter_check_is_not_a_passed_one() -> None:
    verdict = await sweeps.verify_sandbox(_contained_sandbox())
    assert verdict.status == "red"
    skipped = {c.name for c in verdict.checks if c.status == sweeps.CHECK_NOT_PERFORMED}
    assert skipped == set(sweeps.ADAPTER_CHECK_NAMES)


@pytest.mark.parametrize(
    ("override", "failing"),
    [
        ({"network_attempt": ScriptedRun(stdout="open\n")}, "network_attempt"),
        ({"infinite_loop": ScriptedRun(outcome=ExecutionOutcome.OK)}, "infinite_loop"),
        ({"canary_java": ScriptedRun(outcome=ExecutionOutcome.COMPILE_ERROR)}, "canary_java"),
    ],
)
async def test_an_escaping_or_broken_sandbox_is_red(override, failing) -> None:
    provider = _contained_sandbox(**override)
    checked = _WithSelfChecks()
    checked._scripts = provider._scripts
    verdict = await sweeps.verify_sandbox(checked)
    assert verdict.status == "red"
    assert {c.name for c in verdict.checks if c.status == sweeps.CHECK_FAILED} == {failing}


async def test_verification_with_execution_disabled_says_so() -> None:
    assert (await sweeps.verify_sandbox()).status == "disabled"


# ── The tasks and the schedule ──────────────────────────────────────────────


def test_the_coding_tasks_are_registered_where_their_cost_says() -> None:
    for name in (
        "pickready.execute_coding_submission",
        "pickready.reconcile_coding_submissions",
        "pickready.probe_code_execution",
        "pickready.verify_code_execution_sandbox",
    ):
        assert registry.resolve(name).route is Route.LAMBDA
    spec = registry.resolve("pickready.execute_coding_submission")
    assert spec.max_attempts == 3
    from app.services.code_execution import ExecutionUnavailable

    assert spec.retry_on == (ExecutionUnavailable,)


def test_the_sweep_and_the_probe_are_scheduled_and_the_verification_is_not() -> None:
    by_task = {entry.task: entry for entry in schedule.SCHEDULE}
    assert by_task["pickready.reconcile_coding_submissions"].interval_minutes == 15
    assert by_task["pickready.probe_code_execution"].interval_minutes == 5
    assert "pickready.verify_code_execution_sandbox" not in by_task
    assert "pickready.execute_coding_submission" not in by_task


def test_a_submission_defect_is_a_permanent_task_failure() -> None:
    # Execution is disabled in the test settings, so the submission cannot run
    # anywhere: that is a defect to report, not a failure to retry.
    with pytest.raises(PermanentTaskFailure):
        coding_tasks.execute_coding_submission(str(uuid.uuid4()))


def test_the_probe_and_verification_tasks_report_disabled() -> None:
    assert coding_tasks.probe_code_execution() == {"status": "disabled"}
    assert coding_tasks.verify_code_execution_sandbox()["status"] == "disabled"
