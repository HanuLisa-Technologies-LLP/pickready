"""The candidate table row must carry the profile a resume is read through
(2026-08-09).

Reported symptom: resumes could not be viewed or downloaded from the recruiter
portal, and Word documents failed with "This Word document cannot be previewed
because its profile reference is missing."

Both are one defect. Resumes live in PRIVATE storage, so `resume_url` is an
object reference a browser cannot fetch and every read goes through the
authenticated `/candidates/profiles/{id}/resume-file` (or `resume-preview`)
endpoint. The job page's row SELECTed `l.profile_id` and then dropped it on the
way out, so the viewer had nothing to ask for: it fell through to its "missing
its secure profile reference" panel and the Download button pointed at a
storage scheme the browser does nothing with.

The test is on the PAYLOAD rather than on the SQL, because the SQL was already
right. A column that is selected and then not returned is exactly the shape of
bug that a query test cannot see.
"""
from __future__ import annotations

import uuid

from app.schemas.jobs import RankedCandidateOut
from app.services import job_candidates
from app.services.hiring_pipeline import APPLIED

PROFILE_ID = uuid.uuid4()


def _row(**overrides) -> dict:
    base = {
        "link_id": uuid.uuid4(),
        "candidate_id": uuid.uuid4(),
        # Selected by the real query so the row can derive its own
        # COMPANY-JOB-CANDIDATE reference code from the tenant that OWNS it,
        # rather than from whichever tenant the caller thought it was reading.
        "tenant_id": uuid.uuid4(),
        "profile_id": PROFILE_ID,
        "source": "fresh",
        "tier": None,
        "status": APPLIED,
        "status_updated_at": None,
        "application_source": "direct",
        "source_type": "applied",
        "archived_at": None,
        "breakdown": None,
        # The validation questionnaire this row carries for the recruiter's
        # Q&A column. None is what a link submitted before the fields existed
        # looks like, which is the case worth having in the default fixture.
        "validation": None,
        # The candidate's own 38-item profile questionnaire, merged alongside
        # `validation` in the same Q&A column (2026-08-16 report).
        "profile_form": None,
        "full_name": "Asha Rao",
        "email": "asha@example.com",
        "resume_url": "gs://pickready-resumes-private/resumes/abc123",
        "resume_filename": "asha-rao.docx",
        "resume_mime_type": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        "report_id": None,
        "report_ready_at": None,
        "profile_age": "new",
        # New Candidate (workflow section 32). False is the default state and
        # the common one: it is only true after an assessment round has run on
        # the job and somebody applied afterwards.
        "is_new_candidate": False,
        "review_charged": False,
    }
    base.update(overrides)
    return base


def test_the_row_carries_the_profile_the_resume_is_read_through() -> None:
    payload = job_candidates._row_payload(_row(), "Non-managerial")
    assert payload["profile_id"] == PROFILE_ID


def test_the_response_schema_exposes_it_rather_than_dropping_it_at_the_edge() -> None:
    """A payload key the schema does not declare is discarded silently by
    Pydantic, which is how a field can look present in a service test and be
    absent in the browser."""
    assert "profile_id" in RankedCandidateOut.model_fields
    out = RankedCandidateOut.model_validate(
        job_candidates._row_payload(_row(), "Non-managerial")
    )
    assert out.profile_id == PROFILE_ID


def test_a_link_with_no_profile_yet_is_a_null_rather_than_an_error() -> None:
    """`profiles` is LEFT JOINed: a link can exist before its profile row does,
    and that row must still render (as a candidate with no readable resume)."""
    payload = job_candidates._row_payload(
        _row(profile_id=None, resume_url=None, resume_filename=None), "CXO"
    )
    assert payload["profile_id"] is None
    assert RankedCandidateOut.model_validate(payload).profile_id is None


def test_the_row_still_carries_the_mime_type_the_viewer_routes_on() -> None:
    """A private object name has no extension, so the recorded MIME type is
    what tells the viewer to use the server-side DOCX renderer instead of
    handing the bytes to an iframe that renders nothing."""
    payload = job_candidates._row_payload(_row(), "Non-managerial")
    assert payload["resume_mime_type"].endswith("wordprocessingml.document")


def test_no_storage_uri_crosses_the_api_boundary() -> None:
    """The row used to serialize `profiles.resume_url` verbatim.

    That is an `s3://bucket/key` object reference (it was `gs://` before the
    AWS migration): a browser cannot fetch it, so the only thing the client
    ever did with it was ask whether it was truthy, while it handed every
    recruiter's browser the bucket name and the object key for nothing. The
    video surfaces already state the rule -- no bucket name and no object key
    crosses an API boundary -- and this field was quietly breaking it.

    So the flag is a BOOLEAN, which is the whole of what the column was
    answering. The filename and the MIME type stay: one is what a person sees
    in their Downloads folder, the other is what decides whether the DOCX
    renderer or the raw file is the right target, and neither names a bucket.
    """
    payload = job_candidates._row_payload(_row(), "Non-managerial")
    serialized = RankedCandidateOut.model_validate(payload).model_dump_json()
    for scheme in ("s3://", "gs://", "pickready-resumes-private"):
        assert scheme not in serialized, (
            f"{scheme!r} reached the client in {serialized}"
        )
    assert payload["has_resume"] is True
    assert "resume_url" not in payload
    assert "resume_url" not in RankedCandidateOut.model_fields


def test_a_row_with_no_resume_reads_as_false_rather_than_absent() -> None:
    """The empty state is a real state: the table renders a disabled control
    rather than a link that 404s."""
    payload = job_candidates._row_payload(
        _row(profile_id=None, resume_url=None, resume_filename=None), "CXO"
    )
    assert payload["has_resume"] is False
    assert RankedCandidateOut.model_validate(payload).has_resume is False


def test_no_score_leaked_into_the_row_while_adding_a_field() -> None:
    """The row is client-facing (claude.md: no numbers reach a client)."""
    payload = job_candidates._row_payload(_row(), "Non-managerial")
    assert not any(
        key in payload for key in ("score", "overall_score", "match_score", "rank")
    )
