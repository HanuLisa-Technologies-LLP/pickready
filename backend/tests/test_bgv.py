"""Background verification via departmental HR email (add-features spec
2026-09-05, "Candidate Verification").

What has to hold, and what this module pins:

  * the domain-versus-employer-name comparison is deterministic PROVENANCE:
    a word, honest about degenerate input, and never a gate;
  * a free/personal mail provider is refused at intake, subdomains included,
    from the one `sender_domain_blocklist` setting the corporate-sender
    registration also enforces;
  * a failed extraction is reported as a failure, never dressed up as a
    result (seven nulls is not an extraction);
  * an employer tenant sees a result ONLY where the candidate wrote a
    share-consent row for that tenant, in both directions, and a tenant the
    candidate never applied to gets a 404 that does not confirm existence.

Schema and pure-function tests run anywhere; the handler tests need a
database and SKIP cleanly without one (same convention as
test_profile_crud.py).
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import pytest

from app.models import bgv as bgv_model
from app.services import bgv as bgv_service


# ── The domain matcher: deterministic, words only, never a gate ──────────────

@pytest.mark.parametrize(
    "employer,email,expected",
    [
        # The plain case: the registrable label IS a name token.
        ("Infosys Limited", "hr@infosys.com", "matched"),
        # Joined multi-word name.
        ("Infosys BPM", "careers@infosysbpm.com", "matched"),
        # Acronym: Tata Consultancy Services -> tcs.
        ("Tata Consultancy Services", "hr@tcs.com", "matched"),
        # Two-part public suffix: the registrable label of hr.infosys.co.in
        # is "infosys", not "co".
        ("Infosys", "resumes@hr.infosys.co.in", "matched"),
        # A distinctive name token inside a longer label.
        ("Globex Analytics", "hr@globexindia.com", "matched"),
        # A genuinely different company.
        ("Acme Software", "hr@globex.com", "mismatched"),
        # Degenerate input is INDETERMINATE, an honest answer rather than a
        # soft pass in either direction.
        ("Acme Software", "not-an-email", "indeterminate"),
        ("", "hr@acme.com", "indeterminate"),
        # A name made entirely of legal-suffix glue has nothing to compare.
        ("Co Ltd", "hr@co.com", "indeterminate"),
    ],
)
def test_domain_match_cases(employer: str, email: str, expected: str) -> None:
    assert bgv_service.domain_match(employer, email) == expected


def test_domain_match_only_speaks_the_three_words() -> None:
    """The vocabulary is held by the model constant AND the database CHECK;
    the function must never invent a fourth word."""
    samples = [
        ("Infosys", "hr@infosys.com"),
        ("Acme", "hr@globex.com"),
        ("", "nonsense"),
    ]
    for employer, email in samples:
        assert (
            bgv_service.domain_match(employer, email)
            in bgv_model.DOMAIN_MATCH_RESULTS
        )


def test_short_glue_tokens_cannot_manufacture_a_match() -> None:
    """"hr" or "co" appearing inside a label must not count as overlap."""
    assert bgv_service.domain_match("HR Co", "contact@hrco-partners.com") in {
        "mismatched",
        "indeterminate",
    }


# ── Free-provider refusal ────────────────────────────────────────────────────

def test_free_provider_domains_are_refused() -> None:
    assert bgv_service.is_free_provider_domain("hr@gmail.com")
    assert bgv_service.is_free_provider_domain("careers@yahoo.com")
    assert bgv_service.is_free_provider_domain("resumes@outlook.com")


def test_free_provider_subdomains_are_refused_too() -> None:
    """mail.yahoo.com must not slip past the check that refuses yahoo.com."""
    assert bgv_service.is_free_provider_domain("hr@mail.yahoo.com")


def test_corporate_domains_pass_the_provider_check() -> None:
    assert not bgv_service.is_free_provider_domain("hr@infosys.com")
    assert not bgv_service.is_free_provider_domain("careers@acme.co.in")


def test_an_unparseable_address_is_not_labelled_free_provider() -> None:
    """It is refused elsewhere as not-an-address; this check must not lie
    about WHY."""
    assert not bgv_service.is_free_provider_domain("not-an-email")


def test_the_blocklist_is_the_shared_sender_setting() -> None:
    """One list for one concept: this module must read the corporate-sender
    blocklist, not carry a second copy that would drift."""
    from app.core.config import get_settings

    assert bgv_service.blocked_email_domains() == (
        get_settings().sender_blocked_domains()
    )


# ── Parse-failure honesty ────────────────────────────────────────────────────

def test_validator_refuses_a_non_object_payload() -> None:
    with pytest.raises(bgv_service.BGVParseError):
        bgv_service.validate_parsed_fields(["duration", "noc"])


def test_validator_refuses_an_all_null_extraction() -> None:
    """Seven nulls is a FAILED extraction, not a result."""
    with pytest.raises(bgv_service.BGVParseError):
        bgv_service.validate_parsed_fields(
            {field: None for field in bgv_model.BGV_FIELDS}
        )


def test_validator_normalises_to_exactly_the_seven_keys() -> None:
    out = bgv_service.validate_parsed_fields(
        {
            "duration": "Jun 2021 to Mar 2024",
            "exit_formalities": True,
            "surprise_key": "dropped",
            "noc": ["a", "list"],  # structure is not a field value
        }
    )
    assert set(out) == set(bgv_model.BGV_FIELDS)
    assert out["duration"] == "Jun 2021 to Mar 2024"
    assert out["exit_formalities"] is True
    assert out["noc"] is None
    assert "surprise_key" not in out


def test_validator_bounds_field_length() -> None:
    out = bgv_service.validate_parsed_fields({"designation": "x" * 5000})
    assert len(out["designation"]) == 500


async def test_parse_reply_raises_on_empty_input() -> None:
    with pytest.raises(bgv_service.BGVParseError):
        await bgv_service.parse_reply("   ")


async def test_parse_reply_raises_on_non_json_output(monkeypatch) -> None:
    from app.services import llm_router

    async def fake(*_args, **_kwargs):
        return "I am sorry, I cannot help with that."

    monkeypatch.setattr(llm_router, "chat_completion", fake)
    with pytest.raises(bgv_service.BGVParseError):
        await bgv_service.parse_reply("On record: the employee worked here.")


async def test_parse_reply_raises_on_a_json_array(monkeypatch) -> None:
    """`response_format` permits any JSON value; an array parses and then
    fails on the first subscript, so the validator must refuse it here."""
    from app.services import llm_router

    async def fake(*_args, **_kwargs):
        return '["duration", "noc"]'

    monkeypatch.setattr(llm_router, "chat_completion", fake)
    with pytest.raises(bgv_service.BGVParseError):
        await bgv_service.parse_reply("Some reply text.")


async def test_parse_reply_returns_the_validated_fields(monkeypatch) -> None:
    from app.services import llm_router

    async def fake(*_args, **_kwargs):
        return (
            '{"duration": "2019 to 2023", "designation": "Analyst",'
            ' "reporting_manager": null, "compensation": null,'
            ' "exit_formalities": true, "noc": "Issued",'
            ' "relieving_method": "Resignation"}'
        )

    monkeypatch.setattr(llm_router, "chat_completion", fake)
    out = await bgv_service.parse_reply("A perfectly good reply.")
    assert out["designation"] == "Analyst"
    assert out["exit_formalities"] is True
    assert out["reporting_manager"] is None


# ── Status vocabulary and transitions ────────────────────────────────────────

def test_only_collected_and_dispatch_failed_are_dispatchable() -> None:
    """A second email to an HR mailbox that already received one reads as
    spam; only a never-sent or visibly-failed inquiry may (re)send."""
    assert bgv_model.DISPATCHABLE_STATUSES == frozenset(
        {bgv_model.STATUS_COLLECTED, bgv_model.STATUS_DISPATCH_FAILED}
    )


def test_the_model_and_the_migration_agree_on_the_status_vocabulary() -> None:
    """An enum missing a value the column accepts 500s every read; a CHECK
    missing a value the code writes refuses the write. Compare the two."""
    from pathlib import Path

    migration = Path(__file__).resolve().parents[1] / (
        "alembic/versions/0085_bgv_inquiries.py"
    )
    source = migration.read_text(encoding="utf-8")
    check = re.search(
        r"status IN \(([^)]+)\)", source.replace("\"\n            \"", "")
    )
    assert check is not None
    in_check = set(re.findall(r"'([a-z_]+)'", check.group(1)))
    assert in_check == set(bgv_model.ALL_STATUSES)


def test_the_seven_fields_are_the_spec_fields() -> None:
    assert set(bgv_model.BGV_FIELDS) == {
        "duration",
        "exit_formalities",
        "compensation",
        "noc",
        "designation",
        "reporting_manager",
        "relieving_method",
    }


# ── The inquiry email template ───────────────────────────────────────────────

def test_the_inquiry_template_asks_for_all_seven_facts() -> None:
    from app.services.email_render import DEFAULT_TEMPLATES

    subject, body = DEFAULT_TEMPLATES["bgv_inquiry"]
    lowered = body.lower()
    for phrase in (
        "duration",
        "designation",
        "reporting manager",
        "compensation",
        "exit formalities",
        "no-objection certificate",
        "relieving",
    ):
        assert phrase in lowered, phrase
    assert "Reference: BGV-{{reply_token}}" in body
    assert chr(8212) not in subject and chr(8212) not in body


def test_the_webhook_regex_matches_a_real_reply_token() -> None:
    """The webhook's matcher and the template's reference line must agree,
    including for a token at the generator's actual length."""
    import secrets

    from app.api.verification import _BGV_TOKEN_IN_BODY

    token = secrets.token_urlsafe(24)
    reply = f"Confirmed as requested.\n\n> Reference: BGV-{token}\n> sent earlier"
    match = _BGV_TOKEN_IN_BODY.search(reply)
    assert match is not None
    assert match.group(1) == token


