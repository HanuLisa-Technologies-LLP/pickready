"""Event ingestion: the browser reports, the server decides (sections 4 and 9).

THREE THINGS THE BROWSER IS NOT TRUSTED WITH
--------------------------------------------
1. THE PATH. The browser sends an identifier; `catalog.spec_for` says which
   consequence path it takes, and `classify` below moves an event DOWN a path
   when its own duration does not clear the rule the identifier claims. A
   focus loss under the ignore window is logged, not warned. A camera failure
   that recovered inside the recovery window is an interruption, not a
   termination. A browser cannot escalate by naming a graver identifier.
2. THE COUNT. The warning number comes from `state.increment_warning`, an
   atomic Redis INCR, and is mirrored onto the row afterwards. The browser
   never sends a count and would not be believed if it did.
3. THE VERDICT. Whether the third warning ends the assessment is the
   recruiter's job-level setting, read from `jobs.proctoring_warning_policy`
   at the moment it matters and never cached in the browser.

ONE WARNING PER BATCH, AND WHY
------------------------------
A batch is a few seconds of one browser's life. A phone put down on the desk
produces one detection every few frames until it is moved, and a candidate
who has just been told to move it needs the chance to do so before the next
warning lands. The first warning-worthy event in a batch takes the warning;
the rest are recorded with `warning_issued = false` so the report can still
say how many times the thing happened.

WHAT A TERMINATION DOES, IN ORDER
---------------------------------
Records the event, closes the proctoring session with its outcome and reason,
marks the conversation terminated so `gate.require_active` refuses the next
turn, enqueues `pickready.run_functional_assessment` so the PRISM Report is
written from the answers saved so far, and returns a plain-language message
for the candidate's screen. It never deletes an answer.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentConversation
from app.models.proctoring import (
    ENDED_OUTCOMES,
    OUTCOME_TECHNICAL_FAILURE,
    OUTCOME_TERMINATED_INTEGRITY,
    OUTCOME_TERMINATED_WARNINGS,
    POLICY_TERMINATE,
    QUALITY_DEGRADED,
    QUALITY_GOOD,
    QUALITY_POOR,
    ProctoringEvent,
    ProctoringSession,
)
from app.schemas.proctoring import (
    EventBatchIn,
    EventIn,
    HeartbeatOut,
    IngestOut,
    TerminationOut,
    WarningOut,
)
from app.services.proctoring import catalog, identity, phrasing, state
from app.services.proctoring.config import ProctoringConfig, get_config

logger = logging.getLogger(__name__)

__all__ = [
    "CONVERSATION_TERMINATED",
    "NOTE_KEY",
    "NOTE_WITHIN_COOLDOWN",
    "NOTE_ALREADY_REPORTED",
    "NOTE_UNDER_IGNORE_WINDOW",
    "NOTE_DOWNGRADED_FROM",
    "NOTE_WITHIN_DISTANCE",
    "NOTE_NO_WARNING_LEFT",
    "NOTE_BATCH_ALREADY_WARNED",
    "RateLimited",
    "SessionEnded",
    "Classified",
    "WarningDecision",
    "classify",
    "decide_warning",
    "outcome_for_termination",
    "session_quality_for",
    "enqueue_assessment",
    "ingest",
    "apply_server_event",
    "heartbeat",
    "terminate",
    "end_session",
    "termination_out",
]

#: `assessment_conversations.status` once proctoring has ended it. The
#: conversation is neither active (the gate refuses it) nor completed
#: (`completed_at` stays NULL, so the credit reconciler settles it as
#: incomplete, which is what it is).
CONVERSATION_TERMINATED = "terminated"

#: `metadata_json` key under which the server records WHY an event took a
#: different path from the one its identifier names. Internal; the report
#: reads it to decide what counts as an occurrence.
NOTE_KEY = "server_note"
NOTE_WITHIN_COOLDOWN = "within_cooldown"
NOTE_ALREADY_REPORTED = "already_reported_this_session"
NOTE_UNDER_IGNORE_WINDOW = "under_ignore_window"
NOTE_DOWNGRADED_FROM = "downgraded_from"
NOTE_WITHIN_DISTANCE = "within_distance_threshold"
NOTE_NO_WARNING_LEFT = "no_warning_left"
NOTE_BATCH_ALREADY_WARNED = "batch_already_warned"

#: Consecutive-evidence counter names in `state`.
_IDENTITY_RUN = "identity_mismatch"

#: Terminations that are a technical failure rather than candidate behaviour
#: (section 7.3: "A candidate whose laptop camera died must never be presented
#: as suspicious"). `CAMERA_STREAM_FAILED` always is. `INTEGRITY_CHECK_FAILED`
#: is ambiguous (a dead tab or a candidate poking at the page look the same
#: from the server), so it is treated as technical ONLY when the session had
#: issued no warning at all: nothing else in the session suggested the
#: candidate was doing anything, and the honest reading of an unexplained
#: failure on a clean session is that something broke.
_ALWAYS_TECHNICAL = frozenset({"CAMERA_STREAM_FAILED"})
_TECHNICAL_WHEN_CLEAN = frozenset({"INTEGRITY_CHECK_FAILED"})

_MS_PER_SECOND = 1000


class RateLimited(RuntimeError):
    """The browser exceeded `catalog.CLIENT_RATE_LIMIT_PER_MINUTE`."""


class SessionEnded(RuntimeError):
    """An event arrived for a session that is over."""


@dataclass(frozen=True)
class Classified:
    """One event after the server has decided what it is."""

    event_type: str
    path: str
    note: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WarningDecision:
    number: int
    final: bool
    terminate: bool


def _ms(seconds: float) -> int:
    return int(seconds * _MS_PER_SECOND)


def _below(duration_ms: int | None, seconds: float) -> bool:
    """True when a MEASURED duration is under the rule. An unmeasured duration
    (None) is trusted as sent: the browser is the only party that timed it."""
    return duration_ms is not None and duration_ms < _ms(seconds)


def classify(event: EventIn, config: ProctoringConfig) -> Classified:
    """The server's reading of a client event (sections 4.1, 4.2, 9).

    Every downgrade here is a duration that did not clear the rule the
    identifier claims. Nothing is ever upgraded: a client cannot earn a
    graver path by sending a longer number, only lose one by sending a shorter.
    """
    kind = event.event_type
    duration = event.duration_ms
    if kind == "WINDOW_FOCUS_LOST" and _below(duration, config.focus_loss_ignore_under_seconds):
        return Classified(kind, catalog.PATH_C, {NOTE_KEY: NOTE_UNDER_IGNORE_WINDOW})
    if kind == "INTEGRITY_CHECK_FAILED" and _below(duration, config.integrity_failure_termination_seconds):
        return Classified("INTEGRITY_CHECK_WARNING", catalog.PATH_C, {NOTE_DOWNGRADED_FROM: kind})
    if kind == "CAMERA_STREAM_FAILED" and _below(duration, config.camera_recovery_seconds):
        return Classified("CAMERA_STREAM_INTERRUPTED", catalog.PATH_C, {NOTE_DOWNGRADED_FROM: kind})
    if kind == "CAMERA_OBSTRUCTED" and _below(duration, config.obstruction_seconds):
        return Classified("FACE_ABSENT_BRIEF", catalog.PATH_C, {NOTE_DOWNGRADED_FROM: kind})
    if kind == "FACE_ABSENT_EXTENDED" and _below(duration, config.face_absent_extended_seconds):
        if _below(duration, config.face_absent_moderate_seconds):
            return Classified("FACE_ABSENT_BRIEF", catalog.PATH_C, {NOTE_DOWNGRADED_FROM: kind})
        return Classified("FACE_ABSENT_MODERATE", catalog.PATH_B, {NOTE_DOWNGRADED_FROM: kind})
    if kind == "FACE_ABSENT_MODERATE" and _below(duration, config.face_absent_moderate_seconds):
        return Classified("FACE_ABSENT_BRIEF", catalog.PATH_C, {NOTE_DOWNGRADED_FROM: kind})
    return Classified(kind, catalog.spec_for(kind).path)


def decide_warning(number: int, max_warnings: int, policy: str) -> WarningDecision:
    """What the `number`th warning means under the job's policy (section 4.0).

    Below the limit: a warning. At the limit: the recruiter's setting decides,
    and the default setting is to continue, because the product never
    terminates without an explicit choice.
    """
    final = number >= max_warnings
    return WarningDecision(
        number=number,
        final=final,
        terminate=final and policy == POLICY_TERMINATE,
    )


def outcome_for_termination(reason_code: str, warnings_used: int) -> str:
    """Which ended outcome a Path A reason produces. See `_ALWAYS_TECHNICAL`."""
    if reason_code in _ALWAYS_TECHNICAL:
        return OUTCOME_TECHNICAL_FAILURE
    if reason_code in _TECHNICAL_WHEN_CLEAN and warnings_used <= 0:
        return OUTCOME_TECHNICAL_FAILURE
    return OUTCOME_TERMINATED_INTEGRITY


def session_quality_for(measured_fps: float | None, config: ProctoringConfig) -> str:
    """Section 3.6: a slow device is recorded, never refused."""
    if measured_fps is None:
        return QUALITY_GOOD
    if measured_fps < config.sampling_fps_degraded:
        return QUALITY_POOR
    if measured_fps < config.sampling_fps_normal:
        return QUALITY_DEGRADED
    return QUALITY_GOOD


def enqueue_assessment(link_id: str) -> None:
    """Score what was answered and write the PRISM Report. The proctoring
    report is generated by that task afterwards, so the two never race."""
    from app.workers.dispatch import dispatch

    dispatch("pickready.run_functional_assessment", args=[link_id])


def termination_out(ps: ProctoringSession) -> TerminationOut:
    reason = ps.termination_reason or ""
    return TerminationOut(
        reason_code=reason,
        message=phrasing.termination_message(reason, outcome=ps.outcome),
    )


# ── The per-batch context ────────────────────────────────────────────────────


@dataclass
class _Run:
    session: AsyncSession
    ps: ProctoringSession
    policy: str
    config: ProctoringConfig
    now: datetime
    enqueue: Callable[[str], None]
    warnings_used: int
    warned_this_batch: bool = False
    accepted: int = 0
    warning: WarningOut | None = None
    termination: TerminationOut | None = None

    @property
    def ended(self) -> bool:
        return self.termination is not None


def _store(
    run: _Run,
    *,
    event_type: str,
    path: str,
    occurred_at: datetime,
    duration_ms: int | None,
    confidence: float | None,
    question_id: uuid.UUID | None,
    metadata: dict[str, Any],
    warning_issued: bool = False,
    warning_number: int | None = None,
) -> ProctoringEvent:
    row = ProctoringEvent(
        tenant_id=run.ps.tenant_id,
        proctoring_session_id=run.ps.id,
        event_type=event_type,
        occurred_at=occurred_at,
        duration_ms=duration_ms,
        path=path,
        warning_issued=warning_issued,
        warning_number=warning_number,
        confidence=confidence,
        question_id=question_id,
        metadata_json=metadata,
    )
    run.session.add(row)
    run.accepted += 1
    return row


async def end_session(
    session: AsyncSession,
    ps: ProctoringSession,
    *,
    outcome: str,
    reason_code: str | None,
    now: datetime,
) -> None:
    """Close the proctoring session and the conversation under it.

    Shared by every way a session can end early: a Path A event, the third
    warning under a terminate policy, and the reconciler's abandonment sweep.
    """
    ps.outcome = outcome
    ps.ended_at = now
    ps.termination_reason = reason_code
    ps.updated_at = now
    conversation = await session.get(AssessmentConversation, ps.conversation_id)
    if conversation is not None and conversation.completed_at is None:
        conversation.status = CONVERSATION_TERMINATED
    await session.flush()
    await state.clear_session(ps.id)


async def terminate(
    session: AsyncSession,
    ps: ProctoringSession,
    reason_code: str,
    now: datetime,
    *,
    occurred_at: datetime | None = None,
    duration_ms: int | None = None,
    confidence: float | None = None,
    metadata: dict[str, Any] | None = None,
    enqueue: Callable[[str], None] = enqueue_assessment,
) -> TerminationOut:
    """Path A. Records the terminating event, ends the session, enqueues the
    report and returns the candidate's message."""
    if reason_code not in catalog.TERMINATING:
        raise ValueError(f"{reason_code!r} is not a Path A event")
    session.add(
        ProctoringEvent(
            tenant_id=ps.tenant_id,
            proctoring_session_id=ps.id,
            event_type=reason_code,
            occurred_at=occurred_at or now,
            duration_ms=duration_ms,
            path=catalog.PATH_A,
            warning_issued=False,
            warning_number=None,
            confidence=confidence,
            question_id=None,
            metadata_json=dict(metadata or {}),
        )
    )
    outcome = outcome_for_termination(reason_code, ps.warnings_used)
    await end_session(session, ps, outcome=outcome, reason_code=reason_code, now=now)
    logger.info(
        "proctoring.terminated session_id=%s reason=%s outcome=%s",
        ps.id, reason_code, outcome,
    )
    enqueue(str(ps.job_candidate_link_id))
    return termination_out(ps)


