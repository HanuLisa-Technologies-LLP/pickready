"""Tool failures, split by whether retrying them could possibly help.

The split is the point. A timeout and a permission refusal are both "the tool
did not return a value", and treating them the same means either burning three
attempts on a refusal that will refuse identically every time, or giving up on
a provider blip that a 250ms wait would have cleared. The executor never
guesses: retryability is a property of the exception class.
"""
from __future__ import annotations


class ToolError(RuntimeError):
    """Base for every failure raised out of `tools.execute`."""

    #: Attempting the same call again cannot change the outcome.
    retryable = False

    def __init__(self, tool: str, detail: str) -> None:
        super().__init__(f"{tool}: {detail}")
        self.tool = tool
        self.detail = detail


class ToolNotFound(ToolError):
    """No tool is registered under that name."""


class ToolPolicyError(ToolError):
    """Refused by the policy engine, before the handler was reached.

    Carries the status a caller surfacing the refusal must answer with, so the
    SHAPE of the refusal is decided by the rule that made it rather than by
    whichever route renders it. Getting that backwards is how a cross-tenant
    probe learns, from a 403, that the row it named exists.
    """

    #: 403 unless a subclass says otherwise.
    http_status = 403

    def __init__(self, tool: str, detail: str, *, reason: str = "") -> None:
        super().__init__(tool, detail)
        #: The policy rule that refused, by name. Same token the verdict and
        #: the ledger line carry.
        self.reason = reason


class ToolPermissionError(ToolPolicyError):
    """The calling agent does not hold this tool.

    Never retried and never downgraded to a warning. An agent reaching for a
    tool it was not granted is a wiring defect or an injection succeeding, and
    both want to be loud.
    """


class ToolScopeError(ToolPolicyError):
    """The call named an object belonging to another tenant.

    404, never 403, and the detail names no identifier. RBAC 4 forbids one
    client from INFERRING another client's resources, and a refusal that
    distinguishes "forbidden" from "absent" is an existence oracle with a
    prompt in front of it.
    """

    http_status = 404


class ToolApprovalRequired(ToolPolicyError):
    """The call is irreversible and no current human approval was presented.

    Raised INSTEAD of running the handler, which is what "gated at the adapter
    until commit" has to mean in code. An outbound effect issued speculatively
    and compensated afterwards is an email the candidate has already read.
    """


class ToolInputError(ToolError):
    """The payload did not validate against the tool's input model."""


class ToolOutputError(ToolError):
    """The handler returned something its output model rejects.

    A tool defect, not a caller defect. Raised rather than repaired: a shape
    the model refuses is a shape nothing downstream was written against, and
    "repairing" it means inventing the missing field.
    """


class ToolTimeout(ToolError):
    """The handler exceeded the tool's per-attempt timeout."""

    retryable = True


class ToolExecutionError(ToolError):
    """The handler raised. Not retryable unless the cause says so."""


class RetryableToolError(ToolError):
    """Raise from a handler to ask the executor for another attempt.

    The escape hatch for a handler that knows its own failure was transient --
    an upstream 503, a lock it could not take -- in a way the executor cannot
    infer from the exception type alone.
    """

    retryable = True


def is_retryable(exc: BaseException) -> bool:
    """Whether another attempt at an identical call could plausibly differ."""
    if isinstance(exc, ToolError):
        return exc.retryable
    # Transport-shaped failures from anything a handler talks to. Deliberately
    # narrow: an unrecognised exception is treated as deterministic, because
    # retrying a genuine bug three times only makes it three times slower.
    return isinstance(exc, (TimeoutError, ConnectionError))
