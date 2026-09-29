"""The text CAPTCHA every authentication surface asks for (auth spec section 6).

WHAT IT PROTECTS, AND WHERE THE BOUNDARY IS
-------------------------------------------
A person answers a challenge in the browser, the server checks the answer and
hands back a short-lived single-use PROOF, and the route that does the real
work (the session exchange, a security-code request, an invitation's password
setup, a company registration) CONSUMES that proof before it does anything.
The boundary is the consumption at the route, never the screen: a caller who
skips the page and posts to Firebase and then to `/auth/firebase/session`
directly still has to bring a proof, because the application session is the
thing worth protecting (spec 6.4).

NO THIRD-PARTY SERVICE, AND THE IMAGE IS PATHS, NEVER TEXT
----------------------------------------------------------
The challenge is drawn here, server side, as an SVG. Every glyph is a stroke
path computed from a small built-in stroke font and then rotated, scaled and
jittered, so the markup carries coordinates and never a character: an SVG
`<text>` element would put the answer in plain sight in the data URI. Noise
curves and dots are drawn in the same colours and interleaved with the glyph
paths so the element order does not separate signal from noise either.

WHAT IS STORED AND WHAT NEVER IS
--------------------------------
Redis holds `captcha:{challenge_id}` with the HMAC of the answer (keyed with
the application secret and bound to the challenge id), the purpose, the
attempt count and the creation time, for five minutes. The plaintext answer
exists only inside `create_challenge` while it draws the image. A proof is a
random token stored as its SHA-256 digest with the purpose it was issued for,
for two minutes, and it is deleted by the one read that consumes it.

FAILS CLOSED
------------
`core/cache` and `services/rate_limit` fail OPEN because they protect cost.
This protects a security decision, so a Redis that cannot answer raises
`CaptchaUnavailable` (503) and nothing is allowed: a check that "passed"
because the store was down would be no check at all. There is deliberately no
setting that turns the CAPTCHA off, in any environment.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import math
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Iterable

from fastapi import HTTPException, status

from app.core import cache
from app.core.config import get_settings

log = logging.getLogger(__name__)

__all__ = [
    "ALPHABET",
    "ANSWER_LENGTH",
    "CHALLENGE_TTL_SECONDS",
    "MAX_ATTEMPTS",
    "PROOF_TTL_SECONDS",
    "PURPOSES",
    "CaptchaIncorrect",
    "CaptchaRequired",
    "CaptchaUnavailable",
    "Challenge",
    "consume_proof",
    "create_challenge",
    "verify_answer",
]

#: Every surface that asks for a CAPTCHA (spec 6.5). A proof is accepted only
#: for the purpose it was issued for.
PURPOSES = frozenset(
    {
        "candidate_login",
        "candidate_register",
        "company_login",
        "company_register",
        "invite_join",
        "provider_login",
        "bd_login",
        "password_change",
        "password_reset",
    }
)

#: Uppercase letters and digits with the look-alikes removed: 0 and O, 1, I
#: and L (spec 6.1), and Q, whose tail is the only thing separating it from
#: the O that is already gone.
ALPHABET = "ABCDEFGHJKMNPRSTUVWXYZ23456789"
ANSWER_LENGTH = 6
CHALLENGE_TTL_SECONDS = 300
PROOF_TTL_SECONDS = 120
MAX_ATTEMPTS = 3

CAPTCHA_REQUIRED_DETAIL = "Complete the security check and try again."
CAPTCHA_INCORRECT_DETAIL = "Those characters did not match. Try the new image."
CAPTCHA_EXPIRED_DETAIL = "That security check has expired. Try the new image."
CAPTCHA_UNAVAILABLE_DETAIL = (
    "The security check is briefly unavailable. Please try again in a moment."
)


class CaptchaRequired(HTTPException):
    """No proof, an expired or replayed proof, or a proof issued for another
    purpose. One sentence for every cause: which of them it was is not
    something a caller needs, and naming it would help a script more than a
    person."""

    def __init__(self) -> None:
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=CAPTCHA_REQUIRED_DETAIL)


class CaptchaIncorrect(HTTPException):
    """The answer did not match, or the challenge it named is gone.

    `reason` is for the audit row and the log, never for the response."""

    def __init__(self, reason: str, *, expired: bool = False) -> None:
        self.reason = reason
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=CAPTCHA_EXPIRED_DETAIL if expired else CAPTCHA_INCORRECT_DETAIL,
        )


class CaptchaUnavailable(HTTPException):
    """Redis could not answer. A decision that was not made must not read as
    any outcome, least of all a pass."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=CAPTCHA_UNAVAILABLE_DETAIL,
        )


