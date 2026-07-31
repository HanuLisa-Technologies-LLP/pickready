"""Owner console schemas (API_CONTRACT.md `/admin`).

Onboarding captures the company *profile* (industry / culture / details) — it
no longer asks for a sending domain (SMTP replaced per-tenant verified
domains, claude.md rule 5) and it never mentions OTP (Firebase owns
credentials, claude.md rule 2).
"""
import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models.enums import Role
from app.services.capabilities import ALL_CAPABILITIES

# The onboarding dropdown. Kept here so the API is the single source of truth
# and the frontend list can be checked against it.
INDUSTRY_CHOICES: tuple[str, ...] = (
    "Technology",
    "Finance",
    "Healthcare",
    "Retail",
    "Manufacturing",
    "Education",
    "Other",
)

Industry = Literal[
    "Technology",
    "Finance",
    "Healthcare",
    "Retail",
    "Manufacturing",
    "Education",
    "Other",
]

# Roles the Owner may invite into a tenant. `client`, `super_admin` and
# `candidate` are deliberately absent — the Owner invariant (services/owner.py)
# is the backstop, this Literal is the first gate.
StaffRoleName = Literal["hr_manager", "recruiter", "hiring_manager"]

MAX_CULTURE = 5_000
MAX_DETAILS = 20_000
MIN_CULTURE_WORDS = 100
MAX_CULTURE_WORDS = 500


def _clean(value: str | None) -> str | None:
    """Trim; treat an all-whitespace string as absent."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _validate_culture(value: str | None, *, optional: bool) -> str | None:
    cleaned = _clean(value)
    if cleaned is None and optional:
        return None
    if cleaned is None:
        raise ValueError("company culture is required")
    words = len(cleaned.split())
    if not MIN_CULTURE_WORDS <= words <= MAX_CULTURE_WORDS:
        raise ValueError(
            f"company culture must be {MIN_CULTURE_WORDS}-{MAX_CULTURE_WORDS} words"
        )
    return cleaned


def derive_tenant_domain(company_name: str, owner_email: str) -> str:
    """Derive the (legacy, unique) `tenants.domain` value.

    The onboarding form no longer asks for a domain. Prefer the owner email's
    domain — it is what a human would have typed — and fall back to a slug of
    the company name for addresses on shared mail hosts with no usable domain.
    Callers must still de-duplicate against existing rows (`domain` is UNIQUE).
    """
    candidate = (owner_email or "").rsplit("@", 1)[-1].strip().lower()
    if candidate and "." in candidate and " " not in candidate:
        return candidate
    slug = re.sub(r"[^a-z0-9]+", "-", (company_name or "").lower()).strip("-")
    return f"{slug or 'tenant'}.pickready.local"


class TenantCreateIn(BaseModel):
    """POST /admin/tenants — onboard a client company.

    `domain` and `client_phone` remain accepted for API back-compat but are
    optional; the console no longer collects either.
    """

    name: str = Field(min_length=1, max_length=255)
    client_email: EmailStr
    industry: Industry
    culture: str = Field(min_length=1, max_length=MAX_CULTURE)
    details: str | None = Field(default=None, max_length=MAX_DETAILS)
    domain: str | None = Field(default=None, max_length=255)
    client_phone: str | None = Field(default=None, max_length=20)

    @field_validator("name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("must not be blank")
        return trimmed

    @field_validator("culture")
    @classmethod
    def _culture_word_count(cls, v: str) -> str:
        return _validate_culture(v, optional=False) or ""

    @field_validator("details", "domain", "client_phone")
    @classmethod
    def _optional_clean(cls, v: str | None) -> str | None:
        return _clean(v)


class TenantUpdateIn(BaseModel):
    """PUT/PATCH /admin/tenants/{id} — partial update of the company profile.
    `domain`, `created_at` and the client account are NOT editable here."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    industry: Industry | None = None
    culture: str | None = Field(default=None, max_length=MAX_CULTURE)
    details: str | None = Field(default=None, max_length=MAX_DETAILS)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str | None) -> str | None:
        cleaned = _clean(v)
        if v is not None and cleaned is None:
            raise ValueError("company name must not be blank")
        return cleaned

    @field_validator("culture")
    @classmethod
    def _culture_word_count(cls, v: str | None) -> str | None:
        return _validate_culture(v, optional=True)

    @field_validator("details")
    @classmethod
    def _trim(cls, v: str | None) -> str | None:
        # An explicit "" clears the field; None means "leave unchanged".
        return None if v is None else v.strip()

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "TenantUpdateIn":
        if not self.model_fields_set:
            raise ValueError("provide at least one field to update")
        return self


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    domain: str
    spf_dkim_status: str
    created_at: datetime
    industry: str | None = None
    culture: str | None = None
    details: str | None = None
    # Owner / primary point of contact — the tenant's `client` (Client Company
    # Admin) user. Populated by the handler, not by ORM attribute lookup.
    client_email: str | None = None
    client_name: str | None = None
    client_phone: str | None = None
    client_status: str | None = None
    staff_count: int = 0


class TenantDeleteOut(BaseModel):
    """Result of DELETE /admin/tenants/{id}."""

    id: uuid.UUID
    name: str
    deleted: Literal[True] = True
    # What the cascade removed / released, for the confirmation toast and the
    # audit trail.
    removed: dict[str, int] = Field(default_factory=dict)


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: Role
    email: str | None
    phone: str | None
    full_name: str | None
    status: str


