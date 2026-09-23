"""A scripted `CodeExecutionProvider` for tests and the harness. NEVER EXECUTES.

It answers by LOOKUP: a test scripts the outcome for a (source, stdin) pair
and the double returns it. There is no interpreter, no subprocess and no
`exec` anywhere in this module, and `tests/test_code_execution_architecture.py`
walks its AST to keep it that way, because a double that ran candidate code
would be the one execution path that lives inside the application.

It is installed ONLY through `provider.override_provider`, which refuses in
production. It is not a value of `CODE_EXECUTION_BACKEND`.

An unscripted pair RAISES `LookupError` naming nothing but the fact: a double
that invented an outcome would let a test pass against behaviour nobody wrote.
"""
from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass, field
from typing import Sequence

from app.services.code_execution.errors import (
    ExecutionRejected,
    ExecutionTicketLost,
    ExecutionUnavailable,
)
from app.services.code_execution.limits import ExecutionLimits
from app.services.code_execution.provider import (
    ExecutionOutcome,
    ExecutionTicket,
    ProviderHealth,
    TestExecution,
    TestInput,
)

__all__ = ["FakeProvider", "ScriptedRun", "source_digest"]

FAKE_PROVIDER_NAME = "fake"


def source_digest(source: str) -> str:
    """The key a script is filed under. Hashing keeps sources out of reprs."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ScriptedRun:
    """What the double answers for one (source, stdin) pair."""

    outcome: ExecutionOutcome = ExecutionOutcome.OK
    stdout: str = ""
    stderr: str = ""
    compile_output: str = ""


@dataclass
class _Submission:
    language: str
    source_digest: str
    stdins: tuple[str, ...]
    keys: tuple[str, ...]
    limits: ExecutionLimits


@dataclass
class FakeProvider:
    """Scripted outcomes, a configurable number of pending polls, and failures.

    `unavailable` makes every operation raise `ExecutionUnavailable` with that
    reason. `pending_polls` is how many `collect` calls answer "still running"
    before the results appear. `submissions` and `discarded` record what the
    code under test did, so a test can assert on it.
    """

    name: str = FAKE_PROVIDER_NAME
    pending_polls: int = 0
    unavailable: str | None = None
    _scripts: dict[tuple[str, str], ScriptedRun] = field(default_factory=dict)
    _tickets: dict[str, _Submission] = field(default_factory=dict)
    _polls: dict[str, int] = field(default_factory=dict)
    _counter: itertools.count = field(default_factory=itertools.count)
    submissions: list[_Submission] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)

    def script(self, source: str, stdin: str, run: ScriptedRun) -> None:
        self._scripts[(source_digest(source), stdin)] = run

    def _refuse_if_down(self) -> None:
        if self.unavailable is not None:
            raise ExecutionUnavailable("fake sandbox is unavailable", reason=self.unavailable)

    async def submit(
        self,
        *,
        language: str,
        source: str,
        tests: Sequence[TestInput],
        limits: ExecutionLimits,
    ) -> ExecutionTicket:
        self._refuse_if_down()
        if not tests:
            raise ExecutionRejected("a submission needs at least one test input", reason="no_tests")
        ref = f"fake-{next(self._counter)}"
        submission = _Submission(
            language=language,
            source_digest=source_digest(source),
            stdins=tuple(t.stdin for t in tests),
            keys=tuple(t.key for t in tests),
            limits=limits,
        )
        self._tickets[ref] = submission
        self._polls[ref] = 0
        self.submissions.append(submission)
        return ExecutionTicket(provider=self.name, refs=(ref,), keys=submission.keys)

    async def collect(self, ticket: ExecutionTicket) -> list[TestExecution] | None:
        self._refuse_if_down()
        (ref,) = ticket.refs
        submission = self._tickets.get(ref)
        if submission is None:
            raise ExecutionTicketLost("fake sandbox does not hold that ticket", reason="pruned")
        self._polls[ref] += 1
        if self._polls[ref] <= self.pending_polls:
            return None
        results: list[TestExecution] = []
        for key, stdin in zip(submission.keys, submission.stdins):
            scripted = self._scripts.get((submission.source_digest, stdin))
            if scripted is None:
                raise LookupError("fake sandbox has no script for this source and input")
            results.append(
                TestExecution(
                    key=key,
                    outcome=scripted.outcome,
                    stdout=scripted.stdout,
                    stderr=scripted.stderr,
                    compile_output=scripted.compile_output,
                    cpu_ms=None,
                    wall_ms=None,
                    memory_kb=None,
                )
            )
        return results

    async def run(
        self,
        *,
        language: str,
        source: str,
        tests: Sequence[TestInput],
        limits: ExecutionLimits,
        deadline_seconds: float,
    ) -> list[TestExecution]:
        ticket = await self.submit(language=language, source=source, tests=tests, limits=limits)
        try:
            for _ in range(self.pending_polls + 1):
                results = await self.collect(ticket)
                if results is not None:
                    return results
            raise ExecutionUnavailable("fake sandbox did not finish", reason="deadline")
        finally:
            await self.discard(ticket)

    async def discard(self, ticket: ExecutionTicket) -> None:
        for ref in ticket.refs:
            self._tickets.pop(ref, None)
            self.discarded.append(ref)

    async def health(self) -> ProviderHealth:
        self._refuse_if_down()
        return ProviderHealth(
            provider=self.name, available=True, workers=1, queue_depth=0, detail="scripted"
        )
