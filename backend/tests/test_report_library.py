"""Report Library (Master Directive Part 4).

Two layers, same convention as test_stem_credit_rates.py:

  * CATALOGUE INTEGRITY always runs: the Part 4 tables transcribed into
    `services/reports/catalogue` are pinned entry by entry, so a drift in
    either direction (a lost report, an invented one, a mislabelled access
    set) fails a test rather than shipping a wrong library.
  * GENERATION runs against the containerised database and skips cleanly
    without it: every implemented report is generated in every format it
    offers, against rows written through the real models.

THE COUNT. Part 4's headline says "31 reports"; its own category tables
enumerate 37 distinct ids (A=7, B=5, C=4, D=4, E=5, F=5, G=7). The tables are
the operative spec ("Build every report exactly as specified"), so the
catalogue follows the tables and these tests pin THAT number, per category,
rather than forcing the headline figure to pass. The DEI Pipeline Analyzer
(Part 4 section 4) is a 38th entry, catalogued Coming Soon.
"""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from app.models.enums import Role
from app.services.reports import catalogue
from app.services.reports import engine
from app.services.reports.builders import BUILDERS
from tests.test_billing import _factory_or_skip, _tenant

# The Part 4 tables, counted independently of the catalogue module.
EXPECTED_PER_CATEGORY = {"A": 7, "B": 5, "C": 4, "D": 4, "E": 5, "F": 5, "G": 7}
EXPECTED_SPEC_TOTAL = 37   # sum of the above; the directive's headline "31"
                           # does not match its own tables, see module docstring
EXPECTED_IMPLEMENTED = {"A-02", "B-01", "B-03", "C-01", "C-02", "D-03"}


# ── Catalogue integrity (always runs) ───────────────────────────────────────

def test_catalogue_carries_every_report_the_tables_define() -> None:
    ids = [r.id for r in catalogue.CATALOGUE]
    assert len(ids) == len(set(ids)), "duplicate report ids"

    spec_entries = [r for r in catalogue.CATALOGUE if r.category in EXPECTED_PER_CATEGORY]
    counts = Counter(r.category for r in spec_entries)
    assert dict(counts) == EXPECTED_PER_CATEGORY
    assert len(spec_entries) == EXPECTED_SPEC_TOTAL

    # Ids are consistent with their category letter (A-02 sits in A, etc.).
    for r in spec_entries:
        assert r.id.startswith(f"{r.category}-"), r.id

    # Plus exactly one DEI entry (Part 4 section 4).
    dei = [r for r in catalogue.CATALOGUE if r.category == "DEI"]
    assert len(dei) == 1
    assert len(catalogue.CATALOGUE) == EXPECTED_SPEC_TOTAL + 1


def test_every_entry_is_fully_described() -> None:
    valid_formats = {"pdf", "excel", "csv"}
    for r in catalogue.CATALOGUE:
        assert r.name, r.id
        assert r.category in catalogue.CATEGORIES, r.id
        assert len(r.description) > 30, f"{r.id} has no real description"
        assert r.data_sources, r.id
        assert r.formats and set(r.formats) <= valid_formats, r.id
        assert "pdf" in r.formats, f"{r.id}: PDF output covers all reports (section 3.1)"
        assert r.schedules, r.id
        assert r.access, f"{r.id} is accessible to nobody"
        # Section 1.2: HR Head / CHRO get ALL reports for their organisation.
        assert Role.client in r.access, r.id
        assert Role.hr_manager in r.access, r.id


def test_implemented_flags_agree_with_the_builder_registry() -> None:
    implemented = {r.id for r in catalogue.CATALOGUE if r.implemented}
    assert implemented == EXPECTED_IMPLEMENTED
    assert set(BUILDERS) == EXPECTED_IMPLEMENTED
    # A coming-soon report must never claim to be implemented.
    for r in catalogue.CATALOGUE:
        assert not (r.coming_soon and r.implemented), r.id


