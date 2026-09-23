"""The code-execution port: the only surface domain code may import.

WHY A PORT
----------
Candidate code, model-written reference solutions and starter code run in a
sandbox on a separate host that holds no application secret. The domain
(question generation, Run, Submit, scoring) needs "run this program against
these inputs under these limits", and nothing about which sandbox answers.
So this module defines the vocabulary and `get_provider()` hands back an
implementation; exactly one module under `app/` knows a real sandbox's wire
format, and `tests/test_code_execution_architecture.py` keeps it that way.

WHAT A PROVIDER NEVER RECEIVES: AN EXPECTED OUTPUT
--------------------------------------------------
`TestInput` has a key and a stdin, and nothing else. Comparing a program's
output with the answer happens in domain code (`outputs_match`), so hidden
answers never leave the application, never sit in a sandbox database, and
cannot be echoed back by a response field. A sandbox that could grade would be
a sandbox that holds the answer key.

FIVE OPERATIONS, NOT ONE
------------------------
`run` is the interactive convenience: submit, poll to a predictive deadline,
collect, discard. The other four exist because a final submission must
PERSIST its ticket before it polls: a worker killed mid-poll then collects the
same run rather than submitting the candidate's code a second time.

THE TEST DOUBLE IS NOT A BACKEND
--------------------------------
`CODE_EXECUTION_BACKEND` is `judge0` or `disabled`. The fake provider
(`code_execution.fake`) is installed only through `override_provider`, which
REFUSES in production, so no configuration value can route a live candidate
to a double that never runs anything.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Protocol, Sequence, runtime_checkable

from app.core.config import get_settings
from app.services.code_execution.errors import (
    ExecutionError,
    ExecutionNotConfigured,
    ExecutionRejected,
    ExecutionTicketLost,
    ExecutionUnavailable,
)
from app.services.code_execution.limits import ExecutionLimits

__all__ = [
    "ExecutionOutcome",
    "ExecutionLimits",
    "TestInput",
    "TestExecution",
    "ExecutionTicket",
    "ProviderHealth",
    "CodeExecutionProvider",
    "ExecutionError",
    "ExecutionUnavailable",
    "ExecutionRejected",
    "ExecutionTicketLost",
    "ExecutionNotConfigured",
    "get_provider",
    "is_enabled",
    "override_provider",
    "outputs_match",
]

#: The one backend value that selects a real sandbox. Its adapter module is
#: imported lazily in `get_provider`, so importing this port never imports
#: an HTTP client.
_REAL_BACKEND = "judge0"


class ExecutionOutcome(str, Enum):
    """How one program run ended, in the domain's words.

    `INTERNAL_ERROR` is a sandbox fault: retryable, and never the candidate's
    fault, so no caller may grade it as a failed test.
    """

    OK = "ok"
    COMPILE_ERROR = "compile_error"
    RUNTIME_ERROR = "runtime_error"
    TIME_LIMIT = "time_limit"
    MEMORY_LIMIT = "memory_limit"
    OUTPUT_LIMIT = "output_limit"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class TestInput:
    """One input to run a program against. NO expected output, by design.

    `key` is opaque (for example "h3" or "v1") and never the content: it may
    be persisted and logged, the stdin may not.
    """

    __test__ = False  # a dataclass, not a pytest test class

    key: str
    stdin: str


@dataclass(frozen=True)
class TestExecution:
    """The result of one run. Text fields are truncated to the limits."""

    __test__ = False

    key: str
    outcome: ExecutionOutcome
    stdout: str
    stderr: str
    compile_output: str
    cpu_ms: int | None
    wall_ms: int | None
    memory_kb: int | None


@dataclass(frozen=True)
class ExecutionTicket:
    """Opaque references to an in-flight batch, persisted by the caller.

    `keys[i]` is the `TestInput.key` whose run `refs[i]` names, so a result
    can be matched to its input without the sandbox ever seeing the key.
    """

    provider: str
    refs: tuple[str, ...]
    keys: tuple[str, ...]


@dataclass(frozen=True)
class ProviderHealth:
    """What the sandbox says about itself. Operator data, never client data."""

    provider: str
    available: bool
    workers: int | None
    queue_depth: int | None
    detail: str


@runtime_checkable
class CodeExecutionProvider(Protocol):
    """Every sandbox adapter implements exactly these five operations."""

    name: str

    async def submit(
        self,
        *,
        language: str,
        source: str,
        tests: Sequence[TestInput],
        limits: ExecutionLimits,
    ) -> ExecutionTicket: ...

    async def collect(self, ticket: ExecutionTicket) -> list[TestExecution] | None:
        """The results in `ticket.keys` order, or None while any run is pending."""
        ...

    async def run(
        self,
        *,
        language: str,
        source: str,
        tests: Sequence[TestInput],
        limits: ExecutionLimits,
        deadline_seconds: float,
    ) -> list[TestExecution]:
        """Submit, poll and collect within `deadline_seconds`, then discard.

        Raises `ExecutionUnavailable(reason="deadline")` rather than returning
        a partial result as if it were complete.
        """
        ...

    async def discard(self, ticket: ExecutionTicket) -> None:
        """Delete the sandbox's copies of a finished batch."""
        ...

    async def health(self) -> ProviderHealth: ...


_override: CodeExecutionProvider | None = None


def is_enabled() -> bool:
    """True when a real sandbox is configured, or a test override is installed.

    Every surface that offers coding asks this FIRST and records
    "code execution unavailable" when it answers False, rather than offering
    something that cannot run.
    """
    if _override is not None:
        return True
    settings = get_settings()
    return (
        settings.code_execution_backend == _REAL_BACKEND
        and bool((settings.judge0_url or "").strip())
        and bool((settings.judge0_auth_token or "").strip())
    )


def get_provider() -> CodeExecutionProvider:
    """The configured provider. Raises `ExecutionNotConfigured` when none is."""
    if _override is not None:
        return _override
    settings = get_settings()
    if settings.code_execution_backend != _REAL_BACKEND:
        raise ExecutionNotConfigured(
            "code execution is disabled in this deployment", reason="disabled"
        )
    if not (settings.judge0_url or "").strip() or not (settings.judge0_auth_token or "").strip():
        raise ExecutionNotConfigured(
            "code execution is enabled but its sandbox address or token is not set",
            reason="missing_configuration",
        )
    from app.services.code_execution.judge0 import Judge0Provider

    return Judge0Provider.from_settings(settings)


@contextmanager
def override_provider(provider: CodeExecutionProvider) -> Iterator[CodeExecutionProvider]:
    """Install a provider for the duration of a block. Tests and harness only.

    REFUSED IN PRODUCTION: a double that never runs code, reachable on the live
    site, would grade every candidate against a script.
    """
    global _override
    if get_settings().is_production:
        raise RuntimeError("override_provider is refused in production")
    previous = _override
    _override = provider
    try:
        yield provider
    finally:
        _override = previous


def outputs_match(actual: str, expected: str) -> bool:
    """Whether a program's stdout answers a test. Pure.

    Line endings are normalised, trailing whitespace is dropped from every
    line and trailing empty lines are ignored; everything else must match
    exactly. LEADING whitespace is significant, because indentation can be the
    answer (a printed tree, a formatted table).
    """
    return _normalise(actual) == _normalise(expected)


def _normalise(text: str) -> list[str]:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return lines
