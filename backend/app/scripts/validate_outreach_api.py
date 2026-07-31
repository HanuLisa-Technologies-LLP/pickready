"""Live API-level validation for selection → AI preview → Gmail delivery.

This creates/reuses a local validation candidate whose address is the configured
Gmail sender, links it to a mock-company job, and exercises the same endpoints
as the Review Screen. Credentials and recipient addresses are never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.db import get_session_factory, superadmin_scope
from app.core.security import AUDIENCE_ORG, create_access_token
from app.models import Candidate, Job, JobCandidateLink, Profile, Tenant, User
from app.models.enums import LinkSource, Role


async def _fixture() -> tuple[str, str, str, str]:
    settings = get_settings()
    async with get_session_factory()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                tenant = (
                    await session.execute(
                        select(Tenant).where(Tenant.domain == "sarkar-corp.local")
                    )
                ).scalar_one()
                recruiter = (
                    await session.execute(
                        select(User).where(
                            User.tenant_id == tenant.id,
                            User.role == Role.recruiter,
                        )
                    )
                ).scalars().first()
                job = (
                    await session.execute(
                        select(Job)
                        .where(Job.tenant_id == tenant.id, Job.ratified_at.isnot(None))
                        .order_by(Job.created_at.desc())
                    )
                ).scalars().first()
                candidate = (
                    await session.execute(
                        select(Candidate).where(
                            Candidate.email == settings.smtp_user
                        )
                    )
                ).scalars().first()
                if candidate is None:
                    candidate = Candidate(
                        full_name="PickReady Delivery Validation",
                        email=settings.smtp_user,
                        consent_databank=False,
                    )
                    session.add(candidate)
                    await session.flush()
                profile = (
                    await session.execute(
                        select(Profile)
                        .where(Profile.candidate_id == candidate.id)
                        .order_by(Profile.created_at.desc())
                    )
                ).scalars().first()
                if profile is None:
                    profile = Profile(candidate_id=candidate.id)
                    session.add(profile)
                    await session.flush()
                link = (
                    await session.execute(
                        select(JobCandidateLink).where(
                            JobCandidateLink.job_id == job.id,
                            JobCandidateLink.candidate_id == candidate.id,
                        )
                    )
                ).scalar_one_or_none()
                if link is None:
                    link = JobCandidateLink(
                        tenant_id=tenant.id,
                        job_id=job.id,
                        candidate_id=candidate.id,
                        profile_id=profile.id,
                        source=LinkSource.fresh,
                    )
                    session.add(link)
                    await session.flush()
                return (
                    str(recruiter.id),
                    str(tenant.id),
                    str(job.id),
                    str(link.id),
                )


async def _latest_audit(started_at: datetime) -> dict | None:
    settings = get_settings()
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                text(
                    "SELECT metadata_json, target_id FROM audit_log "
                    "WHERE action='email.delivery' AND at >= :started "
                    "AND metadata_json->>'to'=:recipient "
                    "ORDER BY at DESC LIMIT 1"
                ),
                {"started": started_at, "recipient": settings.smtp_user},
            )
        ).first()
        if row is None:
            return None
        return {
            "status": row.metadata_json.get("status"),
            "message_id_recorded": bool(row.target_id),
        }


async def run() -> dict:
    user_id, tenant_id, job_id, link_id = await _fixture()
    token = create_access_token(
        user_id, Role.recruiter.value, tenant_id, audience=AUDIENCE_ORG
    )
    headers = {"Authorization": f"Bearer {token}"}
    started_at = datetime.now(timezone.utc)
    async with httpx.AsyncClient(
        base_url="http://localhost:8000/api/v1", headers=headers, timeout=30
    ) as client:
        preview = await client.post(
            "/outreach/preview",
            json={"job_id": job_id, "link_ids": [link_id], "mode": "ai"},
        )
        preview.raise_for_status()
        preview_data = preview.json()
        send = await client.post(
            "/outreach/send-email",
            json={
                "job_id": job_id,
                "link_ids": [link_id],
                "mode": "ai",
                "overrides": [
                    {
                        "link_id": item["link_id"],
                        "subject": item["subject"],
                        "body": item["body"],
                    }
                    for item in preview_data["recipients"]
                ],
            },
        )
        send.raise_for_status()
        send_data = send.json()
        delivery = None
        for _ in range(80):
            status = await client.post(
                "/outreach/status", json={"task_ids": send_data["task_ids"]}
            )
            status.raise_for_status()
            delivery = status.json()
            if delivery["done"]:
                break
            await asyncio.sleep(1.5)

    audit = await _latest_audit(started_at)
    return {
        "selection_count": 1,
        "preview_status": preview.status_code,
        "preview_recipient_count": len(preview_data["recipients"]),
        "send_status": send.status_code,
        "queued": send_data["queued"],
        "delivery": delivery,
        "audit": audit,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live-send", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live_send:
        parser.error("--confirm-live-send is required")
    result = asyncio.run(run())
    print(json.dumps(result, indent=2))
    delivery = result.get("delivery") or {}
    return 0 if delivery.get("sent") == 1 and result.get("audit") else 1


if __name__ == "__main__":
    raise SystemExit(main())
