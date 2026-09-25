"""`matching.run_matching` over a real database (PLAN-p2 section 5.1, test 6).

The model is doubled at the router boundary and nothing else is: the run,
retrieval, the databank discovery, `start_sourced`, Yukti's inputs, grounding,
scoring and `apply_outcome` all run for real, and every committed-state
assertion reads from a SECOND connection after the run's own commit.

Other test modules may leave consenting databank candidates in the shared
database, and a platform-wide databank search is allowed to find them. So every
assertion here is about THIS module's own rows, never a total.

Mutation checks recorded in the Phase 2 WP-B report:
* reading the RETRIEVAL profile instead of `link.profile_id` fails
  `test_a_linked_candidate_is_read_from_the_resume_their_link_carries`;
* dropping `start_sourced` from the databank discovery fails
  `test_a_databank_candidate_is_linked_as_sourced_with_one_history_row`;
* removing the skills gate fails `test_a_job_whose_skills_were_never_saved_is_not_matched`.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db import superadmin_scope
from app.services import llm_router, matching, matching_progress
from app.services.yukti import config
from tests import skills_fixtures as fx

DIM = 1024
PAY_SENTINEL = "CTC_SENTINEL_7319"
PAY_AMOUNT = "73,19,000"
NAME = "Priya Raghunathan"
EMAIL_LOCAL = "priya.raghunathan"
PHONE = "+91 98450 12345"
EMPLOYER = "Zephyrine Analytics"

LINKED_RESUME = "\n".join(
    [
        f"{NAME}",
        f"{EMAIL_LOCAL}@mail.test | {PHONE}",
        "Senior data engineer",
        f"Owned the Kafka ingestion pipeline serving the fraud team at {EMPLOYER}",
        f"Current CTC {PAY_SENTINEL} which is {PAY_AMOUNT} per year",
        "Tuned slow SQL queries with query plans",
    ]
)
SECOND_RESUME = "PROFILE_TWO_MARKER wrote Kafka consumers in a hobby project"
DATABANK_RESUME = "\n".join(
    [
        "Data engineer",
        "Owned the Kafka ingestion pipeline serving the fraud team in production",
        "Built Python batch jobs",
    ]
)
KAFKA_QUOTE = "Owned the Kafka ingestion pipeline serving the fraud team"


def _vector(sign: float) -> str:
    return "[" + ",".join(f"{sign * (1.0 if i == 0 else 0.0):.1f}" for i in range(DIM)) + "]"


def _fixed_embed(vector: list[float]):
    async def embed(texts, *args, **kwargs):
        return [list(vector) for _ in texts]

    return embed


class Router:
    """The model, answering every candidate it is shown with the Kafka quote."""

    def __init__(self, *, fail: BaseException | None = None, malformed: bool = False) -> None:
        self.fail = fail
        self.malformed = malformed
        self.calls: list[dict] = []
        self.raw: list[str] = []

    async def __call__(self, task_type, messages, *args, **kwargs):
        self.raw.append(json.dumps(messages, ensure_ascii=False))
        payload = json.loads(messages[1]["content"])
        self.calls.append(payload)
        if self.fail is not None:
            raise self.fail
        if self.malformed:
            return "not json at all"
        return json.dumps(
            {
                "results": [
                    {
                        "candidate": c["ref"],
                        "skills": [
                            {
                                "skill": s["ref"],
                                "verdict": "strong" if KAFKA_QUOTE in c["resume"] else "none",
                                "quote": KAFKA_QUOTE if KAFKA_QUOTE in c["resume"] else "",
                            }
                            for s in payload["skills"]
                        ],
                        "experience_level": {"verdict": "none", "quote": "", "tag": ""},
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

    def resumes(self) -> list[str]:
        return [c["resume"] for call in self.calls for c in call["candidates"]]


async def _seed(*, saved: bool = True, databank: bool = False) -> dict[str, Any]:
    w = await fx.seed(
        skills=[
            ("must_have", "Kafka stream processing", True, "sutra", None, "Ran Kafka."),
            ("behavioural", "Ownership", True, "sutra", None, "Owned outcomes."),
        ],
        saved=saved,
        compensation={"ctc_min": 1000000, "ctc_max": 2000000, "note": PAY_SENTINEL},
    )
    ids: dict[str, Any] = {"world": w}
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "UPDATE jobs SET ratified_at = now(), lifecycle_state = 'PUBLISHED', "
                        "status = 'ratified' WHERE id = :j"
                    ),
                    {"j": w.job},
                )
                cand, p1, p2, link = (uuid.uuid4() for _ in range(4))
                await session.execute(
                    text(
                        "INSERT INTO candidates (id, tenant_id, full_name, email, phone) "
                        "VALUES (:c, :t, :n, :e, :ph)"
                    ),
                    {"c": cand, "t": w.tenant, "n": NAME,
                     "e": f"{EMAIL_LOCAL}@mail.test", "ph": PHONE},
                )
                parsed = json.dumps(
                    {"employment_history": [{"company": EMPLOYER, "title": "Engineer"}],
                     "current_ctc": PAY_SENTINEL}
                )
                await session.execute(
                    text(
                        "INSERT INTO profiles (id, candidate_id, source_tenant_id, "
                        "resume_text, parsed_fields_json, embedding) VALUES "
                        "(:p, :c, :t, :r, CAST(:pf AS jsonb), CAST(:v AS vector))"
                    ),
                    {"p": p1, "c": cand, "t": w.tenant, "r": LINKED_RESUME,
                     "pf": parsed, "v": _vector(-1.0)},
                )
                # The candidate's SECOND resume sits exactly on the JD vector,
                # so retrieval ranks the candidate on it.
                await session.execute(
                    text(
                        "INSERT INTO profiles (id, candidate_id, source_tenant_id, "
                        "resume_text, embedding) VALUES (:p, :c, :t, :r, CAST(:v AS vector))"
                    ),
                    {"p": p2, "c": cand, "t": w.tenant, "r": SECOND_RESUME, "v": _vector(1.0)},
                )
                await session.execute(
                    text(
                        "INSERT INTO job_candidate_links (id, tenant_id, job_id, candidate_id, "
                        "profile_id, source, validation_json) VALUES (:l, :t, :j, :c, :p, "
                        "'fresh', CAST(:v AS jsonb))"
                    ),
                    {"l": link, "t": w.tenant, "j": w.job, "c": cand, "p": p1,
                     "v": json.dumps({"current_ctc": PAY_AMOUNT, "expected_ctc": PAY_SENTINEL,
                                      "notice_period": "Immediate"})},
                )
                ids.update(candidate=cand, linked_profile=p1, second_profile=p2, link=link)
                if databank:
                    dcand, dp = uuid.uuid4(), uuid.uuid4()
                    await session.execute(
                        text(
                            "INSERT INTO candidates (id, tenant_id, full_name, email, "
                            "consent_databank, main_profile_id) VALUES (:c, NULL, "
                            "'Databank Person', :e, true, NULL)"
                        ),
                        {"c": dcand, "e": f"{dcand}@databank.test"},
                    )
                    await session.execute(
                        text(
                            "INSERT INTO profiles (id, candidate_id, resume_text, embedding) "
                            "VALUES (:p, :c, :r, CAST(:v AS vector))"
                        ),
                        {"p": dp, "c": dcand, "r": DATABANK_RESUME, "v": _vector(1.0)},
                    )
                    await session.execute(
                        text("UPDATE candidates SET main_profile_id = :p WHERE id = :c"),
                        {"p": dp, "c": dcand},
                    )
                    ids.update(databank_candidate=dcand, databank_profile=dp)
    return ids


async def _drop(ids: dict[str, Any]) -> None:
    await fx.drop(ids["world"])
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                for key in ("candidate", "databank_candidate"):
                    if key in ids:
                        await session.execute(
                            text("DELETE FROM candidates WHERE id = :c"), {"c": ids[key]}
                        )


async def _run(ids: dict[str, Any], monkeypatch, router: Router):
    monkeypatch.setattr(llm_router, "chat_completion", router)
    monkeypatch.setattr(matching, "embed", _fixed_embed([1.0] + [0.0] * (DIM - 1)))
    payloads: list[dict] = []
    progress = matching_progress.Progress(publish=payloads.append)

    async def work(session, job):
        return await matching.run_matching(session, job.id, progress=progress)

    scored = await fx.run_as(ids["world"], work, commit=False)
    return scored, payloads[-1] if payloads else progress.payload()


async def _link_row(link_id: uuid.UUID) -> dict:
    async with fx.sessions()() as session:
        async with superadmin_scope(session):
            row = (
                await session.execute(
                    text(
                        "SELECT status, source_type, yukti_status, yukti_failure_reason, "
                        "yukti_pre_score, yukti_profile_id, profile_id, evidence_tags_json, "
                        "match_score FROM job_candidate_links WHERE id = :l"
                    ),
                    {"l": link_id},
                )
            ).mappings().one()
    return dict(row)


@pytest.fixture
async def world():
    ids = await _seed()
    try:
        yield ids
    finally:
        await _drop(ids)


async def test_a_linked_candidate_is_read_from_the_resume_their_link_carries(
    world, monkeypatch
) -> None:
    router = Router()
    scored, payload = await _run(world, monkeypatch, router)

    row = await _link_row(world["link"])
    assert row["yukti_status"] == config.STATUS_SCORED
    assert row["yukti_profile_id"] == world["linked_profile"] == row["profile_id"]
    assert row["yukti_pre_score"] is not None
    assert row["evidence_tags_json"] and row["evidence_tags_json"][0]["kind"] == config.TAG_KIND_SKILL
    assert row["match_score"] is None, "the retired column is history and is never written"
    assert scored >= 1
    resumes = router.resumes()
    assert not any("PROFILE_TWO_MARKER" in r for r in resumes), (
        "retrieval ranked the candidate on their second resume, and that resume "
        "must never be read against an application made with the first"
    )
    assert payload["degraded"] is False and payload["degraded_reasons"] == []


async def test_one_person_is_read_once_however_many_resumes_they_have(world, monkeypatch) -> None:
    router = Router()
    await _run(world, monkeypatch, router)
    mine = [r for r in router.resumes() if "Senior data engineer" in r or "PROFILE_TWO" in r]
    assert len(mine) == 1


async def test_twice_malformed_output_is_not_assessed_and_committed(world, monkeypatch) -> None:
    router = Router(malformed=True)
    await _run(world, monkeypatch, router)
    row = await _link_row(world["link"])
    assert (row["yukti_status"], row["yukti_failure_reason"]) == (
        config.STATUS_NOT_ASSESSED,
        config.FAILURE_MODEL_OUTPUT_INVALID,
    )
    assert row["yukti_pre_score"] is None


async def test_an_outage_is_not_assessed_with_no_score_and_the_run_says_so(
    world, monkeypatch
) -> None:
    router = Router(fail=llm_router.LLMUnavailableError("down"))
    scored, payload = await _run(world, monkeypatch, router)
    row = await _link_row(world["link"])
    assert (row["yukti_status"], row["yukti_failure_reason"]) == (
        config.STATUS_NOT_ASSESSED,
        config.FAILURE_MODEL_UNAVAILABLE,
    )
    assert row["yukti_pre_score"] is None, "an outage writes no substitute score"
    assert payload["degraded"] is True
    assert any("could not be completed" in reason for reason in payload["degraded_reasons"])
    assert not any(ch.isdigit() for ch in matching.EMBEDDING_DEGRADED)


async def test_an_outage_after_a_good_reading_keeps_the_good_reading(world, monkeypatch) -> None:
    await _run(world, monkeypatch, Router())
    first = await _link_row(world["link"])
    assert first["yukti_status"] == config.STATUS_SCORED
    _, payload = await _run(world, monkeypatch, Router(fail=llm_router.LLMUnavailableError("down")))
    second = await _link_row(world["link"])
    assert second["yukti_status"] == config.STATUS_SCORED
    assert second["yukti_pre_score"] == first["yukti_pre_score"]
    assert any("earlier result" in reason for reason in payload["degraded_reasons"])


async def test_an_embedding_outage_skips_the_semantic_stage_and_says_why(
    world, monkeypatch
) -> None:
    from app.services.embeddings import EmbeddingError

    async def down(texts, *args, **kwargs):
        raise EmbeddingError("down")

    router = Router()
    monkeypatch.setattr(llm_router, "chat_completion", router)
    monkeypatch.setattr(matching, "embed", down)
    payloads: list[dict] = []
    progress = matching_progress.Progress(publish=payloads.append)

    async def work(session, job):
        return await matching.run_matching(session, job.id, progress=progress)

    await fx.run_as(world["world"], work, commit=False)
    payload = payloads[-1]
    stages = {s["key"]: s["status"] for s in payload["stages"]}
    assert stages["semantic_retrieval"] == matching_progress.STATUS_SKIPPED
    assert matching.EMBEDDING_DEGRADED in payload["degraded_reasons"]
    row = await _link_row(world["link"])
    assert row["yukti_status"] == config.STATUS_SCORED, "retrieval never decides who is read"


async def test_no_name_contact_employer_pay_or_history_reaches_the_prompt(
    world, monkeypatch
) -> None:
    router = Router()
    await _run(world, monkeypatch, router)
    assert router.raw, "the model was called"
    sent = "\n".join(router.raw)
    for forbidden in (
        NAME,
        "Raghunathan",
        EMAIL_LOCAL,
        "98450",
        EMPLOYER,
        PAY_SENTINEL,
        PAY_AMOUNT,
        "shortlisted",
        "success_pattern",
    ):
        assert forbidden not in sent, forbidden


async def test_a_job_whose_skills_were_never_saved_is_not_matched(monkeypatch) -> None:
    ids = await _seed(saved=False)
    try:
        router = Router()
        scored, payload = await _run(ids, monkeypatch, router)
        row = await _link_row(ids["link"])
    finally:
        await _drop(ids)
    assert scored == 0
    assert router.calls == []
    assert row["yukti_status"] == config.STATUS_PENDING
    understanding = next(s for s in payload["stages"] if s["key"] == "understanding")
    assert understanding["status"] == matching_progress.STATUS_SKIPPED
    assert understanding["detail"] == matching.SKILLS_NOT_SAVED


async def test_a_databank_candidate_is_linked_as_sourced_with_one_history_row(
    monkeypatch,
) -> None:
    ids = await _seed(databank=True)
    try:
        await _run(ids, monkeypatch, Router())
        async with fx.sessions()() as session:
            async with superadmin_scope(session):
                link = (
                    await session.execute(
                        text(
                            "SELECT id, status, source, source_type, current_stage, profile_id, "
                            "yukti_status FROM job_candidate_links "
                            "WHERE job_id = :j AND candidate_id = :c"
                        ),
                        {"j": ids["world"].job, "c": ids["databank_candidate"]},
                    )
                ).mappings().one()
                history = (
                    await session.execute(
                        text(
                            "SELECT status, set_by FROM pipeline_status "
                            "WHERE job_candidate_link_id = :l"
                        ),
                        {"l": link["id"]},
                    )
                ).all()
                updates = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM candidate_updates "
                            "WHERE job_candidate_link_id = :l"
                        ),
                        {"l": link["id"]},
                    )
                ).scalar()
    finally:
        await _drop(ids)
    assert link["status"] == "sourced", "found in a databank is not applying"
    assert (link["source"], link["source_type"]) == ("databank", "databank")
    assert link["current_stage"] == "Sourced, not yet applied"
    assert link["profile_id"] == ids["databank_profile"], "the main resume is the one read"
    assert link["yukti_status"] == config.STATUS_SCORED
    assert [(h.status, h.set_by) for h in history] == [("sourced", None)]
    assert updates == 0, "sourced is not an event the candidate caused"


async def test_an_archived_application_is_not_put_back_by_retrieval(world, monkeypatch) -> None:
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text("UPDATE job_candidate_links SET archived_at = now() WHERE id = :l"),
                    {"l": world["link"]},
                )
    router = Router()
    await _run(world, monkeypatch, router)
    row = await _link_row(world["link"])
    assert row["yukti_status"] == config.STATUS_PENDING
    assert not any("Senior data engineer" in r for r in router.resumes())
