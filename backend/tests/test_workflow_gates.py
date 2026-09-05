"""The hiring workflow's setup gates, as real checks.

`docs/spec/HIRING_WORKFLOW.md` names eight gates. This module covers the two
that guard JOB SETUP and had no enforcement before:

    Gate 1  Company Hiring Requirements exist before a job can be created
    Gate 2  the experience band spans at most five years

Gates 3 and 4 (the Hiring Manager's SWOT and the finalised matrix, before
publication) are already covered by `test_assessment_setup_gate.py` and by
`api/jobs._publication_blocked`; Gate 5 lives in `test_databank_onboarding.py`,
Gate 8 in `test_job_closure.py`.

Everything here is a unit test over pure validation or a fake session, except
the one case that has to see a real row, which asks the database.
"""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.schemas.jobs import (
    MAX_EXPERIENCE_SPAN_YEARS,
    JDGenerateIn,
    JobCreateIn,
    JobPatchIn,
)
from app.services.hiring import company_requirements


# ── Gate 2: the experience band ──────────────────────────────────────────────

def _create_body(low, high) -> dict:
    return {
        "title": "Backend Engineer",
        "grade": "non_managerial",
        "jd": {},
        "experience_min_years": low,
        "experience_max_years": high,
    }


@pytest.mark.parametrize("low,high", [(0, 5), (2, 7), (3, 8), (5, 10), (15, 20), (4, 4)])
def test_a_band_within_the_ceiling_is_accepted(low, high) -> None:
    """The ceiling is on the SPAN, never on the values.

    15-to-20 is as valid as 0-to-5, which is the property that stops the rule
    from quietly becoming "no senior roles".
    """
    body = JobCreateIn.model_validate(_create_body(low, high))
    assert (
        body.experience_max_years - body.experience_min_years
        <= MAX_EXPERIENCE_SPAN_YEARS
    )


@pytest.mark.parametrize("low,high", [(0, 6), (2, 8), (5, 12), (0, 60)])
def test_a_band_wider_than_the_ceiling_is_refused(low, high) -> None:
    with pytest.raises(ValidationError) as exc:
        JobCreateIn.model_validate(_create_body(low, high))
    message = str(exc.value)
    # The refusal NAMES the span it measured and the ceiling. "Invalid
    # experience range" sends a recruiter back to the form to guess.
    assert str(high - low) in message
    assert str(MAX_EXPERIENCE_SPAN_YEARS) in message


def test_an_inverted_band_is_reported_as_inverted_not_as_a_wide_span() -> None:
    """Ordering matters: min-above-max is a data-entry mistake, not a span.

    `_span_within_ceiling` returns early on an inverted pair precisely so the
    recruiter reads the useful message rather than being told their range is
    minus-seven years wide.
    """
    with pytest.raises(ValidationError) as exc:
        JobCreateIn.model_validate(_create_body(10, 3))
    assert "greater than the maximum" in str(exc.value)


def test_a_half_specified_band_is_still_allowed_on_a_draft() -> None:
    """Both ends absent, or one of them, is a DRAFT, not a violation.

    `JobCreateIn` permits a job saved before the band is decided; only
    `JDGenerateIn` requires both, because the generator writes the sentence.
    """
    assert (
        JobCreateIn.model_validate(_create_body(None, None)).experience_min_years
        is None
    )
    assert (
        JobCreateIn.model_validate(_create_body(3, None)).experience_max_years is None
    )


def test_the_gate_covers_every_route_that_accepts_a_band() -> None:
    """Create, patch and JD generation all inherit the one validator.

    Enforcing it in the mixin rather than per route is what stops a recruiter
    from creating a legal band and then widening it with a PATCH.
    """
    for model in (JobCreateIn, JobPatchIn, JDGenerateIn):
        names = {
            validator.func.__name__
            for validator in model.__pydantic_decorators__.model_validators.values()
        }
        assert "_span_within_ceiling" in names

    with pytest.raises(ValidationError):
        JobPatchIn.model_validate({"experience_min_years": 1, "experience_max_years": 9})
    with pytest.raises(ValidationError):
        JDGenerateIn.model_validate(
            {"title": "SRE", "experience_min_years": 1, "experience_max_years": 9}
        )


# ── Gate 1: Company Hiring Requirements ──────────────────────────────────────

class _FakeResult:
    def __init__(self, row) -> None:
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    """Answers the one query `is_complete` makes."""

    def __init__(self, row) -> None:
        self._row = row

    async def execute(self, *args, **kwargs):
        return _FakeResult(self._row)


@pytest.mark.asyncio
async def test_creation_is_blocked_with_a_named_way_out() -> None:
    blocked = await company_requirements.creation_blocked(
        _FakeSession(None), uuid.uuid4()
    )
    assert blocked == company_requirements.MISSING_MESSAGE
    # The message names the artifact and where to complete it, because
    # "job creation blocked" sends a recruiter to ask a colleague.
    assert "Company Hiring Requirements" in blocked
    assert "Company DNA" in blocked


@pytest.mark.asyncio
async def test_creation_is_allowed_once_the_requirements_are_on_record() -> None:
    assert (
        await company_requirements.creation_blocked(
            _FakeSession((uuid.uuid4(),)), uuid.uuid4()
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_draft_session_is_not_completion() -> None:
    """Asked of the TABLE, and of the CURRENT COMPLETE row specifically.

    A half-answered instrument would hand Sutra a document with sections
    missing rather than sections answered, so an open draft must not satisfy
    the gate. This is the case a stamp on `tenants` would have got wrong, and
    the product has paid for that mistake once already.
    """
    from app.models.hiring import CompanyDNA
    from app.schemas.company_dna import STATUS_COMPLETE, STATUS_DRAFT

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- no DB reachable
        await engine.dispose()
        pytest.skip("no database reachable")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid.uuid4()
    try:
        async with factory() as session:
            await session.execute(
                text("SELECT set_config('app.bypass_rls','on',false)")
            )
            await session.execute(
                text(
                    "INSERT INTO tenants (id, name, domain, status) "
                    "VALUES (:id, :name, :domain, 'active')"
                ),
                {
                    "id": str(tenant_id),
                    "name": f"Gate1 {tenant_id.hex[:8]}",
                    "domain": f"gate1-{tenant_id.hex[:8]}.invalid",
                },
            )
            session.add(
                CompanyDNA(
                    tenant_id=tenant_id,
                    version=1,
                    is_current=True,
                    status=STATUS_DRAFT,
                    answers_json={},
                    artifact_json={},
                    transcript_json=[],
                )
            )
            await session.flush()
            assert await company_requirements.is_complete(session, tenant_id) is False

            # The same row, completed, satisfies it.
            await session.execute(
                text("UPDATE company_dna SET status = :s WHERE tenant_id = :t"),
                {"s": STATUS_COMPLETE, "t": str(tenant_id)},
            )
            assert await company_requirements.is_complete(session, tenant_id) is True
            await session.rollback()
    finally:
        await engine.dispose()
