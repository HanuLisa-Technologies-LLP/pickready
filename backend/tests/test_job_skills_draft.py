"""The Sutra skills draft: dispatched from the JD, written from the JD with the
saved SWOT as optional context, never a template, never over the team's own
skills without their word.

What each test pins, in the plan's terms (PLAN-p1 test 4, CONTRACT v10):

* a job created with a JD, or a JD saved, with no skill row of any kind,
  dispatches `pickready.draft_job_skills` only AFTER its commit, and a
  rolled-back save dispatches nothing (`record` backend);
* the draft needs NO SWOT: the `swot` key is absent from the payload and a
  "swot" source is reflected on; a JD too thin to draft from is refused by the
  explicit action and RECORDED as the failed state by the automatic askers;
* a JD save never re-drafts skills that exist, nor a set the team emptied;
* a draft writes at most five per bucket, `authored_by='sutra'`, and a SWOT
  quotation only when it is VERBATIM in the saved SWOT (else NULL);
* the JD's required skills and the SWOT Weaknesses both reach Must-have: an
  answer that ignores either is reflected on and re-asked;
* an outage is the `failed` state with ZERO rows: there is no template draft;
* a redraft is refused while locked, refused over the team's own skills
  without confirmation, and allowed with it without an IntegrityError;
* Leadership Intelligence: the key is ABSENT from the payload with no saved
  leadership input, present (compiled lines only) with one, and a leadership
  source is accepted only when that source's lines were supplied;
* compensation never reaches either Sutra call.

The worker body runs for real (`pickready.draft_job_skills` under the `record`
dispatch backend) against Postgres; only the model is doubled.

MUTATION CHECKS, recorded: `p1b_mutate.py draft_keeps_unverified_quote` fails
`test_a_quote_not_in_the_saved_swot_is_dropped_not_stored`;
`leadership_key_always` fails `test_no_leadership_input_means_no_key_in_the_payload`;
`draft_no_confirm_check` fails
`test_a_redraft_over_the_teams_skills_needs_their_confirmation`.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.core.db import superadmin_scope
from app.services import generation_sufficiency, llm_router, skills, swot_analysis
from app.workers import dispatch
from tests import skills_fixtures as fx

MUST, NICE, BEHAV = "must_have", "nice_to_have", "behavioural"


def _draft_answer(**override) -> dict:
    answer = {
        MUST: [
            {"name": "Kafka stream processing", "source": "swot",
             "swot_quote": fx.SWOT["weaknesses"]},
            {"name": "Python data engineering", "source": "jd", "swot_quote": ""},
        ],
        NICE: [{"name": "Airflow orchestration", "source": "jd", "swot_quote": ""}],
        BEHAV: [{"name": "Production incident ownership", "source": "swot", "swot_quote": ""}],
    }
    answer.update(override)
    return answer


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


def _drafts() -> list:
    return [d for d in dispatch.recorded() if d.name == skills.DRAFT_TASK]


async def _run_draft_task() -> None:
    """The real task body. In a thread, because a task body runs its own event
    loop (`asyncio.run`), exactly as it does in the Lambda."""
    import asyncio

    from app.workers.tasks import draft_job_skills

    sent = _drafts()
    assert sent, "no draft was dispatched"
    await asyncio.to_thread(draft_job_skills, *sent[-1].args, **sent[-1].kwargs)


async def _request(w: fx.World, *, confirm: bool = False, commit: bool = True):
    return await fx.run_as(
        w,
        lambda s, j: skills.request_draft(
            s, j, requested_by=w.client, confirm_overwrite=confirm
        ),
        commit=commit,
    )


# ── The hand-off (CONTRACT v10: from the JD) ─────────────────────────────────

#: A JD that is only a title's worth of words: below the skills gate.
THIN_JD = "## Description\nOwns the pipelines."


def _after_jd(w: fx.World):
    return lambda s, j: skills.after_jd_saved(s, j, actor_user_id=w.client)


async def test_a_jd_save_dispatches_the_draft_only_after_its_commit(world) -> None:
    w = await world()

    await fx.run_as(w, _after_jd(w), commit=False)
    assert _drafts() == [], "a rolled-back save dispatches nothing"
    assert (await fx.committed_job(w))["skills_draft_status"] == "not_started"

    await fx.run_as(w, _after_jd(w))
    (sent,) = _drafts()
    assert sent.args == (str(w.job),)
    assert sent.kwargs["mode"] == "initial"
    # The saved SWOT the draft may read, as optional context.
    assert sent.kwargs["requested_swot_version"] == 2
    assert (await fx.committed_job(w))["skills_draft_status"] == "drafting"


async def test_the_draft_is_requested_and_written_with_no_swot_at_all(world, monkeypatch) -> None:
    """The JD is the one required input. With no SWOT the request names none,
    the payload carries NO `swot` key, the Weaknesses rule does not apply, and
    the draft records that it read no SWOT."""
    w = await world(swot_row=False)
    jd_only = _draft_answer(
        must_have=[
            {"name": "Kafka stream processing", "source": "jd", "swot_quote": ""},
            {"name": "Python data engineering", "source": "jd", "swot_quote": ""},
        ],
        behavioural=[{"name": "Production incident ownership", "source": "jd", "swot_quote": ""}],
    )
    router = fx.install(monkeypatch, fx.FakeRouter(jd_only))

    await fx.run_as(w, _after_jd(w))
    (sent,) = _drafts()
    assert sent.kwargs["requested_swot_version"] is None
    await _run_draft_task()

    assert len(router.calls) == 1
    assert "swot" not in router.payload(0)
    job = await fx.committed_job(w)
    assert job["skills_draft_status"] == "drafted"
    assert job["skills_drafted_swot_version"] is None
    rows = await fx.committed_skills(w)
    assert fx.active(rows, MUST) == ["Kafka stream processing", "Python data engineering"]
    assert all(row["swot_origin"] is None for row in rows)
    assert all(row["provenance_json"]["swot_version"] is None for row in rows)


async def test_a_swot_source_is_refused_when_no_swot_was_given(world, monkeypatch) -> None:
    """A skill attributed to a SWOT nobody wrote is provenance nobody wrote:
    the evaluator reflects on it and the model is asked again."""
    w = await world(swot_row=False)
    claims_swot = _draft_answer(
        must_have=[{"name": "Kafka stream processing", "source": "swot", "swot_quote": ""}],
        behavioural=[{"name": "Production incident ownership", "source": "jd", "swot_quote": ""}],
    )
    jd_only = _draft_answer(
        must_have=[{"name": "Kafka stream processing", "source": "jd", "swot_quote": ""}],
        behavioural=[{"name": "Production incident ownership", "source": "jd", "swot_quote": ""}],
    )
    router = fx.install(monkeypatch, fx.FakeRouter(claims_swot, jd_only))
    await _request(w)

    await _run_draft_task()

    assert len(router.calls) == 2
    assert "no SWOT was given" in router.calls[1][1][-1]["content"]
    assert fx.active(await fx.committed_skills(w), MUST) == ["Kafka stream processing"]


async def test_a_jd_save_never_redrafts_skills_that_exist(world) -> None:
    w = await world(
        skills=[(MUST, "Kafka", True, "sutra", None, None)],
        draft_status="drafted", drafted_swot_version=1, swot_version=2,
    )

    handle = await fx.run_as(w, _after_jd(w))

    assert handle is None and _drafts() == []


async def test_a_swot_save_only_offers_a_redraft(world) -> None:
    """A SWOT save never drafts (CONTRACT v10): a SWOT newer than the draft is
    an OFFER the team accepts by asking, and `skills` has no SWOT hook left."""
    w = await world(
        skills=[(MUST, "Kafka", True, "sutra", None, None)],
        draft_status="drafted", drafted_swot_version=1, swot_version=2,
    )

    async def _offer(session, job):
        swot = await swot_analysis.get(session, job)
        return skills.redraft_available(job, swot, locked=False, any_row=True)

    assert await fx.run_as(w, _offer, commit=False) is True
    assert not hasattr(skills, "after_swot_saved")


async def test_an_emptied_set_is_never_redrafted_by_a_jd_save(world) -> None:
    """A set the team emptied is a decision, not a missing draft (2026-09-21)."""
    w = await world(skills=[(MUST, "Kafka", False, "sutra", None, None)])

    await fx.run_as(w, _after_jd(w))

    assert _drafts() == []


async def test_a_thin_jd_is_refused_by_the_draft_action(world) -> None:
    w = await world(jd_markdown=THIN_JD)
    with pytest.raises(skills.DraftRefused) as refused:
        await _request(w)
    assert refused.value.detail == generation_sufficiency.EMPTY_STATE_COPY["skills.jd_too_thin"]
    assert _drafts() == []
    assert (await fx.committed_job(w))["skills_draft_status"] == "not_started"


async def test_a_thin_jd_is_recorded_not_dispatched_by_a_jd_save(world) -> None:
    """The automatic asker records the state (the save itself succeeded), so
    the reconcile sweep stops selecting the job; nothing is dispatched."""
    w = await world(jd_markdown=THIN_JD)

    handle = await fx.run_as(w, _after_jd(w))

    assert handle is None and _drafts() == []
    job = await fx.committed_job(w)
    assert job["skills_draft_status"] == "failed"
    assert job["skills_draft_error"] == generation_sufficiency.EMPTY_STATE_COPY[
        "skills.jd_too_thin"
    ]


# ── The draft itself ─────────────────────────────────────────────────────────


async def test_a_draft_writes_sutras_skills_with_their_provenance(world, monkeypatch) -> None:
    w = await world()
    fx.install(monkeypatch, fx.FakeRouter(_draft_answer()))
    await _request(w)

    await _run_draft_task()

    rows = await fx.committed_skills(w)
    assert sorted(fx.active(rows, MUST)) == ["Kafka stream processing", "Python data engineering"]
    assert fx.active(rows, NICE) == ["Airflow orchestration"]
    assert fx.active(rows, BEHAV) == ["Production incident ownership"]
    by_name = {row["name"]: row for row in rows}
    assert all(row["authored_by"] == "sutra" for row in rows)
    assert by_name["Kafka stream processing"]["swot_origin"] == fx.SWOT["weaknesses"]
    assert by_name["Kafka stream processing"]["force_rank"] == 1
    assert by_name["Python data engineering"]["force_rank"] == 2
    assert by_name["Python data engineering"]["provenance_json"]["source"] == "jd"
    job = await fx.committed_job(w)
    assert job["skills_draft_status"] == "drafted"
    assert job["skills_drafted_swot_version"] == 2
    assert job["framework_approved_at"] is None, "a draft is never a saved set"
    (audit,) = await fx.committed_audit(w, "job_skills_drafted")
    assert audit["agent_name"] == "sutra"
    assert str(audit["actor_user_id"]) == str(w.client)


async def test_a_quote_not_in_the_saved_swot_is_dropped_not_stored(world, monkeypatch) -> None:
    """A fabricated citation is worse than a missing one: it reads as provenance."""
    w = await world()
    answer = _draft_answer()
    answer[MUST][0]["swot_quote"] = "The team has never shipped anything on time."
    fx.install(monkeypatch, fx.FakeRouter(answer))
    await _request(w)

    await _run_draft_task()

    by_name = {row["name"]: row for row in await fx.committed_skills(w)}
    assert by_name["Kafka stream processing"]["swot_origin"] is None


async def test_the_jd_skills_and_the_swot_weaknesses_both_reach_must_have(world, monkeypatch) -> None:
    """D1, enforced rather than requested: an answer whose Must-have ignores the
    JD's required skills and the SWOT gap is reflected on and asked again."""
    w = await world()
    ignoring = _draft_answer(
        must_have=[{"name": "Stakeholder management", "source": "company", "swot_quote": ""}]
    )
    router = fx.install(monkeypatch, fx.FakeRouter(ignoring, _draft_answer()))
    await _request(w)

    await _run_draft_task()

    assert len(router.calls) == 2
    reflection = router.calls[1][1][-1]["content"]
    assert "required skills" in reflection and "Weaknesses" in reflection
    payload = router.payload(0)
    assert payload["jd_required_skills"] == fx.JD_SKILLS
    assert payload["swot"]["weaknesses"] == fx.SWOT["weaknesses"]
    assert "Kafka stream processing" in fx.active(await fx.committed_skills(w), MUST)


