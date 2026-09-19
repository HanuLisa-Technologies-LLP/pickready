"""The background-verification rules that decide whether somebody gets an offer.

Three things are asserted here and each is a different KIND of guarantee:

  * the DERIVATION is pure arithmetic over counts, so its whole truth table is
    tested without a database and its determinism is asserted by AST;
  * the IMMUTABILITY is a database trigger, so it is tested by asking the
    database to break it;
  * the OFFER GATE lives at `apply_transition`, so it is tested through that
    function rather than through a route, because a route test would prove only
    that one of six callers behaves.
"""
from __future__ import annotations

import ast
import pathlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.models.bgv_verification import (
    CANDIDATE_BGV_NOT_REQUIRED,
    CANDIDATE_BGV_NOT_STARTED,
    CANDIDATE_BGV_NOT_VERIFIED,
    CANDIDATE_BGV_PENDING,
    CANDIDATE_BGV_VERIFIED,
    VERIFICATION_NOT_VERIFIED,
    VERIFICATION_PENDING,
    VERIFICATION_VERIFIED,
)
from app.models.employment import BACKGROUND_EXPERIENCED, BACKGROUND_FRESHER
from app.services import bgv_workflow, hiring_pipeline

BACKEND = pathlib.Path(__file__).resolve().parents[1]


# ── The derivation, as a truth table ─────────────────────────────────────────


def test_a_fresher_never_needs_verification() -> None:
    """The headline rule. Nothing about employers can change it."""
    assert (
        bgv_workflow.derive_status(
            background=BACKGROUND_FRESHER, employer_count=0, statuses=[]
        )
        == CANDIDATE_BGV_NOT_REQUIRED
    )


def test_an_unanswered_question_does_not_block_anybody() -> None:
    """NULL is not `experienced`.

    A profile field nobody filled in must not end a candidacy: the product
    would be deciding something no person asked it to decide.
    """
    assert (
        bgv_workflow.derive_status(background=None, employer_count=0, statuses=[])
        == CANDIDATE_BGV_NOT_REQUIRED
    )


def test_an_experienced_candidate_with_no_requests_sent_is_not_started() -> None:
    assert (
        bgv_workflow.derive_status(
            background=BACKGROUND_EXPERIENCED, employer_count=3, statuses=[]
        )
        == CANDIDATE_BGV_NOT_STARTED
    )


def test_one_outstanding_employer_holds_the_whole_candidate() -> None:
    """The rule the brief states twice, and the one a naive count gets wrong."""
    assert (
        bgv_workflow.derive_status(
            background=BACKGROUND_EXPERIENCED,
            employer_count=3,
            statuses=[
                VERIFICATION_VERIFIED,
                VERIFICATION_VERIFIED,
                VERIFICATION_PENDING,
            ],
        )
        == CANDIDATE_BGV_PENDING
    )


def test_every_employer_verified_is_the_only_route_to_verified() -> None:
    assert (
        bgv_workflow.derive_status(
            background=BACKGROUND_EXPERIENCED,
            employer_count=3,
            statuses=[VERIFICATION_VERIFIED] * 3,
        )
        == CANDIDATE_BGV_VERIFIED
    )


def test_seven_employers_are_supported_and_all_seven_must_land() -> None:
    """The brief's own worked example."""
    six_of_seven = [VERIFICATION_VERIFIED] * 6 + [VERIFICATION_PENDING]
    assert (
        bgv_workflow.derive_status(
            background=BACKGROUND_EXPERIENCED, employer_count=7, statuses=six_of_seven
        )
        == CANDIDATE_BGV_PENDING
    )
    assert (
        bgv_workflow.derive_status(
            background=BACKGROUND_EXPERIENCED,
            employer_count=7,
            statuses=[VERIFICATION_VERIFIED] * 7,
        )
        == CANDIDATE_BGV_VERIFIED
    )


def test_a_partial_set_of_requests_never_reads_as_complete() -> None:
    """Three employers verified out of seven is not verified.

    Counting the verification rows that EXIST rather than the employers the
    candidate SUBMITTED is the specific arithmetic error this guards: it would
    report a candidate fully verified while four of their jobs had never been
    asked about.
    """
    assert (
        bgv_workflow.derive_status(
            background=BACKGROUND_EXPERIENCED,
            employer_count=7,
            statuses=[VERIFICATION_VERIFIED] * 3,
        )
        == CANDIDATE_BGV_PENDING
    )


