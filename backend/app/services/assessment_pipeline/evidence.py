"""Stage 1 of the assessment pipeline: where each answer lives, and THE ledger
writer for answers.

ONE WRITER, CALLED TWICE, WRITING ONCE
--------------------------------------
`record_answer_evidence` is the only code in the product that files a
candidate's answer in Miti's evidence ledger (`evidence_items`,
`evidence_claims`, `evidence_claim_links`). It is called from two places:

* PER ANSWER, during the conversation (the respond path, wired by the
  assessment phase), so Vaada's contradiction probing reads a ledger that
  exists while the candidate is still answering. Until this module the ledger
  was written only at scoring time, after the conversation had ended, so the
  loop between the interviewer and the grader was dead by construction.
* AS A BACKFILL at scoring (`backfill_answer_evidence`, from
  `functional_assessment`), which covers conversations that finished before
  the per-answer call existed and any answer whose per-answer write failed.

Both calls write the same rows once. The evidence row is idempotent in the
ledger (an existing live row for the same message is returned, and migration
0118's partial unique index makes that hold under concurrency), the claim is
an upsert on its identity, and the attachment is an upsert on (claim,
evidence). `tests/test_answer_evidence_idempotent.py` proves it against a
real table from a second connection.

FOUR RULES, EACH NAMING THE FAILURE IT PREVENTS
-----------------------------------------------
1. A LEDGER FAILURE NEVER FAILS THE CALLER. The caller is either a candidate
   mid-assessment or a report being written for work already done and already
   charged for. Every write runs inside a SAVEPOINT, because an INSERT that
   reaches Postgres and fails aborts the surrounding transaction, and
   swallowing the exception without one would turn "the ledger write failed"
   into "the answer or the report was lost". What is caught is a DATABASE
   failure (`SQLAlchemyError`) and nothing wider: a `TypeError` is a
   programming error and must propagate (the 2026-09-22 rule).
2. A LOCATOR, NEVER THE SENTENCE. The ledger row carries
   `assessment_messages:<id>`; the text stays in the transcript, behind the
   capability that guards it.
3. ONLY A SUBSTANTIVE ANSWER BECOMES EVIDENCE, decided by
   `answer_quality.is_substantive`, the SAME classifier the scorer uses. A
   second set of thresholds here would drift, and the ledger would then claim
   evidence for a grade the scorer had treated as unanswered.
4. EVIDENCE FIRST, THEN THE CLAIM, THEN THE ATTACHMENT. A claim with no live
   evidence under it is CRITICAL to `contradictions.detect`; creating the claim
   first and failing on the evidence would manufacture the most serious finding
   the system has out of a transient write error.

Recording evidence is a SIDE EFFECT and never an input to a grade: nothing in
this module can name the grade scale, the unanswered score or the Must-have cap.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterable

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentConversation, AssessmentMessage
from app.services import answer_quality
from app.services.assessment_pipeline.types import AnswerRecord, AssessmentInputs

logger = logging.getLogger(__name__)

#: Locator relevance for an answer given directly to a question probing this
#: skill. INTERNAL ENGINEERING METADATA: it orders evidence inside a prompt and
#: an operator view, it is never a score, and it never reaches a response schema.
DIRECT_ANSWER_RELEVANCE = 1.0

#: Written into every provenance payload. The ledger is Miti's, whichever stage
#: filed the row, so an operator reading one knows whose working it is.
LEDGER_OWNER = "miti"

#: The subject every answer claim is about.
_SUBJECT = "candidate"


def claim_wording(skill_name: str) -> str:
    """The ledger's OWN normalised wording for an answer claim.

    Written by the product, never lifted from the answer: a phrase from the
    candidate's sentence would put their words in the one table that must never
    hold them. Also the claim IDENTITY, so both callers must use exactly this.
    """
    return f"the candidate demonstrated {skill_name} in the assessment conversation"


async def answer_records(
    session: AsyncSession, link_id: uuid.UUID
) -> dict[str, list[AnswerRecord]]:
    """Every candidate answer on this application, keyed by `question_key`
    exactly as the scorer keys them, in turn order.

    READ FROM THE DATABASE, NOT FROM A TRANSCRIPT A CALLER ASSEMBLED. The
    transcripts callers build carry no row ids, and deriving locators from
    whatever shape a caller happened to send would mean the ledger silently
    recorded nothing on the path that matters.
    """
    rows = (
        await session.execute(
            select(
                AssessmentMessage.id,
                AssessmentMessage.ordinal,
                AssessmentMessage.question_key,
                AssessmentMessage.content,
                AssessmentMessage.created_at,
            )
            .join(
                AssessmentConversation,
                AssessmentConversation.id == AssessmentMessage.conversation_id,
            )
            .where(
                AssessmentConversation.job_candidate_link_id == link_id,
                AssessmentMessage.speaker == "candidate",
            )
            .order_by(AssessmentMessage.ordinal)
        )
    ).all()
    located: dict[str, list[AnswerRecord]] = {}
    for message_id, ordinal, question_key, content, created_at in rows:
        if not question_key:
            continue
        key = str(question_key)
        located.setdefault(key, []).append(
            AnswerRecord(
                message_id=message_id,
                question_key=key,
                turn=int(ordinal or 0),
                text=str(content or ""),
                answered_at=created_at,
            )
        )
    return located


@asynccontextmanager
async def _savepoint(session: Any) -> AsyncIterator[None]:
    """A SAVEPOINT around the ledger writes (rule 1 in the module docstring).

    A session without `begin_nested` (a unit-test stand-in) simply runs the
    body, so the guarantee degrades to the `except` around it, never to a crash.
    """
    nested = getattr(session, "begin_nested", None)
    if nested is None:
        yield
        return
    async with nested():
        yield


async def record_answer_evidence(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    link_id: uuid.UUID,
    candidate_id: uuid.UUID,
    skill_id: uuid.UUID,
    skill_name: str,
    skill_bucket: str,
    question_id: uuid.UUID | None,
    message_id: uuid.UUID,
    turn: int,
    text: str,
    answered_at: datetime | None,
) -> None:
    """File ONE answer as evidence for ONE skill's claim. Idempotent.

    Calling it twice for the same message writes one evidence row and one
    attachment; the second call changes nothing. Returns None whether the
    answer was recorded, was already recorded, was not substantive, or failed
    to write (logged at ERROR with the traceback), because no caller may
    branch on the ledger: it is a side effect, never an input.
    """
    # Imported INSIDE the function, never at module scope. `app.services.
    # evidence` sits on an import cycle (tests/test_import_graph.py names it),
    # and this module is imported by `functional_assessment` at module scope.
    from app.services.evidence import ledger
    from app.services.evidence import negative as negative_evidence
    from app.services.miti import claims as claim_model

    if not answer_quality.is_substantive(text):
        return
    try:
        async with _savepoint(session):
            evidence_id = await ledger.record_evidence(
                session,
                tenant_id=tenant_id,
                job_id=job_id,
                link_id=link_id,
                source_type=ledger.SOURCE_ANSWER,
                source_id=message_id,
                # A LOCATOR, built by the ledger's own helper. Assembling this
                # string by hand is how the sentence eventually gets pasted in.
                ref=ledger.text_ref(table="assessment_messages", row_id=message_id),
                # The candidate said it, unprompted, in their own words. Not
                # `validated`: nobody has confirmed it against anything.
                trust=ledger.TRUST_OBSERVED,
                relevance=DIRECT_ANSWER_RELEVANCE,
                provenance={
                    "agent": LEDGER_OWNER,
                    "candidate_id": str(candidate_id),
                    "competency_id": str(skill_id),
                    "competency_category": skill_bucket,
                    "question_id": str(question_id) if question_id else None,
                    "conversation_turn": int(turn),
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    # A FACT ABOUT THE TEXT, decided while the text is in hand,
                    # because it is the only moment anything holds it (Runbook
                    # 6.1 separates E0 from E1 on exactly this).
                    "has_specifics": claim_model.has_specifics(text),
                },
                freshness_payload=ledger.freshness(answered_at),
            )
            claim_id = await ledger.record_claim(
                session,
                tenant_id=tenant_id,
                job_id=job_id,
                link_id=link_id,
                subject=_SUBJECT,
                dimension=skill_name,
                claim=claim_wording(skill_name),
            )
            # WHICH SIDE of the claim this answer sits on (W6.6). "I have not
            # used Kafka" is a substantive answer AND counter-evidence.
            disclaimed = negative_evidence.disclaimed_terms(text, skill_name)
            await ledger.attach_evidence(
                session,
                tenant_id=tenant_id,
                claim_id=claim_id,
                evidence_id=evidence_id,
                stance=(
                    ledger.STANCE_CONTRADICTS if disclaimed else ledger.STANCE_SUPPORTS
                ),
            )
        if disclaimed:
            logger.info(
                "assessment_pipeline.negative_evidence link_id=%s skill_id=%s "
                "message_id=%s terms=%s",
                link_id, skill_id, message_id, disclaimed,
            )
    except SQLAlchemyError:
        logger.exception(
            "assessment_pipeline.evidence_not_recorded link_id=%s skill_id=%s "
            "message_id=%s",
            link_id, skill_id, message_id,
        )


async def backfill_answer_evidence(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    link_id: uuid.UUID,
    candidate_id: uuid.UUID,
    skill_id: uuid.UUID,
    skill_name: str,
    skill_bucket: str,
    question_id: uuid.UUID | None,
    records: Iterable[AnswerRecord],
) -> None:
    """The scoring-time pass: every located answer to one question, through the
    same writer. Answers the per-answer call already filed are no-ops."""
    for record in records:
        await record_answer_evidence(
            session,
            tenant_id=tenant_id,
            job_id=job_id,
            link_id=link_id,
            candidate_id=candidate_id,
            skill_id=skill_id,
            skill_name=skill_name,
            skill_bucket=skill_bucket,
            question_id=question_id,
            message_id=record.message_id,
            turn=record.turn,
            text=record.text,
            answered_at=record.answered_at,
        )


# ── Stage 1: everything the later stages read, gathered once ────────────────


def answers_by_key(transcript: Iterable[Any] | None) -> dict[str, list[str]]:
    """Candidate answers grouped by the `question_key` stamped on each message.

    Accepts message rows or dicts (`speaker`, `question_key`, `content`). The
    key is the QUESTION's own id, which is what the conversation stamps and
    what Miti's item stage looks answers up by
    (`tests/test_conversation_key_contract.py`).
    """
    grouped: dict[str, list[str]] = {}
    for message in transcript or []:
        if isinstance(message, dict):
            speaker = message.get("speaker")
            key = message.get("question_key")
            content = message.get("content")
        else:
            speaker = getattr(message, "speaker", None)
            key = getattr(message, "question_key", None)
            content = getattr(message, "content", None)
        text = str(content or "").strip()
        if str(speaker) != "candidate" or not key or not text:
            continue
        grouped.setdefault(str(key), []).append(text)
    return grouped


async def structured_answers(
    session: AsyncSession, link_id: uuid.UUID
) -> dict[str, Any]:
    """Every `assessment_answers` row on this application, keyed exactly as the
    scorer keys answers: by the question's own id.

    A failed read RAISES: scoring every structured answer as unanswered because
    the table could not be read would be a synthetic Not Matching written from
    an outage.
    """
    from app.models.assessment import AssessmentAnswer

    rows = (
        await session.execute(
            select(AssessmentAnswer)
            .join(
                AssessmentConversation,
                AssessmentConversation.id == AssessmentAnswer.conversation_id,
            )
            .where(AssessmentConversation.job_candidate_link_id == link_id)
        )
    ).scalars().all()
    return {str(row.question_id): row for row in rows}


async def load_inputs(
    session: AsyncSession,
    *,
    job: Any,
    link: Any,
    conversation: Any,
    questions: Iterable[Any],
    grade: str,
) -> AssessmentInputs:
    """Stage 1: read what the grading and the report are written from.

    The transcript, the answer locators, the structured answers, the coding
    evidence (hidden tests and the quality review, Phase 4's one evidence
    helper), the candidate's name parts and the Validation section. Every read
    RAISES on a failure; none is absorbed into "nothing recorded".
    """
    from app.models.candidate import Candidate
    from app.services.assessment_pipeline.validation import validation_section
    from app.services.coding_assessment import evidence as coding_evidence

    messages = (
        await session.execute(
            select(AssessmentMessage)
            .where(AssessmentMessage.conversation_id == conversation.id)
            .order_by(AssessmentMessage.ordinal)
        )
    ).scalars().all()
    coding = await coding_evidence.for_conversation(session, conversation.id)
    candidate = await session.get(Candidate, link.candidate_id)
    return AssessmentInputs(
        job=job,
        link=link,
        conversation=conversation,
        grade=grade,
        questions=tuple(questions),
        answers=answers_by_key(messages),
        locators=await answer_records(session, link.id),
        structured=await structured_answers(session, link.id),
        coding={str(question_id): item for question_id, item in coding.items()},
        subject_names=tuple(str(getattr(candidate, "full_name", "") or "").split()),
        validation=await validation_section(session, link),
    )