async def test_more_than_five_in_a_bucket_is_never_written(world, monkeypatch) -> None:
    w = await world()
    six = [{"name": f"Skill number {n}", "source": "jd", "swot_quote": ""} for n in range(6)]
    fx.install(monkeypatch, fx.FakeRouter(
        _draft_answer(nice_to_have=six), _draft_answer(nice_to_have=six),
        _draft_answer(nice_to_have=six),
    ))
    await _request(w)

    from app.services.hiring import sutra

    with pytest.raises(sutra.SutraUnavailable):
        await _run_draft_task()

    assert fx.active(await fx.committed_skills(w)) == []
    assert (await fx.committed_job(w))["skills_draft_status"] == "failed"


async def test_an_outage_is_a_failed_state_with_zero_rows_and_no_template(world, monkeypatch) -> None:
    from app.services.hiring import sutra

    w = await world()
    fx.install(monkeypatch, fx.FakeRouter(*(llm_router.LLMUnavailableError("down") for _ in range(3))))
    await _request(w)

    with pytest.raises(sutra.SutraUnavailable):
        await _run_draft_task()

    assert await fx.committed_skills(w) == []
    job = await fx.committed_job(w)
    assert job["skills_draft_status"] == "failed"
    assert job["skills_draft_error"] == skills.DRAFT_FAILED_DETAIL


