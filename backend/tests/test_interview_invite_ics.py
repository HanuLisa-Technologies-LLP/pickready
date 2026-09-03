"""The interview invite carries a real calendar attachment.

WHY THIS FILE EXISTS
--------------------
`api/candidates.schedule_interview` is documented "Interview invite with .ics",
ESD section 12 says the same and the PRD lists .ics interview scheduling as a
capability. Until 2026-09-01 the route minted an `ics_uid`, passed it inside
the template context, and never attached anything: `email_render.build_ics`
existed the whole time and nothing called it, so every invitation went out with
no way to accept it into a calendar.

Nothing caught it because the claim lived in prose. The dead-code gate is what
surfaced the unused function, and the unused function is what surfaced the gap.
This file is the assertion that was missing: it reads the arguments the route
hands the delivery task and checks an attachment is actually there, in the
shape the task and `smtp_service` agree on.

No network and no database. The route is called directly with the same stubs
`test_portal` uses, and the Celery send is captured rather than performed.
"""
from __future__ import annotations

import base64
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import email_render


# ── The builder itself ───────────────────────────────────────────────────────


def test_build_ics_emits_a_parseable_calendar_for_the_invited_pair() -> None:
    starts = datetime(2026, 9, 10, 9, 30, tzinfo=timezone.utc)
    raw = email_render.build_ics(
        uid="abc@readypick.test",
        summary="Backend Engineer interview with Acme",
        starts_at=starts,
        organizer_email="no-reply@acme.test",
        attendee_emails=["candidate@example.test"],
        description="Bring the take-home.",
    )
    assert isinstance(raw, bytes)
    text = raw.decode("utf-8")
    # The properties a calendar client needs to create the event at all.
    assert "BEGIN:VCALENDAR" in text and "END:VCALENDAR" in text
    assert "BEGIN:VEVENT" in text
    assert "UID:abc@readypick.test" in text
    assert "METHOD:REQUEST" in text
    assert "20260910T093000Z" in text
    assert "MAILTO:candidate@example.test" in text
    assert "MAILTO:no-reply@acme.test" in text


def test_the_event_ends_after_it_starts() -> None:
    """A zero or inverted duration produces an event a client silently drops."""
    starts = datetime(2026, 9, 10, 9, 30, tzinfo=timezone.utc)
    text = email_render.build_ics(
        uid="u@x.test", summary="s", starts_at=starts, duration_minutes=45
    ).decode("utf-8")
    assert "DTSTART:20260910T093000Z" in text
    assert "DTEND:20260910T101500Z" in text


# ── The route actually attaches it ───────────────────────────────────────────


class _Session:
    """Enough session for the route: it gets rows and flushes."""

    def __init__(self, rows: dict) -> None:
        self._rows = rows
        self.added: list[object] = []

    async def get(self, model, pk):  # noqa: ANN001 - mirrors AsyncSession.get
        return self._rows.get(model.__name__)

    def add(self, obj) -> None:  # noqa: ANN001
        self.added.append(obj)

    async def flush(self) -> None:
        # SQLAlchemy applies `UUIDPKMixin`'s default at flush, and the route
        # serialises the row afterwards. Without this the id is None and the
        # response model raises on a row that is otherwise correct.
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            if getattr(obj, "created_at", None) is None:
                obj.created_at = datetime.now(timezone.utc)

    async def execute(self, *_a, **_k):  # noqa: ANN001, ANN002
        raise AssertionError("this route should not query beyond session.get")


@pytest.mark.asyncio
async def test_the_invite_carries_an_ics_attachment(monkeypatch) -> None:
    """The regression this file exists for.

    Asserts on the ARGUMENTS handed to `pickready.send_email`, because that is
    the boundary the attachment has to cross. Asserting that `build_ics` was
    called would pass just as happily with the result thrown away.
    """
    from app.api import candidates as module
    from app.models.candidate import Candidate, Interview, JobCandidateLink
    from app.models.job import Job
    from app.models.tenant import Tenant

    tenant_id = uuid.uuid4()
    link = JobCandidateLink(
        tenant_id=tenant_id, job_id=uuid.uuid4(), candidate_id=uuid.uuid4()
    )
    link.id = uuid.uuid4()
    candidate = Candidate(email="candidate@example.test", full_name="Ada")
    tenant = Tenant(name="Acme", domain="acme.test")
    job = Job(tenant_id=tenant_id, title="Backend Engineer", jd_json={})

    session = _Session(
        {
            "Candidate": candidate,
            "Tenant": tenant,
            "Job": job,
            "Interview": None,
        }
    )

    async def fake_get_link(_session, _user, _link_id):  # noqa: ANN001
        return link

    sent: list[tuple] = []
    monkeypatch.setattr(module, "_get_link", fake_get_link)
    monkeypatch.setattr(
        module.celery_app, "send_task", lambda name, args: sent.append((name, args))
    )

    async def fake_audit(*_a, **_k):  # noqa: ANN002
        return None

    monkeypatch.setattr(module, "audit", fake_audit)

    scheduled = datetime.now(timezone.utc) + timedelta(days=3)
    await module.schedule_interview(
        link.id,
        SimpleNamespace(scheduled_at=scheduled, notes="Round two"),
        user=SimpleNamespace(tenant_id=tenant_id, user_id=uuid.uuid4()),
        session=session,
    )

    assert sent, "the invitation was never enqueued"
    name, args = sent[0]
    assert name == "pickready.send_email"
    # `pickready.send_email(tenant_id, to, template_name, context, attachments)`.
    # Four arguments means the attachments slot was never filled, which is
    # exactly the defect this test pins.
    assert len(args) == 5, (
        "the send is missing its attachments argument, so the invitation goes "
        "out with no calendar event. That is the defect this test pins."
    )

    attachments = args[4]
    assert len(attachments) == 1
    attachment = attachments[0]
    assert attachment["filename"].endswith(".ics")

    # The contract `pickready.send_email` documents and `smtp_service` decodes:
    # base64 text, not raw bytes.
    decoded = base64.b64decode(attachment["content"]).decode("utf-8")
    assert "BEGIN:VCALENDAR" in decoded
    assert "MAILTO:candidate@example.test" in decoded
    # The uid on the wire is the one persisted on the Interview row, so a
    # later reschedule can supersede this event rather than duplicate it.
    interview = next(row for row in session.added if isinstance(row, Interview))
    assert f"UID:{interview.ics_uid}" in decoded
