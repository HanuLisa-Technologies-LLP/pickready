"""The erasure state machine, with no database and nothing to skip.

WHY THIS FILE HAS NO INFRASTRUCTURE
-------------------------------------
Every other assertion about deletion in this suite needs a database, and a test
that needs a database is a test that SKIPS when there is not one. A skipped
assertion about the code that permanently removes a person is an assertion
nobody is making. The rules below hold on any machine, in any environment, with
nothing running.

THE ONE THAT MATTERS MOST is
`test_an_outage_cannot_produce_a_completed_erasure`: `remaining` is computed
from HEAD checks, so a pass that recorded a failure did not confirm anything,
and confirming completion on the strength of an empty list would mark a
candidate's files permanently deleted because the store was unreachable.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.services import deletion_requests as dr

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0108_candidate_deletion_requests.py"
)


def test_the_states_match_the_database_check_constraint() -> None:
    """Two copies of one fact stay honest only when something compares them.

    A state the CHECK refuses is one the application can never write, and a
    state the CHECK allows that this module does not know about is a request
    the sweep would skip for ever, silently, because it matches no branch.
    """
    source = MIGRATION.read_text(encoding="utf-8")
    declared = re.search(r"^STATES = \(([^)]*)\)", source, re.MULTILINE)
    assert declared is not None, "migration 0108 no longer declares STATES"
    in_migration = tuple(re.findall(r'"([a-z_]+)"', declared.group(1)))
    assert in_migration == dr.ALL_STATES


def test_the_machine_is_forward_only() -> None:
    """An erasure never goes back, because what it erased does not either."""
    with pytest.raises(dr.InvalidTransition):
        dr.assert_transition(dr.STATE_ROWS_ERASED, dr.STATE_PENDING)
    with pytest.raises(dr.InvalidTransition):
        dr.assert_transition(dr.STATE_COMPLETED, dr.STATE_ROWS_ERASED)
    with pytest.raises(dr.InvalidTransition):
        dr.assert_transition(dr.STATE_PENDING, dr.STATE_COMPLETED)
    with pytest.raises(dr.InvalidTransition):
        dr.assert_transition("halfway", dr.STATE_COMPLETED)
    # The two edges that exist.
    dr.assert_transition(dr.STATE_PENDING, dr.STATE_ROWS_ERASED)
    dr.assert_transition(dr.STATE_ROWS_ERASED, dr.STATE_COMPLETED)


def test_pending_to_completed_is_not_an_edge() -> None:
    """Skipping `rows_erased` would skip the object pass entirely.

    Stated separately from the sweep above because this is the specific
    shortcut a future caller would reach for when a candidate has no files:
    it looks like an optimisation and it removes the only state the sweep
    resumes from.
    """
    assert dr.STATE_COMPLETED not in dr.ALLOWED_TRANSITIONS[dr.STATE_PENDING]


def test_a_finished_pass_completes_the_request() -> None:
    outcome = dr.ObjectDeletionOutcome(deleted=4, remaining=())
    assert outcome.finished
    assert (
        dr.next_state_after(outcome, current=dr.STATE_ROWS_ERASED)
        == dr.STATE_COMPLETED
    )


def test_an_outage_cannot_produce_a_completed_erasure() -> None:
    """A failure with nothing remaining is still not a completion.

    It cannot arise from `delete_candidate_objects`, which appends to
    `remaining` on every failure, and that is exactly why it is asserted here:
    the invariant must hold in the function that DECIDES, not only in the one
    that currently happens to feed it.
    """
    outcome = dr.ObjectDeletionOutcome(
        deleted=0, remaining=(), failure="ObjectStorageError"
    )
    assert (
        dr.next_state_after(outcome, current=dr.STATE_ROWS_ERASED)
        == dr.STATE_ROWS_ERASED
    )


def test_outstanding_objects_keep_the_request_open() -> None:
    outcome = dr.ObjectDeletionOutcome(
        deleted=1, remaining=({"key": "resumes/a.pdf", "kind": "resume"},)
    )
    assert not outcome.finished
    assert (
        dr.next_state_after(outcome, current=dr.STATE_ROWS_ERASED)
        == dr.STATE_ROWS_ERASED
    )


def test_objects_cannot_be_deleted_before_the_rows_are() -> None:
    """The ordering the whole record exists to preserve.

    Deleting the files first and then failing to erase the rows leaves a
    candidate in the product whose documents have been destroyed, which is
    worse than either half of the failure on its own.
    """
    outcome = dr.ObjectDeletionOutcome(deleted=0, remaining=())
    with pytest.raises(dr.InvalidTransition):
        dr.next_state_after(outcome, current=dr.STATE_PENDING)


def test_there_is_no_terminal_failure_state() -> None:
    """"We stopped trying to delete this person's documents" is not an outcome.

    An attempt ceiling in this machine would be a give-up threshold wearing a
    safety rail's clothes. `ATTEMPTS_BEFORE_ALARM` exists to change a LOG
    LEVEL and is deliberately not a state.
    """
    assert dr.ALL_STATES == (
        dr.STATE_PENDING,
        dr.STATE_ROWS_ERASED,
        dr.STATE_COMPLETED,
    )
    assert dr.is_terminal(dr.STATE_COMPLETED)
    assert not dr.is_terminal(dr.STATE_ROWS_ERASED)
    assert not dr.is_terminal(dr.STATE_PENDING)
