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
    SENDER_REVOKED,
    SENDER_STATUSES,
    SENDER_VERIFICATION_EXPIRED,
)
from app.services import email_templates
from app.services.email_senders import (
    IllegalSenderTransition,
    ResendCooldownActive,
    SenderDomainBlocked,
    SenderEmailInvalid,
    assert_transition,
    issue_otp,
    validate_business_email,
    verify_otp,
)
from app.services.email_senders import lifecycle, verification

EM_DASH = chr(8212)


# ── The lifecycle FSM ────────────────────────────────────────────────────────

def test_the_happy_path_is_a_legal_chain() -> None:
    """pending -> verified -> active -> disabled -> active -> revoked."""
    chain = [
        SENDER_PENDING_VERIFICATION,
        SENDER_EMAIL_VERIFIED,
        SENDER_ACTIVE,
        SENDER_DISABLED,
        SENDER_ACTIVE,
        SENDER_REVOKED,
    ]
    for current, target in zip(chain, chain[1:]):
        assert_transition(current, target)  # must not raise


def test_revoked_is_terminal() -> None:
    for target in SENDER_STATUSES:
        with pytest.raises(IllegalSenderTransition):
            assert_transition(SENDER_REVOKED, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        # Activation without verification is the spec section 10 violation.
        (SENDER_PENDING_VERIFICATION, SENDER_ACTIVE),
        # An expired verification cannot jump straight to verified or active.
        (SENDER_VERIFICATION_EXPIRED, SENDER_EMAIL_VERIFIED),
        (SENDER_VERIFICATION_EXPIRED, SENDER_ACTIVE),
        # Disable is only meaningful for an active sender.
        (SENDER_PENDING_VERIFICATION, SENDER_DISABLED),
        (SENDER_EMAIL_VERIFIED, SENDER_DISABLED),
        # Nothing un-verifies a mailbox.
        (SENDER_ACTIVE, SENDER_PENDING_VERIFICATION),
        (SENDER_DISABLED, SENDER_EMAIL_VERIFIED),
    ],
)
def test_illegal_moves_are_refused(current: str, target: str) -> None:
    with pytest.raises(IllegalSenderTransition):
        assert_transition(current, target)


def test_the_fsm_covers_the_whole_status_vocabulary() -> None:
    """A status the CHECK constraint accepts but the FSM does not know would
    make every action on it a KeyError."""
    assert set(lifecycle.TRANSITIONS) == set(SENDER_STATUSES)


def test_verification_can_be_rearmed_after_expiry() -> None:
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


# ── The OTP (Redis-backed; spec section 4) ───────────────────────────────────

@pytest.mark.asyncio
async def test_only_the_hash_lives_in_redis_under_the_spec_key() -> None:
    sender_id = uuid.uuid4()
    code = await issue_otp(sender_id)
    assert re.fullmatch(r"[0-9]{6}", code)

    client = verification._redis()
    state = await client.hgetall(f"email_verification:{sender_id}")
    assert set(state) == {"otp_hash", "expires_at", "attempts"}
    # The plaintext appears in NO stored field, and the hash is a full
    # HMAC-SHA256 digest, not a truncated or reversible encoding.
    assert code not in "".join(state.values())
    assert re.fullmatch(r"[0-9a-f]{64}", state["otp_hash"])
    assert state["attempts"] == "0"
    # The state expires on its own even if nobody ever enters a code.
    assert await client.ttl(f"email_verification:{sender_id}") > 0


@pytest.mark.asyncio
async def test_a_resend_inside_the_cooldown_is_refused_with_the_wait() -> None:
    sender_id = uuid.uuid4()
    await issue_otp(sender_id)
    with pytest.raises(ResendCooldownActive) as excinfo:
        await issue_otp(sender_id)
    assert 1 <= excinfo.value.retry_after <= get_settings().sender_otp_resend_cooldown_seconds


