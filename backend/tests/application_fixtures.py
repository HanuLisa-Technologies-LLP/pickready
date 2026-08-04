"""Shared fixtures for tests that drive `api/portal.apply_to_job` directly.

WHY THIS MODULE EXISTS
----------------------
Three suites -- test_portal, test_candidate_profile, test_resume_upload -- call
the apply handler as a plain Python coroutine rather than over HTTP. That is a
deliberate choice (it keeps the tests on a real database without a running
server), but it has one sharp edge worth stating once, here, instead of three
times in three files:

**A direct call gets the signature's literal default, and for a form field that
default is a `Form(...)` SENTINEL OBJECT, not the value inside it.**

`validation: str = Form(default="{}")` hands a direct caller a `Form` instance.
Over HTTP, FastAPI resolves the field and the handler sees `"{}"`. So when
`validation` and `application_source` were added to the signature after these
tests were written, every positional call started feeding a `Form` instance into
`json.loads()`:

    TypeError: the JSON object must be str, bytes or bytearray, not Form

That reads like a broken apply endpoint and is not one -- the endpoint is fine;
the harness was calling it in a way no real client does. Passing these by
KEYWORD also stops a parameter inserted mid-signature from silently shifting
`user` and `session` along by one, which is the failure the handler's own
comments already warn about.

The payload is built FROM `application_validation` rather than hand-copied, so
adding a seventh mandatory field breaks these tests loudly instead of leaving
them asserting a six-field contract that no longer exists.
"""
from __future__ import annotations

import json

from app.services import application_validation

#: A complete, passing answer to all six mandatory application fields (spec §7).
#: `role_interest` is comfortably over ROLE_INTEREST_MIN_CHARS -- a shorter one
#: is rejected, which is itself asserted separately.
VALIDATION_PAYLOAD: str = json.dumps(
    {
        "current_ctc": "18,00,000",
        "expected_ctc": "24,00,000",
        "notice_period": application_validation.NOTICE_PERIOD_OPTIONS[2],
        "joining_date": "2026-09-01",
        "document_readiness": application_validation.DOCUMENT_READINESS_OPTIONS[0],
        "role_interest": (
            "The role pairs platform work with direct product ownership, which "
            "is the combination I have been looking for."
        ),
    }
)
