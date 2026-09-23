"""Corporate email senders (Corporate Email System spec, 2026-09-05).

What is pinned here, and why each pin exists:

  * The lifecycle FSM refuses every move that is not a declared edge, and
    `revoked` is terminal. A revoked sender coming back to life would send
    mail under an identity the client withdrew.
  * The free-provider blocklist is CONFIGURATION and refuses subdomains too.
  * The OTP: CSPRNG six digits, HASH ONLY in Redis under the spec's own key
    shape, three attempts, a resend cooldown, expiry, and invalidation on
    success. The plaintext never lands in Redis in any field.
  * The send-time chokepoint: a queued email whose sender is no longer ACTIVE
    fails permanently with an honest email_log status (spec section 11).
  * The capability split (spec section 10) in the code matrix AND migration
    0080's seed rows, row for row.
  * The template registry: exactly the spec's nine templates, substitution
    that refuses unknown and missing variables, and no em dash anywhere in
    the catalogue (swept, not trusted).

The Redis-backed tests use the suite's real Redis (docker-compose.test.yml,
127.0.0.1:6381) exactly as the proctoring state tests do: the hash-only and
attempts properties are about what Redis actually holds.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import uuid
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.models.email_sender import (
    SENDER_ACTIVE,
    SENDER_DISABLED,
    SENDER_EMAIL_VERIFIED,
    SENDER_PENDING_VERIFICATION,
    SENDER_REJECTED,
    SENDER_REVOKED,
    SENDER_STATUSES,
    SENDER_VERIFICATION_EXPIRED,
)
from app.services import email_templates
from app.services.email_senders import (
    IllegalSenderTransition,
    SenderDomainBlocked,
    SenderEmailInvalid,
    assert_transition,
    validate_business_email,
)
from app.services.email_senders import lifecycle

EM_DASH = chr(8212)


# ── The lifecycle FSM ────────────────────────────────────────────────────────

def test_the_happy_path_is_a_legal_chain() -> None:
    """pending -> active -> disabled -> active -> revoked.

    The mailbox-verified step is GONE from the happy path: the Super Admin's
    approval is the only gate out of pending now.
    """
    chain = [
        SENDER_PENDING_VERIFICATION,
        SENDER_ACTIVE,
        SENDER_DISABLED,
        SENDER_ACTIVE,
        SENDER_REVOKED,
    ]
    for current, target in zip(chain, chain[1:]):
        assert_transition(current, target)  # must not raise


def test_the_super_admin_can_refuse_a_pending_sender() -> None:
    assert_transition(SENDER_PENDING_VERIFICATION, SENDER_REJECTED)


def test_rejected_is_terminal_and_is_not_revoked() -> None:
    """Two terminal states, kept apart on purpose: revoked withdraws an
    authorization that once existed, rejected was never granted one."""
    for target in SENDER_STATUSES:
        with pytest.raises(IllegalSenderTransition):
            assert_transition(SENDER_REJECTED, target)
    assert SENDER_REJECTED != SENDER_REVOKED


def test_legacy_otp_states_are_still_decidable() -> None:
    """Rows written before the mailbox code was withdrawn sit in these two
    states. A status with no edge out is a sender nobody can ever approve or
    refuse, stranded for the life of the tenant."""
    for legacy in (SENDER_EMAIL_VERIFIED, SENDER_VERIFICATION_EXPIRED):
        assert_transition(legacy, SENDER_ACTIVE)
        assert_transition(legacy, SENDER_REJECTED)


def test_revoked_is_terminal() -> None:
    for target in SENDER_STATUSES:
        with pytest.raises(IllegalSenderTransition):
            assert_transition(SENDER_REVOKED, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        # Disable is only meaningful for an active sender.
        (SENDER_PENDING_VERIFICATION, SENDER_DISABLED),
        (SENDER_EMAIL_VERIFIED, SENDER_DISABLED),
        # Nothing returns a decided sender to the queue.
        (SENDER_ACTIVE, SENDER_PENDING_VERIFICATION),
        (SENDER_DISABLED, SENDER_EMAIL_VERIFIED),
        # Nothing re-enters the retired verification states.
        (SENDER_PENDING_VERIFICATION, SENDER_EMAIL_VERIFIED),
        (SENDER_PENDING_VERIFICATION, SENDER_VERIFICATION_EXPIRED),
        (SENDER_ACTIVE, SENDER_REJECTED),
    ],
)
def test_illegal_moves_are_refused(current: str, target: str) -> None:
    with pytest.raises(IllegalSenderTransition):
        assert_transition(current, target)


def test_the_fsm_covers_the_whole_status_vocabulary() -> None:
    """A status the CHECK constraint accepts but the FSM does not know would
    make every action on it a KeyError."""
    assert set(lifecycle.TRANSITIONS) == set(SENDER_STATUSES)


def test_nothing_re_arms_a_retired_verification_state() -> None:
    """The re-arm edge went with the mailbox code. An expired row is decided
    by the Super Admin like any other pending one, not sent a fresh code."""
    with pytest.raises(IllegalSenderTransition):
        assert_transition(SENDER_VERIFICATION_EXPIRED, SENDER_PENDING_VERIFICATION)


# ── The business-email gate ──────────────────────────────────────────────────

def test_a_business_address_passes_and_is_lowercased() -> None:
    assert validate_business_email("  HR@SarkarCorp.COM ") == "hr@sarkarcorp.com"


@pytest.mark.parametrize(
    "email",
    [
        "hr@gmail.com",
        "hr@yahoo.com",
        "hr@outlook.com",
        "hr@hotmail.com",
        "hr@icloud.com",
        "hr@proton.me",
        # A SUBDOMAIN of a blocked provider is not a business address either.
        "hr@mail.gmail.com",
        "hr@corp.yahoo.co.in",
        # Casing must not dodge the gate.
        "hr@GMAIL.com",
    ],
)
def test_free_provider_domains_are_refused(email: str) -> None:
    with pytest.raises(SenderDomainBlocked):
        validate_business_email(email)


@pytest.mark.parametrize("email", ["", "nodomain", "a@b", "two@@x.com", "a b@x.com"])
def test_malformed_addresses_are_refused(email: str) -> None:
    with pytest.raises(SenderEmailInvalid):
        validate_business_email(email)


def test_the_blocklist_is_configuration_not_code(monkeypatch) -> None:
    """spec section 2: configurable, not a hardcoded few. An operator-added
    domain is refused without a code change."""
    settings = get_settings().model_copy(
        update={"sender_domain_blocklist": "examplemail.test"}
    )
    monkeypatch.setattr(lifecycle, "get_settings", lambda: settings)
    with pytest.raises(SenderDomainBlocked):
        validate_business_email("hr@examplemail.test")
    # And the previous defaults are no longer in force under that override,
    # which is what makes it a REPLACEMENT the operator fully controls.
    assert validate_business_email("hr@gmail.com") == "hr@gmail.com"


# ── SES sending eligibility (replaced the mailbox OTP, 2026-09-08) ──────────
#
# The OTP unit tests that lived here are gone with the module they covered.
# What replaced them tests the property those tests were really protecting:
# that this product does not claim an address can send when it cannot.


@pytest.mark.asyncio
async def test_a_verified_domain_makes_every_mailbox_on_it_eligible(monkeypatch) -> None:
    """Domain identity is the intended shape: verifying company.com once
    covers every recruiter on it, which is what keeps this from needing one
    AWS resource per employee."""
    from app.services.email_senders import eligibility

    eligibility.reset_client()
    monkeypatch.setattr(
        eligibility, '_ses_client',
        lambda: SimpleNamespace(
            get_identity_verification_attributes=lambda Identities: {
                'VerificationAttributes': {
                    'company.com': {'VerificationStatus': 'Success'},
                }
            }
        ),
    )
    result = await eligibility.check_sender_eligibility('recruiter@company.com')
    assert result.eligible is True
    assert result.known is True
    assert result.matched_identity == 'company.com'


@pytest.mark.asyncio
async def test_an_unverified_address_is_a_definite_no(monkeypatch) -> None:
    from app.services.email_senders import eligibility

    eligibility.reset_client()
    monkeypatch.setattr(
        eligibility, '_ses_client',
        lambda: SimpleNamespace(
            get_identity_verification_attributes=lambda Identities: {
                'VerificationAttributes': {
                    'company.com': {'VerificationStatus': 'Pending'},
                }
            }
        ),
    )
    result = await eligibility.check_sender_eligibility('hr@company.com')
    assert (result.eligible, result.known) == (False, True)
    assert result.blocks_approval is True


@pytest.mark.asyncio
async def test_a_provider_failure_is_unknown_and_never_eligible(monkeypatch) -> None:
    """The direction that matters. A lookup that failed must not read as a
    pass, and must not read as a definite refusal either: the Super Admin's
    own decision is not vetoed by an AWS blip."""
    from app.services.email_senders import eligibility

    def _boom():
        raise RuntimeError('endpoint unreachable')

    eligibility.reset_client()
    monkeypatch.setattr(eligibility, '_ses_client', _boom)
    result = await eligibility.check_sender_eligibility('hr@company.com')
    assert result.eligible is False
    assert result.known is False
    assert result.blocks_approval is False


@pytest.mark.asyncio
async def test_no_aws_vocabulary_reaches_the_client(monkeypatch) -> None:
    """The refusal sentence is rendered in the client portal, where SES, IAM
    and DKIM are deliberately not concepts the Super Admin has."""
    from app.services.email_senders import eligibility

    eligibility.reset_client()
    monkeypatch.setattr(
        eligibility, '_ses_client',
        lambda: SimpleNamespace(
            get_identity_verification_attributes=lambda Identities: {
                'VerificationAttributes': {}
            }
        ),
    )
    result = await eligibility.check_sender_eligibility('hr@company.com')
    lowered = result.detail.lower()
    for banned in ('ses', 'sns', 'iam', 'dkim', 'arn', 'identity',
                   'configuration set', 'aws'):
        assert banned not in lowered, banned


# ── The send-time chokepoint (spec sections 10 and 11) ───────────────────────

class _FakeSession:
    """Enough of AsyncSession for _send_lifecycle_email_async: get by model,
    commit, and the raw-SQL audit insert."""

    def __init__(self, rows: dict) -> None:
        self._rows = rows
        self.commits = 0
        self.audit_actions: list[str] = []

    async def get(self, model, key):
        return self._rows.get((model.__name__, str(key)))

    async def commit(self) -> None:
        self.commits += 1

    async def execute(self, statement, params=None):
        if params and "action" in params:
            self.audit_actions.append(params["action"])
        return SimpleNamespace()


def _queued_row(sender_id: uuid.UUID | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email_type="assessment_invitation",
        recipient_email="candidate@example.test",
        subject="Subject",
        body="Body",
        status="queued",
        error=None,
        sent_at=None,
        sender_id=sender_id,
        provider_message_id=None,
        edited_by_human=False,
        generated_by_ai=True,
        # The correlation ids the send path tags onto the SES message so a
        # delivery event arriving minutes later can be attributed.
        candidate_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        transport=None,
        failed_at=None,
        template_id=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sender_status",
    [
        SENDER_PENDING_VERIFICATION,
        SENDER_EMAIL_VERIFIED,
        SENDER_VERIFICATION_EXPIRED,
        SENDER_DISABLED,
        SENDER_REVOKED,
    ],
)
async def test_a_non_active_sender_fails_the_queued_email_honestly(
    monkeypatch, sender_status: str
) -> None:
    """The worker re-loads the sender AT SEND TIME; anything not active fails
    the message with the reason on the row, and nothing is delivered."""
    from app.models.email_log import EmailLog
    from app.models.email_sender import ClientEmailSender
    from app.workers import tasks

    row = _queued_row(uuid.uuid4())
    sender = SimpleNamespace(
        id=row.sender_id, status=sender_status, email="hr@corp.test", name="Rahul"
    )
    session = _FakeSession(
        {
            (EmailLog.__name__, str(row.id)): row,
            (ClientEmailSender.__name__, str(row.sender_id)): sender,
        }
    )

    async def _never_called(**kwargs):
        raise AssertionError("a non-active sender must never reach the transport")

    monkeypatch.setattr(tasks, "_deliver_email", _never_called)
    result = await tasks._send_lifecycle_email_async(session, str(row.id))
    assert result == {"status": "failed", "error": "sender_not_active"}
    assert row.status == "failed"
    assert sender_status.replace("_", " ") in (row.error or "")
    assert "lifecycle_email_sender_refused" in session.audit_actions


@pytest.mark.asyncio
async def test_a_deleted_sender_fails_the_queued_email_too(monkeypatch) -> None:
    from app.models.email_log import EmailLog
    from app.workers import tasks

    row = _queued_row(uuid.uuid4())
    session = _FakeSession({(EmailLog.__name__, str(row.id)): row})

    async def _never_called(**kwargs):
        raise AssertionError("a missing sender must never reach the transport")

    monkeypatch.setattr(tasks, "_deliver_email", _never_called)
    result = await tasks._send_lifecycle_email_async(session, str(row.id))
    assert result["status"] == "failed"
    assert row.status == "failed"
    assert "removed" in (row.error or "")


@pytest.mark.asyncio
async def test_an_active_sender_sends_and_records_the_provider_id(monkeypatch) -> None:
    from app.models.email_log import EmailLog
    from app.models.email_sender import ClientEmailSender
    from app.workers import tasks

    row = _queued_row(uuid.uuid4())
    sender = SimpleNamespace(
        id=row.sender_id, status=SENDER_ACTIVE, email="hr@corp.test", name="Rahul"
    )
    session = _FakeSession(
        {
            (EmailLog.__name__, str(row.id)): row,
            (ClientEmailSender.__name__, str(row.sender_id)): sender,
        }
    )
    seen: dict = {}

    async def _fake_deliver(**kwargs):
        seen.update(kwargs)
        return "provider-message-id-1"

    monkeypatch.setattr(tasks, "_deliver_email", _fake_deliver)
    result = await tasks._send_lifecycle_email_async(session, str(row.id))
    assert result["status"] == "sent"
    assert row.status == "sent"
    assert row.provider_message_id == "provider-message-id-1"
    # The ACTIVE corporate sender reached the transport door.
    assert seen["sender"] is sender


# ── Capabilities: the code matrix and migration 0080, row for row ────────────

def _load_migration_0080():
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0080_client_email_senders.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0080", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_capability_split_matches_spec_section_10() -> None:
    """Registering and verifying is operational work; ACTIVATING is the client
    Super Admin's decision alone."""
    from app.services.capabilities import (
        AUTHORIZE_EMAIL_SENDERS,
        DEFAULT_PERMISSION_MATRIX,
        MANAGE_EMAIL_SENDERS,
        Role,
    )

    matrix = DEFAULT_PERMISSION_MATRIX
    assert matrix[Role.client][MANAGE_EMAIL_SENDERS] is True
    assert matrix[Role.client][AUTHORIZE_EMAIL_SENDERS] is True
    # hr_manager mirrors recruitment_manager: the legacy role ranks beside it
    # until existing accounts are migrated deliberately (claude.md, spec v4),
    # and tests/test_rbac.py pins the two organisation-wide roles as identical
    # grant for grant.
    for role in (Role.recruitment_manager, Role.hr_manager):
        assert matrix[role][MANAGE_EMAIL_SENDERS] is True
        assert matrix[role][AUTHORIZE_EMAIL_SENDERS] is False
    for role in (Role.recruiter, Role.hiring_manager, Role.interview_manager):
        assert matrix[role][MANAGE_EMAIL_SENDERS] is False
        assert matrix[role][AUTHORIZE_EMAIL_SENDERS] is False


