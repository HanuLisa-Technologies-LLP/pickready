"""The two-stage, per-item consent catalogue (vivekium feature 6).

THE ITEMS ARE DATA, AND THE SERVER IS THEIR ONLY AUTHOR. Seven items: the
brief's six, plus feature 4's statutory-identifier tick (PAN / PF / ESI:
consent only, NO numbers collected, NO numbers stored). Each is recorded
individually with its own timestamp in `candidate_consents` (migration 0101),
which is the ONE table behind the brief's "three destinations": the candidate
record (portal), the BGV record (the recruiter's BGV response) and the
candidate page in the Executive Profile all READ this table, so one write is
all three destinations by construction rather than by three writes that could
disagree.

Stage A is asked at registration; Stage B during the assessment interaction,
recorded in the same transaction as the assessment-mode consent
(`assessment_consent.record_consent`), which is what makes the brief's "one
operation" literally true.

THE WORDING RULES, both enforced by `tests/test_platform_audit.py`: no em
dash anywhere, and never the forbidden relationship terms; the sanctioned
phrase is "employer clients registered on the platform". The brief's own
templates carry both violations and are reproduced here for their SUBSTANCE,
re-set to comply.

A RE-AFFIRMATION MOVES THE TIMESTAMP. UNIQUE (candidate, item): the row
answers "does this consent stand, since when", which is the question the
renewal cycle (feature 8) and an erasure receipt both ask. It is not a
history table; the audit log is where the history of acts lives.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

STAGE_REGISTRATION = "A"
STAGE_ASSESSMENT = "B"

#: How a consent arrived, recorded so a dispute can say which screen asked.
SOURCE_REGISTRATION = "registration"
SOURCE_ASSESSMENT = "assessment"
SOURCE_PORTAL = "portal"


@dataclass(frozen=True)
class ConsentItem:
    key: str
    stage: str
    text: str


#: The catalogue. Keys are permanent identifiers: they land in rows, so
#: renaming one orphans every consent already given under it.
CONSENT_ITEMS: tuple[ConsentItem, ...] = (
    ConsentItem(
        key="profile_retention",
        stage=STAGE_REGISTRATION,
        text=(
            "My profile and verified information will be retained and may be "
            "considered by employer clients registered on the platform who "
            "post openings."
        ),
    ),
    ConsentItem(
        key="media_and_records_access",
        stage=STAGE_REGISTRATION,
        text=(
            "My photos, assessment videos and background verification records "
            "will be stored and may be accessed by employer clients "
            "registered on the platform for current and future hiring "
            "decisions."
        ),
    ),
    ConsentItem(
        key="job_scoped_assessment_data",
        stage=STAGE_ASSESSMENT,
        text=(
            "The assessment report for this job, including my scores and "
            "interview transcript, is tied to this job only. When the "
            "employer closes the position it is automatically and "
            "permanently removed."
        ),
    ),
    ConsentItem(
        key="bgv_portability",
        stage=STAGE_ASSESSMENT,
        text=(
            "My background verification records are retained independently "
            "of any job. Only my last two employers are kept; updating my "
            "employment history automatically replaces the oldest record."
        ),
    ),
    ConsentItem(
        key="accuracy_declaration",
        stage=STAGE_ASSESSMENT,
        text="All information I have provided is accurate and complete.",
    ),
    ConsentItem(
        key="hr_contact_dpdp",
        stage=STAGE_ASSESSMENT,
        text=(
            "I consent to the platform contacting my previous employers' HR "
            "teams for employment verification under India's Digital "
            "Personal Data Protection Act, 2023."
        ),
    ),
    # Feature 4's statutory tick. CONSENT ONLY: the platform collects no PAN,
    # PF or ESI number, and stores none. The checkbox is the whole record.
    ConsentItem(
        key="statutory_identifier_verification",
        stage=STAGE_ASSESSMENT,
        text=(
            "I consent to verification against my statutory employment "
            "records (PAN, PF, ESI). No identification numbers are collected "
            "or stored by the platform."
        ),
    ),
)

ITEMS_BY_KEY: dict[str, ConsentItem] = {item.key: item for item in CONSENT_ITEMS}

STAGE_A_KEYS: tuple[str, ...] = tuple(
    item.key for item in CONSENT_ITEMS if item.stage == STAGE_REGISTRATION
)
STAGE_B_KEYS: tuple[str, ...] = tuple(
    item.key for item in CONSENT_ITEMS if item.stage == STAGE_ASSESSMENT
)


def catalogue_payload() -> list[dict[str, str]]:
    """The items as the API serves them, stage and wording included, so no
    screen ever authors consent copy of its own."""
    return [
        {"key": item.key, "stage": item.stage, "text": item.text}
        for item in CONSENT_ITEMS
    ]


async def record_items(
    session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    keys: list[str] | tuple[str, ...],
    source: str,
) -> int:
    """Record (or re-affirm) each named item, individually timestamped.

    An unknown key RAISES rather than writing an unreadable row: the caller
    is code, not a candidate, so a bad key is a bug and silence would bury
    it. Returns how many rows were written or refreshed.
    """
    now = datetime.now(timezone.utc)
    written = 0
    for key in keys:
        if key not in ITEMS_BY_KEY:
            raise ValueError(f"unknown consent item key: {key!r}")
        await session.execute(
            text(
                "INSERT INTO candidate_consents "
                "(id, candidate_id, item_key, stage, source, consented_at) "
                "VALUES (:id, :cid, :key, :stage, :source, :now) "
                "ON CONFLICT (candidate_id, item_key) "
                "DO UPDATE SET consented_at = :now, source = :source"
            ),
            {
                "id": str(uuid.uuid4()),
                "cid": str(candidate_id),
                "key": key,
                "stage": ITEMS_BY_KEY[key].stage,
                "source": source,
                "now": now,
            },
        )
        written += 1
    return written


async def items_for(
    session: AsyncSession, candidate_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Every catalogue item with its consent stamp, or None when never given.

    ALWAYS the full catalogue, present or not, the compliance-slots pattern:
    an absent consent must render as "not given" rather than hide in a short
    list.
    """
    rows = {
        row[0]: row[1]
        for row in (
            await session.execute(
                text(
                    "SELECT item_key, consented_at FROM candidate_consents "
                    "WHERE candidate_id = :cid"
                ),
                {"cid": str(candidate_id)},
            )
        ).all()
    }
    return [
        {
            "key": item.key,
            "stage": item.stage,
            "text": item.text,
            "consented_at": rows.get(item.key),
        }
        for item in CONSENT_ITEMS
    ]