def test_no_em_dash_in_the_bgv_surfaces() -> None:
    """Swept over the module sources rather than one string, because a rule
    enforced at one call site is a rule the next entry breaks. The dash is
    built from chr(8212) so a repo-wide sweep cannot rewrite this test."""
    import inspect
    from pathlib import Path

    from app.api import verification as verification_api

    dash = chr(8212)
    for source in (
        inspect.getsource(bgv_model),
        inspect.getsource(bgv_service),
        # The BGV addition to the shared webhook module; the module's older
        # non-BGV docstrings predate the rule and are not this feature's to
        # rewrite.
        inspect.getsource(verification_api._match_bgv_reply),
        (
            Path(__file__).resolve().parents[1]
            / "app/prompts/bgv_reply_extraction_system.txt"
        ).read_text(encoding="utf-8"),
    ):
        assert dash not in source


# ── Handler tests: share gating and the in-tenant 404 gate ───────────────────

async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- no DB reachable
        await engine.dispose()
        pytest.skip("no database reachable, skipping BGV handler tests")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _World:
    """Two tenants with applications from one candidate, one consented; a
    third tenant with no application at all."""

    def __init__(self) -> None:
        self.tenant_a = uuid.uuid4()
        self.tenant_b = uuid.uuid4()
        self.tenant_c = uuid.uuid4()
        self.job_a = uuid.uuid4()
        self.job_b = uuid.uuid4()
        self.cand_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.inquiry_id = uuid.uuid4()
        self.email = f"bgv-{uuid.uuid4().hex[:8]}@candidates.pickready.test"


