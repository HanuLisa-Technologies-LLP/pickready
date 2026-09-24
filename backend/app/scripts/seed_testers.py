"""Reserve named accounts on a customer so real people can sign in and test.

WHAT "RESERVE" MEANS, AND WHY IT IS NOT "CREATE WITH A PASSWORD"
-----------------------------------------------------------------
Vivekium stores no password, has no password store and no forgot-password
flow: Firebase owns credentials (claude.md rule 2). So an account here is a
`users` row carrying an email, a name and a ROLE, with `status = invited` and
no `firebase_uid`. The person proves the address through Firebase, with Google
or with an email and password they choose there, and `/auth/firebase/session`
binds the uid and flips the row to `active` on their first sign-in.

A password handed to this script would therefore have nowhere to go. It is not
accepted, not stored and not logged, and that is a property of the design
rather than a limitation of this file.

WHY THE ROSTER IS AN ARGUMENT AND NOT A CONSTANT
--------------------------------------------------
These are real people's addresses. A constant would commit them to the
repository permanently for the sake of one afternoon's testing, so the roster
arrives in `READYPICK_TESTERS` as JSON:

    [{"email": "...", "full_name": "...", "role": "client"}, ...]

`client` is the customer's SUPER ADMIN in RBAC terms, tenant-scoped, and is
never the platform `super_admin` whose tenant is NULL. Confusing those two is a
privilege escalation that looks correct in a diff, so this script refuses every
role outside one customer organisation's own set.

WHAT IT REUSES RATHER THAN REIMPLEMENTS
-----------------------------------------
The invite primitives in `app.models.invite`, the owner invariant, and the
`pickready.send_email` task: the same ones the company portal's staff invite
(`POST /companies/me/staff`) uses.
The one thing it deliberately does NOT do is print the join link. An invite
token is a credential, and a credential in a CloudWatch log is a credential.
The invitation goes by email, the way it does for every other invited account.

IDEMPOTENT. A rerun updates the name, revokes any invitation still outstanding
and sends a fresh one. It leaves a row that has already bound a Firebase
identity alone, because re-inviting somebody who is already signed in sends
them to a page that tells them so.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update as sa_update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.models.enums import Role, UserStatus
from app.models.invite import (
    StaffInvite,
    build_invite_link,
    generate_invite_token,
    hash_invite_token,
    invite_expiry,
)
from app.models.tenant import Tenant
from app.models.user import User
from app.services.owner import OwnerRoleViolation, ensure_owner_invariant
from app.workers.dispatch import dispatch

ROSTER_ENV = "READYPICK_TESTERS"
TENANT_ENV = "READYPICK_TESTER_TENANT"

#: Exactly the roles a customer organisation has. The platform `super_admin` is
#: deliberately absent: this script must not be able to mint one.
ALLOWED_ROLES = frozenset(
    {
        Role.client,
        Role.recruitment_manager,
        Role.hr_manager,
        Role.recruiter,
        Role.hiring_manager,
        Role.interview_manager,
    }
)


class RosterRefused(RuntimeError):
    """The roster or the target customer is not usable. Nothing was written."""


def _roster() -> list[dict[str, str]]:
    raw = os.environ.get(ROSTER_ENV, "").strip()
    if not raw:
        raise RosterRefused(f"{ROSTER_ENV} is not set")
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise RosterRefused(f"{ROSTER_ENV} is not valid JSON") from exc
    if not isinstance(parsed, list) or not parsed:
        raise RosterRefused(f"{ROSTER_ENV} must be a non-empty JSON array")

    entries: list[dict[str, str]] = []
    for item in parsed:
        email = str((item or {}).get("email") or "").strip()
        role_name = str((item or {}).get("role") or "").strip()
        if not email or "@" not in email:
            raise RosterRefused(f"entry has no usable email: {item!r}")
        try:
            role = Role(role_name)
        except ValueError as exc:
            raise RosterRefused(f"{role_name!r} is not a role") from exc
        if role not in ALLOWED_ROLES:
            raise RosterRefused(
                f"{role_name!r} is not a customer role this script may create"
            )
        try:
            ensure_owner_invariant(role, email)
        except OwnerRoleViolation as exc:
            raise RosterRefused(str(exc)) from exc
        entries.append(
            {
                "email": email,
                "full_name": str((item or {}).get("full_name") or "").strip(),
                "role": role.value,
            }
        )
    return entries


async def _tenant(session, name: str) -> Tenant:
    tenant = (
        (
            await session.execute(
                select(Tenant).where(func.lower(Tenant.name) == name.strip().lower())
            )
        )
        .scalars()
        .first()
    )
    if tenant is None:
        # Refused, never created. A customer is onboarded through the Provider
        # console, which does a great deal more than insert a row; inventing one
        # here would produce a tenant with no billing, no permission rows and no
        # compliance slots, which looks like a customer until somebody uses it.
        raise RosterRefused(
            f"no customer named {name!r}. Onboard them in the Provider console "
            "first; this script only reserves accounts inside one."
        )
    return tenant


async def _reserve(session, tenant: Tenant, entry: dict[str, str]) -> str:
    email, role = entry["email"], Role(entry["role"])
    user = (
        (
            await session.execute(
                select(User).where(
                    User.tenant_id == tenant.id,
                    func.lower(User.email) == email.lower(),
                )
            )
        )
        .scalars()
        .first()
    )

    if user is None:
        user = User(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            role=role,
            email=email,
            full_name=entry["full_name"] or None,
            status=UserStatus.invited,
        )
        session.add(user)
        await session.flush()
        outcome = "created"
    else:
        if entry["full_name"]:
            user.full_name = entry["full_name"]
        if user.role != role:
            # Named rather than silent. A role change moves what this person
            # can do, and a tester whose role quietly differs from the plan
            # makes every later "is this a bug or a permission?" unanswerable.
            user.role = role
            outcome = "role changed and re-invited"
        else:
            outcome = "re-invited"
        if user.firebase_uid:
            return "already signed in; left alone"

    await session.execute(
        sa_update(StaffInvite)
        .where(
            StaffInvite.user_id == user.id,
            StaffInvite.accepted_at.is_(None),
            StaffInvite.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )
    token = generate_invite_token()
    session.add(
        StaffInvite(
            tenant_id=tenant.id,
            user_id=user.id,
            email=email,
            role=role.value,
            token_hash=hash_invite_token(token),
            invited_by=None,
            expires_at=invite_expiry(),
        )
    )
    await session.flush()
    dispatch(
        "pickready.send_email",
        args=[
            str(tenant.id),
            email,
            "client_invite",
            {
                "tenant_name": tenant.name,
                "invite_link": build_invite_link(get_settings().frontend_url, token),
            },
        ],
    )
    return outcome


async def main() -> int:
    try:
        entries = _roster()
        tenant_name = os.environ.get(TENANT_ENV, "").strip()
        if not tenant_name:
            raise RosterRefused(f"{TENANT_ENV} is not set")
    except RosterRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    tenant = await _tenant(session, tenant_name)
                    for entry in entries:
                        outcome = await _reserve(session, tenant, entry)
                        # The address, the role and what happened. Never the
                        # token and never a join link.
                        print(
                            f"testers.reserved tenant={tenant.name!r} "
                            f"email={entry['email']} role={entry['role']} "
                            f"outcome={outcome!r}"
                        )
    except RosterRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()

    print(
        "Each person opens the link in their invitation, signs in with Google "
        "or with an email and password they set in Firebase, and the account "
        "binds on that first sign-in. Vivekium stores no password."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
