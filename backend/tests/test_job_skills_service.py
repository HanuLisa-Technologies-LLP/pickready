"""The Skills step's edits, at the service, over a real table and its real constraint.

`services/skills` is the ONE implementation of add, paste, rename, move and
remove; the routes are thin over it. So the rules that broke in production are
pinned HERE, against Postgres, with every assertion that matters read from a
SECOND connection after the writer committed:

* the soft delete under a hard unique constraint (2026-09-21): a removed name
  comes back by REVIVING its row, never by an INSERT onto a live key;
* the rename and revive asymmetry on `swot_origin` (2026-09-23);
* the move that 500'd (audit #17): a move onto a removed occupant revives it;
* the per-bucket limit of five (D1), refused whole with nothing written;
* the lock (D5): once a candidate has started, every write is refused and
  nothing changes;
* an edit makes the skills UNSAVED again, so the job stops being invitable.

MUTATION CHECK, recorded: replacing `_find` in `skills.move` with the old
reorder route's blind `row.category = bucket` makes
`test_a_move_onto_a_removed_occupant_revives_it_and_never_500s` fail with
`IntegrityError` on `uq_job_competency_name`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.core.db import superadmin_scope
from app.services import assessment_contract, skills
from tests import skills_fixtures as fx

MUST, NICE, BEHAV = "must_have", "nice_to_have", "behavioural"


@pytest.fixture
async def world():
    worlds: list[fx.World] = []

    async def _make(**kwargs) -> fx.World:
        w = await fx.seed(**kwargs)
        worlds.append(w)
        return w

    yield _make
    for w in worlds:
        await fx.drop(w)


async def _saved_and_ready(w: fx.World) -> None:
    async def _stamp(session, job):
        job.framework_approved_at = datetime.now(timezone.utc)
        job.assessment_context_json = {"role_summary": "", "generated_by": "test"}
        await skills.refresh_setup_status(session, job)

    await fx.run_as(w, _stamp)
    job = await fx.committed_job(w)
    assert job["assessment_status"] == skills.READY_FOR_CANDIDATES


# ── Add and paste ────────────────────────────────────────────────────────────


async def test_a_removed_name_comes_back_by_revival_keeping_its_swot_quote(world) -> None:
    w = await world(skills=[(MUST, "Kafka", False, "sutra", "Nobody has run streaming.", "Ran Kafka.")])

    await fx.run_as(w, lambda s, j: skills.add(s, j, MUST, "Kafka", actor_user_id=w.client))

    rows = await fx.committed_skills(w)
    assert len(rows) == 1, "revived, never re-inserted"
    assert rows[0]["is_active"] is True
    assert rows[0]["id"] == w.skills["Kafka"]
    # The same name coming back is the same skill: its quotation still refers to it.
    assert rows[0]["swot_origin"] == "Nobody has run streaming."
    assert rows[0]["observable_evidence"] == "Ran Kafka."


async def test_adding_a_name_already_there_is_idempotent(world) -> None:
    w = await world(skills=[(MUST, "Python", True, "sutra", None, None)])

    rows = await fx.run_as(
        w, lambda s, j: skills.add_many(s, j, MUST, ["python", "SQL"], actor_user_id=w.client)
    )

    assert [row.name for row in rows] == ["Python", "SQL"]
    assert sorted(fx.active(await fx.committed_skills(w), MUST)) == ["Python", "SQL"]


async def test_a_sixth_skill_is_refused_and_nothing_is_written(world) -> None:
    w = await world(skills=[(MUST, f"Skill {n}", True, "sutra", None, None) for n in range(5)])

    with pytest.raises(skills.SkillLimitReached) as refused:
        await fx.run_as(w, lambda s, j: skills.add(s, j, MUST, "Sixth", actor_user_id=w.client))

    assert refused.value.http_status == 409
    assert "Must-have" in refused.value.detail
    assert not any(ch.isdigit() for ch in refused.value.detail), refused.value.detail
    assert "Sixth" not in fx.active(await fx.committed_skills(w))


async def test_a_paste_past_the_limit_is_refused_whole_naming_how_many_fit(world) -> None:
    w = await world(skills=[(NICE, f"Skill {n}", True, "sutra", None, None) for n in range(3)])

    with pytest.raises(skills.SkillLimitReached) as refused:
        await fx.run_as(
            w,
            lambda s, j: skills.add_many(s, j, NICE, ["A", "B", "C"], actor_user_id=w.client),
        )

    assert "two more skills" in refused.value.detail
    assert len(fx.active(await fx.committed_skills(w), NICE)) == 3, "nothing was added"


async def test_a_name_active_in_another_bucket_is_refused(world) -> None:
    w = await world(skills=[(MUST, "Python", True, "sutra", None, None)])

    with pytest.raises(skills.SkillClash) as refused:
        await fx.run_as(w, lambda s, j: skills.add(s, j, NICE, "Python", actor_user_id=w.client))
    assert refused.value.detail == '"Python" is already an entry under Must-have on this job.'


@pytest.mark.parametrize("action", ["add", "rename", "move"])
async def test_culture_is_refused_into_behavioural_by_every_door(world, action) -> None:
    w = await world(skills=[(MUST, "Culture fit", True, "human", None, None),
                            (BEHAV, "Ownership", True, "sutra", None, None)])

    async def _do(session, job):
        if action == "add":
            return await skills.add(session, job, BEHAV, "Culture fit", actor_user_id=w.client)
        if action == "rename":
            return await skills.rename(
                session, job, w.skills["Ownership"], "Cultural alignment", actor_user_id=w.client
            )
        return await skills.move(session, job, w.skills["Culture fit"], BEHAV, actor_user_id=w.client)

    with pytest.raises(skills.SkillRefused):
        await fx.run_as(w, _do)
    assert fx.active(await fx.committed_skills(w), BEHAV) == ["Ownership"]


# ── Rename ───────────────────────────────────────────────────────────────────


async def test_a_rename_clears_everything_derived_and_becomes_the_teams(world) -> None:
    w = await world(skills=[(MUST, "Kubernetes operations", True, "sutra",
                             "Nobody has run the cluster.", "Ran a cluster upgrade.")])

    await fx.run_as(
        w,
        lambda s, j: skills.rename(
            s, j, w.skills["Kubernetes operations"], "Incident command", actor_user_id=w.client
        ),
    )

    (row,) = await fx.committed_skills(w)
    assert row["name"] == "Incident command"
    # A quotation about Kubernetes attributed to "Incident command" would be a
    # fabricated citation (2026-09-23).
    assert row["swot_origin"] is None
    assert row["observable_evidence"] is None and row["description"] is None
    assert row["force_rank"] is None
    assert row["authored_by"] == "human"


@pytest.mark.parametrize("occupant_active", [True, False])
async def test_a_rename_onto_an_occupied_name_is_a_409_naming_it(world, occupant_active) -> None:
    w = await world(skills=[(MUST, "Python", True, "sutra", None, None),
                            (MUST, "Go", occupant_active, "sutra", None, None)])

    with pytest.raises(skills.SkillClash) as refused:
        await fx.run_as(
            w, lambda s, j: skills.rename(s, j, w.skills["Python"], "Go", actor_user_id=w.client)
        )
    assert '"Go"' in refused.value.detail
    names = {row["name"] for row in await fx.committed_skills(w)}
    assert names == {"Python", "Go"}


# ── Move ─────────────────────────────────────────────────────────────────────


async def test_a_move_changes_the_bucket_and_forgets_the_old_priority(world) -> None:
    w = await world(skills=[(MUST, "Airflow", True, "sutra", None, "Ran a DAG.")])

    await fx.run_as(
        w, lambda s, j: skills.move(s, j, w.skills["Airflow"], NICE, actor_user_id=w.client)
    )

    (row,) = await fx.committed_skills(w)
    assert row["category"] == NICE
    assert row["force_rank"] is None
    assert row["observable_evidence"] == "Ran a DAG.", "the same skill in another bucket"


async def test_a_move_onto_an_active_occupant_is_a_409(world) -> None:
    # Seeded target first, so the fixture's name map points at the MUST row.
    w = await world(skills=[(NICE, "Airflow", True, "sutra", None, None),
                            (MUST, "Airflow", True, "sutra", None, None)])
    with pytest.raises(skills.SkillClash):
        await fx.run_as(
            w, lambda s, j: skills.move(s, j, w.skills["Airflow"], NICE, actor_user_id=w.client)
        )


async def test_a_move_onto_a_removed_occupant_revives_it_and_never_500s(world) -> None:
    """Audit #17. The reorder route changed the category blind and the unique
    constraint answered 500 whenever the target held the name, even removed."""
    w = await world(skills=[(MUST, "Airflow", True, "human", None, None)])
    removed = uuid.uuid4()
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "INSERT INTO job_competencies (id, tenant_id, job_id, category, name, "
                        "required_level, ordinal, is_active, authored_by, swot_origin) VALUES "
                        "(:i, :t, :j, 'nice_to_have', 'Airflow', 82, 1, false, 'sutra', 'Quoted.')"
                    ),
                    {"i": removed, "t": w.tenant, "j": w.job},
                )

    await fx.run_as(
        w, lambda s, j: skills.move(s, j, w.skills["Airflow"], NICE, actor_user_id=w.client)
    )

    rows = {row["id"]: row for row in await fx.committed_skills(w)}
    assert rows[removed]["is_active"] is True, "the occupant was revived in the target"
    assert rows[removed]["swot_origin"] == "Quoted.", "with its own provenance"
    assert rows[w.skills["Airflow"]]["is_active"] is False


async def test_behavioural_is_a_valid_destination_now(world) -> None:
    w = await world(skills=[(MUST, "Stakeholder negotiation", True, "sutra", None, None)])
    await fx.run_as(
        w,
        lambda s, j: skills.move(
            s, j, w.skills["Stakeholder negotiation"], BEHAV, actor_user_id=w.client
        ),
    )
    assert fx.active(await fx.committed_skills(w), BEHAV) == ["Stakeholder negotiation"]


# ── Every edit unsaves; the lock refuses every edit ─────────────────────────


@pytest.mark.parametrize("action", ["add", "rename", "move", "remove"])
async def test_every_edit_makes_the_skills_unsaved_again(world, action) -> None:
    w = await world(skills=[(MUST, "Python", True, "sutra", None, "Shipped Python."),
                            (BEHAV, "Ownership", True, "sutra", None, "Owned a thing end to end.")])
    await _saved_and_ready(w)

    async def _do(session, job):
        if action == "add":
            await skills.add(session, job, NICE, "Terraform", actor_user_id=w.client)
        elif action == "rename":
            await skills.rename(session, job, w.skills["Python"], "Go", actor_user_id=w.client)
        elif action == "move":
            await skills.move(session, job, w.skills["Python"], NICE, actor_user_id=w.client)
        else:
            await skills.remove(session, job, w.skills["Python"], actor_user_id=w.client)

    await fx.run_as(w, _do)

    job = await fx.committed_job(w)
    assert job["framework_approved_at"] is None
    assert job["assessment_status"] == skills.PENDING_REVIEW


async def _lock(w: fx.World) -> None:
    """A candidate starts: the contract snapshot is written, as the start does."""
    link, conversation, candidate, profile = (uuid.uuid4() for _ in range(4))
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "INSERT INTO candidates (id, tenant_id, full_name, email, consent_databank) "
                        "VALUES (:c, :t, 'Started Candidate', :e, false)"
                    ),
                    {"c": candidate, "t": w.tenant, "e": f"{candidate}@skills.test"},
                )
                await session.execute(
                    text(
                        "INSERT INTO profiles (id, candidate_id, source_tenant_id, resume_text) "
                        "VALUES (:p, :c, :t, 'Python')"
                    ),
                    {"p": profile, "c": candidate, "t": w.tenant},
                )
                await session.execute(
                    text(
                        "INSERT INTO job_candidate_links (id, tenant_id, job_id, candidate_id, "
                        "profile_id, source) VALUES (:l, :t, :j, :c, :p, 'manual')"
                    ),
                    {"l": link, "t": w.tenant, "j": w.job, "c": candidate, "p": profile},
                )
                await session.execute(
                    text(
                        "INSERT INTO assessment_conversations (id, tenant_id, job_id, "
                        "job_candidate_link_id, grade, status, next_question_index, "
                        "reminders_sent, follow_ups_used, reasks_used, mode, started_at) "
                        "VALUES (:i, :t, :j, :l, 'managerial', 'active', 0, 0, 0, 0, "
                        "'conversational', now())"
                    ),
                    {"i": conversation, "t": w.tenant, "j": w.job, "l": link},
                )
                await assessment_contract.lock_contract(session, w.job, conversation)


@pytest.mark.parametrize("action", ["add", "paste", "rename", "move", "remove", "draft"])
async def test_every_write_after_a_start_is_refused_and_changes_nothing(world, action) -> None:
    w = await world(skills=[(MUST, "Python", True, "sutra", None, "Shipped Python."),
                            (BEHAV, "Ownership", True, "sutra", None, "Owned a thing end to end.")])
    await _saved_and_ready(w)
    await _lock(w)
    before = await fx.committed_skills(w)

    async def _do(session, job):
        if action == "add":
            await skills.add(session, job, NICE, "Terraform", actor_user_id=w.client)
        elif action == "paste":
            await skills.add_many(session, job, NICE, ["A", "B"], actor_user_id=w.client)
        elif action == "rename":
            await skills.rename(session, job, w.skills["Python"], "Go", actor_user_id=w.client)
        elif action == "move":
            await skills.move(session, job, w.skills["Python"], NICE, actor_user_id=w.client)
        elif action == "remove":
            await skills.remove(session, job, w.skills["Python"], actor_user_id=w.client)
        else:
            await skills.request_draft(session, job, requested_by=w.client, confirm_overwrite=True)

    with pytest.raises(skills.SkillsLocked) as refused:
        await fx.run_as(w, _do)
    assert str(refused.value) == assessment_contract.SKILLS_LOCKED_DETAIL
    assert await fx.committed_skills(w) == before
    assert (await fx.committed_job(w))["framework_approved_at"] is not None


# ── The view ─────────────────────────────────────────────────────────────────


async def test_the_view_is_names_and_states_only(world) -> None:
    w = await world(skills=[
        (MUST, "Kafka stream processing", True, "sutra", SWOT_QUOTE := fx.SWOT["weaknesses"], "x"),
        (MUST, "Typed by the team", True, "human", None, None),
        (NICE, "Removed", False, "sutra", None, None),
    ])

    view = await fx.run_as(w, lambda s, j: skills.view(s, j), commit=False)

    assert [entry.name for entry in view.buckets[MUST]] == ["Kafka stream processing", "Typed by the team"]
    assert view.buckets[NICE] == ()
    assert view.buckets[MUST][0].from_swot == SWOT_QUOTE
    assert view.buckets[MUST][0].source == "swot"
    assert view.buckets[MUST][1].source == "team"
    assert view.human_authored_names == ("Typed by the team",)
    assert view.blocking_reason == "Add at least one Behavioural skill before saving."
    assert view.locked is False and view.saved is False
    for field in ("evidence_line", "priority", "force_rank", "observable_evidence"):
        assert not hasattr(view.buckets[MUST][0], field), field