async def _apply(
    run: _Run,
    classified: Classified,
    *,
    occurred_at: datetime,
    duration_ms: int | None,
    confidence: float | None,
    question_id: uuid.UUID | None,
    metadata: dict[str, Any],
) -> None:
    """Apply one classified event to the session. The whole state machine."""
    kind, path = classified.event_type, classified.path
    spec = catalog.spec_for(kind)
    meta = {**metadata, **classified.note}

    if path == catalog.PATH_A:
        run.accepted += 1
        run.termination = await terminate(
            run.session, run.ps, kind, run.now,
            occurred_at=occurred_at, duration_ms=duration_ms,
            confidence=confidence, metadata=meta, enqueue=run.enqueue,
        )
        return

    if path == catalog.PATH_C:
        _store(
            run, event_type=kind, path=path, occurred_at=occurred_at,
            duration_ms=duration_ms, confidence=confidence,
            question_id=question_id, metadata=meta,
        )
        if kind == "IDENTITY_CHECK_MISMATCH":
            await _identity_check(run, meta)
        return

    # Path B, the shared counter.
    if spec.once_per_session and not await state.claim_once(run.ps.id, kind):
        _store(
            run, event_type=kind, path=catalog.PATH_C, occurred_at=occurred_at,
            duration_ms=duration_ms, confidence=confidence, question_id=question_id,
            metadata={**meta, NOTE_KEY: NOTE_ALREADY_REPORTED},
        )
        return
    if spec.cooldown_key and await state.in_cooldown(run.ps.id, spec.cooldown_key):
        _store(
            run, event_type=kind, path=path, occurred_at=occurred_at,
            duration_ms=duration_ms, confidence=confidence, question_id=question_id,
            metadata={**meta, NOTE_KEY: NOTE_WITHIN_COOLDOWN},
        )
        return
    if run.warned_this_batch:
        _store(
            run, event_type=kind, path=path, occurred_at=occurred_at,
            duration_ms=duration_ms, confidence=confidence, question_id=question_id,
            metadata={**meta, NOTE_KEY: NOTE_BATCH_ALREADY_WARNED},
        )
        return
    if run.warnings_used >= run.config.max_warnings:
        # The limit was reached under a continue-and-note policy. Recorded so
        # the report says it kept happening; no further warning exists to give.
        _store(
            run, event_type=kind, path=path, occurred_at=occurred_at,
            duration_ms=duration_ms, confidence=confidence, question_id=question_id,
            metadata={**meta, NOTE_KEY: NOTE_NO_WARNING_LEFT},
        )
        return

    number = await state.increment_warning(run.ps.id)
    if number > run.config.max_warnings:
        # Two batches raced past the limit. The row's CHECK forbids a count
        # above the maximum, and there is no fourth warning to issue.
        _store(
            run, event_type=kind, path=path, occurred_at=occurred_at,
            duration_ms=duration_ms, confidence=confidence, question_id=question_id,
            metadata={**meta, NOTE_KEY: NOTE_NO_WARNING_LEFT},
        )
        return
    run.warnings_used = number
    run.ps.warnings_used = number
    run.ps.updated_at = run.now
    run.warned_this_batch = True
    if spec.cooldown and spec.cooldown_key:
        await state.start_cooldown(
            run.ps.id, spec.cooldown_key, getattr(run.config, spec.cooldown)
        )
    decision = decide_warning(number, run.config.max_warnings, run.policy)
    _store(
        run, event_type=kind, path=path, occurred_at=occurred_at,
        duration_ms=duration_ms, confidence=confidence, question_id=question_id,
        metadata=meta, warning_issued=True, warning_number=number,
    )
    logger.info(
        "proctoring.warning session_id=%s event=%s number=%d final=%s terminate=%s",
        run.ps.id, kind, number, decision.final, decision.terminate,
    )
    if decision.terminate:
        await end_session(
            run.session, run.ps,
            outcome=OUTCOME_TERMINATED_WARNINGS, reason_code=kind, now=run.now,
        )
        run.enqueue(str(run.ps.job_candidate_link_id))
        run.termination = termination_out(run.ps)
        return
    run.warning = WarningOut(
        number=number,
        max_warnings=run.config.max_warnings,
        event_type=kind,
        message=phrasing.warning_message(
            kind, number=number, max_warnings=run.config.max_warnings, policy=run.policy
        ),
        final=decision.final,
    )


