"""The job page's ranked candidate table: the row and the page.

EVERY FIELD THE SERIALIZER RETURNS IS DECLARED HERE, AND AN UNDECLARED ONE
FAILS. The previous row schema (`schemas/jobs.RankedCandidateOut`) ignored
extra keys, so the seven recruiter columns added on 2026-09-18 (the CTC,
notice and education words, the BGV status) were computed by the serializer,
dropped by pydantic, and never reached a browser: the table read its empty
word in every row in production while the serializer's own tests passed
(PLAN-p2 NF-1). `extra="forbid"` turns that silence into a 500 on the first
request, and `tests/test_ranked_candidates_api.py` asserts the RESPONSE JSON
rather than the serializer, which is where the bug was invisible.

NO NUMBER CROSSES THIS BOUNDARY (D3, with no exception since the Vivekium
release). There is no score, percentage, rank, weight or band index anywhere
on the row: the AI Match is a word, its tags are text, its provenance is
sentences. The only integers are the pager's counts.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class TransitionOptionOut(BaseModel):
    """One entry of the Decision / "Move to" dropdown."""

    model_config = ConfigDict(extra="forbid")

    status: str
    label: str


class ValidationAnswerOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    question: str
    #: Exactly as submitted. Null when this application predates the field or
    #: the candidate left it blank; the client says "Not answered" rather than
    #: hiding the row, because "never asked" and "did not answer" look
    #: identical when a row is simply missing and only one of them is the
    #: candidate's doing.
    answer: str | None = None
    #: "Application" for the mandatory fields, or the candidate profile form's
    #: own section title. Client-side grouping only.
    group: str | None = None


class EvidenceTagOut(BaseModel):
    """One piece of evidence the resume check found, or a Must-have it did not.

    `text` is a skill's CURRENT name (resolved at read time, so a rename moves
    the label and never the order) or a short phrase the check wrote and the
    server vetted (no digit, at most five words). `shown_in_row` is the
    server's decision about which tags fit on the row; the Details dialog
    shows every tag.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    polarity: Literal["positive", "negative"]
    shown_in_row: bool = False


class RankedCandidateOut(BaseModel):
    """One row of the job page's candidate table. Words only."""

    model_config = ConfigDict(extra="forbid")

    link_id: uuid.UUID
    candidate_id: uuid.UUID
    full_name: str
    #: COMPANY-JOB-CANDIDATE, rendered under the name. A LABEL, never a
    #: permission: nothing authorises on this value.
    reference_code: str = ""
    email: str | None = None
    #: The older databank|fresh retrieval marker. `source_type` answers the
    #: procurement question the table shows.
    source: str | None = None
    archived_at: datetime | None = None

    # ── The resume ───────────────────────────────────────────────────────────
    #: The only handle the viewer and the download endpoint accept: resumes
    #: live in private storage and no storage URI crosses this boundary.
    profile_id: uuid.UUID | None = None
    has_resume: bool = False
    resume_filename: str | None = None
    resume_mime_type: str | None = None

    # ── Reports ──────────────────────────────────────────────────────────────
    has_report: bool = False
    report_ready_at: datetime | None = None
    #: The assessment mode and video words (`assessment_mode`,
    #: `assessment_mode_label`, `video_status`) and the table's "Assessment"
    #: column went in the stage 3 final sweeps: one assessment mode is left,
    #: so the mode word told a recruiter nothing, and the recording is
    #: reached from the report's own video section (`api/videos`), which
    #: still carries both words.
    prism_report_status: str = "Not available"
    proctoring_report_status: str = "Not available"

    # ── Type of procurement ──────────────────────────────────────────────────
    #: applied | sourced | databank. Display only: all three are parsed,
    #: matched and assessed identically.
    source_type: str = "applied"
    source_type_label: str = "Applied"
    #: "Databank, not an applicant" / "Sourced, not an applicant" while the
    #: candidate has not applied; null once they have (or always, for an
    #: applicant). Somebody AI Matching found is not somebody who asked for
    #: this job, and the row says so.
    applicant_label: str | None = None

    # ── Old and New Profiles, New Candidates ─────────────────────────────────
    profile_age: str = "new"
    profile_age_label: str = "New Profile"
    review_charged: bool = False
    is_new_candidate: bool = False

    # ── Hiring pipeline ──────────────────────────────────────────────────────
    application_source: str | None = None
    status: str
    stage_label: str
    status_updated_at: datetime | None = None
    allowed_transitions: list[str] = []
    allowed_transition_options: list[TransitionOptionOut] = []

    # ── The recruiter columns (derived words, never stored) ──────────────────
    #: The application answers as an explicit Q&A, never rated.
    validation_answers: list[ValidationAnswerOut] = []
    #: "Within range" / "Above range" / "Below range", null when not stated.
    ctc_match_label: str | None = None
    notice_period_label: str | None = None
    education_match_label: str | None = None
    #: The BGV status CODE (read by filters) and its display word.
    bgv_status: str
    bgv_status_label: str

    # ── AI Match (Yukti), words only ─────────────────────────────────────────
    #: What the resume check holds for this row. `legacy` is a grade carried
    #: over from the retired matcher until AI Matching runs again.
    ai_match_status: Literal["pending", "scored", "not_assessed", "legacy"]
    #: One of the four grade words, blended with the Tatva Assessment once
    #: there is one, or null when there is no grade at all.
    ai_match_label: str | None = None
    #: "Not checked yet" / "Not assessed" when there is no grade, else null.
    ai_match_status_word: str | None = None
    evidence_tags: list[EvidenceTagOut] = []
    #: Where the grade came from, in sentences.
    provenance: list[str] = []
    #: The skills or the resume changed after the check, or the grade predates
    #: evidence tags: running AI Matching again would refresh it.
    ai_match_stale: bool = False


class RankedCandidatesOut(BaseModel):
    """A page of the candidate table plus everything the pager needs."""

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    #: The job's grade enum value. Shown nowhere as a column: the grade is a
    #: property of the job, not of a candidate.
    grade: str
    #: The one line above the table, written by the server:
    #: "Resume check only. Real skills are tested in the assessment." until
    #: somebody on the job has been assessed, then a sentence that says how the
    #: assessment moves the order, in words.
    ranking_header: str
    results: list[RankedCandidateOut]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_previous: bool
    #: 1-indexed inclusive bounds for "Showing X-Y of Z" (0 when empty).
    range_start: int
    range_end: int
    #: How many candidates on this JOB arrived after the last assessment
    #: round, counted over the whole job and never narrowed by the filters.
    new_candidate_count: int = 0
