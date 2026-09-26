"""Siddhi's surface against a REAL database, read back from a SECOND connection.

What the grading orchestrator (WP5-D) writes and what the report API (WP5-F)
reads meet in one place: `functional_skills_reports.gap_analysis_json["siddhi"]`
and `review_dispositions`. Everything here is WRITTEN AND COMMITTED through one
engine and READ through another, under the tenant's own RLS session, because a
value that answered correctly inside the writing transaction and did not
survive the commit (or the JSONB round trip) is invisible to any assertion made
before the commit, and this repository has shipped exactly that.

  * the composed namespace survives JSONB: the trail reads back, withheld
    statements stay withheld and are named nowhere in the stored row, the
    support verdicts (including a passage found elsewhere) come back intact,
    and the quality gate reads its grades out of the STORED record;
  * `resolve_evidence` resolves locators only inside the report's own
    application, under the tenant's RLS session: another application's
    message, another source's chunk and another tenant's chunk all resolve to
    nothing;
  * G4 reads the committed dispositions: none blocks, one recorded BEFORE the
    report was written does not clear it, one on another application does not
    clear it, one recorded after it does.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.services.siddhi import delivery, quality_gate, support, trail
from app.services.siddhi import report as siddhi_report

pytestmark = pytest.mark.asyncio

SECRET = "an uncitable sentence nobody may read"
ANSWER = (
    "I moved the orders service onto the new cluster over two sprints and "
    "wrote the rollback runbook myself."
)
ELSEWHERE = (
    "Later I ran the Kubernetes upgrade for payments and handled the on-call "
    "rota during the cutover."
)
REMARK = "They led the Kubernetes upgrade for payments."


async def _engine_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except OSError:
        await engine.dispose()
        pytest.skip("no database reachable")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _Fx:
    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.other_tenant_id = uuid.uuid4()
        self.job_id = uuid.uuid4()
        self.other_job_id = uuid.uuid4()
        self.decider_id = uuid.uuid4()
        self.links = {name: uuid.uuid4() for name in ("mine", "theirs", "elsewhere")}
        self.candidates = {name: uuid.uuid4() for name in self.links}
        self.conversations = {name: uuid.uuid4() for name in ("mine", "theirs")}
        self.messages = {name: uuid.uuid4() for name in ("mine", "theirs")}
        self.question_id = uuid.uuid4()
        self.chunks = {
            name: uuid.uuid4() for name in ("mine", "theirs", "other_tenant")
        }
        self.report_id = uuid.uuid4()
        self.written_at = datetime.now(timezone.utc)


def _chunk(chunk_id, tenant_id, source_id, content, *, ordinal=0):
    from app.models.context import ContextChunk

    return ContextChunk(
        id=chunk_id,
        tenant_id=tenant_id,
        source_type="assessment",
        source_id=source_id,
        source_version="v1",
        section_type="answer",
        ordinal=ordinal,
        content=content,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        updated_at=datetime.now(timezone.utc),
    )


async def _seed(factory, fx: _Fx) -> None:
    from app.core.db import superadmin_scope
    from app.models import Candidate, Job, JobStatus, LinkSource, Role, Tenant, User
    from app.models.assessment import AssessmentConversation, AssessmentMessage
    from app.models.candidate import JobCandidateLink
    from app.models.enums import UserStatus

    now = fx.written_at
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                for tenant in (fx.tenant_id, fx.other_tenant_id):
                    s.add(Tenant(id=tenant, name=f"Siddhi {tenant.hex[:6]}",
                                 domain=f"{tenant}.siddhi.test"))
                await s.flush()
                s.add(User(id=fx.decider_id, email=f"decider-{fx.decider_id.hex[:8]}@siddhi.test",
                           role=Role.client, tenant_id=fx.tenant_id,
                           full_name="Integrity Reviewer", status=UserStatus.active))
                for job, tenant in ((fx.job_id, fx.tenant_id), (fx.other_job_id, fx.other_tenant_id)):
                    s.add(Job(id=job, tenant_id=tenant, title="Engineer", jd_json={},
                              status=JobStatus.ratified, ratified_at=now,
                              assessment_status="ready_for_candidates",
                              assessment_grade="non_managerial"))
                for name in fx.links:
                    s.add(Candidate(id=fx.candidates[name],
                                    email=f"{name}{fx.candidates[name].hex[:8]}@siddhi.test",
                                    full_name=f"Candidate {name}", consent_databank=False))
                await s.flush()
                for name, link in fx.links.items():
                    tenant = fx.other_tenant_id if name == "elsewhere" else fx.tenant_id
                    job = fx.other_job_id if name == "elsewhere" else fx.job_id
                    s.add(JobCandidateLink(id=link, tenant_id=tenant, job_id=job,
                                           candidate_id=fx.candidates[name],
                                           source=LinkSource.fresh, status="applied"))
                await s.flush()
                for name in fx.conversations:
                    s.add(AssessmentConversation(
                        id=fx.conversations[name], tenant_id=fx.tenant_id, job_id=fx.job_id,
                        job_candidate_link_id=fx.links[name], grade="non_managerial",
                        status="completed", next_question_index=0, started_at=now,
                    ))
                await s.flush()
                for name in fx.messages:
                    s.add(AssessmentMessage(
                        id=fx.messages[name], tenant_id=fx.tenant_id,
                        conversation_id=fx.conversations[name], ordinal=2,
                        speaker="candidate", domain="technical", question_key="q",
                        content=f"{name}: {ANSWER}",
                    ))
                s.add(_chunk(fx.chunks["mine"], fx.tenant_id, fx.links["mine"], ELSEWHERE))
                s.add(_chunk(fx.chunks["theirs"], fx.tenant_id, fx.links["theirs"],
                             "theirs: " + ELSEWHERE))
                # Another tenant's chunk that names MY application as its source:
                # only RLS stands between it and my report's citation view.
                s.add(_chunk(fx.chunks["other_tenant"], fx.other_tenant_id,
                             fx.links["mine"], "other tenant: " + ELSEWHERE, ordinal=1))
                await s.flush()


async def _cleanup(factory, fx: _Fx) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(text("DELETE FROM review_dispositions WHERE tenant_id = :t"),
                                {"t": str(fx.tenant_id)})
                for tenant in (fx.tenant_id, fx.other_tenant_id):
                    await s.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": str(tenant)})
                await s.execute(text("DELETE FROM candidates WHERE id = ANY(:ids)"),
                                {"ids": [str(value) for value in fx.candidates.values()]})
                await s.execute(text("DELETE FROM users WHERE id = :u"), {"u": str(fx.decider_id)})


def _rows() -> list[dict]:
    return [
        {"category": "must_have", "name": "Distributed Systems", "grade": "Matching",
         "remark": REMARK, "remark_provenance": "model"},
        {"category": "behavioural", "name": "Judgement under pressure", "grade": "Highly Matching",
         "remark": "Called the rollback first on the orders move.", "remark_provenance": "template"},
    ]


def _uncited_groups() -> list[dict]:
    return [{
        "category": None, "label": "Unfiled",
        "items": [{"name": "Not On This Report", "grade": "Not Matching", "remark": None,
                   "probes": [SECRET], "probes_source": "model"}],
        "no_gaps_statement": None, "cap_statement": None,
    }]


class _Piece:
    def __init__(self, chunk_id: uuid.UUID, content: str) -> None:
        self.chunk_id, self.content = chunk_id, content

    @property
    def locator(self) -> str:
        return f"context_chunks:{self.chunk_id}"


class _Passages:
    def __init__(self, pieces=(), degraded=False, reason=None) -> None:
        self.pieces, self.degraded, self.reason = tuple(pieces), degraded, reason


async def _compose_and_commit(factory, fx: _Fx) -> dict:
    """Compose exactly what the orchestrator would, and commit it as a report."""
    from app.core.db import superadmin_scope
    from app.models.assessment import FunctionalSkillsReport

    async def _source(statement: str):
        if statement == REMARK:
            return _Passages([_Piece(fx.chunks["mine"], ELSEWHERE)])
        return _Passages()

    exchanges = {
        "Distributed Systems": [{
            "question": "A migration you owned?", "answer": ANSWER,
            "question_id": str(fx.question_id), "message_ids": [str(fx.messages["mine"])],
        }],
        # A locator pointing at ANOTHER application's message: stored, and
        # never resolved for this report.
        "Judgement under pressure": [{
            "question": "A hard call?", "answer": ANSWER,
            "question_id": None, "message_ids": [str(fx.messages["theirs"])],
        }],
    }
    composed = await siddhi_report.compose_prism(
        dimensions=_rows(),
        evidence_by_item=exchanges,
        gap_groups=_uncited_groups(),
        overall_summary="Owned the orders move and the rollback runbook.",
        overall_grade="Matching",
        embed=None,
        passage_source=_source,
    )
    gap_analysis_json = {"groups": _uncited_groups(), "siddhi": composed.siddhi_namespace()}
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(FunctionalSkillsReport(
                    id=fx.report_id, tenant_id=fx.tenant_id, job_id=fx.job_id,
                    job_candidate_link_id=fx.links["mine"], grade="non_managerial",
                    overall_summary="Owned the orders move and the rollback runbook.",
                    validation_json={}, synthesized_at=fx.written_at,
                    gap_analysis_json=gap_analysis_json,
                    needs_human_review=composed.needs_human_review,
                    review_findings_json=composed.review_findings(),
                ))
    return {"composed": composed}


async def _read_report(second_factory, fx: _Fx):
    """The committed row, through the TENANT's RLS session on a new engine."""
    from app.core.db import tenant_scope
    from app.models.assessment import FunctionalSkillsReport

    async with second_factory() as s:
        async with s.begin():
            async with tenant_scope(s, fx.tenant_id):
                return (
                    await s.execute(
                        select(FunctionalSkillsReport).where(
                            FunctionalSkillsReport.id == fx.report_id
                        )
                    )
                ).scalars().one()


