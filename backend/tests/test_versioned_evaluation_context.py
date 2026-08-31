"""A candidate is evaluated against the criteria in force WHEN THEY APPLIED.

THE SCENARIO spec-doc6 5 ASKS FOR, VERBATIM
---------------------------------------------
    "A candidate's evaluation context references the exact versions in force
     when they applied (RBAC 22). Test this with a scenario: candidate applies,
     criteria are revised, candidate's evaluation still resolves against the
     original version."

WHAT WAS ACTUALLY WRONG, WHICH IS SUBTLER THAN "NO VERSIONING"
----------------------------------------------------------------
The versioning existed. `evaluations.scorecard_version` is copied rather than
joined, `job_company_dna_bindings` is append-only, and both carry the discipline
`report_dimensions.required_level` established years ago. The defect was the
INSTANT: the version was copied at SCORING time. A criteria revision landing
between the moment somebody applies and the moment they are scored regraded them
against rules that did not exist when they chose to apply.

Nothing about that is visible afterwards. The evaluation carries a version, the
version is real, the row looks correct, and the only way to notice is to compare
two timestamps nobody thought to compare. So the resolution is done once, as of
`job_candidate_links.created_at`, and this file is the proof.

THE TEST USES REAL POSTGRES, AND HAS TO
-----------------------------------------
The resolution is a `LEFT JOIN LATERAL ... ORDER BY frozen_at DESC LIMIT 1` with
an inclusive `<=` boundary. Every one of those is a property of the query rather
than of the Python around it, and a fake session would assert the Python.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.services.agents import provenance
from app.services.orchestration import versioning

#: The instants the scenario turns on, spaced far enough apart that no clock
#: skew or truncation can reorder them.
FROZEN_V1 = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
APPLIED_EARLY = datetime(2026, 3, 5, 9, 0, tzinfo=timezone.utc)
FROZEN_V2 = datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc)
APPLIED_LATE = datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)


async def _db_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:
        await engine.dispose()
        pytest.skip("no database reachable, skipping the versioned-context scenario")
    return engine


class _Scenario:
    """One tenant, one job, two freezes and two applicants, torn down after.

    Rows are inserted with explicit ids and explicit `created_at` values,
    because the whole question is which instant precedes which and a
    `DEFAULT now()` would make every applicant contemporaneous with every
    freeze.
    """

    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.job_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.dna_v1 = uuid.uuid4()
        self.dna_v2 = uuid.uuid4()
        self.candidate_early = uuid.uuid4()
        self.candidate_late = uuid.uuid4()
        self.link_early = uuid.uuid4()
        self.link_late = uuid.uuid4()
        self.correlation_id = provenance.correlation_for_job(self.job_id)

    @property
    def principal(self) -> provenance.Principal:
        return provenance.Principal(
            user_id=str(self.user_id),
            role="hiring_manager",
            tenant_id=str(self.tenant_id),
        )

    async def build(self, session) -> None:
        await session.execute(
            text(
                "INSERT INTO tenants (id, name, domain) "
                "VALUES (:id, :name, :domain)"
            ),
            {
                "id": self.tenant_id,
                "name": f"Versioning Scenario {self.tenant_id.hex[:8]}",
                "domain": f"vs-{self.tenant_id.hex[:12]}.example",
            },
        )
        await session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, title, status, correlation_id) "
                "VALUES (:id, :tenant, :title, 'draft', :correlation)"
            ),
            {
                "id": self.job_id,
                "tenant": self.tenant_id,
                "title": "Senior Data Platform Engineer",
                "correlation": self.correlation_id,
            },
        )
        for dna_id, version in ((self.dna_v1, 1), (self.dna_v2, 2)):
            await session.execute(
                text(
                    "INSERT INTO company_dna (id, tenant_id, version, status, "
                    "is_current) VALUES (:id, :tenant, :version, 'complete', false)"
                ),
                {"id": dna_id, "tenant": self.tenant_id, "version": version},
            )
        for candidate_id, link_id, applied in (
            (self.candidate_early, self.link_early, APPLIED_EARLY),
            (self.candidate_late, self.link_late, APPLIED_LATE),
        ):
            await session.execute(
                text("INSERT INTO candidates (id) VALUES (:id)"),
                {"id": candidate_id},
            )
            await session.execute(
                text(
                    "INSERT INTO job_candidate_links "
                    "(id, tenant_id, job_id, candidate_id, source, created_at) "
                    "VALUES (:id, :tenant, :job, :candidate, 'applied', :at)"
                ),
                {
                    "id": link_id,
                    "tenant": self.tenant_id,
                    "job": self.job_id,
                    "candidate": candidate_id,
                    "at": applied,
                },
            )

    async def freeze(self, session, *, sequence: int, dna_id, at) -> None:
        """One append-only freeze. Nothing ever updates a binding."""
        await session.execute(
            text(
                "INSERT INTO job_company_dna_bindings "
                "(id, tenant_id, job_id, company_dna_id, company_dna_version, "
                " freeze_sequence, scorecard_version, correlation_id, frozen_at) "
                "VALUES (:id, :tenant, :job, :dna, :dna_version, :sequence, "
                "        :sequence, :correlation, :at)"
            ),
            {
                "id": uuid.uuid4(),
                "tenant": self.tenant_id,
                "job": self.job_id,
                "dna": dna_id,
                "dna_version": sequence,
                "sequence": sequence,
                "correlation": self.correlation_id,
                "at": at,
            },
        )

    async def evaluate(self, session, *, link_id, version: int) -> None:
        await session.execute(
            text(
                "INSERT INTO evaluations (id, tenant_id, job_id, link_id, "
                "scorecard_version) VALUES (:id, :tenant, :job, :link, :version)"
            ),
            {
                "id": uuid.uuid4(),
                "tenant": self.tenant_id,
                "job": self.job_id,
                "link": link_id,
                "version": version,
            },
        )

    async def teardown(self, session) -> None:
        # `tenants` cascades to jobs, links and bindings; candidates have no
        # tenant, so they are removed explicitly.
        await session.execute(
            text("DELETE FROM tenants WHERE id = :id"), {"id": self.tenant_id}
        )
        await session.execute(
            text("DELETE FROM candidates WHERE id = ANY(:ids)"),
            {"ids": [self.candidate_early, self.candidate_late]},
        )


async def _scenario(session) -> _Scenario:
    scenario = _Scenario()
    await scenario.build(session)
    await scenario.freeze(session, sequence=1, dna_id=scenario.dna_v1, at=FROZEN_V1)
    await scenario.freeze(session, sequence=2, dna_id=scenario.dna_v2, at=FROZEN_V2)
    await session.commit()
    return scenario


# ══════════════════════════════════════════════════════════════════════════
# THE SCENARIO
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_revision_does_not_reach_backwards_into_an_earlier_application() -> None:
    """Applied under v1, criteria revised to v2, still evaluated under v1."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            scenario = await _scenario(session)
            try:
                early = await versioning.resolve_for_application(
                    session, scenario.link_early
                )
                late = await versioning.resolve_for_application(
                    session, scenario.link_late
                )

                assert early.scorecard_version == 1
                assert early.company_dna_version == 1
                assert early.company_dna_id == str(scenario.dna_v1)
                assert early.freeze_sequence == 1
                assert early.applied_at == APPLIED_EARLY

                # The same job, the same revision, a later applicant: v2. If
                # this did not move, the resolution would be pinning the first
                # version forever rather than resolving as of an instant.
                assert late.scorecard_version == 2
                assert late.company_dna_id == str(scenario.dna_v2)

                # The flow id travels with the context, so an evaluation is
                # joinable to the freeze that produced its criteria.
                assert early.correlation_id == scenario.correlation_id
            finally:
                await scenario.teardown(session)
                await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_freeze_in_the_same_instant_as_the_application_counts() -> None:
    """Boundaries are inclusive and ties go to the candidate, which is the rule
    this codebase already follows for posting windows and tier cut-points."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            scenario = _Scenario()
            await scenario.build(session)
            await scenario.freeze(
                session, sequence=1, dna_id=scenario.dna_v1, at=APPLIED_EARLY
            )
            await session.commit()
            try:
                context = await versioning.resolve_for_application(
                    session, scenario.link_early
                )
                assert context.scorecard_version == 1
            finally:
                await scenario.teardown(session)
                await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_application_that_predates_every_freeze_is_refused() -> None:
    """Not defaulted to the current version. Falling back to today's criteria is
    the silent regrade this whole module exists to prevent, and it would be
    invisible: the evaluation would carry a real version that simply was not the
    one the candidate applied under."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            scenario = _Scenario()
            await scenario.build(session)
            await scenario.freeze(
                session,
                sequence=1,
                dna_id=scenario.dna_v1,
                at=APPLIED_EARLY + timedelta(days=1),
            )
            await session.commit()
            try:
                with pytest.raises(versioning.UnresolvableContext) as exc:
                    await versioning.resolve_for_application(
                        session, scenario.link_early
                    )
                assert "do not evaluate it against today's" in str(exc.value)
            finally:
                await scenario.teardown(session)
                await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_application_that_does_not_exist_is_refused() -> None:
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            with pytest.raises(versioning.UnresolvableContext):
                await versioning.resolve_for_application(session, uuid.uuid4())
    finally:
        await engine.dispose()


