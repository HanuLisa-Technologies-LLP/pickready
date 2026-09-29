"""The text CAPTCHA, against the real Redis (auth spec 6 and 34.1).

What is pinned: a valid challenge yields one proof; a wrong answer, an expired
challenge, a replayed proof, a proof for another purpose and a fourth guess
are all refused; the image carries no text; the store fails CLOSED; there is
no setting that turns the check off; and the session exchange refuses a
caller with no proof BEFORE it verifies the Firebase token.
"""
from __future__ import annotations

import base64
import re
import uuid

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api import auth
from app.core import cache
from app.core.config import Settings, get_settings
from app.models.enums import Role, UserStatus
from app.models.tenant import AuditLog, Tenant
from app.models.user import User
from app.schemas.auth import CaptchaVerifyIn, FirebaseSessionIn
from app.services import captcha, firebase_auth
from app.services.firebase_auth import FirebaseIdentity
from tests.captcha_support import KNOWN_ANSWER, captcha_proof



async def _redis_or_skip() -> None:
    client = cache._redis()  # noqa: SLF001
    if client is None:
        pytest.skip("no Redis client could be built for REDIS_URL")
    try:
        await client.ping()
    except (RedisError, OSError):
        pytest.skip("no Redis reachable at REDIS_URL")


async def _challenge(monkeypatch, purpose: str = "candidate_login") -> captcha.Challenge:
    monkeypatch.setattr(captcha, "_new_answer", lambda: KNOWN_ANSWER)
    return await captcha.create_challenge(purpose)


# -- the challenge ------------------------------------------------------------


async def test_a_challenge_is_an_svg_image_with_no_text_in_it(monkeypatch) -> None:
    await _redis_or_skip()
    challenge = await _challenge(monkeypatch)
    assert challenge.expires_in == 300
    assert challenge.image.startswith("data:image/svg+xml;base64,")
    svg = base64.b64decode(challenge.image.split(",", 1)[1]).decode("utf-8")
    assert svg.startswith("<svg")
    # The answer must never be in the markup: no text element, and no run of
    # the answer's characters anywhere.
    assert "<text" not in svg
    assert KNOWN_ANSWER not in svg
    assert "<path" in svg


def test_the_alphabet_has_no_look_alikes() -> None:
    for character in "0O1IL":
        assert character not in captcha.ALPHABET
    assert captcha.ANSWER_LENGTH == 6
    assert set(captcha.ALPHABET) <= set(captcha._GLYPHS)  # noqa: SLF001


def test_the_rendered_image_never_carries_the_answer_as_text() -> None:
    for _ in range(20):
        answer = captcha._new_answer()  # noqa: SLF001
        svg = captcha.render_svg(answer)
        assert "<text" not in svg and answer not in svg


async def test_redis_holds_an_hmac_never_the_answer(monkeypatch) -> None:
    await _redis_or_skip()
    challenge = await _challenge(monkeypatch)
    stored = await cache._redis().hgetall(f"captcha:{challenge.challenge_id}")  # noqa: SLF001
    assert stored["purpose"] == "candidate_login"
    assert stored["attempts"] == "0"
    assert KNOWN_ANSWER not in "".join(stored.values())
    assert KNOWN_ANSWER.lower() not in "".join(stored.values())
    ttl = await cache._redis().ttl(f"captcha:{challenge.challenge_id}")  # noqa: SLF001
    assert 0 < ttl <= 300


async def test_an_unknown_purpose_is_refused() -> None:
    with pytest.raises(HTTPException) as exc:
        await captcha.create_challenge("login")
    assert exc.value.status_code == 422


# -- verification -------------------------------------------------------------


async def test_a_right_answer_yields_one_proof_case_insensitively(monkeypatch) -> None:
    await _redis_or_skip()
    challenge = await _challenge(monkeypatch)
    proof = await captcha.verify_answer(challenge.challenge_id, " abc def ", "candidate_login")
    assert len(proof) >= 32
    # The challenge is spent by the right answer.
    with pytest.raises(captcha.CaptchaIncorrect):
        await captcha.verify_answer(challenge.challenge_id, KNOWN_ANSWER, "candidate_login")
    assert await captcha.consume_proof(proof, "candidate_login") == "candidate_login"


async def test_a_wrong_answer_is_refused(monkeypatch) -> None:
    await _redis_or_skip()
    challenge = await _challenge(monkeypatch)
    with pytest.raises(captcha.CaptchaIncorrect) as exc:
        await captcha.verify_answer(challenge.challenge_id, "ZZZZZZ", "candidate_login")
    assert exc.value.status_code == 400
    assert exc.value.reason == "wrong_answer"
    # A wrong answer with attempts left does not spend the challenge.
    assert await captcha.verify_answer(challenge.challenge_id, KNOWN_ANSWER, "candidate_login")