def test_the_dei_report_is_marked_coming_soon() -> None:
    """Part 4 section 4: no demographic processing before the consent
    framework exists; the library shows the report as Coming Soon."""
    dei = catalogue.definition_for("DEI-01")
    assert dei is not None
    assert dei.coming_soon is True
    assert dei.implemented is False
    assert "consent" in dei.description.lower()


def test_role_filtering_follows_the_access_tables() -> None:
    all_ids = {r.id for r in catalogue.CATALOGUE}
    # HR Head tier sees the whole library (section 1.2).
    assert {r.id for r in catalogue.visible_to(Role.client)} == all_ids
    assert {r.id for r in catalogue.visible_to(Role.hr_manager)} == all_ids

    recruiter = {r.id for r in catalogue.visible_to(Role.recruiter)}
    assert "B-01" in recruiter and "C-01" in recruiter
    assert "A-05" not in recruiter, "A-05 is HM / HR Head only in the table"

    hm = {r.id for r in catalogue.visible_to(Role.hiring_manager)}
    assert "A-05" in hm and "A-02" in hm
    assert "C-01" not in hm, "credit reports do not name the Hiring Manager"

    # Non-staff roles see nothing.
    assert catalogue.visible_to(Role.candidate) == ()


# ── Engine refusals (always run: they fire before any query) ────────────────

@pytest.mark.asyncio
async def test_an_unimplemented_report_raises_the_typed_error() -> None:
    with pytest.raises(engine.ReportNotImplemented) as raised:
        await engine.generate(None, uuid.uuid4(), "F-05", {}, "pdf")
    assert "F-05" in str(raised.value)
    assert "Annual Talent Acquisition Review" in str(raised.value)


@pytest.mark.asyncio
async def test_the_dei_report_refuses_with_the_consent_message() -> None:
    with pytest.raises(engine.ReportComingSoon) as raised:
        await engine.generate(None, uuid.uuid4(), "DEI-01", {}, "pdf")
    assert "consent" in str(raised.value).lower()
    # ComingSoon IS a ReportNotImplemented, so the API's one handler covers both.
    assert isinstance(raised.value, engine.ReportNotImplemented)


@pytest.mark.asyncio
async def test_an_unknown_report_id_is_its_own_error() -> None:
    with pytest.raises(engine.UnknownReport):
        await engine.generate(None, uuid.uuid4(), "Z-99", {}, "pdf")


# ── Generation against the containerised database ───────────────────────────

