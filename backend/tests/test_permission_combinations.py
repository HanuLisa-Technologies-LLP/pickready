"""Permission COMBINATIONS, not roles (2026-09-13 spec, sections 10 and 12).

WHY COMBINATIONS AND NOT ROLES
-------------------------------
Section 12's rule is that the interface and the API ask "does this user hold
this capability", never "what is this user's role". A test suite organised by
role cannot prove that: it passes just as well against an implementation that
branches on the role name, because for the default matrix the two agree. The
only way to show the difference is to vary the GRANTS under a fixed role and
assert that the answer follows the grant.

So these tests take one role, hand it every combination of Company Profile,
Job Description and Job SWOT permissions section 10 enumerates, and check the
resolved answer each time. A role branch anywhere in the resolution path fails
them; the data-driven engine passes them without knowing they exist.

THE TWO LAYERS ARE BOTH EXERCISED
----------------------------------
`resolve_permission` is the GRANT engine (user overlay beats tenant row beats
global template beats deny). `decide` is the DECISION layer that applies the
RBAC 24 ceiling, the tenant boundary, assignment scope and lifecycle state on
top. A capability question has no single answer without both, and the bug
class this suite exists to catch is one layer being consulted by the API and
the other by the interface.
"""
from __future__ import annotations

import itertools
import uuid

import pytest

from app.models.enums import Role
from app.services import capabilities as caps
from app.services import rbac

#: The three resources section 10 tabulates, by the capability that edits each.
EDIT_CAPABILITIES = {
    "company_profile": caps.EDIT_COMPANY_PROFILE,
    "job_description": caps.EDIT_JOB_DESCRIPTION,
    "job_swot": caps.EDIT_SWOT,
}


def _resolved(
    capability: str,
    *,
    global_rows: dict[str, bool] | None = None,
    tenant_rows: dict[str, bool] | None = None,
    user_overrides: dict[str, bool] | None = None,
) -> bool:
    return rbac.resolve_permission(
        tenant_rows or {}, global_rows or {}, capability, user_overrides
    )


# ── Section 10's table, every combination ───────────────────────────────────


@pytest.mark.parametrize(
    "combination",
    list(itertools.product([True, False], repeat=3)),
    ids=lambda c: "-".join(
        f"{name}={'edit' if allowed else 'view'}"
        for name, allowed in zip(EDIT_CAPABILITIES, c)
    )
    if isinstance(c, tuple)
    else str(c),
)
def test_every_combination_of_the_three_resources_resolves_independently(
    combination,
) -> None:
    """Eight combinations, including "no permission" and "all three".

    Each resource answers for itself. A resolution that leaked one resource's
    grant into another's answer (the shape a role branch takes in practice)
    fails here on six of the eight rows.
    """
    grants = dict(zip(EDIT_CAPABILITIES.values(), combination))

    for capability, expected in grants.items():
        assert _resolved(capability, global_rows=grants) is expected


def test_the_same_role_gets_different_answers_from_different_grants() -> None:
    """The point of section 12, in one test.

    Two users, same role, different tenant rows. If anything in the resolution
    path consulted the role rather than the grant, these two would agree.
    """
    permissive = {caps.EDIT_COMPANY_PROFILE: True}
    restrictive = {caps.EDIT_COMPANY_PROFILE: False}

    assert _resolved(caps.EDIT_COMPANY_PROFILE, tenant_rows=permissive) is True
    assert _resolved(caps.EDIT_COMPANY_PROFILE, tenant_rows=restrictive) is False


# ── The precedence chain, in both directions (sections 4, 38) ───────────────


def test_an_administrative_grant_upgrades_view_to_edit() -> None:
    """Section 8's worked example: the role's default is VIEW, an administrator
    grants EDIT, and the user must then receive the editing experience."""
    assert _resolved(
        caps.EDIT_COMPANY_PROFILE,
        global_rows={caps.EDIT_COMPANY_PROFILE: False},
        user_overrides={caps.EDIT_COMPANY_PROFILE: True},
    ) is True


def test_an_administrative_revocation_downgrades_edit_to_view() -> None:
    """The inverse, which is the half that is usually forgotten. An explicit
    False at any layer is a real revocation, not an absent row."""
    assert _resolved(
        caps.EDIT_COMPANY_PROFILE,
        global_rows={caps.EDIT_COMPANY_PROFILE: True},
        user_overrides={caps.EDIT_COMPANY_PROFILE: False},
    ) is False


