"""The emailed assessment link forces sign-in, and lands on THAT assessment.

Reported defect (2026-08-11): the invitation email carried
`{frontend}/portal/assessments/{link_id}` -- a bare application id inside a
guarded portal route. Two things followed from that and both were visible to
candidates:

  * nothing bound the link to the person it was sent to, and nothing expired,
    so the id was the whole of the authorization story; and
  * an unauthenticated click hit the portal shell, which redirected to `/login`
    with NO `next`, so signing in landed the candidate on the jobs board. They
    had been sent to the right page and the app threw the destination away.

The fix is a signed token resolved by `GET /assessments/invitations/{token}`
before any portal route is involved. What is pinned here is the ORDER of the
checks and the fact that every refusal is its own state rather than a generic
error -- the four edge cases in the brief (wrong account, expired, already
completed, retake) each have to be reachable and distinguishable, because a
fallthrough is exactly how "it just says something went wrong" happens.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.api.assessments import resolve_invitation
from app.models.assessment import AssessmentConversation
from app.models.candidate import JobCandidateLink
from app.models.job import Job
from app.models.tenant import Tenant
from app.models.user import User
from app.services import assessment_invite

INVITED = "asha@example.com"
NOW = datetime.now(timezone.utc)


# ── The token itself ─────────────────────────────────────────────────────────

def test_a_minted_token_round_trips() -> None:
    link_id = uuid.uuid4()
    payload = assessment_invite.verify(
        assessment_invite.mint(link_id=link_id, email=INVITED)
    )
    assert payload["link_id"] == str(link_id)
    assert payload["email"] == INVITED
    assert payload["purpose"] == assessment_invite.PURPOSE


def test_an_expired_token_is_expired_not_merely_invalid() -> None:
    """The two are told apart deliberately: "this link is too old" and "this is
    not one of our links" read completely differently to a candidate."""
    token = assessment_invite.mint(link_id=uuid.uuid4(), email=INVITED, ttl_days=-1)
    with pytest.raises(assessment_invite.InviteTokenError) as caught:
        assessment_invite.verify(token)
    assert caught.value.reason == "expired"


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not-a-token",
        "a.b.c",
        # A well-formed JWT signed with the wrong key.
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.Xbogus",
    ],
)
def test_garbage_is_invalid(token: str) -> None:
    with pytest.raises(assessment_invite.InviteTokenError) as caught:
        assessment_invite.verify(token)
    assert caught.value.reason == "invalid"


def test_a_session_token_cannot_be_used_as_an_invitation() -> None:
    """The audiences are disjoint on purpose. A candidate access token is the
    one thing an attacker is most likely to have lying around, and it must not
    open somebody else's assessment."""
    from app.core.security import AUDIENCE_CANDIDATE, create_access_token

    access = create_access_token(
        uuid.uuid4(), "candidate", None, audience=AUDIENCE_CANDIDATE
    )
    with pytest.raises(assessment_invite.InviteTokenError):
        assessment_invite.verify(access)


def test_the_invite_token_fails_every_portal_audience() -> None:
    """The direction that would actually grant something: an invitation token
    must not be accepted as a session by any portal."""
    import jwt as pyjwt

    from app.core.security import (
        AUDIENCE_CANDIDATE,
        AUDIENCE_ORG,
        AUDIENCE_OWNER,
        decode_token,
    )

    token = assessment_invite.mint(link_id=uuid.uuid4(), email=INVITED)
    for audience in (AUDIENCE_CANDIDATE, AUDIENCE_ORG, AUDIENCE_OWNER):
        with pytest.raises(pyjwt.PyJWTError):
            decode_token(token, audience=audience)


# ── Email matching ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "invited,signed_in",
    [
        ("asha@example.com", "asha@example.com"),
        ("Asha@Example.com", "asha@example.com"),
        ("asha@example.com", " ASHA@example.com "),
    ],
)
def test_case_and_whitespace_are_not_a_different_person(
    invited: str, signed_in: str
) -> None:
    assert assessment_invite.emails_match(invited, signed_in)


@pytest.mark.parametrize(
    "invited,signed_in",
    [
        ("asha@example.com", "ravi@example.com"),
        # The dangerous direction: two empty values must NOT compare equal, or
        # an account with no email walks into everyone's assessment.
        ("", ""),
        (None, None),
        ("asha@example.com", ""),
        ("", "asha@example.com"),
    ],
)
def test_a_different_or_missing_address_never_matches(invited, signed_in) -> None:
    assert not assessment_invite.emails_match(invited, signed_in)


def test_the_mask_shows_enough_to_recognise_and_no_more() -> None:
    assert assessment_invite.mask_email("asha@example.com") == "as***@example.com"
    assert assessment_invite.mask_email("a@example.com") == "a***@example.com"
    assert assessment_invite.mask_email(None) == "the invited address"


