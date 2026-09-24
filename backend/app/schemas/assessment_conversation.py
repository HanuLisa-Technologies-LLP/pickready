"""The candidate's side of the assessment: invitation, consent, turns, voice.

Moved out of `schemas/assessments.py` with the routes that speak them
(`api/assessment_conversation.py`). `AnswerBehaviourIn` and `QuestionOut` stay
there because proctoring and the recording routes read them too.

NO NUMBER ABOUT THE CANDIDATE CROSSES HERE. What does cross is the clock: an
allocation in seconds, a deadline and the server's own time, so the countdown
the candidate reads is the server's and not the browser's. A clock is not a
score (Appendix B section 3; the 2026-09-24 supersession of `time-guidance`'s
"no digit reads as a clock"). The progress count (question N of M) is the same
operational count it has always been.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.assessments import AnswerBehaviourIn, QuestionOut
from app.services.assessment_formats import types as question_types


# ── The assessment invitation link (2026-08-11) ──────────────────────────────


class InvitationResolveOut(BaseModel):
    """What the invitation landing page needs to decide where to send someone.

    ONE response shape covers the whole flow -- signed out, signed in as the
    wrong person, expired, already submitted, ready -- because the page's job
    is to branch, and a branch is far harder to get wrong when the states are
    an enum in one payload than when they are spread across status codes.

    `state` is the branch. Everything else is context for the copy.
    """

    #: One of:
    #:   needs_auth        the link is good, nobody is signed in
    #:   wrong_account     signed in, but not as the invited candidate
    #:   ready             go to the assessment
    #:   in_progress       partly answered, same destination, different copy
    #:   completed         already submitted; the report is the destination
    #:   not_invited       the recruiter has not invited this application
    #:   expired           the signed link is past its lifetime
    #:   window_closed     the 30 + 5 day posting window has ended
    #:   invalid           not one of our links
    state: str
    #: Where to send the browser once the state allows it. Always a path on
    #: this site, never an absolute URL: an open redirect in an emailed link is
    #: exactly the thing a phisher would want from this endpoint.
    redirect_to: str | None = None
    #: Masked, e.g. `as***@example.com`. Only populated for `wrong_account`, so
    #: the candidate can tell which of their addresses was invited.
    invited_email_masked: str | None = None
    #: The email currently signed in, unmasked -- the caller already knows it.
    signed_in_email: str | None = None
    job_title: str | None = None
    company_name: str | None = None
    #: Human-readable, already resolved server-side. The page renders this
    #: rather than mapping the state to copy itself, so the email, the API and
    #: the page cannot describe the same situation three different ways.
    message: str


# ── Consent: one text, one mode (2026-09-24) ─────────────────────────────────


class ConsentTermsOut(BaseModel):
    """The consent screen, exactly as configured (spec 3.2, 3.3).

    The versions travel with the text so the client shows what the server will
    stamp; the row written on acceptance re-reads the settings server-side and
    never trusts these echoes back.
    """

    text: str
    consent_version: str
    privacy_policy_version: str
    terms_version: str
    #: The Stage B per-item catalogue (vivekium feature 6), server-authored:
    #: {key, stage, text, version} each. The screen renders these VERBATIM and
    #: ticks them one at a time; acceptance stamps each item server-side.
    items: list[dict] = []


class AssessmentConsentIn(BaseModel):
    """The Stage B items the candidate actually ticked.

    The keys are sent rather than a single boolean because the record has to
    be able to say each item was agreed to separately (vivekium feature 6).
    The server refuses anything but the complete Stage B set.
    """

    model_config = ConfigDict(extra="forbid")

    consent_keys: list[str] = []


class ConsentStateOut(BaseModel):
    """Whether this session's consent exists, and the terms it is given under."""

    consented: bool
    consent: ConsentTermsOut


# ── The turn ─────────────────────────────────────────────────────────────────