async def test_the_composed_namespace_survives_the_commit_and_reads_back_whole() -> None:
    engine, factory = await _engine_or_skip()
    second, second_factory = await _engine_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        await _compose_and_commit(factory, fx)
        row = await _read_report(second_factory, fx)

        # THE WITHHELD SENTENCE IS NOWHERE IN THE STORED ROW, in any column.
        stored_blob = json.dumps(
            {"gap": row.gap_analysis_json, "findings": row.review_findings_json}
        )
        siddhi_blob = json.dumps(row.gap_analysis_json["siddhi"])
        assert SECRET not in siddhi_blob
        assert SECRET not in json.dumps(row.review_findings_json)
        # (The raw group the caller stored still carries it: withholding is
        # Siddhi's, and what the section stores beside it is the caller's. The
        # trail, the findings and every rendered statement are the reader's.)
        assert SECRET in stored_blob

        read = trail.read_trail(row.gap_analysis_json)
        assert read.available and read.version == siddhi_report.TRAIL_VERSION
        assert all(SECRET not in statement.text for statement in read.statements)
        # The unrated item's grade line and its probe: both uncitable, both
        # withheld, reported by place and kind and never by sentence.
        assert {(held["kind"], held["item"], held["problem"]) for held in read.withheld} == {
            ("grade", "Not On This Report", "no_citation"),
            ("probe", "Not On This Report", "no_citation"),
        }
        assert row.needs_human_review is True
        issues = {finding["issue"] for finding in row.review_findings_json}
        assert {"uncited_statement", "template_output"} <= issues
        assert "citation_unsupported" not in issues  # rescued elsewhere, not unsupported

        remark = read.statement_for("must_have", "Distributed Systems")
        assert remark.support_level == support.LEVEL_WEAK
        assert remark.support_reason == support.REASON_ELSEWHERE
        assert remark.support_passages == (f"context_chunks:{fx.chunks['mine']}",)
        assert remark.passage_check == support.PASSAGE_FOUND

        # The quality gate reads its grades out of the STORED record.
        verdict = quality_gate.evaluate(
            gap_analysis_json=row.gap_analysis_json,
            dimensions=_rows(),
            overall_summary=row.overall_summary,
            validation={},
            validation_source={},
            evidence_by_item={},
            miti_grades={"Distributed Systems": "Not Matching",
                         "Judgement under pressure": "Highly Matching"},
            miti_overall_grade="Matching",
        )
        assert "grade_disagrees_with_scoring" in {f.issue for f in verdict.findings}
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
        await second.dispose()


