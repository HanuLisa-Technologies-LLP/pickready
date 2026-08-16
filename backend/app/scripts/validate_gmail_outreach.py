"""Live AI-outreach + Celery + Gmail SMTP validation.

This is intentionally opt-in because it sends a real email. It never prints
credentials or the recipient address.

Usage:
    python -m app.scripts.validate_gmail_outreach --confirm-live-send
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.services.outreach_content import generate_outreach_email
from app.workers.celery_app import celery_app


async def _compose() -> dict:
    return await generate_outreach_email(
        candidate={
            "name": "ReadyPick Test Candidate",
            "skills_comment": (
                "Strong evidence across Python, FastAPI, PostgreSQL, Redis, "
                "Docker, and production API ownership."
            ),
            "experience_comment": (
                "Seven years of backend engineering with measurable delivery "
                "impact and technical leadership."
            ),
            "role_comment": (
                "The candidate's recent platform work closely matches the "
                "role's reliability and scale responsibilities."
            ),
            "education_comment": (
                "The stated computer science background meets the role's "
                "preferred foundation."
            ),
        },
        job={"title": "Senior Backend Engineer"},
        company={
            "name": "ReadyPick",
            "culture": (
                "Clear ownership, thoughtful collaboration, candid feedback, "
                "and reliable delivery without unnecessary process."
            ),
        },
        kind="next_round",
    )


async def _latest_delivery(started_at: datetime, recipient: str) -> dict | None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT metadata_json, target_id FROM audit_log "
                        "WHERE action = 'email.delivery' "
                        "AND at >= :started_at "
                        "AND metadata_json->>'to' = :recipient "
                        "ORDER BY at DESC LIMIT 1"
                    ),
                    {"started_at": started_at, "recipient": recipient},
                )
            ).first()
            if row is None:
                return None
            metadata = dict(row.metadata_json or {})
            return {
                "status": metadata.get("status"),
                "template": metadata.get("template"),
                "sender_path": metadata.get("sender_path"),
                "message_id_recorded": bool(row.target_id),
            }
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--confirm-live-send",
        action="store_true",
        help="Required acknowledgement that this sends a real Gmail message.",
    )
    args = parser.parse_args()
    if not args.confirm_live_send:
        parser.error("--confirm-live-send is required")

    settings = get_settings()
    recipient = settings.smtp_user
    if not recipient or not settings.smtp_password:
        raise SystemExit("Gmail SMTP credentials are not configured.")

    content = asyncio.run(_compose())
    body = content["text"].split("\n\nContinue to the next round:", 1)[0]
    started_at = datetime.now(timezone.utc)
    task = celery_app.send_task(
        "pickready.send_email",
        args=[
            None,
            recipient,
            "outreach_direct",
            {"subject": content["subject"], "body": content["text"]},
        ],
    )
    task.get(timeout=120)
    audit = asyncio.run(_latest_delivery(started_at, recipient))

    result = {
        "smtp": {
            "host": settings.smtp_host,
            "port": settings.smtp_port,
            "starttls": settings.smtp_starttls,
            "ssl": settings.smtp_ssl,
            "sender_matches_authenticated_user": (
                settings.smtp_from_email == settings.smtp_user
            ),
        },
        "ai": {
            "subject_generated": bool(content["subject"].strip()),
            "body_words": len(body.split()),
            "within_required_range": 150 <= len(body.split()) <= 200,
        },
        "celery_task": {"id": task.id, "state": task.state},
        "audit": audit,
    }
    print(json.dumps(result, indent=2))
    return 0 if audit and audit.get("status") == "sent" else 1


if __name__ == "__main__":
    raise SystemExit(main())