# ══════════════════════════════════════════════════════════════════════════
# THE CONTROLLED REVISION WORKFLOW (RBAC 12)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_revision_supersedes_and_never_mutates() -> None:
    """The plan names the version it replaces, the version it creates, its
    author and how many people it does not apply to."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            scenario = await _scenario(session)
            await scenario.evaluate(session, link_id=scenario.link_early, version=2)
            await session.commit()
            try:
                revision = await versioning.plan_revision(
                    session,
                    job_id=scenario.job_id,
                    reason="criterion_defective",
                    author=scenario.principal,
                    note="The Kubernetes criterion duplicated the container one.",
                )
                assert revision.supersedes_version == 2
                assert revision.new_version == 3
                assert revision.author_user_id == str(scenario.user_id)
                assert revision.author_role == "hiring_manager"
                assert revision.correlation_id == scenario.correlation_id
                # The count of people the new version does NOT apply to, put in
                # front of the person authorising it rather than discovered
                # afterwards.
                assert revision.evaluated_under_previous == 1
            finally:
                await scenario.teardown(session)
                await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_unclassified_revision_reason_is_refused() -> None:
    """An unclassified criteria change is one nobody can review later."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            scenario = await _scenario(session)
            try:
                with pytest.raises(versioning.RevisionRefused) as exc:
                    await versioning.plan_revision(
                        session,
                        job_id=scenario.job_id,
                        reason="tidy_up",
                        author=scenario.principal,
                    )
                assert "criterion_defective" in str(exc.value)
            finally:
                await scenario.teardown(session)
                await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_author_from_another_tenant_cannot_revise() -> None:
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            scenario = await _scenario(session)
            stranger = provenance.Principal(
                user_id=str(uuid.uuid4()),
                role="hiring_manager",
                tenant_id=str(uuid.uuid4()),
            )
            try:
                with pytest.raises(versioning.RevisionRefused):
                    await versioning.plan_revision(
                        session,
                        job_id=scenario.job_id,
                        reason="role_changed",
                        author=stranger,
                    )
            finally:
                await scenario.teardown(session)
                await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_job_that_was_never_frozen_cannot_be_revised() -> None:
    """Freezing and revising are different operations with different
    authorization, and a caller reaching for one when it means the other should
    be told rather than quietly given the wrong one."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            scenario = _Scenario()
            await scenario.build(session)
            await session.commit()
            try:
                with pytest.raises(versioning.RevisionRefused) as exc:
                    await versioning.plan_revision(
                        session,
                        job_id=scenario.job_id,
                        reason="role_changed",
                        author=scenario.principal,
                    )
                assert "Freeze the matrix first" in str(exc.value)
            finally:
                await scenario.teardown(session)
                await session.commit()
    finally:
        await engine.dispose()


def test_every_revision_reason_is_a_recorded_category() -> None:
    """A closed list. "other" is how an audit trail stops answering the
    question it was built for."""
    assert "other" not in versioning.REVISION_REASONS
    assert len(set(versioning.REVISION_REASONS)) == len(versioning.REVISION_REASONS)
