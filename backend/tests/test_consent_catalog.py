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
    # SEVEN since 2026-09-25: change request 23's optional cross-employer
    # evidence reuse retired with the reuse it authorised
    # (`consent_catalog.RETIRED_KEYS`, `tests/test_prefill_removed.py`).
    assert len(cc.CONSENT_ITEMS) == 7
    assert set(cc.STAGE_A_KEYS) == {
        "profile_retention",
        "media_and_records_access",
    }
    assert len(cc.STAGE_B_KEYS) == 5
    # Feature 4's statutory tick rides in Stage B.
    assert "statutory_identifier_verification" in cc.STAGE_B_KEYS


def test_only_the_briefs_own_registration_items_can_block():
    """Every item in the catalogue today is required. `STAGE_A_REQUIRED_KEYS`
    is still DERIVED from `required` rather than listed, so an optional item
    added later cannot become a condition of having a profile by accident."""
    assert set(cc.STAGE_A_REQUIRED_KEYS) == {
        "profile_retention",
        "media_and_records_access",
    }
    assert all(cc.ITEMS_BY_KEY[key].required for key in cc.STAGE_A_KEYS)
    assert all(cc.ITEMS_BY_KEY[key].required for key in cc.STAGE_B_KEYS)


def test_a_retired_key_is_never_a_live_item():
    """A key cannot be retired and offered at once, and a retired key keeps a
    date so the record of when it stopped being asked is in the source."""
    assert not set(cc.RETIRED_KEYS) & set(cc.ITEMS_BY_KEY)
    for key, retired_on in cc.RETIRED_KEYS.items():
        assert len(retired_on) == 10 and retired_on[4] == "-", (key, retired_on)


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
    assert all(
        set(p) == {"key", "stage", "text", "version", "required"} for p in payload
    )


def test_stage_payload_is_the_catalogue_filtered_and_nothing_else():
    served = cc.stage_payload(cc.STAGE_ASSESSMENT)
    assert [p["key"] for p in served] == list(cc.STAGE_B_KEYS)
    assert served == [
        p for p in cc.catalogue_payload() if p["stage"] == cc.STAGE_ASSESSMENT
    ]


# ── The wording ledger: an edit without a version bump fails here ───────────

#: (key, version) -> sha256 of the text served under it. THIS IS THE POINT OF
#: THE VERSION FIELD. The catalogue is Python data, so editing `text` used to
#: silently re-describe every consent already given under the old sentence.
#: The digest lives HERE rather than beside the text so the two cannot be
#: edited in one absent-minded motion: changing a sentence fails this file,
#: and the only honest way to make it pass is to bump the item's `version` and
#: add its new digest as a NEW entry, leaving the superseded one in place.
#:
#: NEVER DELETE AN ENTRY. A superseded digest is the evidence that a
#: `candidate_consents.text_sha256` written months ago refers to real words,
#: and removing it turns a stored digest into an unresolvable hash.
PINNED_DIGESTS: dict[tuple[str, int], str] = {
    ("profile_retention", 1): (
        "b68a719f06ec592448bed8fc647b6ce998bd93f98df310f3f17a7b751f655d34"
    ),
    ("media_and_records_access", 1): (
        "ec9f9e435382a724544d5cc0edaae4c122c1acf9da9f97d153c5eb0254286bb7"
    ),
    ("job_scoped_assessment_data", 1): (
        "df878d29b35de5956da6aad0f1ba100f6059f8e13641093885b92fa92ae88802"
    ),
    ("bgv_portability", 1): (
        "f16b9483649a9f463fcba915c1b9b66b147097387cde079d35fd4a7a7e82a51a"
    ),
    ("accuracy_declaration", 1): (
        "5e5a472b413c9895457d6db9e470b89b1732c36ba8599e8d563f0e9657648bc1"
    ),
    ("hr_contact_dpdp", 1): (
        "d570d1e9bf1651df4e7bbdf8ecc642d81bf99c13bdfb491521289d904069ee0d"
    ),
    ("statutory_identifier_verification", 1): (
        "4833d447f743d6f39e5ca9495e04866cce2960ce26d5d5745bb85c2dd19be2fa"
    ),
}


def test_editing_an_item_without_bumping_its_version_fails():
    for item in cc.CONSENT_ITEMS:
        pinned = PINNED_DIGESTS.get((item.key, item.version))
        assert pinned is not None, (
            f"{item.key} v{item.version} has no pinned digest. If you changed "
            "the wording, BUMP the version and add a new PINNED_DIGESTS entry; "
            "leave the superseded one in place."
        )
        assert cc.text_digest(item) == pinned, (
            f"{item.key} v{item.version} was re-worded without a version bump. "
            "Every consent already recorded under v"
            f"{item.version} would now read as this new sentence."
        )


def test_a_superseded_digest_is_never_reused():
    """Two versions of one item sharing a digest would mean a bump with no
    change, which makes the version number a lie in the other direction."""
    by_key: dict[str, list[str]] = {}
    for (key, _version), digest in PINNED_DIGESTS.items():
        by_key.setdefault(key, []).append(digest)
    for key, digests in by_key.items():
        assert len(digests) == len(set(digests)), key


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
                await conn.execute(
                    text("SELECT 1 FROM candidate_consent_events LIMIT 1")
                )
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
                history = await cc.history_for(session, cid)
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
        # Feature 7: the consent record dies with the subject. Read AFTER the
        # delete, from a fresh session, so this is committed state and not the
        # identity map remembering what it wrote.
        async with factory() as session:
            async with superadmin_scope(session):
                surviving = (
                    await session.execute(
                        text(
                            "SELECT COUNT(*) FROM candidate_consent_events "
                            "WHERE candidate_id = :i"
                        ),
                        {"i": str(cid)},
                    )
                ).scalar_one()
        return first, second, count, history, surviving

    first, second, count, history, surviving = _run(_flow)

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

    # The version and the digest travel with the standing consent, so a later
    # rewording shows up as a consent to superseded words.
    for key in cc.STAGE_A_KEYS:
        assert by_key_second[key]["consented_version"] == cc.ITEMS_BY_KEY[key].version
        assert by_key_second[key]["wording_current"] is True
    for key in cc.STAGE_B_KEYS:
        assert by_key_first[key]["consented_version"] is None
        assert by_key_first[key]["wording_current"] is False

    # THE HISTORY IS THE POINT OF 0117. Two Stage A grants plus the
    # re-affirmation is THREE acts, where the standing rows are two: the
    # re-affirmation moved a timestamp and would otherwise have erased the
    # earlier consent without trace.
    assert len(history) == len(cc.STAGE_A_KEYS) + 1
    reaffirmations = [act for act in history if act["key"] == "profile_retention"]
    assert len(reaffirmations) == 2
    assert {act["source"] for act in reaffirmations} == {
        cc.SOURCE_REGISTRATION,
        cc.SOURCE_PORTAL,
    }
    # Each act carries the verbatim wording and its digest, so the sentence
    # survives a later edit to the catalogue.
    for act in history:
        item = cc.ITEMS_BY_KEY[act["key"]]
        assert act["text"] == item.text
        assert act["text_sha256"] == cc.text_digest(item)
        assert act["version"] == item.version

    # And it is erased with the candidate, unlike audit_log, which is exactly
    # why 0117 does not put this history there.
    assert surviving == 0


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