async def test_resolution_is_scoped_to_this_application_under_the_tenants_rls() -> None:
    from app.core.db import tenant_scope

    engine, factory = await _engine_or_skip()
    second, second_factory = await _engine_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        await _compose_and_commit(factory, fx)
        row = await _read_report(second_factory, fx)
        read = trail.read_trail(row.gap_analysis_json)

        # Plant a locator to the other application's chunk and to the other
        # tenant's chunk beside the real one, as a corrupted or hostile trail
        # would: none of them may resolve.
        planted = trail.CitationTrail(
            available=True,
            version=read.version,
            statements=(
                *read.statements,
                trail.TrailStatement(
                    section="must_have", kind="finding", item="Distributed Systems",
                    text="planted", refs=(), support_level=support.LEVEL_WEAK,
                    support_reason=support.REASON_ELSEWHERE,
                    support_passages=(
                        f"context_chunks:{fx.chunks['theirs']}",
                        f"context_chunks:{fx.chunks['other_tenant']}",
                    ),
                ),
            ),
            nodes=read.nodes,
            withheld=read.withheld,
        )
        async with second_factory() as s:
            async with s.begin():
                async with tenant_scope(s, fx.tenant_id):
                    resolved = await trail.resolve_evidence(
                        s, planted, link_id=fx.links["mine"],
                        chunk_source_ids=(fx.links["mine"],),
                    )
        mine = resolved[f"context_chunks:{fx.chunks['mine']}"]
        assert mine.excerpt == ELSEWHERE
        assert resolved[f"context_chunks:{fx.chunks['theirs']}"].excerpt is None
        assert resolved[f"context_chunks:{fx.chunks['other_tenant']}"].excerpt is None

        answers = {
            ref: value for ref, value in resolved.items()
            if value.kind == "answer" and value.excerpt is not None
        }
        assert list(answers.values())[0].excerpt.startswith("mine:")
        assert all(not value.excerpt.startswith("theirs:") for value in answers.values())

        shaped = trail.view(planted, resolved)
        blob = json.dumps(shaped)
        for secret in (SECRET, "theirs:", "other tenant:", str(fx.chunks["mine"]),
                       str(fx.messages["mine"]), "context_chunks", "assessment_messages"):
            assert secret not in blob, secret
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
        await second.dispose()


