"""Candidate portal schemas (API_CONTRACT.md `/portal`)."""
import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from app.models.enums import JobStatus, PipelineStatus

#: E.164-ish: an optional leading '+' and 7-15 digits. Separators (spaces,
#: dashes, dots, parentheses) are accepted from the client and stripped before
#: validation and storage, matching the max_length=20 phone columns used by the
#: staff/admin schemas.
_PHONE_SEPARATORS_RE = re.compile(r"[\s\-.()]")
_PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")


class MeOut(BaseModel):
    """The signed-in user's own editable details (GET/PATCH /portal/me).

    `email` is READ-ONLY here: Firebase owns credentials and account recovery
    (claude.md rule 2), so changing it is a Firebase operation, never a
    PickReady database write.
    """

    id: uuid.UUID
    full_name: str | None
    # Nullable: a phone-only (Firebase phone provider) account has no email.
    email: str | None
    phone: str | None
    role: str


class MeUpdateIn(BaseModel):
    """PATCH /portal/me. Every field is optional; an omitted field is left
    untouched. `phone` is explicitly nullable — sending null clears it."""

    full_name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    #: Declared ONLY so that sending it produces a clear 422 instead of being
    #: silently ignored. It is never written (see _reject_email_change).
    email: str | None = Field(
        default=None,
        description="Read-only. Sending this field is rejected with 422.",
    )

    @field_validator("full_name")
    @classmethod
    def _non_blank_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("full_name cannot be blank")
        return cleaned

    @field_validator("phone")
    @classmethod
    def _valid_phone(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = _PHONE_SEPARATORS_RE.sub("", v.strip())
        if not cleaned:
            return None  # explicit clear
        if not _PHONE_RE.match(cleaned):
            raise ValueError(
                "phone must be 7-15 digits, optionally prefixed with '+'"
            )
        return cleaned

    @model_validator(mode="after")
    def _reject_email_change(self) -> "MeUpdateIn":
        if self.email is not None:
            raise ValueError(
                "email cannot be changed here, Firebase owns credentials and "
                "account recovery. Change it in your Google/Firebase account."
            )
        return self


class AspectOut(BaseModel):
    id: int
    prompt: str


class OutreachInfoOut(BaseModel):
    """What the outreach link asks the candidate to provide (FR-5.1/6.1)."""
    job_title: str | None
    company_name: str | None
    already_submitted: bool
    # Personal fields (FR-5.1 a-d) still missing on the candidate record
    personal_fields: list[str]
    # The 40 aspects minus any covered by the personal fields (FR-5.1)
    aspects: list[AspectOut]
    resume_required: bool = True
    max_employer_emails: int = 3


class OutreachSubmitOut(BaseModel):
    profile_id: uuid.UUID
    aspects_received: int
    verification_requests_created: int
    parse_task: str = "queued"


class PortalJobOut(BaseModel):
    """A published job as the candidate portal shows it.

    Carries the JD itself so the apply dialog renders the description from the
    SAME response that lists the job — no second round-trip to
    `/jobs/public/{id}`. Strictly the candidate-facing subset: no compensation,
    no created_by, no approval state, no match scores.
    """

    id: uuid.UUID
    title: str
    department: str | None
    level: str | None
    company_name: str | None
    status: JobStatus
    #: Canonical JD column (jobs.jd_json). Defaults to {} so a legacy row with
    #: a NULL JD serializes as an empty object rather than failing validation.
    jd_json: dict = {}
    #: Canonical grade column (jobs.assessment_grade); surfaced via `grade`.
    assessment_grade: str | None = None

    # ── The employer, as a candidate needs to see it ─────────────────────────
    # A candidate deciding whether to apply is choosing the company as much as
    # the role, so About/Culture travel with the job rather than requiring a
    # separate company lookup the candidate portal has no route for.
    company_about: str | None = None
    company_culture: str | None = None
    company_industry: str | None = None
    company_benefits: str | None = None

    # Serialization mirrors, matching JobDetailOut/PublicJobOut: the frontend
    # reads `jd` and `grade`, the canonical columns are `jd_json` and
    # `assessment_grade`. Computed from the canonical value, so they can never
    # drift from it.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def jd(self) -> dict:
        return self.jd_json

    @computed_field  # type: ignore[prop-decorator]
    @property
    def grade(self) -> str:
        """Never null — legacy rows without a stored grade read as
        non_managerial."""
        return self.assessment_grade or "non_managerial"


class PortalJobsOut(BaseModel):
    jobs: list[PortalJobOut]


class ApplyOut(BaseModel):
    link_id: uuid.UUID
    job_id: uuid.UUID
    profile_id: uuid.UUID
    # True when the resume was carried over from a previous application
    # (reuse_previous) rather than freshly uploaded (FR-6.2 / FR-9.2).
    resume_reused: bool = False
    aspects_received: int = 0
    parse_task: str = "queued"
    # ── Six-month retake rule (spec §5.1) ────────────────────────────────────
    # False when a recent assessment was reused, so the portal can say "nothing
    # further to do" instead of pointing at an assessment that will not exist.
    assessment_required: bool = True
    #: The sentence explaining a reuse or a retake. None on a first assessment,
    #: which needs no preamble.
    assessment_notice: str | None = None


class StatusEventOut(BaseModel):
    """One step of the candidate-visible status timeline."""
    status: str
    label: str
    at: datetime


class ApplicationOut(BaseModel):
    link_id: uuid.UUID
    job_id: uuid.UUID
    job_title: str
    company_name: str | None
    applied_at: datetime
    # Latest pipeline status; None means still in review.
    #
    # RETAINED for backwards compatibility: this is the OLD five-value enum
    # (shortlisted/rejected/hold/offered/joined) and it is null for every
    # application sitting in one of the new pipeline stages. New clients should
    # read `status`/`stage_label` below, which cover all ten.
    stage: PipelineStatus | None
    assessment_status: str | None = None
    conversation_status: str | None = None
    report_ready: bool = False

    # ── The 10-stage pipeline (spec §3.3 / §4.2) ─────────────────────────────
    status: str = "applied"
    stage_label: str = "Application received"
    status_updated_at: datetime | None = None
    timeline: list[StatusEventOut] = []

    # ── Posting window (spec §2, §5.2) ───────────────────────────────────────
    posting_status: str | None = None
    posting_end_date: datetime | None = None
    grace_period_end_date: datetime | None = None
    #: Whether this application may still be edited, and until when. The UI
    #: renders "Edit window closes in X days" from these.
    can_edit: bool = False
    edit_closes_at: datetime | None = None
    days_until_edit_closes: int = 0

    # ── Assessment access (spec §3.1) ────────────────────────────────────────
    #: True only once a recruiter has invited this candidate. The portal shows
    #: the assessment link on this, never on the application existing — an
    #: uninvited candidate who saw a link would hit a 403.
    assessment_invited: bool = False
    assessment_completed: bool = False


class ApplicationsOut(BaseModel):
    applications: list[ApplicationOut]
