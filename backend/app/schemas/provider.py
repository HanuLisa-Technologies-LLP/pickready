"""Provider Portal schemas — the ReadyPick owner's view of its CUSTOMERS.

Vocabulary (the spec is strict about this and the code follows it):

    Provider Portal   the ReadyPick owner's console — these schemas
    Customer Portal   a client company's own HR/recruitment dashboard
    Candidate Portal  the public candidate surface

A "customer" is one onboarded client company, i.e. a `tenants` row. The Owner
console's existing `/admin/tenants` endpoints keep their tenant vocabulary and
their onboarding/delete semantics; this module is the customer-management view
layered over the same rows, with analytics, compliance documents and the
reversible archive lifecycle the Provider Portal needs.

Read-only is enforced by absence, not by a flag: there is no input schema here
for a contact detail, a team member, or a compliance document, because the
Provider cannot write any of them.
"""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.compliance import (
    COMMERCIAL_DOCUMENT_TYPES,
    DOCUMENT_LABELS,
    TAX_DOCUMENT_TYPES,
)
from app.schemas.admin import INDUSTRY_CHOICES, Industry

from app.schemas.pagination import PageMeta

__all__ = [
    "INDUSTRY_CHOICES",
    "ComplianceDocumentOut",
    "ComplianceDocumentSlot",
    "CustomerAnalyticsOut",
    "CustomerDetailOut",
    "CustomerListOut",
    "CustomerOut",
    "CustomerStatus",
    "CustomerTeamMemberOut",
    "CustomerUpdateIn",
    "PrimaryContactOut",
]

CustomerStatus = Literal["active", "archived"]

DocumentType = Literal[
    "gstin_certificate",
    "pan_card",
    "tan_number",
    "bank_account_details",
    "signed_agreement",
    "purchase_order",
    "msme_certificate",
]

DocumentGroup = Literal["tax", "commercial"]

MAX_NOTES = 5_000

#: How many team members the detail panel carries inline. The full roster can
#: be large; the panel is a summary, and `team_size` remains the exact count.
TEAM_PREVIEW_LIMIT = 10


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


# ── Primary contact (read-only) ──────────────────────────────────────────────

class PrimaryContactOut(BaseModel):
    """The customer's HR Head / Client Company Admin.

    Sourced live from their `users` row rather than denormalised onto the
    customer: they maintain these details themselves in the Customer Portal, so
    a copy here would be stale the first time they edit it. The Provider views
    and never edits (spec §2.3).
    """

    user_id: uuid.UUID | None = None
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    #: Landline WITH extension, as one string — "+91-22-1234-5678 ext. 101".
    landline: str | None = None
    status: str | None = None


class CustomerTeamMemberOut(BaseModel):
    """One staff member in the detail panel's team preview."""

    id: uuid.UUID
    name: str | None = None
    email: str | None = None
    role: str
    status: str


# ── Analytics ────────────────────────────────────────────────────────────────

class CustomerAnalyticsOut(BaseModel):
    """Spec §2.2 / §4.1. Every field is a plain count.

    `jobs_closed` and `jobs_ongoing` OVERLAP during a job's 5-day grace period
    and are not a partition of `jobs_posted` — see
    `services/provider_analytics` for why that is the intended reading.
    """

    jobs_posted: int = 0
    jobs_closed: int = 0
    jobs_ongoing: int = 0
    total_candidates_interacted: int = 0
    jobs_last_30_days: int = 0


# ── Compliance documents (read-only for the Provider) ────────────────────────

class ComplianceDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_type: DocumentType
    label: str
    group: DocumentGroup
    file_name: str
    mime_type: str | None = None
    size_bytes: int | None = None
    uploaded_at: datetime
    #: Resolved display name of the HR Head who uploaded it; None once that
    #: account is gone (the document outlives the person).
    uploaded_by_name: str | None = None


class ComplianceDocumentSlot(BaseModel):
    """One of the seven slots, present or not.

    The API returns all seven ALWAYS, in fixed order, with `document` null for
    the ones not yet supplied. The UI's "Not Available Yet" is then a rendering
    of data rather than a gap the client has to reconstruct from a short list —
    which is what keeps a missing PAN card visible instead of invisible.
    """

    document_type: DocumentType
    label: str
    group: DocumentGroup
    document: ComplianceDocumentOut | None = None


def document_slots(
    documents: dict[str, ComplianceDocumentOut],
) -> list[ComplianceDocumentSlot]:
    """All seven slots in spec order: the four tax records, then the three
    commercial ones."""
    return [
        ComplianceDocumentSlot(
            document_type=document_type,  # type: ignore[arg-type]
            label=DOCUMENT_LABELS[document_type],
            group=group,  # type: ignore[arg-type]
            document=documents.get(document_type),
        )
        for group, types in (
            ("tax", TAX_DOCUMENT_TYPES),
            ("commercial", COMMERCIAL_DOCUMENT_TYPES),
        )
        for document_type in types
    ]


# ── Customer rows ────────────────────────────────────────────────────────────

class CustomerOut(BaseModel):
    """One row of the Provider Portal's customer table (spec §2.1)."""

    id: uuid.UUID
    name: str
    industry: str | None = None
    website_domain: str | None = None
    #: The internal tenant key. Shown as a subtitle, never editable.
    domain: str
    status: CustomerStatus
    archived_at: datetime | None = None
    created_at: datetime
    notes: str | None = None
    primary_contact: PrimaryContactOut
    team_size: int = 0
    analytics: CustomerAnalyticsOut = Field(default_factory=CustomerAnalyticsOut)


class CustomerListOut(PageMeta):
    """Paginated customer list. `total` counts everything matching the filter,
    not just this page, so the UI can render "showing 25 of 108"."""

    customers: list[CustomerOut]
    total: int
    page: int
    page_size: int


class CustomerDetailOut(CustomerOut):
    """The detail panel (spec §4.1): the list row plus the customer profile
    prose, the team preview, and all seven compliance slots."""

    culture: str | None = None
    details: str | None = None
    team: list[CustomerTeamMemberOut] = Field(default_factory=list)
    compliance_documents: list[ComplianceDocumentSlot] = Field(default_factory=list)


class CustomerUpdateIn(BaseModel):
    """PATCH /provider/customers/{id} — the Provider's edit modal (spec §4.2).

    Only three fields are writable, and the customer's IDENTITY is not among
    them: name, contact, team and created date are the customer's own to
    maintain. `status` is here so archive and unarchive are one endpoint with
    one audit action rather than two near-identical ones.
    """

    industry: Industry | None = None
    website_domain: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=MAX_NOTES)
    status: CustomerStatus | None = None

    @field_validator("website_domain")
    @classmethod
    def _clean_domain(cls, value: str | None) -> str | None:
        cleaned = _clean(value)
        if cleaned is None:
            return None
        # Accept what a human pastes; store the bare host.
        for prefix in ("https://", "http://"):
            if cleaned.lower().startswith(prefix):
                cleaned = cleaned[len(prefix):]
        return cleaned.strip("/") or None

    @field_validator("notes")
    @classmethod
    def _trim_notes(cls, value: str | None) -> str | None:
        # An explicit "" clears the field; an absent key leaves it unchanged.
        return None if value is None else value.strip()

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "CustomerUpdateIn":
        if not self.model_fields_set:
            raise ValueError("provide at least one field to update")
        return self
