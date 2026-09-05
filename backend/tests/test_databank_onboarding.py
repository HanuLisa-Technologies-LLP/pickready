"""Gate 5: a databank candidate is not an applicant until they apply.

Workflow sections 10 to 13. A recruiter moving a resume out of their own filing
cabinet is a sourcing decision. It is not a person reading a job, wanting it,
and submitting an application with their notice period and expected
compensation in it. Until this gate existed the product wrote both as
`applied`, and every count, funnel and "applicants" figure downstream inherited
the claim with no way to unpick it afterwards.

The enforcement is a MISSING EDGE in the FSM rather than a flag, so most of
this file is about that edge: what it does not allow, and what still works
around it.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import hiring_pipeline as hp


# ── The stage ────────────────────────────────────────────────────────────────

def test_sourced_is_the_first_stage_and_is_not_applied() -> None:
    assert hp.SOURCED == "sourced"
    assert hp.SOURCED != hp.APPLIED
    assert hp.PIPELINE_ORDER[0] == hp.SOURCED
    assert hp.PIPELINE_ORDER[1] == hp.APPLIED
    assert hp.SOURCED in hp.ALL_STATUSES


def test_a_sourced_candidate_can_only_become_an_applicant() -> None:
    """ONE forward edge. This single fact is the whole of Gate 5.

    Not a flag somebody has to remember to check, and not a condition inside a
    handler that a second caller can skip: the moves simply do not exist, so
    every route in the product that goes through `apply_transition` refuses
    them without knowing this rule.
    """
    targets = hp.allowed_transitions(hp.SOURCED)
    assert hp.APPLIED in targets
    assert hp.ASSESSMENT_INVITED not in targets
    assert hp.ASSESSMENT_IN_PROGRESS not in targets
    assert hp.ASSESSMENT_COMPLETED not in targets
    assert hp.SHORTLISTED not in targets
    assert hp.OFFER_EXTENDED not in targets
    assert hp.JOINED not in targets


def test_a_recruiter_can_still_stop_or_pause_a_sourced_candidate() -> None:
    """`rejected` and `hold` stay reachable from everywhere.

    A recruiter can always decide not to pursue somebody they sourced, and
    making them invite that person first in order to reject them would be
    absurd.
    """
    targets = hp.allowed_transitions(hp.SOURCED)
    assert hp.REJECTED in targets
    assert hp.HOLD in targets


def test_entering_sourced_mails_nobody() -> None:
    """A resume landing in a databank is not an event the candidate caused.

    The invitation is a separate, deliberate act with its own route. Firing a
    transition email here would mail somebody the moment a recruiter uploaded
    a file, which is both a surprise to them and outside the recruiter's
    control.
    """
    assert hp.TRANSITION_EMAIL[hp.SOURCED] is None
    # And converting to `applied` DOES confirm, because by then they acted.
    assert hp.TRANSITION_EMAIL[hp.APPLIED] == "application_confirmation"


def test_the_stage_label_says_what_is_true() -> None:
    label = hp.STAGE_LABELS[hp.SOURCED]
    assert "applied" in label.lower()
    assert "not yet" in label.lower()


# ── The invitation ───────────────────────────────────────────────────────────

def test_the_invitation_is_not_a_transition_email() -> None:
    """Nothing about the candidate's state changes when it is sent.

    It is in EMAIL_TYPES so it can be logged and hand-composed, and absent from
    TRANSITION_EMAIL because there is no transition. A type in both would
    eventually be fired by the FSM and would move somebody into the pipeline
    for having been emailed.
    """
    from app.models.email_log import EMAIL_TYPE_DATABANK_INVITATION, EMAIL_TYPES

    assert EMAIL_TYPE_DATABANK_INVITATION in EMAIL_TYPES
    assert EMAIL_TYPE_DATABANK_INVITATION not in hp.TRANSITION_EMAIL.values()


def test_the_invitation_carries_the_application_link_and_is_link_checked() -> None:
    """The one URL it may contain, declared where the link guard reads it.

    An email type absent from `_REQUIRED_LINK_KEY` is not link-checked at all,
    and this is the one email in the product whose entire purpose is getting
    somebody to open a link they were not expecting.
    """
    from app.models.email_log import EMAIL_TYPE_DATABANK_INVITATION
    from app.services import lifecycle_email

    assert (
        lifecycle_email._REQUIRED_LINK_KEY[EMAIL_TYPE_DATABANK_INVITATION]
        == "application_link"
    )
    defects = lifecycle_email.link_defects(
        EMAIL_TYPE_DATABANK_INVITATION,
        {"application_link": "https://readypick.ai/apply/abc"},
        "Hi there, have a look at https://not-us.example.test/phish",
    )
    assert defects, "an invented address in this email must be a defect"


def test_the_deterministic_invitation_never_claims_they_applied() -> None:
    """The fallback body ships whenever the provider chain is down, so it is
    the copy most likely to be sent on the worst day.

    A person told they have applied will reasonably believe a decision is
    already being made about them, and will wait for an answer that is never
    coming because there is no application.
    """
    from app.models.email_log import EMAIL_TYPE_DATABANK_INVITATION
    from app.services import lifecycle_email

    subject, body = lifecycle_email.fallback_draft(
        EMAIL_TYPE_DATABANK_INVITATION,
        {
            "candidate_name": "Asha",
            "job_title": "Backend Engineer",
            "company_name": "Acme",
            "application_link": "https://readypick.ai/apply/abc",
        },
    )
    lowered = f"{subject}\n{body}".lower()
    assert "https://readypick.ai/apply/abc" in body
    assert "you have not applied" in lowered
    for claim in ("your application", "shortlist", "congratulations", "selected"):
        assert claim not in lowered, claim
    # No number reaches a candidate through it either.
    assert not any(character.isdigit() for character in subject)


# ── The route ────────────────────────────────────────────────────────────────

class _InviteSession:
    def __init__(self, rows) -> None:
        self._rows = rows
        self.added: list = []

    async def execute(self, *args, **kwargs):
        return SimpleNamespace(all=lambda: self._rows)

    async def get(self, model, ident):
        return SimpleNamespace(name="Acme")

    def add(self, row) -> None:
        self.added.append(row)
        # `_queue_databank_invitation` reads `log.id` back after the flush, so
        # the fake has to behave like a flush that assigned one.
        if getattr(row, "id", None) is None:
            row.id = uuid.uuid4()

    async def flush(self):
        return None


def _job(closed=False):
    from datetime import datetime, timedelta, timezone

    from app.models.job import Job
    from app.models.enums import JobStatus

    now = datetime.now(timezone.utc)
    return Job(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        title="Backend Engineer",
        jd_json={},
        status=JobStatus.ratified,
        ratified_at=now - timedelta(days=2),
        posting_start_date=now - timedelta(days=2),
        closed_at=now - timedelta(hours=1) if closed else None,
        created_by=uuid.uuid4(),
        created_at=now,
    )


def _link(job, status=hp.SOURCED):
    from app.models.candidate import SOURCE_TYPE_DATABANK, JobCandidateLink, LinkSource

    return JobCandidateLink(
        id=uuid.uuid4(),
        tenant_id=job.tenant_id,
        job_id=job.id,
        candidate_id=uuid.uuid4(),
        source=LinkSource.databank,
        source_type=SOURCE_TYPE_DATABANK,
        status=status,
    )


def _candidate(link, email="asha@example.com"):
    from app.models.candidate import Candidate

    return Candidate(
        id=link.candidate_id,
        tenant_id=link.tenant_id,
        email=email,
        full_name="Asha Rao",
    )


async def _invite(monkeypatch, job, pairs, link_ids=None):
    from app.api import jobs as jobs_api
    from app.schemas.jobs import DatabankInviteIn

    calls: dict = {}

    async def _fake_audit(session, **kwargs):
        calls["audit"] = kwargs

    async def _fake_visible(session, user, job_id):
        return job

    async def _fake_draft(email_type, context, session=None):
        calls.setdefault("drafts", []).append((email_type, context))
        return {
            "subject": "An opening at Acme",
            "body": f"Hi. {context['application_link']}",
            "html": "",
            "generated_by_ai": False,
            "email_type": email_type,
        }

    from app.services import lifecycle_email

    monkeypatch.setattr(jobs_api, "audit", _fake_audit)
    monkeypatch.setattr(jobs_api, "_get_visible_job", _fake_visible)
    monkeypatch.setattr(lifecycle_email, "draft", _fake_draft)
    monkeypatch.setattr(
        jobs_api, "dispatch",
        lambda *a, **k: calls.setdefault("tasks", []).append(a),
    )
    monkeypatch.setattr(
        jobs_api, "get_settings",
        lambda: SimpleNamespace(frontend_url="https://readypick.ai"),
    )
    user = SimpleNamespace(
        user_id=uuid.uuid4(), tenant_id=job.tenant_id, audience="org"
    )
    out = await jobs_api.invite_databank_candidates(
        job.id,
        DatabankInviteIn(link_ids=link_ids or [link.id for link, _ in pairs]),
        user=user,
        session=_InviteSession(pairs),
    )
    return out, calls


@pytest.mark.asyncio
async def test_inviting_a_sourced_candidate_queues_one_email(monkeypatch) -> None:
    job = _job()
    link = _link(job)
    out, calls = await _invite(monkeypatch, job, [(link, _candidate(link))])

    assert out.invited == 1 and out.skipped == 0
    assert out.results[0].invited is True
    # The link in the mail is the PUBLIC application page for this job: the
    # candidate signs in and applies there like anybody who found the role on
    # their own.
    _type, context = calls["drafts"][0]
    assert context["application_link"].endswith(f"/apply/{job.id}")
    assert calls["tasks"], "the send is a Celery task, never inline SMTP"


@pytest.mark.asyncio
async def test_the_invitation_moves_nobody_into_the_pipeline(monkeypatch) -> None:
    """The point of the whole feature.

    Being emailed is not applying. If sending the invitation advanced the
    stage, the recruiter would see an applicant who has done nothing, which is
    the exact confusion Gate 5 removes.
    """
    job = _job()
    link = _link(job)
    await _invite(monkeypatch, job, [(link, _candidate(link))])
    assert link.status == hp.SOURCED


@pytest.mark.asyncio
async def test_a_resume_with_no_email_is_skipped_with_a_reason(monkeypatch) -> None:
    """Mailing `@placeholder.invalid` sends nothing and bounces nowhere.

    It would write an `email_log` row claiming the candidate was contacted, so
    the recruiter would be told the invitation went out and would never hear
    back, with nothing anywhere explaining why.
    """
    from app.api import jobs as jobs_api

    job = _job()
    link = _link(job)
    placeholder = jobs_api._placeholder_email(job.id, "deadbeef")
    assert jobs_api.is_placeholder_email(placeholder)

    out, calls = await _invite(
        monkeypatch, job, [(link, _candidate(link, email=placeholder))]
    )
    assert out.invited == 0 and out.skipped == 1
    assert out.results[0].invited is False
    assert "email address" in out.results[0].reason
    assert "drafts" not in calls


@pytest.mark.asyncio
async def test_someone_who_already_applied_is_skipped(monkeypatch) -> None:
    job = _job()
    link = _link(job, status=hp.APPLIED)
    out, _ = await _invite(monkeypatch, job, [(link, _candidate(link))])
    assert out.invited == 0
    assert "already applied" in out.results[0].reason


@pytest.mark.asyncio
async def test_a_link_from_another_job_is_skipped_not_mailed(monkeypatch) -> None:
    """The query filters on this job; a missing id is REPORTED, not ignored.

    A batch that silently drops rows tells the recruiter twenty invitations
    went out when eleven did.
    """
    job = _job()
    stranger = uuid.uuid4()
    out, calls = await _invite(monkeypatch, job, [], link_ids=[stranger])
    assert out.requested == 1 and out.invited == 0 and out.skipped == 1
    assert out.results[0].link_id == stranger
    assert "not on this job" in out.results[0].reason
    assert "drafts" not in calls


@pytest.mark.asyncio
async def test_a_closed_job_refuses_the_whole_batch(monkeypatch) -> None:
    """The link would 404. Sending it anyway is worse than refusing: the
    candidate acts on it, fails, and blames themselves."""
    job = _job(closed=True)
    link = _link(job)
    with pytest.raises(HTTPException) as exc:
        await _invite(monkeypatch, job, [(link, _candidate(link))])
    assert exc.value.status_code == 409


# ── The candidate's side ─────────────────────────────────────────────────────

def test_a_sourced_row_is_not_read_as_a_duplicate_application() -> None:
    """The apply path must convert it, not refuse it.

    Refusing would tell somebody acting on our own invitation that they had
    already applied: false, a dead end, and with no reason for them to believe
    the message is wrong. Asserted against the SOURCE because the conversion
    sits inside a large multipart handler that a unit test cannot reach without
    reimplementing half of it.
    """
    import inspect

    from app.api import portal

    source = inspect.getsource(portal.apply_to_job)
    assert "sourced_link" in source
    # The refusal is conditional on it, rather than on `dup is not None`.
    assert "if dup is not None and sourced_link is None:" in source
    # And the conversion goes through the FSM, so the history records a real
    # `sourced -> applied` edge rather than a row that silently changed shape.
    assert "apply_transition" in source


def test_the_apply_screen_does_not_say_they_already_applied() -> None:
    import inspect

    from app.api import portal

    source = inspect.getsource(portal.apply_context)
    assert "hiring_pipeline.SOURCED" in source
