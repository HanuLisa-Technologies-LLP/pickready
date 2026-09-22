"""The execution engine: one scenario, from prepared environment to emitted report.

THE LIFECYCLE THIS IMPLEMENTS
-------------------------------
HARNESS.md section 31 in order, and the order is the contract:

  1. prepare the environment      `_prepare`
  2. establish the initial state  `world.build`
  3. run the workload             `workload.run_step`
  4. inject faults                `faults.apply`, wrapping step 3
  5. exercise the REAL system     the workload is HTTP over the real app
  6. capture telemetry and the trajectory   `ScenarioContext`
  7. observe side effects         `dispatch.recorded()`
  8. validate the final state     `StateReader`, a SECOND connection
  9. evaluate against ground truth  `assertions`
 10. compute metrics              `evaluators`
 11. store artifacts              `ArtifactStore`
 12. emit the report              the CLI, from what this appended

WHY THIS IS THREE EVENT LOOPS AND NOT ONE
-------------------------------------------
`TestClient` runs the application on a portal-managed loop of its own and
blocks the calling thread while it does. Driving it from inside an
`asyncio.run` would deadlock. So the world is built on one loop, the workload
runs synchronously against the client's, and the judging runs on a third. Every
engine here is `NullPool` for exactly that reason: an asyncpg connection
belongs to the loop that opened it, and a pooled one handed across these
boundaries fails the next caller for something the previous one did.

That split is also what makes the second-connection rule cheap to keep: by the
time the state reader opens, the client is closed and its loop is gone, so
there is no request session left in the process to accidentally read from.

WHY A FAILURE HERE IS RECORDED RATHER THAN RAISED
---------------------------------------------------
`execute` appends checks and outcomes to the run and returns. It raises only
when the harness itself cannot proceed, and even then the manifest written at
`store.begin` is already on disk. A scenario that blew up is evidence; an
exception that escaped to the CLI and printed a traceback over the top of
nineteen other scenarios' results is not.

Provenance: docs/spec/HARNESS.md sections 1, 3, 4, 5 and 7.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
from typing import Any, Sequence

import asyncpg
from sqlalchemy.exc import DBAPIError

from app.workers import dispatch

from harness import assertions
from harness.artifacts import ArtifactStore
from harness.context import ScenarioContext, StateReader, open_state_reader
from harness.probes import ProbeError
from harness.evaluators import EvaluationInput, evaluate
from harness.run import UNAVAILABLE, Check, EvaluationOutcome, RunRecord
from harness.scenario import Scenario
from harness.workload import Application, WorkloadError, run_step
from harness.world import SchemaMissing, World, WorldError, build, session_factory
from harness.world import teardown as teardown_world

__all__ = ["execute"]

#: The only isolated dispatch backend (HARNESS.md section 1). `record` accepts
#: the dispatch, runs nothing and remembers it, and `dispatch.backend()` already
#: refuses it outright in production, so a harness run can never be one that
#: quietly did the work.
REQUIRED_DISPATCH_BACKEND = "record"

#: What "the stack is not there" looks like, in the three shapes it arrives in.
#:
#: `DBAPIError` is SQLAlchemy's wrapper, which is what a failure on an
#: ESTABLISHED connection raises. It is not enough on its own: a connect-time
#: refusal never reaches the wrapper, so asyncpg's own `PostgresError` (a
#: missing database, a rejected password) and `OSError` (nothing listening on
#: the port) both propagate raw. Measured while verifying this harness: with
#: only the wrapper caught, `DATABASE_URL` pointing at a database that does not
#: exist printed an `InvalidCatalogNameError` traceback and exited 1, which in
#: CI is indistinguishable from a product regression.
_UNREACHABLE_DATABASE = (DBAPIError, asyncpg.PostgresError, OSError)


def execute(*, run: RunRecord, scenario: Scenario, store: ArtifactStore) -> None:
    """Run one scenario and record what happened on `run`."""
    problem = _prepare(run)
    if problem is not None:
        _unavailable(run, scenario, problem)
        return

    sessions = session_factory()
    try:
        world = asyncio.run(build(scenario.given.world, scenario.given.overrides, sessions=sessions))
    except SchemaMissing as exc:
        # Not a failure. An un-migrated database has told us nothing about the
        # product, and a red scenario in front of somebody whose fix is
        # `alembic upgrade head` is a red scenario nobody learns from.
        _unavailable(run, scenario, f"the world could not be built: {exc}")
        return
    except WorldError as exc:
        _unavailable(run, scenario, f"the world builder refused: {exc}")
        return
    except _UNREACHABLE_DATABASE as exc:
        # THE STACK IS NOT THERE, AND THAT IS NOT A PRODUCT FAILURE.
        #
        # `preflight` already turns a missing RELATION into `unavailable`, and
        # it cannot reach a missing DATABASE or an unreachable server, because
        # the failure happens on the connection it needs in order to ask.
        # Measured while verifying this harness: pointing `DATABASE_URL` at a
        # database that does not exist produced an `InvalidCatalogNameError`
        # traceback and exit 1, which in CI is indistinguishable from a
        # product regression and sends somebody reading a diff.
        #
        # Caught for the BUILD ONLY. A database error raised later comes out of
        # a real route under a real request and is the finding.
        _unavailable(
            run,
            scenario,
            "the world could not be built because the database could not be "
            f"reached or read: {_describe(exc)}. Nothing "
            "about the product was measured. Check DATABASE_URL and that the "
            "test stack is up and migrated.",
        )
        return

    ctx = ScenarioContext(world=world, seed=run.seed)
    reader: StateReader | None = None
    try:
        applied = _drive(ctx, scenario, world)
        run.config["faults_applied"] = list(applied)
        reader = open_state_reader()
        try:
            checks = asyncio.run(_judge(ctx, scenario, reader, world))
        except (assertions.AssertionError_, ProbeError) as exc:
            # TWO CAUSES WEAR ONE EXCEPTION, AND THEY ARE OPPOSITE FINDINGS.
            #
            # A malformed assertion is the AUTHOR's problem and is fixed by
            # editing a file, which is the distinction `ScenarioError` already
            # draws. Reporting it as a failing check would send somebody
            # looking for a product defect behind a missing bracket, so it is
            # `unavailable`.
            #
            # But the SAME exception is raised when an assertion names a step
            # that made no request, and the commonest way for that to happen is
            # that the route RAISED: `TestClient` re-raises a server exception,
            # so no observation is recorded and the probe finds nothing to read.
            # Reporting that as `unavailable` says "we could not measure" about
            # a run in which the product threw an unhandled exception out of a
            # real route, which is the most actionable failure a harness can
            # find. Measured while verifying this harness: the 2026-09-20
            # soft-delete defect, reintroduced deliberately, produced
            # `UniqueViolationError` from the bulk route and the run reported
            # `unavailable` with the constraint name visible in the trajectory
            # and nowhere in the verdict.
            #
            # `ctx.aborted` is what separates them, and it is set only by the
            # workload driver when a step could not complete.
            if ctx.aborted is None:
                _unavailable(run, scenario, f"{scenario.source_path}: {exc}")
                return
            run.checks.append(
                Check(
                    kind="output",
                    expression="the workload ran to completion",
                    expected="every declared step completed",
                    actual=ctx.aborted,
                    passed=False,
                    note=(
                        "the remaining ground-truth assertions were not "
                        f"evaluated because the workload stopped: {exc}"
                    ),
                )
            )
            run.evaluations.extend(
                asyncio.run(
                    _measure(ctx, scenario, tuple(run.checks), reader, applied)
                )
            )
            run.finish()
            return
        run.checks.extend(checks)
        run.evaluations.extend(
            asyncio.run(_measure(ctx, scenario, tuple(checks), reader, applied))
        )
    finally:
        store.write_json(run.run_id, "trajectory.json", ctx.as_dict())
        store.write_json(
            run.run_id,
            "side_effects.json",
            {
                "dispatched": [
                    {"task": item.name, "args": list(item.args)}
                    for item in dispatch.recorded()
                ],
                "world": ctx.as_dict()["world_ids"],
            },
        )
        if reader is not None:
            asyncio.run(reader.close())
        asyncio.run(teardown_world(world, sessions=sessions))
    run.finish()


# ── 1. Prepare the environment ───────────────────────────────────────────────


def _prepare(run: RunRecord) -> str | None:
    """Assert the isolation guarantees, then clear the process-global state.

    The two things cleared are process-global and time-based, which is the
    combination that produces an order-dependent failure: a scenario that
    provoked a credential failure would otherwise leave the router's breaker
    open for every later scenario in the same process, and a scenario asserting
    "exactly one email was dispatched" would pass or fail depending on what ran
    before it. `tests/conftest.py` clears both per test for the same reason.
    """
    from app.services import llm_router

    try:
        backend = dispatch.backend()
    except dispatch.DispatchError as exc:
        return f"the dispatch backend is unusable: {exc}"
    if backend != REQUIRED_DISPATCH_BACKEND:
        return (
            f"the dispatch backend is {backend!r}, not "
            f"{REQUIRED_DISPATCH_BACKEND!r}. A harness run must accept work "
            "and not do it, or a scenario would fire real background work at "
            "whatever infrastructure this process can reach."
        )
    run.config["dispatch_backend_verified"] = backend
    dispatch.clear_recorded()
    llm_router.clear_provider_breaker()
    return None


def _describe(exc: Exception) -> str:
    """Name the underlying driver error rather than SQLAlchemy's wrapper.

    `DBAPIError.orig` carries the asyncpg exception that actually happened;
    without it every connection problem reads as the same generic wrapper and
    a reader cannot tell a missing database from a rejected password.
    """
    original = getattr(exc, "orig", None) or exc
    return f"{type(original).__name__}: {original}"


def _unavailable(run: RunRecord, scenario: Scenario, reason: str) -> None:
    """Record, per evaluator the scenario asked for, that nothing was measured.

    One outcome per named evaluator rather than a single blanket line, so the
    report lists them by name. A blanket line would read as a scenario that
    declared no evaluators, which is a different defect entirely, and it is the
    shape `cli._unwired_run` already chose for the same reason.
    """
    for name in scenario.expect.evaluators or ("harness_engine",):
        run.evaluations.append(
            EvaluationOutcome(evaluator=name, outcome=UNAVAILABLE, reason=reason)
        )
    run.finish()


# ── 3, 4, 5, 6, 7. The workload, wrapped in its faults ───────────────────────


def _load_faults() -> Any:
    """Import the fault layer, or return None because it is not there yet.

    `find_spec` rather than catching `ImportError`, for the reason `cli.py`
    already gives: a fault layer that exists and fails to import is a real
    defect, and `except ImportError` would report it as "not wired yet" and
    hide it behind a message inviting the reader to do nothing.
    """
    if importlib.util.find_spec("harness.faults") is None:
        return None
    return importlib.import_module("harness.faults")


def _drive(ctx: ScenarioContext, scenario: Scenario, world: World) -> tuple[str, ...]:
    """Run the workload under the scenario's faults, then probe for recovery.

    The recovery probe is made OUTSIDE the fault block and INSIDE the same
    client, deliberately. Outside, because a probe taken while the fault is
    still applied measures the fault; inside the same client, because a fresh
    process would recover trivially and prove nothing about whether the one
    that had been failing latched a breaker, cached a failure or left a
    connection in a bad state.
    """
    faults = _load_faults()
    if scenario.faults and faults is None:
        ctx.fault_layer_absent = "harness/faults.py does not exist"
        ctx.notes.append(
            "this scenario asked for "
            + ", ".join(fault.name for fault in scenario.faults)
            + " and the fault layer is absent, so the workload ran unfaulted "
            "and nothing about degradation or recovery was measured"
        )

    with Application(world) as client:
        applied = _run_workload(ctx, scenario, client, faults)
        probe = client.request(ctx, "recovery_probe", "GET", "/health")
        ctx.facts["recovery_probe"] = {
            "status": probe.status,
            "elapsed_ms": round(probe.elapsed_ms, 3),
        }
    return applied


def _run_workload(
    ctx: ScenarioContext,
    scenario: Scenario,
    client: Application,
    faults: Any,
) -> tuple[str, ...]:
    if not scenario.faults or faults is None:
        _steps(ctx, scenario.workload, client)
        return ()
    with faults.apply(scenario.faults) as applied:
        names = tuple(spec.name for spec in applied)
        ctx.facts["faults_applied"] = list(names)
        _steps(ctx, scenario.workload, client)
    # Asserted after the block, so a fault that failed to unwind is attributed
    # to the scenario that applied it rather than to the next one to run.
    faults.assert_clean()
    return names


def _steps(ctx: ScenarioContext, steps: Sequence[str], client: Application) -> None:
    """Run each step in order, stopping at the first one that cannot continue.

    STOPPING, not skipping. A workload is a sequence in which each step assumes
    the one before it landed, so continuing past a failure would produce
    assertions about a state nobody established, and the report would name the
    wrong step. `ctx.aborted` carries which one, so the evaluators can say the
    later assertions never ran rather than that they failed.
    """
    for step in steps:
        try:
            run_step(step, client, ctx)
        except WorkloadError as exc:
            ctx.aborted = f"step {step!r} could not run: {exc}"
            return
        except Exception as exc:  # noqa: BLE001 - re-raised as recorded evidence
            # An unexpected exception out of a real route IS the finding, so it
            # is recorded with its type and message and the run continues to
            # its judging phase rather than losing every artifact to a
            # traceback. The run still fails: `functional_correctness` reads
            # `aborted`.
            ctx.aborted = f"step {step!r} raised {type(exc).__name__}: {exc}"
            return


# ── 8, 9. Validate the final state, from a second connection ─────────────────


async def _judge(
    ctx: ScenarioContext, scenario: Scenario, reader: StateReader, world: World
) -> list[Check]:
    """Every ground-truth assertion the scenario declared, in section 3's order."""
    checks: list[Check] = []
    checks.extend(await assertions.evaluate_state(scenario.expect.state, reader, world))
    checks.extend(assertions.evaluate_dispatched(scenario.expect.dispatched))
    checks.extend(assertions.evaluate_output(scenario.expect.output, ctx))
    checks.extend(assertions.evaluate_trajectory(scenario.expect.trajectory, ctx))
    checks.extend(assertions.evaluate_prohibited(scenario.expect.prohibited, ctx))
    return checks


# ── 10. Compute metrics ──────────────────────────────────────────────────────


async def _measure(
    ctx: ScenarioContext,
    scenario: Scenario,
    checks: tuple[Check, ...],
    reader: StateReader,
    applied: tuple[str, ...],
) -> list[EvaluationOutcome]:
    data = EvaluationInput(
        scenario=scenario,
        ctx=ctx,
        checks=checks,
        reader=reader,
        faults_applied=applied,
    )
    return [await evaluate(name, data) for name in scenario.expect.evaluators]