async def _build_world(factory, w: _World) -> None:
    from app.core.db import superadmin_scope
    from app.models import Candidate, Role, Tenant, User
    from app.models.candidate import JobCandidateLink
    from app.models.bgv import BGVInquiry, BGVShareConsent
    from app.models.enums import JobStatus, LinkSource, UserStatus
    from app.models.job import Job

    now = datetime.now(timezone.utc)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                for tenant_id, tag in (
                    (w.tenant_a, "A"),
                    (w.tenant_b, "B"),
                    (w.tenant_c, "C"),
                ):
                    s.add(Tenant(id=tenant_id, name=f"BGV Tenant {tag} {tenant_id.hex[:6]}",
                                 domain=f"{tenant_id.hex[:10]}.bgv.test"))
                await s.flush()
                s.add(User(id=w.user_id, email=w.email, role=Role.candidate,
                           tenant_id=None, full_name="BGV Candidate",
                           status=UserStatus.active))
                await s.flush()
                s.add(Candidate(id=w.cand_id, email=w.email, user_id=w.user_id,
                                full_name="BGV Candidate", consent_databank=False))
                for job_id, tenant_id in ((w.job_a, w.tenant_a), (w.job_b, w.tenant_b)):
                    s.add(Job(id=job_id, tenant_id=tenant_id, title="Engineer",
                              jd_json={}, status=JobStatus.ratified, ratified_at=now,
                              assessment_grade="non_managerial"))
                await s.flush()
                for job_id, tenant_id in ((w.job_a, w.tenant_a), (w.job_b, w.tenant_b)):
                    s.add(JobCandidateLink(id=uuid.uuid4(), tenant_id=tenant_id,
                                           job_id=job_id, candidate_id=w.cand_id,
                                           source=LinkSource.fresh, status="applied"))
                s.add(BGVInquiry(
                    id=w.inquiry_id, candidate_id=w.cand_id,
                    employer_name="Prior Employer Pvt Ltd",
                    departmental_email="hr@prioremployer.com",
                    domain_match_result="matched",
                    status=bgv_model.STATUS_PARSED,
                    reply_token=uuid.uuid4().hex,
                    inquiry_sent_at=now, response_received_at=now,
                    response_raw="the reply, verbatim",
                    parsed_fields_json={
                        "duration": "2019 to 2023",
                        "exit_formalities": True,
                        "compensation": None,
                        "noc": "Issued",
                        "designation": "Analyst",
                        "reporting_manager": None,
                        "relieving_method": "Resignation",
                    },
                ))
                await s.flush()
                # Shared with tenant A only.
                s.add(BGVShareConsent(id=uuid.uuid4(), bgv_inquiry_id=w.inquiry_id,
                                      tenant_id=w.tenant_a, consented_at=now))