class ConversationMessageIn(BaseModel):
    """One answer, naming the turn it answers.

    `turn_seq` is the turn the candidate was shown. Any other number is a
    stale or replayed request (a double click, a retry after a lost response)
    and is refused with nothing written, so a retry can never be filed as the
    answer to the NEXT question.

    `answer` is the prose for a text turn. A structured format sends
    `answer_payload` in the shape `assessment_formats.types.ANSWER_MODELS`
    names and the server renders the transcript line from it. A spoken answer
    sends `voice_answer_id` and the server uses the FINAL transcript it holds,
    ignoring `answer`.

    `timed_out` says the countdown reached zero and the client submitted what
    it had; the SERVER still decides whether the answer arrived in time.
    There is no `paused_ms`: every paused second is a row the server wrote,
    and `extra="forbid"` makes an old client that sends one a 422 rather than
    a number quietly ignored.
    """

    model_config = ConfigDict(extra="forbid")

    turn_seq: int = Field(ge=1)
    answer: str = Field(default="", max_length=question_types.MAX_TEXT_ANSWER_CHARS)
    answer_payload: dict[str, Any] | None = None
    voice_answer_id: uuid.UUID | None = None
    timed_out: bool = False
    behaviour: AnswerBehaviourIn | None = None


class DraftIn(BaseModel):
    """The candidate's unsent answer, saved every few seconds.

    Kept for exactly one turn and submitted by the server only if time runs
    out. Never a second answer and never an edit of a past one.
    """

    model_config = ConfigDict(extra="forbid")

    turn_seq: int = Field(ge=1)
    answer: str = Field(default="", max_length=question_types.MAX_TEXT_ANSWER_CHARS)
    answer_payload: dict[str, Any] | None = None


class TurnOut(BaseModel):
    """The clock of the question on screen, as the server keeps it."""

    #: base | probe | reask
    kind: str
    allocation_seconds: int
    #: When the answer is due, in server time; moves later while paused.
    deadline_at: datetime
    #: The server's clock at the moment of the response, so the countdown can
    #: correct for a browser whose clock is wrong.
    server_now: datetime
    paused: bool
    #: device_loss | transcription | warning, when paused.
    pause_reason: str | None = None


class HistoryEntryOut(BaseModel):
    """One past exchange, READ-ONLY. There is no route that edits an answer."""

    question: str
    answer: str


class ConversationOut(BaseModel):
    conversation_id: uuid.UUID
    #: active | preparing | paused | completed | terminated
    status: str
    prompt: str | None
    progress_label: str
    answered_questions: int
    total_questions: int
    is_reask: bool = False
    answer_message_id: uuid.UUID | None = None
    #: The format of the prompt on screen. None once the conversation is over
    #: and on a follow-up or re-ask, which is always answered in prose.
    question: QuestionOut | None = None
    #: The turn on screen; 0 and no `turn` while preparing or once finished.
    turn_seq: int = 0
    turn: TurnOut | None = None
    #: Whether this turn may be SPOKEN: a prose turn in a deployment where
    #: speech to text is configured. False means the microphone is not offered.
    voice_input_available: bool = False
    #: Every exchange already answered, oldest first, read-only.
    history: list[HistoryEntryOut] = []
    #: The proctoring termination notice, in plain language, when `status` is
    #: terminated. Never a reason code.
    termination_message: str | None = None


# ── Spoken answers ───────────────────────────────────────────────────────────


class VoiceBeginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_seq: int = Field(ge=1)


class VoiceAnswerOut(BaseModel):
    """One spoken answer, as the candidate may see it.

    No object key and no bucket (the media delivery rule). `transcript` is the
    FINAL text once transcribed, read-only; `message` is the plain-language
    account of a failure, after which the answer is typed.
    """

    id: uuid.UUID
    #: recording | uploaded | transcribing | transcribed | failed | consumed
    status: str
    transcript: str | None = None
    message: str | None = None
    #: The recorder's ceiling, served so client and server agree on it.
    max_seconds: int
