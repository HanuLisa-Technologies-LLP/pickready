"""Named, reversible, composable fault injectors, applied at the real seams.

HARNESS.md section 4 is the contract this implements, and its last paragraph is
the reason the module exists at all:

    A fault must be observable in the result. The point is never that the
    system survived. It is that the system DEGRADED THE WAY IT SAID IT WOULD.

Four properties follow from that, and each one is enforced here rather than
left to the caller's discipline.

**The seam is the product's own.** A model failure is a response served to the
real `httpx` client the real `llm_router` builds, so the real `raise_for_status`
raises it and the real `classify_failure` classifies it. Nothing here patches a
product function to return a canned answer: that would prove the fallback runs
when a mock says to, which is a statement about the mock.

**A fault is REVERSIBLE and restores the exact prior object.** Not a fresh
default, and not a reset helper that happens to look the same. `object_storage`
caches a boto3 client and offers `reset_client()`; using it to unwind would drop
a client somebody else built. Every install below captures what was there and
puts that back.

**Faults COMPOSE, and unwind in reverse order.** `apply()` is an `ExitStack`, so
the body raising does not skip an unwind, and two faults sharing a seam are
undone innermost first. Unwinding out of order is refused rather than tolerated:
on a shared seam it restores the wrong prior state, and the damage shows up in
the NEXT scenario.

**A fault must not leak.** `assert_clean()` raises if anything is still applied,
and it is the check a runner makes between scenarios. Two pieces of PRODUCT
state are snapshotted and restored alongside the vendor seams, because a fault
changes them and the change outlives the fault: `llm_router._breakers`, since a
credential failure trips the breaker on its first occurrence and a tripped
breaker refuses every later call in the process; and
`vendor_contract._checked`, the once-per-path memo, since one served response
consumes the first-live-use check that the NEXT scenario was relying on. Both
are restored on the way out, after the scenario has had its chance to assert on
them.

WHAT A FAULT DOES NOT DO
--------------------------
It does not resolve a credential. `llm_router.key_for_model` returns None when
the key for the called model is unset, and the router refuses before it ever
builds a request, so a scenario that wants the transport reached supplies a key
the way `tests/test_router_recovery.py` does. Injecting one here would mean the
fault layer deciding what is configured, which is the world builder's job.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Callable, Iterator, Mapping, Sequence

import httpx

from app.config.llm_providers import SETTINGS_ATTR_FOR_MODEL

from harness.doubles.clock import Clock
from harness.doubles.storage import FailingObjectStore
from harness.doubles.vendor import (
    OPENAI_HOST,
    VOYAGE_HOST,
    openai_responder,
    voyage_responder,
)

__all__ = [
    "FaultLeak",
    "FaultSpec",
    "FaultUnwindError",
    "UnknownFault",
    "active_clock",
    "active_faults",
    "apply",
    "assert_clean",
    "clock_advance",
    "clock_at",
    "dispatch_failure",
    "embedding_failure",
    "model_failure",
    "object_store_failure",
    "redis_down",
    "redis_slow",
]


class UnknownFault(LookupError):
    """A manifest named a fault this registry does not have.

    Raised rather than skipped. A replay that silently dropped a fault would
    re-run the scenario under conditions the manifest does not describe and
    report the result under the original run's id.
    """


class FaultLeak(RuntimeError):
    """A fault was still applied when the harness expected a clean process."""


class FaultUnwindError(RuntimeError):
    """Faults were unwound in an order that cannot restore the prior state."""


# -- The descriptor ----------------------------------------------------------


@dataclass(frozen=True)
class FaultSpec:
    """One fault, named and parameterised, in a form a manifest can hold.

    JSON serializability is checked at construction rather than at write time.
    A manifest that cannot be written is a run that cannot be replayed, and
    finding that out when the artifacts are flushed means finding it after the
    interesting failure has already happened.
    """

    name: str
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.name not in _REGISTRY:
            raise UnknownFault(
                f"unknown fault {self.name!r}; the registry holds "
                f"{sorted(_REGISTRY)}"
            )
        try:
            json.dumps(dict(self.params), sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                f"fault {self.name!r} carries parameters that will not "
                f"serialize into a run manifest: {self.params!r}"
            ) from exc

    def as_dict(self) -> dict[str, Any]:
        return {"fault": self.name, "params": dict(self.params)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FaultSpec":
        return cls(
            name=str(payload["fault"]), params=dict(payload.get("params", {}))
        )

    @classmethod
    def coerce(cls, value: Any) -> "FaultSpec":
        """Accept a spec, a manifest mapping, or a scenario's `FaultRequest`.

        `scenario.FaultRequest` carries the same two fields and is deliberately
        a separate type: that module is parse only and must not import this
        registry, or loading a scenario file would start validating fault names
        against the injectors, and a scenario would stop being readable without
        them. Reading it here by field rather than by class is what lets the
        runner hand a parsed scenario straight to `apply` with no translation
        layer in between, since a translation layer is where a parameter gets
        dropped in silence.
        """
        if isinstance(value, FaultSpec):
            return value
        if isinstance(value, Mapping):
            return cls.from_dict(value)
        name = getattr(value, "name", None)
        params = getattr(value, "params", None)
        if isinstance(name, str) and isinstance(params, Mapping):
            return cls(name=name, params=dict(params))
        raise TypeError(
            f"cannot read a fault out of {type(value).__name__}; expected a "
            f"FaultSpec, a manifest mapping, or anything carrying a 'name' and "
            f"a 'params'"
        )


@dataclass
class _Applied:
    spec: FaultSpec
    undo: Callable[[], None]


#: Everything currently applied, innermost last. A list rather than a set,
#: because order is what makes an unwind correct.
_ACTIVE: list[_Applied] = []


def active_faults() -> tuple[FaultSpec, ...]:
    """The descriptors currently applied, outermost first.

    This is what the run manifest records, and therefore what a replay applies.
    """
    return tuple(entry.spec for entry in _ACTIVE)


def assert_clean() -> None:
    """Raise if any fault is still applied.

    Called between scenarios. A leaked fault turns the next scenario's result
    into a statement about the previous one, and the symptom is a failure in
    something unrelated that passes when run alone, which is the most expensive
    shape of test failure there is.
    """
    if _ACTIVE:
        leaked = [entry.spec.as_dict() for entry in _ACTIVE]
        raise FaultLeak(
            f"{len(leaked)} fault(s) still applied: {leaked}. A fault is a "
            f"context manager and must be unwound by leaving its block."
        )


@contextmanager
def _applied(
    spec: FaultSpec, install: Callable[[], Callable[[], None]]
) -> Iterator[FaultSpec]:
    """Apply one fault, register it, and guarantee its unwind.

    `install` does the patching and returns the callable that puts everything
    back. Splitting it that way means the prior state is captured in the same
    closure that restores it, so there is no way to add a patch here and forget
    its matching restore.
    """
    undo = install()
    entry = _Applied(spec=spec, undo=undo)
    _ACTIVE.append(entry)
    try:
        yield spec
    finally:
        out_of_order = not _ACTIVE or _ACTIVE[-1] is not entry
        position = next((i for i, item in enumerate(_ACTIVE) if item is entry), None)
        if position is not None:
            del _ACTIVE[position]
        # Unwound anyway, then reported. Out of order the restore may put back a
        # value a later fault had already replaced, which is wrong; leaving the
        # patch in place and raising would be wrong AND permanent, and the next
        # scenario would inherit it with nothing pointing back here.
        undo()
        if out_of_order:
            raise FaultUnwindError(
                f"fault {spec.name!r} was unwound out of order (it sat at "
                f"position {position} of {len(_ACTIVE) + 1}). Faults sharing a "
                f"seam restore the state the one before them captured, so the "
                f"only correct unwind is innermost first. It has been undone "
                f"regardless, but the seam it shares may now hold a stale "
                f"value: treat any result from this run as unsound."
            )


# -- The HTTP seam -----------------------------------------------------------
#
# One patch of `httpx.AsyncClient`, shared by every vendor fault, installed when
# the first route is pushed and removed when the last is popped. Per-fault
# patching would mean the second fault capturing the first fault's replacement
# as "the prior state", and unwinding would leave the patch behind.
#
# Routing is by HOST, so a model fault and an embedding fault compose without
# either seeing the other's traffic. An unrouted host falls through to a real
# transport rather than being refused, because a fault is a statement about one
# vendor and must not become an accidental network embargo.


@dataclass(frozen=True)
class _HttpRoute:
    label: str
    host: str
    responder: Callable[[httpx.Request], httpx.Response]


_HTTP_ROUTES: list[_HttpRoute] = []
_REAL_ASYNC_CLIENT: Any = None


def _match_route(request: httpx.Request) -> _HttpRoute | None:
    for route in reversed(_HTTP_ROUTES):
        if request.url.host == route.host:
            return route
    return None


class _RoutingTransport(httpx.AsyncBaseTransport):
    """Serve a routed host from a fixture, and pass everything else through.

    The canned half goes through `httpx.MockTransport`, which is the seam
    HARNESS.md names and the one `tests/test_router_recovery.py` already uses,
    so a responder that raises (a timeout) propagates exactly as it does there.
    """

    def __init__(self) -> None:
        self._passthrough: httpx.AsyncBaseTransport | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        route = _match_route(request)
        if route is not None:
            mock = httpx.MockTransport(route.responder)
            return await mock.handle_async_request(request)
        if self._passthrough is None:
            # Built on first use, so a scenario that never touches an unrouted
            # host never opens a connection pool it has to close.
            self._passthrough = httpx.AsyncHTTPTransport()
        return await self._passthrough.handle_async_request(request)

    async def aclose(self) -> None:
        if self._passthrough is not None:
            await self._passthrough.aclose()
            self._passthrough = None


def _install_route(route: _HttpRoute) -> Callable[[], None]:
    global _REAL_ASYNC_CLIENT

    _HTTP_ROUTES.append(route)
    if len(_HTTP_ROUTES) == 1:
        _REAL_ASYNC_CLIENT = httpx.AsyncClient
        real = _REAL_ASYNC_CLIENT

        def factory(*args: Any, **kwargs: Any) -> Any:
            kwargs["transport"] = _RoutingTransport()
            return real(*args, **kwargs)

        httpx.AsyncClient = factory  # type: ignore[misc]

    def undo() -> None:
        global _REAL_ASYNC_CLIENT
        _HTTP_ROUTES.remove(route)
        if not _HTTP_ROUTES:
            httpx.AsyncClient = _REAL_ASYNC_CLIENT  # type: ignore[misc]
            _REAL_ASYNC_CLIENT = None

    return undo


#: The value written into a credential setting while a vendor fault is applied.
#: Obviously not a key, so a value that ever escaped into a log or an artifact
#: names itself. It unlocks nothing: the routing transport answers the vendor's
#: host from an authored fixture, so no request built with this can leave the
#: process.
_HARNESS_CREDENTIAL = "harness-fault-credential-not-a-real-key"


def _supply_credentials(attributes: Sequence[str]) -> Callable[[], None]:
    """Give the router a credential for the duration, and put back what was there.

    WHY THE FAULT SUPPLIES THIS AND NOT THE SCENARIO
    --------------------------------------------------
    `llm_router.key_for_model` returns None when the key for the called model
    is unset, and the router then REFUSES before it ever builds a request. So
    with no credential a `model_failure` fault intercepts nothing: the scenario
    observes the product's no-credential refusal, which is a real behaviour and
    is not the behaviour the fault was injected to measure. A 401 that never
    reached the transport cannot be classified as a credential failure, cannot
    trip the breaker, and cannot demonstrate the rule that a credential failure
    trips it on the FIRST occurrence unlike a 429 or a 5xx.

    A scenario cannot supply it either: scenarios are declarative YAML with no
    way to reach process configuration, and giving them one would be giving
    every scenario a way to change what is configured.

    HARNESS.md section 1's guarantee is NOT weakened by this. The guarantee is
    that no model credential is set, and what it protects against is a run
    silently calling a real vendor. This value calls nothing: the routing
    transport is installed first and answers that host from a fixture, and the
    value is removed when the fault unwinds, BEFORE the prohibited-outcome
    probe `a_model_credential_was_configured` is evaluated in the judging
    phase. Both halves are checked: the probe still runs, and it still reads
    the real setting.
    """
    from app.core.config import get_settings  # noqa: PLC0415 -- product import

    settings = get_settings()
    previous = {name: getattr(settings, name, None) for name in attributes}
    for name in attributes:
        object.__setattr__(settings, name, _HARNESS_CREDENTIAL)

    def restore() -> None:
        for name, value in previous.items():
            object.__setattr__(settings, name, value)

    return restore


def _snapshot_vendor_state() -> Callable[[], None]:
    """Capture the product state a served vendor response changes.

    See the module docstring. Both of these outlive the fault that caused them
    and would silently change the next scenario's behaviour.
    """
    from app.services import llm_router  # noqa: PLC0415 -- product import
    from app.services.reliability import vendor_contract  # noqa: PLC0415

    breakers = copy.deepcopy(llm_router._breakers)  # noqa: SLF001
    checked = set(vendor_contract._checked)  # noqa: SLF001

    def restore() -> None:
        llm_router._breakers.clear()  # noqa: SLF001
        llm_router._breakers.update(breakers)  # noqa: SLF001
        vendor_contract._checked.clear()  # noqa: SLF001
        vendor_contract._checked.update(checked)  # noqa: SLF001

    return restore


@contextmanager
def model_failure(kind: str) -> Iterator[FaultSpec]:
    """Serve `kind` from the OpenAI seam for the duration.

    Kinds: rate_limited, server_error, unavailable, credential, timeout,
    malformed, partial. Every one is an authored fixture from
    `tests/fixtures/vendor/openai/` or a named mutation of one, except timeout,
    which is the absence of a response and cannot have a fixture at all.

    The response is served to the router's own client, so what the scenario
    observes is the REAL classification: a 429 is `rate_limit` and retries
    against `retry-after`, a 401 is `credential` and trips the breaker on the
    first occurrence, and a malformed body is a `VendorContractViolation`
    naming the fixture it disagreed with.
    """
    spec = FaultSpec("model_failure", {"kind": kind})
    responder = openai_responder(kind)

    def install() -> Callable[[], None]:
        restore_state = _snapshot_vendor_state()
        undo_route = _install_route(
            _HttpRoute(label=f"model:{kind}", host=OPENAI_HOST, responder=responder)
        )
        # The route goes in FIRST and comes out LAST, so at no instant is a
        # credential configured without the transport that intercepts it.
        restore_keys = _supply_credentials(tuple(SETTINGS_ATTR_FOR_MODEL.values()))

        def undo() -> None:
            restore_keys()
            undo_route()
            restore_state()

        return undo

    with _applied(spec, install) as applied:
        yield applied


@contextmanager
def embedding_failure(kind: str) -> Iterator[FaultSpec]:
    """Serve `kind` from the Voyage seam for the duration.

    Kinds: rate_limited, credential, timeout, malformed. `server_error` and
    `unavailable` are REFUSED with the name of the fixture that would have to be
    authored, because Voyage error bodies were deliberately never written down:
    the client classifies on the status and never parses the body, since an
    embedding request carries a real candidate's resume text.

    Worth knowing what this does NOT reach: with no `VOYAGE_CONTEXT_4` set,
    `embeddings.embed` returns its deterministic dev fallback vectors outside
    production and never opens a connection, so there is nothing for this fault
    to intercept. That branch is the one that hid a non-existent model id for a
    whole phase, and a scenario about embedding failure has to set the key for
    the real path to be the path under test.
    """
    spec = FaultSpec("embedding_failure", {"kind": kind})
    responder = voyage_responder(kind)

    def install() -> Callable[[], None]:
        restore_state = _snapshot_vendor_state()
        undo_route = _install_route(
            _HttpRoute(
                label=f"embedding:{kind}", host=VOYAGE_HOST, responder=responder
            )
        )
        # Same argument as `model_failure`, and this module's own docstring
        # already stated the consequence of not doing it: with no
        # `VOYAGE_CONTEXT_4` set, `embeddings.embed` returns its deterministic
        # dev vectors outside production and never opens a connection, so the
        # fault would intercept nothing and the scenario would measure the
        # fallback that once hid a non-existent model id for a whole phase.
        restore_keys = _supply_credentials(("voyage_context_4",))

        def undo() -> None:
            restore_keys()
            undo_route()
            restore_state()

        return undo

    with _applied(spec, install) as applied:
        yield applied


# -- The Redis seam ----------------------------------------------------------


@contextmanager
def redis_down() -> Iterator[FaultSpec]:
    """Take Redis away at the factory, in both the shapes the product uses it.

    `core/cache._redis()` answers None, which is the state that module already
    has a documented contract for: a cache miss and a dead Redis look identical
    to the caller. `redis.asyncio.from_url` raises, which is what the four
    modules that build their OWN client would meet.

    THE LIMIT, STATED: a module holding an ALREADY BUILT client keeps it.
    `proctoring/state` and `workers/status` cache theirs in a module global, so
    a scenario that exercised them before applying this fault still reaches a
    live client. `cache._redis` is the seam HARNESS.md names and is the one that
    covers `rate_limit`, `auth_sessions` and the cache itself; the rest is
    covered only from a cold process.

    What the scenario asserts is the DIVERGENT behaviour: the cache degrades to
    a miss, `rate_limit` fails open by design, and `auth_sessions` answers 503
    rather than a fail-open check, because an unavailable session store must
    never turn revocation into an allow.
    """
    spec = FaultSpec("redis_down", {})

    def install() -> Callable[[], None]:
        import redis.asyncio as redis_asyncio  # noqa: PLC0415 -- optional import

        from app.core import cache  # noqa: PLC0415 -- product import

        prior_factory = cache._redis  # noqa: SLF001
        prior_from_url = redis_asyncio.from_url

        def no_client() -> Any:
            return None

        def refuse(*args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("harness fault: redis is down")

        cache._redis = no_client  # type: ignore[assignment]  # noqa: SLF001
        redis_asyncio.from_url = refuse  # type: ignore[assignment]

        def undo() -> None:
            cache._redis = prior_factory  # type: ignore[assignment]  # noqa: SLF001
            redis_asyncio.from_url = prior_from_url  # type: ignore[assignment]

        return undo

    with _applied(spec, install) as applied:
        yield applied


class _SlowRedis:
    """A client that sleeps before every operation, then delegates.

    Wraps whatever `cache._redis()` currently returns rather than replacing it,
    so latency composes with an in-memory double or with a real client, and so
    the fault is about LATENCY alone. A slow client that also changed the
    answers would be two faults under one name, and a scenario could not say
    which one the product reacted to.
    """

    def __init__(self, inner: Any, seconds: float) -> None:
        self._inner = inner
        self._seconds = seconds

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if inspect.isasyncgenfunction(attribute):

            async def slow_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
                await asyncio.sleep(self._seconds)
                async for item in attribute(*args, **kwargs):
                    yield item

            return slow_stream
        if inspect.iscoroutinefunction(attribute):

            async def slow_call(*args: Any, **kwargs: Any) -> Any:
                await asyncio.sleep(self._seconds)
                return await attribute(*args, **kwargs)

            return slow_call
        if name == "pipeline":

            def slow_pipeline(*args: Any, **kwargs: Any) -> Any:
                return _SlowPipeline(attribute(*args, **kwargs), self._seconds)

            return slow_pipeline
        return attribute


class _SlowPipeline:
    """The queueing half is synchronous; only `execute` waits.

    That is where the round trip actually happens, so charging the delay to the
    queue calls would put the latency somewhere the wire never spends it.
    """

    def __init__(self, inner: Any, seconds: float) -> None:
        self._inner = inner
        self._seconds = seconds

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if name != "execute":
            def queue(*args: Any, **kwargs: Any) -> "_SlowPipeline":
                attribute(*args, **kwargs)
                return self

            return queue

        async def execute(*args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(self._seconds)
            return await attribute(*args, **kwargs)

        return execute


@contextmanager
def redis_slow(seconds: float) -> Iterator[FaultSpec]:
    """Add `seconds` to every Redis round trip.

    The interesting question this asks is not whether the product is slower. It
    is whether a timeout somewhere else fires first: `cache._redis` builds its
    client with a one second socket timeout, so a delay past that is a
    different failure from a delay under it, and the product should say which
    one it met.
    """
    if seconds < 0:
        raise ValueError(f"redis_slow({seconds}) is not a delay")
    spec = FaultSpec("redis_slow", {"seconds": float(seconds)})

    def install() -> Callable[[], None]:
        from app.core import cache  # noqa: PLC0415 -- product import

        prior_factory = cache._redis  # noqa: SLF001

        def slow_factory() -> Any:
            inner = prior_factory()
            return None if inner is None else _SlowRedis(inner, seconds)

        cache._redis = slow_factory  # type: ignore[assignment]  # noqa: SLF001

        def undo() -> None:
            cache._redis = prior_factory  # type: ignore[assignment]  # noqa: SLF001

        return undo

    with _applied(spec, install) as applied:
        yield applied


# -- The object store seam ---------------------------------------------------


@contextmanager
def object_store_failure(kind: str) -> Iterator[FaultSpec]:
    """Replace the bucket client with one that fails `kind` every time.

    `server_error` fails every operation as `InternalError`, which
    `object_storage` raises `ObjectStorageError` for, naming the code.
    `missing_key` fails only reads, which is the more interesting state: a
    database row pointing at an object the bucket has never heard of, which the
    product meets as a 404 on a reference it believes in.

    The client is swapped at `object_storage._client` rather than through
    `reset_client()`, because unwinding has to put back the exact client that
    was there, not drop whatever the process had built.
    """
    spec = FaultSpec("object_store_failure", {"kind": kind})
    failing = FailingObjectStore(kind)

    def install() -> Callable[[], None]:
        from app.services import object_storage  # noqa: PLC0415 -- product import

        prior = object_storage._client  # noqa: SLF001
        object_storage._client = failing  # noqa: SLF001

        def undo() -> None:
            object_storage._client = prior  # noqa: SLF001

        return undo

    with _applied(spec, install) as applied:
        yield applied


# -- The dispatch seam -------------------------------------------------------


class _RefusingRecord(list):  # type: ignore[type-arg]
    """A recorded-dispatch list that refuses to record.

    A list subclass rather than a patched `dispatch()` so everything before the
    append still runs: `resolve(name)` still refuses a task that is not
    registered, `backend()` still refuses `record` in production, and the wire
    payload is still built. What fails is the handoff itself, which is the
    failure the call sites were written against, several of which catch it and
    degrade visibly.
    """

    def append(self, item: Any) -> None:
        from app.workers.dispatch import DispatchError  # noqa: PLC0415

        raise DispatchError(
            f"harness fault: the dispatch backend refused "
            f"{getattr(item, 'name', item)!r}"
        )


@contextmanager
def dispatch_failure() -> Iterator[FaultSpec]:
    """Make the `record` backend raise instead of recording.

    `dispatch` raises rather than swallowing, by design, and that design is the
    only reason the empty retrieval index was ever visible: a dispatcher that
    degraded would have reported a queue it never wrote to. This fault is how a
    scenario checks that its caller still behaves when the handoff fails.
    """
    spec = FaultSpec("dispatch_failure", {})

    def install() -> Callable[[], None]:
        from app.workers import dispatch  # noqa: PLC0415 -- product import

        prior = dispatch._recorded  # noqa: SLF001
        # Seeded with what is already there so `recorded()` keeps answering
        # truthfully during the fault: the dispatches that DID happen before it
        # was applied are evidence a scenario may still be asserting on.
        dispatch._recorded = _RefusingRecord(prior)  # noqa: SLF001

        def undo() -> None:
            dispatch._recorded = prior  # noqa: SLF001

        return undo

    with _applied(spec, install) as applied:
        yield applied


# -- The clock seam ----------------------------------------------------------


@dataclass(frozen=True)
class _ClockSeam:
    """One product function that answers "what time is it".

    `accepts_override` records the two signatures that exist: most helpers take
    nothing, and `bd_leads._now` and `provider_analytics._now` take a caller
    supplied instant and fall back. The override is HONOURED rather than
    overwritten, because a caller passing an explicit timestamp is passing data,
    not asking the clock.
    """

    module: str
    attribute: str
    accepts_override: bool


#: Every time seam in `app/` that is a named function, which is the complete set
#: a fault can bind without rewriting `datetime` itself.
#:
#: BE EXACT ABOUT THE LIMIT. A module that calls `datetime.now(timezone.utc)`
#: inline is NOT moved by this fault, and neither is `time.monotonic`, which is
#: deliberate: `llm_router` computes its wall clock budget as
#: `time.monotonic() + budget`, so moving it would make every deadline
#: unreachable and turn a clock fault into a router outage. A scenario reasoning
#: about a date reads the seams below; a scenario reasoning about a timeout
#: injects a timeout.
CLOCK_SEAMS: tuple[_ClockSeam, ...] = (
    _ClockSeam("app.api.proctoring", "_now", False),
    _ClockSeam("app.services.agent_actions.ledger", "_now", False),
    _ClockSeam("app.services.approval_fsm", "_now", False),
    _ClockSeam("app.services.bd_leads", "_now", True),
    _ClockSeam("app.services.consent_lifecycle", "utcnow", False),
    _ClockSeam("app.services.projects.pipeline", "_now", False),
    _ClockSeam("app.services.provider_analytics", "_now", True),
    _ClockSeam("app.services.reports.builders", "_now", False),
    _ClockSeam("app.services.swot_analysis", "_now", False),
)

_CLOCK: Clock | None = None


def active_clock() -> Clock | None:
    """The clock a clock fault installed, or None outside one."""
    return _CLOCK


def _bind_clock(clock: Clock) -> Callable[[], None]:
    """Point every seam at `clock`, and return the restore."""
    global _CLOCK
    import importlib  # noqa: PLC0415 -- only needed when a clock fault applies

    prior_clock = _CLOCK
    restores: list[Callable[[], None]] = []

    for seam in CLOCK_SEAMS:
        module = importlib.import_module(seam.module)
        if not hasattr(module, seam.attribute):
            raise AttributeError(
                f"clock seam {seam.module}.{seam.attribute} does not exist. "
                f"The helper was renamed or removed; update CLOCK_SEAMS in the "
                f"same change, because a seam that silently stops being bound "
                f"leaves a scenario reading the real clock while reporting that "
                f"it froze one."
            )
        prior = getattr(module, seam.attribute)

        if seam.accepts_override:
            def frozen_with_override(
                now: datetime | None = None, _clock: Clock = clock
            ) -> datetime:
                return now if now is not None else _clock.now()

            replacement: Callable[..., datetime] = frozen_with_override
        else:
            def frozen(_clock: Clock = clock) -> datetime:
                return _clock.now()

            replacement = frozen

        setattr(module, seam.attribute, replacement)
        restores.append(
            lambda m=module, a=seam.attribute, p=prior: setattr(m, a, p)
        )

    _CLOCK = clock

    def undo() -> None:
        global _CLOCK
        for restore in reversed(restores):
            restore()
        _CLOCK = prior_clock

    return undo


@contextmanager
def clock_at(when: datetime | str) -> Iterator[FaultSpec]:
    """Freeze every product time seam at `when`.

    Accepts an ISO string as well as a datetime, because that is the form a run
    manifest holds: a replay reads its faults back out of JSON, and a fault that
    only accepted a datetime would need a converter at the one call site where
    getting it wrong is least visible.
    """
    instant = datetime.fromisoformat(when) if isinstance(when, str) else when
    clock = Clock(instant)
    spec = FaultSpec("clock_at", {"when": clock.instant.isoformat()})

    with _applied(spec, lambda: _bind_clock(clock)) as applied:
        yield applied


@contextmanager
def clock_advance(seconds: float) -> Iterator[FaultSpec]:
    """Move time forward by `seconds` for the duration.

    Composes with `clock_at`: applied inside one, it advances from that frozen
    instant, and applied alone it advances from the deterministic default. It
    advances a COPY, so leaving the block puts the outer clock back exactly
    where it was rather than leaving it where the inner fault pushed it.
    """
    spec = FaultSpec("clock_advance", {"seconds": float(seconds)})
    base = _CLOCK
    moved = Clock(base.instant if base is not None else None)
    moved.advance(float(seconds))

    with _applied(spec, lambda: _bind_clock(moved)) as applied:
        yield applied


# -- Composition -------------------------------------------------------------

_REGISTRY: Mapping[str, Callable[..., Any]] = {
    "model_failure": model_failure,
    "embedding_failure": embedding_failure,
    "redis_down": redis_down,
    "redis_slow": redis_slow,
    "object_store_failure": object_store_failure,
    "dispatch_failure": dispatch_failure,
    "clock_at": clock_at,
    "clock_advance": clock_advance,
}


def registered() -> tuple[str, ...]:
    """Every fault name, for a manifest reader and for the CLI."""
    return tuple(sorted(_REGISTRY))


@contextmanager
def apply(specs: Sequence[Any]) -> Iterator[tuple[FaultSpec, ...]]:
    """Apply several faults, unwinding in reverse order whatever happens.

    An `ExitStack` rather than nested `with` statements, because the list comes
    from a scenario file and is not known at the call site. The stack is also
    what makes the failure path correct: a fault that raises while being
    applied does not leave the ones before it behind, and a body that raises
    still unwinds all of them innermost first.
    """
    with ExitStack() as stack:
        applied: list[FaultSpec] = []
        for value in specs:
            spec = FaultSpec.coerce(value)
            factory = _REGISTRY[spec.name]
            stack.enter_context(factory(**spec.params))
            applied.append(spec)
        yield tuple(applied)