async def test_three_wrong_answers_spend_the_challenge(monkeypatch) -> None:
    await _redis_or_skip()
    challenge = await _challenge(monkeypatch)
    for _ in range(captcha.MAX_ATTEMPTS):
        with pytest.raises(captcha.CaptchaIncorrect):
            await captcha.verify_answer(challenge.challenge_id, "ZZZZZZ", "candidate_login")
    with pytest.raises(captcha.CaptchaIncorrect) as exc:
        await captcha.verify_answer(challenge.challenge_id, KNOWN_ANSWER, "candidate_login")
    assert exc.value.reason == "expired"


async def test_an_expired_challenge_is_refused(monkeypatch) -> None:
    await _redis_or_skip()
    challenge = await _challenge(monkeypatch)
    # Expiry is the Redis TTL; a key past it is simply absent.
    await cache._redis().delete(f"captcha:{challenge.challenge_id}")  # noqa: SLF001
    with pytest.raises(captcha.CaptchaIncorrect) as exc:
        await captcha.verify_answer(challenge.challenge_id, KNOWN_ANSWER, "candidate_login")
    assert exc.value.reason == "expired"
    assert exc.value.detail == captcha.CAPTCHA_EXPIRED_DETAIL


async def test_a_challenge_answered_for_another_purpose_is_spent(monkeypatch) -> None:
    await _redis_or_skip()
    challenge = await _challenge(monkeypatch, "company_login")
    with pytest.raises(captcha.CaptchaIncorrect) as exc:
        await captcha.verify_answer(challenge.challenge_id, KNOWN_ANSWER, "candidate_login")
    assert exc.value.reason == "purpose_mismatch"
    with pytest.raises(captcha.CaptchaIncorrect):
        await captcha.verify_answer(challenge.challenge_id, KNOWN_ANSWER, "company_login")


# -- the proof ----------------------------------------------------------------


async def test_a_proof_is_single_use() -> None:
    await _redis_or_skip()
    proof = await captcha_proof("company_login")
    await captcha.consume_proof(proof, "company_login")
    with pytest.raises(captcha.CaptchaRequired) as exc:
        await captcha.consume_proof(proof, "company_login")
    assert exc.value.status_code == 400
    assert exc.value.detail == captcha.CAPTCHA_REQUIRED_DETAIL


async def test_a_proof_is_accepted_only_for_its_purpose() -> None:
    await _redis_or_skip()
    proof = await captcha_proof("candidate_login")
    with pytest.raises(captcha.CaptchaRequired):
        await captcha.consume_proof(proof, "company_login")
    # ...and the wrong-purpose attempt spent it.
    with pytest.raises(captcha.CaptchaRequired):
        await captcha.consume_proof(proof, "candidate_login")


async def test_a_proof_may_be_offered_to_a_set_of_purposes() -> None:
    await _redis_or_skip()
    proof = await captcha_proof("invite_join")
    assert await captcha.consume_proof(proof, ["company_login", "invite_join"]) == "invite_join"


async def test_a_missing_or_invented_proof_is_refused() -> None:
    await _redis_or_skip()
    for proof in (None, "", "x" * 40):
        with pytest.raises(captcha.CaptchaRequired):
            await captcha.consume_proof(proof, "candidate_login")


async def test_a_proof_expires() -> None:
    await _redis_or_skip()
    proof = await captcha_proof("candidate_login")
    ttl = await cache._redis().ttl(captcha._proof_key(proof))  # noqa: SLF001
    assert 0 < ttl <= captcha.PROOF_TTL_SECONDS


# -- fails closed, and cannot be switched off --------------------------------


async def test_no_store_means_no_pass(monkeypatch) -> None:
    monkeypatch.setattr(cache, "_redis", lambda: None)
    with pytest.raises(captcha.CaptchaUnavailable) as exc:
        await captcha.create_challenge("candidate_login")
    assert exc.value.status_code == 503
    with pytest.raises(captcha.CaptchaUnavailable):
        await captcha.consume_proof("x" * 40, "candidate_login")


async def test_a_store_error_means_no_pass(monkeypatch) -> None:
    class Broken:
        async def getdel(self, *_a, **_k):
            raise RedisError("down")

        async def hgetall(self, *_a, **_k):
            raise RedisError("down")

    monkeypatch.setattr(cache, "_redis", lambda: Broken())
    with pytest.raises(captcha.CaptchaUnavailable):
        await captcha.consume_proof("x" * 40, "candidate_login")
    with pytest.raises(captcha.CaptchaUnavailable):
        await captcha.verify_answer("abc", "ABCDEF", "candidate_login")


def test_there_is_no_setting_that_disables_the_captcha() -> None:
    assert not [name for name in Settings.model_fields if "captcha" in name.lower()]


# -- the routes ---------------------------------------------------------------


def _limited(path: str) -> bool:
    for route in auth.router.routes:
        if getattr(route, "path", "") != path:
            continue
        for dependency in getattr(route, "dependencies", []):
            call = getattr(dependency, "dependency", None)
            if "rate_limit" in getattr(call, "__qualname__", ""):
                return True
    return False


