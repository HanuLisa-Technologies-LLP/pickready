"""Intercom: the customer-success surface, and the field allowlist that guards it.

WHAT WAS HERE BEFORE THIS FILE: NOTHING. A search of the whole repository for
`intercom`, case-insensitive, across Python, TypeScript, Terraform and Markdown
returned zero hits. There was no partial integration to extend and no
half-written client to finish, so this is the first and only implementation.

WHAT SYNCS, AND THE ONE RULE THAT DECIDES IT
----------------------------------------------
A COMPANY is a `tenants` row. A CONTACT is a member of that tenant's staff --
somebody who has an account here, who talks to support, and whose email a
support tool already holds by virtue of them having written to it.

**A CANDIDATE IS NOT A CONTACT AND NEVER BECOMES ONE.** No candidate, profile,
resume, application, assessment, transcript, report, grade, score, band or
matrix is projected here, and the enforcement is not a filter somebody could
edit: `COMPANY_FIELDS` and `CONTACT_FIELDS` are closed allowlists of ATTRIBUTE
names, each carrying the reason it is present, and `project_company` /
`project_contact` build their payload BY ITERATING THE ALLOWLIST rather than by
iterating the row. A column added to `tenants` tomorrow does not appear in
Intercom, and that is the whole design: the failure mode of every CRM sync ever
written is that it forwards the model, and the model grows.

The brief that asked for this said it twice, and both halves are load bearing:
do not leak sensitive candidate information, and do not blindly synchronize
every database field. The second is how the first stops depending on somebody
remembering.

NO NUMBER THAT IS A JUDGEMENT LEAVES THIS PRODUCT
---------------------------------------------------
Operational counts about a CUSTOMER would be allowed here for the same reason
the intelligence dashboards allow them: they describe an account, not a person
being assessed. A candidate score is a judgement about a human being, it is
never rendered as a number even to the client who paid for it, and it has no
business in a support inbox.

DEGRADATION IS RECORDED, AND AN UNCONFIGURED DEPLOYMENT IS NOT DEGRADED
------------------------------------------------------------------------
`INTERCOM_ACCESS_TOKEN` may be absent, and on a deployment without it every
entry point answers `status="unconfigured"` with a plain reason and changes
nothing. That is the shape `web_research` already uses for `TAVILY_API_KEY`,
and the distinction it draws is the one `rag/reranker` draws too: a deployment
that made a choice is not a deployment that failed. A configured deployment
whose call fails records `status="unavailable"` with the fault CLASS, never the
response body, because a support payload carries customer contact details.

WHY THE WRITE IS DISPATCHED AND NEVER INLINE
----------------------------------------------
A third-party round trip inside a request handler makes this product's latency
a function of Intercom's. The task is `Route.LAMBDA`: measured in seconds, and
a sweep, so it does nothing when there is nothing to send.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from app.core.config import get_settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.intercom.io"

#: Intercom pins its request and response shape to a version header. Stated
#: explicitly rather than left to the account default, because the default
#: MOVES when Intercom promotes a version, and a payload shape that changed
#: underneath a running integration is the `voyage-context-4` failure with a
#: different vendor: nothing raises, the fields simply stop arriving.
API_VERSION = "2.11"

REQUEST_TIMEOUT_SECONDS = 10.0

#: Statuses. Named, because they land in a record an operator reads and two
#: spellings of one condition are two conditions to them.
STATUS_OK = "ok"
STATUS_UNCONFIGURED = "unconfigured"
STATUS_UNAVAILABLE = "unavailable"
STATUS_SKIPPED = "skipped"

UNCONFIGURED_REASON = (
    "INTERCOM_ACCESS_TOKEN is not set on this deployment, so nothing was sent. "
    "That is a configuration choice, not a failure."
)

#: THE COMPANY ALLOWLIST. Intercom attribute name -> the `tenants` attribute it
#: reads. Every entry is a fact about the ACCOUNT and carries its reason.
COMPANY_FIELDS: dict[str, str] = {
    # Who the customer is. Support cannot route a conversation without it.
    "name": "name",
    # The domain support already sees on every inbound email, so it links a
    # writer to an account. Not sensitive: it is on the customer's own website.
    "website": "website_domain",
    # Segments the customer base. Industry is on their public profile.
    "industry": "industry",
    # `active | archived | prospect`. Support needs to know whether an account
    # is live before promising anything, and an archived customer writing in is
    # exactly the case a human should see flagged.
    "lifecycle_status": "status",
}

#: THE CONTACT ALLOWLIST. A member of the CUSTOMER's staff, never a candidate.
#: `role` is the product role and is what tells support whether they are
#: talking to somebody who can authorise a change.
CONTACT_FIELDS: dict[str, str] = {
    "email": "email",
    "name": "full_name",
    "role": "role",
}

#: Roles that may be projected as contacts, ENUMERATED. `candidate` is absent
#: and its absence is the enforcement: a check written as `!= "candidate"`
#: admits every role invented later, including one that turns out to be
#: candidate-shaped.
SYNCABLE_ROLES: frozenset[str] = frozenset(
    {"client", "hr_manager", "recruitment_manager", "recruiter", "hiring_manager"}
)

#: Names that must never appear in a projected payload, whatever a future
#: allowlist entry is called. Belt AND braces deliberately: the allowlist is the
#: real control, and this is what fails loudly, in a test, if somebody widens
#: the allowlist without reading the docstring above.
FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "candidate",
    "resume",
    "profile",
    "assessment",
    "transcript",
    "report",
    "score",
    "grade",
    "band",
    "matrix",
    "evidence",
    "proctor",
    "salary",
    "ctc",
)


class IntercomUnavailable(RuntimeError):
    """The vendor could not answer. Carries a fault CLASS, never a body."""

    def __init__(self, fault: str) -> None:
        super().__init__(fault)
        self.fault = fault


@dataclass(frozen=True)
class SyncOutcome:
    """What happened, in the words a record should carry."""

    status: str
    reason: str | None = None
    #: The attribute names actually sent. Recorded so an operator can answer
    #: "what does Intercom know about us" from OUR side, without asking
    #: Intercom.
    fields_sent: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "fields_sent": list(self.fields_sent),
        }


def _token() -> str:
    return str(getattr(get_settings(), "intercom_access_token", "") or "").strip()


def is_configured() -> bool:
    return bool(_token())


def _assert_no_forbidden_name(payload: Mapping[str, Any]) -> None:
    """Refuse a payload whose KEYS name anything candidate-shaped.

    Raises rather than filtering. A filter would send the rest of the payload
    and leave nobody aware that a field had been added which should never have
    been considered; the raise stops the sync and names the key.
    """
    for key in payload:
        lowered = key.lower()
        for forbidden in FORBIDDEN_SUBSTRINGS:
            if forbidden in lowered:
                raise ValueError(
                    f"refusing to send {key!r} to Intercom: the name contains "
                    f"{forbidden!r}. Candidate data does not leave this product "
                    "for a support tool. If the field is genuinely about the "
                    "CUSTOMER, rename it so that is obvious to the next reader."
                )


def project_company(tenant: Any) -> dict[str, Any]:
    """Build the company payload BY ITERATING THE ALLOWLIST, never the row.

    The direction is the whole guarantee. Iterating the row and removing known
    bad fields is a denylist wearing an allowlist's clothes: it forwards
    whatever nobody thought to exclude, and `tenants` gains columns.

    A missing attribute is OMITTED rather than sent as null, so a partially
    filled profile does not blank a field a human curated in Intercom.
    """
    payload: dict[str, Any] = {}
    for attribute, source in COMPANY_FIELDS.items():
        value = getattr(tenant, source, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        payload[attribute] = str(value)
    _assert_no_forbidden_name(payload)
    return payload


def project_contact(user: Any) -> dict[str, Any] | None:
    """Build the contact payload, or None when this person is not a contact.

    None for a candidate, for any role outside `SYNCABLE_ROLES`, and for a user
    with no email. None rather than a raise because "this row is not a contact"
    is the ORDINARY case in a table where candidates outnumber staff.
    """
    role = str(getattr(user, "role", "") or "")
    if role not in SYNCABLE_ROLES:
        return None
    payload: dict[str, Any] = {}
    for attribute, source in CONTACT_FIELDS.items():
        value = getattr(user, source, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        payload[attribute] = str(value)
    if not payload.get("email"):
        return None
    _assert_no_forbidden_name(payload)
    return payload


def _post(path: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """One bounded call. Raises `IntercomUnavailable` naming the fault class.

    No retry here. `workers/runtime.run_task` owns the retry loop for this
    product, and two mechanisms stacked MULTIPLY, which is the lesson the
    platform's own retry configuration records.
    """
    token = _token()
    if not token:
        raise IntercomUnavailable("credential_not_configured")
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Intercom-Version": API_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # The STATUS, never the body. An Intercom error echoes the payload back,
        # and that payload carries a customer's contact details into whatever
        # log sink this lands in.
        raise IntercomUnavailable(f"http_{exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise IntercomUnavailable(type(exc).__name__) from exc
    except json.JSONDecodeError as exc:
        raise IntercomUnavailable("unparseable_response") from exc


def sync_company(tenant: Any) -> SyncOutcome:
    """Upsert one customer. Idempotent on `company_id`, our own tenant id.

    Keyed on the tenant UUID rather than on the name, because a customer who
    renames themselves must stay ONE company in Intercom rather than becoming a
    second one whose history starts today. Same reasoning
    `bd_leads.promoted_tenant_id` records for a re-signed lead.
    """
    if not is_configured():
        return SyncOutcome(STATUS_UNCONFIGURED, UNCONFIGURED_REASON)
    payload = project_company(tenant)
    if not payload:
        return SyncOutcome(
            STATUS_SKIPPED,
            "the tenant carries no allowlisted field with a value in it",
        )
    top_level = {"name", "website"}
    body = {
        "company_id": str(getattr(tenant, "id", "")),
        "custom_attributes": {
            k: v for k, v in payload.items() if k not in top_level
        },
        **{k: v for k, v in payload.items() if k in top_level},
    }
    try:
        _post("/companies", body)
    except IntercomUnavailable as exc:
        logger.warning("intercom.sync_company_failed fault=%s", exc.fault)
        return SyncOutcome(STATUS_UNAVAILABLE, exc.fault)
    return SyncOutcome(STATUS_OK, fields_sent=tuple(sorted(payload)))


def sync_contact(user: Any, tenant_id: uuid.UUID | str | None) -> SyncOutcome:
    """Upsert one member of a customer's staff, attached to their company."""
    if not is_configured():
        return SyncOutcome(STATUS_UNCONFIGURED, UNCONFIGURED_REASON)
    payload = project_contact(user)
    if payload is None:
        return SyncOutcome(
            STATUS_SKIPPED,
            "not a customer contact: a candidate is never projected to Intercom",
        )
    body: dict[str, Any] = {"role": "user", **payload}
    if tenant_id:
        body["companies"] = [{"company_id": str(tenant_id)}]
    try:
        _post("/contacts", body)
    except IntercomUnavailable as exc:
        logger.warning("intercom.sync_contact_failed fault=%s", exc.fault)
        return SyncOutcome(STATUS_UNAVAILABLE, exc.fault)
    return SyncOutcome(STATUS_OK, fields_sent=tuple(sorted(payload)))
