"""The per-item consent catalogue: wording rules, stamps, and the one table.

Vivekium feature 6. The pure half (the catalogue itself) runs with nothing;
the database half seeds a candidate, records both stages and asserts the
stamps, including the one behaviour a UNIQUE row makes subtle: a
re-affirmation MOVES the timestamp rather than failing or duplicating.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.services import consent_catalog as cc


# ── The catalogue is what the brief specifies ────────────────────────────────


def test_the_catalogue_is_two_stages_and_seven_items():
    assert len(cc.CONSENT_ITEMS) == 7
    assert set(cc.STAGE_A_KEYS) == {"profile_retention", "media_and_records_access"}
    assert len(cc.STAGE_B_KEYS) == 5
    # Feature 4's statutory tick rides in Stage B.
    assert "statutory_identifier_verification" in cc.STAGE_B_KEYS


def test_the_wording_rules_hold_inside_the_catalogue():
    """No em dash, no forbidden relationship terms, and the sanctioned
    phrase present where the brief requires it. The platform-wide sweeps in
    test_platform_audit also cover this file; this is the nearest guard."""
    em_dash = chr(8212)
    forbidden = ("direct " + "employer", "manpower " + "agency")
    for item in cc.CONSENT_ITEMS:
        assert em_dash not in item.text, item.key
        lowered = item.text.lower()
        for term in forbidden:
            assert term not in lowered, item.key
    # The two Stage A items are the ones that name the relationship.
    for key in cc.STAGE_A_KEYS:
        assert "employer clients registered on the platform" in (
            cc.ITEMS_BY_KEY[key].text
        ), key


def test_the_statutory_tick_stores_no_numbers_by_its_own_words():
    text_ = cc.ITEMS_BY_KEY["statutory_identifier_verification"].text
    assert "No identification numbers are collected" in text_


def test_catalogue_payload_is_served_verbatim():
    payload = cc.catalogue_payload()
    assert [p["key"] for p in payload] == [i.key for i in cc.CONSENT_ITEMS]
    assert all(set(p) == {"key", "stage", "text"} for p in payload)


# ── The stamps, against a real database ──────────────────────────────────────


def _run(coro_factory):
    """One loop, one engine, per call; the per-loop binding rule."""
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
                await conn.execute(text("SELECT 1 FROM candidate_consents LIMIT 1"))
                return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            await engine.dispose()

    if not asyncio.run(_probe()):
        pytest.skip("no database with candidate_consents reachable")


def test_record_read_and_reaffirm() -> None:
    _skip_without_database()
    cid = uuid.uuid4()

    async def _flow(factory):
        from app.core.db import superadmin_scope

        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        text(
                            "INSERT INTO candidates (id, full_name, email, "
                            "consent_databank) VALUES (:i, 'Consent Subject', "
                            ":e, false)"
                        ),
                        {"i": str(cid), "e": f"{cid}@consent.test"},
                    )
                    await cc.record_items(
                        session, candidate_id=cid, keys=cc.STAGE_A_KEYS,
                        source=cc.SOURCE_REGISTRATION,
                    )
        async with factory() as session:
            async with superadmin_scope(session):
                first = await cc.items_for(session, cid)
        # Re-affirm one item; its stamp must MOVE, not duplicate or fail.
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await cc.record_items(
                        session, candidate_id=cid,
                        keys=["profile_retention"], source=cc.SOURCE_PORTAL,
                    )
        async with factory() as session:
            async with superadmin_scope(session):
                second = await cc.items_for(session, cid)
                count = (
                    await session.execute(
                        text(
                            "SELECT COUNT(*) FROM candidate_consents "
                            "WHERE candidate_id = :i"
                        ),
                        {"i": str(cid)},
                    )
                ).scalar_one()
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        text("DELETE FROM candidates WHERE id = :i"),
                        {"i": str(cid)},
                    )
        return first, second, count

    first, second, count = _run(_flow)

    by_key_first = {i["key"]: i for i in first}
    by_key_second = {i["key"]: i for i in second}

    # The FULL catalogue comes back, given or not.
    assert len(first) == len(cc.CONSENT_ITEMS)
    for key in cc.STAGE_A_KEYS:
        assert by_key_first[key]["consented_at"] is not None, key
    for key in cc.STAGE_B_KEYS:
        assert by_key_first[key]["consented_at"] is None, key

    # The re-affirmed stamp moved; the untouched one did not; no duplicates.
    assert (
        by_key_second["profile_retention"]["consented_at"]
        > by_key_first["profile_retention"]["consented_at"]
    )
    assert (
        by_key_second["media_and_records_access"]["consented_at"]
        == by_key_first["media_and_records_access"]["consented_at"]
    )
    assert count == len(cc.STAGE_A_KEYS)


def test_an_unknown_key_raises() -> None:
    _skip_without_database()

    async def _flow(factory):
        from app.core.db import superadmin_scope

        async with factory() as session:
            async with superadmin_scope(session):
                with pytest.raises(ValueError):
                    await cc.record_items(
                        session, candidate_id=uuid.uuid4(),
                        keys=["not_a_real_item"], source=cc.SOURCE_PORTAL,
                    )

    _run(_flow)
