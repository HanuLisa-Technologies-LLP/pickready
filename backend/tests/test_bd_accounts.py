"""Business Development ACCOUNTS — how a `bd` user comes into existence and
where their token may go.

The BD Portal's backend shipped complete but unreachable: no route could create
a `bd` user, because every invite path in the product is tenant-scoped and a BD
user deliberately has none. These tests guard the four things that make the
fourth portal reachable without weakening the other three:

  * a BD row is PLATFORM staff — `tenant_id` NULL, role `bd`, status `invited`
    until Firebase binds an identity. A row minted with a tenant would quietly
    become a customer's employee;
  * the token audience is OWNER, and the ORG session dependency must still
    refuse it. `bd` in `_ORG_ROLES` would hand a tenant-less user the
    tenant-scoped session and produce a 500 at best;
  * only the Owner may list or create BD accounts, enforced by the same
    `get_superadmin_db` dependency as the rest of the console;
  * DISABLING IS REVERSIBLE and never deletes. A BD rep owns leads.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import admin as admin_api
from app.api import deps
from app.core.security import AUDIENCE_ORG, AUDIENCE_OWNER, audience_for_role
from app.models.enums import Role, UserStatus
from app.models.user import User
from app.schemas.admin import BDUserCreateIn, BDUserUpdateIn
from app.services import capabilities as caps
from app.services.capabilities import DEFAULT_PERMISSION_MATRIX

NOW = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)


# ── Fakes ────────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, rows: list):
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _Session:
    """Just enough AsyncSession for the three handlers under test."""

    def __init__(self, rows: list | None = None):
        self.rows = rows or []
        self.added: list = []

    async def execute(self, _query):
        return _Result(self.rows)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            if getattr(obj, "created_at", None) is None:
                obj.created_at = NOW


class _DeleteSession(_Session):
    """`_Session` plus the two things the delete handler needs: an UPDATE that
    reports a rowcount, and `delete`."""

    def __init__(self, rows: list | None = None, released: int = 0):
        super().__init__(rows)
        self.released = released
        self.updates = 0
        self.deleted: list = []

    async def execute(self, query):
        # The handler's only non-SELECT is the lead release.
        if query.__class__.__name__ == "Update":
            self.updates += 1
            return SimpleNamespace(rowcount=self.released)
        return _Result(self.rows)

    async def delete(self, obj) -> None:
        self.deleted.append(obj)


def _bd_row(**overrides):
    base = dict(
        id=uuid.uuid4(),
        tenant_id=None,
        role=Role.bd,
        email="bd@pickready.example",
        phone=None,
        full_name="BD Rep",
        status=UserStatus.invited,
        firebase_uid=None,
        created_at=NOW,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _owner() -> deps.CurrentUser:
    return deps.CurrentUser(
        user_id=uuid.uuid4(), tenant_id=None, role=Role.super_admin,
        audience=AUDIENCE_OWNER,
    )


@pytest.fixture
def no_audit(monkeypatch):
    recorded: list[dict] = []

    async def _audit(_session, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(admin_api, "audit", _audit)
    return recorded


# ── A BD user is platform staff ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_created_bd_user_has_no_tenant_and_the_bd_role(no_audit) -> None:
    session = _Session()
    out = await admin_api.create_bd_user(
        BDUserCreateIn(email="rep@pickready.example", full_name="Rep One"),
        user=_owner(),
        session=session,
    )
    created = session.added[0]
    assert isinstance(created, User)
    assert created.tenant_id is None
    assert created.role is Role.bd
    # Reserved, not yet usable: Firebase owns the credential and binds on the
    # first proven sign-in.
    assert created.status is UserStatus.invited
    assert created.firebase_uid is None
    assert out.signed_in is False
    assert out.status == "invited"
    assert no_audit[0]["action"] == "bd_user_created"
    assert no_audit[0]["tenant_id"] is None


@pytest.mark.asyncio
async def test_no_password_is_accepted_or_stored() -> None:
    """claude.md rule 2: Firebase owns credentials. The guarantee is that the
    payload has no such FIELD, not that a handler ignores it."""
    assert set(BDUserCreateIn.model_fields) == {"email", "full_name", "phone"}
    assert "password" not in set(BDUserUpdateIn.model_fields)


@pytest.mark.asyncio
async def test_a_duplicate_bd_email_is_refused(no_audit) -> None:
    """`uq_users_tenant_email_role` cannot catch this: NULL tenant_ids do not
    collide, so two rows would insert and the login lookup would go ambiguous.
    """
    session = _Session([_bd_row(email="rep@pickready.example")])
    with pytest.raises(HTTPException) as caught:
        await admin_api.create_bd_user(
            BDUserCreateIn(email="rep@pickready.example"),
            user=_owner(),
            session=session,
        )
    assert caught.value.status_code == 409
    assert session.added == []


# ── The token audience ───────────────────────────────────────────────────────

def test_a_bd_token_carries_the_owner_audience() -> None:
    assert audience_for_role("bd") == AUDIENCE_OWNER
    assert audience_for_role(Role.bd) == AUDIENCE_OWNER


@pytest.mark.asyncio
async def test_a_bd_token_cannot_reach_a_tenant_scoped_session() -> None:
    """The owner audience is not an org audience. `get_tenant_db` must refuse
    before it ever tries to open an RLS scope for a tenant that is NULL."""
    bd_user = deps.CurrentUser(
        user_id=uuid.uuid4(), tenant_id=None, role=Role.bd, audience=AUDIENCE_OWNER,
    )
    with pytest.raises(HTTPException) as caught:
        await deps.get_tenant_db(user=bd_user).__anext__()
    assert caught.value.status_code == 403


def test_bd_is_not_an_org_role() -> None:
    """Adding it there would route a tenant-less user through `get_tenant_db`."""
    from app.core.security import _ORG_ROLES

    assert "bd" not in _ORG_ROLES
    assert audience_for_role("hr_manager") == AUDIENCE_ORG


# ── Who may manage BD accounts ───────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,audience",
    [
        (Role.bd, AUDIENCE_OWNER),
        (Role.hr_manager, AUDIENCE_ORG),
        (Role.client, AUDIENCE_ORG),
    ],
)
async def test_only_the_owner_reaches_the_bd_routes(role, audience) -> None:
    """Gating is the shared `get_superadmin_db` dependency, which demands BOTH
    the owner audience and the super_admin role. A BD user holds the owner
    AUDIENCE, so the role half of that check is what stops them managing their
    own colleagues."""
    caller = deps.CurrentUser(
        user_id=uuid.uuid4(), tenant_id=None, role=role, audience=audience,
    )
    request = SimpleNamespace(
        method="GET", url=SimpleNamespace(path="/admin/bd-users", query=""),
    )
    with pytest.raises(HTTPException) as caught:
        await deps.get_superadmin_db(request=request, user=caller).__anext__()
    assert caught.value.status_code == 403


def test_the_bd_routes_are_all_gated_by_the_superadmin_session() -> None:
    paths = {
        route.path: route
        for route in admin_api.router.routes
        if getattr(route, "path", "").startswith("/bd-users")
    }
    assert set(paths) == {"/bd-users", "/bd-users/{bd_user_id}"}
    for route in paths.values():
        names = {dep.call for dep in route.dependant.dependencies}
        assert deps.get_superadmin_db in names, route.path


# ── Delete (client decision, 2026-08-09) ─────────────────────────────────────
# This REVERSES the previous "there is no delete route" rule, and the reason
# that rule existed is what these tests now guard instead: a BD rep owns leads,
# and a lead is the pipeline, not the rep. Deleting the account must release
# the leads, never delete them, and never touch a promoted customer.

@pytest.mark.asyncio
async def test_deleting_a_bd_user_releases_their_leads_instead_of_deleting_them(
    no_audit,
) -> None:
    row = _bd_row(email="rep@pickready.example")
    session = _DeleteSession([row], released=3)
    out = await admin_api.delete_bd_user(
        row.id, confirm="rep@pickready.example", user=_owner(), session=session
    )
    assert session.deleted == [row]
    assert out.leads_released == 3
    # The UPDATE that ran is a release to unassigned, not a DELETE of the leads.
    assert session.updates == 1
    assert no_audit[0]["action"] == "bd_user_deleted"
    assert no_audit[0]["metadata"]["leads_released"] == 3


@pytest.mark.asyncio
async def test_deleting_a_bd_user_needs_the_email_retyped(no_audit) -> None:
    row = _bd_row(email="rep@pickready.example")
    session = _DeleteSession([row])
    for wrong in ("", "rep@pickready", "yes", "Rep One"):
        with pytest.raises(HTTPException) as caught:
            await admin_api.delete_bd_user(
                row.id, confirm=wrong, user=_owner(), session=session
            )
        assert caught.value.status_code == 400
    assert session.deleted == []
    assert session.updates == 0


@pytest.mark.asyncio
async def test_the_delete_confirmation_is_about_intent_not_typing(no_audit) -> None:
    """Same guard as the tenant delete: case and whitespace insensitive, and a
    literal DELETE is accepted."""
    for typed in ("  REP@PickReady.example ", "delete", "DELETE"):
        row = _bd_row(email="rep@pickready.example")
        session = _DeleteSession([row])
        await admin_api.delete_bd_user(
            row.id, confirm=typed, user=_owner(), session=session
        )
        assert session.deleted == [row]


@pytest.mark.asyncio
async def test_deleting_an_unknown_bd_user_is_a_404(no_audit) -> None:
    session = _DeleteSession([])
    with pytest.raises(HTTPException) as caught:
        await admin_api.delete_bd_user(
            uuid.uuid4(), confirm="delete", user=_owner(), session=session
        )
    assert caught.value.status_code == 404


def test_delete_is_the_only_new_verb_and_is_still_owner_gated() -> None:
    """Adding a destructive route must not have widened who can reach it."""
    delete_routes = [
        route
        for route in admin_api.router.routes
        if getattr(route, "path", "").startswith("/bd-users")
        and "DELETE" in (getattr(route, "methods", None) or set())
    ]
    assert len(delete_routes) == 1
    assert delete_routes[0].path == "/bd-users/{bd_user_id}"
    names = {dep.call for dep in delete_routes[0].dependant.dependencies}
    assert deps.get_superadmin_db in names


def test_disable_survives_as_the_reversible_alternative() -> None:
    """Delete did not replace Disable. Disable is still correct for a rep who
    may come back: it is reversible and keeps their lead ownership intact."""
    assert "status" in set(BDUserUpdateIn.model_fields)


# ── Listing ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_owner_lists_bd_users_with_their_sign_in_state() -> None:
    session = _Session([
        _bd_row(email="one@pickready.example"),
        _bd_row(email="two@pickready.example", firebase_uid="uid-2",
                status=UserStatus.active),
    ])
    rows = await admin_api.list_bd_users(session=session)
    assert [row.email for row in rows] == [
        "one@pickready.example", "two@pickready.example",
    ]
    # "Has never signed in" is the usual reason a new BD account looks broken,
    # so the list says so rather than leaving the operator to guess.
    assert [row.signed_in for row in rows] == [False, True]


# ── Disabling is reversible ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disabling_then_re_enabling_restores_the_account(no_audit) -> None:
    row = _bd_row(firebase_uid="uid-1", status=UserStatus.active)
    session = _Session([row])
    out = await admin_api.update_bd_user(
        row.id, BDUserUpdateIn(status="disabled"), user=_owner(), session=session,
    )
    assert out.status == "disabled"
    assert row.status is UserStatus.disabled
    # Nothing else moved: the leads this person owns are untouched.
    assert row.firebase_uid == "uid-1"

    out = await admin_api.update_bd_user(
        row.id, BDUserUpdateIn(status="active"), user=_owner(), session=session,
    )
    assert out.status == "active"
    assert row.status is UserStatus.active


@pytest.mark.asyncio
async def test_re_enabling_an_account_that_never_signed_in_returns_to_invited(
    no_audit,
) -> None:
    """Reporting `active` for a row with no Firebase identity would claim a
    working login that does not exist."""
    row = _bd_row(status=UserStatus.disabled, firebase_uid=None)
    out = await admin_api.update_bd_user(
        row.id, BDUserUpdateIn(status="active"), user=_owner(),
        session=_Session([row]),
    )
    assert out.status == "invited"


@pytest.mark.asyncio
async def test_details_can_be_edited_without_touching_the_status(no_audit) -> None:
    row = _bd_row(status=UserStatus.disabled, full_name="Old Name")
    await admin_api.update_bd_user(
        row.id, BDUserUpdateIn(full_name="New Name", phone="9876543210"),
        user=_owner(), session=_Session([row]),
    )
    assert row.full_name == "New Name"
    assert row.phone == "9876543210"
    assert row.status is UserStatus.disabled


@pytest.mark.asyncio
async def test_an_unknown_bd_user_is_a_404(no_audit) -> None:
    with pytest.raises(HTTPException) as caught:
        await admin_api.update_bd_user(
            uuid.uuid4(), BDUserUpdateIn(full_name="X"), user=_owner(),
            session=_Session([]),
        )
    assert caught.value.status_code == 404


def test_the_email_is_not_editable() -> None:
    """It IS the identity Firebase binds to; changing it after a sign-in would
    orphan the account from its credential."""
    assert "email" not in set(BDUserUpdateIn.model_fields)


def test_an_empty_patch_is_refused() -> None:
    with pytest.raises(ValueError):
        BDUserUpdateIn()


def test_only_the_two_operator_choosable_statuses_are_accepted() -> None:
    with pytest.raises(ValueError):
        BDUserUpdateIn(status="invited")


# ── Capabilities stay data ───────────────────────────────────────────────────

def test_the_bd_role_carries_its_three_capabilities_and_no_recruitment_one() -> None:
    granted = DEFAULT_PERMISSION_MATRIX[Role.bd]
    assert granted == {
        caps.MANAGE_BD_LEADS: True,
        caps.VIEW_BD_CUSTOMERS: True,
        caps.USE_AI_REACH: True,
    }
    for capability in (caps.MANAGE_BD_LEADS, caps.VIEW_BD_CUSTOMERS, caps.USE_AI_REACH):
        assert capability in caps.ALL_CAPABILITIES
