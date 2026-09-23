"""The two-stage, per-item consent catalogue (vivekium feature 6).

THE ITEMS ARE DATA, AND THE SERVER IS THEIR ONLY AUTHOR. Eight items: the
brief's six, feature 4's statutory-identifier tick (PAN / PF / ESI: consent
only, NO numbers collected, NO numbers stored), and change request 23's
optional cross-employer evidence reuse. Each is recorded
individually with its own timestamp in `candidate_consents` (migration 0101),
which is the ONE table behind the brief's "three destinations": the candidate
record (portal), the BGV record (the recruiter's BGV response) and the
candidate page in the Executive Profile all READ this table, so one write is
all three destinations by construction rather than by three writes that could
disagree.

A NEW PURPOSE GETS A NEW ITEM, NEVER A WIDER READING OF AN OLD ONE. That is
what the catalogue is for: `profile_retention` says a profile "may be
CONSIDERED by employer clients registered on the platform", and evidence
gathered while hiring for one employer being reused to GRADE the candidate
for another is a different act that sentence does not describe. An item may
also be OPTIONAL (`required=False`): the stage says when it is asked, and
`required` says what declining costs. `STAGE_A_REQUIRED_KEYS` is what the
profile gate refuses over, so an optional consent can never become a
condition of having a profile.

Stage A is asked at registration; Stage B during the assessment interaction,
recorded in the same transaction as the assessment-mode consent
(`assessment_consent.record_consent`), which is what makes the brief's "one
operation" literally true.

THE WORDING RULES, both enforced by `tests/test_platform_audit.py`: no em
dash anywhere, and never the forbidden relationship terms; the sanctioned
phrase is "employer clients registered on the platform". The brief's own
templates carry both violations and are reproduced here for their SUBSTANCE,
re-set to comply.

A RE-AFFIRMATION MOVES THE TIMESTAMP. UNIQUE (candidate, item): the standing
row answers "does this consent stand, since when", which is the question the
renewal cycle (feature 8) and an erasure receipt both ask.

AND EVERY ACT IS ALSO APPENDED, which is migration 0117 and supersedes 0101's
"the audit log is where the history of acts lives". It cannot be: `audit_log`
rows deliberately SURVIVE `erasure.cascade_erasure` (no foreign key, on
purpose), while feature 7 deletes a candidate's consent records WITH their
profile. Two opposite retention rules cannot share one table, so
`candidate_consent_events` carries the history under the cascade the subject
is owed. Read 0117's docstring before changing any of this.

THE WORDING IS VERSIONED, AND A VERSION IS HALF A CHANGE. Editing an item's
`text` without bumping its `version` silently re-describes every consent
already given under the old words; `tests/test_consent_catalog.py` pins each
(key, version) to the digest of its text and fails on exactly that edit. Every
act stores the version, the digest AND the verbatim sentence, because a digest
proves a stored sentence was not altered and cannot reconstruct one that no
longer exists in the source.
"""
from __future__ import annotations

import hashlib
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
    """One tickable consent, and the version of the words it was ticked under.

    `version` starts at 1 and is bumped by hand whenever `text` changes, the
    same discipline `app/prompts` follows for its `# version:` header. It is
    not derived from the digest: a version is a decision that the new wording
    is a NEW thing to consent to, and deriving it from the bytes would bump it
    for a corrected comma as readily as for a changed meaning.
    """

    key: str
    stage: str
    text: str
    version: int = 1
    #: Whether refusing it stops the candidate. The STAGE says when an item is
    #: asked; this says what declining costs. The brief's own items are all
    #: mandatory; an item covering a purpose the candidate can live without
    #: must not be able to hold their profile hostage, and an optional item
    #: that blocked would be a mandatory item with softer wording.
    required: bool = True