async def test_a_redelivered_first_draft_is_a_no_op(world, monkeypatch) -> None:
    w = await world()
    router = fx.install(monkeypatch, fx.FakeRouter(_draft_answer()))
    await _request(w)
    await _run_draft_task()
    written = await fx.committed_skills(w)

    async def _drafting_again(session, job):
        job.skills_draft_status = "drafting"

    await fx.run_as(w, _drafting_again)
    await _run_draft_task()

    assert len(router.calls) == 1, "the redelivery spent no model call"
    assert await fx.committed_skills(w) == written


# ── Redraft ──────────────────────────────────────────────────────────────────


async def test_a_redraft_over_the_teams_skills_needs_their_confirmation(world, monkeypatch) -> None:
    w = await world(skills=[
        (MUST, "Typed by the team", True, "human", None, None),
        (MUST, "Kafka stream processing", False, "sutra", "old quote", "Ran Kafka."),
    ], draft_status="drafted", drafted_swot_version=1)

    with pytest.raises(skills.HumanSkillsWouldBeReplaced) as refused:
        await _request(w)
    assert refused.value.names == ("Typed by the team",)
    assert _drafts() == []

    fx.install(monkeypatch, fx.FakeRouter(_draft_answer()))
    await _request(w, confirm=True)
    await _run_draft_task()

    rows = await fx.committed_skills(w)
    by_name = {row["name"]: row for row in rows}
    assert by_name["Typed by the team"]["is_active"] is False
    # The removed row of the same name was REVIVED, never re-inserted onto its
    # live unique key, and it carries the new draft's quotation.
    assert by_name["Kafka stream processing"]["id"] == w.skills["Kafka stream processing"]
    assert by_name["Kafka stream processing"]["is_active"] is True
    assert by_name["Kafka stream processing"]["swot_origin"] == fx.SWOT["weaknesses"]
    assert by_name["Kafka stream processing"]["authored_by"] == "sutra"


