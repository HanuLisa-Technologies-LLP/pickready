"""The `select-candidates` invitation email must carry a real assessment link
(reported 2026-08-16).

`_queue_transition_email` was called on the `ASSESSMENT_INVITED` transition
with no `extra_context`, so `assessment_link` fell back to the empty string
in `lifecycle_email.draft`'s per-type defaults, and the last-line-of-defence
`repair_link` guard is a no-op when `expected` is empty
(`services/lifecycle_email.py:repair_link`). Candidates got an email with no
working link to the candidate portal.

`_transition_email_extra_context` is the fix: pulled out of the endpoint so
the one thing that actually needs to vary per email type is testable without
a database, mirroring the two call sites that already built this link
correctly (`api/emails.py`, `workers/tasks.py`).
"""
from __future__ import annotations

import uuid

from app.api.pipeline import _transition_email_extra_context
from app.models.email_log import (
    EMAIL_TYPE_ASSESSMENT_INVITATION,
    EMAIL_TYPE_REJECTED,
)


def _row(email: str | None = "asha@example.com") -> dict:
    return {"email": email}


def test_an_assessment_invitation_gets_a_real_working_link() -> None:
    link_id = uuid.uuid4()
    extra = _transition_email_extra_context(
        EMAIL_TYPE_ASSESSMENT_INVITATION, _row(), link_id
    )
    assert extra is not None
    link = extra["assessment_link"]
    assert link
    # Must be the signed invite-token path, not the bare guarded portal route
    # that started the 2026-08-11 defect this token scheme replaced.
    assert "/assessments/invite/" in link
    assert "/portal/assessments/" not in link


def test_a_candidate_with_no_email_still_gets_a_link_not_a_blank() -> None:
    link_id = uuid.uuid4()
    extra = _transition_email_extra_context(
        EMAIL_TYPE_ASSESSMENT_INVITATION, _row(email=None), link_id
    )
    assert extra is not None
    assert extra["assessment_link"] == f"http://localhost:3000/portal/assessments/{link_id}" or extra["assessment_link"].endswith(
        f"/portal/assessments/{link_id}"
    )


def test_an_email_type_that_needs_no_extra_context_gets_none() -> None:
    """Other transition emails (e.g. rejected) never asked for this and must
    not suddenly carry an unused assessment_link key."""
    assert (
        _transition_email_extra_context(
            EMAIL_TYPE_REJECTED, _row(), uuid.uuid4()
        )
        is None
    )
