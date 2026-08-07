"""One-time, resumable Cloudinary-to-private-GCS resume migration.

Usage: ``python -m app.scripts.migrate_resumes_to_gcs --apply``.
Without ``--apply`` it only reports the number of eligible profiles.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.db import get_session_factory, superadmin_scope
from app.models.candidate import Profile
from app.services.resume_storage import (
    apply_resume_asset,
    fetch_resume_bytes,
    store_resume_bytes,
)


async def migrate(*, apply: bool) -> tuple[int, int]:
    factory = get_session_factory()
    async with factory() as session:
        async with superadmin_scope(session):
            profiles = (
                await session.execute(
                    select(Profile).where(
                        Profile.resume_url.is_not(None),
                        Profile.resume_storage_provider == "cloudinary",
                    )
                )
            ).scalars().all()
    if not apply:
        print(f"eligible={len(profiles)} migrated=0 dry_run=true")
        return len(profiles), 0

    migrated = 0
    for detached in profiles:
        legacy_id = detached.resume_public_id
        data = await fetch_resume_bytes(detached)
        expected_sha = hashlib.sha256(data).hexdigest()
        asset = await store_resume_bytes(
            data,
            detached.resume_original_filename or f"{detached.id}.pdf",
            detached.resume_mime_type or "application/pdf",
        )
        if asset.sha256 != expected_sha:
            raise RuntimeError(f"checksum mismatch for profile {detached.id}")
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    profile = await session.get(Profile, detached.id)
                    if profile is None or profile.resume_storage_provider == "gcs":
                        continue
                    old_metadata = dict(profile.resume_metadata_json or {})
                    apply_resume_asset(profile, asset)
                    profile.resume_legacy_public_id = legacy_id
                    profile.resume_metadata_json = {
                        **asset.metadata,
                        "migration": {
                            "cloudinary_public_id": legacy_id,
                            "migrated_at": datetime.now(timezone.utc).isoformat(),
                            "legacy_metadata": old_metadata,
                        },
                    }
        migrated += 1
        print(f"migrated={migrated}/{len(profiles)} profile={detached.id}")
    return len(profiles), migrated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(migrate(apply=args.apply))


if __name__ == "__main__":
    main()