async def test_a_redraft_is_refused_once_a_candidate_has_started(world) -> None:
    from tests.test_job_skills_service import _lock

    w = await world(skills=[
        (MUST, "Kafka", True, "sutra", None, "Ran Kafka in production for a year."),
        (BEHAV, "Ownership", True, "sutra", None, "Owned a migration end to end."),
    ], saved=True)
    await _lock(w)

    with pytest.raises(skills.SkillsLocked):
        await _request(w, confirm=True)
    assert _drafts() == []


# ── What the model is given ──────────────────────────────────────────────────


async def test_no_leadership_input_means_no_key_in_the_payload(world, monkeypatch) -> None:
    """The enhancement-layer contract, at the prompt: no leadership input, no
    key, and no rule about it in the instruction either, so the request is the
    bytes it was before Leadership Intelligence existed."""
    w = await world(department="Engineering")
    router = fx.install(monkeypatch, fx.FakeRouter(_draft_answer()))
    await _request(w)
    await _run_draft_task()

    payload = router.payload(0)
    assert "leadership_context" not in payload
    system = router.calls[0][1][0]["content"]
    assert "leadership_context" not in system
    rows = await fx.committed_skills(w)
    assert all("leadership" not in (row["provenance_json"] or {}) for row in rows)


async def test_leadership_reaches_the_payload_as_compiled_lines_only(world, monkeypatch) -> None:
    """A CEO's and the department head's saved input reach the draft as the
    COMPILED lines, each with its source, and a refused line never does."""
    from tests import leadership_fixtures as lf

    w = await world(department="Engineering")
    engineering = await lf.add_department(w, "Engineering")
    await lf.set_job_department(w, engineering, "Engineering")
    ceo = await lf.add_leader(w, "ceo")
    head = await lf.add_leader(w, "functional_head", department_id=engineering)
    await lf.save(w, ceo, company=f"{lf.CEO_LINE} {lf.UNSAFE_LINE} {lf.VAGUE_LINE}")
    await lf.save(w, head, department=lf.FH_LINE)
    router = fx.install(monkeypatch, fx.FakeRouter(_draft_answer()))
    await _request(w)
    await _run_draft_task()

    lines = router.payload(0)["leadership_context"]
    assert {line["source"] for line in lines} == {"leadership_ceo", "leadership_functional_head"}
    texts = [line["text"] for line in lines]
    assert lf.CEO_LINE in texts and lf.FH_LINE in texts
    raw = router.raw(0)
    assert "under 30" not in raw and "aggressive" not in raw
    assert "leadership_context" in router.calls[0][1][0]["content"]
    rows = await fx.committed_skills(w)
    sources = rows[0]["provenance_json"]["leadership"]
    assert set(sources) == {"leadership_ceo", "leadership_functional_head"}


