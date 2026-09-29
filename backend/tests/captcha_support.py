"""A REAL CAPTCHA proof for tests, minted by the production path.

There is no setting, header or flag that turns the CAPTCHA off, in any
environment, and there must never be one: a bypass a test can reach is a
bypass production can reach. So a test obtains a proof the way a person does:
a challenge is created, answered and verified. The only thing fixed is the
ANSWER the challenge draws (`captcha._new_answer`, the CSPRNG draw), so the
test knows what to type. Everything else, the HMAC, the attempt count, the
single-use proof in Redis, runs exactly as it does for a browser.

Requires the suite's Redis. It does not skip: a helper that quietly skipped
would turn every test built on it back into an untested claim.
"""
from __future__ import annotations

import pytest

from app.services import captcha

KNOWN_ANSWER = "ABCDEF"


async def captcha_proof(purpose: str) -> str:
    """A fresh single-use proof for `purpose`."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(captcha, "_new_answer", lambda: KNOWN_ANSWER)
        challenge = await captcha.create_challenge(purpose)
    return await captcha.verify_answer(challenge.challenge_id, KNOWN_ANSWER.lower(), purpose)