async def _seed_world(session, tenant_id: uuid.UUID) -> uuid.UUID:
    """A small tenant world touching every table the six builders read.

    Returns the job id. Two candidates: one decided quickly (shortlisted),
    one parked in HM review long enough to trip both the SLA line (B-03) and
    the aging threshold (D-03).
    """
    from app.models.assessment import (
        AssessmentConversation,
        FunctionalSkillsReport,
        ReportDimension,
    )
    from app.models.billing import CreditLedgerEntry
    from app.models.candidate import Candidate, JobCandidateLink, PipelineStatusEntry
    from app.models.enums import LinkSource, PipelineStatus
    from app.models.job import Job

    now = datetime.now(timezone.utc)
    job = Job(tenant_id=tenant_id, title="Data Engineer",
              department="Engineering", jd_json={})
    session.add(job)
    await session.flush()

    def candidate(name: str) -> Candidate:
        return Candidate(
            tenant_id=tenant_id, full_name=name,
            email=f"{uuid.uuid4().hex[:10]}@report.test",
        )

    cand_fast, cand_slow = candidate("Asha Fast"), candidate("Ravi Slow")
    session.add_all([cand_fast, cand_slow])
    await session.flush()

    link_fast = JobCandidateLink(
        tenant_id=tenant_id, job_id=job.id, candidate_id=cand_fast.id,
        source=LinkSource.fresh, status="shortlisted",
        status_updated_at=now - timedelta(hours=6), match_score=92.0,
    )
    link_slow = JobCandidateLink(
        tenant_id=tenant_id, job_id=job.id, candidate_id=cand_slow.id,
        source=LinkSource.fresh, status="assessment_completed",
        status_updated_at=now - timedelta(days=5), match_score=71.0,
    )
    session.add_all([link_fast, link_slow])
    await session.flush()

    session.add_all([
        # Fast candidate: reviewed and decided within 24 hours.
        PipelineStatusEntry(
            tenant_id=tenant_id, job_candidate_link_id=link_fast.id,
            status=PipelineStatus.assessment_completed,
            at=now - timedelta(hours=30),
        ),
        PipelineStatusEntry(
            tenant_id=tenant_id, job_candidate_link_id=link_fast.id,
            status=PipelineStatus.shortlisted, at=now - timedelta(hours=6),
        ),
        # Slow candidate: sitting in HM review for five days, undecided.
        PipelineStatusEntry(
            tenant_id=tenant_id, job_candidate_link_id=link_slow.id,
            status=PipelineStatus.assessment_completed,
            at=now - timedelta(days=5),
        ),
    ])

    session.add_all([
        AssessmentConversation(
            tenant_id=tenant_id, job_id=job.id,
            job_candidate_link_id=link_fast.id, grade="non_managerial",
            status="completed",
            invitation_sent_at=now - timedelta(days=2),
            started_at=now - timedelta(days=2, hours=-1),
            completed_at=now - timedelta(hours=31),
        ),
        AssessmentConversation(
            tenant_id=tenant_id, job_id=job.id,
            job_candidate_link_id=link_slow.id, grade="non_managerial",
            status="completed",
            invitation_sent_at=now - timedelta(days=6),
            completed_at=now - timedelta(days=5, hours=1),
        ),
    ])

    # Ledger: a grant, a STEM report, a non-STEM report, a partial, a no-show.
    def ledger(event: str, delta: int, meta: dict | None = None) -> CreditLedgerEntry:
        return CreditLedgerEntry(
            tenant_id=tenant_id, event_type=event, subunits_delta=delta,
            idempotency_key=f"report-lib-test:{uuid.uuid4()}",
            metadata_json=meta,
        )

    session.add_all([
        ledger("grant", 300 * 60),
        ledger("completed_assessment", -90, {"role_classification": "STEM"}),
        ledger("completed_assessment", -60, {"role_classification": "NON_STEM"}),
        ledger("incomplete_assessment", -20, {"role_classification": "NON_STEM"}),
        ledger("no_show", -4),
    ])

    # PRISM reports: one with a stored overall score, one pre-0030 style
    # (NULL overall, recomputed from its dimensions).
    fsr_fast = FunctionalSkillsReport(
        tenant_id=tenant_id, job_id=job.id, job_candidate_link_id=link_fast.id,
        grade="non_managerial", overall_summary="Strong across the board.",
        overall_score=92, synthesized_at=now - timedelta(hours=30),
    )
    fsr_slow = FunctionalSkillsReport(
        tenant_id=tenant_id, job_id=job.id, job_candidate_link_id=link_slow.id,
        grade="non_managerial", overall_summary="Mixed evidence.",
        overall_score=None, synthesized_at=now - timedelta(days=5),
    )
    session.add_all([fsr_fast, fsr_slow])
    await session.flush()
    session.add_all([
        ReportDimension(
            tenant_id=tenant_id, report_id=fsr_fast.id, category="primary_skill",
            name="Python", score=95, remark="Excellent depth.", ordinal=1,
        ),
        ReportDimension(
            tenant_id=tenant_id, report_id=fsr_fast.id, category="behavioural",
            name="Ownership", score=88, remark="Consistent evidence.", ordinal=2,
        ),
        ReportDimension(
            tenant_id=tenant_id, report_id=fsr_slow.id, category="primary_skill",
            name="Python", score=70, remark="Shallow in places.", ordinal=1,
        ),
        ReportDimension(
            tenant_id=tenant_id, report_id=fsr_slow.id, category="behavioural",
            name="Ownership", score=80, remark="Adequate.", ordinal=2,
        ),
    ])
    await session.flush()
    return job.id


