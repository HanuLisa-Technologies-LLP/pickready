"""JWT audience separation (one audience per portal) — DB-free.

Guarantees that a token minted for one portal cannot be decoded against another,
which is the cryptographic half of the cross-portal-reuse defence (the HTTP half
is enforced by the deps in app/api/deps.py).
"""
from __future__ import annotations

import uuid

import jwt as pyjwt
import pytest

from app.core import security
from app.core.security import (
    AUDIENCE_CANDIDATE,
    AUDIENCE_ORG,
    AUDIENCE_OWNER,
    audience_for_role,
)
from app.models.enums import Role


# ── audience_for_role ────────────────────────────────────────────────────────

def test_owner_role_maps_to_owner_audience() -> None:
    assert audience_for_role(Role.super_admin) == AUDIENCE_OWNER


def test_org_roles_map_to_org_audience() -> None:
    for role in (Role.client, Role.hr_manager, Role.recruiter, Role.hiring_manager):
        assert audience_for_role(role) == AUDIENCE_ORG


def test_candidate_role_maps_to_candidate_audience() -> None:
    assert audience_for_role(Role.candidate) == AUDIENCE_CANDIDATE


def test_audience_for_role_accepts_plain_strings() -> None:
    assert audience_for_role("super_admin") == AUDIENCE_OWNER
    assert audience_for_role("recruiter") == AUDIENCE_ORG
    assert audience_for_role("candidate") == AUDIENCE_CANDIDATE


def test_unknown_role_has_no_audience() -> None:
    with pytest.raises(ValueError):
        audience_for_role("root")


def test_the_three_audiences_are_distinct() -> None:
    assert len({AUDIENCE_OWNER, AUDIENCE_ORG, AUDIENCE_CANDIDATE}) == 3


def test_internal_alias_points_at_org_not_owner() -> None:
    # The deprecated single "internal" audience now means ORG; an owner token
    # must NOT be mintable through the alias.
    assert security.AUDIENCE_INTERNAL == AUDIENCE_ORG
    assert security.AUDIENCE_INTERNAL != AUDIENCE_OWNER


# ── Token round-trips and cross-audience rejection ──────────────────────────

def _access(role: Role, tenant=None):
    return security.create_access_token(
        uuid.uuid4(), role.value, tenant, audience=audience_for_role(role)
    )


def test_token_decodes_under_its_own_audience() -> None:
    tok = _access(Role.recruiter, uuid.uuid4())
    payload = security.decode_token(tok, audience=AUDIENCE_ORG)
    assert payload["aud"] == AUDIENCE_ORG
    assert payload["role"] == "recruiter"


def test_owner_token_rejected_under_org_audience() -> None:
    owner = _access(Role.super_admin)
    with pytest.raises(pyjwt.InvalidAudienceError):
        security.decode_token(owner, audience=AUDIENCE_ORG)


def test_org_token_rejected_under_owner_audience() -> None:
    org = _access(Role.client, uuid.uuid4())
    with pytest.raises(pyjwt.InvalidAudienceError):
        security.decode_token(org, audience=AUDIENCE_OWNER)


def test_candidate_token_rejected_under_internal_audiences() -> None:
    cand = _access(Role.candidate)
    for aud in (AUDIENCE_OWNER, AUDIENCE_ORG):
        with pytest.raises(pyjwt.InvalidAudienceError):
            security.decode_token(cand, audience=aud)


def test_org_token_rejected_under_candidate_audience() -> None:
    org = _access(Role.hr_manager, uuid.uuid4())
    with pytest.raises(pyjwt.InvalidAudienceError):
        security.decode_token(org, audience=AUDIENCE_CANDIDATE)


def test_refresh_token_carries_the_selected_audience() -> None:
    tok = security.create_refresh_token(uuid.uuid4(), audience=AUDIENCE_OWNER)
    payload = security.decode_token(tok, audience=AUDIENCE_OWNER)
    assert payload["type"] == "refresh"
    with pytest.raises(pyjwt.InvalidAudienceError):
        security.decode_token(tok, audience=AUDIENCE_ORG)
