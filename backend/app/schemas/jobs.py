"""Job + approval FSM schemas (API_CONTRACT.md `/jobs`)."""
import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator,
)

from app.models.enums import ApprovalDecision, JobStatus
from app.models.job import REPORTING_TO_OPTIONS
from app.models.proctoring import DEFAULT_WARNING_POLICY, WARNING_POLICIES

# Job grade (spec §5/§6) — REQUIRED on the Create Job form, stored on the
# existing jobs.assessment_grade column (additive: no duplicate column).
# Drives the technical question count (20/17/15/12) and the PPI question count
# (25/20/15/10). The PPI framework itself comes from the JD, not the grade.
JobGrade = Literal["non_managerial", "managerial", "leadership", "cxo"]


class JDIn(BaseModel):
    """Structured JD fields (FR-3.1).

    Still accepted and still stored on `jd_json`, but no longer the thing a
    recruiter types: as of 2026-07-28 these sections are DERIVED by parsing
    `jd_markdown`, the unified document that is now canonical. `reportees` was
    removed entirely (client decision); an old client still sending it is
    ignored rather than rejected, so nothing 422s mid-upgrade.
    """
    model_config = ConfigDict(extra="ignore")

    description: str | None = None
    reporting_to: str | None = None
    role: str | None = None
    responsibilities: list[str] | str | None = None
    accountabilities: list[str] | str | None = None
    education: str | None = None
    skills: list[str] = []
    experience_years: float | str | None = None

    @field_validator("experience_years", mode="before")
    @classmethod
    def _experience_is_number_or_range(cls, value):
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            return None
        if not re.fullmatch(r"\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?", value):
            raise ValueError("experience_years must be a number or numeric range")
        return re.sub(r"\s*-\s*", "-", value)


#: Sanity bound on the experience band. 60 years is well past any real career
#: and stops a typo ("50" for "5") from producing a nonsense JD sentence.
MAX_EXPERIENCE_YEARS = 60

#: The widest band a recruiter may advertise (workflow Gate 2). A 0-to-12-year
#: posting is not one role: it collects freshers and architects into a single
#: candidate pool that no scorecard can rank coherently, because the rubric
#: level a Must-have needs at 1 year and at 12 is not the same level. The
#: ceiling is on the SPAN, never on the values, so 15-to-20 is as valid as
#: 0-to-5.
MAX_EXPERIENCE_SPAN_YEARS = 5


