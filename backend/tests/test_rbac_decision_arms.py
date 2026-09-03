"""The refusal arms of `rbac.decide`, and the cache around the grant engine.

`test_rbac_conformance` proves the MATRIX is right and `test_dashboard_rbac_matrix`
proves it is ATTACHED to the routes. Neither walks the individual arms of the
decision function, and each arm is a different answer to the same question:
whether a refusal is a 403 the caller may learn from or a 404 that tells them
nothing.

Those two are not interchangeable. RBAC section 4 says a user of one client
must not access, INFER, modify, delete or retrieve another client's resources,
and inference is the whole reason a cross-tenant refusal is 404: a 403 confirms
the resource exists. So the arms below are tested for the STATUS and the REASON
token, not merely for "was refused".

`decide` is pure by construction -- the grant engine's answer is passed in --
so none of this needs a database.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.enums import Role
from app.services import capabilities as caps
from app.services import rbac
from app.services.capabilities import Invariant


TENANT = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()


def _principal(role: Role = Role.recruiter, tenant=TENANT) -> rbac.Principal:
    return rbac.Principal(user_id=uuid.uuid4(), tenant_id=tenant, role=role)


# ── The three statuses a decision can carry ──────────────────────────────────


def test_an_allowed_decision_reports_200() -> None:
    allowed = rbac.Authorization(rbac.Decision.ALLOW, "ok", Invariant.ALLOW)
    assert allowed.allowed is True
    assert allowed.http_status == 200


def test_a_not_found_decision_reports_404_and_is_not_allowed() -> None:
    """The cross-tenant answer. It must never be 403, because 403 confirms the
    resource exists to somebody who may not know that."""
    refused = rbac.Authorization(rbac.Decision.NOT_FOUND, "cross_tenant", Invariant.ALLOW)
    assert refused.allowed is False
    assert refused.http_status == 404


def test_a_denied_decision_reports_403() -> None:
    refused = rbac.Authorization(rbac.Decision.DENY, "capability_not_granted", Invariant.DENY)
    assert refused.allowed is False
    assert refused.http_status == 403


# ── Tenant comparison ────────────────────────────────────────────────────────


def test_a_missing_tenant_on_either_side_is_never_the_same_tenant() -> None:
    """Absent is not equal. Two rows with no tenant must not be treated as
    belonging together, which would make a tenant-less resource readable by
    anyone whose own tenant is also unset."""
    assert rbac._same_tenant(None, None) is False
    assert rbac._same_tenant(TENANT, None) is False
    assert rbac._same_tenant(None, TENANT) is False


def test_the_same_tenant_compares_equal_across_uuid_and_string() -> None:
    """A principal carries a UUID and a loaded row may carry its string form;
    a mismatch there would refuse a legitimate caller."""
    assert rbac._same_tenant(TENANT, str(TENANT)) is True


# ── The refusal arms, in RBAC 3's order ──────────────────────────────────────


def test_a_cross_tenant_resource_is_404_before_anything_else_is_considered() -> None:
    """Tenant is checked FIRST, so a caller who also lacks the capability
    still gets 404 rather than 403: the shape of the refusal must not vary
    with facts about a resource they cannot see."""
    resource = rbac.Resource(kind="job", tenant_id=OTHER_TENANT, job_id=uuid.uuid4())
    result = rbac.decide(
        _principal(), caps.VIEW_CANDIDATE_RATINGS, resource, granted=False
    )
    assert result.decision is rbac.Decision.NOT_FOUND
    assert result.http_status == 404


def test_a_capability_the_engine_denies_is_403_with_a_named_reason() -> None:
    resource = rbac.Resource(kind="job", tenant_id=TENANT, job_id=uuid.uuid4())
    result = rbac.decide(
        _principal(), caps.VIEW_CANDIDATE_RATINGS, resource, granted=False
    )
    assert result.decision is rbac.Decision.DENY
    assert result.reason == "capability_not_granted"


def test_a_scoped_capability_without_a_job_is_refused_rather_than_waved_through() -> None:
    """A scoped role holds its capability only for a job it is assigned to.
    A resource with no job cannot satisfy that, and defaulting to allow would
    make "scoped" mean nothing for exactly the resources hardest to scope."""
    resource = rbac.Resource(kind="job", tenant_id=TENANT, job_id=None)
    result = rbac.decide(
        _principal(Role.recruiter), caps.VIEW_CANDIDATE_RATINGS, resource, granted=True
    )
    assert result.decision is rbac.Decision.DENY
    assert result.reason == "scoped_capability_needs_a_job"


def test_an_org_wide_role_is_not_narrowed_by_the_scope_rule() -> None:
    """The HR Manager holds the same capability unscoped, so the arm above must
    not fire for them."""
    resource = rbac.Resource(kind="job", tenant_id=TENANT, job_id=uuid.uuid4())
    result = rbac.decide(
        _principal(Role.hr_manager), caps.VIEW_CANDIDATE_RATINGS, resource, granted=True
    )
    assert result.allowed is True


# ── The permission cache ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_cached_permission_payload_is_returned_without_a_query(monkeypatch) -> None:
    """The engine caches the ROWS for two minutes. A cache hit must reconstruct
    the same tuples the query would have produced, including the boolean, or a
    grant silently changes shape depending on whether the cache was warm."""
    async def fake_get_json(_key):  # noqa: ANN001
        return [[None, "view_dashboard", True], [str(TENANT), "create_job", False]]

    monkeypatch.setattr(rbac.tenant_cache, "get_json", fake_get_json)

    class _Session:
        async def execute(self, *_a, **_k):  # noqa: ANN001, ANN002
            raise AssertionError("a cache hit must not reach the database")

    rows = await rbac._permission_rows(_Session(), TENANT, Role.recruiter)
    assert rows == [(None, "view_dashboard", True), (str(TENANT), "create_job", False)]


@pytest.mark.asyncio
async def test_invalidating_one_tenant_drops_only_that_tenant_s_keys(monkeypatch) -> None:
    deleted: list[str] = []
    patterns: list[str] = []

    async def fake_delete(key):  # noqa: ANN001
        deleted.append(key)

    async def fake_delete_pattern(pattern):  # noqa: ANN001
        patterns.append(pattern)

    monkeypatch.setattr(rbac.tenant_cache, "delete", fake_delete)
    monkeypatch.setattr(rbac.tenant_cache, "delete_pattern", fake_delete_pattern)

    await rbac.invalidate_role_permissions(TENANT, [Role.recruiter])
    assert patterns == [], "a single tenant must not clear every tenant's cache"
    assert len(deleted) == 1
    assert str(TENANT) in deleted[0]


@pytest.mark.asyncio
async def test_invalidating_with_no_tenant_clears_the_global_template(monkeypatch) -> None:
    """Editing the global template changes what every tenant inherits, so the
    per-tenant keys all have to go; deleting one tenant's would leave the rest
    serving the old answer for two minutes."""
    patterns: list[str] = []

    async def fake_delete_pattern(pattern):  # noqa: ANN001
        patterns.append(pattern)

    monkeypatch.setattr(rbac.tenant_cache, "delete_pattern", fake_delete_pattern)
    monkeypatch.setattr(rbac.tenant_cache, "delete", lambda *_a: None)

    await rbac.invalidate_role_permissions(None)
    assert len(patterns) == 1
    assert patterns[0].endswith("role_permissions:*")