async def _identity_check(run: _Run, meta: dict[str, Any]) -> None:
    """Section 3.3: two consecutive mismatches confirm an identity mismatch.

    The run is counted in Redis so it survives the browser's batching, and it
    is reset by a heartbeat that reports a match (`heartbeat`) or by a check
    whose reported distance is inside the threshold.
    """
    distance = meta.get(identity.MISMATCH_DISTANCE_KEY)
    if not identity.is_mismatch(distance, run.config):
        meta[NOTE_KEY] = NOTE_WITHIN_DISTANCE
        await state.reset_consecutive(run.ps.id, _IDENTITY_RUN)
        return
    consecutive = await state.bump_consecutive(run.ps.id, _IDENTITY_RUN)
    if consecutive < run.config.identity_consecutive_mismatches:
        return
    await state.reset_consecutive(run.ps.id, _IDENTITY_RUN)
    run.accepted += 1
    run.termination = await terminate(
        run.session, run.ps, "IDENTITY_MISMATCH", run.now,
        metadata={"consecutive_mismatches": consecutive}, enqueue=run.enqueue,
    )


async def _start(
    session: AsyncSession,
    ps: ProctoringSession,
    policy: str,
    now: datetime,
    enqueue: Callable[[str], None],
) -> _Run:
    if ps.outcome in ENDED_OUTCOMES:
        raise SessionEnded(ps.outcome)
    config = get_config()
    used = await state.seed_warnings(ps.id, ps.warnings_used)
    return _Run(
        session=session, ps=ps, policy=policy, config=config, now=now,
        enqueue=enqueue, warnings_used=used,
    )


