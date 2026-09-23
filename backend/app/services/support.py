"""The support thread FSM, and the one place a message is written.

THE TRANSITION IS DERIVED FROM WHO WROTE, NOT CHOSEN BY THE CALLER
-------------------------------------------------------------------
`status_after_message` is a pure function of (current status, author side).
Nothing else decides. This is deliberate and it is the whole reason the queue
can be trusted: if a handler could pass a status alongside a message, then
"threads waiting on Vivekium" would mean "threads somebody remembered to mark",
and the first missed call would be a customer waiting on a reply that nobody
could see was owed.

The one status a human sets by hand is `resolved`, and only Vivekium staff set
it, through `PATCH`. A customer closing their own ticket is a feature nobody
asked for; a customer REOPENING one by replying is the behaviour they expect,
and it falls out of the table below rather than needing a special case.

WHY `resolved` IS NOT TERMINAL
--------------------------------
`email_senders.revoked` is terminal and should be: it is a withdrawal of trust
and re-earning it means proving the mailbox again. A resolved support thread is
the opposite. Making it terminal would force a customer with a follow-up
question to open a new thread and lose the history that made their question
answerable, so a reply moves it back to `open` and the record stays whole.

THE CANDIDATE BOUNDARY
------------------------
`SYSTEM_COPY` is the ONLY catalogue of message text this product authors, and
it holds no candidate-shaped substitution at all. A support conversation is
about the customer. Nothing automated may compose a message out of assessment,
report or candidate material, and `tests/test_support_candidate_boundary.py`
sweeps the tree for a write path that does. Free text a human types is not
validated and cannot be: saying that plainly is better than a filter that
catches "score" and misses "they got a 4".
"""
from __future__ import annotations

from app.models.support import (
    MESSAGE_SIDES,
    SIDE_CUSTOMER,
    SIDE_STAFF,
    THREAD_AWAITING_CUSTOMER,
    THREAD_OPEN,
    THREAD_RESOLVED,
    THREAD_STATUSES,
)

__all__ = [
    "IllegalThreadTransition",
    "MAX_BODY_CHARS",
    "MAX_SUBJECT_CHARS",
    "STATUS_AFTER_MESSAGE",
    "SYSTEM_COPY",
    "assert_status",
    "assert_transition",
    "status_after_message",
]

#: Ceilings, so a paste of a whole log cannot become an unbounded row that
#: every list render then has to carry. Generous on purpose: a support message
#: legitimately contains a stack trace, and truncating one would cost the
#: reader the line that mattered. Refused rather than trimmed, for the reason
#: the experience-band gate states: trimming picks which half to discard and
#: stores something nobody wrote.
MAX_SUBJECT_CHARS = 200
MAX_BODY_CHARS = 20_000

#: (author side) -> the status the thread lands in. Read as: whoever just
#: wrote is no longer the one being waited on.
#:
#: This is a total function over `MESSAGE_SIDES`, so there is no "current
#: status" arm that can be forgotten. A resolved thread receiving a customer
#: reply becomes `open`, which is the reopen behaviour, and a resolved thread
#: receiving a staff reply becomes `awaiting_customer`, which is a staff member
#: adding something after the fact and correctly putting the ball back.
STATUS_AFTER_MESSAGE: dict[str, str] = {
    SIDE_CUSTOMER: THREAD_OPEN,
    SIDE_STAFF: THREAD_AWAITING_CUSTOMER,
}

#: current status -> the statuses a human may move it to by hand. Only
#: Vivekium staff hold this, through the provider PATCH route. Writing a
#: message is NOT in this table: that path goes through
#: `status_after_message`, so the two mechanisms cannot disagree.
MANUAL_TRANSITIONS: dict[str, frozenset[str]] = {
    THREAD_OPEN: frozenset({THREAD_AWAITING_CUSTOMER, THREAD_RESOLVED}),
    THREAD_AWAITING_CUSTOMER: frozenset({THREAD_OPEN, THREAD_RESOLVED}),
    # Reopening by hand is allowed as well as by reply, because a staff member
    # who resolved the wrong thread should be able to undo it without writing
    # a message into a customer's inbox to do it.
    THREAD_RESOLVED: frozenset({THREAD_OPEN, THREAD_AWAITING_CUSTOMER}),
}

#: Copy this product writes into a support surface. Fixed strings, no model
#: call, and NO candidate substitution of any kind. Kept in one place for the
#: same reason `services/candidate_updates` keeps its catalogue in one place:
#: a rule enforced at a call site is a rule the next call site breaks.
SYSTEM_COPY: dict[str, str] = {
    "thread_resolved_note": (
        "This conversation has been marked resolved. Reply here if you need "
        "anything further and it will reopen."
    ),
}


class IllegalThreadTransition(ValueError):
    """A move the FSM does not allow. Raised, never logged and continued."""


def assert_status(status: str) -> str:
    if status not in THREAD_STATUSES:
        raise IllegalThreadTransition(
            f"{status!r} is not a support thread status; expected one of "
            f"{list(THREAD_STATUSES)}"
        )
    return status


def status_after_message(author_side: str) -> str:
    """Where a thread lands once `author_side` has written in it.

    Takes no current status on purpose. Every arrival of a message settles the
    question of who is waited on, whatever the thread was doing before, so a
    current-status parameter would only create arms that can disagree.
    """
    if author_side not in MESSAGE_SIDES:
        raise IllegalThreadTransition(
            f"{author_side!r} is not a message side; expected one of "
            f"{list(MESSAGE_SIDES)}"
        )
    return STATUS_AFTER_MESSAGE[author_side]


def assert_transition(current: str, target: str) -> str:
    """Refuse a manual move the table does not allow, naming both ends."""
    assert_status(current)
    assert_status(target)
    if target not in MANUAL_TRANSITIONS[current]:
        raise IllegalThreadTransition(
            f"a support thread cannot move from {current!r} to {target!r}; "
            f"allowed from {current!r}: {sorted(MANUAL_TRANSITIONS[current])}"
        )
    return target