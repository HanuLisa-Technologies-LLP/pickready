"""Value types shared by the assessment pipeline stages.

Frozen dataclasses only: no ORM row, no session, no model call. A stage hands
the next one values, so no later stage can reach back through a live object
into the database a previous stage read.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AnswerRecord:
    """One candidate answer, LOCATED: which message it is, which question it
    answered, and the turn it was given on.

    `text` is carried so the evidence writer can decide, while the text is in
    hand, whether the answer is substantive and whether it disclaims the skill.
    It never reaches the ledger: the ledger stores a LOCATOR to the message,
    because a copy of the sentence in a table anyone with database access can
    read would be a quiet route around the capability that guards the
    transcript (`view_review_screen`).
    """

    message_id: uuid.UUID
    #: The `question_key` the answer was filed under: a `candidate_questions`
    #: id as a string on every current conversation. A follow-up reuses its
    #: parent's key, so both answers are evidence for the same question.
    question_key: str
    turn: int
    text: str
    answered_at: datetime | None
