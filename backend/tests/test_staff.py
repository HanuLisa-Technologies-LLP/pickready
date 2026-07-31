"""Staff management + invitation rules — DB-free pure functions.

Covers role validation, the legacy Hiring Manager cap predicate, and the
invite token/lifecycle helpers that the /join flow depends on.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.api.companies import (
    MAX_HIRING_MANAGERS,
    ROLE_LABELS,
    STAFF_ROLES,
    hiring_manager_cap_reached,
    validate_staff_role,
)
from app.models.enums import Role
from app.models.invite import (
    INVITE_ACCEPTED,
    INVITE_EXPIRED,
    INVITE_PENDING,
    INVITE_REVOKED,
    INVITE_TTL_DAYS,
    build_invite_link,
    generate_invite_token,
    hash_invite_token,
    invite_expiry,
    invite_state,
)


# ── Role validation (400 on anything but the 3 staff roles) ──────────────────

def test_the_three_staff_roles_are_accepted() -> None:
    assert validate_staff_role("hr_manager") == Role.hr_manager
    assert validate_staff_role("recruiter") == Role.recruiter
    assert validate_staff_role("hiring_manager") == Role.hiring_manager


@pytest.mark.parametrize("forbidden", ["client", "super_admin", "candidate"])
def test_privileged_and_external_roles_are_rejected(forbidden: str) -> None:
    with pytest.raises(ValueError):
        validate_staff_role(forbidden)


@pytest.mark.parametrize("bogus", ["", "admin", "HR_MANAGER", "owner", "staff"])
def test_unknown_role_strings_are_rejected(bogus: str) -> None:
    with pytest.raises(ValueError):
        validate_staff_role(bogus)


def test_staff_roles_constant_is_exactly_the_three() -> None:
    assert STAFF_ROLES == {Role.hr_manager, Role.recruiter, Role.hiring_manager}


def test_every_staff_role_has_a_human_label() -> None:
    # The flat model means all three are equally addable — each needs copy.
    assert set(ROLE_LABELS) == STAFF_ROLES
    assert ROLE_LABELS[Role.hiring_manager] == "Hiring Manager"


# ── Hiring Manager cap (FR-2.2: max 5 ACTIVE; HR/Recruiter uncapped) ─────────

def test_cap_not_reached_below_five() -> None:
    for count in range(MAX_HIRING_MANAGERS):
        assert hiring_manager_cap_reached(count) is False


def test_cap_reached_at_exactly_five() -> None:
    # The 6th ACTIVE Hiring Manager must be refused (409 at the API layer).
    assert hiring_manager_cap_reached(MAX_HIRING_MANAGERS) is True


def test_cap_reached_above_five() -> None:
    assert hiring_manager_cap_reached(MAX_HIRING_MANAGERS + 3) is True


def test_disabled_hms_free_up_slots() -> None:
    # The caller counts only status != disabled — 5 total with 1 disabled
    # passes 4 into the predicate, which must allow another hire.
    active_after_one_disabled = MAX_HIRING_MANAGERS - 1
    assert hiring_manager_cap_reached(active_after_one_disabled) is False


def test_max_is_five() -> None:
    assert MAX_HIRING_MANAGERS == 5


# ── Invite tokens: unguessable, stored hashed, never recoverable ────────────

def test_tokens_are_unique_and_url_safe() -> None:
    tokens = {generate_invite_token() for _ in range(200)}
    assert len(tokens) == 200
    for token in tokens:
        assert len(token) >= 32
        assert all(ch.isalnum() or ch in "-_" for ch in token)


def test_hash_is_deterministic_sha256_hex() -> None:
    token = generate_invite_token()
    assert hash_invite_token(token) == hash_invite_token(token)
    assert len(hash_invite_token(token)) == 64
    assert hash_invite_token(token) != token  # never store the raw secret


def test_different_tokens_hash_differently() -> None:
    assert hash_invite_token("a") != hash_invite_token("b")


def test_link_points_at_the_join_page() -> None:
    link = build_invite_link("http://localhost:3000", "tok123")
    assert link == "http://localhost:3000/join?invite=tok123"


def test_link_tolerates_a_trailing_slash_on_the_base_url() -> None:
    assert build_invite_link("https://picready.com/", "t") == (
        "https://picready.com/join?invite=t"
    )


# ── Invite lifecycle ────────────────────────────────────────────────────────

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def test_expiry_is_seven_days_out() -> None:
    assert INVITE_TTL_DAYS == 7
    assert invite_expiry(NOW) == NOW + timedelta(days=7)


def test_fresh_invite_is_pending() -> None:
    assert invite_state(
        accepted_at=None, revoked_at=None, expires_at=invite_expiry(NOW), now=NOW
    ) == INVITE_PENDING


def test_past_expiry_is_expired() -> None:
    assert invite_state(
        accepted_at=None, revoked_at=None, expires_at=NOW - timedelta(seconds=1), now=NOW
    ) == INVITE_EXPIRED


def test_expiry_boundary_is_exclusive() -> None:
    # Exactly at expires_at the link is already dead — no grace second.
    assert invite_state(
        accepted_at=None, revoked_at=None, expires_at=NOW, now=NOW
    ) == INVITE_EXPIRED


def test_revoked_beats_expired() -> None:
    assert invite_state(
        accepted_at=None, revoked_at=NOW, expires_at=NOW - timedelta(days=1), now=NOW
    ) == INVITE_REVOKED


def test_accepted_beats_everything() -> None:
    # Single use: once burned, an accepted invite stays accepted forever.
    assert invite_state(
        accepted_at=NOW, revoked_at=NOW, expires_at=NOW - timedelta(days=99), now=NOW
    ) == INVITE_ACCEPTED


def test_naive_expiry_is_treated_as_utc() -> None:
    # Some drivers hand back naive datetimes; this must not raise.
    assert invite_state(
        accepted_at=None,
        revoked_at=None,
        expires_at=datetime(2026, 7, 26, 12, 0),
        now=NOW,
    ) == INVITE_PENDING
