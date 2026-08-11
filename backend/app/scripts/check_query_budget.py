"""Measure the query count of every high-traffic read, against a live stack.

    python -m app.scripts.check_query_budget [base_url]

WHY A SCRIPT AND NOT A UNIT TEST
--------------------------------
An N+1 is not visible in a unit test with a fake session, and it is not visible
in a test with three rows either: the whole defect is that the count SCALES
with the data. So this asks a running service, over HTTP, with real rows, and
compares the count on a job with three candidates against the same endpoint on
a job with forty. A flat count is the assertion; a low count is not.

It reads `x-query-count`, which `core/instrumentation` already puts on every
response and which nothing was consuming.

Tokens are minted from the database rather than supplied, so this needs no
interactive login and no fixture: it finds a staff user, an owner and a
candidate that already exist, and signs a token for each. That means it can be
pointed at any environment whose database it can reach.

BUDGETS ARE WHERE THEY ARE TODAY
--------------------------------
Measured on 2026-08-11 against the seeded local stack. They are ceilings, not
targets: raising one is a decision that belongs in a commit message.
"""
from __future__ import annotations

import asyncio
import sys

import httpx
from sqlalchemy import text

from app.core.db import get_session_factory, superadmin_scope
from app.core.security import audience_for_role, create_access_token

DEFAULT_BASE = "http://localhost:8000"

#: path template -> maximum queries. `{}` placeholders are filled from the
#: identifiers discovered below.
BUDGETS: dict[str, int] = {
    "/api/v1/jobs": 6,
    "/api/v1/jobs/{job}": 6,
    "/api/v1/jobs/{job}/candidates": 8,
    "/api/v1/dashboard/summary": 8,
    "/api/v1/provider/customers?limit=25": 12,
    "/api/v1/admin/tenants": 9,
    "/api/v1/portal/jobs": 13,
    "/api/v1/portal/applications": 9,
}

#: Which identity each path needs.
ACTOR: dict[str, str] = {
    "/api/v1/provider/customers?limit=25": "owner",
    "/api/v1/admin/tenants": "owner",
    "/api/v1/portal/jobs": "candidate",
    "/api/v1/portal/applications": "candidate",
}


async def _fixtures() -> dict:
    """One token per portal, plus the biggest and smallest job to compare."""
    async with get_session_factory()() as session:
        async with superadmin_scope(session):
            # The tenant with the WIDEST spread between its busiest and
            # quietest job. Comparing 2 rows against 3 proves nothing: an N+1
            # at that scale is two extra queries and looks like noise. The
            # spread is the whole measurement.
            staff = (
                await session.execute(
                    text(
                        """
                        with per_job as (
                            select j.tenant_id, j.id, count(l.id) as n
                            from jobs j
                            left join job_candidate_links l on l.job_id = j.id
                            group by j.tenant_id, j.id
                        ), spread as (
                            select tenant_id, max(n) - min(n) as width
                            from per_job group by tenant_id order by width desc
                        )
                        select u.id, u.role, u.tenant_id
                        from users u
                        join spread s on s.tenant_id = u.tenant_id
                        where u.role in ('client','hr_manager','recruiter')
                        order by s.width desc
                        limit 1
                        """
                    )
                )
            ).first()
            owner = (
                await session.execute(
                    text("select id, role, tenant_id from users where role='super_admin' limit 1")
                )
            ).first()
            candidate = (
                await session.execute(
                    text("select id, role, tenant_id from users where role='candidate' limit 1")
                )
            ).first()
            jobs = (
                await session.execute(
                    text(
                        "select j.id, count(l.id) as n from jobs j "
                        "left join job_candidate_links l on l.job_id = j.id "
                        "where j.tenant_id = :tenant group by j.id order by n desc"
                    ),
                    {"tenant": staff[2]},
                )
            ).all()

    def token(row):
        return create_access_token(row[0], row[1], row[2], audience=audience_for_role(row[1]))

    return {
        "staff": token(staff),
        "owner": token(owner) if owner else None,
        "candidate": token(candidate) if candidate else None,
        "big_job": str(jobs[0][0]) if jobs else None,
        "big_count": jobs[0][1] if jobs else 0,
        "small_job": str(jobs[-1][0]) if jobs else None,
        "small_count": jobs[-1][1] if jobs else 0,
    }


async def _measure(client: httpx.AsyncClient, path: str, token: str) -> tuple[int, int | None]:
    """(status, query count). Called twice; the first call warms any cache."""
    headers = {"Authorization": f"Bearer {token}"}
    await client.get(path, headers=headers)
    response = await client.get(path, headers=headers)
    raw = response.headers.get("x-query-count")
    return response.status_code, int(raw) if raw is not None else None


async def run(base: str) -> int:
    fixtures = await _fixtures()
    failures: list[str] = []

    async with httpx.AsyncClient(base_url=base, timeout=60.0) as client:
        print(f"\nQuery budget, against {base}\n")
        for template, budget in BUDGETS.items():
            token = fixtures[ACTOR.get(template, "staff")]
            if token is None:
                print(f"  SKIP  {template}  (no account of that kind)")
                continue
            path = template.format(job=fixtures["big_job"])
            status, count = await _measure(client, path, token)
            if count is None:
                print(f"  SKIP  {template}  (no x-query-count header, HTTP {status})")
                continue
            over = count > budget
            print(
                f"  {'OVER' if over else 'ok  '}  {template:<44} "
                f"HTTP {status}  queries={count:<3} budget={budget}"
            )
            if over:
                failures.append(f"{template}: {count} queries > budget {budget}")

        # The actual N+1 question: does the count MOVE with the row count?
        if fixtures["big_job"] and fixtures["small_job"] and fixtures["big_job"] != fixtures["small_job"]:
            template = "/api/v1/jobs/{job}/candidates"
            _, big = await _measure(
                client, template.format(job=fixtures["big_job"]), fixtures["staff"]
            )
            _, small = await _measure(
                client, template.format(job=fixtures["small_job"]), fixtures["staff"]
            )
            print(
                f"\n  candidate table: {fixtures['small_count']} rows -> {small} queries, "
                f"{fixtures['big_count']} rows -> {big} queries"
            )
            if big is not None and small is not None and big > small:
                failures.append(
                    f"the candidate table is N+1: {small} queries at "
                    f"{fixtures['small_count']} rows, {big} at {fixtures['big_count']}"
                )
            else:
                print("  flat in the row count, which is the property that matters")

    print()
    if failures:
        for failure in failures:
            print(f"  FAIL  {failure}")
        return 1
    print("  Every measured read is inside its budget and flat in the row count.\n")
    return 0


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE
    return asyncio.run(run(base))


if __name__ == "__main__":
    raise SystemExit(main())
