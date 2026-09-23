"""The failure vocabulary of code execution, shared by every adapter.

Four classes, because a caller must act differently on each and a single
"it failed" would force it to guess:

  ExecutionUnavailable    the sandbox could not be reached or could not take
                          the work (network, timeout, 5xx, queue full, a bad
                          credential). RETRYABLE, and never the candidate's
                          fault. `reason` is a short machine word.
  ExecutionRejected       the sandbox refused OUR request as malformed. A bug
                          on this side; retrying the same request cannot help,
                          so it is not retried.
  ExecutionTicketLost     the sandbox no longer knows a ticket it issued (the
                          host was replaced, or its hourly prune ran). The run
                          must be submitted again; collecting harder will not
                          find it.
  ExecutionNotConfigured  this deployment has no sandbox: the backend is
                          `disabled`, or its URL or token is empty, or the
                          configured limits do not fit the sandbox.
"""
from __future__ import annotations

__all__ = [
    "ExecutionError",
    "ExecutionUnavailable",
    "ExecutionRejected",
    "ExecutionTicketLost",
    "ExecutionNotConfigured",
]


class ExecutionError(RuntimeError):
    """Base class. Carries a short machine-readable `reason`."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class ExecutionUnavailable(ExecutionError):
    """Retryable: the sandbox could not take or finish the work."""


class ExecutionRejected(ExecutionError):
    """Not retryable: the sandbox refused the request as malformed."""


class ExecutionTicketLost(ExecutionError):
    """The sandbox no longer holds a submission it issued a ticket for."""


class ExecutionNotConfigured(ExecutionError):
    """This deployment has no usable sandbox."""