async def _cleanup_world(factory, w: _World) -> None:
    from sqlalchemy import text

    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(text("DELETE FROM candidates WHERE id = :c"),
                                {"c": str(w.cand_id)})
                await s.execute(text("DELETE FROM users WHERE id = :u"),
                                {"u": str(w.user_id)})
                for tenant_id in (w.tenant_a, w.tenant_b, w.tenant_c):
                    await s.execute(text("DELETE FROM tenants WHERE id = :t"),
                                    {"t": str(tenant_id)})


def _org_user(tenant_id: uuid.UUID):
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_ORG
    from app.models import Role

    return CurrentUser(user_id=uuid.uuid4(), tenant_id=tenant_id,
                       role=Role.recruiter, audience=AUDIENCE_ORG)


def _candidate_user(w: _World):
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_CANDIDATE
    from app.models import Role

    return CurrentUser(user_id=w.user_id, tenant_id=None, role=Role.candidate,
                       audience=AUDIENCE_CANDIDATE)


@pytest.fixture
def _full_capability(monkeypatch):
    """The capability engine is not under test here (test_rbac_conformance
    covers it); the consent filter and the in-tenant 404 gate are."""
    from app.api import candidates as candidates_api

    async def _has_capability(*_args, **_kwargs):
        return True

    monkeypatch.setattr(candidates_api.rbac, "has_capability", _has_capability)