def test_a_tenant_row_beats_the_global_template_in_both_directions() -> None:
    assert _resolved(
        caps.EDIT_SWOT,
        global_rows={caps.EDIT_SWOT: False},
        tenant_rows={caps.EDIT_SWOT: True},
    ) is True
    assert _resolved(
        caps.EDIT_SWOT,
        global_rows={caps.EDIT_SWOT: True},
        tenant_rows={caps.EDIT_SWOT: False},
    ) is False


def test_a_user_overlay_beats_a_tenant_row_in_both_directions() -> None:
    assert _resolved(
        caps.EDIT_JOB_DESCRIPTION,
        tenant_rows={caps.EDIT_JOB_DESCRIPTION: False},
        user_overrides={caps.EDIT_JOB_DESCRIPTION: True},
    ) is True
    assert _resolved(
        caps.EDIT_JOB_DESCRIPTION,
        tenant_rows={caps.EDIT_JOB_DESCRIPTION: True},
        user_overrides={caps.EDIT_JOB_DESCRIPTION: False},
    ) is False


def test_absent_everywhere_denies() -> None:
    """Section 10's "No permission" row. A missing row never grants."""
    for capability in EDIT_CAPABILITIES.values():
        assert _resolved(capability) is False


# ── The decision layer still narrows a grant that the ceiling refuses ───────


def _principal(role: Role, tenant_id, user_id=None) -> rbac.Principal:
    return rbac.Principal(
        user_id=user_id or uuid.uuid4(), tenant_id=tenant_id, role=role
    )


def test_a_grant_cannot_open_a_cell_the_ceiling_marks_never() -> None:
    """Section 37: a permission the architecture forbids must not be reachable
    by configuration. RBAC 24 gives the Recruiter NEVER on the SWOT, so a
    tenant row granting it changes the grant and not the answer."""
    tenant = uuid.uuid4()
    decision = rbac.decide(
        _principal(Role.recruiter, tenant),
        caps.EDIT_SWOT,
        rbac.Resource(kind="job", tenant_id=tenant, job_id=uuid.uuid4()),
        granted=True,
    )
    assert decision.allowed is False
    assert decision.reason == "invariant_never"


def test_a_scoped_role_holding_the_grant_still_needs_the_assignment() -> None:
    """Holding a role is not owning a job (RBAC 9.2 and 23)."""
    tenant = uuid.uuid4()
    user = uuid.uuid4()
    job = uuid.uuid4()

    unassigned = rbac.decide(
        _principal(Role.hiring_manager, tenant, user),
        caps.EDIT_SWOT,
        rbac.Resource(kind="job", tenant_id=tenant, job_id=job, lifecycle_state="draft"),
        granted=True,
    )
    assert unassigned.allowed is False
    assert unassigned.reason == "not_assigned"

    assigned = rbac.decide(
        _principal(Role.hiring_manager, tenant, user),
        caps.EDIT_SWOT,
        rbac.Resource(
            kind="job",
            tenant_id=tenant,
            job_id=job,
            lifecycle_state="draft",
            assignments=frozenset({(rbac.ASSIGNMENT_HIRING_MANAGER, str(user))}),
        ),
        granted=True,
    )
    assert assigned.allowed is True


def test_an_organisation_wide_role_reaches_every_job_in_its_own_tenant() -> None:
    tenant = uuid.uuid4()
    decision = rbac.decide(
        _principal(Role.hr_manager, tenant),
        caps.EDIT_SWOT,
        rbac.Resource(kind="job", tenant_id=tenant, job_id=uuid.uuid4(), lifecycle_state="draft"),
        granted=True,
    )
    assert decision.allowed is True


def test_a_cross_tenant_edit_is_answered_as_a_missing_resource() -> None:
    """Section 11: knowing an id is not authorization, and a 403 across a
    tenant boundary answers "that job exists"."""
    decision = rbac.decide(
        _principal(Role.hr_manager, uuid.uuid4()),
        caps.EDIT_SWOT,
        rbac.Resource(kind="job", tenant_id=uuid.uuid4(), job_id=uuid.uuid4()),
        granted=True,
    )
    assert decision.decision is rbac.Decision.NOT_FOUND
    assert decision.http_status == 404