class TenantCreateOut(BaseModel):
    tenant: TenantOut
    client_user: AdminUserOut


# ── Owner-side team management ───────────────────────────────────────────────

class StaffInviteIn(BaseModel):
    """POST /admin/staff-invites — invite a staff member INTO a chosen tenant
    from the Owner console. Mirrors the client-side flow (`/companies/me/staff`)
    and reuses the same `staff_invites` row + `/join` acceptance page."""

    tenant_id: uuid.UUID
    email: EmailStr
    role: StaffRoleName
    full_name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=20)

    @field_validator("full_name", "phone")
    @classmethod
    def _optional_clean(cls, v: str | None) -> str | None:
        return _clean(v)


class StaffInviteOut(BaseModel):
    user: AdminUserOut
    tenant_id: uuid.UUID
    tenant_name: str
    role: StaffRoleName
    # Copyable fallback so onboarding never depends on mail delivery.
    invite_link: str
    expires_at: datetime | None = None
    # True when the send_email task was enqueued (never "was delivered").
    email_queued: bool = False


class OwnerStaffOut(BaseModel):
    """One staff row across all tenants for the Owner's Team Management page."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    email: str
    full_name: str | None = None
    role: StaffRoleName
    status: str
    invite_status: str | None = None
    invite_sent_at: datetime | None = None
    invite_expires_at: datetime | None = None


# ── Business Development team (platform staff, no tenant) ────────────────────
#
# A BD user is PickReady's own salesperson, not a customer's employee: the row
# carries `role = 'bd'` and `tenant_id = NULL`, so none of the tenant-scoped
# invite machinery applies (a `staff_invites` row needs a tenant). Identity is
# the same as everywhere else: the Owner reserves the email here, Firebase owns
# the credential, and the first proven sign-in binds `firebase_uid` and flips
# invited to active (see api/auth._finalize_single).

class BDUserOut(BaseModel):
    """One row on the Provider Portal's Business Development page."""

    id: uuid.UUID
    email: str
    full_name: str | None = None
    phone: str | None = None
    # invited (reserved, never signed in), active, or disabled.
    status: str
    created_at: datetime | None = None
    # Whether a Firebase identity has been bound yet. False means the person
    # has not signed in even once, which is the usual reason a new BD account
    # "does not work".
    signed_in: bool = False


class BDUserCreateIn(BaseModel):
    """POST /admin/bd-users — reserve a Business Development account.

    No password is accepted or stored: Firebase owns credentials (claude.md
    rule 2). The account becomes usable the moment its owner signs in at the
    normal login page with this email.
    """

    email: EmailStr
    full_name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=20)

    @field_validator("full_name", "phone")
    @classmethod
    def _optional_clean(cls, v: str | None) -> str | None:
        return _clean(v)


class BDUserUpdateIn(BaseModel):
    """PATCH /admin/bd-users/{id} — edit details, or disable / re-enable.

    `status` accepts only the two values an operator can choose. Re-enabling is
    resolved server-side (back to active when the account has already signed
    in, back to invited when it has not), so disabling is always reversible.
    Email is deliberately not editable: it IS the identity Firebase binds to.
    """

    full_name: str | None = None
    phone: str | None = None
    status: Literal["active", "disabled"] | None = None

    @field_validator("full_name", "phone")
    @classmethod
    def _optional_clean(cls, v: str | None) -> str | None:
        return _clean(v)

    @model_validator(mode="after")
    def _not_empty(self) -> "BDUserUpdateIn":
        if not self.model_fields_set:
            raise ValueError("nothing to update")
        return self


# ── Org-portal view of the caller's own tenant profile ───────────────────────

class TenantProfileOut(BaseModel):
    """GET /admin/my-tenant — the signed-in org user's own company profile."""

    id: uuid.UUID
    name: str
    industry: str | None = None
    culture: str | None = None
    details: str | None = None
    created_at: datetime
    client_email: str | None = None
    client_name: str | None = None
    client_phone: str | None = None
    # True when the caller may PUT this resource (Client Company Admin only).
    editable: bool = False


class TenantProfileIn(BaseModel):
    """PUT /admin/my-tenant — Client Company Admin edits their own profile."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    industry: Industry | None = None
    culture: str | None = Field(default=None, max_length=MAX_CULTURE)
    details: str | None = Field(default=None, max_length=MAX_DETAILS)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str | None) -> str | None:
        cleaned = _clean(v)
        if v is not None and cleaned is None:
            raise ValueError("company name must not be blank")
        return cleaned

    @field_validator("culture")
    @classmethod
    def _culture_word_count(cls, v: str | None) -> str | None:
        return _validate_culture(v, optional=True)

    @field_validator("details")
    @classmethod
    def _trim(cls, v: str | None) -> str | None:
        return None if v is None else v.strip()

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "TenantProfileIn":
        if not self.model_fields_set:
            raise ValueError("provide at least one field to update")
        return self


class PermissionEntry(BaseModel):
    role: Role
    capability: str
    allowed: bool

    @field_validator("capability")
    @classmethod
    def _known_capability(cls, v: str) -> str:
        if v not in ALL_CAPABILITIES:
            raise ValueError(f"unknown capability: {v}")
        return v


class PermissionsUpdateIn(BaseModel):
    tenant_id: uuid.UUID | None = None  # None -> edit the global template
    entries: list[PermissionEntry] = Field(min_length=1)


class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: Role
    capability: str
    allowed: bool


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    metadata_json: dict | None
    at: datetime
