"""The SWOT Analysis routes use the platform's authorization, not their own.

Section 27 of the 2026-09-13 spec: "Do not create a separate authorization
system exclusively for SWOT." That is a claim about the SOURCE of every gate on
these four routes, and it is checkable by reading them: the capability each one
names, and the dependency factory it names it through.

WHY THE DEPENDENCY FACTORY MATTERS AS MUCH AS THE CAPABILITY
-------------------------------------------------------------
`require_capability` answers "may this role do this at all" and is the right
gate for a route with no resource in it. These routes all name a job, so the
right gate is `rbac.require_authorized`, which runs the whole RBAC 3 chain:
tenant, then the RBAC 24 ceiling, then the grant, then assignment scope, then
lifecycle state. A SWOT route on the weaker gate would be authorized and still
let a Recruiter edit a job they are not assigned to, which is the exact
failure section 11 describes.

THE VIEW AND EDIT SPLIT IS AT THE API, NOT ONLY IN THE INTERFACE
------------------------------------------------------------------
Section 27 wants a real split: view access shows the SWOT and no controls,
edit access shows the controls and no restriction notice. Two capabilities on
two sets of routes is what makes that true for a caller with a terminal, and
not merely for a caller with a browser.
"""
from __future__ import annotations

import inspect

import pytest
from fastapi import routing

# The routes moved from `api/assessments.py` to `api/job_setup.py` (Vivekium
# release) with every URL and every gate unchanged.
from app.api import job_setup
from app.models.enums import Role
from app.services import capabilities as caps
from app.services import rbac

#: route path -> (method, the capability its gate must name)
EXPECTED_GATES: dict[tuple[str, str], str] = {
    ("GET", "/jobs/{job_id}/swot-analysis"): caps.VIEW_COMPANY_JOBS,
    ("PUT", "/jobs/{job_id}/swot-analysis"): caps.EDIT_SWOT,
    ("POST", "/jobs/{job_id}/swot-analysis/generate"): caps.EDIT_SWOT,
    ("POST", "/jobs/{job_id}/swot-analysis/restore"): caps.EDIT_SWOT,
}


def _swot_analysis_routes() -> dict[tuple[str, str], routing.APIRoute]:
    found: dict[tuple[str, str], routing.APIRoute] = {}
    for route in job_setup.router.routes:
        if not isinstance(route, routing.APIRoute):
            continue
        if "swot-analysis" not in route.path:
            continue
        for method in route.methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            found[(method, route.path)] = route
    return found


def _gate_source(route: routing.APIRoute) -> str:
    """The handler's signature, as written. The dependency factory's NAME is
    what this test is about, and the name survives into the source."""
    return inspect.getsource(route.endpoint)


def test_the_feature_exposes_exactly_the_four_routes_it_documents() -> None:
    assert set(_swot_analysis_routes()) == set(EXPECTED_GATES)


@pytest.mark.parametrize("key", sorted(EXPECTED_GATES))
def test_each_route_names_the_capability_the_spec_assigns_it(key) -> None:
    route = _swot_analysis_routes()[key]
    source = _gate_source(route)
    expected = EXPECTED_GATES[key]
    # Named through the capability CONSTANT, never a string literal: a literal
    # survives a rename and silently stops matching any seeded row.
    constant = next(
        name
        for name in dir(caps)
        if name.isupper() and getattr(caps, name, None) == expected
    )
    assert f"caps.{constant}" in source, (
        f"{key[0]} {key[1]} must gate on caps.{constant}"
    )


#: The three routes that CHANGE the document. Each one runs the whole RBAC 3
#: chain, because a wrong answer here changes what the job says.
WRITE_ROUTES = {
    key for key in EXPECTED_GATES if key[0] in ("PUT", "POST")
}


@pytest.mark.parametrize("key", sorted(WRITE_ROUTES))
def test_every_write_runs_the_full_chain_and_not_the_capability_gate_alone(
    key,
) -> None:
    source = _gate_source(_swot_analysis_routes()[key])
    assert "rbac.require_authorized" in source, (
        f"{key[0]} {key[1]} changes the document, so it needs tenant, scope "
        "and state, not require_capability alone"
    )


def test_the_read_is_not_stricter_than_reading_the_job_it_belongs_to() -> None:
    """The read deliberately does NOT apply assignment scope.

    RBAC 24 marks `view_company_jobs` SCOPED for three of the five client
    roles. Since the Vivekium release the CREATOR of a job is assigned to it
    (`rbac.assign_creator`), but nothing assigns anybody else, so a scope
    check on the read would still refuse a Recruiter the SWOT of a job
    somebody else created while its JD is on the same page, which is stricter
    than the job endpoint itself and is the contradiction section 5 forbids,
    pointing the other way.

    The WRITES are unaffected and stay on the full chain. When an assignment
    workflow exists, this test is the place the decision gets revisited.
    """
    source = _gate_source(_swot_analysis_routes()[("GET", "/jobs/{job_id}/swot-analysis")])
    assert "require_capability(caps.VIEW_COMPANY_JOBS)" in source
    assert "rbac.require_authorized" not in source


def test_no_swot_route_invents_a_capability_of_its_own() -> None:
    """Section 27, stated as the absence it asks for: every capability these
    routes name already exists in the platform's list."""
    for key, capability in EXPECTED_GATES.items():
        assert capability in caps.ALL_CAPABILITIES, key


def test_the_write_capability_is_the_one_rbac_24_already_governs() -> None:
    """`edit_swot` is a Hiring-Manager-controlled field. Reusing it means the
    SWOT document inherits RBAC 24's ceiling without a line of new code, and
    that inheritance is what this asserts."""
    assert caps.EDIT_SWOT in caps.HIRING_MANAGER_CONTROLLED
    row = caps.RBAC_INVARIANTS[caps.EDIT_SWOT]
    assert row[Role.recruiter] is caps.Invariant.NEVER
    assert row[Role.hiring_manager] is caps.Invariant.SCOPED
    assert row[Role.hr_manager] is caps.Invariant.ALLOW
    assert row[Role.interview_manager] is caps.Invariant.DENY


def test_an_interview_manager_reads_the_job_and_cannot_write_the_swot() -> None:
    """The view/edit split, resolved through the real decision layer rather
    than asserted about the interface."""
    import uuid

    tenant = uuid.uuid4()
    user = uuid.uuid4()
    job = uuid.uuid4()
    resource = rbac.Resource(
        kind="job",
        tenant_id=tenant,
        job_id=job,
        lifecycle_state="draft",
        assignments=frozenset({(rbac.ASSIGNMENT_INTERVIEW_MANAGER, str(user))}),
    )
    principal = rbac.Principal(
        user_id=user, tenant_id=tenant, role=Role.interview_manager
    )

    assert rbac.decide(
        principal, caps.VIEW_COMPANY_JOBS, resource, granted=True
    ).allowed is True
    # Even if a tenant row granted it, the ceiling refuses.
    assert rbac.decide(
        principal, caps.EDIT_SWOT, resource, granted=True
    ).allowed is False
