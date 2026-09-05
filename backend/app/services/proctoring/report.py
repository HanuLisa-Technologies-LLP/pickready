"""The Proctoring Report (section 7) and its join onto the PRISM Report.

WORDS, ORDERED, HEDGED
----------------------
Every field of the report is a sentence, a list of sentences or a table of
sentences. Counts are spelled out and durations are approximate, through
`phrasing.py`; the only digits are the clock times in the date line and the
activity log, which this module renders itself. "Ordering carries the
weight": within each findings group the most significant thing comes first
(a termination, then a warned occurrence, then an unwarned one, then a note),
and the summary opens with the single most significant finding of the whole
session. No icon, no colour, no column of severities.

WHAT COUNTS AS AN OCCURRENCE
----------------------------
Events of one type are folded into one sentence: "A phone was visible on
camera twice, for about a minute." The count excludes repeats the server
recorded inside a cooldown or after a once-per-session marker (they are the
same phone, still there), while the duration includes them (it was there
that long). Two identifiers that read the same to a recruiter, a fullscreen
exit and a focus loss, or a brief and a moderate absence, fold into one
family so the report never says the same sentence twice.

GAPS ARE STATED. A heartbeat gap, a degraded device, an unavailable analysis
service: each is a sentence in the findings and sets
`monitoring_was_incomplete`, because a report that was blind for a while and
says "no issues" is the false clean report section 9 forbids.

GENERATED ONCE. `generate` returns the existing row if one exists; the report
is a record of a session that is over and does not change afterwards.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.models.candidate import Candidate, JobCandidateLink
from app.models.job import Job
from app.models.proctoring import (
    OUTCOME_ACTIVE,
    OUTCOME_TERMINATED_WARNINGS,
    QUALITY_GOOD,
    REPORT_VERSION,
    ProctoringEvent,
    ProctoringReport,
    ProctoringSession,
)
from app.schemas.proctoring import ProctoringReportOut
from app.services.proctoring import catalog, ingestion, phrasing
from app.services.proctoring.config import get_config

logger = logging.getLogger(__name__)

__all__ = [
    "REPORT_TIMEZONE",
    "EventView",
    "compose",
    "generate",
    "load_report_out",
    "ReportNotReady",
]

#: Clock times in the report are rendered in the platform's timezone, the same
#: one the scheduled sweeps run against. A recruiter reading "10:32" should read
#: the time on their own wall clock, and the product is sold in India. Pinned by
#: `tests/test_proctoring_report.py` against `config.PLATFORM_TIMEZONE` so a
#: report and a schedule cannot drift into two different days.
REPORT_TIMEZONE = config.PLATFORM_TIMEZONE

#: Event types that read the same to a recruiter fold into one sentence.
_FAMILY: dict[str, str] = {
    "FULLSCREEN_EXITED": "WINDOW_FOCUS_LOST",
    "FACE_ABSENT_BRIEF": "FACE_ABSENT_MODERATE",
}

#: Server notes under which a stored event is a REPEAT rather than a new
#: occurrence. Its duration still counts.
_REPEAT_NOTES = frozenset({ingestion.NOTE_WITHIN_COOLDOWN, ingestion.NOTE_ALREADY_REPORTED})

_RANK_TERMINATED = 0
_RANK_WARNED = 1
_RANK_UNWARNED = 2
_RANK_NOTE = 3


class ReportNotReady(RuntimeError):
    """A report was asked for on a session that has not ended."""


@dataclass(frozen=True)
class EventView:
    """The slice of a `ProctoringEvent` the composer reads. A dataclass so the
    composer is testable from plain values without a database."""

    event_type: str
    occurred_at: datetime
    duration_ms: int | None
    path: str
    warning_issued: bool
    warning_number: int | None
    metadata: dict[str, Any]

    @classmethod
    def of(cls, row: ProctoringEvent) -> "EventView":
        return cls(
            event_type=row.event_type,
            occurred_at=row.occurred_at,
            duration_ms=row.duration_ms,
            path=row.path,
            warning_issued=bool(row.warning_issued),
            warning_number=row.warning_number,
            metadata=dict(row.metadata_json or {}),
        )

    @property
    def is_repeat(self) -> bool:
        return self.metadata.get(ingestion.NOTE_KEY) in _REPEAT_NOTES

    @property
    def audio_note(self) -> str | None:
        note = self.metadata.get("note")
        if self.event_type == "SESSION_QUALITY_DEGRADED" and note in (
            phrasing.AUDIO_UNAVAILABLE_NOTE, phrasing.AUDIO_FAILED_NOTE
        ):
            return str(note)
        return None


@dataclass
class _Finding:
    family: str
    group: str
    first_at: datetime
    occurrences: int = 0
    duration_ms: int = 0
    rank: int = _RANK_NOTE

    @property
    def sentence(self) -> str:
        return phrasing.finding_sentence(
            self.family, times=self.occurrences, duration_ms=self.duration_ms or None
        )


def _rank(event: EventView) -> int:
    if event.path == catalog.PATH_A:
        return _RANK_TERMINATED
    if event.path == catalog.PATH_B:
        return _RANK_WARNED if event.warning_issued else _RANK_UNWARNED
    return _RANK_NOTE


def _clock(moment: datetime, zone: ZoneInfo) -> str:
    return moment.astimezone(zone).strftime("%H:%M")


def _date_line(started: datetime, ended: datetime | None, zone: ZoneInfo) -> str:
    day = started.astimezone(zone).strftime("%d %B %Y").lstrip("0")
    if ended is None:
        return f"{day}, started at {_clock(started, zone)}"
    return f"{day}, {_clock(started, zone)} to {_clock(ended, zone)}"


def _group_of(event: EventView) -> str:
    if event.audio_note:
        return catalog.GROUP_AUDIO
    return catalog.spec_for(event.event_type).group


def _fold(events: list[EventView]) -> dict[str, _Finding]:
    """One finding per family, counts and durations folded in."""
    findings: dict[str, _Finding] = {}
    for event in sorted(events, key=lambda e: e.occurred_at):
        if event.audio_note:
            continue
        family = _FAMILY.get(event.event_type, event.event_type)
        finding = findings.get(family)
        if finding is None:
            finding = _Finding(
                family=family, group=_group_of(event), first_at=event.occurred_at
            )
            findings[family] = finding
        if not event.is_repeat:
            finding.occurrences += 1
        finding.duration_ms += int(event.duration_ms or 0)
        finding.rank = min(finding.rank, _rank(event))
    return findings


def _ordered(findings: dict[str, _Finding]) -> list[_Finding]:
    return sorted(findings.values(), key=lambda f: (f.rank, f.first_at))


def _system_sentences(events: list[EventView], ps_quality: str) -> list[str]:
    """Monitoring gaps and device quality notes, stated plainly."""
    sentences: list[str] = []
    gaps = [e for e in events if e.event_type == "MONITORING_INTERRUPTED"]
    for gap in sorted(gaps, key=lambda e: e.occurred_at):
        sentences.append(phrasing.finding_sentence(gap.event_type, times=1, duration_ms=gap.duration_ms))
    degraded = any(
        e.event_type == "SESSION_QUALITY_DEGRADED" and not e.audio_note for e in events
    )
    if degraded or ps_quality != QUALITY_GOOD:
        sentences.append(phrasing.MONITORING_REDUCED_RATE)
    return sentences


def _audio_sentence(events: list[EventView], audio_available: bool) -> str | None:
    """What to say about audio when it could not be listened to."""
    if not audio_available or any(e.audio_note == phrasing.AUDIO_UNAVAILABLE_NOTE for e in events):
        return phrasing.AUDIO_UNAVAILABLE
    if any(e.audio_note == phrasing.AUDIO_FAILED_NOTE for e in events):
        return (
            "Audio monitoring stopped working during the assessment, so a second "
            "voice may not have been detected."
        )
    return None


def _warned_sentence(ps_outcome: str, warnings: int, findings: list[_Finding]) -> str:
    if ps_outcome == OUTCOME_TERMINATED_WARNINGS:
        return (
            "The candidate was warned "
            f"{phrasing.count_word(warnings)} and the assessment was stopped when "
            "the warning limit was crossed, as set for this role."
        )
    if any(f.rank == _RANK_TERMINATED for f in findings):
        if warnings > 0:
            return (
                f"The candidate was warned {phrasing.count_word(warnings)} before "
                "the assessment was ended by the system."
            )
        return "The assessment was ended by the system without any prior warning."
    if warnings > 0:
        return (
            f"The candidate was warned {phrasing.count_word(warnings)} and "
            "continued to the end of the assessment."
        )
    return "The candidate was not warned at any point."


def compose(
    *,
    candidate_name: str,
    assessment_name: str,
    ps: ProctoringSession,
    events: list[EventView],
    audio_available: bool,
) -> dict[str, Any]:
    """The report content, from the session row and its events. Pure."""
    zone = ZoneInfo(REPORT_TIMEZONE)
    started = ps.started_at or ps.consented_at
    findings = _ordered(_fold(events))
    substantive = [
        f for f in findings
        if f.group != catalog.GROUP_SYSTEM
        and f.family != "BLOCKED_ACTION_ATTEMPTED"
    ]
    system = _system_sentences(events, ps.session_quality)
    audio_note = _audio_sentence(events, audio_available)

    groups: dict[str, list[str]] = {
        catalog.GROUP_SCREEN: [],
        catalog.GROUP_CAMERA: [],
        catalog.GROUP_AUDIO: [],
        catalog.GROUP_ANSWERS: [],
    }
    blocked = next((f for f in findings if f.family == "BLOCKED_ACTION_ATTEMPTED"), None)
    for finding in findings:
        if finding.group not in groups or finding is blocked:
            continue
        groups[finding.group].append(finding.sentence)
    if blocked is not None and blocked.occurrences > 0:
        # Section 7.2: the blocked-actions sentence appears ONLY when attempts
        # occurred, and it says what is blocked, not that blocking is total.
        groups[catalog.GROUP_SCREEN].append(blocked.sentence)
    if audio_note is not None:
        groups[catalog.GROUP_AUDIO].append(audio_note)
    for key, sentences in groups.items():
        if not sentences:
            groups[key] = [phrasing.NO_ISSUES]

    # The summary: the overall picture in two to four sentences.
    gaps = [*system, *([audio_note] if audio_note else [])]
    summary_parts: list[str] = []
    if substantive:
        lead = substantive[0].sentence
        summary_parts.append("The most notable thing detected: " + lead[0].lower() + lead[1:])
    else:
        summary_parts.append(phrasing.NO_EVENTS_AT_ALL)
    summary_parts.append(_warned_sentence(ps.outcome, ps.warnings_used, findings))
    summary_parts.extend(gaps[:2])

    activity: list[dict[str, str]] = []
    for event in sorted(events, key=lambda e: e.occurred_at):
        activity.append(
            {
                "time": _clock(event.occurred_at, zone),
                "what_happened": phrasing.activity_description(event.event_type, event.metadata),
                "how_long": (
                    phrasing.duration_phrase(event.duration_ms)
                    if event.duration_ms else "Momentary"
                ),
                "what_the_system_did": phrasing.system_action(
                    path=event.path,
                    warning_issued=event.warning_issued,
                    warning_number=event.warning_number,
                    terminated=event.path == catalog.PATH_A
                    or (
                        ps.outcome == OUTCOME_TERMINATED_WARNINGS
                        and event.warning_issued
                        and event.warning_number == ps.warnings_used
                    ),
                ),
            }
        )

    incomplete = bool(gaps) or ps.session_quality != QUALITY_GOOD
    return {
        "candidate": candidate_name,
        "assessment": assessment_name,
        "date_line": _date_line(started, ps.ended_at, zone),
        "outcome": phrasing.outcome_sentence(
            ps.outcome, warnings=ps.warnings_used, termination_reason=ps.termination_reason
        ),
        "summary": " ".join(summary_parts),
        "findings": {
            "screen_browser": groups[catalog.GROUP_SCREEN],
            "camera": groups[catalog.GROUP_CAMERA],
            "audio": groups[catalog.GROUP_AUDIO],
            "answer_patterns": groups[catalog.GROUP_ANSWERS],
        },
        "activity_log": activity,
        "closing": phrasing.CLOSING,
        "monitoring_was_incomplete": incomplete,
    }


async def _existing(session: AsyncSession, ps_id: uuid.UUID) -> ProctoringReport | None:
    return (
        await session.execute(
            select(ProctoringReport).where(ProctoringReport.proctoring_session_id == ps_id)
        )
    ).scalars().first()


async def generate(session: AsyncSession, ps: ProctoringSession) -> ProctoringReport:
    """Write the report once. Returns the existing row on a second call."""
    existing = await _existing(session, ps.id)
    if existing is not None:
        return existing
    if ps.outcome == OUTCOME_ACTIVE:
        raise ReportNotReady(f"proctoring session {ps.id} has not ended")
    rows = (
        await session.execute(
            select(ProctoringEvent)
            .where(ProctoringEvent.proctoring_session_id == ps.id)
            .order_by(ProctoringEvent.occurred_at)
        )
    ).scalars().all()
    candidate = await session.get(Candidate, ps.candidate_id)
    job = await session.get(Job, ps.job_id)
    link = await session.get(JobCandidateLink, ps.job_candidate_link_id)
    if candidate is None or job is None or link is None:
        raise ValueError(f"proctoring session {ps.id} is missing its candidate, job or link")
    content = compose(
        candidate_name=candidate.full_name or "Candidate",
        assessment_name=f"Tatva Assessment for {job.title}",
        ps=ps,
        events=[EventView.of(row) for row in rows],
        audio_available=get_config().audio_analysis_available,
    )
    now = datetime.now(timezone.utc)
    row = ProctoringReport(
        tenant_id=ps.tenant_id,
        proctoring_session_id=ps.id,
        generated_at=now,
        report_content=content,
        report_version=REPORT_VERSION,
    )
    session.add(row)
    await session.flush()
    logger.info(
        "proctoring.report_generated session_id=%s outcome=%s events=%d",
        ps.id, ps.outcome, len(rows),
    )
    return row


async def load_report_out(
    session: AsyncSession, link_id: uuid.UUID
) -> ProctoringReportOut | None:
    """The delivered shape for one application, or None when no report exists."""
    ps = (
        await session.execute(
            select(ProctoringSession).where(ProctoringSession.job_candidate_link_id == link_id)
        )
    ).scalars().first()
    if ps is None:
        return None
    row = await _existing(session, ps.id)
    if row is None:
        return None
    return ProctoringReportOut(**row.report_content, generated_at=row.generated_at)