class ExperienceBandMixin(BaseModel):
    """The `experience_min_years` / `experience_max_years` pair, validated once.

    Replaces the old free-text `Level` box on the Create Job form. The rule the
    recruiter cares about is the only one enforced here: a minimum above the
    maximum is a data-entry mistake, not a band, so it is refused rather than
    quietly swapped. Swapping would publish a JD advertising a range the
    recruiter never chose.

    `grade` is SEPARATE and unaffected: it drives the assessment, these two
    describe the candidate the recruiter wants.
    """

    experience_min_years: int | None = Field(default=None, ge=0, le=MAX_EXPERIENCE_YEARS)
    experience_max_years: int | None = Field(default=None, ge=0, le=MAX_EXPERIENCE_YEARS)

    @field_validator("experience_min_years", "experience_max_years", mode="before")
    @classmethod
    def _blank_is_missing(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _min_not_above_max(self):
        low, high = self.experience_min_years, self.experience_max_years
        if low is not None and high is not None and low > high:
            raise ValueError(
                "Minimum years of experience cannot be greater than the maximum."
            )
        return self

    @model_validator(mode="after")
    def _span_within_ceiling(self):
        """Gate 2: the band may not be wider than MAX_EXPERIENCE_SPAN_YEARS.

        Refused rather than clamped. Clamping would have to choose which end of
        the recruiter's band to discard, and either choice publishes a JD
        advertising a range nobody agreed to. The message names the span it
        measured and the ceiling, because "invalid experience range" sends a
        recruiter back to the form to guess.

        Runs AFTER `_min_not_above_max` so an inverted pair is reported as the
        data-entry mistake it is rather than as an enormous span.
        """
        low, high = self.experience_min_years, self.experience_max_years
        if low is None or high is None or low > high:
            return self
        span = high - low
        if span > MAX_EXPERIENCE_SPAN_YEARS:
            raise ValueError(
                f"The experience range spans {span} years. A job may span at "
                f"most {MAX_EXPERIENCE_SPAN_YEARS} years, so raise the minimum "
                f"or lower the maximum."
            )
        return self


class JobCloseIn(BaseModel):
    """Closing a job because the hiring requirement was met (workflow Gate 8).

    The reason is OPTIONAL and free text. It is the client's own words about
    their own requisition: nothing reads it back, no model is shown it, and it
    never reaches a candidate. Bounded only so a paste of an entire email
    thread does not become a column value nobody can render.
    """

    reason: str | None = Field(default=None, max_length=1000)


class JobCreateIn(ExperienceBandMixin):
    title: str = Field(min_length=1, max_length=255)
    department: str | None = Field(default=None, max_length=255)
    level: str | None = Field(default=None, max_length=100)
    requirement_period: str | None = Field(default=None, max_length=100)
    # REQUIRED (Create Job form dropdown). Anything outside the four literals → 422.
    grade: JobGrade
    jd: JDIn
    #: The unified JD document. Optional on create because the recruiter's flow
    #: is generate -> edit -> publish: they may save a draft before the document
    #: is finished. Publishing without one is what is refused (see
    #: api/jobs.publish_job).
    jd_markdown: str | None = None
    #: Whether this create call also publishes.
    #
    # ASSUMPTION (2026-07-28): defaults to True so the established
    # create-publishes-immediately contract (PRD v1.0 §4, flat staff model) is
    # preserved for every existing caller. The new Create Job screen sends
    # `publish: false`, writes the AI draft, lets the recruiter edit it, and
    # then calls POST /jobs/{id}/publish explicitly, which is what the client
    # asked for. Additive rather than a silent behaviour change.
    publish: bool = True
    # Optional at creation: omit them and the job snapshots the company
    # profile's values (spec §3.2). Supplying one here is a per-job override
    # from the very first save.
    about_company: str | None = Field(default=None, max_length=4000)
    work_life: str | None = Field(default=None, max_length=4000)
    benefits: str | None = Field(default=None, max_length=4000)
    #: The server-side draft this job was created from (Master Directive
    #: Part 3 Rule 3). When present, the job inherits the classification that
    #: was locked to the RAW AI-generated JD at generation time. This is an
    #: opaque reference — the classification itself never travels through the
    #: client (Rule 2).
    jd_draft_id: uuid.UUID | None = None
    #: The one proctoring setting (proctoring spec section 6): what happens
    #: at the third warning. Omitted means the column default, which is to
    #: continue and note it; the product never terminates without an
    #: explicit choice.
    proctoring_warning_policy: str | None = None

    @field_validator("proctoring_warning_policy")
    @classmethod
    def _policy_in_vocabulary(cls, value: str | None) -> str | None:
        return _valid_warning_policy(value)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    department: str | None
    level: str | None
    status: JobStatus
    requirement_period: str | None
    created_by: uuid.UUID | None
    ratified_at: datetime | None
    archived_at: datetime | None = None
    assessment_status: str | None = "ready_for_candidates"
    assessment_grade: str | None = None
    #: The experience band that replaced the free-text Level field.
    experience_min_years: int | None = None
    experience_max_years: int | None = None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def grade(self) -> str:
        """Canonical job grade, mirrored from assessment_grade. Never null —
        legacy rows without a stored grade read as non_managerial."""
        return self.assessment_grade or "non_managerial"
    # Public application link (FR-3.4) — readypick.ai/apply/{job_uuid}. Populated by
    # the endpoint (not a DB column); None when the job isn't published.
    public_url: str | None = None
    #: The same absolute link under the name the 2026-07-28 spec asks for. Both
    #: are emitted (evolve additively) and are computed from one builder, so
    #: they cannot drift. This is the string the copy-link popup shows after
    #: publishing, ready to paste into LinkedIn or Naukri.
    public_application_url: str | None = None

    #: The unified, canonical, candidate-facing JD document (Markdown). Present
    #: on the list read too, because the job cards render an excerpt from it.
    jd_markdown: str | None = None

    # ── Fixed 30-day posting window (spec §2.1) ──────────────────────────────
    # The recruiter never supplies these. `posting_start_date` is stamped at
    # publish; the other two are database-generated and immutable.
    posting_start_date: datetime | None = None
    posting_end_date: datetime | None = None
    grace_period_end_date: datetime | None = None
    #: closed | scheduled | active | grace_period | expired. Computed at READ
    #: time — it depends on `now()`, so it cannot be a stored column and must
    #: never be cached (see services/job_posting). `closed` is the client's own
    #: early stop and outranks the other four.
    posting_status: str | None = None
    days_until_posting_ends: int | None = None
    days_until_grace_ends: int | None = None
    #: The one-line description shown on the recruiter's job page.
    posting_summary: str | None = None
    #: When the client closed this job because the requirement was met, and
    #: what they said about it. Team-facing only: no candidate surface renders
    #: either, because why a requisition closed is the client's business.
    closed_at: datetime | None = None
    closed_reason: str | None = None

    # ── STEM / Non-STEM classification (Master Directive Part 3 §7) ─────────
    # Visible to the client on the job card and detail page, READ-ONLY (Rule
    # 5): the label and what a report costs, nothing else. Confidence and
    # signals are Provider Portal material and are not emitted here.
    role_classification: str = "NON_STEM"
    credit_cost_per_report: float = 1.0
    #: What happens at the third proctoring warning (proctoring spec 6).
    proctoring_warning_policy: str = DEFAULT_WARNING_POLICY

    @field_validator("role_classification", mode="before")
    @classmethod
    def _classification_never_null(cls, value: object) -> object:
        # An unflushed ORM instance (unit tests, in-memory drafts) has not had
        # its column default applied yet; Part 3 §11's deployment default is
        # the same answer.
        return value or "NON_STEM"

    @field_validator("proctoring_warning_policy", mode="before")
    @classmethod
    def _policy_never_null(cls, value: object) -> object:
        # Same reason as the classification above: an unflushed row reads
        # None, and the column default is the answer.
        return value or DEFAULT_WARNING_POLICY

    @field_validator("credit_cost_per_report", mode="before")
    @classmethod
    def _cost_never_null(cls, value: object) -> object:
        return 1.0 if value is None else value


class JobDetailOut(JobOut):
    jd_json: dict
    compensation_json: dict | None

    # ── Company-narrative JD sections (spec §3.1) ────────────────────────────
    # Resolved by the endpoint: the job's own per-job override when it has one,
    # otherwise the live company profile. The client always receives text to
    # render and never has to know which layer it came from.
    about_company: str | None = None
    work_life: str | None = None
    benefits: str | None = None
    #: Which of the three the JOB overrides. The edit form uses this to show
    #: "inherited from company profile" vs "overridden for this job".
    overridden_sections: list[str] = []

    # ── Serialization mirrors (2026-07-25) ───────────────────────────────────
    # The frontend `Job` type reads `job.jd` / `job.compensation`; the canonical
    # columns are `jd_json` / `compensation_json`. Both names are emitted so the
    # established contract is preserved (CLAUDE.md: evolve additively). They are
    # computed from the canonical column and therefore can never drift.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def jd(self) -> dict:
        return self.jd_json

    @computed_field  # type: ignore[prop-decorator]
    @property
    def compensation(self) -> dict | None:
        return self.compensation_json


class PublicJobOut(BaseModel):
    """The canonical PUBLIC (unauthenticated) read of a published job — only
    the fields safe to show on the open application page. No internal ATS
    fields (status, created_by, compensation, approvals) leak here."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    department: str | None
    level: str | None
    jd_json: dict
    #: The canonical candidate-facing document. The public apply page renders
    #: this and falls back to the per-section `jd_json` only for jobs written
    #: before 2026-07-28.
    jd_markdown: str | None = None
    experience_min_years: int | None = None
    experience_max_years: int | None = None
    assessment_grade: str | None = None
    company_name: str | None = None
    # The narrative sections are candidate-facing by design — they are the part
    # of the JD written FOR the applicant — so the public page gets them too,
    # resolved through the same override -> company-profile chain.
    about_company: str | None = None
    work_life: str | None = None
    benefits: str | None = None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def jd(self) -> dict:
        """Mirror of jd_json — the public /apply page reads `jd`."""
        return self.jd_json

    @computed_field  # type: ignore[prop-decorator]
    @property
    def grade(self) -> str:
        return self.assessment_grade or "non_managerial"


# ── AI JD generation (FR-3.3 Path A) ─────────────────────────────────────────

class JDGenerateIn(ExperienceBandMixin):
    """The Create Job brief (reworked 2026-07-28).

    Three changes the client asked for, all visible here:
      * `company_context` is GONE. The company narrative lives in the three
        snapshotted company sections, so asking for it twice was noise.
      * the old "requirements brief" box is now **Skills** (`skills`). The
        previous field name survives as `key_requirements` and is folded into
        `skills`, so a client mid-deploy is never rejected.
      * the free-text Level box became the experience band (from the mixin).
        `grade` is unchanged and is a separate, required dropdown.
    """

    title: str = Field(min_length=1, max_length=255)
    #: Free text or a list. This is the renamed "Skills" box on the form.
    skills: list[str] | str = []
    #: DEPRECATED alias for `skills`, accepted so an older client keeps working.
    key_requirements: list[str] | str | None = None
    department: str | None = Field(default=None, max_length=255)
    #: Required on the form. Kept separate from the experience band: grade
    #: drives the assessment, experience describes the candidate.
    grade: JobGrade | None = None
    #: Whatever the recruiter picked from the dropdown or typed under "Others".
    reporting_to: str | None = Field(default=None, max_length=255)
    #: Required on the Create JD form, unlike on JobCreateIn where a draft may
    #: legitimately not have them yet.
    experience_min_years: int = Field(ge=0, le=MAX_EXPERIENCE_YEARS)
    experience_max_years: int = Field(ge=0, le=MAX_EXPERIENCE_YEARS)

    def merged_skills(self) -> list[str]:
        """`skills` plus the deprecated `key_requirements`, de-duplicated."""
        out: list[str] = []
        for source in (self.skills, self.key_requirements):
            if source is None:
                continue
            items = source if isinstance(source, list) else [source]
            for item in items:
                text = str(item).strip()
                if text and text not in out:
                    out.append(text)
        return out


class JDGenerateOut(BaseModel):
    """The AI draft, before any recruiter edit.

    `jd_markdown` is the canonical document; `jd` is the per-section projection
    parsed straight back out of it, ready to drop into `JobCreateIn.jd`.
    """

    jd_markdown: str
    jd: dict
    #: False when the provider chain was unavailable and the deterministic
    #: template was used, so the UI can tell the recruiter to rewrite it.
    generated_by_ai: bool = True

    # ── STEM / Non-STEM classification (Master Directive Part 3 §3 Step 3) ──
    # Classified server-side on the RAW draft the moment it was generated,
    # BEFORE the recruiter edits anything. `jd_draft_id` is what the Create
    # Job call passes back so the job inherits this exact result; the label
    # and cost are display-only (read-only badge, Part 3 §7.1).
    jd_draft_id: uuid.UUID | None = None
    role_classification: str = "NON_STEM"
    credit_cost_per_report: float = 1.0

    # ── Legacy mirrors (evolve additively) ───────────────────────────────────
    # Before 2026-07-28 this endpoint returned the JD section dict at the TOP
    # level and callers read `res.role` / `res.skills` directly. Those keys are
    # still emitted, computed from `jd`, so a client that has not been rebuilt
    # keeps working and the two can never disagree.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def description(self) -> str | None:
        return self.jd.get("description")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def role(self) -> str | None:
        return self.jd.get("role")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reporting_to(self) -> str | None:
        return self.jd.get("reporting_to")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def responsibilities(self) -> list[str]:
        return self.jd.get("responsibilities") or []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def accountabilities(self) -> list[str]:
        return self.jd.get("accountabilities") or []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def education(self) -> str | None:
        return self.jd.get("education")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def skills(self) -> list[str]:
        return self.jd.get("skills") or []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def experience_years(self) -> int | None:
        return self.jd.get("experience_years")


class JDMarkdownIn(BaseModel):
    """An explicit save of the unified JD document.

    Allowed at any time, before or after publish: the client asked for an
    always-available Edit button, and a live JD with a typo in it should be
    fixable without unpublishing the role.
    """

    jd_markdown: str = Field(min_length=1, max_length=60000)

    @field_validator("jd_markdown")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("The job description cannot be empty.")
        return value


class ReportingToOptionsOut(BaseModel):
    """The reporting-to dropdown contents.

    Served from the API rather than hardcoded in the UI so the list is one
    thing in one place. `other_value` is the sentinel that reveals the free
    text box; the value finally stored is always a plain string.
    """

    options: list[str] = list(REPORTING_TO_OPTIONS)
    other_value: str = "Others"


# ── Upload Candidate Data Bank (2026-07-28) ──────────────────────────────────

class DatabankUploadResultOut(BaseModel):
    """The outcome of ONE file in a bulk upload.

    Every file gets a row, succeeded or not. Partial success is the normal
    case: one unreadable PDF must never cost the recruiter the other 24, so
    each file is committed on its own and its failure is reported rather than
    raised.
    """

    filename: str
    ok: bool
    #: Present on success.
    candidate_id: uuid.UUID | None = None
    profile_id: uuid.UUID | None = None
    link_id: uuid.UUID | None = None
    #: The identity the resume was filed under. False when no email could be
    #: read and a placeholder identity was generated, so the recruiter knows
    #: which rows need a human to fill in a real address.
    email: str | None = None
    identified: bool = True
    #: Present on failure. Plain language, no stack traces, no vendor names.
    error: str | None = None


class DatabankUploadOut(BaseModel):
    job_id: uuid.UUID
    received: int
    created: int
    failed: int
    results: list[DatabankUploadResultOut] = []


# ── The databank invitation (workflow sections 12 and 13) ────────────────────

class DatabankInviteIn(BaseModel):
    """Which sourced candidates to invite to apply.

    Explicit link ids rather than "everybody sourced on this job". Mailing a
    batch nobody enumerated is how a recruiter contacts people they never
    looked at, which is the same argument the candidate table already makes for
    keeping tick-box selection per page.
    """

    link_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


class DatabankInviteResultOut(BaseModel):
    """One invitation attempt. A skip is reported, never swallowed."""

    link_id: uuid.UUID
    invited: bool
    candidate_name: str | None = None
    #: Why this one was skipped. Present exactly when `invited` is false.
    reason: str | None = None


class DatabankInviteOut(BaseModel):
    job_id: uuid.UUID
    requested: int
    invited: int
    skipped: int
    results: list[DatabankInviteResultOut] = []


class PublishJobOut(JobOut):
    """The publish response. Carries the link the copy popup shows."""

    #: Always populated here: publishing is what creates it.
    public_application_url: str = ""


class ApproveIn(BaseModel):
    decision: Literal["approved", "rejected"]
    remarks: str | None = None


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    level: JobStatus
    approver_user_id: uuid.UUID | None
    decision: ApprovalDecision
    remarks: str | None
    decided_at: datetime


class CompensationIn(BaseModel):
    compensation: dict

    @field_validator("compensation")
    @classmethod
    def _not_empty(cls, v: dict) -> dict:
        if not v:
            raise ValueError("compensation must not be empty")
        return v


class JDUpdateIn(BaseModel):
    jd: JDIn
    title: str | None = Field(default=None, max_length=255)
    department: str | None = Field(default=None, max_length=255)
    level: str | None = Field(default=None, max_length=100)
    # Optional grade change; omit to leave the job's grade untouched.
    grade: JobGrade | None = None


class JobPatchIn(ExperienceBandMixin):
    """PARTIAL in-place JD edit (spec §3.1) — every field optional.

    PATCH semantics, honestly implemented: a field that is ABSENT is left
    untouched. That distinction matters here because the three narrative
    sections are inheritable — sending `about_company: null` explicitly clears
    the per-job override so the job falls back to the company profile, which is
    a different intent from not mentioning the field at all. `model_fields_set`
    is what tells the two apart, so the endpoint reads that rather than testing
    for None.
    """
    title: str | None = Field(default=None, max_length=255)
    department: str | None = Field(default=None, max_length=255)
    level: str | None = Field(default=None, max_length=100)
    requirement_period: str | None = Field(default=None, max_length=100)
    grade: JobGrade | None = None
    jd: JDIn | None = None
    #: Editing the document here re-derives `jd_json` from it, same as
    #: PATCH /jobs/{id}/jd. The document stays canonical either way.
    jd_markdown: str | None = None
    about_company: str | None = Field(default=None, max_length=4000)
    work_life: str | None = Field(default=None, max_length=4000)
    benefits: str | None = Field(default=None, max_length=4000)
    #: The recruiter's third-warning choice (proctoring spec 6). Applied only
    #: when SENT and non-null; a PATCH that omits it leaves the job's setting.
    proctoring_warning_policy: str | None = None

    @field_validator("proctoring_warning_policy")
    @classmethod
    def _policy_in_vocabulary(cls, value: str | None) -> str | None:
        return _valid_warning_policy(value)


def _valid_warning_policy(value: str | None) -> str | None:
    """One of `models.proctoring.WARNING_POLICIES`, or None. Refused with the
    vocabulary named, because a typo here would silently keep the default and
    the recruiter who chose to stop assessments would never know."""
    if value is None:
        return None
    if value not in WARNING_POLICIES:
        raise ValueError(
            f"proctoring_warning_policy must be one of {', '.join(WARNING_POLICIES)}"
        )
    return value


# ── Inline candidate ranking table (spec §2) ─────────────────────────────────

class TransitionOptionOut(BaseModel):
    """One entry of the Decision / "Move to" dropdown."""

    status: str
    label: str


class RankedCandidateOut(BaseModel):
    """One row of the job page's candidate table.

    Carries the five rated comments and their WORD LABELS, and no numeric score
    of any kind — the ordering that used those scores already happened in SQL
    (services/job_candidates), so nothing downstream needs them.
    """
    model_config = ConfigDict(from_attributes=True)

    link_id: uuid.UUID
    candidate_id: uuid.UUID
    full_name: str
    #: COMPANY-JOB-CANDIDATE, e.g. "K7QP-2M4X-9TB1". Rendered under the name.
    #: One stable, readable handle for this application: names repeat, and a
    #: UUID is not something a person carries between a screen, an email and a
    #: phone call. Derived from the three ids under the app secret and one-way,
    #: so it identifies a row without disclosing anything about it. It is a
    #: LABEL, never a permission -- nothing authorises on this value.
    reference_code: str = ""
    email: str | None = None
    #: The job's grade, as a display label ("Non-managerial", "CXO", ...).
    level: str
    source: str | None = None
    tier: str | None = None
    archived_at: datetime | None = None

    #: The application's Profile. Resumes live in private storage, so this is
    #: the only handle the viewer and the download endpoint can use; a row
    #: without it renders as an unreadable resume.
    profile_id: uuid.UUID | None = None
    resume_url: str | None = None
    resume_filename: str | None = None
    resume_mime_type: str | None = None
    has_report: bool = False
    report_ready_at: datetime | None = None

    # ── Type of procurement (2026-07-28) ─────────────────────────────────────
    #: applied | sourced | databank. Display and filtering ONLY: all three go
    #: through identical parsing, matching and assessment.
    source_type: str = "applied"
    #: "Applied" / "Sourced" / "Databank", so the tag is never a raw enum.
    source_type_label: str = "Applied"

    # ── Old Profiles vs New Profiles (spec §4.2) ─────────────────────────────
    #: `old` when this application came in before the CURRENT posting window —
    #: i.e. the job has since been renewed. Presentation and billing only: an
    #: Old Profile is ranked, listed and openable exactly like a new one, which
    #: is the candidate-data-ownership promise made on the landing page.
    profile_age: str = "new"
    profile_age_label: str = "New Profile"
    #: True once someone on this team has already paid the bulk review rate for
    #: this profile, so the UI can say the reopen is free.
    review_charged: bool = False

    # ── New Candidates (workflow section 32) ─────────────────────────────────
    #: This application arrived AFTER the last assessment round on this job, so
    #: nobody has considered it yet. Presentation only, exactly like
    #: `profile_age`: it changes no score, no ranking and no access. False
    #: before the first round, when there is one pool rather than a pool plus a
    #: supplement.
    is_new_candidate: bool = False

    # ── Hiring pipeline (spec §3.3) ──────────────────────────────────────────
    #: direct | sourced — where this applicant came from.
    application_source: str | None = None
    status: str
    stage_label: str
    status_updated_at: datetime | None = None
    #: Which moves the recruiter may pick from the "Move to" dropdown. This is
    #: the MANUAL set, which deliberately excludes `shortlisted` (see
    #: services/hiring_pipeline.manual_transitions) — the UI renders exactly
    #: these, so a button never appears that would 409 and no offered option is
    #: one the client asked us to remove.
    allowed_transitions: list[str] = []
    #: The same list with human labels, so the UI never title-cases an enum.
    allowed_transition_options: list["TransitionOptionOut"] = []

    #: "ready" once matching has scored this link, else "not_scored".
    ranking_status: str

    skills_match_comment: str | None = None
    experience_comment: str | None = None
    role_alignment_comment: str | None = None
    education_comment: str | None = None
    overall_comment: str | None = None

    skills_match_label: str | None = None
    experience_label: str | None = None
    role_alignment_label: str | None = None
    education_label: str | None = None
    overall_label: str | None = None

    #: One entry per matching category this candidate was ACTUALLY scored on,
    #: in the job's own order. The flat fields above cover only the four
    #: categories the product scored every job on before the lists became per
    #: job (spec §3.2); a client should render this instead.
    categories: list[dict] = []

    #: The validation questionnaire as an explicit Q&A (spec §29), so a
    #: recruiter can read each question and the candidate's exact response on
    #: the row. Never rated, never summarised, never interpreted (spec §14).
    validation_answers: list["ValidationAnswerOut"] = []


class ValidationAnswerOut(BaseModel):
    key: str
    question: str
    #: Exactly as submitted. Null when this application predates the field or
    #: the candidate left it blank; the client says "Not answered" rather than
    #: hiding the row, because "never asked" and "did not answer" look identical
    #: when a row is simply missing and only one of them is the candidate's
    #: doing.
    answer: str | None = None
    #: "Application" for the six mandatory fields, or the candidate profile
    #: form's own section title for the 38 profile items. Client-side grouping
    #: only.
    group: str | None = None


class RankedCandidatesOut(BaseModel):
    """A page of the candidate table plus everything the pager needs."""
    job_id: uuid.UUID
    grade: str
    level: str
    results: list[RankedCandidateOut]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_previous: bool
    #: 1-indexed inclusive bounds for the "Showing X-Y of Z" header (0 when empty).
    range_start: int
    range_end: int
    #: How many candidates on this JOB arrived after the last assessment round.
    #: Counted over the whole job and never narrowed by the page's filters,
    #: because the question it answers is "how many people are waiting outside
    #: the list you are looking at".
    new_candidate_count: int = 0


class ReviewProfileOut(BaseModel):
    """What POST /jobs/{id}/candidates/{link}/review answers.

    `charged` is false for a New Profile and for a re-open, so the UI can say
    "already reviewed" instead of implying a second deduction.
    """

    profile_age: str
    charged: bool
    subunits_charged: int