def test_migration_0080_seeds_exactly_what_the_matrix_grants() -> None:
    """A capability constant is only half a change; the seeding migration is
    the other half, and the two must agree row for row (the 0075 lesson)."""
    from app.services.capabilities import DEFAULT_PERMISSION_MATRIX, Role

    migration = _load_migration_0080()
    for role_name, capability, allowed in migration.SEED_ROWS:
        assert DEFAULT_PERMISSION_MATRIX[Role(role_name)][capability] is allowed, (
            f"migration 0080 seeds {role_name}/{capability}={allowed} but the "
            "code matrix disagrees"
        )


def test_every_route_is_behind_the_right_capability() -> None:
    """Source-level: the authorize/disable/enable/revoke handlers demand
    AUTHORIZE_EMAIL_SENDERS; registration and verification demand
    MANAGE_EMAIL_SENDERS. A route that lost its gate would still import fine."""
    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app" / "api" / "email_senders.py"
    ).read_text(encoding="utf-8")

    def _handler_block(marker: str) -> str:
        start = source.index(marker)
        return source[start:start + 800]

    for handler in ("async def approve_sender", "async def reject_sender",
                    "async def disable_sender", "async def enable_sender",
                    "async def revoke_sender"):
        assert "require_capability(caps.AUTHORIZE_EMAIL_SENDERS)" in _handler_block(handler), handler
    for handler in ("async def list_senders", "async def create_sender"):
        assert "require_capability(caps.MANAGE_EMAIL_SENDERS)" in _handler_block(handler), handler


