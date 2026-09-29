"""Remove disposable test COMPANY accounts from a test environment.

    python -m app.scripts.cleanup_test_accounts \\
        --confirm-environment pilot \\
        --keep-email saravankumar2503@gmail.com --keep-email manjuchro@gmail.com
    ... add --apply to delete what the dry run counted.

Owner spec 2026-09-29, section 35: keep the explicit test identities needed
for manual validation and clean the other disposable customer accounts, with
an allowlist, from a development or test environment. NOT a migration, and
nothing runs it automatically: an operator types it, reads the counts, and
only then adds `--apply`.

WHAT IT TOUCHES
---------------
COMPANY accounts only: a tenant (with everything that cascades from it, by
`services/tenant_deletion`, the one implementation the Provider's delete also
uses) is removed when NONE of its users' addresses is on the keep list, and a
company registration (`company_registrations`) is removed when its address is
not on the keep list. Platform staff (the Provider owner, business
development) and candidates have no tenant and are never touched; a
candidate's data leaves only through Delete My Profile and the erasure path.
Firebase identities are not touched either: they live in Firebase.

WHAT IT REFUSES
---------------
* Without `--confirm-environment` naming THIS deployment's `ENVIRONMENT`, it
  does nothing at all, dry run included: the name is the operator saying which
  database they believe they are pointed at.
* Without at least one `--keep-email`: an empty allowlist would mean "every
  company", which is never what a cleanup of TEST accounts means.
* A demonstration tenant (`tenants.is_demo`) is never removed unless it is
  named with `--include-demo-tenant "<exact name>"`.

It prints COUNTS only: no company name, no address, nothing a terminal log
should keep. Exit 0 on a completed dry run or apply, 2 on a refusal.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.core.db import get_session_factory, superadmin_scope
from app.models.company_registration import CompanyRegistration
from app.models.tenant import Tenant
from app.models.user import User
from app.services import tenant_deletion

__all__ = ["Plan", "main", "plan", "run"]


@dataclass
class Plan:
    """What a run would remove. Ids are carried, never printed."""

    tenant_ids: list[uuid.UUID] = field(default_factory=list)
    tenants_kept_by_email: int = 0
    demo_tenants_skipped: int = 0
    users_in_removed_tenants: int = 0
    registration_ids: list[uuid.UUID] = field(default_factory=list)

    def lines(self) -> list[str]:
        return [
            f"tenants_to_remove={len(self.tenant_ids)}",
            f"tenants_kept_by_email={self.tenants_kept_by_email}",
            f"demo_tenants_skipped={self.demo_tenants_skipped}",
            f"users_in_removed_tenants={self.users_in_removed_tenants}",
            f"registrations_to_remove={len(self.registration_ids)}",
        ]


def _normal(values) -> set[str]:
    return {value.strip().lower() for value in values if value and value.strip()}


async def plan(
    session,
    *,
    keep_emails: set[str],
    include_demo_tenants: set[str],
    within: set[uuid.UUID] | None = None,
) -> Plan:
    """Decide what goes. `within` narrows the tenants and registrations
    considered to a known set; the command line never passes it (the test
    suite does, so a test cannot remove another test's rows)."""
    out = Plan()
    tenants = (await session.execute(select(Tenant).order_by(Tenant.created_at))).scalars().all()
    for tenant in tenants:
        if within is not None and tenant.id not in within:
            continue
        emails = _normal(
            (await session.execute(
                select(User.email).where(User.tenant_id == tenant.id)
            )).scalars().all()
        )
        if emails & keep_emails:
            out.tenants_kept_by_email += 1
            continue
        if tenant.is_demo and tenant.name.strip().lower() not in include_demo_tenants:
            out.demo_tenants_skipped += 1
            continue
        out.tenant_ids.append(tenant.id)
        out.users_in_removed_tenants += (
            await session.execute(
                select(func.count()).select_from(User).where(User.tenant_id == tenant.id)
            )
        ).scalar_one()
    registrations = (await session.execute(select(CompanyRegistration))).scalars().all()
    for registration in registrations:
        if within is not None and registration.id not in within:
            continue
        if registration.email.strip().lower() in keep_emails:
            continue
        if registration.tenant_id is not None and registration.tenant_id not in out.tenant_ids:
            # Its company is being kept (a kept colleague, or a demo tenant).
            continue
        out.registration_ids.append(registration.id)
    return out


async def run(
    *,
    environment: str | None,
    keep_emails: list[str],
    include_demo_tenants: list[str],
    apply: bool,
    within: set[uuid.UUID] | None = None,
) -> tuple[int, list[str]]:
    settings = get_settings()
    if not environment or environment.strip().lower() != (settings.environment or "").strip().lower():
        return 2, [
            "REFUSED: pass --confirm-environment with this deployment's ENVIRONMENT "
            "name. Nothing was read or written."
        ]
    keep = _normal(keep_emails)
    if not keep:
        return 2, [
            "REFUSED: name at least one --keep-email. An empty allowlist would "
            "remove every company. Nothing was read or written."
        ]
    demo = _normal(include_demo_tenants)
    factory = get_session_factory()
    async with factory() as session, session.begin():
        async with superadmin_scope(session):
            decided = await plan(
                session, keep_emails=keep, include_demo_tenants=demo, within=within
            )
    lines = decided.lines()
    if not apply:
        lines.append("DRY RUN: nothing was written. Add --apply to remove what is counted above.")
        return 0, lines

    removed_tenants = 0
    # One transaction per tenant, re-entering the transaction-local bypass
    # scope each time, so one failure does not undo the others.
    for tenant_id in decided.tenant_ids:
        async with factory() as session, session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "UPDATE company_registrations SET status = 'cancelled' "
                        "WHERE tenant_id = :t AND status IN "
                        "('email_verification_pending', 'email_verified')"
                    ),
                    {"t": str(tenant_id)},
                )
                tenant = await session.get(Tenant, tenant_id)
                if tenant is None:
                    continue
                await tenant_deletion.delete_tenant(
                    session, tenant, actor_user_id=None,
                    metadata={"via": "cleanup_test_accounts"},
                )
                removed_tenants += 1
    removed_registrations = 0
    if decided.registration_ids:
        async with factory() as session, session.begin():
            async with superadmin_scope(session):
                result = await session.execute(
                    text("DELETE FROM company_registrations WHERE id = ANY(:ids)"),
                    {"ids": decided.registration_ids},
                )
                removed_registrations = result.rowcount or 0
    lines.append(f"tenants_removed={removed_tenants}")
    lines.append(f"registrations_removed={removed_registrations}")
    return 0, lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Remove disposable test company accounts. Dry run unless --apply; "
            "refuses without --confirm-environment and --keep-email."
        )
    )
    parser.add_argument("--confirm-environment", help="This deployment's ENVIRONMENT name.")
    parser.add_argument(
        "--keep-email", action="append", default=[],
        help="An address whose company (and registration) is kept. Repeatable.",
    )
    parser.add_argument(
        "--include-demo-tenant", action="append", default=[],
        help="The exact name of a demonstration tenant to remove too. Repeatable.",
    )
    parser.add_argument("--apply", action="store_true", help="Delete what the dry run counts.")
    args = parser.parse_args()
    status, lines = asyncio.run(
        run(
            environment=args.confirm_environment,
            keep_emails=args.keep_email,
            include_demo_tenants=args.include_demo_tenant,
            apply=args.apply,
        )
    )
    stream = sys.stderr if status == 2 else sys.stdout
    for line in lines:
        print(line, file=stream)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