def _out(run: _Run) -> IngestOut:
    return IngestOut(
        accepted=run.accepted,
        warnings_used=run.warnings_used,
        max_warnings=run.config.max_warnings,
        status=run.ps.outcome,
        warning=run.warning,
        termination=run.termination,
    )


async def ingest(
    session: AsyncSession,
    ps: ProctoringSession,
    policy: str,
    batch: EventBatchIn,
    *,
    now: datetime,
    enqueue: Callable[[str], None] = enqueue_assessment,
) -> IngestOut:
    """One client batch. Raises `SessionEnded`, `RateLimited` or
    `state.StateUnavailable`; the API maps those to 409, 429 and 503."""
    run = await _start(session, ps, policy, now, enqueue)
    total = await state.count_in_minute(ps.id, now, len(batch.events))
    if total > catalog.CLIENT_RATE_LIMIT_PER_MINUTE:
        logger.warning(
            "proctoring.rate_limited session_id=%s minute_total=%d", ps.id, total
        )
        raise RateLimited(str(total))
    for event in batch.events:
        if run.ended:
            # Nothing after a termination is applied: the session is over and
            # the counters are cleared. The events are dropped, not stored,
            # because storing them would date them after the session ended.
            break
        await _apply(
            run, classify(event, run.config),
            occurred_at=event.occurred_at, duration_ms=event.duration_ms,
            confidence=event.confidence, question_id=event.question_id,
            metadata=dict(event.metadata),
        )
    await session.flush()
    return _out(run)


