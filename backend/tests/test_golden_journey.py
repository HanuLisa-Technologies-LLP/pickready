"""The golden end-to-end journey, against real Postgres (CONTRACT v4 item 1).

One customer, one job, two applicants (one assessed, one rival who only
applies), from an empty funded tenant to the recruiter reading the executive
profile, over the real routes, with exactly three services faked at their
boundaries: the model (at the router), the code execution provider
(`override_provider(FakeProvider())`) and speech to text
(`transcribe.run_transcription`), plus the in-memory object store standing in
for S3 as infrastructure. The sequence lives in `harness.golden_journey` so
this module and the harness scenario drive the same journey.

WHAT IT PROVES, GATE BY GATE: job setup to publish, two applications, AI
Matching, the batch invitation, questions written in every format, the
proctoring session and consent, the start that locks the contract, typed
prose, a spoken answer, a multiple choice, a fill-in-the-blank and a coding
Run and Submit, completion, the sandbox execution of the coding answer, Miti's
evaluation and Siddhi's report (neither templated), the proctoring report,
Yukti's re-rank putting the assessed candidate above the rival, and the
recruiter reading only words. Then, over the whole run: Vaada and Miti logged
the same contract digest, no number and no em dash reached any response, and
the spoken answer was stored under the bucket's own KMS key and deleted.

HOW IT DIFFERS FROM THE HARNESS SCENARIO
------------------------------------------
Both callers are REAL sessions: the staff user and the candidate are signed in
through `auth._issue_session`, the production minting path, and no dependency
is overridden, so a gate that passes here passes for a browser. And every gate
is judged the moment it is reached, from a SECOND CONNECTION, after the
request's transaction committed or rolled back (the 2026-09-20 lesson: a write
that answered 200 and vanished is invisible to the connection that made it).

A TIMESTAMP IS NOT EVIDENCE THAT WORK HAPPENED. Every gate asserts a row.

`tests/test_end_to_end_journey.py` is NOT superseded by this and stays: it
runs every situation type (this journey runs one) and asserts the provenance
ledger, the A2A contracts and the gate arithmetic row by row, none of which
this journey reaches over HTTP.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api import auth
from app.api.deps import ACCESS_COOKIE, REFRESH_COOKIE, SESSION_HINT_COOKIE
from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.core.security import AUDIENCE_CANDIDATE, AUDIENCE_ORG
from app.main import app
from app.models.user import User
from app.services import rating
from harness import golden_journey as journey
from harness import probes
from harness import world as harness_world
from harness.doubles.golden_model import GoldenModel


def _sessions() -> async_sessionmaker:
    return async_sessionmaker(
        create_async_engine(get_settings().database_url, poolclass=NullPool),
        expire_on_commit=False,
    )


async def _reachable() -> bool:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect():
            return True
    except OSError:
        return False
    finally:
        await engine.dispose()


def _cookie(response: Response, name: str) -> str:
    for raw in response.headers.getlist("set-cookie"):
        if raw.startswith(name + "="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"_issue_session set no {name} cookie")


async def _signed_in(user_id: uuid.UUID, audience: str) -> dict[str, str]:
    """The three cookies a browser holds after signing in, minted by the
    production path for a user the world seeded."""
    factory = _sessions()
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                user = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
    response = Response()
    await auth._issue_session(response, user, audience)  # noqa: SLF001
    return {
        ACCESS_COOKIE: _cookie(response, ACCESS_COOKIE),
        REFRESH_COOKIE: _cookie(response, REFRESH_COOKIE),
        SESSION_HINT_COOKIE: "1",
    }


class _RealClient:
    """One TestClient for the whole journey (one lifespan), the principal
    switched by swapping the cookie jar, never by an override."""

    def __init__(
        self, http: TestClient, staff: dict[str, str], candidates: dict[str, dict[str, str]]
    ):
        self._http = http
        self._staff = staff
        self._candidates = candidates
        self.bodies: list[tuple[str, str, Any]] = []

    def as_staff(self) -> None:
        self._http.cookies.clear()
        self._http.cookies.update(self._staff)

    def as_candidate(self, who: str = "candidate") -> None:
        self._http.cookies.clear()
        self._http.cookies.update(self._candidates[who])

    def call(self, step: str, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        response = self._http.request(method, path, **kwargs)
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text or None
        self.bodies.append((step, path, body))
        return response.status_code, body


# ── The gates, each judged from a second connection ─────────────────────────


def _order(page: dict[str, Any]) -> list[uuid.UUID]:
    return [uuid.UUID(str(row["link_id"])) for row in page["results"]]



async def _rows(sql: str, **params: Any) -> list[Any]:
    factory = _sessions()
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                return list((await session.execute(text(sql), params)).all())


async def _judge(name: str, state: journey.JourneyState) -> None:
    job = str(state.job)
    if name == "job_created":
        rows = await _rows("SELECT tenant_id, ratified_at FROM jobs WHERE id = :j", j=job)
        assert rows and rows[0].tenant_id == state.tenant and rows[0].ratified_at is None
    elif name == "jd_saved":
        rows = await _rows("SELECT jd_markdown FROM jobs WHERE id = :j", j=job)
        assert rows[0].jd_markdown.strip() == journey.JD_MARKDOWN.strip()
    elif name == "swot_saved":
        rows = await _rows(
            "SELECT weaknesses, version FROM job_swot_analyses WHERE job_id = :j", j=job
        )
        assert rows and rows[0].weaknesses == journey.SWOT["weaknesses"]
    elif name == "skills_drafted":
        rows = await _rows(
            "SELECT category, name FROM job_competencies WHERE job_id = :j AND is_active",
            j=job,
        )
        assert {row.category for row in rows} >= {"must_have", "behavioural"}
    elif name == "skills_saved":
        rows = await _rows(
            "SELECT framework_approved_at FROM jobs WHERE id = :j", j=job
        )
        assert rows[0].framework_approved_at is not None
        missing = await _rows(
            "SELECT name FROM job_competencies WHERE job_id = :j AND is_active "
            "AND (observable_evidence IS NULL OR btrim(observable_evidence) = '')",
            j=job,
        )
        assert not missing, f"saved skills without hidden context: {missing}"
    elif name == "job_published":
        rows = await _rows("SELECT ratified_at, posting_start_date FROM jobs WHERE id = :j", j=job)
        assert rows[0].ratified_at is not None and rows[0].posting_start_date is not None
    elif name == "applied":
        rows = await _rows(
            "SELECT job_id, candidate_id, status, validation_json FROM job_candidate_links "
            "WHERE id = :l",
            l=str(state.link),
        )
        assert rows and rows[0].candidate_id == state.candidate
        assert rows[0].status == "applied" and rows[0].validation_json
        assert not await _rows(
            "SELECT id FROM assessment_conversations WHERE job_candidate_link_id = :l",
            l=str(state.link),
        ), "applying created an assessment"
    elif name == "matched":
        rows = await _rows(
            "SELECT id, yukti_status, yukti_pre_score FROM job_candidate_links WHERE job_id = :j",
            j=job,
        )
        scored = {row.id: row for row in rows}
        assert set(scored) == {state.link, state.rival_link}
        assert all(row.yukti_status == "scored" for row in rows), rows
        # The rival reads better on the resume alone, so they are listed first
        # and the assessment has something to overturn.
        assert scored[state.rival_link].yukti_pre_score > scored[state.link].yukti_pre_score
        assert _order(state.responses["ranked_before"]) == [state.rival_link, state.link]
    elif name == "invited":
        rows = await _rows(
            "SELECT id, status FROM assessment_conversations WHERE job_candidate_link_id = :l",
            l=str(state.link),
        )
        assert len(rows) == 1 and rows[0].status == "active"
        state.conversation = rows[0].id
        link = await _rows("SELECT status FROM job_candidate_links WHERE id = :l", l=str(state.link))
        assert link[0].status == "assessment_invited"
    elif name == "questions_written":
        rows = await _rows(
            "SELECT question_type, generated_at FROM candidate_questions "
            "WHERE job_candidate_link_id = :l ORDER BY ordinal",
            l=str(state.link),
        )
        kinds = [row.question_type for row in rows]
        # Every format CONTRACT v4 item 1 names is in this one assessment, and
        # every question was WRITTEN (a templated row carries no generated_at).
        assert {"mcq_single", "fill_blank", "coding"} <= set(kinds), kinds
        assert {"evidence_based", "short_answer"} & set(kinds), kinds
        assert all(row.generated_at is not None for row in rows), "a question was templated"
        record = await _rows(
            "SELECT composition_json FROM assessment_conversations "
            "WHERE job_candidate_link_id = :l",
            l=str(state.link),
        )
        composition = record[0].composition_json
        assert composition["degraded"] == [] and composition["templated"] == [], composition
    elif name == "proctoring_opened":
        rows = await _rows(
            "SELECT outcome, face_descriptor_baseline IS NOT NULL AS has_baseline "
            "FROM proctoring_sessions WHERE job_candidate_link_id = :l",
            l=str(state.link),
        )
        assert len(rows) == 1 and rows[0].outcome == "active" and rows[0].has_baseline
        consent = await _rows(
            "SELECT c.consent_status FROM assessment_consents c "
            "JOIN assessment_conversations a ON a.id = c.conversation_id "
            "WHERE a.job_candidate_link_id = :l",
            l=str(state.link),
        )
        assert [row.consent_status for row in consent] == ["granted"]
    elif name == "started":
        rows = await _rows(
            "SELECT started_at, skill_snapshot_id, contract_digest, questions_contract_digest "
            "FROM assessment_conversations WHERE id = :c",
            c=str(state.conversation),
        )
        row = rows[0]
        assert row.started_at is not None and row.skill_snapshot_id is not None
        assert row.contract_digest == row.questions_contract_digest, (
            "the questions were not written against the contract the start locked"
        )
        link = await _rows("SELECT status FROM job_candidate_links WHERE id = :l", l=str(state.link))
        assert link[0].status == "assessment_in_progress"
    elif name == "voice_transcribed":
        rows = await _rows(
            "SELECT status, transcript_text, audio_deleted_at FROM voice_answers WHERE id = :v",
            v=str(state.responses["voice_id"]),
        )
        assert rows[0].status == "transcribed" and rows[0].audio_deleted_at is not None
        assert rows[0].transcript_text == journey.SPOKEN_ANSWER
    elif name == "coding_run":
        rows = await _rows(
            "SELECT count(*) AS n FROM coding_runs WHERE conversation_id = :c",
            c=str(state.conversation),
        )
        assert rows[0].n == 1
    elif name == "completed":
        rows = await _rows(
            "SELECT status, completed_at, credit_event FROM assessment_conversations WHERE id = :c",
            c=str(state.conversation),
        )
        assert rows[0].status == "completed" and rows[0].completed_at is not None
        assert rows[0].credit_event == "completed_assessment"
        answers = await _rows(
            "SELECT answer_json FROM assessment_answers WHERE conversation_id = :c",
            c=str(state.conversation),
        )
        inputs = [row.answer_json.get("input") for row in answers]
        assert "voice" in inputs, inputs
        assert state.answered.get("voice") == 1 and state.answered.get("typed", 0) >= 1
        assert state.answered.get("mcq") == 1 and state.answered.get("fill_blank") == 1
        assert state.answered.get("coding") == 1
    elif name == "coding_executed":
        rows = await _rows(
            "SELECT execution_status, review_status FROM coding_submissions "
            "WHERE conversation_id = :c",
            c=str(state.conversation),
        )
        assert [(row.execution_status, row.review_status) for row in rows] == [
            ("complete", "complete")
        ], rows
    elif name == "graded":
        rows = await _rows(
            "SELECT status, superseded_at, contract_digest FROM evaluations WHERE link_id = :l",
            l=str(state.link),
        )
        live = [row for row in rows if row.superseded_at is None]
        assert len(live) == 1 and live[0].status != "not_assessed", rows
    elif name == "reported":
        rows = await _rows(
            "SELECT id, overall_status, overall_score, scoring_mode, contract_digest, "
            "must_have_failed FROM functional_skills_reports WHERE job_candidate_link_id = :l",
            l=str(state.link),
        )
        assert len(rows) == 1, "exactly one PRISM Report for the application"
        report = rows[0]
        assert report.overall_score is not None and report.overall_status != "not_assessed"
        assert report.must_have_failed is False
        dimensions = await _rows(
            "SELECT category, name, assessment_status, remark FROM report_dimensions "
            "WHERE report_id = :r",
            r=str(report.id),
        )
        assert dimensions and all(row.assessment_status != "not_assessed" for row in dimensions)
        conversation = await _rows(
            "SELECT contract_digest FROM assessment_conversations WHERE id = :c",
            c=str(state.conversation),
        )
        assert report.contract_digest == conversation[0].contract_digest
        link = await _rows("SELECT status FROM job_candidate_links WHERE id = :l", l=str(state.link))
        assert link[0].status == "assessment_completed"
    elif name == "proctoring_reported":
        rows = await _rows(
            "SELECT r.id FROM proctoring_reports r JOIN proctoring_sessions s "
            "ON s.id = r.proctoring_session_id WHERE s.job_candidate_link_id = :l",
            l=str(state.link),
        )
        assert len(rows) == 1
    elif name == "reranked":
        before = state.responses["ranked_before"]
        after = state.responses["ranked_after"]
        # YUKTI RE-RANKS ON THE ASSESSMENT: the assessed candidate's overall is
        # blended over their resume reading and now lists them above the rival,
        # who still ranks on the resume alone. Nothing was stored to do it.
        assert _order(after) == [state.link, state.rival_link], after["results"]
        assert before["ranking_header"] != after["ranking_header"]
        rival = await _rows(
            "SELECT status FROM job_candidate_links WHERE id = :l", l=str(state.rival_link)
        )
        assert rival[0].status == "applied", "the rival was never invited"
    elif name == "profile_read":
        # THE EXECUTIVE PROFILE IS THE PRISM REPORT (2026-09-04), read by the
        # recruiter who invited the candidate, and it states WORDS: every grade
        # is one of the four, every remark is prose, and the proctoring report
        # travels inside it as its last section.
        profile = state.responses["executive_profile"]
        assert profile["overall_grade"] in rating.GRADES, profile["overall_grade"]
        items = profile["must_have"] + profile["nice_to_have"] + profile["behavioural"]
        assert profile["must_have"] and profile["behavioural"], "a PRISM section is empty"
        assert all(item["grade"] in rating.GRADES for item in items), [i["grade"] for i in items]
        assert all(item["remark"].strip() for item in items)
        proctoring = profile["proctoring"]
        assert proctoring, "the executive profile carries no proctoring section"
        assert proctoring == state.responses["proctoring_report"], (
            "the PRISM Report and the proctoring route disagree about the same report"
        )
        report = await _rows(
            "SELECT model_id, generation_provenance_json FROM functional_skills_reports "
            "WHERE job_candidate_link_id = :l",
            l=str(state.link),
        )
        # WRITTEN, not templated: a report no model wrote carries NULL here,
        # and a template that stood in for a remark is named in the provenance.
        assert report[0].model_id is not None
        provenance = report[0].generation_provenance_json or {}
        assert not provenance.get("templates"), provenance
        transcript = state.responses["transcript"]
        assert journey.SPOKEN_ANSWER in str(transcript), "the spoken answer is not in the transcript"
    else:
        raise AssertionError(f"no judge for gate {name!r}")


def test_the_golden_journey() -> None:
    if not asyncio.run(_reachable()):
        pytest.skip("no database reachable")
    sessions = harness_world.session_factory()
    world = asyncio.run(harness_world.build("golden_journey_ready", {}, sessions=sessions))
    reached: list[str] = []

    model = GoldenModel()

    def gate(name: str, state: journey.JourneyState) -> None:
        try:
            asyncio.run(_judge(name, state))
        except AssertionError as exc:
            # A gate that fails because a caller degraded names the model call
            # nobody scripted, which is almost always the reason.
            raise AssertionError(
                f"gate {name!r} failed: {exc}; unscripted model calls so far: "
                f"{sorted(set(model.unscripted))}"
            ) from exc
        reached.append(name)

    try:
        staff = asyncio.run(_signed_in(world.id("staff"), AUDIENCE_ORG))
        candidates = {
            who: asyncio.run(_signed_in(world.id(f"{who}_user"), AUDIENCE_CANDIDATE))
            for who in ("candidate", "rival")
        }
        state = journey.JourneyState(
            tenant=world.id("tenant"),
            staff=world.id("staff"),
            candidate=world.id("candidate"),
            rival=world.id("rival"),
        )
        with (
            TestClient(app) as http,
            model.installed(),
            journey.deployment() as deployment,
            journey.digest_lines() as digests,
        ):
            client = _RealClient(http, staff, candidates)
            journey.drive(client, state, gate)
            installed = deployment.store
        assert reached == list(journey.GATES)
        assert model.unscripted == [], f"a model call nobody scripted: {model.unscripted}"
        # THE DIGEST PAIR: Vaada logged the contract it locked at the start and
        # Miti the one it graded against. One conversation, one digest.
        by_stage = {stage: (conversation, digest) for stage, conversation, digest in digests}
        assert set(by_stage) == {"vaada", "miti"}, digests
        assert by_stage["vaada"] == by_stage["miti"]
        assert by_stage["vaada"][0] == str(state.conversation)
        # RULE 1 AND RULE 7 OVER EVERY PAYLOAD THE JOURNEY RECEIVED, recruiter
        # and candidate alike, judged by the harness's own probes so the suite
        # and the scenario cannot disagree about what a number is.
        received = [(path, body) for _step, path, body in client.bodies]
        assert received, "the journey recorded no response"
        numbers_seen = probes.number_hits(received)
        assert numbers_seen == [], "; ".join(numbers_seen)
        dashes_seen = probes.em_dash_hits(received)
        assert dashes_seen == [], "; ".join(dashes_seen)
        # The spoken answer was stored under the bucket policy's own key, and
        # deleted once transcribed (the gate read the row; this reads the store).
        voice_keys = [key for key in installed.encryption if key.startswith("voice-answers/")]
        assert voice_keys and all(
            installed.encryption[key] == ("aws:kms", "golden-journey-key") for key in voice_keys
        ), installed.encryption
        assert not [key for key in installed.objects if key.startswith("voice-answers/")]
    finally:
        asyncio.run(harness_world.teardown(world, sessions=sessions))
