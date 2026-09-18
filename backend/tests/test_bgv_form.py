"""The employer checkbox form: token lifecycle, statuses, and C4's boundary.

Vivekium feature 4. The pure half runs with nothing; the flow half seeds one
candidate with one employment and one verification and drives the public
routes directly, the way the closure tests drive `close_job`.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.services import bgv_form


# ── The pure half ────────────────────────────────────────────────────────────


def test_the_items_are_the_briefs_seven():
    assert len(bgv_form.FORM_ITEMS) == 7
    assert len(bgv_form.REQUIRED_KEYS) == 6
    optional = [item for item in bgv_form.FORM_ITEMS if not item.required]
    assert [item.key for item in optional] == ["eligible_for_rehire"]


def test_status_from_answers_is_all_or_not_verified():
    everything = {key: True for key in bgv_form.ITEM_KEYS}
    assert bgv_form.status_from_answers(everything) == "verified"
    # The optional item does not gate verification.
    no_rehire = dict(everything, eligible_for_rehire=False)
    assert bgv_form.status_from_answers(no_rehire) == "verified"
    # One required item the employer would not confirm dominates.
    one_denied = dict(everything, compensation_accurate=False)
    assert bgv_form.status_from_answers(one_denied) == "not_verified"
    assert bgv_form.status_from_answers({}) == "not_verified"


def test_clean_answers_drops_the_unknown_and_coerces():
    cleaned = bgv_form.clean_answers(
        {"job_title_accurate": 1, "evil": True, "compensation_accurate": None}
    )
    assert cleaned == {
        "job_title_accurate": True,
        "compensation_accurate": False,
    }
    assert bgv_form.clean_answers("nonsense") == {}


def test_expiry_is_ttl_days_from_issue():
    issued = datetime(2026, 9, 1, tzinfo=timezone.utc)
    from app.core.config import get_settings

    ttl = get_settings().verification_link_ttl_days
    assert bgv_form.expires_at(issued) == issued + timedelta(days=ttl)
    assert not bgv_form.is_expired(issued, now=issued + timedelta(days=ttl, seconds=-1))
    assert bgv_form.is_expired(issued, now=issued + timedelta(days=ttl))


def test_the_masked_address_shows_one_character_and_the_domain():
    assert bgv_form.masked_email("hr.team@corp.example") == "h***@corp.example"
    assert bgv_form.masked_email(None) == "(address unavailable)"
    assert "@" not in bgv_form.masked_email("not-an-address").split("***")[0]


# ── The flow, against a real database ────────────────────────────────────────


def _run(coro_factory):
    async def _wrapped():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.core.config import get_settings

        engine = create_async_engine(get_settings().database_url)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            return await coro_factory(factory)
        finally:
            await engine.dispose()

    return asyncio.run(_wrapped())


def _skip_without_database() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    async def _probe():
        engine = create_async_engine(get_settings().database_url)
        try:
            async with engine.connect() as conn:
                await conn.execute(
                    text("SELECT form_token FROM bgv_verifications LIMIT 1")
                )
                return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            await engine.dispose()

    if not asyncio.run(_probe()):
        pytest.skip("no database at migration 0102 reachable")


class _World:
    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.candidate = uuid.uuid4()
        self.employment = uuid.uuid4()
        self.verification = uuid.uuid4()
        self.token = bgv_form.mint_token()


async def _seed(session, w: _World, issued_at: datetime) -> None:
    await session.execute(
        text(
            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
            "VALUES (:t, :n, :d, 'pending')"
        ),
        {"t": str(w.tenant), "n": f"bgvform-{w.tenant}",
         "d": f"{w.tenant}.bgvform.test"},
    )
    await session.execute(
        text(
            "INSERT INTO candidates (id, tenant_id, full_name, email, "
            "consent_databank, employment_background) "
            "VALUES (:c, :t, 'Form Subject', :e, false, 'experienced')"
        ),
        {"c": str(w.candidate), "t": str(w.tenant),
         "e": f"{w.candidate}@bgvform.test"},
    )
    await session.execute(
        text(
            "INSERT INTO candidate_employments (id, candidate_id, "
            "employer_name, designation, started_on, ended_on, hr_name, "
            "hr_email, created_at) VALUES (:i, :c, 'Prior Corp', 'Engineer', "
            "'2021-01-01', '2023-01-01', 'HR Team', 'hr@prior.example', now())"
        ),
        {"i": str(w.employment), "c": str(w.candidate)},
    )
    await session.execute(
        text(
            "INSERT INTO bgv_verifications (id, tenant_id, candidate_id, "
            "candidate_employment_id, status, form_token, "
            "form_token_issued_at, created_at) VALUES (:v, :t, :c, :e, "
            "'pending', :tok, :at, now())"
        ),
        {"v": str(w.verification), "t": str(w.tenant), "c": str(w.candidate),
         "e": str(w.employment), "tok": w.token, "at": issued_at},
    )


async def _cleanup(session, w: _World) -> None:
    await session.execute(
        text("DELETE FROM candidates WHERE id = :c"), {"c": str(w.candidate)}
    )
    await session.execute(
        text("DELETE FROM tenants WHERE id = :t"), {"t": str(w.tenant)}
    )


def _all_true():
    return {key: True for key in bgv_form.ITEM_KEYS}


def test_the_form_round_trip_and_the_single_use_latch(monkeypatch) -> None:
    from app.api import bgv as bgv_api
    from app.core.db import superadmin_scope

    _skip_without_database()
    w = _World()
    sent: list[tuple] = []
    monkeypatch.setattr(
        bgv_api, "dispatch", lambda name, args=None, **kw: sent.append((name, args))
    )

    async def _flow(factory):
        now = datetime.now(timezone.utc)
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await _seed(session, w, issued_at=now)
        try:
            async with factory() as session:
                async with superadmin_scope(session):
                    form = await bgv_api.get_employer_checkbox_form(
                        w.token, session=session
                    )
            assert form["candidate_name"] == "Form Subject"
            assert form["employer_name"] == "Prior Corp"
            assert len(form["items"]) == 7

            # A submission missing an answer is refused before any write.
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        with pytest.raises(HTTPException) as missing:
                            await bgv_api.submit_employer_checkbox_form(
                                w.token,
                                {"answers": {"job_title_accurate": True}},
                                session=session,
                            )
            assert missing.value.status_code == 422

            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        out = await bgv_api.submit_employer_checkbox_form(
                            w.token, {"answers": _all_true()}, session=session
                        )
            assert out == {"status": "recorded"}

            async with factory() as session:
                async with superadmin_scope(session):
                    row = (
                        await session.execute(
                            text(
                                "SELECT status, form_submitted_at, responded_at, "
                                "form_answers_json, decided_by FROM "
                                "bgv_verifications WHERE id = :v"
                            ),
                            {"v": str(w.verification)},
                        )
                    ).one()
            assert row[0] == "verified"
            assert row[1] is not None and row[2] is not None
            assert row[3]["employment_period_accurate"] is True
            # C4's boundary: the form is its own provenance; decided_by names
            # a platform user and stays NULL.
            assert row[4] is None

            # Email 2 went to the candidate, with no verification detail.
            assert sent and sent[-1][1][2] == "bgv_completed"
            assert sent[-1][1][1] == f"{w.candidate}@bgvform.test"

            # The latch: a second submission answers 409, not 410.
            async with factory() as session:
                async with superadmin_scope(session):
                    with pytest.raises(HTTPException) as again:
                        await bgv_api.submit_employer_checkbox_form(
                            w.token, {"answers": _all_true()}, session=session
                        )
            assert again.value.status_code == 409
        finally:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await _cleanup(session, w)

    _run(_flow)


def test_an_expired_link_is_410_and_writes_nothing(monkeypatch) -> None:
    from app.api import bgv as bgv_api
    from app.core.db import superadmin_scope
    from app.core.config import get_settings

    _skip_without_database()
    w = _World()
    monkeypatch.setattr(bgv_api, "dispatch", lambda *a, **k: None)

    async def _flow(factory):
        ttl = get_settings().verification_link_ttl_days
        stale = datetime.now(timezone.utc) - timedelta(days=ttl, minutes=1)
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await _seed(session, w, issued_at=stale)
        try:
            async with factory() as session:
                async with superadmin_scope(session):
                    with pytest.raises(HTTPException) as gone:
                        await bgv_api.get_employer_checkbox_form(
                            w.token, session=session
                        )
            assert gone.value.status_code == 410
            async with factory() as session:
                async with superadmin_scope(session):
                    with pytest.raises(HTTPException) as gone_post:
                        await bgv_api.submit_employer_checkbox_form(
                            w.token, {"answers": _all_true()}, session=session
                        )
            assert gone_post.value.status_code == 410
            async with factory() as session:
                async with superadmin_scope(session):
                    status_now = (
                        await session.execute(
                            text(
                                "SELECT status, form_submitted_at FROM "
                                "bgv_verifications WHERE id = :v"
                            ),
                            {"v": str(w.verification)},
                        )
                    ).one()
            assert status_now == ("pending", None)
        finally:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await _cleanup(session, w)

    _run(_flow)


def test_a_partial_confirmation_records_not_verified(monkeypatch) -> None:
    """One required item the employer would not confirm dominates, and email
    2 is NOT sent: a partial confirmation goes to the recruiter, not to the
    candidate as good news."""
    from app.api import bgv as bgv_api
    from app.core.db import superadmin_scope

    _skip_without_database()
    w = _World()
    sent: list[tuple] = []
    monkeypatch.setattr(
        bgv_api, "dispatch", lambda name, args=None, **kw: sent.append((name, args))
    )

    async def _flow(factory):
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await _seed(session, w, issued_at=datetime.now(timezone.utc))
        try:
            answers = dict(_all_true(), compensation_accurate=False)
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await bgv_api.submit_employer_checkbox_form(
                            w.token, {"answers": answers}, session=session
                        )
            async with factory() as session:
                async with superadmin_scope(session):
                    status_now = (
                        await session.execute(
                            text(
                                "SELECT status FROM bgv_verifications "
                                "WHERE id = :v"
                            ),
                            {"v": str(w.verification)},
                        )
                    ).scalar_one()
            assert status_now == "not_verified"
            assert not sent
        finally:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await _cleanup(session, w)

    _run(_flow)


def test_a_bounced_inquiry_alerts_the_candidate_with_a_masked_address(
    monkeypatch,
) -> None:
    """Email 4: the bounce handler correlates back through the HR address and
    tells the candidate, masked. Driven at the helper, which is the whole of
    the new behaviour; the SNS envelope around it is unchanged and has its
    own tests."""
    from types import SimpleNamespace

    from app.api import email_senders as es_api
    from app.core.db import superadmin_scope

    _skip_without_database()
    w = _World()
    sent: list[tuple] = []
    monkeypatch.setattr(
        "app.workers.dispatch.dispatch",
        lambda name, args=None, **kw: sent.append((name, args)),
    )

    async def _flow(factory):
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await _seed(session, w, issued_at=datetime.now(timezone.utc))
        try:
            log_row = SimpleNamespace(
                tenant_id=w.tenant, recipient_email="hr@prior.example"
            )
            async with factory() as session:
                async with superadmin_scope(session):
                    await es_api._alert_candidate_of_bgv_bounce(session, log_row)
            assert sent, "no alert was dispatched for a bounced BGV inquiry"
            name, args = sent[-1]
            assert args[1] == f"{w.candidate}@bgvform.test"
            assert args[2] == "bgv_bounced"
            context = args[3]
            assert context["masked_hr_email"] == "h***@prior.example"
            assert "hr@prior.example" not in str(context.values())
        finally:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await _cleanup(session, w)

    _run(_flow)


def test_append_enforces_the_last_two_and_fires_auto_maintenance(
    monkeypatch,
) -> None:
    """Feature 5 end to end at the service layer: the append lands, the
    OLDEST row (by start date) is auto-dropped taking its verification with
    it, the 0103 trigger admits only that delete, and auto-maintenance opens
    and sends the new employer's verification for the tenant already
    verifying."""
    from app.core.db import superadmin_scope
    from app.services import bgv_maintenance

    _skip_without_database()
    w = _World()
    sent: list[tuple] = []
    monkeypatch.setattr(
        "app.services.bgv_maintenance.dispatch",
        lambda name, args=None, **kw: sent.append((name, args)),
        raising=False,
    )
    import app.workers.dispatch as dispatch_mod

    monkeypatch.setattr(
        dispatch_mod, "dispatch",
        lambda name, args=None, **kw: sent.append((name, args)),
    )

    async def _flow(factory):
        # Seed the standard world (one employer 2021-2023, one pending
        # verification for the tenant), then FINALISE the history and add a
        # second, OLDER employer so the drop target is unambiguous.
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await _seed(session, w, issued_at=datetime.now(timezone.utc))
                    await session.execute(
                        text(
                            "UPDATE candidates SET "
                            "employment_history_finalized_at = now() "
                            "WHERE id = :c"
                        ),
                        {"c": str(w.candidate)},
                    )
        oldest = uuid.uuid4()
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    # INSERT is allowed post-finalisation (append semantics).
                    await session.execute(
                        text(
                            "INSERT INTO candidate_employments (id, "
                            "candidate_id, employer_name, designation, "
                            "started_on, ended_on, hr_name, hr_email, "
                            "created_at) VALUES (:i, :c, 'Ancient Corp', "
                            "'Intern', '2015-01-01', '2017-01-01', 'HR', "
                            "'hr@ancient.example', now())"
                        ),
                        {"i": str(oldest), "c": str(w.candidate)},
                    )
        try:
            # The trigger still refuses an UNSANCTIONED delete.
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        with pytest.raises(Exception) as refused:
                            await session.execute(
                                text(
                                    "DELETE FROM candidate_employments "
                                    "WHERE id = :i"
                                ),
                                {"i": str(oldest)},
                            )
            assert "final" in str(refused.value)

            # The sanctioned append: caps at two, dropping Ancient Corp.
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        result = await bgv_maintenance.append_employer(
                            session,
                            candidate_id=w.candidate,
                            employer_name="Newest Corp",
                            designation="Staff Engineer",
                            started_on=date(2024, 1, 1),
                            ended_on=date(2026, 1, 1),
                            hr_name="HR Desk",
                            hr_email="people@newest.example",
                        )
            assert result["dropped"] == [str(oldest)]

            async with factory() as session:
                async with superadmin_scope(session):
                    names = [
                        r[0]
                        for r in (
                            await session.execute(
                                text(
                                    "SELECT employer_name FROM "
                                    "candidate_employments WHERE "
                                    "candidate_id = :c ORDER BY started_on"
                                ),
                                {"c": str(w.candidate)},
                            )
                        ).all()
                    ]
            assert names == ["Prior Corp", "Newest Corp"]

            # Auto-maintenance: the tenant already verifying gets the new
            # employer's verification, pending, with a form link sent.
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        opened = await bgv_maintenance.auto_open_verifications(
                            session,
                            candidate_id=w.candidate,
                            employment_id=uuid.UUID(result["added_id"]),
                        )
            assert opened == 1
            assert sent and sent[-1][1][2] == "bgv_verification"
            assert sent[-1][1][1] == "people@newest.example"
            assert "/verify-employment/" in sent[-1][1][3]["body"]
            # Idempotent: a second run opens nothing.
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        again = await bgv_maintenance.auto_open_verifications(
                            session,
                            candidate_id=w.candidate,
                            employment_id=uuid.UUID(result["added_id"]),
                        )
            assert again == 0
        finally:
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await session.execute(
                            text(
                                "SELECT set_config("
                                "'app.bgv_maintenance', 'on', true)"
                            )
                        )
                        await _cleanup(session, w)

    _run(_flow)