async def apply_server_event(
    session: AsyncSession,
    ps: ProctoringSession,
    policy: str,
    event_type: str,
    *,
    now: datetime,
    duration_ms: int | None = None,
    metadata: dict[str, Any] | None = None,
    enqueue: Callable[[str], None] = enqueue_assessment,
) -> IngestOut:
    """A SERVER-derived event (second voice, a heartbeat gap) through the same
    machinery a client event goes through, so a derived warning counts on the
    same counter and obeys the same policy."""
    if catalog.spec_for(event_type).client_emittable:
        raise ValueError(f"{event_type!r} is a client event; send it through ingest")
    run = await _start(session, ps, policy, now, enqueue)
    await _apply(
        run, Classified(event_type, catalog.spec_for(event_type).path),
        occurred_at=now, duration_ms=duration_ms, confidence=None,
        question_id=None, metadata=dict(metadata or {}),
    )
    await session.flush()
    return _out(run)


async def heartbeat(
    session: AsyncSession,
    ps: ProctoringSession,
    *,
    identity_matched: bool | None,
    monitoring: dict[str, bool],
    now: datetime,
) -> HeartbeatOut:
    """Section 9. A gap over `heartbeat_gap_seconds` is a monitoring
    interruption and appears in the report; a heartbeat that reports a
    matching identity check resets the consecutive-mismatch run.

    An ended session answers with its termination rather than a 409, because
    the browser that missed the terminating response is exactly the one still
    sending heartbeats.
    """
    config = get_config()
    if ps.outcome in ENDED_OUTCOMES:
        return HeartbeatOut(
            status=ps.outcome,
            warnings_used=ps.warnings_used,
            server_time=now,
            interval_seconds=config.heartbeat_interval_seconds,
            termination=termination_out(ps),
        )
    previous = ps.last_heartbeat_at or ps.started_at or ps.consented_at
    gap_ms = int((now - previous).total_seconds() * _MS_PER_SECOND)
    if gap_ms > _ms(config.heartbeat_gap_seconds):
        session.add(
            ProctoringEvent(
                tenant_id=ps.tenant_id,
                proctoring_session_id=ps.id,
                event_type="MONITORING_INTERRUPTED",
                occurred_at=previous,
                duration_ms=gap_ms,
                path=catalog.PATH_C,
                metadata_json={},
            )
        )
        logger.info("proctoring.heartbeat_gap session_id=%s gap_ms=%d", ps.id, gap_ms)
    ps.last_heartbeat_at = now
    ps.updated_at = now
    if identity_matched is True:
        await state.reset_consecutive(ps.id, _IDENTITY_RUN)
    down = sorted(name for name, alive in monitoring.items() if alive is False)
    if down and await state.claim_once(ps.id, "heartbeat_integrity_warning"):
        # The browser's own self-check escalates to INTEGRITY_CHECK_FAILED on
        # its own clock; this is the server's record that a heartbeat said a
        # component was down, once, so a browser whose events never arrive
        # still leaves a trace in the report.
        session.add(
            ProctoringEvent(
                tenant_id=ps.tenant_id,
                proctoring_session_id=ps.id,
                event_type="INTEGRITY_CHECK_WARNING",
                occurred_at=now,
                duration_ms=None,
                path=catalog.PATH_C,
                metadata_json={"components": down, "source": "heartbeat"},
            )
        )
    await session.flush()
    return HeartbeatOut(
        status=ps.outcome,
        warnings_used=ps.warnings_used,
        server_time=now,
        interval_seconds=config.heartbeat_interval_seconds,
        termination=None,
    )
