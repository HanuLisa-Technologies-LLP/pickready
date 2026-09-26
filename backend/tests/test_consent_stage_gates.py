"""Where the two consent stages are actually COLLECTED, and what refuses.

Vivekium feature 6 says Stage A is taken "at registration, before candidate
profile creation completes", and that every item is consented individually.
Two gates carry that, and neither had a test:

* `PUT /portal/me/profile-form` is where profile creation completes, so it is
  where Stage A is asked. A save that would leave the profile COMPLETE while
  registration consent is outstanding is refused; a PARTIAL save is not,
  because the form is filled in over several sittings and locking a candidate
  out of their own half-finished profile would be a worse outcome than an
  unticked box.
* `assessment_consent.record_consent` refuses anything but the whole Stage B
  set, so the record can honestly say each item was agreed to separately
  whatever the screen looked like.

The half worth writing a test for is the one nobody thinks of: a candidate who
ALREADY consented, through the outreach form or an application, must not be
asked again, and a candidate who registered before any of this existed must
not be locked out of a route. Both are asserted below.

Same convention as test_candidate_profile.py: skips cleanly with no database.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import text

COMPLETE_ANSWERS = {
    "current_city": "Pune",
    "total_experience": "4 Years 0 Months",
    "current_ctc": "12 LPA",
    "expected_ctc": "18 LPA",
    "notice_period": "Maximum of 30 Days",
    "shift_preference": ["Day Shift"],
    "declaration_accepted": True,
    "declaration_full_name": "Consent Applicant",
    "education": {"graduation": {"course": "B.Tech", "year_of_passing": "2020"}},
}

PARTIAL_ANSWERS = {"current_city": "Pune"}


async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1 FROM candidate_consent_events LIMIT 1"))
    except Exception:  # noqa: BLE001 - no database, or 0117 not applied
        await engine.dispose()
        pytest.skip("no database with candidate_consent_events reachable")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _Fx:
    def __init__(self) -> None:
        self.user_id = uuid.uuid4()
        self.cand_id = uuid.uuid4()
        self.email = f"consent-{uuid.uuid4().hex[:8]}@candidates.pickready.test"


async def _seed(factory, fx: _Fx) -> None:
    from app.core.db import superadmin_scope
    from app.models import Candidate, Role, User
    from app.models.enums import UserStatus

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(User(id=fx.user_id, email=fx.email, role=Role.candidate,
                           tenant_id=None, full_name="Consent Applicant",
                           status=UserStatus.active))
                await s.flush()
                s.add(Candidate(id=fx.cand_id, email=fx.email, user_id=fx.user_id,
                                full_name="Consent Applicant",
                                consent_databank=False))


async def _cleanup(factory, fx: _Fx) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(text("DELETE FROM candidates WHERE id = :c"),
                                {"c": str(fx.cand_id)})
                await s.execute(text("DELETE FROM users WHERE id = :u"),
                                {"u": str(fx.user_id)})


def _user(fx: _Fx):
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_CANDIDATE
    from app.models import Role

    return CurrentUser(user_id=fx.user_id, tenant_id=None, role=Role.candidate,
                       audience=AUDIENCE_CANDIDATE)


# ── Stage A, at the point profile creation completes ────────────────────────


async def test_a_complete_profile_is_refused_while_stage_a_is_outstanding() -> None:
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.services import consent_catalog

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        user = _user(fx)

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    with pytest.raises(HTTPException) as refused:
                        await portal_mod.save_profile_form(
                            portal_mod.ProfileFormIn(answers=dict(COMPLETE_ANSWERS)),
                            user=user, session=s,
                        )
        assert refused.value.status_code == 422
        # The refusal quotes the server's own consent wording, so the screen
        # cannot describe the requirement in words of its own.
        for key in consent_catalog.STAGE_A_REQUIRED_KEYS:
            assert consent_catalog.ITEMS_BY_KEY[key].text in refused.value.detail

        # NOTHING WAS WRITTEN. The refusal is before the answers land, so a
        # candidate is never left with a saved profile they did not consent to.
        async with factory() as s:
            async with superadmin_scope(s):
                stored = (await s.execute(
                    text("SELECT profile_form_json FROM candidates WHERE id = :c"),
                    {"c": str(fx.cand_id)},
                )).scalar_one()
        assert stored in (None, {})
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_a_partial_save_is_accepted_with_consent_outstanding() -> None:
    """The candidate is not locked out of their own half-finished profile."""
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.services import consent_catalog

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        user = _user(fx)

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    saved = await portal_mod.save_profile_form(
                        portal_mod.ProfileFormIn(answers=dict(PARTIAL_ANSWERS)),
                        user=user, session=s,
                    )
        assert saved.answers["current_city"] == "Pune"
        assert saved.complete is False
        assert saved.consent_missing == list(consent_catalog.STAGE_A_REQUIRED_KEYS)
        # The wording travels with the payload, so the screen renders the
        # server's sentences rather than authoring its own.
        assert [item["text"] for item in saved.consent_items] == [
            consent_catalog.ITEMS_BY_KEY[key].text
            for key in consent_catalog.STAGE_A_KEYS
        ]
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_ticking_both_items_completes_the_profile_and_stamps_each() -> None:
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.services import consent_catalog

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        user = _user(fx)

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    saved = await portal_mod.save_profile_form(
                        portal_mod.ProfileFormIn(
                            answers=dict(COMPLETE_ANSWERS),
                            consent_keys=list(consent_catalog.STAGE_A_KEYS),
                        ),
                        user=user, session=s,
                    )
        assert saved.complete is True
        assert saved.consent_missing == []

        # Read the stamps back from a SECOND connection: the assertion that
        # matters is committed state, not what the response said.
        async with factory() as s:
            async with superadmin_scope(s):
                rows = (await s.execute(
                    text(
                        "SELECT item_key, item_version, text_sha256, source "
                        "FROM candidate_consents WHERE candidate_id = :c "
                        "ORDER BY item_key"
                    ),
                    {"c": str(fx.cand_id)},
                )).all()
        assert {row[0] for row in rows} == set(consent_catalog.STAGE_A_KEYS)
        for key, version, digest, source in rows:
            item = consent_catalog.ITEMS_BY_KEY[key]
            assert version == item.version
            assert digest == consent_catalog.text_digest(item)
            assert source == consent_catalog.SOURCE_REGISTRATION
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_consent_given_elsewhere_is_never_asked_for_again() -> None:
    """The gate asks the TABLE, so a candidate who consented through the
    outreach form or an application completes their profile without ticking
    anything, and a candidate who registered before this existed is asked
    once rather than being shut out of the route."""
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.services import consent_catalog

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        user = _user(fx)

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await consent_catalog.record_items(
                        s, candidate_id=fx.cand_id,
                        keys=consent_catalog.STAGE_A_KEYS,
                        source=consent_catalog.SOURCE_REGISTRATION,
                    )

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    saved = await portal_mod.save_profile_form(
                        portal_mod.ProfileFormIn(answers=dict(COMPLETE_ANSWERS)),
                        user=user, session=s,
                    )
        assert saved.complete is True
        assert saved.consent_missing == []
        assert all(item["consented_at"] is not None for item in saved.consent_items)
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_the_retired_reuse_key_is_refused_by_the_registration_route() -> None:
    """Change request 23's cross-employer evidence reuse retired on
    2026-09-25 (`consent_catalog.RETIRED_KEYS`). A browser still holding the
    old catalogue that posts the key gets the route's own 422, and nothing is
    stamped: a consent to something the product no longer does is not a
    consent anybody should hold."""
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        user = _user(fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    with pytest.raises(HTTPException) as refused:
                        await portal_mod.save_profile_form(
                            portal_mod.ProfileFormIn(
                                answers=dict(COMPLETE_ANSWERS),
                                consent_keys=["cross_employer_evidence_reuse"],
                            ),
                            user=user, session=s,
                        )
        assert refused.value.status_code == 422
        assert "cross_employer_evidence_reuse" in refused.value.detail

        async with factory() as s:
            async with superadmin_scope(s):
                rows = (await s.execute(
                    text(
                        "SELECT item_key FROM candidate_consents "
                        "WHERE candidate_id = :c"
                    ),
                    {"c": str(fx.cand_id)},
                )).scalars().all()
        assert "cross_employer_evidence_reuse" not in rows
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_a_stage_b_key_is_refused_by_the_registration_route() -> None:
    """Posting an assessment item to the registration gate must not stamp it:
    the two stages are asked in different places under different wording."""
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.services import consent_catalog

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        user = _user(fx)

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    with pytest.raises(HTTPException) as refused:
                        await portal_mod.save_profile_form(
                            portal_mod.ProfileFormIn(
                                answers=dict(PARTIAL_ANSWERS),
                                consent_keys=[consent_catalog.STAGE_B_KEYS[0]],
                            ),
                            user=user, session=s,
                        )
        assert refused.value.status_code == 422

        async with factory() as s:
            async with superadmin_scope(s):
                count = (await s.execute(
                    text(
                        "SELECT count(*) FROM candidate_consents "
                        "WHERE candidate_id = :c"
                    ),
                    {"c": str(fx.cand_id)},
                )).scalar_one()
        assert count == 0
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


# ── Stage B, every item or none ─────────────────────────────────────────────


def test_a_short_stage_b_list_is_refused() -> None:
    """No database needed: the refusal is decided on the KEYS alone, which is
    why it is a separate pure function and why `record_consent` runs it before
    the idempotent shortcut reads a row."""
    from app.services import assessment_consent, consent_catalog

    partial = list(consent_catalog.STAGE_B_KEYS)[:-1]
    stage_a_smuggled = [*consent_catalog.STAGE_B_KEYS, "profile_retention"]
    for keys in ([], partial, stage_a_smuggled):
        with pytest.raises(HTTPException) as refused:
            assessment_consent.assert_all_items_ticked(list(keys))
        assert refused.value.status_code == 422
        assert refused.value.detail == assessment_consent.ITEMS_REQUIRED_DETAIL


def test_the_whole_stage_b_set_is_accepted() -> None:
    """The negative test above is only meaningful beside this one: a refusal
    that fired on everything would pass it and protect nothing."""
    from app.services import assessment_consent, consent_catalog

    assessment_consent.assert_all_items_ticked(list(consent_catalog.STAGE_B_KEYS))


def test_the_catalogue_is_the_only_author_of_consent_copy() -> None:
    """Every sentence a consent screen shows comes from `consent_catalog`.

    The two screens render `stage_payload(...)`, so this asserts the source of
    the copy rather than its words: a screen that hardcoded a sentence would
    be able to promise something the record does not say.
    """
    import pathlib

    import app as app_package

    from app.services import consent_catalog

    screens = ("assessment-consent-screen.tsx", "candidate-profile-form.tsx")
    backend = pathlib.Path(app_package.__file__).resolve().parent
    served = (backend / "api" / "assessment_conversation.py").read_text(
        encoding="utf-8"
    )
    assert "stage_payload(consent_catalog.STAGE_ASSESSMENT)" in served
    portal = (backend / "api" / "portal.py").read_text(encoding="utf-8")
    assert "consent_catalog.items_for(session, candidate.id)" in portal

    frontend = None
    for parent in pathlib.Path(__file__).resolve().parents:
        if (parent / "frontend" / "components").exists():
            frontend = parent / "frontend" / "components"
            break
    if frontend is None:
        pytest.skip("frontend tree absent: the screen half did not run")
    for name in screens:
        source = next(frontend.rglob(name)).read_text(encoding="utf-8")
        # The consent sentences are long and distinctive; any one of them
        # appearing in a screen means that screen is authoring copy.
        for item in consent_catalog.CONSENT_ITEMS:
            assert item.text not in source, f"{name} hardcodes {item.key}"


# ── Timestamps are per item, not per press ──────────────────────────────────


async def test_each_item_carries_its_own_stamp_even_in_one_operation() -> None:
    """Stage B is recorded in ONE transaction, and the brief still requires
    each item timestamped individually. One row per item is what delivers it;
    this pins the row count so a future "one row per acceptance" shortcut
    cannot pass."""
    from app.core.db import superadmin_scope
    from app.services import consent_catalog

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await consent_catalog.record_items(
                        s, candidate_id=fx.cand_id,
                        keys=consent_catalog.STAGE_B_KEYS,
                        source=consent_catalog.SOURCE_ASSESSMENT,
                    )
        async with factory() as s:
            async with superadmin_scope(s):
                rows = (await s.execute(
                    text(
                        "SELECT item_key, consented_at FROM candidate_consents "
                        "WHERE candidate_id = :c"
                    ),
                    {"c": str(fx.cand_id)},
                )).all()
                events = (await s.execute(
                    text(
                        "SELECT count(*) FROM candidate_consent_events "
                        "WHERE candidate_id = :c"
                    ),
                    {"c": str(fx.cand_id)},
                )).scalar_one()
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()

    assert len(rows) == len(consent_catalog.STAGE_B_KEYS)
    assert events == len(consent_catalog.STAGE_B_KEYS)
    assert all(
        isinstance(stamp, datetime) and stamp.tzinfo is not None
        for _key, stamp in rows
    )
    assert datetime.now(timezone.utc) >= max(stamp for _key, stamp in rows)