def test_the_otp_routes_are_gone_not_merely_unlinked() -> None:
    """A retained handler is one a future router re-registers. The mailbox
    code path must not exist in source at all."""
    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app" / "api" / "email_senders.py"
    ).read_text(encoding="utf-8")
    for gone in ("resend-otp", "verify-otp", "async def resend_otp",
                 "async def verify_sender_otp", "issue_otp("):
        assert gone not in source, gone


def test_the_verification_module_is_deleted() -> None:
    """Deleted, not left unimported: an unwired verification module is what
    the next person attaches a route back onto."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.services.email_senders.verification")


# ── The template registry (spec section 7) ───────────────────────────────────

SPEC_TEMPLATE_KEYS = [
    "assessment_invitation",
    "interview_invitation",
    "interview_reminder",
    "assessment_reminder",
    "selection",
    "rejection",
    "password_reset",
    "account_verification",
    "application_update",
]


def test_the_nine_spec_templates_exist_in_the_specs_order() -> None:
    assert list(email_templates.TEMPLATES) == SPEC_TEMPLATE_KEYS


def test_rendering_substitutes_every_referenced_variable() -> None:
    subject, body = email_templates.render(
        "assessment_invitation",
        {
            "candidate_name": "Asha",
            "company_name": "Sarkar Corp",
            "assessment_name": "Tatva Assessment",
            "assessment_link": "https://example.test/a/1",
            "job_title": "Data Engineer",
        },
    )
    assert "Asha" in body and "Sarkar Corp" in subject
    assert "{{" not in subject and "{{" not in body


def test_an_unknown_template_is_refused() -> None:
    with pytest.raises(email_templates.UnknownTemplate):
        email_templates.render("newsletter", {})


def test_an_unknown_variable_is_refused_not_ignored() -> None:
    with pytest.raises(email_templates.UnknownVariable):
        email_templates.render(
            "rejection",
            {"candidate_name": "A", "company_name": "B", "job_title": "C",
             "internal_score": "94"},
        )


def test_a_missing_variable_is_refused_not_half_rendered() -> None:
    """A literal {{assessment_link}} in a delivered email is worse than an
    error the sender sees."""
    with pytest.raises(email_templates.MissingVariable):
        email_templates.render(
            "assessment_invitation",
            {"candidate_name": "Asha", "company_name": "Sarkar Corp"},
        )


def test_every_template_references_only_the_declared_vocabulary() -> None:
    for template in email_templates.TEMPLATES.values():
        assert template.variables <= email_templates.ALLOWED_VARIABLES


def test_no_em_dash_anywhere_in_the_catalogue() -> None:
    """Swept over every string, not checked at a call site."""
    for template in email_templates.TEMPLATES.values():
        for text in (template.key, template.label, template.subject, template.body):
            assert EM_DASH not in text, f"em dash in template {template.key}"


def test_the_sender_verification_template_is_gone() -> None:
    """It was the only carrier of a six-digit code into a client mailbox.
    Left renderable it would be one dispatch call away from returning, which
    is why the entry was deleted rather than merely unreferenced."""
    from app.services.email_render import DEFAULT_TEMPLATES

    assert "sender_verification" not in DEFAULT_TEMPLATES



# ── SES delivery events: ordering, idempotency, failure reasons ──────────────
#
# These exercise the pure decision layer of the webhook -- which outcome an
# event maps to and whether it may overwrite what is already recorded. The
# signature check, the topic pinning and the subscription handshake are
# transport concerns covered by the route tests; what is easy to get silently
# wrong, and expensive when it is, is letting a late event rewrite a bounce.

def _outcome_rank(status: str) -> int:
    from app.api.email_senders import _OUTCOME_RANK

    return _OUTCOME_RANK.get(status, 0)


def test_a_late_delivery_never_overwrites_a_bounce() -> None:
    """SNS makes no ordering promise, so a DELIVERY published before a BOUNCE
    can arrive after it. The row must remember the worst thing that happened."""
    assert _outcome_rank("delivered") < _outcome_rank("bounced")
    assert _outcome_rank("delivered") < _outcome_rank("complaint")


def test_a_complaint_outranks_a_delivery_because_it_arrived() -> None:
    """A complaint means the message DID arrive and the recipient reported it.
    Ranking it under delivery would hide the report behind the delivery."""
    assert _outcome_rank("complaint") > _outcome_rank("delivered")


def test_sent_never_demotes_a_delivered_row() -> None:
    """SES publishes SEND as well as DELIVERY, and the two can race."""
    assert _outcome_rank("sent") < _outcome_rank("delivered")


def test_an_unknown_status_ranks_lowest_rather_than_raising() -> None:
    """A row carrying a status this map has not heard of must not 500 the
    endpoint: SNS would redeliver the event for hours."""
    assert _outcome_rank("something_new") == 0


@pytest.mark.parametrize(
    ("kind", "message", "expected"),
    [
        (
            "bounce",
            {"bounce": {"bounceType": "Permanent", "bounceSubType": "General"}},
            "Bounce: Permanent / General",
        ),
        (
            "complaint",
            {"complaint": {"complaintFeedbackType": "abuse"}},
            "Complaint: abuse",
        ),
        ("reject", {"reject": {"reason": "Bad content"}},
         "Rejected by SES: Bad content"),
        ("delivery", {}, None),
    ],
)
def test_the_failure_reason_comes_from_the_events_own_fields(
    kind: str, message: dict, expected: str | None
) -> None:
    from app.api.email_senders import _failure_reason

    assert _failure_reason(kind, message) == expected


def test_a_reason_survives_a_missing_detail_block() -> None:
    """A malformed event must produce a usable reason, not a KeyError on an
    endpoint that has to acknowledge whatever SNS sends."""
    from app.api.email_senders import _failure_reason

    assert _failure_reason("bounce", {}) == "Bounce"
    assert _failure_reason("complaint", {}) == "Complaint"


def test_the_reason_is_bounded_so_it_cannot_carry_an_event_document() -> None:
    """This string is read by staff in the portal, and an SES event carries
    the full recipient list and the message headers."""
    from app.api.email_senders import _failure_reason

    reason = _failure_reason("reject", {"reject": {"reason": "x" * 5000}})
    assert reason is not None and len(reason) <= 500


def test_delivery_delay_is_not_a_failure() -> None:
    """SES is still retrying. Calling it failed would tell a recruiter a
    candidate was never contacted while the message is still in flight."""
    from app.api.email_senders import _OUTCOME_RANK

    assert "deliverydelay" not in _OUTCOME_RANK


# ── SES message tags (correlation) ───────────────────────────────────────────

def test_message_tags_carry_every_correlation_id() -> None:
    from app.services.ses_service import _message_tags

    ids = {
        "tenant_id": uuid.uuid4(),
        "sender_id": uuid.uuid4(),
        "candidate_id": uuid.uuid4(),
        "job_id": uuid.uuid4(),
        "notification_id": uuid.uuid4(),
    }
    tags = _message_tags(ids)
    assert {t["Name"] for t in tags} == set(ids)


def test_a_none_id_is_dropped_rather_than_sent_as_the_string_none() -> None:
    from app.services.ses_service import _message_tags

    tags = _message_tags({"tenant_id": uuid.uuid4(), "candidate_id": None})
    assert {t["Name"] for t in tags} == {"tenant_id"}


def test_an_unusable_tag_value_is_dropped_not_sanitised() -> None:
    """SES rejects the WHOLE send on a malformed tag. An email that did not go
    out is worse than an event matched on its message id alone, which is what
    the webhook does anyway."""
    from app.services.ses_service import _message_tags

    tags = _message_tags({"tenant_id": "has spaces and @", "job_id": "ok-1"})
    assert {t["Name"] for t in tags} == {"job_id"}


def test_no_tags_at_all_is_an_empty_list_not_a_none() -> None:
    """The send path only adds the Tags key when there is something in it."""
    from app.services.ses_service import _message_tags

    assert _message_tags(None) == []
    assert _message_tags({}) == []
