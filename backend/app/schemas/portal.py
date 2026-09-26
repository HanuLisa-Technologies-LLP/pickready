"""Candidate portal schemas (API_CONTRACT.md `/portal`)."""
import re
import uuid
from datetime import datetime

from pydantic import (
    BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator,
)

from app.models.enums import JobStatus

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
    Vivekium database write.
    """

    id: uuid.UUID
    full_name: str | None
    # Nullable for history only: accounts created by the phone sign-in removed
    # on 2026-09-24 carry no email. Every new account has one.
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
    # NO `level`. The free-text level column predates the experience band and
    # the grade (2026-07-28); a candidate board that printed it beside the
    # grade showed two answers to one question. The column stays in the
    # database as history (CONTRACT S4) and nothing on this surface reads it.
    company_name: str | None
    #: Slug of the employer's PUBLIC page (/employers/{slug}), when one is
    #: visible; None when the page is hidden, so the portal never links to a
    #: URL that would 404 (2026-09-05 add-features spec).
    company_slug: str | None = None
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

    # ── This candidate's relationship to the job ─────────────────────────────
    #: True when the candidate already holds an APPLICATION on this job. A
    #: recruiter's `sourced` databank entry is not one: it is somebody else's
    #: act, and reporting it as "applied" would dead-end a candidate who is
    #: acting on the recruiter's own invitation (Gate 5).
    already_applied: bool = False
    #: The application's link id when `already_applied`, so the board can link
    #: straight to it on Applied Jobs instead of opening an apply form that can
    #: only answer 409.
    application_id: uuid.UUID | None = None

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
    """What an application (or a grace-period edit of one) answers.

    Deliberately says nothing about an assessment. Applying is not being
    assessed: the invitation is a separate, recruiter-initiated act
    (`assessment_conversations` IS the invitation), so an application answer
    that talked about "the assessment" pointed the candidate at a page that
    would refuse them.
    """

    link_id: uuid.UUID
    job_id: uuid.UUID
    profile_id: uuid.UUID
    # True when the resume was carried over from the candidate's main resume
    # (reuse_previous) rather than freshly uploaded (FR-6.2 / FR-9.2).
    resume_reused: bool = False


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
    #: Slug of the employer's PUBLIC page, same contract as PortalJobOut.
    company_slug: str | None = None
    applied_at: datetime
    # The OLD five-value `stage` projection (shortlisted/rejected/hold/
    # offered/joined, null for every newer stage) was DELETED at the 2026-09
    # compatibility cutoff: no client read it, and `status`/`stage_label`
    # below cover all ten stages.
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


class UpdateOut(BaseModel):
    """One row of the candidate's Updates feed (workflow section 14).

    NO SCORE, NO GRADE, NO RANK and no number describing the candidate: this is
    a client-facing surface and the no-numbers rule applies in full. The copy
    is a fixed catalogue in `services/candidate_updates`, so nothing a model
    wrote reaches it.
    """

    id: uuid.UUID
    kind: str
    title: str
    body: str
    #: A relative portal path, or null when this update is information rather
    #: than an action. Relative by database CHECK, so a stored row can never
    #: turn the Updates page into somebody else's redirector.
    link_path: str | None = None
    job_title: str | None = None
    company_name: str | None = None
    #: Whether the same event also went out by email, so the candidate knows
    #: whether to look in their inbox for the detail.
    emailed: bool = False
    read_at: datetime | None = None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unread(self) -> bool:
        return self.read_at is None

    model_config = ConfigDict(from_attributes=True)


class UpdatesOut(BaseModel):
    updates: list[UpdateOut]
    #: Unread across the WHOLE feed, not this page. It drives the nav badge,
    #: which has to be right on page two as well as page one.
    unread_count: int
    total: int
    page: int
    page_size: int
    has_next: bool


class UpdatesSummaryOut(BaseModel):
    """Just the badge. A dedicated route so the nav does not fetch a page of
    rows it will not render on every navigation."""

    unread_count: int


class MarkUpdatesReadIn(BaseModel):
    """Which updates to mark read.

    Omitting `ids` marks the whole feed read, which is what opening the page
    does. Passing them explicitly exists so a future per-row control does not
    have to invent a second route.
    """

    ids: list[uuid.UUID] | None = None


class DeletionNoticeOut(BaseModel):
    """The smart warning screen, authored by the server (feature 7).

    Every string here comes from `services/account_deletion`. The screen
    renders them and writes none of its own, for the reason
    `components/permission-notice.tsx` exists: a client that authors copy about
    a server-side rule keeps promising whatever it promised on the day it was
    written, and this particular rule is irreversible.
    """

    heading: str
    warnings: list[str]
    confirmation_phrase: str
    instruction: str


class DeleteMeIn(BaseModel):
    """DELETE /portal/me. The typed confirmation, checked on the SERVER.

    A confirmation only the browser checks is a speed bump: the route is
    reachable by anything holding the session cookie. Same shape as the
    Provider's tenant delete, where the operator retypes the company name.
    """

    confirmation: str = Field(
        max_length=40,
        description="Must be the phrase from GET /portal/me/deletion-notice.",
    )


class DeleteMeOut(BaseModel):
    """What the erasure actually did. Counts, never content.

    Mirrors `erasure.ErasureReceipt` rather than inventing a second vocabulary
    for the same event, so the number the candidate is shown and the number in
    the audit row are the same number.
    """

    deleted: bool
    #: Echoed so a support conversation can be had about a specific erasure
    #: without anybody needing to find the candidate row, which is gone.
    candidate_id: uuid.UUID
    erased_at: datetime
    chunks_deleted: int
    profile_vectors_cleared: int
    projects_deleted: int
    cache_keys_deleted: int
    sign_in_accounts_deleted: int
    #: How many stored files (resumes, recordings, staged originals, files on
    #: the candidate's conversations) this erasure has to remove. Reported
    #: because the row half and the OBJECT half of an erasure finish at
    #: different moments, and telling somebody "deleted" while an unknown
    #: number of their documents are still queued is the kind of half-truth
    #: `deletion_state` exists to replace.
    objects_total: int
    #: Where the erasure stands, from `services/deletion_requests`. It is
    #: `rows_erased` when the request returns, which is the honest word for
    #: "you are out of the product and your stored files are being removed";
    #: the object pass runs in `pickready.cascade_erasure` and is swept until
    #: every file is verifiably gone.
    deletion_state: str
    #: Whether the Firebase sign-in identity was deleted with the profile. True
    #: in the ordinary case. False only when the same address is ALSO a staff
    #: sign-in on this platform, because that identity is not the candidate's
    #: alone to delete; `sign_in_identity_note` then says so in the server's
    #: words.
    sign_in_identity_deleted: bool = False
    sign_in_identity_note: str | None = None


class RenewConsentIn(BaseModel):
    """POST /portal/consent/renew. The one-click link's own payload.

    The token is the whole authorization and it authorises exactly one act. It
    is never a session, never identifies the holder to any other route, and is
    consumed by the renewal itself. See `services/consent_renewal`.
    """

    token: str = Field(min_length=16, max_length=200)


class ConsentRenewalOut(BaseModel):
    """GET /portal/me/consent/renewal: where the candidate stands on the
    "keep my profile" cycle (feature 8).

    Every value is DERIVED by `services/consent_lifecycle` from timestamps on
    the candidate row, exactly as the sweep derives it, so the card and the
    sweep cannot disagree. `message` is the server's sentence.
    """

    #: When the current consent period began (registration or last renewal).
    consented_at: datetime
    #: When confirmation is next asked for.
    renewal_due_at: datetime
    #: `consent_lifecycle` stage: active | reminder_due | final_warning_due |
    #: deletion_due. A stage, never a count of days.
    stage: str
    #: True once `renewal_due_at` has passed, whatever letters have gone out.
    renewal_needed: bool
    message: str


class ConsentRenewedOut(BaseModel):
    """What a renewal answers, on both the signed-in and the one-click path.

    `message` is the SERVER's sentence, for the reason every other rule
    sentence in this product is: the page must not be able to promise
    something different from what was written.
    """

    renewed: bool
    renewed_at: datetime
    message: str


class ApplicationsOut(BaseModel):
    applications: list[ApplicationOut]


# ── Background verification (add-features spec 2026-09-05) ──────────────────
#
# Candidate-owned surface: the candidate names up to two previous employers'
# departmental mailboxes, watches each inquiry's status in words, reads the
# parsed reply, and controls per-tenant sharing. No score and no number is
# involved anywhere in this feature; every state is a word.


class BGVInquiryCreateIn(BaseModel):
    """One previous employer and their departmental mailbox."""

    employer_name: str = Field(min_length=2, max_length=200)
    departmental_email: str = Field(max_length=320)

    @field_validator("employer_name", "departmental_email")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class BGVShareConsentOut(BaseModel):
    tenant_id: uuid.UUID
    #: The employer's display name, so the card labels the toggle in words.
    tenant_name: str | None = None
    consented_at: datetime


class BGVInquiryOut(BaseModel):
    id: uuid.UUID
    employer_name: str
    departmental_email: str
    #: matched | mismatched | indeterminate. Provenance, never a gate.
    domain_match_result: str
    #: collected | dispatched | dispatch_failed | response_received |
    #: parsed | parse_failed
    status: str
    inquiry_sent_at: datetime | None
    response_received_at: datetime | None
    #: Exactly the seven spec fields once parsed, otherwise None. The raw
    #: reply email is never serialized to anyone.
    parsed_fields: dict | None = None
    #: Tenants this inquiry's result is currently shared with.
    consents: list[BGVShareConsentOut] = Field(default_factory=list)


class BGVShareableTenantOut(BaseModel):
    """A tenant the candidate has an application with, i.e. one they may
    grant or revoke BGV visibility for."""

    tenant_id: uuid.UUID
    tenant_name: str | None = None


class BGVListOut(BaseModel):
    inquiries: list[BGVInquiryOut]
    shareable_tenants: list[BGVShareableTenantOut]
    #: True while the candidate may still add another inquiry (the spec
    #: collects the previous TWO employers).
    can_add: bool


class BGVConsentIn(BaseModel):
    """Grant or revoke visibility of ONE inquiry to ONE tenant."""

    tenant_id: uuid.UUID
    granted: bool
