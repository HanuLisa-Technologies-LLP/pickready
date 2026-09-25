"""`yukti.scoring.score_links` over a real database: the per-link lock, the
one-reading-per-link rule, and what reaches the router.

The model is doubled at the router boundary. The link's Yukti COLUMNS arrive
with Phase 2 WP-B's migration; until they are mapped, `apply_outcome` sets
them as plain attributes, which is enough to assert WHAT was written. The
committed-state assertions live with WP-B's run tests.

Mutation checks recorded in the Phase 2 report: removing the
`try_advisory_lock` check fails
`test_a_link_another_transaction_is_reading_is_skipped_not_waited_on`;
removing the dedupe of `pairs` fails
`test_a_link_passed_twice_is_read_once`.
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy import text

from app.core.db import superadmin_scope
from app.services import llm_router, locks
from app.services.yukti import config, scoring
from tests import skills_fixtures as fx

RESUME = "\n".join(
    [
        "Senior data engineer",
        "Owned the Kafka ingestion pipeline serving the fraud team in production",
        "Tuned slow SQL queries with query plans",
    ]
)


class Router:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, task_type, messages, *args, **kwargs):
        payload = json.loads(messages[1]["content"])
        self.calls.append(payload)
        return json.dumps(
            {
                "results": [
                    {
                        "candidate": c["ref"],
                        "skills": [
                            {
                                "skill": s["ref"],
                                "verdict": "strong",
                                "quote": "Owned the Kafka ingestion pipeline serving the fraud team",
                            }
                            for s in payload["skills"]
                        ],
                        "experience_level": {
                            "verdict": "some",
                            "quote": "Senior data engineer",
                            "tag": "Senior data engineer",
                        },
                        "role_fit": {"verdict": "none", "quote": "", "tag": ""},
                        "company_needs": [
                            {"need": n["ref"], "verdict": "none", "quote": "", "tag": ""}
                            for n in payload["needs"]
                        ],
                    }
                    for c in payload["candidates"]
                ]
            }
        )


async def _seed(*, resume: str | None = RESUME) -> tuple[fx.World, uuid.UUID]:
    w = await fx.seed(
        skills=[("must_have", "Kafka stream processing", True, "sutra", None, "Ran Kafka.")],
        saved=True,
    )
    link = uuid.uuid4()
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                candidate, profile = uuid.uuid4(), uuid.uuid4()
                await session.execute(
                    text(
                        "INSERT INTO candidates (id, tenant_id, full_name, email) "
                        "VALUES (:c, :t, 'Lock Candidate', :e)"
                    ),
                    {"c": candidate, "t": w.tenant, "e": f"{candidate}@lock.test"},
                )
                await session.execute(
                    text(
                        "INSERT INTO profiles (id, candidate_id, source_tenant_id, resume_text) "
                        "VALUES (:p, :c, :t, :r)"
                    ),
                    {"p": profile, "c": candidate, "t": w.tenant, "r": resume},
                )
                await session.execute(
                    text(
                        "INSERT INTO job_candidate_links (id, tenant_id, job_id, candidate_id, "
                        "profile_id, source) VALUES (:l, :t, :j, :c, :p, 'fresh')"
                    ),
                    {"l": link, "t": w.tenant, "j": w.job, "c": candidate, "p": profile},
                )
    return w, link


async def _score(w: fx.World, link_id: uuid.UUID, *, times: int = 1, hold_lock: bool = False):
    from app.models import JobCandidateLink, Profile
    from app.models.job import Job

    maker = fx.sessions()
    async with maker() as holder, maker() as session:
        await holder.begin()
        if hold_lock:
            assert await locks.try_advisory_lock(holder, locks.YUKTI_LINK, link_id)
        await session.begin()
        try:
            async with superadmin_scope(session):
                job = await session.get(Job, w.job)
                link = await session.get(JobCandidateLink, link_id)
                profile = await session.get(Profile, link.profile_id)
                summary = await scoring.score_links(session, job, [(link, profile)] * times)
                written = {
                    "status": getattr(link, "yukti_status", None),
                    "profile": getattr(link, "yukti_profile_id", None),
                    "tags": getattr(link, "evidence_tags_json", None),
                    "match_score": link.match_score,
                    "profile_id": link.profile_id,
                }
        finally:
            await session.rollback()
            await holder.rollback()
    return summary, written


async def test_a_link_is_read_and_its_outcome_written_onto_it(monkeypatch) -> None:
    router = Router()
    monkeypatch.setattr(llm_router, "chat_completion", router)
    w, link_id = await _seed()
    try:
        summary, written = await _score(w, link_id)
    finally:
        await fx.drop(w)
    assert len(router.calls) == 1
    outcome = summary.outcomes[link_id]
    assert outcome.status == config.STATUS_SCORED
    assert written["status"] == config.STATUS_SCORED
    assert written["profile"] == written["profile_id"], "the resume read is the one the link carries"
    assert written["tags"] and written["tags"][0]["kind"] == config.TAG_KIND_SKILL
    assert written["match_score"] is None, "the legacy column is never written"


async def test_a_link_passed_twice_is_read_once(monkeypatch) -> None:
    router = Router()
    monkeypatch.setattr(llm_router, "chat_completion", router)
    w, link_id = await _seed()
    try:
        summary, _ = await _score(w, link_id, times=3)
    finally:
        await fx.drop(w)
    assert len(router.calls) == 1
    assert len(router.calls[0]["candidates"]) == 1
    assert list(summary.outcomes) == [link_id]


async def test_a_link_another_transaction_is_reading_is_skipped_not_waited_on(monkeypatch) -> None:
    router = Router()
    monkeypatch.setattr(llm_router, "chat_completion", router)
    w, link_id = await _seed()
    try:
        summary, written = await _score(w, link_id, hold_lock=True)
    finally:
        await fx.drop(w)
    assert summary.skipped_locked == [link_id]
    assert summary.outcomes == {}
    assert router.calls == [], "a skipped link costs no model call"
    # The column is mapped since migration 0122, so an untouched link reads
    # its server default rather than a missing attribute.
    assert written["status"] == config.STATUS_PENDING, "a skipped link is never written"


async def test_a_resume_with_no_text_is_not_assessed_without_a_model_call(monkeypatch) -> None:
    router = Router()
    monkeypatch.setattr(llm_router, "chat_completion", router)
    w, link_id = await _seed(resume="   ")
    try:
        summary, written = await _score(w, link_id)
    finally:
        await fx.drop(w)
    assert router.calls == []
    outcome = summary.outcomes[link_id]
    assert (outcome.status, outcome.failure_reason) == (
        config.STATUS_NOT_ASSESSED,
        config.FAILURE_NO_RESUME_TEXT,
    )
    assert outcome.pre_score is None
    assert written["status"] == config.STATUS_NOT_ASSESSED
    assert not summary.degraded, "a blank resume is the candidate's state, not an outage"
