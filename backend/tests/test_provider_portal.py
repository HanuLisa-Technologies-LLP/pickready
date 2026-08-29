"""Provider Portal — customers, analytics, lifecycle and compliance records.

The four things worth guarding here, and why:

  * the analytics BOUNDARIES. `jobs_closed` / `jobs_ongoing` decide what the
    owner believes about a customer's activity, and an off-by-one on an
    inclusive boundary is invisible in manual testing;
  * the seven compliance SLOTS. A missing PAN card must render as a visible
    "Not Available Yet" row — the failure mode is a document silently not
    appearing at all, which is the exact opposite of what a compliance view is
    for;
  * the archive LIFECYCLE, in both directions. An unarchive that leaves
    `archived_at` set shows a restored customer with an archive date;
  * READ-ONLY BY ABSENCE. The Provider must not be able to write a customer's
    contact, team or documents, and the guarantee is that no such route exists
    — so the test asserts over the router, not over a handler's behaviour.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import provider as provider_api
from app.models.compliance import (
    COMMERCIAL_DOCUMENT_TYPES,
    DOCUMENT_GROUPS,
    DOCUMENT_LABELS,
    DOCUMENT_TYPES,
    TAX_DOCUMENT_TYPES,
)
from app.models.tenant import CUSTOMER_ACTIVE, CUSTOMER_ARCHIVED
from app.schemas.provider import (
    ComplianceDocumentOut,
    CustomerUpdateIn,
    document_slots,
)
from app.services import capabilities as caps
from app.services import document_storage, provider_analytics
from app.services.capabilities import DEFAULT_PERMISSION_MATRIX
from app.models.enums import Role

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


# ── Analytics: boundaries ────────────────────────────────────────────────────

def _compiled(query) -> str:
    return str(query.compile(compile_kwargs={"literal_binds": False}))


def test_closed_is_strictly_past_posting_end_and_ongoing_is_inclusive() -> None:
    """Ties go to the job still being live (claude.md rule 8).

    A job exactly ON its `posting_end_date` is still active, so `closed` must
    use `<` and `ongoing` must use `>=`. Flipping either operator moves a
    customer's headline numbers by a day.
    """
    sql = _compiled(provider_analytics.jobs_counts_query([uuid.uuid4()], NOW))
    assert "jobs.posting_end_date < " in sql
    assert "jobs.grace_period_end_date >= " in sql


def test_ongoing_uses_the_grace_end_not_the_posting_end() -> None:
    """A job in its 5-day grace tail is still ongoing — applicants can edit."""
    sql = _compiled(provider_analytics.jobs_counts_query([uuid.uuid4()], NOW))
    # The FILTER clauses appear in the order the query builds them:
    # [0] is everything before the first, then closed, ongoing, recent.
    _prefix, closed, ongoing, _recent = sql.split("FILTER")
    assert "grace_period_end_date" in ongoing
    # "Closed" must NOT be measured against the grace end, or every job would
    # look open for an extra five days.
    assert "grace_period_end_date" not in closed
    assert "posting_end_date" in closed


def test_candidates_are_counted_distinctly() -> None:
    """One candidate applying to four of a customer's jobs is ONE candidate
    interacted with, not four."""
    sql = _compiled(provider_analytics.candidates_count_query([uuid.uuid4()]))
    assert "count(DISTINCT job_candidate_links.candidate_id)" in sql


def test_recent_activity_window_is_thirty_days() -> None:
    assert provider_analytics.RECENT_ACTIVITY_DAYS == 30


# ── Analytics: assembling the results ────────────────────────────────────────

@dataclass
class _Rows:
    rows: list

    def all(self):
        return self.rows


class _AnalyticsSession:
    """Returns the job aggregate first, the candidate aggregate second — the
    order `counts_for_tenants` issues them in."""

    def __init__(self, job_rows, candidate_rows):
        self._results = [_Rows(job_rows), _Rows(candidate_rows)]

    async def execute(self, _query):
        return self._results.pop(0)


def _job_row(tenant_id, posted, closed, ongoing, recent):
    return SimpleNamespace(
        tenant_id=tenant_id,
        jobs_posted=posted,
        jobs_closed=closed,
        jobs_ongoing=ongoing,
        jobs_last_30_days=recent,
    )


@pytest.mark.asyncio
async def test_counts_merge_jobs_and_candidates_per_tenant() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    session = _AnalyticsSession(
        [_job_row(first, 12, 6, 7, 3), _job_row(second, 2, 0, 2, 2)],
        [(first, 35), (second, 2)],
    )
    result = await provider_analytics.counts_for_tenants(
        session, [first, second], now=NOW
    )
    assert result[first] == provider_analytics.CustomerAnalytics(
        jobs_posted=12, jobs_closed=6, jobs_ongoing=7,
        total_candidates_interacted=35, jobs_last_30_days=3,
    )
    assert result[second].total_candidates_interacted == 2


@pytest.mark.asyncio
async def test_closed_and_ongoing_may_overlap_during_grace() -> None:
    """Documented and intended: they answer two independent questions and are
    NOT two halves of `jobs_posted`. A test pins it so a later "fix" that makes
    them sum has to be a deliberate decision."""
    tenant_id = uuid.uuid4()
    session = _AnalyticsSession([_job_row(tenant_id, 12, 6, 7, 3)], [(tenant_id, 1)])
    result = await provider_analytics.counts_for_tenants(session, [tenant_id], now=NOW)
    analytics = result[tenant_id]
    assert analytics.jobs_closed + analytics.jobs_ongoing > analytics.jobs_posted


@pytest.mark.asyncio
async def test_a_customer_with_no_jobs_is_absent_not_null() -> None:
    """Callers use `.get(id, EMPTY_ANALYTICS)`, so "no activity" renders as
    zeroes without a branch."""
    tenant_id = uuid.uuid4()
    session = _AnalyticsSession([], [])
    result = await provider_analytics.counts_for_tenants(session, [tenant_id], now=NOW)
    assert tenant_id not in result
    assert result.get(tenant_id, provider_analytics.EMPTY_ANALYTICS).jobs_posted == 0


@pytest.mark.asyncio
async def test_no_tenants_issues_no_query() -> None:
    class _Explode:
        async def execute(self, _query):  # pragma: no cover - must never run
            raise AssertionError("queried with an empty id list")

    assert await provider_analytics.counts_for_tenants(_Explode(), []) == {}


@pytest.mark.asyncio
async def test_candidate_count_alone_still_produces_a_row() -> None:
    """Links can exist for a job created before the window columns existed;
    the counters must not vanish because the job aggregate missed the tenant."""
    tenant_id = uuid.uuid4()
    session = _AnalyticsSession([], [(tenant_id, 4)])
    result = await provider_analytics.counts_for_tenants(session, [tenant_id], now=NOW)
    assert result[tenant_id].total_candidates_interacted == 4
    assert result[tenant_id].jobs_posted == 0


# ── The seven compliance slots ───────────────────────────────────────────────

def test_there_are_exactly_seven_types_in_two_groups() -> None:
    assert len(TAX_DOCUMENT_TYPES) == 4
    assert len(COMMERCIAL_DOCUMENT_TYPES) == 3
    assert len(DOCUMENT_TYPES) == 7
    assert len(set(DOCUMENT_TYPES)) == 7


def test_every_type_has_a_label_and_a_group() -> None:
    for document_type in DOCUMENT_TYPES:
        assert DOCUMENT_LABELS[document_type]
        assert DOCUMENT_GROUPS[document_type] in {"tax", "commercial"}


def test_all_seven_slots_are_returned_even_when_nothing_is_filed() -> None:
    """The absent ones ARE the point — "Not Available Yet" has to be a rendered
    row, not a gap the client infers from a short list."""
    slots = document_slots({})
    assert [slot.document_type for slot in slots] == list(DOCUMENT_TYPES)
    assert all(slot.document is None for slot in slots)


def test_slot_order_is_tax_records_then_commercial_ones() -> None:
    slots = document_slots({})
    assert [slot.group for slot in slots] == ["tax"] * 4 + ["commercial"] * 3


def test_a_filed_document_lands_in_its_own_slot_only() -> None:
    filed = ComplianceDocumentOut(
        id=uuid.uuid4(),
        document_type="pan_card",
        label=DOCUMENT_LABELS["pan_card"],
        group="tax",
        file_name="pan.pdf",
        uploaded_at=NOW,
        uploaded_by_name="Priya",
    )
    slots = {slot.document_type: slot for slot in document_slots({"pan_card": filed})}
    assert slots["pan_card"].document is filed
    assert sum(1 for slot in slots.values() if slot.document is not None) == 1


# ── Archive lifecycle ────────────────────────────────────────────────────────

class _FlushSession:
    async def flush(self) -> None:
        return None


def _tenant(**overrides):
    base = dict(
        id=uuid.uuid4(),
        name="Acme Corp",
        status=CUSTOMER_ACTIVE,
        archived_at=None,
        industry="Technology",
        website_domain=None,
        notes=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def no_audit(monkeypatch):
    recorded: list[dict] = []

    async def _audit(_session, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(provider_api, "audit", _audit)
    return recorded


@pytest.mark.asyncio
async def test_archiving_stamps_the_time(no_audit) -> None:
    tenant = _tenant()
    changed = await provider_api._apply_customer_update(
        _FlushSession(), tenant, {"status": CUSTOMER_ARCHIVED}, uuid.uuid4()
    )
    assert changed == ["status"]
    assert tenant.status == CUSTOMER_ARCHIVED
    assert tenant.archived_at is not None
    assert no_audit[0]["action"] == "customer_archived"


@pytest.mark.asyncio
async def test_unarchiving_CLEARS_the_time(no_audit) -> None:
    """Otherwise a restored customer displays an archive date and reads as
    still archived."""
    tenant = _tenant(status=CUSTOMER_ARCHIVED, archived_at=NOW)
    changed = await provider_api._apply_customer_update(
        _FlushSession(), tenant, {"status": CUSTOMER_ACTIVE}, uuid.uuid4()
    )
    assert changed == ["status"]
    assert tenant.status == CUSTOMER_ACTIVE
    assert tenant.archived_at is None
    assert no_audit[0]["action"] == "customer_unarchived"


@pytest.mark.asyncio
async def test_archiving_an_already_archived_customer_is_a_no_op(no_audit) -> None:
    """No change means no audit row — a double click must not manufacture a
    second archive event with a fresh timestamp."""
    tenant = _tenant(status=CUSTOMER_ARCHIVED, archived_at=NOW)
    changed = await provider_api._apply_customer_update(
        _FlushSession(), tenant, {"status": CUSTOMER_ARCHIVED}, uuid.uuid4()
    )
    assert changed == []
    assert tenant.archived_at == NOW
    assert no_audit == []


@pytest.mark.asyncio
async def test_editing_metadata_does_not_touch_the_lifecycle(no_audit) -> None:
    tenant = _tenant()
    changed = await provider_api._apply_customer_update(
        _FlushSession(),
        tenant,
        {"industry": "Finance", "notes": "Renewal due Q4"},
        uuid.uuid4(),
    )
    assert sorted(changed) == ["industry", "notes"]
    assert tenant.status == CUSTOMER_ACTIVE
    assert tenant.archived_at is None
    assert no_audit[0]["action"] == "customer_updated"


@pytest.mark.asyncio
async def test_an_absent_key_leaves_the_field_alone(no_audit) -> None:
    """`exclude_unset` semantics: not sending `notes` must not clear notes."""
    tenant = _tenant(notes="Existing note")
    await provider_api._apply_customer_update(
        _FlushSession(), tenant, {"industry": "Retail"}, uuid.uuid4()
    )
    assert tenant.notes == "Existing note"


@pytest.mark.asyncio
async def test_an_explicit_blank_clears_the_field(no_audit) -> None:
    tenant = _tenant(notes="Existing note")
    changed = await provider_api._apply_customer_update(
        _FlushSession(), tenant, {"notes": ""}, uuid.uuid4()
    )
    assert changed == ["notes"]
    assert tenant.notes is None


# ── The edit payload ─────────────────────────────────────────────────────────

def test_the_provider_cannot_rename_a_customer_or_edit_its_contact() -> None:
    """Identity and contact belong to the customer. Extra keys are dropped by
    pydantic, so the guarantee is that the model has no such FIELDS."""
    fields = set(CustomerUpdateIn.model_fields)
    assert fields == {"industry", "website_domain", "notes", "status"}


def test_a_pasted_url_is_stored_as_a_bare_host() -> None:
    assert (
        CustomerUpdateIn(website_domain="https://acme.example.com/").website_domain
        == "acme.example.com"
    )
    assert (
        CustomerUpdateIn(website_domain="http://acme.example.com").website_domain
        == "acme.example.com"
    )


def test_a_blank_website_is_none_not_an_empty_string() -> None:
    assert CustomerUpdateIn(website_domain="   ").website_domain is None


def test_an_empty_patch_is_refused() -> None:
    with pytest.raises(ValueError):
        CustomerUpdateIn()


def test_an_unknown_status_is_refused() -> None:
    with pytest.raises(ValueError):
        CustomerUpdateIn(status="deleted")


# ── Read-only by absence ─────────────────────────────────────────────────────

def _provider_routes() -> list[tuple[str, frozenset[str]]]:
    return [
        (route.path, frozenset(route.methods))
        for route in provider_api.router.routes
        if hasattr(route, "methods")
    ]


def test_the_provider_router_exposes_no_way_to_write_a_document() -> None:
    """The owner reads compliance records; only the customer files them. The
    guarantee is structural — there is no route to call."""
    writes = {"POST", "PUT", "PATCH", "DELETE"}
    offenders = [
        path
        for path, methods in _provider_routes()
        if "compliance-documents" in path and methods & writes
    ]
    assert offenders == []


def test_the_provider_router_never_deletes_a_customer() -> None:
    """Archive is the reversible hide. The irreversible delete stays on the
    Owner console, behind retyping the company name."""
    assert not any("DELETE" in methods for _path, methods in _provider_routes())


# ── Who may file a compliance document ───────────────────────────────────────

def test_every_customer_role_may_manage_compliance_documents() -> None:
    """All four customer-side roles hold this grant.

    This test previously asserted the opposite, that MANAGE_COMPLIANCE_DOCUMENTS
    was the one place the flat staff model was deliberately not flat. Migration
    0031 (deployed) seeds it for hr_manager, recruiter and hiring_manager too,
    so the live `role_permissions` rows have granted it to all four since that
    migration ran and the old assertion described a state production was not in.
    The code template and the migration have to agree, because
    `api/admin._seed_permissions` copies the template into tenant rows that
    OVERRIDE the migration's global rows for console-created customers.

    Inverted rather than deleted: the grant stays a deliberate, visible decision,
    and narrowing it later is a test change somebody has to justify.
    """
    capability = caps.MANAGE_COMPLIANCE_DOCUMENTS
    for role in (Role.client, Role.hr_manager, Role.recruiter, Role.hiring_manager):
        assert DEFAULT_PERMISSION_MATRIX[role][capability] is True
    # Still not something a NEW role inherits by default.
    assert capability not in DEFAULT_PERMISSION_MATRIX[Role.bd]


def test_the_capability_is_registered() -> None:
    """An unregistered capability is rejected by the permission schemas, so the
    Owner could never grant it per tenant."""
    assert caps.MANAGE_COMPLIANCE_DOCUMENTS in caps.ALL_CAPABILITIES


# ── Document storage ─────────────────────────────────────────────────────────

def test_a_stored_document_reference_is_never_a_browsable_url() -> None:
    """The View/Download split moved out of the URL and into the endpoint.

    `attachment_url` used to rewrite a Cloudinary delivery path to insert
    `fl_attachment`, which is what made Download behave differently from View.
    Cloudinary is gone, the stored value is now an `s3://` object reference that
    no browser can follow at all, and both buttons route through the
    authenticated, tenant-scoped download endpoint -- it is that endpoint's
    `Content-Disposition` that separates them now.

    The function was deleted rather than left as an identity, because an
    identity function on a URL is an invitation to hand the stored value
    straight to an <a href>. This test is what stops the stored reference from
    quietly becoming browsable again.
    """
    assert not hasattr(document_storage, "attachment_url")
    assert document_storage.OBJECT_PREFIX == "compliance"

def test_the_upload_hint_never_names_a_storage_vendor() -> None:
    """claude.md, 2026-07-26: candidates and customers are told the limits, not
    where the bytes land."""
    hint = document_storage.UPLOAD_LIMITS_HINT.lower()
    assert "cloudinary" not in hint
    assert "10 mb" in hint


# ── Pagination ───────────────────────────────────────────────────────────────

def test_the_page_size_matches_the_rest_of_the_product() -> None:
    assert provider_api.DEFAULT_PAGE_SIZE == 25


@pytest.mark.asyncio
async def test_an_unknown_status_filter_is_refused_not_silently_ignored() -> None:
    """Silently ignoring it would show ACTIVE customers under an "archived"
    heading — the one mistake this filter must never make."""
    with pytest.raises(HTTPException) as caught:
        await provider_api.list_customers(
            search=None, status_filter="everything", page=1, page_size=25,
            session=object(),
        )
    assert caught.value.status_code == 422
