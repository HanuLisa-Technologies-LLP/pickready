#!/usr/bin/env python3
"""Mint a short-lived ReadyPick access token for the deploy smoke test.

WHY THIS EXISTS
---------------
The smoke test used to read `TEST_BEARER_TOKEN`, a JWT pasted into a GitHub
secret by hand. That cannot work, and the run history shows it failing two
different ways for the same underlying reason:

    run #9   {"detail":"Signature has expired"}
    run #10  {"detail":"Invalid crypto padding"}

An access token lives 15 minutes. A deploy spends about 10 of those building
images before the smoke step runs, so a token minted at push time is usually
dead on arrival -- and re-pasting it by hand just moves the race, as run #10's
malformed value shows. Worse, `promote-to-prod` runs the SAME probes after a
HUMAN approval gate, which can sit for hours. No static secret can survive that.

So the token is minted fresh, in CI, immediately before each smoke step. Nothing
is stored, nothing expires between runs, and no human refreshes anything.

WHY IT IS SAFE TO MINT HERE
---------------------------
This signs with the same `JWT_SECRET` the API verifies with, read at run time
from Secret Manager. It is NOT a new trust path: `github-deployer` already holds
roles/secretmanager.secretAccessor project-wide, and already deploys the code
that reads that secret. Minting a token it could otherwise obtain by deploying
changes nothing about what CI can do. No IAM change was needed.

Deliberately NOT done: adding a token-minting endpoint to the API. That would be
a real auth backdoor reachable from the internet, to spare a build step.

WHY STDLIB ONLY
---------------
HS256 is an HMAC and 60 lines of base64url. Doing it here keeps the smoke step
free of a `pip install` that could itself fail and turn a green deploy red for a
reason unrelated to the product.

THE IDENTITY IT SIGNS FOR
-------------------------
A seeded mock hiring-manager account, not a real person. The defaults are the
deterministic UUIDs the dev seed produces, so they are stable across deploys and
across database rebuilds. Override via the environment if the seed ever changes:

    SMOKE_USER_ID  SMOKE_TENANT_ID  SMOKE_ROLE

The role matters. `hr_manager` carries the full operational capability set under
the flat staff model, so /dashboard/summary and /jobs return real data rather
than an empty-but-200 body -- which is exactly the failure the smoke test's
capabilities check exists to catch.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time

# Seeded mock hiring manager. Not a real user, and stable by construction: the
# dev seed assigns these UUIDs deterministically.
DEFAULT_USER_ID = "20000000-0000-4000-8000-000000000002"
DEFAULT_TENANT_ID = "10000000-0000-4000-8000-000000000001"
DEFAULT_ROLE = "hr_manager"

#: Must match core/security.audience_for_role for a tenant staff role, or
#: get_tenant_db answers 403 "Org-portal session required".
AUDIENCE_ORG = "pickready:org"

#: Long enough to cover a cold start plus every probe, short enough that a
#: token leaked into a CI log is worthless by the time anyone reads it. It does
#: NOT need to cover the approval gate: promote-to-prod mints its own.
TTL_SECONDS = int(os.environ.get("SMOKE_TOKEN_TTL_SECONDS", "600"))


def _b64(raw: bytes) -> bytes:
    """base64url with padding stripped, per RFC 7515."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def mint(secret: str, user_id: str, tenant_id: str, role: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": user_id,
        "role": role,
        "tenant_id": tenant_id,
        "aud": AUDIENCE_ORG,
        "iat": now,
        # 30s of backdating absorbs clock skew between the GitHub runner and
        # Cloud Run. A token that is not yet valid fails identically to an
        # expired one, and the message would send the next reader hunting the
        # wrong end of the lifetime.
        "nbf": now - 30,
        "exp": now + TTL_SECONDS,
        "type": "access",
    }
    # separators= matters: the default json.dumps inserts spaces, which are
    # legal but make the token needlessly long.
    segments = [
        _b64(json.dumps(header, separators=(",", ":")).encode()),
        _b64(json.dumps(payload, separators=(",", ":")).encode()),
    ]
    signing_input = b".".join(segments)
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return (signing_input + b"." + _b64(signature)).decode()


def main() -> int:
    # Read from the environment, never argv: a secret in argv is visible in the
    # process table and in any command echo the runner produces.
    secret = os.environ.get("JWT_SECRET", "").strip()
    if not secret:
        print(
            "JWT_SECRET is empty. The smoke step reads it with "
            "`gcloud secrets versions access latest --secret=JWT_SECRET`; "
            "check that the deploy service account still holds "
            "roles/secretmanager.secretAccessor.",
            file=sys.stderr,
        )
        return 1

    token = mint(
        secret,
        os.environ.get("SMOKE_USER_ID", DEFAULT_USER_ID),
        os.environ.get("SMOKE_TENANT_ID", DEFAULT_TENANT_ID),
        os.environ.get("SMOKE_ROLE", DEFAULT_ROLE),
    )
    # stdout carries ONLY the token, so the caller can capture it directly.
    # Every diagnostic above goes to stderr for that reason.
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