def test_the_email_link_is_built_in_exactly_one_place() -> None:
    url = assessment_invite.assessment_link_url(
        "https://app.example.com/", link_id=uuid.uuid4(), email=INVITED
    )
    assert url.startswith("https://app.example.com/assessments/invite/")
    # It must NOT be the raw portal path any more: that is the route that
    # bounced to a login with no destination.
    assert "/portal/assessments/" not in url


def test_a_candidate_with_no_email_still_gets_a_working_link() -> None:
    """A link that cannot be bound to an address is worse as a dead link than
    as an unbound one. `emails_match` refuses the empty string for everybody,
    so minting one here would break the flow for a data gap."""
    link_id = uuid.uuid4()
    url = assessment_invite.assessment_link_url(
        "https://app.example.com", link_id=link_id, email=None
    )
    assert url == f"https://app.example.com/portal/assessments/{link_id}"


# ── The resolver ─────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def first(self):
        return self._value


class _Session:
    """Serves `get` from a type-keyed table and `execute` from one queued row.

    The resolver reads a fixed set of objects in a fixed order, so a fake is
    enough and keeps these assertions about the DECISION rather than about
    SQLAlchemy.
    """

    def __init__(self, rows: dict, conversation=None) -> None:
        self._rows = rows
        self._conversation = conversation

    async def get(self, model, _id):
        return self._rows.get(model)

    async def execute(self, _statement):
        return _Result(self._conversation)


def _world(
    *,
    signed_in_email: str | None = INVITED,
    invited_at: datetime | None = NOW,
    started: bool = False,
    completed: bool = False,
    conversation: bool = True,
    posting_start: datetime | None = None,
):
    link_id = uuid.uuid4()
    link = SimpleNamespace(
        id=link_id,
        candidate_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        created_at=NOW - timedelta(days=1),
    )
    start = posting_start if posting_start is not None else NOW - timedelta(days=2)
    job = SimpleNamespace(
        id=link.job_id,
        title="Backend Engineer",
        posting_start_date=start,
        posting_end_date=start + timedelta(days=30),
        grace_period_end_date=start + timedelta(days=35),
    )
    rows = {
        JobCandidateLink: link,
        Job: job,
        Tenant: SimpleNamespace(name="Northwind"),
        User: SimpleNamespace(email=signed_in_email),
    }
    convo = (
        SimpleNamespace(
            invitation_sent_at=invited_at,
            started_at=NOW if started else None,
            completed_at=NOW if completed else None,
        )
        if conversation
        else None
    )
    return link, _Session(rows, convo)


def _candidate():
    from app.models.enums import Role

    return SimpleNamespace(user_id=uuid.uuid4(), tenant_id=None, role=Role.candidate)


async def _resolve(link, session, *, user, monkeypatch=None):
    token = assessment_invite.mint(link_id=link.id, email=INVITED)
    return await resolve_invitation(token, user=user, session=session)


@pytest.fixture(autouse=True)
def _no_retake_lookup(monkeypatch):
    """`retake.decide` queries a table this fake does not model. Default it to
    "no prior report"; the one test that cares overrides it."""
    from app.services import retake

    async def _decide(*_args, **_kwargs):
        return retake.RetakeDecision(decision=retake.DECISION_FIRST_ASSESSMENT)

    monkeypatch.setattr(retake, "decide", _decide)


@pytest.mark.asyncio
async def test_signed_out_is_sent_to_sign_in_and_nowhere_else() -> None:
    """The headline requirement: there is no path that skips auth. A signed-out
    caller gets `needs_auth` and, critically, NO redirect_to -- the page cannot
    forward someone to an assessment they have not authenticated for even if it
    wanted to."""
    link, session = _world()
    out = await _resolve(link, session, user=None)
    assert out.state == "needs_auth"
    assert out.redirect_to is None


@pytest.mark.asyncio
async def test_the_right_account_lands_on_that_assessment() -> None:
    link, session = _world()
    out = await _resolve(link, session, user=_candidate())
    assert out.state == "ready"
    assert out.redirect_to == f"/portal/assessments/{link.id}"
    assert out.job_title == "Backend Engineer"
    assert out.company_name == "Northwind"


@pytest.mark.asyncio
async def test_a_part_finished_assessment_resumes_rather_than_restarting() -> None:
    link, session = _world(started=True)
    out = await _resolve(link, session, user=_candidate())
    assert out.state == "in_progress"
    assert out.redirect_to == f"/portal/assessments/{link.id}"


@pytest.mark.asyncio
async def test_the_wrong_account_is_blocked_and_told_which_one_is_right() -> None:
    """Never silently attach the assessment to whoever happens to be signed in.
    Both addresses appear, one masked, because "wrong account" with no way to
    tell which account is right is an error nobody can act on."""
    link, session = _world(signed_in_email="ravi@example.com")
    out = await _resolve(link, session, user=_candidate())
    assert out.state == "wrong_account"
    assert out.redirect_to is None
    assert out.invited_email_masked == "as***@example.com"
    assert out.signed_in_email == "ravi@example.com"


@pytest.mark.asyncio
async def test_an_account_with_no_email_is_the_wrong_account() -> None:
    link, session = _world(signed_in_email=None)
    out = await _resolve(link, session, user=_candidate())
    assert out.state == "wrong_account"


