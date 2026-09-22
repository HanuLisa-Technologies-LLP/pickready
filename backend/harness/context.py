"""What one scenario execution accumulated, and the second connection that judges it.

WHY THE STATE READER IS A SEPARATE OBJECT WITH ITS OWN ENGINE
---------------------------------------------------------------
HARNESS.md section 3 puts this first among the assertion kinds and says why:

    State assertions, evaluated against the database FROM A SECOND CONNECTION
    after the request completes. This is not stylistic. A write that answered
    200 and then rolled back at commit is invisible to an assertion on the
    response body, and this repository has shipped exactly that bug: the post
    flush `audit_log` rollback, 2026-09-20.

A comment saying "use a second connection" is a rule the next author breaks.
So the property is STRUCTURAL here, three ways over:

  * The session factory the application under test runs on is owned by
    `_Application` and is never reachable from a `ScenarioContext`. There is no
    attribute to accidentally reach for.
  * `StateReader` builds its OWN engine, and refuses to open while the
    application client is still running. A reader constructed mid-request could
    still see the request's uncommitted work through a pooled connection, which
    is the exact illusion being guarded against.
  * `StateReader` is created by the runner AFTER the client is closed and is
    handed only to the state-assertion pass. A workload step never receives
    one, so a scenario author cannot assert state from inside the workload at
    all.

WHY AN OBSERVATION CARRIES THE WHOLE RESPONSE
-----------------------------------------------
`Check` renders expected against actual, and a report whose "actual" column
reads "assertion failed" is the least useful report a harness can produce. So
every request the workload makes is kept in full: its step name, its method and
path, its status, its parsed body and how long it took. That is also what the
output assertions, the number sweep and the latency evaluator all read, so
there is one record of what the client saw rather than three.

Provenance: docs/spec/HARNESS.md sections 3, 4 and 7.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Iterator, Mapping, Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db import superadmin_scope

from harness.world import World

__all__ = [
    "ContextError",
    "Degradation",
    "Observation",
    "ScenarioContext",
    "StateReader",
]


class ContextError(RuntimeError):
    """The context was used in a way that would make its evidence untrue."""


@dataclass(frozen=True)
class Observation:
    """One request the workload made, and everything the client could see."""

    step: str
    method: str
    path: str
    status: int
    body: Any
    elapsed_ms: float
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "method": self.method,
            "path": self.path,
            "status": self.status,
            "body": self.body,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "note": self.note,
        }


@dataclass(frozen=True)
class Degradation:
    """Something the system said it was doing instead of what it usually does.

    RECORDED BY THE WORKLOAD FROM THE PRODUCT'S OWN SIGNAL, never inferred from
    the fact that a fault was injected. That distinction is the whole of
    `degradation_honesty`: a run where a model outage was injected and nothing
    was recorded is a system that survived SILENTLY, which HARNESS.md section 4
    calls a finding rather than a pass.

    `signal` names where the admission came from, so a reader can go and check
    it: `generated_by_ai=False`, `LoopResult.degraded`, `reranker="lexical"`,
    an `LLMUnavailableError` the caller caught and reported.
    """

    kind: str
    signal: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "signal": self.signal, "detail": self.detail}


class StateReader:
    """A second connection, opened after the response, with RLS bypassed.

    BYPASSED DELIBERATELY, and it is not a weakening of the thing under test.
    The tenant boundary is exercised by the REQUEST, through the real
    `get_tenant_db` and the real policy; what this reader answers afterwards is
    "what does the database actually hold", and a reader scoped to one tenant
    could not tell a row that was never written from a row it is not allowed to
    see. The tenancy evaluator needs both answers to say anything at all.

    `set_config('app.bypass_rls','on', false)` is NOT used here: this reader
    runs inside an explicit transaction, so `superadmin_scope`'s transaction
    local setting binds exactly as it does in the product. The
    session-level/transaction-local trap recorded on 2026-09-16 applies to
    probing OUTSIDE a transaction, which this never does.
    """

    def __init__(self, *, allowed: bool) -> None:
        if not allowed:
            raise ContextError(
                "a state reader was opened while the application client was "
                "still running. A connection taken then can observe the "
                "request's own uncommitted work, which is precisely the "
                "illusion that let the 2026-09-20 audit_log rollback answer "
                "200 and vanish. Close the client first."
            )
        self._engine = create_async_engine(
            get_settings().database_url, poolclass=NullPool
        )
        self._sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self._engine, expire_on_commit=False
        )

    async def scalar(self, sql: str, params: Mapping[str, Any] | None = None) -> Any:
        async with self._sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    result = await session.execute(sa.text(sql), dict(params or {}))
                    return result.scalar()

    async def column(
        self, sql: str, params: Mapping[str, Any] | None = None
    ) -> list[Any]:
        async with self._sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    result = await session.execute(sa.text(sql), dict(params or {}))
                    return list(result.scalars().all())

    async def rows(
        self, sql: str, params: Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        async with self._sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    result = await session.execute(sa.text(sql), dict(params or {}))
                    return [dict(row) for row in result.mappings().all()]

    async def close(self) -> None:
        await self._engine.dispose()


@dataclass
class ScenarioContext:
    """Everything one scenario execution accumulated, in one place.

    Mutable, like `RunRecord` and for the same reason: it has a genuine before
    and after, and a crashed workload must still leave behind the observations
    it managed to make. A frozen context would mean a scenario that died on its
    third request reported nothing about its first two.
    """

    world: World
    seed: int
    observations: list[Observation] = field(default_factory=list)
    trajectory: list[str] = field(default_factory=list)
    degradations: list[Degradation] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    #: Set by the runner when a fault could not be applied, so the evaluators
    #: report `unavailable` naming the missing layer rather than judging a run
    #: that was never faulted. HARNESS.md section 4: a fault must be observable
    #: in the result, and a fault that never happened has nothing to observe.
    fault_layer_absent: str | None = None
    #: Populated when the workload aborted. A check that never ran is a
    #: different state from one that ran and saw nothing (`run.Check.actual`
    #: is None only in the first case), and collapsing them sends a reader to
    #: the wrong half of the system.
    aborted: str | None = None

    def record(self, observation: Observation) -> Observation:
        self.observations.append(observation)
        return observation

    def stage(self, name: str) -> None:
        """Note that a named stage was reached, in order.

        The trajectory is the ORDER of stages, so a stage is appended even when
        it repeats: "invited, invited, scored" and "invited, scored" are
        different journeys and a set would render them identically.
        """
        self.trajectory.append(name)

    def degraded(self, kind: str, *, signal: str, detail: str) -> None:
        self.degradations.append(Degradation(kind, signal, detail))

    @property
    def last(self) -> Observation:
        if not self.observations:
            raise ContextError(
                "no request was made, so there is no last response to read. A "
                "scenario asserting over output must declare a workload."
            )
        return self.observations[-1]

    def by_step(self, step: str) -> list[Observation]:
        return [item for item in self.observations if item.step == step]

    def iter_bodies(self) -> Iterator[tuple[str, Any]]:
        """Every payload a client received, with the path it came back from.

        Used by `no_numbers_to_client`, which has to sweep what actually
        crossed the boundary rather than what a serializer was supposed to
        emit. A number reaches a client through the wire, not through a model.
        """
        for item in self.observations:
            if item.body is not None:
                yield item.path, item.body

    def as_dict(self) -> dict[str, Any]:
        return {
            "world": self.world.name,
            "world_ids": {
                key: _renderable(value) for key, value in sorted(self.world.ids.items())
            },
            "seed": self.seed,
            "observations": [item.as_dict() for item in self.observations],
            "trajectory": list(self.trajectory),
            "degradations": [item.as_dict() for item in self.degradations],
            "facts": {key: _renderable(value) for key, value in sorted(self.facts.items())},
            "notes": list(self.notes),
            "fault_layer_absent": self.fault_layer_absent,
            "aborted": self.aborted,
        }


def _renderable(value: Any) -> Any:
    """Anything, in a form `json.dumps` will not choke on.

    A world id is a `uuid.UUID` and a fact may be anything a workload chose to
    keep. The artifact store serialises with `default=str`, so this is belt and
    braces for the readers that do not: a manifest that cannot be written is a
    run that cannot be replayed.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(key): _renderable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_renderable(item) for item in value]
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
    return value


class _ClientGuard:
    """Tracks whether the application client is open, for `StateReader`.

    A module level flag rather than a field on the context, because the
    constraint is a property of the PROCESS: one application, one database, and
    a reader opened anywhere while a request is in flight can see into it.
    """

    def __init__(self) -> None:
        self.depth = 0

    def __enter__(self) -> "_ClientGuard":
        self.depth += 1
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.depth -= 1

    @property
    def open(self) -> bool:
        return self.depth > 0


CLIENT_GUARD = _ClientGuard()


def open_state_reader() -> StateReader:
    """The only way to get a reader, and it refuses while a client is open."""
    return StateReader(allowed=not CLIENT_GUARD.open)