def test_every_captcha_and_password_route_is_rate_limited() -> None:
    for path in (
        "/captcha/challenge", "/captcha/verify", "/firebase/session",
        "/password-reset/request", "/password-reset/verify", "/password-reset/complete",
        "/password-change/request", "/password-change/verify", "/password-change/complete",
    ):
        assert _limited(path), path


async def _factory_or_skip():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect():
            pass
    except OSError:
        await engine.dispose()
        pytest.skip("no database reachable at DATABASE_URL")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_a_wrong_answer_is_audited_without_the_answer(monkeypatch) -> None:
    await _redis_or_skip()
    engine, factory = await _factory_or_skip()
    try:
        challenge = await _challenge(monkeypatch, "bd_login")
        marker = f"Q{uuid.uuid4().hex[:5]}"
        async with factory() as session:
            with pytest.raises(captcha.CaptchaIncorrect):
                await auth.captcha_verify(
                    CaptchaVerifyIn(
                        challenge_id=challenge.challenge_id, answer=marker, purpose="bd_login"
                    ),
                    session,
                )
        # Read back from a SECOND connection: the refusal committed its row.
        async with factory() as session:
            rows = (await session.execute(
                select(AuditLog).where(AuditLog.action == "captcha_challenge_failed")
                .order_by(AuditLog.at.desc())
            )).scalars().all()
        assert rows, "a failed CAPTCHA leaves an audit row"
        assert rows[0].metadata_json == {"purpose": "bd_login", "reason": "wrong_answer"}
        assert all(marker not in str(row.metadata_json) for row in rows)
    finally:
        await engine.dispose()


def test_the_exchange_schema_requires_a_proof_and_a_session_purpose() -> None:
    with pytest.raises(ValidationError):
        FirebaseSessionIn(id_token="t" * 40)
    with pytest.raises(ValidationError):
        FirebaseSessionIn(id_token="t" * 40, captcha_proof="p" * 40, captcha_purpose="password_reset")
    with pytest.raises(ValidationError):
        FirebaseSessionIn(
            id_token="t" * 40, captcha_proof="p" * 40, captcha_purpose="candidate_login",
            portal="org",
        )


async def test_the_exchange_refuses_before_it_verifies_the_token(monkeypatch) -> None:
    """The boundary is the session exchange (spec 6.4): an invented proof is
    refused and Firebase is never asked."""
    await _redis_or_skip()
    asked: list[str] = []
    monkeypatch.setattr(firebase_auth, "verify_id_token", lambda token: asked.append(token))
    body = FirebaseSessionIn(
        id_token="t" * 40, captcha_proof="p" * 40, captcha_purpose="candidate_login"
    )
    with pytest.raises(captcha.CaptchaRequired):
        await auth.firebase_session(body, Response(), session=None)
    assert asked == []


async def test_a_proof_for_one_surface_cannot_open_another_portal(monkeypatch) -> None:
    """A company account signing in on the CANDIDATE surface is refused, and
    a candidate signing in on the COMPANY surface is refused: the purpose is
    the portal filter."""
    await _redis_or_skip()
    engine, factory = await _factory_or_skip()
    tenant_id = uuid.uuid4()
    email = f"surface-{uuid.uuid4().hex}@captcha.test"
    try:
        async with factory() as session:
            session.add(Tenant(id=tenant_id, name=f"Surface-{tenant_id}", domain=f"{tenant_id}.t"))
            session.add(User(role=Role.recruiter, email=email, tenant_id=tenant_id,
                             status=UserStatus.active))
            await session.commit()
        identity = FirebaseIdentity(
            uid=f"fbuid-{uuid.uuid4().hex}", email=email, name="R",
            provider="password", email_verified=True,
        )
        monkeypatch.setattr(firebase_auth, "verify_id_token", lambda _t: identity)
        async with factory() as session:
            with pytest.raises(HTTPException) as exc:
                await auth.firebase_session(
                    FirebaseSessionIn(
                        id_token="t" * 40,
                        captcha_proof=await captcha_proof("candidate_login"),
                        captcha_purpose="candidate_login",
                    ),
                    Response(), session,
                )
        assert exc.value.status_code == 403
        assert re.match(r"No candidate workspace is linked", exc.value.detail)
        async with factory() as session:
            out = await auth.firebase_session(
                FirebaseSessionIn(
                    id_token="t" * 40,
                    captcha_proof=await captcha_proof("company_login"),
                    captcha_purpose="company_login",
                ),
                Response(), session,
            )
        assert out.user is not None and out.user.role == Role.recruiter
    finally:
        async with factory() as session:
            await session.execute(User.__table__.delete().where(User.tenant_id == tenant_id))
            await session.execute(Tenant.__table__.delete().where(Tenant.id == tenant_id))
            await session.commit()
        await engine.dispose()