@pytest.mark.asyncio
async def test_an_already_submitted_assessment_offers_the_application() -> None:
    link, session = _world(completed=True)
    out = await _resolve(link, session, user=_candidate())
    assert out.state == "completed"
    assert out.redirect_to is not None
    assert "/portal/assessments/" not in out.redirect_to


@pytest.mark.asyncio
async def test_submitted_beats_a_closed_window() -> None:
    """A candidate who finished on the last day is shown their submission, not
    told the job has closed. Ordering, not an accident."""
    link, session = _world(
        completed=True, posting_start=NOW - timedelta(days=60)
    )
    out = await _resolve(link, session, user=_candidate())
    assert out.state == "completed"


@pytest.mark.asyncio
async def test_a_closed_posting_is_its_own_state() -> None:
    link, session = _world(posting_start=NOW - timedelta(days=60))
    out = await _resolve(link, session, user=_candidate())
    assert out.state == "window_closed"
    assert out.redirect_to is None


@pytest.mark.asyncio
async def test_an_uninvited_application_is_refused_by_name() -> None:
    link, session = _world(conversation=False)
    out = await _resolve(link, session, user=_candidate())
    assert out.state == "not_invited"
    assert out.redirect_to is None


@pytest.mark.asyncio
async def test_an_invitation_row_with_no_send_stamp_is_not_an_invitation() -> None:
    link, session = _world(invited_at=None)
    out = await _resolve(link, session, user=_candidate())
    assert out.state == "not_invited"


@pytest.mark.asyncio
async def test_the_wrong_account_check_runs_before_the_state_checks() -> None:
    """Someone holding the link must not learn whether that candidate has
    finished their assessment. So `wrong_account` wins over `completed`."""
    link, session = _world(signed_in_email="ravi@example.com", completed=True)
    out = await _resolve(link, session, user=_candidate())
    assert out.state == "wrong_account"


@pytest.mark.asyncio
async def test_an_expired_token_never_reaches_the_database() -> None:
    class _Explode:
        async def get(self, *_a):
            raise AssertionError("the token was not checked first")

        async def execute(self, *_a):
            raise AssertionError("the token was not checked first")

    token = assessment_invite.mint(
        link_id=uuid.uuid4(), email=INVITED, ttl_days=-1
    )
    out = await resolve_invitation(token, user=None, session=_Explode())
    assert out.state == "expired"
    assert out.redirect_to is None


@pytest.mark.asyncio
async def test_a_recent_prior_report_is_reported_so_the_candidate_is_told_why(
    monkeypatch,
) -> None:
    """The six-month rule. It never SKIPS the assessment -- under PPI every
    section is scoped to the job it was written for -- but the candidate is
    owed the reason they are answering questions again."""
    from app.services import retake

    async def _decide(*_args, **_kwargs):
        return retake.RetakeDecision(
            decision=retake.DECISION_REUSE, age_days=30
        )

    monkeypatch.setattr(retake, "decide", _decide)
    link, session = _world()
    out = await _resolve(link, session, user=_candidate())
    assert out.state == "ready"
    assert out.recent_prior_report is True
    assert out.redirect_to == f"/portal/assessments/{link.id}"


@pytest.mark.asyncio
async def test_a_failing_retake_lookup_does_not_block_the_assessment(
    monkeypatch,
) -> None:
    """The classification is explanatory. It must never be the reason a
    candidate cannot open their assessment."""
    from app.services import retake

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("prior-report lookup is down")

    monkeypatch.setattr(retake, "decide", _boom)
    link, session = _world()
    out = await _resolve(link, session, user=_candidate())
    assert out.state == "ready"
    assert out.recent_prior_report is False


@pytest.mark.asyncio
async def test_a_deleted_application_says_so_rather_than_crashing() -> None:
    link, _ = _world()
    empty = _Session({}, None)
    out = await _resolve(link, empty, user=_candidate())
    assert out.state == "gone"


@pytest.mark.asyncio
async def test_every_state_carries_a_real_explanation() -> None:
    """Section 1's engineering constraint, applied to this endpoint: no state
    may fall through to a generic message, and no refusal may carry a
    destination."""
    from app.api.assessments import _INVITE_STATE_MESSAGES

    cases = [
        (_world(), _candidate()),
        (_world(signed_in_email="ravi@example.com"), _candidate()),
        (_world(completed=True), _candidate()),
        (_world(conversation=False), _candidate()),
        (_world(posting_start=NOW - timedelta(days=60)), _candidate()),
        (_world(), None),
    ]
    for (link, session), user in cases:
        out = await _resolve(link, session, user=user)
        assert out.message and len(out.message) > 20, out.state
        assert out.message != "This link could not be opened.", out.state
        if out.state not in {"ready", "in_progress", "completed"}:
            assert out.redirect_to is None, out.state
    # And the table itself has no empty entries.
    assert all(text.strip() for text in _INVITE_STATE_MESSAGES.values())
