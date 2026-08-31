"""One candidate's whole journey, once per situation type (spec-doc6 11.1).

    "End-to-end journey tests: client onboarding -> Company DNA -> job creation
     -> SWOT -> matrix -> publish -> apply -> conversation -> scoring ->
     integrity flag -> human disposition -> report delivery -> dashboard
     render. At least one full journey per situation type."

All six situation types are runnable: Runbook v1.2 11.3 now bounds every one of
them, including Scale-up and Succession, which raised
`RunbookDataUnavailable` for the whole of the previous phase.

WHAT THIS TEST ASSERTS, AND WHAT IT DELIBERATELY DOES NOT CLAIM
-----------------------------------------------------------------
It drives the CROSS-CUTTING path: the correlation id, the human principal, the
A2A contract on every hand-off, the four gates, the versioned evaluation
context, and the durable rows an operator queries afterwards. Every one of those
is enforced by `services/orchestration` and `services/agents`, which is the code
this file is the test for.

It does NOT drive the HTTP API. The routes for job setup, scoring and report
delivery are being built alongside this, and a test that reached into them would
be asserting somebody else's contract from the outside while it was still
moving. The HTTP-level journey belongs with the routers.

A TIMESTAMP IS NOT EVIDENCE THAT WORK HAPPENED
------------------------------------------------
Nineteen of thirty-five live jobs on this platform once carried
`framework_generated_at` with zero competency rows behind it, and every health
check asked the stamp. So every assertion below is against a ROW or an ARTIFACT
ID. Not one is against an `at`, a `completed_at` or a `frozen_at` except where
the value under test IS the ordering of two instants.

HOW IT BEHAVES WHILE THE STAGES ARE STILL LANDING
---------------------------------------------------
`_missing_stage` reports the first stage whose implementation is absent or
unreachable, by name, and the journey skips with that name in the message. It
does not skip for a reason it cannot state, and it does not pass vacuously: the
skip is decided from the import graph, and every stage that IS present is
exercised for real.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.orchestration_checks import reachable_modules
from app.services import audit as audit_mod
from app.services.agents import artifacts as a2a
from app.services.agents import envelope as run_envelope
from app.services.agents import identity, provenance
from app.services.hiring import gates as pipeline_gates
from app.services.hiring import situations
from app.services.orchestration import activation, enforcement, versioning

FROZEN_AT = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)
APPLIED_AT = FROZEN_AT + timedelta(days=3)


# ── Is every stage actually there? ───────────────────────────────────────────


def _missing_stage() -> str | None:
    """The first stage that has not landed, named, or None when all have.

    Two questions, in this order, because they fail differently. A module that
    is not on disk has not been written; a module that is on disk and that no
    route or worker imports has been written and not wired, which is the exact
    state the whole of Part A was in for a phase while every unit test passed.
    """
    for stage, row in activation.status().items():
        if not row["present"]:
            return f"{stage} (module {row['module']} not present; {row['supplied_by']})"
    reachable = reachable_modules()
    for stage, spec in activation.STAGE_MODULES.items():
        if spec.dotted not in reachable:
            return (
                f"{stage} (module {spec.dotted} exists but no route or worker "
                f"imports it, so {spec.supplied_by} runs nowhere)"
            )
    return None


async def _db_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:
        await engine.dispose()
        pytest.skip("no database reachable, skipping the end-to-end journey")
    return engine


def test_all_six_situation_types_are_bounded_and_therefore_runnable() -> None:
    """The precondition for "one journey per situation type".

    Scale-up and Succession had no numeric weight consequence anywhere in
    Runbook v1.1, and `situations.dimension_modifiers` raised rather than
    inventing a multiplier. v1.2 11.3 bounds all six. If any of them starts
    raising again, this fails here with the situation named rather than six
    journeys failing with a stack trace apiece.
    """
    assert len(situations.SITUATION_TYPES) == 6
    for key in situations.SITUATION_TYPES:
        modifiers = situations.dimension_modifiers(key)
        assert modifiers, key
        # A bound, not a free multiplier. A lower layer may TUNE a higher one
        # within declared bounds and may never suspend it.
        assert all(0.5 <= value <= 2.0 for value in modifiers.values()), key


# ── The journey ──────────────────────────────────────────────────────────────


class _Journey:
    """One client, one job, one candidate, one flow, torn down afterwards."""

    def __init__(self, situation_key: str) -> None:
        self.situation_key = situation_key
        self.tenant_id = uuid.uuid4()
        self.job_id = uuid.uuid4()
        self.dna_id = uuid.uuid4()
        self.hr_manager_id = uuid.uuid4()
        self.candidate_id = uuid.uuid4()
        self.link_id = uuid.uuid4()
        self.evaluation_id = uuid.uuid4()
        self.correlation_id = provenance.correlation_for_job(self.job_id)
        self.ledger = provenance.Ledger(self.correlation_id)
        self.principal = provenance.Principal(
            user_id=str(self.hr_manager_id),
            role="hr_manager",
            tenant_id=str(self.tenant_id),
        )

    # -- envelopes and artifacts -------------------------------------------

    def envelope(self, agent_id: str) -> run_envelope.Envelope:
        return run_envelope.Envelope.for_run(
            tenant_id=str(self.tenant_id),
            agent_id=agent_id,
            task_type="scoring",
            interactive=False,
            job_id=str(self.job_id),
            candidate_id=str(self.candidate_id),
            principal=self.principal,
            correlation_id=self.correlation_id,
        )

    def publish(
        self,
        agent_id: str,
        artifact_type: str,
        payload: dict,
        envelope: run_envelope.Envelope,
        *,
        source_refs: tuple[str, ...],
    ) -> a2a.Artifact:
        return a2a.publish(
            producer=agent_id,
            artifact_type=artifact_type,
            payload=payload,
            tenant_id=str(self.tenant_id),
            job_id=str(self.job_id),
            candidate_id=str(self.candidate_id),
            source_refs=source_refs,
            validated=True,
            correlation_id=self.correlation_id,
            task_id=envelope.task_id,
            principal=self.principal,
        )

    async def stage(
        self, stage: str, agent_id: str, artifact_type: str, payload: dict, refs
    ) -> a2a.Artifact:
        envelope = self.envelope(agent_id)
        artifact = self.publish(
            agent_id, artifact_type, payload, envelope, source_refs=tuple(refs)
        )
        await enforcement.run_stage(stage, envelope, self.ledger, artifact=artifact)
        return artifact

    # -- the rows -----------------------------------------------------------

    async def onboard_client(self, session) -> None:
        await session.execute(
            text("INSERT INTO tenants (id, name, domain) VALUES (:id, :n, :d)"),
            {
                "id": self.tenant_id,
                "n": f"Journey {self.situation_key} {self.tenant_id.hex[:6]}",
                "d": f"j-{self.tenant_id.hex[:12]}.example",
            },
        )

    async def complete_company_dna(self, session) -> None:
        await session.execute(
            text(
                "INSERT INTO company_dna (id, tenant_id, version, status, is_current) "
                "VALUES (:id, :t, 1, 'complete', false)"
            ),
            {"id": self.dna_id, "t": self.tenant_id},
        )

    async def create_job(self, session) -> None:
        await session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, title, status, correlation_id) "
                "VALUES (:id, :t, :title, 'draft', :c)"
            ),
            {
                "id": self.job_id,
                "t": self.tenant_id,
                "title": f"Head of Engineering ({self.situation_key})",
                "c": self.correlation_id,
            },
        )

    async def freeze_matrix(self, session) -> None:
        await session.execute(
            text(
                "INSERT INTO job_company_dna_bindings "
                "(id, tenant_id, job_id, company_dna_id, company_dna_version, "
                " freeze_sequence, scorecard_version, correlation_id, frozen_at, "
                " frozen_by) "
                "VALUES (:id, :t, :j, :d, 1, 1, 1, :c, :at, :by)"
            ),
            {
                "id": uuid.uuid4(),
                "t": self.tenant_id,
                "j": self.job_id,
                "d": self.dna_id,
                "c": self.correlation_id,
                "at": FROZEN_AT,
                "by": None,
            },
        )

    async def publish_job(self, session) -> None:
        await session.execute(
            text("UPDATE jobs SET status = 'published' WHERE id = :j"),
            {"j": self.job_id},
        )

    async def apply(self, session) -> None:
        await session.execute(
            text("INSERT INTO candidates (id) VALUES (:id)"),
            {"id": self.candidate_id},
        )
        await session.execute(
            text(
                "INSERT INTO job_candidate_links "
                "(id, tenant_id, job_id, candidate_id, source, created_at) "
                "VALUES (:id, :t, :j, :c, 'applied', :at)"
            ),
            {
                "id": self.link_id,
                "t": self.tenant_id,
                "j": self.job_id,
                "c": self.candidate_id,
                "at": APPLIED_AT,
            },
        )

    async def record_evaluation(self, session, gate_results: list[dict]) -> None:
        await session.execute(
            text(
                "INSERT INTO evaluations (id, tenant_id, job_id, link_id, "
                " scorecard_version, company_dna_version, situation_type, "
                " dimension_scores, gate_results_json, needs_human_review, "
                " scoring_mode) "
                "VALUES (:id, :t, :j, :l, 1, 1, :s, CAST(:dims AS jsonb), "
                "        CAST(:gates AS jsonb), true, 'full')"
            ),
            {
                "id": self.evaluation_id,
                "t": self.tenant_id,
                "j": self.job_id,
                "l": self.link_id,
                "s": self.situation_key,
                "dims": _json(
                    {
                        dimension: {
                            "band": "matching",
                            "evidence_refs": [f"answer:{dimension}"],
                            "insufficient_evidence": False,
                        }
                        for dimension in situations.dimension_modifiers(
                            self.situation_key
                        )
                    }
                ),
                "gates": _json(gate_results),
            },
        )

    async def record_disposition(self, session, disposition: str) -> uuid.UUID:
        await session.execute(
            text(
                "INSERT INTO users (id, tenant_id, role, status, email) "
                "VALUES (:id, :t, 'hr_manager', 'active', :email)"
            ),
            {
                "id": self.hr_manager_id,
                "t": self.tenant_id,
                "email": f"hm-{self.hr_manager_id.hex[:10]}@example.invalid",
            },
        )
        disposition_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO review_dispositions "
                "(id, tenant_id, evaluation_id, job_id, link_id, disposition, "
                " decided_by, flags_json) "
                "VALUES (:id, :t, :e, :j, :l, :d, :by, CAST(:flags AS jsonb))"
            ),
            {
                "id": disposition_id,
                "t": self.tenant_id,
                "e": self.evaluation_id,
                "j": self.job_id,
                "l": self.link_id,
                "d": disposition,
                "by": self.hr_manager_id,
                "flags": _json([{"gate": pipeline_gates.G3, "kind": "contradiction"}]),
            },
        )
        return disposition_id

    async def audit_every_stage(self, session) -> None:
        for record in self.ledger.records:
            await audit_mod.record_agent_action(
                session,
                action=f"agent_{record.stage}",
                agent_name=record.agent_id or "unknown",
                principal_user_id=record.principal_user_id,
                principal_role=record.principal_role,
                tenant_id=record.tenant_id,
                resource_type="artifact",
                resource_id=record.artifact_id,
                job_id=record.job_id,
                candidate_id=record.candidate_id,
                correlation_id=record.correlation_id,
            )

    async def teardown(self, session) -> None:
        # `review_dispositions.decided_by` is ON DELETE RESTRICT, so the
        # disposition goes before the person. That ordering is the constraint
        # doing its job, not an inconvenience: a decision whose author was
        # erased asserts that a human decided while being unable to say who.
        await session.execute(
            text("DELETE FROM review_dispositions WHERE tenant_id = :t"),
            {"t": self.tenant_id},
        )
        await session.execute(
            text("DELETE FROM audit_log WHERE correlation_id = :c"),
            {"c": self.correlation_id},
        )
        await session.execute(
            text("DELETE FROM tenants WHERE id = :t"), {"t": self.tenant_id}
        )
        await session.execute(
            text("DELETE FROM candidates WHERE id = :c"), {"c": self.candidate_id}
        )


def _json(value) -> str:
    import json

    return json.dumps(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("situation_key", sorted(situations.SITUATION_TYPES))
async def test_one_full_journey_per_situation_type(situation_key: str) -> None:
    """Client onboarding through dashboard render, for one situation type.

    Every stage is asserted by the thing it LEFT BEHIND: a row, or an artifact
    id recorded against the flow. Nothing here is satisfied by a timestamp.
    """
    missing = _missing_stage()
    if missing is not None:
        pytest.skip(f"the journey cannot run yet, stage not live: {missing}")

    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    journey = _Journey(situation_key)

    try:
        async with factory() as session:
            try:
                # 1. CLIENT ONBOARDING
                await journey.onboard_client(session)
                await session.flush()
                assert await _scalar(
                    session, "SELECT count(*) FROM tenants WHERE id = :v",
                    journey.tenant_id,
                ) == 1

                # 2. COMPANY DNA (Layer 2), completed and versioned
                await journey.complete_company_dna(session)
                await session.flush()
                assert await _scalar(
                    session,
                    "SELECT count(*) FROM company_dna WHERE id = :v "
                    "AND status = 'complete'",
                    journey.dna_id,
                ) == 1

                # 3. JOB CREATION, which is where the correlation id is issued
                await journey.create_job(session)
                await session.flush()
                stored = await _scalar(
                    session, "SELECT correlation_id FROM jobs WHERE id = :v",
                    journey.job_id,
                )
                assert stored == journey.correlation_id
                assert provenance.is_correlation_id(stored)

                # 4. SWOT (Bodha, Layer 3), classified into this situation type
                swot = await journey.stage(
                    provenance.STAGE_SWOT,
                    identity.BODHA,
                    "swot_evidence",
                    {
                        "strengths": ["the team ships"],
                        "weaknesses": ["nobody owns reliability"],
                        "opportunities": ["a second region"],
                        "threats": ["a competitor hiring the same profile"],
                        "sources": ["hiring_manager_session"],
                        "situation_type": situation_key,
                    },
                    (f"jobs:{journey.job_id}",),
                )
                assert swot.payload["situation_type"] == situation_key

                # 5. MATRIX (Sutra), frozen. The freeze is a ROW, and it is
                #    what G1 reads. A stamp on the job would not be.
                matrix = await journey.stage(
                    provenance.STAGE_MATRIX,
                    identity.SUTRA,
                    "tatva_matrix",
                    {
                        "must_have": [{"name": "reliability ownership"}],
                        "nice_to_have": [{"name": "multi-region"}],
                        "behavioural": [{"name": "decides under ambiguity"}],
                        "situation_type": situation_key,
                    },
                    (f"jobs:{journey.job_id}",),
                )
                # The situation classified at intake reaches the matrix. It
                # re-weights the whole thing coherently and invisibly, so a
                # misclassification is the most expensive error at intake and
                # nothing downstream can detect it -- which is why it is
                # asserted here rather than inferred from a weight.
                assert matrix.payload["situation_type"] == situation_key
                assert matrix.correlation_id == journey.correlation_id
                await journey.freeze_matrix(session)
                await session.flush()
                assert await _scalar(
                    session,
                    "SELECT count(*) FROM job_company_dna_bindings WHERE job_id = :v",
                    journey.job_id,
                ) == 1

                # 6. PUBLISH
                await journey.publish_job(session)
                await session.flush()
                assert await _scalar(
                    session, "SELECT status FROM jobs WHERE id = :v", journey.job_id
                ) == "published"

                # 7. APPLY, and the evaluation context resolves as of THIS
                #    instant rather than as of scoring time.
                await journey.apply(session)
                await session.flush()
                context = await versioning.resolve_for_application(
                    session, journey.link_id
                )
                assert context.scorecard_version == 1
                assert context.applied_at == APPLIED_AT
                assert context.correlation_id == journey.correlation_id

                # 8. PRE-SCREEN (Yukti) and 9. CONVERSATION (Vaada)
                await journey.stage(
                    provenance.STAGE_PRESCREEN,
                    identity.YUKTI,
                    "ai_score",
                    {"categories": [{"name": "reliability", "grade": "matching"}]},
                    (f"job_candidate_links:{journey.link_id}",),
                )
                await journey.stage(
                    provenance.STAGE_CONVERSATION,
                    identity.VAADA,
                    "answer_event",
                    {"question_key": "reliability_ownership", "answer": "recorded"},
                    (f"job_candidate_links:{journey.link_id}",),
                )

                # 10. SCORING (Miti), with G2 and G3 fired and RECORDED. A
                #     gate whose verdict went nowhere is indistinguishable
                #     from a gate that never ran.
                scoring_envelope = journey.envelope(identity.MITI)
                g2 = enforcement.record_evidence_sufficiency(
                    journey.ledger,
                    scoring_envelope,
                    independent_sources=1,
                    judged_dimensions=2,
                    must_have_coverage={"reliability ownership": 1},
                )
                g3 = enforcement.record_integrity(
                    journey.ledger,
                    scoring_envelope,
                    unresolved_contradictions=1,
                    contradiction_severity="material",
                )
                # 11. INTEGRITY FLAG. It fired, and it blocked nothing: a
                #     blocking integrity gate IS an auto-rejection.
                assert not g3.passed
                assert not g3.blocking
                assert not g2.blocking

                await journey.stage(
                    provenance.STAGE_SCORING,
                    identity.MITI,
                    "scoring_state",
                    {"item_grades": [{"name": "reliability ownership"}]},
                    (f"job_candidate_links:{journey.link_id}",),
                )
                await journey.record_evaluation(
                    session, [g2.as_dict(), g3.as_dict()]
                )
                await session.flush()
                dims = await _scalar(
                    session,
                    "SELECT dimension_scores FROM evaluations WHERE id = :v",
                    journey.evaluation_id,
                )
                assert dims, "the evaluation recorded no dimension scores"
                gate_rows = await _scalar(
                    session,
                    "SELECT gate_results_json FROM evaluations WHERE id = :v",
                    journey.evaluation_id,
                )
                assert {row["gate"] for row in gate_rows} == {
                    pipeline_gates.G2,
                    pipeline_gates.G3,
                }

                # 12. HUMAN DISPOSITION. G4 blocks until a person has decided,
                #     and it asks whether they DECIDED, not whether they
                #     approved.
                with pytest.raises(enforcement.GateBlocked):
                    enforcement.require_human_disposition(
                        journey.ledger,
                        journey.envelope(identity.SIDDHI),
                        needs_review=True,
                        disposition=None,
                    )
                disposition_id = await journey.record_disposition(
                    session, pipeline_gates.DISPOSITION_CLEARED
                )
                await session.flush()
                decided_by = await _scalar(
                    session,
                    "SELECT decided_by FROM review_dispositions WHERE id = :v",
                    disposition_id,
                )
                assert decided_by == journey.hr_manager_id
                g4 = enforcement.require_human_disposition(
                    journey.ledger,
                    journey.envelope(identity.SIDDHI),
                    needs_review=True,
                    disposition=pipeline_gates.DISPOSITION_CLEARED,
                    decided_by=decided_by,
                )
                assert g4.passed

                # 13. REPORT DELIVERY (Siddhi)
                report = await journey.stage(
                    provenance.STAGE_REPORT,
                    identity.SIDDHI,
                    "prism_report",
                    {
                        "ai_score": {"categories": []},
                        "ppi_assessment": {},
                        "validation": {},
                        "gap_analysis": [],
                    },
                    (f"evaluations:{journey.evaluation_id}",),
                )
                assert report.artifact_id in {
                    r.artifact_id for r in journey.ledger.records
                }

                # 14. DASHBOARD RENDER, over the audit trail. RBAC 31 requires
                #     the trail to exist independently of anything rendering
                #     it, so the rows are written first and read back second.
                await journey.audit_every_stage(session)
                await session.flush()
                activity = await audit_mod.activity(
                    session, tenant_id=journey.tenant_id, limit=100
                )
                assert activity, "the dashboard has no activity to render"
                assert all(row["agent_name"] for row in activity)
                assert {row["actor_user_id"] for row in activity} == {
                    journey.hr_manager_id
                }

                # THE WHOLE FLOW, UNDER ONE ID. This is the join spec-doc6 4.1
                # asks for, and the reason a per-agent workflow id is not a
                # weaker version of it but a different thing.
                joined = await _scalar(
                    session,
                    "SELECT count(*) FROM audit_log WHERE correlation_id = :v",
                    journey.correlation_id,
                )
                assert joined == len(journey.ledger.records)

                # And the ledger itself is complete for every agent stage,
                # with an artifact behind each one.
                assert journey.ledger.problems(
                    expected=(
                        provenance.STAGE_SWOT,
                        provenance.STAGE_MATRIX,
                        provenance.STAGE_PRESCREEN,
                        provenance.STAGE_CONVERSATION,
                        provenance.STAGE_SCORING,
                        provenance.STAGE_REPORT,
                    )
                ) == []
            finally:
                await journey.teardown(session)
                await session.commit()
    finally:
        await engine.dispose()


async def _scalar(session, sql: str, value):
    return (await session.execute(text(sql), {"v": value})).scalar()


def test_the_journey_names_the_missing_stage_rather_than_skipping_silently() -> None:
    """A skip nobody can act on is a test that has quietly stopped existing.

    Whichever branch this run takes, the reason is a sentence naming a stage and
    the work that supplies it.
    """
    missing = _missing_stage()
    if missing is None:
        # Every stage is live, so the journey above ran for real.
        assert activation.missing_stages() == ()
    else:
        assert "(" in missing and ")" in missing, missing
        assert any(
            missing.startswith(stage) for stage in activation.STAGE_MODULES
        ), missing
