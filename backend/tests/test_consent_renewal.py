"""Consent renewal exists, is single use, and restarts the cycle.

WHAT WAS WRONG
----------------
`consent_renewed_at` was READ in three places and WRITTEN IN NONE. There was no
route, no link and no confirm endpoint anywhere in the product. The reminder
letter said "sign in and confirm to stay visible"; signing in stamps
`last_engagement_at`, which is the INACTIVITY clock and answers a different
question, so every candidate advanced to `deletion_due` whatever they did. The
only thing standing between that and a mass erasure was
`consent_auto_deletion_enabled` defaulting to off.

THE ASSERTION THAT PINS THE FIX is
`test_renewing_puts_the_candidate_back_to_active`, and it reaches that verdict
through `consent_lifecycle.stage_for` rather than by restating a threshold. If
renewal wrote `consent_renewed_at` and left the two letter stamps standing, the
stage would fall straight back to the final warning step the moment the grace
window elapsed, having just told the person they were safe.

THE TOKEN IS NOT A CREDENTIAL, and three tests say so: it is spent by use, it
dies at its expiry, and it renews exactly one candidate.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.services import consent_lifecycle as cl
from app.services import consent_renewal


def test_only_the_digest_is_ever_stored() -> None:
    """A stored token is a replayable token.

    The minted secret must not appear in what goes on the row, or anybody who
    can read `candidates` can follow anybody's renewal link.
    """
    minted = consent_renewal.mint()
    assert minted.token not in minted.token_hash
    assert minted.token_hash == consent_renewal.hash_token(minted.token)
    assert len(minted.token_hash) == 64


def test_two_mints_are_never_the_same_token() -> None:
    tokens = {consent_renewal.mint().token for _ in range(50)}
    assert len(tokens) == 50


def test_the_link_points_at_a_page_and_not_at_the_api() -> None:
    """Following a link from a mailbox has to render something readable.

    It is also why the act is a POST the person presses: a GET that renewed
    would be spent by the first scanner or preview fetcher that touched the
    letter, and the person would then be told their own link was invalid.
    """
    url = consent_renewal.renewal_url("abc123")
    assert url.endswith("/keep-profile/abc123")
    assert "/api/" not in url


async def _factory_or_skip():
    engine = create_async_engine(
        get_settings().database_url, pool_size=1, max_overflow=0
    )
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        await engine.dispose()
        pytest.skip("no database reachable, skipping consent renewal test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(session, candidate_id: uuid.UUID, created_at: datetime) -> None:
    await session.execute(
        text(
            "INSERT INTO candidates (id, full_name, email, consent_databank, "
            "created_at) VALUES (:c, 'Test Person', :e, false, :created)"
        ),
        {
            "c": str(candidate_id),
            "e": f"{candidate_id}@renewal.test",
            "created": created_at,
        },
    )


async def _stage(session, candidate_id: uuid.UUID, now: datetime) -> str:
    row = (
        await session.execute(
            text(
                "SELECT created_at, consent_renewed_at, "
                "consent_reminder_sent_at, consent_final_warning_at "
                "FROM candidates WHERE id = :c"
            ),
            {"c": str(candidate_id)},
        )
    ).one()
    settings = get_settings()
    return cl.stage_for(
        now=now,
        consented_at=cl.consented_at_for(created_at=row[0], renewed_at=row[1]),
        reminder_sent_at=row[2],
        final_warning_sent_at=row[3],
        thresholds=cl.Thresholds(
            renewal_months=settings.consent_renewal_months,
            grace_days=settings.consent_grace_days,
            inactivity_months=settings.consent_inactivity_months,
        ),
    )


@pytest.mark.asyncio
async def test_renewing_puts_the_candidate_back_to_active() -> None:
    """The whole point, asserted through the lifecycle's own function."""
    engine, factory = await _factory_or_skip()
    subject = uuid.uuid4()
    now = datetime.now(timezone.utc)
    settings = get_settings()
    overdue = now - timedelta(days=30 * settings.consent_renewal_months + 1)
    grace = timedelta(days=settings.consent_grace_days)
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                await _seed(session, subject, overdue)
                # Both letters have gone out: this candidate is one grace
                # window away from erasure.
                await session.execute(
                    text(
                        "UPDATE candidates SET consent_reminder_sent_at = :r, "
                        "consent_final_warning_at = :w WHERE id = :c"
                    ),
                    {
                        "r": now - grace * 2,
                        "w": now - grace,
                        "c": str(subject),
                    },
                )
                await session.commit()

                assert await _stage(session, subject, now) == cl.STAGE_DELETION_DUE

                await consent_renewal.renew(session, subject, now=now)
                await session.commit()

                assert await _stage(session, subject, now) == cl.STAGE_ACTIVE, (
                    "renewal did not restart the cycle. Writing "
                    "consent_renewed_at without clearing the two letter stamps "
                    "drops the candidate straight back to the final warning "
                    "step, having just told them they were safe."
                )
                # And it stays active for the whole of the next cycle, rather
                # than merely for the instant of renewal.
                later = now + timedelta(
                    days=30 * settings.consent_renewal_months - 1
                )
                assert await _stage(session, subject, later) == cl.STAGE_ACTIVE
    finally:
        async with factory() as session:
            async with superadmin_scope(session):
                await session.execute(
                    text("DELETE FROM candidates WHERE id = :c"),
                    {"c": str(subject)},
                )
                await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_token_renews_one_candidate_and_is_spent_by_use() -> None:
    """Single use, and bound to one person.

    The second lookup failing is what makes a link sitting in a forwarded
    mailbox harmless, and the bystander assertion is what makes it a token
    rather than a master key.
    """
    engine, factory = await _factory_or_skip()
    subject = uuid.uuid4()
    bystander = uuid.uuid4()
    now = datetime.now(timezone.utc)
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                await _seed(session, subject, now - timedelta(days=400))
                await _seed(session, bystander, now - timedelta(days=400))
                minted = consent_renewal.mint(now=now)
                await consent_renewal.store_token(session, subject, minted)
                await session.commit()

                resolved = await consent_renewal.candidate_id_for_token(
                    session, minted.token, now=now
                )
                assert resolved == subject

                await consent_renewal.renew(session, subject, now=now)
                await session.commit()

                with pytest.raises(consent_renewal.RenewalTokenInvalid):
                    await consent_renewal.candidate_id_for_token(
                        session, minted.token, now=now
                    )

                other = (
                    await session.execute(
                        text(
                            "SELECT consent_renewed_at FROM candidates "
                            "WHERE id = :c"
                        ),
                        {"c": str(bystander)},
                    )
                ).scalar_one()
                assert other is None, (
                    "one candidate's renewal token renewed another candidate"
                )
    finally:
        async with factory() as session:
            async with superadmin_scope(session):
                for candidate in (subject, bystander):
                    await session.execute(
                        text("DELETE FROM candidates WHERE id = :c"),
                        {"c": str(candidate)},
                    )
                await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_expired_token_renews_nobody() -> None:
    """The expiry is checked inside the lookup statement, so there is no
    branch that can forget it and no row for something later to act on."""
    engine, factory = await _factory_or_skip()
    subject = uuid.uuid4()
    now = datetime.now(timezone.utc)
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                await _seed(session, subject, now - timedelta(days=400))
                minted = consent_renewal.mint(now=now)
                await consent_renewal.store_token(session, subject, minted)
                await session.commit()

                with pytest.raises(consent_renewal.RenewalTokenInvalid):
                    await consent_renewal.candidate_id_for_token(
                        session,
                        minted.token,
                        now=minted.expires_at + timedelta(seconds=1),
                    )
                # Still live one second before it dies, so the refusal above
                # is the expiry and not a broken lookup.
                assert (
                    await consent_renewal.candidate_id_for_token(
                        session,
                        minted.token,
                        now=minted.expires_at - timedelta(seconds=1),
                    )
                    == subject
                )
    finally:
        async with factory() as session:
            async with superadmin_scope(session):
                await session.execute(
                    text("DELETE FROM candidates WHERE id = :c"),
                    {"c": str(subject)},
                )
                await session.commit()
        await engine.dispose()


def test_the_letters_carry_the_link_that_makes_them_true() -> None:
    """A letter whose instruction names no control is a letter that lies.

    Both renewal letters tell the reader to confirm, and both now carry the
    slot the sweep fills with a minted link.
    """
    from app.services.email_render import DEFAULT_TEMPLATES

    for name in ("consent_renewal_reminder", "consent_final_warning"):
        _subject, body = DEFAULT_TEMPLATES[name]
        assert "{{renewal_url}}" in body, (
            f"{name} asks the reader to confirm and gives them no way to"
        )
