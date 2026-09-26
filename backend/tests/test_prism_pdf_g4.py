"""The PDF route runs gate G4: a flagged report is not downloaded until a person decides.

PLAN-p5 P5-D7. `siddhi.delivery` had no production caller, so the PDF route
rendered every report, flagged or not, straight through the renderer and G4
guarded nothing a client received. The route now takes the clearance G4 mints
and answers 409 with the server's own sentence while a flagged report has no
recorded human disposition. The on-screen report is not gated (it is where
that person reads the report), and that is asserted too.

Reuses the retention-consent fixture: the download already needs the
candidate's explicit Yes, and this file adds the second gate on top of it.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from tests.test_retention_consent import (
    _cleanup,
    _factory_or_skip,
    _Fixture,
    _seed,
    _staff_user,
)


async def _consent_and_flag(factory, fx: _Fixture) -> None:
    from app.core.db import superadmin_scope
    from app.models import Candidate
    from app.models.assessment import FunctionalSkillsReport

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                candidate = await s.get(Candidate, fx.cand_id)
                candidate.retain_assessment_consent = True
                candidate.retain_assessment_consented_at = datetime.now(timezone.utc)
                # A report is written once and never UPDATEd (PLAN-p5 P5-D12:
                # the database refuses it), so a flagged report is WRITTEN
                # flagged, the way the scoring run writes one. The seeded,
                # unflagged row is replaced rather than edited in place, which
                # keeps this test valid once the immutability trigger lands.
                seeded = await s.get(FunctionalSkillsReport, fx.report_id)
                summary, written_at = seeded.overall_summary, seeded.synthesized_at
                await s.delete(seeded)
                await s.flush()
                s.add(
                    FunctionalSkillsReport(
                        id=fx.report_id,
                        tenant_id=fx.tenant_id,
                        job_id=fx.job_id,
                        job_candidate_link_id=fx.link_id,
                        grade="non_managerial",
                        overall_summary=summary,
                        validation_json={},
                        synthesized_at=written_at,
                        needs_human_review=True,
                    )
                )


async def _download(factory, fx: _Fixture):
    from app.api import assessments as assessments_mod
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                return await assessments_mod.download_report_pdf(
                    link_id=fx.link_id, user=_staff_user(fx), session=s
                )


@pytest.mark.asyncio
async def test_a_flagged_report_without_a_disposition_is_refused_with_the_servers_sentence() -> None:
    from app.api import assessments as assessments_mod
    from app.core.db import superadmin_scope
    from app.services.siddhi import delivery

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        await _consent_and_flag(factory, fx)
        with pytest.raises(HTTPException) as caught:
            await _download(factory, fx)
        assert caught.value.status_code == 409
        assert caught.value.detail == delivery.PDF_BLOCKED_REASON

        # The on-screen report is NOT gated: it is where the decision is made.
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    report_out = await assessments_mod.get_report(
                        link_id=fx.link_id, user=_staff_user(fx), session=s
                    )
        assert report_out.overall_summary
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_recorded_human_disposition_releases_the_pdf() -> None:
    from app.core.db import superadmin_scope
    from app.models.hiring import ReviewDisposition

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        await _consent_and_flag(factory, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    s.add(
                        ReviewDisposition(
                            tenant_id=fx.tenant_id,
                            job_id=fx.job_id,
                            link_id=fx.link_id,
                            disposition="cleared",
                            decided_by=fx.staff_user_id,
                        )
                    )
        response = await _download(factory, fx)
        assert response.media_type == "application/pdf"
        assert response.body.startswith(b"%PDF")
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


def test_the_route_reaches_the_renderer_only_through_the_gated_path() -> None:
    """Asserted over the source: the route names the gate and the gated
    renderer, and never the raw renderer."""
    import inspect

    from app.api import assessments as assessments_mod

    source = inspect.getsource(assessments_mod.download_report_pdf)
    assert "delivery.gate_delivery(" in source
    assert "delivery.prism_pdf(" in source
    assert "render_report_pdf" not in source
    assert source.index("gate_delivery(") < source.index("prism_pdf(")