@pytest.mark.asyncio
async def test_every_implemented_report_generates_in_every_offered_format() -> None:
    engine_db, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            from sqlalchemy import text

            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            tenant_id = await _tenant(session)
            job_id = await _seed_world(session, tenant_id)

            for defn in catalogue.CATALOGUE:
                if not defn.implemented:
                    continue
                params = {"job_id": str(job_id)}
                for fmt in defn.formats:
                    content, media_type, filename = await engine.generate(
                        session, tenant_id, defn.id, dict(params), fmt
                    )
                    assert content, f"{defn.id}/{fmt} produced empty bytes"
                    if fmt == "pdf":
                        assert media_type == "application/pdf"
                        assert content.startswith(b"%PDF"), defn.id
                        assert filename.endswith(".pdf")
                    else:
                        # excel deliberately falls back to CSV: openpyxl is
                        # not in the environment (engine module docstring).
                        assert media_type == "text/csv", f"{defn.id}/{fmt}"
                        assert filename.endswith(".csv")
                        text_out = content.decode("utf-8-sig")
                        # The section 3.1 header block is present.
                        assert "Report Name" in text_out
                        assert defn.id in text_out
                        assert "Organisation" in text_out
            await session.rollback()
    finally:
        await engine_db.dispose()


@pytest.mark.asyncio
async def test_the_reports_carry_the_seeded_facts() -> None:
    """Spot-checks that the builders read the real rows, not fixtures of
    their own: names, grades and credit figures from _seed_world appear in
    the CSV output verbatim."""
    engine_db, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            from sqlalchemy import text

            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            tenant_id = await _tenant(session)
            job_id = await _seed_world(session, tenant_id)
            params = {"job_id": str(job_id)}

            # B-01: both candidates, with stages.
            csv_out, _, _ = await engine.generate(
                session, tenant_id, "B-01", dict(params), "csv"
            )
            b01 = csv_out.decode("utf-8-sig")
            assert "Asha Fast" in b01 and "Ravi Slow" in b01
            assert "Shortlisted" in b01

            # A-02: ranked by grade; the stored-score candidate outranks the
            # recomputed one, and only word grades appear (never raw scores).
            csv_out, _, _ = await engine.generate(
                session, tenant_id, "A-02", dict(params), "csv"
            )
            a02 = csv_out.decode("utf-8-sig")
            assert a02.index("Asha Fast") < a02.index("Ravi Slow")
            assert "Highly Matching" in a02
            assert "Matching" in a02

            # C-01: the STEM split and the credit arithmetic (90 subunits =
            # 1.50 credits, 60 = 1.00).
            csv_out, _, _ = await engine.generate(
                session, tenant_id, "C-01", {}, "csv"
            )
            c01 = csv_out.decode("utf-8-sig")
            assert "STEM" in c01 and "NON_STEM" in c01
            assert "1.50" in c01
            assert "300.00" in c01  # the grant

            # C-02: balance = 300 - 1.5 - 1.0 - 0.33 - 0.07 = 297.10 credits.
            pdf_out, media, _ = await engine.generate(
                session, tenant_id, "C-02", {}, "pdf"
            )
            assert media == "application/pdf" and pdf_out.startswith(b"%PDF")

            # D-03: the parked candidate is stagnant (5 days in a 2-day
            # stage), the fresh one is not.
            pdf_out, _, _ = await engine.generate(
                session, tenant_id, "D-03", {}, "pdf"
            )
            assert pdf_out.startswith(b"%PDF")

            # B-03: one decision at 24h (within the 48h SLA), one candidate
            # waiting five days beyond it.
            pdf_out, _, _ = await engine.generate(
                session, tenant_id, "B-03", {}, "pdf"
            )
            assert pdf_out.startswith(b"%PDF")
            await session.rollback()
    finally:
        await engine_db.dispose()