async def test_share_gating_both_directions_and_the_404_gate(
    _full_capability,
) -> None:
    from fastapi import HTTPException

    from app.api import candidates as candidates_api
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.schemas.portal import BGVConsentIn

    engine, factory = await _factory_or_skip()
    w = _World()
    try:
        await _build_world(factory, w)

        # Consented tenant: the parsed fields are visible, the raw reply is
        # not serialized anywhere.
        async with factory() as s:
            async with superadmin_scope(s):
                out = await candidates_api.get_bgv_results(
                    w.cand_id, user=_org_user(w.tenant_a), session=s
                )
        assert out["inquiries"][0]["shared"] is True
        assert out["inquiries"][0]["employer_name"] == "Prior Employer Pvt Ltd"
        assert out["inquiries"][0]["parsed_fields"]["designation"] == "Analyst"
        assert "response_raw" not in str(out)
        assert "the reply, verbatim" not in str(out)

        # Linked but unconsented tenant: the unshared marker carries NO
        # employer name, mailbox or fields.
        async with factory() as s:
            async with superadmin_scope(s):
                out_b = await candidates_api.get_bgv_results(
                    w.cand_id, user=_org_user(w.tenant_b), session=s
                )
        assert out_b["inquiries"] == [
            {"shared": False, "note": "Not shared by the candidate"}
        ]

        # A tenant with no application from this candidate: 404, never 403,
        # so a cross-tenant read cannot confirm existence.
        async with factory() as s:
            async with superadmin_scope(s):
                with pytest.raises(HTTPException) as exc:
                    await candidates_api.get_bgv_results(
                        w.cand_id, user=_org_user(w.tenant_c), session=s
                    )
        assert exc.value.status_code == 404

        # The candidate revokes: tenant A drops to the unshared marker on the
        # very next read, because the recruiter surface reads the table live.
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await portal_mod.set_bgv_share_consent(
                        w.inquiry_id,
                        BGVConsentIn(tenant_id=w.tenant_a, granted=False),
                        user=_candidate_user(w),
                        session=s,
                    )
        async with factory() as s:
            async with superadmin_scope(s):
                out_a2 = await candidates_api.get_bgv_results(
                    w.cand_id, user=_org_user(w.tenant_a), session=s
                )
        assert out_a2["inquiries"] == [
            {"shared": False, "note": "Not shared by the candidate"}
        ]

        # And grants tenant B: visible there on the next read.
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await portal_mod.set_bgv_share_consent(
                        w.inquiry_id,
                        BGVConsentIn(tenant_id=w.tenant_b, granted=True),
                        user=_candidate_user(w),
                        session=s,
                    )
        async with factory() as s:
            async with superadmin_scope(s):
                out_b2 = await candidates_api.get_bgv_results(
                    w.cand_id, user=_org_user(w.tenant_b), session=s
                )
        assert out_b2["inquiries"][0]["shared"] is True
    finally:
        await _cleanup_world(factory, w)
        await engine.dispose()


async def test_consent_is_refused_for_a_tenant_without_an_application() -> None:
    """Sharing targets exactly the tenants the candidate has applied to."""
    from fastapi import HTTPException

    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.schemas.portal import BGVConsentIn

    engine, factory = await _factory_or_skip()
    w = _World()
    try:
        await _build_world(factory, w)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    with pytest.raises(HTTPException) as exc:
                        await portal_mod.set_bgv_share_consent(
                            w.inquiry_id,
                            BGVConsentIn(tenant_id=w.tenant_c, granted=True),
                            user=_candidate_user(w),
                            session=s,
                        )
        assert exc.value.status_code == 422
    finally:
        await _cleanup_world(factory, w)
        await engine.dispose()


async def test_free_provider_mailbox_is_refused_at_the_api() -> None:
    from fastapi import HTTPException

    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.schemas.portal import BGVInquiryCreateIn

    engine, factory = await _factory_or_skip()
    w = _World()
    try:
        await _build_world(factory, w)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    with pytest.raises(HTTPException) as exc:
                        await portal_mod.create_bgv_inquiry(
                            BGVInquiryCreateIn(
                                employer_name="Prior Employer",
                                departmental_email="hr@gmail.com",
                            ),
                            user=_candidate_user(w),
                            session=s,
                        )
        assert exc.value.status_code == 422
    finally:
        await _cleanup_world(factory, w)
        await engine.dispose()


async def test_a_dispatched_inquiry_cannot_be_resent() -> None:
    """Only `collected` and `dispatch_failed` are dispatchable: a duplicate
    inquiry email to an HR mailbox reads as spam."""
    from fastapi import HTTPException

    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope

    engine, factory = await _factory_or_skip()
    w = _World()
    try:
        await _build_world(factory, w)  # the seeded inquiry is `parsed`
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    with pytest.raises(HTTPException) as exc:
                        await portal_mod.dispatch_bgv_inquiry(
                            w.inquiry_id, user=_candidate_user(w), session=s
                        )
        assert exc.value.status_code == 409
    finally:
        await _cleanup_world(factory, w)
        await engine.dispose()