def test_a_negative_finding_outranks_every_confirmation() -> None:
    """Six employers confirming does not outvote one that did not.

    A status that averaged them would hide the single finding the whole
    workflow exists to surface.
    """
    assert (
        bgv_workflow.derive_status(
            background=BACKGROUND_EXPERIENCED,
            employer_count=7,
            statuses=[VERIFICATION_VERIFIED] * 6 + [VERIFICATION_NOT_VERIFIED],
        )
        == CANDIDATE_BGV_NOT_VERIFIED
    )


# ── What each status permits ─────────────────────────────────────────────────


def test_only_not_required_and_verified_open_the_offer() -> None:
    allowed = {
        status
        for status in (
            CANDIDATE_BGV_NOT_REQUIRED,
            CANDIDATE_BGV_NOT_STARTED,
            CANDIDATE_BGV_PENDING,
            CANDIDATE_BGV_VERIFIED,
            CANDIDATE_BGV_NOT_VERIFIED,
        )
        if bgv_workflow.blocking_message(status) is None
    }
    assert allowed == {CANDIDATE_BGV_NOT_REQUIRED, CANDIDATE_BGV_VERIFIED}


def test_every_refusal_names_the_next_action() -> None:
    """A gate that says "blocked" and stops has moved the work to the person
    least able to see why."""
    for status in (
        CANDIDATE_BGV_NOT_STARTED,
        CANDIDATE_BGV_PENDING,
        CANDIDATE_BGV_NOT_VERIFIED,
    ):
        message = bgv_workflow.blocking_message(status)
        assert message and len(message) > 60, status


# ── Determinism, structurally ────────────────────────────────────────────────


def test_the_workflow_calls_no_model() -> None:
    """An LLM that could answer "is this candidate verified" is an LLM that can
    authorise an offer. Asserted over the import graph, not in a docstring."""
    tree = ast.parse(
        (BACKEND / "app" / "services" / "bgv_workflow.py").read_text(encoding="utf-8")
    )
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {name for name in imported if "llm" in name or "agent" in name}
    assert forbidden == set(), forbidden


def test_the_offer_gate_lives_at_the_chokepoint() -> None:
    """Read out of the source: the refusal has to happen in `apply_transition`,
    which every one of its six callers reaches, and BEFORE the first write."""
    source = (BACKEND / "app" / "services" / "hiring_pipeline.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "apply_transition"
    )
    gate_line = min(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute) and node.attr == "offer_blocked"
    )
    first_write_line = min(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "UPDATE job_candidate_links" in node.value
    )
    assert gate_line < first_write_line, (
        "the BGV gate runs after the status has already been written, which "
        "blocks the email while recording the promotion"
    )


def test_rejection_is_not_gated() -> None:
    """A rejected candidate does not need verification finished first.

    Holding a candidacy open until previous employers reply, for a decision
    that has already been made, would be the gate working against the thing it
    exists to protect.
    """
    assert hiring_pipeline.REJECTED not in hiring_pipeline.OFFER_STAGES
    assert hiring_pipeline.SHORTLISTED not in hiring_pipeline.OFFER_STAGES
    assert hiring_pipeline.OFFER_EXTENDED in hiring_pipeline.OFFER_STAGES
    # The legacy synonym too: a gate that knew only the canonical name would be
    # a gate with a documented way around it.
    assert hiring_pipeline.OFFERED in hiring_pipeline.OFFER_STAGES


# ── The database's own guarantees ────────────────────────────────────────────


async def _factory_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- any connect failure means "no DB here"
        await engine.dispose()
        pytest.skip("no database reachable -- skipping BGV database tests")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _candidate(session, background: str | None = None) -> uuid.UUID:
    candidate_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO candidates (id, full_name, email, consent_databank, "
            "employment_background, created_at) "
            "VALUES (:id, 'BGV Tester', :email, false, :bg, now())"
        ),
        {
            "id": candidate_id,
            "email": f"bgv-{candidate_id.hex[:10]}@example.com",
            "bg": background,
        },
    )
    return candidate_id


async def _employment(session, candidate_id: uuid.UUID, name: str) -> uuid.UUID:
    employment_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO candidate_employments (id, candidate_id, employer_name, "
            "designation, started_on, ended_on, hr_name, hr_email) "
            "VALUES (:id, :cid, :name, 'Engineer', DATE '2019-01-01', "
            "DATE '2021-06-30', 'Asha R', :email)"
        ),
        {
            "id": employment_id,
            "cid": candidate_id,
            "name": name,
            "email": f"hr@{name.lower().replace(' ', '')}.example",
        },
    )
    return employment_id


