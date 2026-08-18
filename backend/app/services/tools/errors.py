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


class ToolPermissionError(ToolError):
    """The calling agent does not hold this tool.

    Never retried and never downgraded to a warning. An agent reaching for a
    tool it was not granted is a wiring defect or an injection succeeding, and
    both want to be loud.
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
