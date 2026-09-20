"""Support thread schemas, for both sides of one conversation.

TWO OUTPUT SHAPES, DELIBERATELY, AND THE DIFFERENCE IS ONE FACT
-----------------------------------------------------------------
The customer sees their own thread. Vivekium staff see the same thread plus
which CUSTOMER it belongs to, because their queue spans every customer and a
subject line alone does not say who is waiting.

The staff shape EXTENDS the customer one rather than restating it, so the two
cannot drift apart in what they say about a thread. What the customer shape
does not carry is not hidden detail; it is a fact the customer already knows.

NO NUMBER THAT DESCRIBES A PERSON CROSSES THIS BOUNDARY, and there is nothing
here that could carry one. A support thread is about an account. Counts of
messages and of waiting threads are OPERATIONAL, the same class the
intelligence dashboards already allow, and they describe a queue rather than a
candidate.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.support import MESSAGE_SIDES, THREAD_STATUSES
from app.schemas.pagination import PageMeta
from app.services.support import MAX_BODY_CHARS, MAX_SUBJECT_CHARS

__all__ = [
    "MessageIn",
    "ProviderThreadDetailOut",
    "ProviderThreadListOut",
    "ProviderThreadOut",
    "SupportMessageOut",
    "ThreadDetailOut",
    "ThreadListOut",
    "ThreadOpenIn",
    "ThreadOut",
    "ThreadPatchIn",
]

ThreadStatus = Literal["open", "awaiting_customer", "resolved"]
MessageSide = Literal["customer", "staff"]


def _assert_literals_match_the_model() -> None:
    """The `Literal`s above and the model's constants are two statements of one
    vocabulary, so they are compared at import rather than trusted to agree.

    A `Literal` that fell behind the database CHECK would answer 422 to a value
    the column accepts, which reads to the caller as their own bad request. The
    same class of split that put `PipelineStatus` five values behind migration
    0018 and 500'd a whole tenant's dashboard.
    """
    assert set(THREAD_STATUSES) == set(ThreadStatus.__args__), (
        THREAD_STATUSES,
        ThreadStatus.__args__,
    )
    assert set(MESSAGE_SIDES) == set(MessageSide.__args__), (
        MESSAGE_SIDES,
        MessageSide.__args__,
    )


_assert_literals_match_the_model()


class ThreadOpenIn(BaseModel):
    """A customer starting a conversation.

    Subject AND first message together: a thread with no message in it is a row
    nobody can answer, and letting one exist would put an empty entry at the
    top of the staff queue.
    """

    subject: str = Field(min_length=1, max_length=MAX_SUBJECT_CHARS)
    body: str = Field(min_length=1, max_length=MAX_BODY_CHARS)

    @field_validator("subject", "body")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """Refused rather than trimmed into acceptance.

        `min_length` counts characters and a subject of three spaces has three,
        so without this the row would be refused by the database CHECK as a 500
        rather than by the schema as a 422. The emptiness test is stated once in
        SQL (`btrim`) and this is the caller-facing half of the same rule.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("must contain something other than whitespace")
        return stripped


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=MAX_BODY_CHARS)

    @field_validator("body")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must contain something other than whitespace")
        return stripped


class ThreadPatchIn(BaseModel):
    """Vivekium staff moving a thread by hand. Status only.

    Assignment is deliberately absent: the first responder claims a thread by
    REPLYING to it. A separate claim action is one more thing to remember on a
    surface whose whole point is answering quickly, and a claim field nobody is
    obliged to use is a field that is empty on every real thread.
    """

    status: ThreadStatus


class SupportMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    #: Read from the stored column and never re-derived from the author's
    #: current role. See `models/support.py` for why.
    author_side: MessageSide
    #: The person's name at render time, or None once their account is gone.
    #: None rather than a placeholder: "Deleted user" is a claim about what
    #: happened to them, and an absent name is the honest shape.
    author_name: str | None = None
    body: str
    created_at: datetime


class ThreadOut(BaseModel):
    """What a customer sees in their own list."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject: str
    status: ThreadStatus
    created_at: datetime
    last_message_at: datetime
    #: Operational: about a queue, not about a person.
    message_count: int = 0


class ThreadDetailOut(ThreadOut):
    messages: list[SupportMessageOut] = Field(default_factory=list)


class ProviderThreadOut(ThreadOut):
    """The staff queue row: the same thread, plus who is waiting.

    `tenant_id` travels because the staff filter is by customer and a UI that
    could only filter by name would break on two customers with similar ones.
    No billing figure, no contact detail and no applicant count rides along:
    the Provider's read-only-by-absence rule applies to what a schema DECLARES
    just as much as to which routes exist.
    """

    tenant_id: uuid.UUID
    tenant_name: str
    assigned_to: uuid.UUID | None = None
    assigned_to_name: str | None = None


class ProviderThreadDetailOut(ProviderThreadOut):
    messages: list[SupportMessageOut] = Field(default_factory=list)


class ThreadListOut(PageMeta):
    """`PageMeta` is a MIXIN, not a field: it derives `total_pages`,
    `has_next` and `has_previous` from the three counts below, so a handler
    cannot update one and forget the others."""

    items: list[ThreadOut]
    total: int
    page: int
    page_size: int


class ProviderThreadListOut(PageMeta):
    items: list[ProviderThreadOut]
    total: int
    page: int
    page_size: int
    #: How many threads are waiting on Vivekium right now, across every
    #: customer and UNNARROWED by the page filters, so the number answers "how
    #: much is owed" rather than "how much is on this screen". Same shape and
    #: same reasoning as the New Candidates count on the job page.
    open_total: int = 0