async def test_a_leadership_source_is_kept_when_supplied_and_refused_when_not(
    world, monkeypatch
) -> None:
    """A skill drafted from the CEO's line records `leadership_ceo`; a skill
    attributed to the MD, who wrote nothing, is reflected on and re-asked."""
    from tests import leadership_fixtures as lf

    w = await world()
    ceo = await lf.add_leader(w, "ceo")
    await lf.save(w, ceo, company=lf.CEO_LINE)
    claims_md = _draft_answer(
        behavioural=[
            {"name": "Production incident ownership", "source": "leadership_md", "swot_quote": ""}
        ],
    )
    from_ceo = _draft_answer(
        behavioural=[
            {"name": "Production incident ownership", "source": "leadership_ceo", "swot_quote": ""}
        ],
    )
    router = fx.install(monkeypatch, fx.FakeRouter(claims_md, from_ceo))
    await _request(w)
    await _run_draft_task()

    assert len(router.calls) == 2
    assert '"leadership_ceo"' in router.calls[1][1][-1]["content"]
    rows = {row["name"]: row for row in await fx.committed_skills(w)}
    assert rows["Production incident ownership"]["provenance_json"]["source"] == "leadership_ceo"


async def test_compensation_never_reaches_the_draft_call(world, monkeypatch) -> None:
    w = await world(compensation={"ctc_min": 1800000, "ctc_max": 2600000, "currency": "INR"})
    router = fx.install(monkeypatch, fx.FakeRouter(_draft_answer()))
    await _request(w)
    await _run_draft_task()

    sent = router.calls[0][1][1]["content"].lower()
    for needle in ("1800000", "2600000", "ctc", "compensation", "salary", "inr"):
        assert needle not in sent, needle


async def test_a_stale_drafting_state_reads_as_failed(world) -> None:
    from datetime import timedelta

    w = await world(
        draft_status="drafting",
        draft_requested_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    view = await fx.run_as(w, lambda s, j: skills.view(s, j), commit=False)

    assert view.draft_status == "failed"
    assert view.draft_error == skills.DRAFT_FAILED_DETAIL
    assert (await fx.committed_job(w))["skills_draft_status"] == "drafting", "never written"
