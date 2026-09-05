"""`RPN_PIPELINE_HALT`: the one permitted switch in Part A, and it fails closed.

spec-doc6 D1 allows exactly one runtime switch in the activated pipeline and
states what it must do:

    "an operational kill switch that FAILS CLOSED: `RPN_PIPELINE_HALT=<stage>`
     causes the stage to raise a loud, audited `PipelineHalted` error and refuse
     to proceed. It must never fall back to old logic, degraded logic, or a
     stub. A halted stage blocks progression; it does not silently produce a
     worse answer. Exactly one implementation of this, in one module, tested."

WHY IT IS NOT A FEATURE FLAG, AND THE DIFFERENCE IS THE WHOLE POINT
--------------------------------------------------------------------
A feature flag selects between two implementations, which is the thing D1
forbids: two implementations means two code paths, one of which is never
exercised until the day it is switched on, at which point nobody has run it for
months. This switch selects between ONE implementation and NOTHING. There is no
second branch to drift, because the halted branch produces no answer at all.

The operational need it serves is real and narrow: a defect is found in a live
stage, and the choice is between shipping a revert and stopping that stage until
one lands. Stopping is the safer half of that choice only if stopping actually
stops. A switch that degraded to "the old scorer" or "a template report" would
turn a known-bad answer into an unknown-bad one, and nobody downstream could
tell which they were reading.

WHY THE AUDIT ROW GOES IN ITS OWN SESSION
------------------------------------------
The caller's transaction is about to be rolled back -- an HTTP handler answers
5xx, a background task raises and retries -- so a row written into it disappears
along with the refusal it was recording. The record of a halt is the only thing
that distinguishes "the platform refused, deliberately" from "the platform
broke", and it is worth its own connection.

A failure to WRITE that row is logged at ERROR and never swallowed into
silence, but it does not stop the halt: the refusal is the safety property, the
row is the record of it, and a halt that could be defeated by an unreachable
database would be a kill switch with a way round it.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterable

logger = logging.getLogger(__name__)

__all__ = [
    "ENV_VAR",
    "HALT_ALL",
    "STAGES",
    "STAGE_BODHA_SWOT",
    "STAGE_SUTRA_MATRIX",
    "STAGE_SCORECARD_FREEZE",
    "STAGE_JOB_PUBLICATION",
    "STAGE_YUKTI_PRESCREEN",
    "STAGE_MITI_EVALUATION",
    "STAGE_SIDDHI_REPORT",
    "PipelineHalted",
    "UnknownHaltStage",
    "halted_stages",
    "is_halted",
    "check",
    "enforce",
]

#: The environment variable, spelled exactly as spec-doc6 D1 writes it.
ENV_VAR = "RPN_PIPELINE_HALT"

#: The wildcard. Stops every stage, for the case where the fault is not yet
#: localised and the honest thing is to stop the whole pipeline.
HALT_ALL = "all"

# ── The stages ───────────────────────────────────────────────────────────────
#
# NAMED CONSTANTS RATHER THAN FREE STRINGS, and the reason is operational. This
# switch is typed into a deployment console by somebody under time pressure,
# and `RPN_PIPELINE_HALT=sutra` misspelled as `RPN_PIPELINE_HALT=sutraa` would
# silently halt nothing while reading, in the console, as though it had. An
# unrecognised stage name is therefore an error at configuration read time, not
# a no-op.

STAGE_BODHA_SWOT = "bodha_swot"
STAGE_SUTRA_MATRIX = "sutra_matrix"
STAGE_SCORECARD_FREEZE = "scorecard_freeze"
STAGE_JOB_PUBLICATION = "job_publication"
STAGE_YUKTI_PRESCREEN = "yukti_prescreen"
STAGE_MITI_EVALUATION = "miti_evaluation"
STAGE_SIDDHI_REPORT = "siddhi_report"

#: Every haltable stage of Part A, in pipeline order. The scoring and report
#: stages are declared here even though this phase wires only the first four,
#: because D1 asks for ONE implementation of the switch and a second module
#: adding its own stage names later is how one implementation becomes two.
STAGES: tuple[str, ...] = (
    STAGE_BODHA_SWOT,
    STAGE_SUTRA_MATRIX,
    STAGE_SCORECARD_FREEZE,
    STAGE_JOB_PUBLICATION,
    STAGE_YUKTI_PRESCREEN,
    STAGE_MITI_EVALUATION,
    STAGE_SIDDHI_REPORT,
)

#: What an operator is told when a request hits a halted stage. It names the
#: stage and says the refusal was deliberate, because the alternative -- a bare
#: 503 -- is indistinguishable from an outage to the person reading it.
_MESSAGE = (
    "{stage} is halted by operator configuration ({var}={value}). Nothing was "
    "generated, scored or written. This is a deliberate refusal, not a failure: "
    "clear {var} to resume."
)


class PipelineHalted(RuntimeError):
    """A stage refused to run because an operator halted it.

    Carries the stage so a handler can name it without re-reading the
    environment, and so an audit row records which stage was stopped rather
    than only that something was.
    """

    def __init__(self, stage: str, configured: str) -> None:
        super().__init__(_MESSAGE.format(stage=stage, var=ENV_VAR, value=configured))
        self.stage = stage
        self.configured = configured


class UnknownHaltStage(ValueError):
    """`RPN_PIPELINE_HALT` names something that is not a stage.

    Raised rather than ignored. A kill switch whose typo reads as "off" is a
    kill switch that will one day be believed to be on.
    """


def _configured() -> str:
    return (os.environ.get(ENV_VAR) or "").strip()


def halted_stages() -> frozenset[str]:
    """The stages the environment currently halts.

    Read on every call rather than cached at import. A halt is applied by an
    operator to a running deployment, and a value captured at import would take
    effect only on the next restart -- which is exactly the delay the switch
    exists to avoid.
    """
    raw = _configured()
    if not raw:
        return frozenset()
    names = [part.strip().lower() for part in raw.split(",") if part.strip()]
    if HALT_ALL in names:
        return frozenset(STAGES)
    unknown = sorted(name for name in names if name not in STAGES)
    if unknown:
        raise UnknownHaltStage(
            f"{ENV_VAR}={raw!r} names {unknown}, which are not pipeline stages. "
            f"Valid stages are {list(STAGES)}, or {HALT_ALL!r} for every stage. "
            f"Nothing has been halted: fix the value rather than assuming it "
            f"took effect."
        )
    return frozenset(names)


def is_halted(stage: str) -> bool:
    """Whether this stage is currently halted. Never raises for a valid stage."""
    _require_known(stage)
    return stage in halted_stages()


def _require_known(stage: str) -> None:
    if stage not in STAGES:
        raise UnknownHaltStage(
            f"{stage!r} is not a declared pipeline stage. Add it to "
            f"`pipeline_halt.STAGES` rather than passing a free string, or a "
            f"halt aimed at it will silently do nothing."
        )


def check(stage: str) -> None:
    """Raise `PipelineHalted` if this stage is halted. Otherwise return.

    Pure: no session, no I/O beyond reading the environment. Call it as the
    FIRST statement of the stage, before anything is read or written, so a halt
    cannot leave a half-built artifact behind.
    """
    _require_known(stage)
    if stage in halted_stages():
        raise PipelineHalted(stage, _configured())


async def enforce(
    stage: str,
    *,
    tenant_id: Any = None,
    actor_user_id: Any = None,
    job_id: Any = None,
    correlation_id: str | None = None,
    agent: str | None = None,
) -> None:
    """`check`, plus the audit row and the log line D1 calls for.

    The audit row is written in ITS OWN session and committed there, because the
    caller's transaction is about to be rolled back by the refusal this function
    is raising. See the module docstring.
    """
    try:
        check(stage)
        return
    except PipelineHalted as halt:
        logger.error(
            "pipeline_halt.refused stage=%s configured=%s tenant_id=%s job_id=%s "
            "correlation_id=%s agent=%s",
            halt.stage,
            halt.configured,
            tenant_id,
            job_id,
            correlation_id,
            agent,
        )
        await _record(
            halt,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            job_id=job_id,
            correlation_id=correlation_id,
            agent=agent,
        )
        raise


async def _record(
    halt: PipelineHalted,
    *,
    tenant_id: Any,
    actor_user_id: Any,
    job_id: Any,
    correlation_id: str | None,
    agent: str | None,
) -> None:
    """One committed audit row, in a session of its own."""
    try:
        from app.core.db import get_session_factory, superadmin_scope  # noqa: PLC0415
        from app.services.audit import audit  # noqa: PLC0415

        async with get_session_factory()() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    row = await audit(
                        session,
                        tenant_id=tenant_id,
                        actor_user_id=actor_user_id,
                        action="pipeline_halted",
                        target_type="job" if job_id else "pipeline",
                        target_id=job_id,
                        metadata={
                            "stage": halt.stage,
                            "configured": halt.configured,
                            "env_var": ENV_VAR,
                        },
                    )
                    if job_id is not None:
                        row.job_id = job_id
                    row.correlation_id = correlation_id
                    row.agent_name = agent
    except Exception:  # noqa: BLE001 - reported, never swallowed into silence
        # The refusal has already happened and is already logged; this is the
        # RECORD of it failing, which an operator needs to know about and which
        # must not be allowed to defeat the halt itself.
        logger.exception(
            "pipeline_halt.audit_write_failed stage=%s job_id=%s. The stage was "
            "still refused.",
            halt.stage,
            job_id,
        )


def http_detail(halt: PipelineHalted) -> str:
    """The message a client is shown. Names the stage and the way out."""
    return str(halt)


def as_dict(halt: PipelineHalted) -> dict[str, Any]:
    return {"stage": halt.stage, "configured": halt.configured, "env_var": ENV_VAR}


def declared_stages() -> Iterable[str]:
    return STAGES
