"""The erasure state machine, as pure functions.

`consent_lifecycle` answers "where is this candidate in the renewal cycle"
without a database; this answers "how far through is this erasure" the same
way, and for the same reason. The dangerous half of a feature is the half that
has to be testable without infrastructure, because infrastructure is what a
test skips when it is not available and a skipped assertion about deletion is
an assertion nobody is making.

THREE STATES, AND WHY THERE IS NO FOURTH
------------------------------------------
    pending      The request exists; the database rows have not been erased.
                 Reachable only in the instant between opening the record and
                 running the cascade, and after a crash in that instant. A
                 resume from here runs the whole erasure.

    rows_erased  Rows, vectors and caches are gone. Objects are outstanding.
                 THIS IS THE STATE THE SWEEP EXISTS FOR, and it is the honest
                 description of a half-erasure: the person is out of the
                 product, and their documents are not yet out of the store.

    completed    Every enumerated object was deleted and CONFIRMED absent by a
                 HEAD. Terminal.

There is deliberately no `failed`. A failed erasure is not a state a person's
data may be left in: an object store that refuses today answers tomorrow, and
the only correct behaviour is to keep trying. What a failure moves is
`deletion_attempts` and `last_failure`, which is what makes a stuck request
loud rather than terminal. `ATTEMPTS_BEFORE_ALARM` is the line past which the
sweep escalates its log level, and it is NOT a give-up threshold: nothing in
this module or its callers ever stops retrying.

There is also no `objects_deleted` state distinct from `completed`. A request
whose objects are all gone but which has not been marked completed would be
indistinguishable from one that is still working, and the sweep would have two
ways to read the same row.
"""
from __future__ import annotations

from dataclasses import dataclass

#: The record exists; the database rows are still there.
STATE_PENDING = "pending"
#: Rows, vectors and caches are gone; objects are outstanding.
STATE_ROWS_ERASED = "rows_erased"
#: Everything is gone and every object deletion was confirmed. Terminal.
STATE_COMPLETED = "completed"

#: Mirrors migration 0108's CHECK constraint. `tests/test_deletion_requests.py`
#: compares the two, because a state the database refuses is a state the
#: application can never write and a state the database allows and this module
#: does not know about is a request the sweep would skip for ever.
ALL_STATES: tuple[str, ...] = (STATE_PENDING, STATE_ROWS_ERASED, STATE_COMPLETED)

#: The only edges. Forward-only: an erasure never goes back, because the thing
#: it erased does not come back either.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATE_PENDING: frozenset({STATE_ROWS_ERASED}),
    # An erasure with no objects at all (a databank candidate who never
    # uploaded anything) goes straight from rows_erased to completed with
    # nothing in between, which is the same edge a candidate with fifty
    # objects takes once the last one is confirmed gone.
    STATE_ROWS_ERASED: frozenset({STATE_COMPLETED}),
    STATE_COMPLETED: frozenset(),
}

#: Why the erasure was run. `consent_lifecycle` owns the two automatic reasons
#: because it is what decides them; this is the one a person chooses for
#: themselves, and it lives here because no lifecycle clock produces it.
REASON_CANDIDATE_REQUESTED = "candidate_requested"

#: How many failed object-deletion passes before the sweep escalates from a
#: warning to an error. NOT a give-up threshold: see the module docstring.
ATTEMPTS_BEFORE_ALARM = 5


class InvalidTransition(RuntimeError):
    """A state change the machine does not allow.

    Raised rather than ignored. An erasure that quietly refused to advance
    would sit in `rows_erased` for ever while the sweep reported it as work in
    progress, which is exactly the silence this record exists to end.
    """


def is_terminal(state: str) -> bool:
    return state == STATE_COMPLETED


def assert_transition(current: str, target: str) -> None:
    """Refuse a move the machine does not have. Raises `InvalidTransition`."""
    if current not in ALLOWED_TRANSITIONS:
        raise InvalidTransition(f"unknown deletion state {current!r}")
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(
            f"a deletion request cannot move from {current!r} to {target!r}"
        )


@dataclass(frozen=True)
class ObjectDeletionOutcome:
    """What one pass over a request's objects achieved.

    `remaining` is the keys still present, so the next pass has a shorter list
    rather than re-deleting what is already gone. `failure` is a CLASS NAME, or
    None: the record outlives the rows, so nothing that could quote one goes
    into it.
    """

    deleted: int
    remaining: tuple[dict[str, str], ...]
    failure: str | None = None

    @property
    def finished(self) -> bool:
        return not self.remaining


def next_state_after(outcome: ObjectDeletionOutcome, *, current: str) -> str:
    """Where a request lands after one object-deletion pass.

    Pure, so the decision "is this erasure finished" is one expression that a
    test can drive with every outcome shape, including the one that matters:
    an outcome with a failure and nothing remaining CANNOT complete, because
    `remaining` is computed from HEAD checks and a failure means some HEAD did
    not answer.
    """
    if current != STATE_ROWS_ERASED:
        raise InvalidTransition(
            f"objects can only be deleted from {STATE_ROWS_ERASED!r}, not "
            f"{current!r}"
        )
    if outcome.finished and outcome.failure is None:
        return STATE_COMPLETED
    return STATE_ROWS_ERASED