async def _add_disposition(factory, fx: _Fx, *, link: str, created_at: datetime | None) -> None:
    from app.core.db import superadmin_scope
    from app.models.hiring import ReviewDisposition

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                fields = dict(tenant_id=fx.tenant_id, job_id=fx.job_id,
                              link_id=fx.links[link], disposition="rejected",
                              decided_by=fx.decider_id)
                if created_at is not None:
                    fields["created_at"] = created_at
                s.add(ReviewDisposition(**fields))


async def _gate(second_factory, fx: _Fx):
    from app.core.db import tenant_scope

    row = await _read_report(second_factory, fx)
    async with second_factory() as s:
        async with s.begin():
            async with tenant_scope(s, fx.tenant_id):
                return await delivery.clearance_or_reason(s, row)


async def test_g4_reads_the_committed_dispositions_and_only_a_later_one_clears() -> None:
    engine, factory = await _engine_or_skip()
    second, second_factory = await _engine_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        await _compose_and_commit(factory, fx)

        clearance, reason = await _gate(second_factory, fx)
        assert clearance is None and reason == delivery.PDF_BLOCKED_REASON

        # A decision about ANOTHER application clears nothing here.
        await _add_disposition(factory, fx, link="theirs", created_at=None)
        clearance, reason = await _gate(second_factory, fx)
        assert clearance is None and reason == delivery.PDF_BLOCKED_REASON

        # A decision recorded BEFORE this report existed was about something
        # else (an earlier evaluation), so nobody has read this report yet.
        await _add_disposition(
            factory, fx, link="mine", created_at=fx.written_at - timedelta(hours=1)
        )
        clearance, reason = await _gate(second_factory, fx)
        assert clearance is None and reason == delivery.PDF_BLOCKED_REASON

        # A decision after it clears, whatever it decided: G4 asks whether a
        # human decided, not whether they approved.
        await _add_disposition(
            factory, fx, link="mine", created_at=fx.written_at + timedelta(minutes=5)
        )
        clearance, reason = await _gate(second_factory, fx)
        assert reason is None
        assert clearance.needed_review is True
        assert clearance.disposition == "rejected"
        assert clearance.as_dict()["decided_by"] == str(fx.decider_id)
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
        await second.dispose()