@pytest.mark.asyncio
async def test_three_wrong_attempts_kill_the_code_even_for_the_right_one() -> None:
    sender_id = uuid.uuid4()
    code = await issue_otp(sender_id)
    wrong = "000000" if code != "000000" else "999999"

    first = await verify_otp(sender_id, wrong)
    assert (first.verified, first.reason) == (False, verification.REASON_MISMATCH)
    assert first.attempts_remaining == 2
    second = await verify_otp(sender_id, wrong)
    assert second.attempts_remaining == 1
    third = await verify_otp(sender_id, wrong)
    assert third.reason == verification.REASON_ATTEMPTS_EXHAUSTED
    # Attempt four, with the CORRECT code: the budget is spent, the code is dead.
    fourth = await verify_otp(sender_id, code)
    assert fourth.verified is False
    assert fourth.reason == verification.REASON_ATTEMPTS_EXHAUSTED


@pytest.mark.asyncio
async def test_success_invalidates_the_code_so_it_cannot_be_replayed() -> None:
    sender_id = uuid.uuid4()
    code = await issue_otp(sender_id)
    outcome = await verify_otp(sender_id, code)
    assert (outcome.verified, outcome.reason) == (True, verification.REASON_VERIFIED)
    replay = await verify_otp(sender_id, code)
    assert (replay.verified, replay.reason) == (False, verification.REASON_NOT_ISSUED)


@pytest.mark.asyncio
async def test_an_expired_code_reads_as_expired_and_is_removed() -> None:
    sender_id = uuid.uuid4()
    code = await issue_otp(sender_id)
    client = verification._redis()
    # Force the stored deadline into the past; the TTL alone is not the check.
    await client.hset(f"email_verification:{sender_id}", "expires_at", "1")
    outcome = await verify_otp(sender_id, code)
    assert (outcome.verified, outcome.reason) == (False, verification.REASON_EXPIRED)
    # The expired state is gone, so it can never be retried into life.
    assert await client.hgetall(f"email_verification:{sender_id}") == {}


@pytest.mark.asyncio
async def test_verifying_with_nothing_issued_is_not_an_error_it_is_an_answer() -> None:
    outcome = await verify_otp(uuid.uuid4(), "123456")
    assert (outcome.verified, outcome.reason) == (False, verification.REASON_NOT_ISSUED)


@pytest.mark.asyncio
async def test_a_code_issued_for_one_sender_never_verifies_another() -> None:
    """The sender id is inside the HMAC message, so cross-sender replay is a
    mismatch, not a verification."""
    a, b = uuid.uuid4(), uuid.uuid4()
    code_a = await issue_otp(a)
    await issue_otp(b)
    outcome = await verify_otp(b, code_a)
    assert outcome.verified is False


def test_the_ttl_sits_inside_the_specs_window_and_attempts_are_three() -> None:
    settings = get_settings()
    assert 300 <= settings.sender_otp_ttl_seconds <= 600
    assert settings.sender_otp_max_attempts == 3
    assert settings.sender_otp_resend_cooldown_seconds == 45


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

    for handler in ("async def authorize_sender", "async def disable_sender",
                    "async def enable_sender", "async def revoke_sender"):
        assert "require_capability(caps.AUTHORIZE_EMAIL_SENDERS)" in _handler_block(handler), handler
    for handler in ("async def list_senders", "async def create_sender",
                    "async def resend_otp", "async def verify_sender_otp"):
        assert "require_capability(caps.MANAGE_EMAIL_SENDERS)" in _handler_block(handler), handler


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


def test_the_sender_verification_email_carries_no_em_dash_and_the_code_hole() -> None:
    """The OTP email itself (services/email_render): the code travels only as
    a template variable, and the copy is em-dash free like everything else."""
    from app.services.email_render import DEFAULT_TEMPLATES

    subject, body = DEFAULT_TEMPLATES["sender_verification"]
    assert EM_DASH not in subject and EM_DASH not in body
    assert "{{otp_code}}" in body
