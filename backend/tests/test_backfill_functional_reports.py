"""`backfill_functional_reports` saves a demo job's skills only when it has a
saveable set, and says so otherwise.

The version this replaced stamped `framework_approved_at` and
`ready_for_candidates` on every job it touched BEFORE looking for skills, so a
demo job with none, or with a set Save Skills would refuse, read as ready for
candidates. Real tables, the script's own commit, and a SECOND connection for
every assertion.

MUTATION CHECK, recorded: dropping the `validate_for_save` refusal (the
`continue` after the skip line) fails
`test_an_unsaveable_set_is_skipped_and_never_stamped`: the job reads saved.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.core.db import superadmin_scope
from app.scripts import backfill_functional_reports as backfill
from tests import skills_fixtures as fx

MUST, BEHAV = "must_have", "behavioural"
SAVEABLE = [
    (MUST, "Kafka stream processing", True, "sutra", None, None),
    (BEHAV, "Production incident ownership", True, "sutra", None, None),
]


async def _mock_applicant(w: fx.World) -> uuid.UUID:
    """One seed-corpus candidate (the suffix the script selects on), applied."""
    candidate, profile, link = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    email = f"{candidate.hex[:12]}{backfill.MOCK_EMAIL_SUFFIX}"
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "INSERT INTO candidates (id, tenant_id, full_name, email, "
                        "consent_databank) VALUES (:c, NULL, 'Mock Person', :e, TRUE)"
                    ),
                    {"c": candidate, "e": email},
                )
                await session.execute(
                    text(
                        "INSERT INTO profiles (id, candidate_id, resume_text) "
                        "VALUES (:p, :c, 'Six years on streaming pipelines.')"
                    ),
                    {"p": profile, "c": candidate},
                )
                await session.execute(
                    text(
                        "INSERT INTO job_candidate_links (id, tenant_id, job_id, "
                        "candidate_id, profile_id, source, status, source_type) "
                        "VALUES (:l, :t, :j, :c, :p, 'fresh', 'applied', 'applied')"
                    ),
                    {"l": link, "t": w.tenant, "j": w.job, "c": candidate, "p": profile},
                )
    return candidate


async def _drop_candidate(candidate: uuid.UUID) -> None:
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text("DELETE FROM candidates WHERE id = :c"), {"c": candidate}
                )


@pytest.fixture(autouse=True)
def _a_fresh_engine_per_run(monkeypatch) -> None:
    """The script asks the app for its process-wide engine, which is bound to
    the first event loop that used it; each test runs on its own loop, so the
    second test would inherit a pool from a closed loop (the 2026-09-16
    singleton class). The same NullPool factory the fixtures read back with."""
    monkeypatch.setattr(backfill, "get_session_factory", fx.sessions)


@pytest.fixture
async def world():
    worlds: list[fx.World] = []
    candidates: list[uuid.UUID] = []

    async def _make(**kwargs) -> fx.World:
        w = await fx.seed(**kwargs)
        worlds.append(w)
        candidates.append(await _mock_applicant(w))
        return w

    yield _make
    for w in worlds:
        await fx.drop(w)
    for candidate in candidates:
        await _drop_candidate(candidate)


async def test_a_saveable_demo_set_is_saved_with_the_honest_empty_context(world) -> None:
    w = await world(skills=SAVEABLE)

    counts = await backfill.backfill(apply=True)

    assert counts["reports"] >= 1
    job = await fx.committed_job(w)
    assert job["framework_approved_at"] is not None
    assert job["assessment_context_json"] == {
        "role_summary": "",
        "generated_by": "backfill_functional_reports",
    }
    assert job["assessment_status"] == "ready_for_candidates"


async def test_an_unsaveable_set_is_skipped_and_never_stamped(world) -> None:
    w = await world(skills=[SAVEABLE[0]])

    await backfill.backfill(apply=True)

    job = await fx.committed_job(w)
    assert job["framework_approved_at"] is None
    assert job["assessment_context_json"] is None
    assert job["assessment_status"] != "ready_for_candidates"


async def test_a_job_with_no_skills_is_skipped_and_never_stamped(world) -> None:
    w = await world()

    await backfill.backfill(apply=True)

    job = await fx.committed_job(w)
    assert job["framework_approved_at"] is None
    assert job["assessment_status"] != "ready_for_candidates"