async def test_an_unflagged_report_clears_without_a_disposition_and_a_linkless_one_refuses() -> None:
    from types import SimpleNamespace

    clearance = await delivery.gate_delivery(
        None, SimpleNamespace(needs_human_review=False, job_candidate_link_id=None, id=None)
    )
    assert clearance.needed_review is False and clearance.disposition is None
    with pytest.raises(ValueError):
        await delivery.gate_delivery(
            None, SimpleNamespace(needs_human_review=True, job_candidate_link_id=None, id=None)
        )
    with pytest.raises(TypeError):
        delivery.DeliveryClearance(needed_review=False)


async def test_the_one_read_call_serves_the_committed_trail_words_only() -> None:
    """`trail.citation_view` is what the report API serves: read, resolve,
    shape, through the tenant's session, from the committed row."""
    from app.core.db import tenant_scope

    engine, factory = await _engine_or_skip()
    second, second_factory = await _engine_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        await _compose_and_commit(factory, fx)
        row = await _read_report(second_factory, fx)
        async with second_factory() as s:
            async with s.begin():
                async with tenant_scope(s, fx.tenant_id):
                    shaped = await trail.citation_view(
                        s, row.gap_analysis_json, link_id=fx.links["mine"],
                        chunk_source_ids=(fx.links["mine"],),
                    )
                    empty = await trail.citation_view(
                        s, {"groups": []}, link_id=fx.links["mine"], chunk_source_ids=(),
                    )
        assert empty == {"trail_available": False, "statements": []}
        assert shaped["trail_available"] is True
        [remark] = [
            entry for entry in shaped["statements"]
            if entry["item"] == "Distributed Systems" and entry["kind"] == "finding"
        ]
        assert remark["support"] == support.SUPPORT_NOTES[support.REASON_ELSEWHERE]
        kinds = [entry["kind"] for entry in remark["evidence"]]
        assert kinds[-1] == trail.EVIDENCE_KIND_WORDS[trail.SUPPORTING_PASSAGE]
        assert remark["evidence"][-1]["excerpt"] == ELSEWHERE
        answer = next(e for e in remark["evidence"] if e["kind"] == "The candidate's answer")
        assert answer["excerpt"].startswith("mine:")
        blob = json.dumps(shaped)
        assert SECRET not in blob
        for identifier in (*fx.messages.values(), *fx.chunks.values(), fx.question_id):
            assert str(identifier) not in blob
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
        await second.dispose()