@pytest.mark.asyncio
async def test_submitted_history_cannot_be_edited_even_by_raw_sql() -> None:
    """The immutability the brief asks for, proven where it has to hold.

    The service layer refuses first with an explanation. This asserts the OTHER
    half: that a future route, a data fix or a psql session is refused too.
    """
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    candidate_id = await _candidate(session, BACKGROUND_EXPERIENCED)
                    await _employment(session, candidate_id, "Company A")

                    # Before submission the candidate may still correct a typo.
                    await session.execute(
                        text(
                            "UPDATE candidate_employments SET designation = 'Dev II' "
                            "WHERE candidate_id = :cid"
                        ),
                        {"cid": candidate_id},
                    )

                    await session.execute(
                        text(
                            "UPDATE candidates SET employment_history_finalized_at = "
                            "now() WHERE id = :cid"
                        ),
                        {"cid": candidate_id},
                    )
                    with pytest.raises(Exception) as raised:
                        await session.execute(
                            text(
                                "UPDATE candidate_employments SET employer_name = "
                                "'Somewhere Else' WHERE candidate_id = :cid"
                            ),
                            {"cid": candidate_id},
                        )
                    assert "final" in str(raised.value).lower()
                await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_candidate_status_reads_only_this_tenants_verifications() -> None:
    """Tenant isolation stated as arithmetic: one customer's diligence must not
    clear another customer's hire."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
                    for tid, name in ((tenant_a, "BGV-A"), (tenant_b, "BGV-B")):
                        await session.execute(
                            text(
                                "INSERT INTO tenants (id, name, domain, "
                                "spf_dkim_status) VALUES (:id, :n, :d, 'pending')"
                            ),
                            {"id": tid, "n": name, "d": f"{tid.hex[:8]}.example.com"},
                        )
                    candidate_id = await _candidate(session, BACKGROUND_EXPERIENCED)
                    employment_id = await _employment(session, candidate_id, "Company A")

                    # Tenant A verified it. Tenant B has done nothing at all.
                    user_id = uuid.uuid4()
                    await session.execute(
                        text(
                            "INSERT INTO users (id, tenant_id, role, email, status) "
                            "VALUES (:id, :tid, 'recruiter', :email, 'active')"
                        ),
                        {
                            "id": user_id,
                            "tid": tenant_a,
                            "email": f"r-{user_id.hex[:8]}@a.example",
                        },
                    )
                    await session.execute(
                        text(
                            "INSERT INTO bgv_verifications (id, tenant_id, "
                            "candidate_id, candidate_employment_id, status, "
                            "decided_by, decided_at) VALUES (gen_random_uuid(), "
                            ":tid, :cid, :eid, 'verified', :uid, now())"
                        ),
                        {
                            "tid": tenant_a,
                            "cid": candidate_id,
                            "eid": employment_id,
                            "uid": user_id,
                        },
                    )

                    assert (
                        await bgv_workflow.candidate_status(
                            session, tenant_id=tenant_a, candidate_id=candidate_id
                        )
                        == CANDIDATE_BGV_VERIFIED
                    )
                    assert (
                        await bgv_workflow.candidate_status(
                            session, tenant_id=tenant_b, candidate_id=candidate_id
                        )
                        == CANDIDATE_BGV_NOT_STARTED
                    )
                await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_decided_verification_must_name_its_decider() -> None:
    """The database refuses a decision nobody made, so the audit question
    "who cleared this employer" always has an answer."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    tenant_id = uuid.uuid4()
                    await session.execute(
                        text(
                            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                            "VALUES (:id, 'BGV-D', :d, 'pending')"
                        ),
                        {"id": tenant_id, "d": f"{tenant_id.hex[:8]}.example.com"},
                    )
                    candidate_id = await _candidate(session, BACKGROUND_EXPERIENCED)
                    employment_id = await _employment(session, candidate_id, "Company A")
                    with pytest.raises(Exception) as raised:
                        await session.execute(
                            text(
                                "INSERT INTO bgv_verifications (id, tenant_id, "
                                "candidate_id, candidate_employment_id, status) "
                                "VALUES (gen_random_uuid(), :tid, :cid, :eid, "
                                "'verified')"
                            ),
                            {
                                "tid": tenant_id,
                                "cid": candidate_id,
                                "eid": employment_id,
                            },
                        )
                    assert "ck_bgv_verifications_decided" in str(raised.value)
                await session.rollback()
    finally:
        await engine.dispose()
