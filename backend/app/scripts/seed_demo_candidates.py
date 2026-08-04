"""Seed the 30 permanent demonstration candidates from the shipped resume corpus.

    python -m app.scripts.seed_demo_candidates [--dry-run]

WHY THIS EXISTS
---------------
`seed_resumes.seed_resume_corpus` builds all thirty demo candidates from
`Resume_01..Resume_30`, but it had no entry point of its own: it was only ever
called from `seed_dev_data`, which does a great deal more than seed candidates
and is not something to point at a production database.

Measured on production 2026-08-04, with RLS bypassed so the count is real:
two candidates, against the thirty every demo assumes. The corpus never reached
the image, because it lived outside the backend build context (see
`seed_resumes.resumes_dir`). This script plus that move is the whole fix.

IDEMPOTENT, AND THAT IS LOAD-BEARING
------------------------------------
`seed_resume_corpus` skips any candidate whose email already exists and passes
`overwrite=False` to Cloudinary, so running this twice adds nothing and
re-uploads nothing. It is safe to run on every deploy if anyone ever wants to,
and safe to re-run after a partial failure -- which matters, because thirty
uploads over a network is exactly the kind of thing that fails halfway.

IT ONLY EVER ADDS
-----------------
There is no delete, no expiry and no reconciliation here. The demo candidates
are permanent fixtures, and a script that could remove them is a script that
could remove them by accident. If a candidate is missing it is created; nothing
else is touched.

The candidates are DATABANK rows, shared rather than owned by one tenant, which
is what lets all three demo companies see them. `source_tenant_id` records which
tenant introduced the profile; Sarkar Corp is used because it is the first
demonstration tenant and the value is provenance, not access control.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from app.core.db import superadmin_scope
from app.scripts.seed_resumes import resumes_dir, seed_resume_corpus

#: Sarkar Corp. Provenance only: a databank profile is visible to every tenant
#: regardless of which one introduced it.
SOURCE_TENANT_ID = uuid.UUID("10000000-0000-4000-8000-000000000001")


async def _run(dry_run: bool) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    directory = resumes_dir()
    if directory is None:
        # Loud, and non-zero. A silent "found nothing" is precisely how
        # production ended up with two candidates while every deploy reported
        # success.
        print(
            "ERROR: resume corpus not found. Expected it at "
            "backend/demo_resumes (shipped in the image), /resumes, or "
            "$SEED_RESUMES_DIR.",
            file=sys.stderr,
        )
        return 1
    print(f"  = resume corpus at {directory}")

    files = sorted(p.name for p in directory.glob("*.docx"))
    print(f"  = {len(files)} resume file(s) found")
    if dry_run:
        for name in files:
            print(f"    - would seed {name}")
        return 0

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                # The candidates are databank rows with no owning tenant, so the
                # write has to happen outside any single tenant's RLS scope.
                async with superadmin_scope(session):
                    # allow_production: these thirty are permanent
                    # demonstration fixtures and are SUPPOSED to exist in
                    # production. `seed_resume_corpus` refuses production by
                    # default to protect `seed_dev_data`, which seeds an entire
                    # development world and must never be aimed at a real
                    # database. That default is left alone; this caller is the
                    # deliberate exception, which is why the opt-in is explicit
                    # and lives here rather than in the shared function.
                    created = await seed_resume_corpus(
                        session, SOURCE_TENANT_ID, allow_production=True
                    )
        print(f"  = done: {created} candidate(s) created")
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be seeded without writing or uploading anything.",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