def text_digest(item: ConsentItem) -> str:
    """The digest stamped beside a consent, so a later edit is detectable.

    Over the text alone: the key and the stage identify the slot, the text is
    the thing consented to, and mixing them would change the digest of words
    that did not change the day an item moved stage.
    """
    return hashlib.sha256(item.text.encode("utf-8")).hexdigest()


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
    # Change request 23 (portable evidence). NOT a widening of
    # `profile_retention`, which says a profile "may be considered by employer
    # clients registered on the platform": being CONSIDERED by an employer is
    # not the same act as evidence gathered while hiring for one employer
    # being reused to GRADE the candidate for another. Stretching an existing
    # sentence over a new purpose is precisely the failure a per-item
    # catalogue exists to prevent, so the new purpose gets its own item, its
    # own tick and its own timestamp.
    #
    # STAGE A, because the evidence is the candidate's own and spans their
    # whole account rather than any one assessment, which is the same family
    # the other two registration items belong to. OPTIONAL, because a
    # candidate who declines simply has their criteria established from what
    # they submit for that job; refusing it costs them convenience, not
    # access, and a mandatory version would be consent extracted rather than
    # given.
    #
    # THE OPERATIONAL SWITCH IS ELSEWHERE AND STAYS THERE. The reuse path
    # asks `retention_consent.reuse_across_jobs_allowed`, which reads
    # `candidates.retain_assessment_consent` and refuses on NULL. This row is
    # the record of WHICH PURPOSE was agreed to and under what words; that
    # flag is what the code checks. They must be reconciled by whoever owns
    # the flag, and until they are the standing behaviour is the safe one:
    # absent consent refuses on both sides.
    ConsentItem(
        key="cross_employer_evidence_reuse",
        stage=STAGE_REGISTRATION,
        required=False,
        text=(
            "My verified career history, education, employment confirmations "
            "and the core skills drawn from my resume may be reused to "
            "establish the criteria for later roles with other employer "
            "clients registered on the platform. If I do not agree, each "
            "role is assessed only on what I submit for it."
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

#: The registration items that BLOCK. `STAGE_A_KEYS` is what a screen shows;
#: this is what the profile gate refuses over, and the two are different sets
#: the moment an optional item exists. Deriving it rather than listing it
#: means an item added as optional cannot become mandatory by being forgotten
#: about here.
STAGE_A_REQUIRED_KEYS: tuple[str, ...] = tuple(
    item.key
    for item in CONSENT_ITEMS
    if item.stage == STAGE_REGISTRATION and item.required
)


def catalogue_payload() -> list[dict[str, Any]]:
    """The items as the API serves them, stage and wording included, so no
    screen ever authors consent copy of its own.

    The version travels with the text because the two are one fact: a client
    that cached a payload and posts a tick against stale words should be
    visible in the record as having consented to those words.
    """
    return [
        {
            "key": item.key,
            "stage": item.stage,
            "text": item.text,
            "version": item.version,
            "required": item.required,
        }
        for item in CONSENT_ITEMS
    ]


def stage_payload(stage: str) -> list[dict[str, Any]]:
    """The catalogue for one stage, the shape every consent screen renders."""
    return [item for item in catalogue_payload() if item["stage"] == stage]


async def record_items(
    session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    keys: list[str] | tuple[str, ...],
    source: str,
) -> int:
    """Record (or re-affirm) each named item, individually timestamped.

    TWO WRITES PER ITEM, AND ONE WRITER FOR BOTH. The standing row answers
    "does this stand, since when" and is upserted; the event row is appended
    and never touched again, carrying the version, the digest and the verbatim
    wording in force at this moment. Routing both through this one function is
    what keeps a history and its mirror from disagreeing, the same reason
    `apply_transition` is the only writer of `pipeline_status`.

    An unknown key RAISES rather than writing an unreadable row: the caller
    is code, not a candidate, so a bad key is a bug and silence would bury
    it. Returns how many items were recorded.
    """
    now = datetime.now(timezone.utc)
    written = 0
    for key in keys:
        if key not in ITEMS_BY_KEY:
            raise ValueError(f"unknown consent item key: {key!r}")
        item = ITEMS_BY_KEY[key]
        digest = text_digest(item)
        params = {
            "cid": str(candidate_id),
            "key": key,
            "stage": item.stage,
            "source": source,
            "version": item.version,
            "digest": digest,
            "now": now,
        }
        await session.execute(
            text(
                "INSERT INTO candidate_consents "
                "(id, candidate_id, item_key, stage, source, consented_at, "
                "item_version, text_sha256) "
                "VALUES (:id, :cid, :key, :stage, :source, :now, "
                ":version, :digest) "
                "ON CONFLICT (candidate_id, item_key) "
                "DO UPDATE SET consented_at = :now, source = :source, "
                "item_version = :version, text_sha256 = :digest"
            ),
            {"id": str(uuid.uuid4()), **params},
        )
        await session.execute(
            text(
                "INSERT INTO candidate_consent_events "
                "(id, candidate_id, item_key, stage, source, item_version, "
                "consent_text, text_sha256, recorded_at) "
                "VALUES (:id, :cid, :key, :stage, :source, :version, "
                ":body, :digest, :now)"
            ),
            {"id": str(uuid.uuid4()), "body": item.text, **params},
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

    `text` is the CURRENT wording and `consented_version` is what the
    candidate actually agreed to, so the two disagreeing is the whole signal:
    `wording_current` is False when an item has been re-worded since, which is
    a standing consent to words nobody is showing any more. A row written
    before migration 0117 carries no version, and reports None rather than a
    number invented to fill the column.
    """
    rows = {
        row[0]: (row[1], row[2])
        for row in (
            await session.execute(
                text(
                    "SELECT item_key, consented_at, item_version "
                    "FROM candidate_consents WHERE candidate_id = :cid"
                ),
                {"cid": str(candidate_id)},
            )
        ).all()
    }
    payload: list[dict[str, Any]] = []
    for item in CONSENT_ITEMS:
        consented_at, consented_version = rows.get(item.key, (None, None))
        payload.append(
            {
                "key": item.key,
                "stage": item.stage,
                "text": item.text,
                "version": item.version,
                "required": item.required,
                "consented_at": consented_at,
                "consented_version": consented_version,
                "wording_current": consented_version == item.version,
            }
        )
    return payload


async def history_for(
    session: AsyncSession, candidate_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Every consent act this candidate has performed, newest first.

    The verbatim wording travels with each act. A candidate asking what they
    agreed to in March must be answered with March's sentence, and this is the
    only place that sentence still exists once the catalogue has moved on.
    """
    rows = (
        await session.execute(
            text(
                "SELECT item_key, stage, source, item_version, consent_text, "
                "text_sha256, recorded_at FROM candidate_consent_events "
                "WHERE candidate_id = :cid ORDER BY recorded_at DESC, id"
            ),
            {"cid": str(candidate_id)},
        )
    ).all()
    return [
        {
            "key": row[0],
            "stage": row[1],
            "source": row[2],
            "version": row[3],
            "text": row[4],
            "text_sha256": row[5],
            "recorded_at": row[6],
        }
        for row in rows
    ]


async def stage_a_missing(
    session: AsyncSession, candidate_id: uuid.UUID
) -> list[str]:
    """The REQUIRED Stage A keys this candidate has not consented to, asked of
    the TABLE rather than of any stamp on the candidate row.

    Empty means registration consent stands. This is what the profile gate
    reads, so a candidate who already consented through the outreach form or
    an application is never asked a second time, and an OPTIONAL registration
    item they declined never holds their profile open.
    """
    held = {
        row[0]
        for row in (
            await session.execute(
                text(
                    "SELECT item_key FROM candidate_consents "
                    "WHERE candidate_id = :cid AND item_key = ANY(:keys)"
                ),
                {"cid": str(candidate_id), "keys": list(STAGE_A_REQUIRED_KEYS)},
            )
        ).all()
    }
    return [key for key in STAGE_A_REQUIRED_KEYS if key not in held]


CROSS_EMPLOYER_EVIDENCE_REUSE = "cross_employer_evidence_reuse"


async def cross_employer_reuse_allowed(
    session: AsyncSession, candidate_id: uuid.UUID
) -> bool:
    """May this candidate's portable evidence establish criteria for ANOTHER employer?

    THIS IS THE ONE AUTHORITY, and it reads the catalogue row rather than
    `candidates.retain_assessment_consent`. The two were briefly both in play
    and that was a defect, not a belt and braces: two records for one
    permission is the shape rule 5 forbids, and the one that must win is the
    one whose WORDING actually says what is being permitted.

    `retain_assessment_consent` says "retain the completed assessment for
    future jobs, or for this job only". That is a statement about RETENTION,
    and the product had it wired to the narrower DOWNLOAD verb. It nowhere
    tells a candidate that evidence gathered while employer A assessed them
    may be reused to establish what employer B grades them against. Reading it
    here would have been a consent stretched to cover a purpose it does not
    name, which is precisely the failure the per item catalogue exists to
    prevent.

    A missing row refuses. Absence of consent is never consent, and because
    the item is OPTIONAL a candidate who was offered it and declined is
    indistinguishable here from one who has not been asked yet: both get every
    criterion assessed fresh, which is the product's behaviour from before
    portable evidence existed and is never wrong, only slower.
    """
    row = (
        await session.execute(
            text(
                "SELECT 1 FROM candidate_consents "
                "WHERE candidate_id = :cid AND item_key = :key"
            ),
            {"cid": str(candidate_id), "key": CROSS_EMPLOYER_EVIDENCE_REUSE},
        )
    ).first()
    return row is not None