@dataclass(frozen=True)
class Challenge:
    challenge_id: str
    #: `data:image/svg+xml;base64,...`, drawn server side.
    image: str
    expires_in: int = CHALLENGE_TTL_SECONDS


# ── Storage ──────────────────────────────────────────────────────────────────


def _client():
    client = cache._redis()  # noqa: SLF001 - the shared loop-aware client and test seam
    if client is None:
        log.error("captcha.store_unavailable op=client")
        raise CaptchaUnavailable()
    return client


async def _call(operation: str, factory):
    try:
        return await factory()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - any store failure fails CLOSED
        log.error("captcha.store_unavailable op=%s err=%s", operation, type(exc).__name__)
        raise CaptchaUnavailable() from exc


def _challenge_key(challenge_id: str) -> str:
    return f"captcha:{challenge_id}"


def _proof_key(proof: str) -> str:
    return "captcha:proof:" + hashlib.sha256(proof.encode("utf-8")).hexdigest()


def _answer_mac(challenge_id: str, answer: str) -> str:
    """HMAC over (challenge id, answer), keyed with the app secret. The id in
    the message binds a hash to ITS challenge, so no answer carries over."""
    return hmac.new(
        get_settings().jwt_secret.encode("utf-8"),
        f"captcha:{challenge_id}:{answer}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _normalise(answer: str) -> str:
    """Case-insensitive, and spaces a person typed between groups are ignored."""
    return "".join((answer or "").split()).upper()


def _check_purpose(purpose: str) -> str:
    if purpose not in PURPOSES:
        raise HTTPException(status_code=422, detail="Unknown security check purpose")
    return purpose


def _new_answer() -> str:
    """A CSPRNG draw from the alphabet. A module-level function so a test can
    fix the answer it then types, which is the only way a test obtains a real
    proof: there is no bypass flag, in any environment."""
    return "".join(secrets.choice(ALPHABET) for _ in range(ANSWER_LENGTH))


# ── The image ────────────────────────────────────────────────────────────────

#: A stroke font on a 4 wide by 6 tall grid, y downwards. Each glyph is a list
#: of polylines. Hand drawn for this module; nothing is loaded at runtime.
_GLYPHS: dict[str, list[list[tuple[float, float]]]] = {
    "A": [[(0, 6), (2, 0), (4, 6)], [(0.9, 3.6), (3.1, 3.6)]],
    "B": [[(0, 6), (0, 0), (3, 0), (4, 1), (4, 2), (3, 3), (0, 3)],
          [(3, 3), (4, 4), (4, 5), (3, 6), (0, 6)]],
    "C": [[(4, 1), (3, 0), (1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5)]],
    "D": [[(0, 0), (0, 6), (2.5, 6), (4, 4.5), (4, 1.5), (2.5, 0), (0, 0)]],
    "E": [[(4, 0), (0, 0), (0, 6), (4, 6)], [(0, 3), (3, 3)]],
    "F": [[(4, 0), (0, 0), (0, 6)], [(0, 3), (3, 3)]],
    "G": [[(4, 1), (3, 0), (1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5), (4, 3.2), (2.2, 3.2)]],
    "H": [[(0, 0), (0, 6)], [(4, 0), (4, 6)], [(0, 3), (4, 3)]],
    "J": [[(4, 0), (4, 5), (3, 6), (1, 6), (0, 5)]],
    "K": [[(0, 0), (0, 6)], [(4, 0), (0, 3.6)], [(1.3, 2.5), (4, 6)]],
    "M": [[(0, 6), (0, 0), (2, 3.2), (4, 0), (4, 6)]],
    "N": [[(0, 6), (0, 0), (4, 6), (4, 0)]],
    "P": [[(0, 6), (0, 0), (3, 0), (4, 1), (4, 2), (3, 3), (0, 3)]],
    "R": [[(0, 6), (0, 0), (3, 0), (4, 1), (4, 2), (3, 3), (0, 3)], [(2, 3), (4, 6)]],
    "S": [[(4, 1), (3, 0), (1, 0), (0, 1), (0, 2), (1, 3), (3, 3), (4, 4), (4, 5), (3, 6), (1, 6), (0, 5)]],
    "T": [[(0, 0), (4, 0)], [(2, 0), (2, 6)]],
    "U": [[(0, 0), (0, 5), (1, 6), (3, 6), (4, 5), (4, 0)]],
    "V": [[(0, 0), (2, 6), (4, 0)]],
    "W": [[(0, 0), (1, 6), (2, 2.4), (3, 6), (4, 0)]],
    "X": [[(0, 0), (4, 6)], [(4, 0), (0, 6)]],
    "Y": [[(0, 0), (2, 3), (4, 0)], [(2, 3), (2, 6)]],
    "Z": [[(0, 0), (4, 0), (0, 6), (4, 6)]],
    "2": [[(0, 1), (1, 0), (3, 0), (4, 1), (4, 2), (0, 6), (4, 6)]],
    "3": [[(0, 1), (1, 0), (3, 0), (4, 1), (4, 2), (3, 3), (1.5, 3)],
          [(3, 3), (4, 4), (4, 5), (3, 6), (1, 6), (0, 5)]],
    "4": [[(3, 6), (3, 0), (0, 4), (4, 4)]],
    "5": [[(4, 0), (0, 0), (0, 3), (3, 3), (4, 4), (4, 5), (3, 6), (1, 6), (0, 5)]],
    "6": [[(4, 1), (3, 0), (1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (3, 3), (0, 3)]],
    "7": [[(0, 0), (4, 0), (1.5, 6)]],
    "8": [[(1, 3), (0, 2), (0, 1), (1, 0), (3, 0), (4, 1), (4, 2), (3, 3), (1, 3),
           (0, 4), (0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (3, 3)]],
    "9": [[(4, 3), (1, 3), (0, 2), (0, 1), (1, 0), (3, 0), (4, 1), (4, 5), (3, 6), (1, 6), (0, 5)]],
}

_WIDTH = 200
_HEIGHT = 64
#: Navy structure tones from DESIGN.md. The image carries its own light
#: background so it reads the same in the light and the dark theme.
_BACKGROUND = "#F4F6FA"
_INKS = ("#012654", "#0B3A73", "#1D4E89")


def _rand(low: float, high: float) -> float:
    return low + (high - low) * (secrets.randbelow(10_000) / 10_000)


def _glyph_paths(answer: str) -> list[str]:
    paths: list[str] = []
    slot = (_WIDTH - 24) / len(answer)
    for index, char in enumerate(answer):
        scale = _rand(5.2, 6.6)
        angle = math.radians(_rand(-22, 22))
        skew = _rand(-0.25, 0.25)
        centre_x = 12 + slot * (index + 0.5) + _rand(-3, 3)
        centre_y = _HEIGHT / 2 + _rand(-5, 5)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        width = _rand(2.2, 3.1)
        ink = _INKS[secrets.randbelow(len(_INKS))]
        for line in _GLYPHS[char]:
            points = []
            for gx, gy in line:
                # Centre the 4 x 6 grid, skew, scale, rotate, jitter, place.
                x = (gx - 2 + skew * (gy - 3)) * scale
                y = (gy - 3) * scale
                px = centre_x + x * cos_a - y * sin_a + _rand(-0.7, 0.7)
                py = centre_y + x * sin_a + y * cos_a + _rand(-0.7, 0.7)
                points.append(f"{px:.1f} {py:.1f}")
            d = "M" + " L".join(points)
            paths.append(
                f'<path d="{d}" fill="none" stroke="{ink}" stroke-width="{width:.1f}" '
                'stroke-linecap="round" stroke-linejoin="round"/>'
            )
    return paths


def _noise_paths() -> list[str]:
    paths: list[str] = []
    for _ in range(5):
        x0, y0 = _rand(0, 30), _rand(4, _HEIGHT - 4)
        x3, y3 = _rand(_WIDTH - 30, _WIDTH), _rand(4, _HEIGHT - 4)
        c1 = (_rand(40, 100), _rand(-10, _HEIGHT + 10))
        c2 = (_rand(100, 160), _rand(-10, _HEIGHT + 10))
        ink = _INKS[secrets.randbelow(len(_INKS))]
        paths.append(
            f'<path d="M{x0:.1f} {y0:.1f} C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} '
            f'{x3:.1f} {y3:.1f}" fill="none" stroke="{ink}" '
            f'stroke-width="{_rand(0.8, 1.6):.1f}" opacity="0.7"/>'
        )
    for _ in range(36):
        paths.append(
            f'<circle cx="{_rand(0, _WIDTH):.1f}" cy="{_rand(0, _HEIGHT):.1f}" '
            f'r="{_rand(0.6, 1.5):.1f}" fill="{_INKS[secrets.randbelow(len(_INKS))]}" '
            'opacity="0.6"/>'
        )
    return paths


def render_svg(answer: str) -> str:
    """The challenge image as SVG markup. Public so a test can assert the
    markup never carries the answer as text."""
    elements = _glyph_paths(answer) + _noise_paths()
    # Interleave glyph strokes and noise in a random order, so the position of
    # an element in the markup says nothing about which of the two it is.
    for i in range(len(elements) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        elements[i], elements[j] = elements[j], elements[i]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_WIDTH}" height="{_HEIGHT}" '
        f'viewBox="0 0 {_WIDTH} {_HEIGHT}">'
        f'<rect width="{_WIDTH}" height="{_HEIGHT}" rx="8" fill="{_BACKGROUND}"/>'
        + "".join(elements)
        + "</svg>"
    )


# ── The three operations ─────────────────────────────────────────────────────


async def create_challenge(purpose: str) -> Challenge:
    """Draw a fresh challenge for `purpose` and remember only its HMAC."""
    _check_purpose(purpose)
    challenge_id = uuid.uuid4().hex
    answer = _new_answer()
    client = _client()
    key = _challenge_key(challenge_id)

    async def _write():
        pipe = client.pipeline(transaction=True)
        pipe.hset(
            key,
            mapping={
                "answer_mac": _answer_mac(challenge_id, answer),
                "purpose": purpose,
                "attempts": "0",
                "created_at": str(int(time.time())),
            },
        )
        pipe.expire(key, CHALLENGE_TTL_SECONDS)
        return await pipe.execute()

    await _call("create", _write)
    image = "data:image/svg+xml;base64," + base64.b64encode(
        render_svg(answer).encode("utf-8")
    ).decode("ascii")
    return Challenge(challenge_id=challenge_id, image=image)


async def verify_answer(challenge_id: str, answer: str, purpose: str) -> str:
    """Judge one answer and return a single-use proof for `purpose`.

    The attempt is COUNTED BEFORE THE COMPARISON, so a flood of guesses spends
    the challenge rather than racing it. A challenge is spent by its third
    wrong answer and by its first right one: the success path deletes it and
    mints a proof only if that delete removed the row, so two requests racing
    with the right answer produce one proof, not two.

    Raises `CaptchaIncorrect` (400) for a wrong, expired, unknown or
    wrong-purpose challenge, and `CaptchaUnavailable` (503) when Redis cannot
    answer. Never logs the answer.
    """
    _check_purpose(purpose)
    if not challenge_id or len(challenge_id) > 64:
        raise CaptchaIncorrect("unknown", expired=True)
    client = _client()
    key = _challenge_key(challenge_id)
    stored = await _call("verify.read", lambda: client.hgetall(key))
    if not stored:
        raise CaptchaIncorrect("expired", expired=True)
    if stored.get("purpose") != purpose:
        # A challenge answered on the wrong surface is spent, not retried: the
        # surface is part of what the person was asked.
        await _call("verify.purpose_delete", lambda: client.delete(key))
        raise CaptchaIncorrect("purpose_mismatch", expired=True)
    attempts = await _call("verify.count", lambda: client.hincrby(key, "attempts", 1))
    if int(attempts) > MAX_ATTEMPTS:
        await _call("verify.exhausted_delete", lambda: client.delete(key))
        raise CaptchaIncorrect("attempts_exhausted", expired=True)
    expected = stored.get("answer_mac", "")
    offered = _answer_mac(challenge_id, _normalise(answer))
    if not hmac.compare_digest(expected, offered):
        if int(attempts) >= MAX_ATTEMPTS:
            await _call("verify.last_delete", lambda: client.delete(key))
        raise CaptchaIncorrect("wrong_answer")
    removed = await _call("verify.spend", lambda: client.delete(key))
    if not removed:
        # Another request with the same right answer got there first.
        raise CaptchaIncorrect("already_used", expired=True)
    proof = secrets.token_urlsafe(32)
    await _call(
        "verify.proof",
        lambda: client.set(_proof_key(proof), purpose, ex=PROOF_TTL_SECONDS),
    )
    return proof


async def consume_proof(proof: str | None, purpose: str | Iterable[str]) -> str:
    """Spend `proof`, which must have been issued for `purpose` (or for one of
    `purpose` when an iterable is given). Returns the purpose it was issued for.

    The read DELETES (GETDEL), so a proof is accepted exactly once whatever
    happens next, including when it turns out to have been issued for another
    surface. Raises `CaptchaRequired` for a missing, expired, replayed or
    wrong-purpose proof and `CaptchaUnavailable` when Redis cannot answer.
    """
    allowed = {purpose} if isinstance(purpose, str) else set(purpose)
    for item in allowed:
        _check_purpose(item)
    if not proof or len(proof) > 128:
        raise CaptchaRequired()
    client = _client()
    issued_for = await _call("consume", lambda: client.getdel(_proof_key(proof)))
    if issued_for is None or issued_for not in allowed:
        raise CaptchaRequired()
    return issued_for
