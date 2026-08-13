"""Client company schemas (API_CONTRACT.md `/companies`)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models.enums import APPROVAL_CHAIN


class CompanyPageIn(BaseModel):
    brief: str | None = None
    culture: str | None = None
    policies: str | None = None
    benefits: str | None = None


class CompanyPageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    brief: str | None
    culture: str | None
    policies: str | None
    benefits: str | None
    approval_levels_config: dict | None


# ── Company Profile (spec §3.2 — the page formerly called Settings) ──────────
# The three narrative sections every new job snapshots. Read-only company
# identity travels alongside so the page can render without a second request.

_PROFILE_MAX = 4000


class CompanyProfileIn(BaseModel):
    """PATCH body. Every field optional — an ABSENT field is left untouched,
    an explicit null clears the section. `model_fields_set` distinguishes them
    (the endpoint reads that, not `is None`)."""
    about_company: str | None = Field(default=None, max_length=_PROFILE_MAX)
    work_life: str | None = Field(default=None, max_length=_PROFILE_MAX)
    benefits: str | None = Field(default=None, max_length=_PROFILE_MAX)


class CompanyProfileOut(BaseModel):
    """The Company Profile page's read model.

    `company_name` and `industry` are read-only identity, sourced from the
    tenant rather than the company row — they are not editable here.
    """
    tenant_id: uuid.UUID
    company_name: str
    industry: str | None = None
    about_company: str | None = None
    work_life: str | None = None
    benefits: str | None = None
    #: Advisory only — the UI shows a soft hint, the API does not reject short
    #: or long text. Blocking a save because a paragraph is 480 characters
    #: would lose the recruiter's work for no real benefit.
    recommended_min_chars: int = 500
    recommended_max_chars: int = 1000


# ── Per-user permission matrix (spec §7.1) ───────────────────────────────────

class StaffPermissionsIn(BaseModel):
    """The FULL set of capability pins for this person.

    Replaces the stored overlay rather than merging into it: omitting a
    capability returns it to the role default, which is what unticking a box on
    the permissions screen should mean.
    """
    overrides: dict[str, bool] = {}


class StaffPermissionsOut(BaseModel):
    """Effective permissions, plus enough provenance to render the UI honestly."""
    user_id: uuid.UUID
    role: str
    full_name: str | None = None
    email: str | None = None
    #: Every capability that exists, so the screen can render a complete list
    #: without hardcoding one that then drifts from the backend.
    all_capabilities: list[str]
    #: Granted by this person's ROLE, before any per-user pin.
    role_defaults: list[str]
    #: The explicit per-user pins. Sparse — absent means "follow the role".
    overrides: dict[str, bool]
    #: What actually applies: role defaults with the overlay on top.
    effective: list[str]
    #: The role's display name in the hierarchy (spec §29), so the screen names
    #: it the same way the org chart does.
    role_label: str | None = None
    #: What the CALLER may grant: exactly what the caller holds. A hierarchy
    #: whose managers can grant what they do not hold is a ladder, not a
    #: ceiling, so the screen must not offer a switch the server will refuse.
    grantable: list[str] = []


class StaffCreateIn(BaseModel):
    """POST /companies/me/staff (contract rev 2). `role` is a plain string so
    unknown/forbidden roles surface as an explicit 400 in the handler (the
    contract mandates 400, not a 422 validation error)."""
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    role: str = Field(min_length=1, max_length=30)
    approval_level: str | None = None  # a JobStatus value if pre-assigned (HMs only)

    @model_validator(mode="after")
    def _valid_level(self) -> "StaffCreateIn":
        if self.approval_level is not None:
            valid = {s.value for s in APPROVAL_CHAIN}
            if self.approval_level not in valid:
                raise ValueError(f"approval_level must be one of {sorted(valid)}")
        return self


class StaffUpdateIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    role: str = Field(min_length=1, max_length=30)
    approval_level: str | None = None

    @model_validator(mode="after")
    def _valid_level(self) -> "StaffUpdateIn":
        if self.approval_level is not None:
            valid = {s.value for s in APPROVAL_CHAIN}
            if self.approval_level not in valid:
                raise ValueError(f"approval_level must be one of {sorted(valid)}")
        return self


class StaffOut(BaseModel):
    """A team member row for /org/staff.

    The invite fields are the difference between a usable and a broken invite
    flow: `invite_link` is returned for a freshly minted invitation so an admin
    can copy it manually when email delivery is not configured, and
    `email_dispatch` states honestly whether the invite email could actually be
    delivered by the configured SMTP sender.
    """
    id: uuid.UUID  # user id
    email: str
    full_name: str | None
    phone: str | None
    role: str
    status: str
    approval_level: str | None = None  # hiring managers only
    created_at: datetime | None = None
    # Invite lifecycle — pending | accepted | revoked | expired | None (no invite)
    invite_status: str | None = None
    invite_sent_at: datetime | None = None
    invite_expires_at: datetime | None = None
    # Raw single-use link. Only ever populated on the response to the request
    # that MINTED the token (create / resend) — never on plain list reads,
    # because the token is stored hashed and cannot be recovered.
    invite_link: str | None = None
    # "queued" | "not_configured" — set on create/resend only.
    email_dispatch: str | None = None


class PublicInviteOut(BaseModel):
    """GET /companies/invites/{token} — unauthenticated view shown on /join.
    Deliberately minimal: enough to explain the invitation, nothing more."""
    email: str
    full_name: str | None
    role: str
    company_name: str
    invited_by_name: str | None
    expires_at: datetime
    status: str  # always "pending" here; other states return 410


class InviteAcceptOut(BaseModel):
    accepted: bool
    role: str
    company_name: str


class ApprovalLevelEntry(BaseModel):
    active: bool = False
    approver_user_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _approver_required_when_active(self) -> "ApprovalLevelEntry":
        if self.active and self.approver_user_id is None:
            raise ValueError("an active level requires approver_user_id")
        return self


class ApprovalLevelsIn(BaseModel):
    config: dict[str, ApprovalLevelEntry]

    @model_validator(mode="after")
    def _only_known_levels(self) -> "ApprovalLevelsIn":
        valid = {s.value for s in APPROVAL_CHAIN}
        unknown = set(self.config) - valid
        if unknown:
            raise ValueError(f"unknown approval levels: {sorted(unknown)}")
        return self


class ApprovalLevelsOut(BaseModel):
    config: dict[str, ApprovalLevelEntry]


class EmailTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1)


class EmailTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    subject: str
    body: str
    version: int
    is_active: bool
    created_at: datetime


class CompanyProfileResearchOut(BaseModel):
    """A researched DRAFT of the three profile sections (spec §30).

    Deliberately not the same shape as `CompanyProfileIn`: this is a proposal,
    not a submission. A client applies it by sending the sections it keeps to
    PATCH /companies/me/profile, which is the explicit human step the client
    asked for.
    """

    about_company: str = ""
    work_life: str = ""
    benefits: str = ""
    #: The pages the sections were written from, so a recruiter can check them.
    #: Never a social-media host: `company_research.is_allowed_source` refuses
    #: Facebook, X, Reddit and Instagram on the URL rather than only in the
    #: search query.
    sources: list[str] = []
    #: True when nothing usable could be retrieved or drafted. The screen shows
    #: `message` instead of an empty form that looks like a finished draft.
    degraded: bool = False
    message: str | None = None
