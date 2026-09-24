"""One in-memory Redis double, covering the operations the product issues.

WHAT IT IS SCOPED TO, AND HOW THAT SCOPE WAS DECIDED
------------------------------------------------------
By reading the callers rather than by copying a vendor's command list. Five
modules reach Redis through `core/cache._redis()` or build a client the same
way, and between them they issue exactly the commands implemented below:

    services/rate_limit       incr, expire, ttl
    services/auth_sessions    pipeline(hset/expire/sadd/delete/srem), smembers,
                              eval (two Lua scripts)
    workers/status            get, set(ex=)
    services/proctoring/state get, set(nx=, ex=), incr, incrby, expire, delete,
                              exists, scan
    core/cache                get, set(ex=), delete, scan_iter
    api/health                ping, aclose

Anything outside that set is ABSENT rather than approximated. A double that
answers a command the product does not issue is a claim nobody checked, and a
double that answers one it issues WRONGLY is worse, because the scenario passes.

DECODED RESPONSES, BECAUSE THE CLIENT IS BUILT THAT WAY
---------------------------------------------------------
`cache._redis()` builds its client with `decode_responses=True`, so every read
in the product is a `str`. This double stores `str` and stringifies on write for
the same reason real Redis does: `proctoring/state` writes `int(mirrored)` and
reads it back expecting text it can `int()`. A double that handed back the
Python object it was given would let a test pass on a round trip the wire cannot
perform.

THE LUA SCRIPTS ARE EMULATED BY NAME, NOT INTERPRETED
-------------------------------------------------------
There is no Lua interpreter here and there will not be one. `auth_sessions`
ships two scripts and this module reimplements those two in Python, recognising
each by comparing the script text it was handed against the product's own
constant. Any other script raises, loudly, naming what it was asked to run.

That comparison is the load bearing half. If somebody edits `_TOUCH` or
`_ROTATE`, the double stops recognising it and REFUSES, instead of quietly
running yesterday's semantics against today's script: a session store that
answers a rotation with the wrong token is exactly the failure the rotation
grace window exists to prevent, and it would surface as a flaky logout.
"""
from __future__ import annotations

import fnmatch
from typing import Any, AsyncIterator, Iterable, Mapping

from harness.doubles.clock import Clock


class RedisDoubleError(RuntimeError):
    """The double was asked for something it cannot answer faithfully.

    Its own type so a scenario can tell "the product issued a command this
    double does not implement" from "the product's own code raised". The first
    is a gap in the harness and is fixed here; the second is a finding.
    """


class WrongTypeError(RedisDoubleError):
    """A key holding one Redis type was addressed as another.

    Real Redis answers WRONGTYPE for this. Nothing in the product should ever
    provoke it, so reaching it means two features are sharing a key name, which
    is worth an exception rather than a coerced answer.
    """


#: What TTL reports for a key that exists and carries no deadline.
_NO_EXPIRY = -1

#: What TTL reports for a key that is not there at all. A different answer from
#: the one above, and `rate_limit` reads them differently, so they are not
#: collapsed.
_NO_KEY = -2


class InMemoryRedis:
    """Enough of an async Redis client to drive this product.

    Takes a `Clock` so key expiry is controllable: a scenario proving that a
    session dies on the 30 minute idle deadline has to cross that deadline
    without waiting it out, and a double reading the wall clock would make that
    test either slow or a sleep nobody trusts.
    """

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock if clock is not None else Clock()
        self._strings: dict[str, str] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._sets: dict[str, set[str]] = {}
        self._expiry: dict[str, float] = {}
        self.closed = False

    @property
    def clock(self) -> Clock:
        return self._clock

    # -- Expiry, which every read goes through --------------------------------

    def _expired(self, key: str) -> bool:
        deadline = self._expiry.get(key)
        return deadline is not None and self._clock.time() >= deadline

    def _reap(self, key: str) -> None:
        """Drop `key` if its deadline has passed.

        Lazy rather than swept, which is what real Redis does and what this
        double has no choice about: the harness clock does not tick on its own,
        so there is nothing for a sweep to run on.
        """
        if self._expired(key):
            self._strings.pop(key, None)
            self._hashes.pop(key, None)
            self._sets.pop(key, None)
            self._expiry.pop(key, None)

    def _present(self, key: str) -> bool:
        self._reap(key)
        return key in self._strings or key in self._hashes or key in self._sets

    def _live_keys(self) -> list[str]:
        for key in sorted(set(self._strings) | set(self._hashes) | set(self._sets)):
            self._reap(key)
        return sorted(set(self._strings) | set(self._hashes) | set(self._sets))

    def _claim(self, key: str, kind: str) -> None:
        """Refuse a key that already holds a different Redis type."""
        holders: dict[str, dict[str, Any]] = {
            "string": self._strings,
            "hash": self._hashes,
            "set": self._sets,
        }
        for name, store in holders.items():
            if name != kind and key in store:
                raise WrongTypeError(
                    f"key {key!r} holds a {name} and was addressed as a {kind}"
                )

    # -- Strings and counters -------------------------------------------------

    async def get(self, key: str) -> str | None:
        self._reap(key)
        self._claim(key, "string")
        return self._strings.get(key)

    async def set(
        self,
        key: str,
        value: Any,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        """SET, with the two modifiers the product uses.

        Returns None rather than False when `nx` loses, because that is what
        redis-py returns and `proctoring/state._once` reads the result for
        truthiness: False behaves identically today and would diverge the moment
        somebody writes `is None`.
        """
        self._reap(key)
        self._claim(key, "string")
        if nx and key in self._strings:
            return None
        self._strings[key] = str(value)
        if ex is None:
            self._expiry.pop(key, None)
        else:
            self._expiry[key] = self._clock.time() + float(ex)
        return True

    async def incr(self, key: str) -> int:
        return await self.incrby(key, 1)

    async def incrby(self, key: str, amount: int) -> int:
        """INCRBY, which CREATES a missing key at zero and keeps any TTL.

        Both halves matter. The fixed window counter in `rate_limit` depends on
        the create, and the fact that INCRBY does not touch an existing expiry
        is why that module re-reads `ttl` and re-applies `expire` when it finds
        -1. A double that reset the deadline on every increment would make a
        window that never closes look correct.
        """
        self._reap(key)
        self._claim(key, "string")
        current = self._strings.get(key, "0")
        try:
            total = int(current) + int(amount)
        except ValueError as exc:
            raise WrongTypeError(
                f"key {key!r} holds {current!r}, which is not an integer"
            ) from exc
        self._strings[key] = str(total)
        return total

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            present = self._present(key)
            self._strings.pop(key, None)
            self._hashes.pop(key, None)
            self._sets.pop(key, None)
            self._expiry.pop(key, None)
            removed += int(present)
        return removed

    async def exists(self, *keys: str) -> int:
        return sum(int(self._present(key)) for key in keys)

    # -- Expiry commands ------------------------------------------------------

    async def expire(self, key: str, seconds: int, nx: bool = False) -> bool:
        """EXPIRE, with the `nx` modifier redis-py accepts.

        `nx` sets a deadline only when the key has none, which is how a fixed
        window is pinned to its FIRST request rather than sliding forward on
        every later one.
        """
        if not self._present(key):
            return False
        if nx and key in self._expiry:
            return False
        self._expiry[key] = self._clock.time() + float(seconds)
        return True

    async def ttl(self, key: str) -> int:
        """Seconds remaining: -2 for a missing key, -1 for one with no deadline.

        Rounded UP, matching Redis, so a key with half a second left reports 1
        rather than 0. A 0 reads to `rate_limit` as a window it should re-arm,
        which would extend a window that was about to close.
        """
        if not self._present(key):
            return _NO_KEY
        deadline = self._expiry.get(key)
        if deadline is None:
            return _NO_EXPIRY
        remaining_ms = int((deadline - self._clock.time()) * 1000)
        return max(0, -(-remaining_ms // 1000))

    # -- Hashes ---------------------------------------------------------------

    async def hset(
        self, key: str, mapping: Mapping[str, Any] | None = None
    ) -> int:
        """HSET, `mapping=` form only.

        That is the only form `auth_sessions.create` uses. A field/value
        positional form would be a second path with no caller and therefore no
        test, which is how a double acquires behaviour nobody has checked.
        """
        if mapping is None:
            raise RedisDoubleError(
                "hset takes mapping=; the field/value form has no caller in "
                "this product and is deliberately not implemented"
            )
        self._reap(key)
        self._claim(key, "hash")
        bucket = self._hashes.setdefault(key, {})
        added = sum(1 for field in mapping if field not in bucket)
        for field, value in mapping.items():
            bucket[field] = str(value)
        return added

    async def hget(self, key: str, field: str) -> str | None:
        self._reap(key)
        self._claim(key, "hash")
        return self._hashes.get(key, {}).get(field)

    # -- Sets -----------------------------------------------------------------

    async def sadd(self, key: str, *members: Any) -> int:
        self._reap(key)
        self._claim(key, "set")
        bucket = self._sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(str(member) for member in members)
        return len(bucket) - before

    async def srem(self, key: str, *members: Any) -> int:
        self._reap(key)
        self._claim(key, "set")
        bucket = self._sets.get(key)
        if bucket is None:
            return 0
        before = len(bucket)
        bucket.difference_update(str(member) for member in members)
        if not bucket:
            self._sets.pop(key, None)
            self._expiry.pop(key, None)
        return before - len(bucket)

    async def smembers(self, key: str) -> set[str]:
        self._reap(key)
        self._claim(key, "set")
        return set(self._sets.get(key, set()))

    # -- Scanning -------------------------------------------------------------

    async def scan(
        self, cursor: int = 0, match: str | None = None, count: int | None = None
    ) -> tuple[int, list[str]]:
        """SCAN, returning the whole keyspace in ONE round trip.

        Paginating would emulate a guarantee real SCAN does not make: the cursor
        protocol promises only that a key present for the whole scan is returned
        at least once, and `count` is advisory to the server. What IS reproduced
        faithfully is the cursor sentinel, zero meaning complete, because that
        is the value `proctoring/state` loops on and an always-nonzero cursor
        would spin for ever.
        """
        if cursor != 0:
            return 0, []
        keys = self._live_keys()
        if match is not None:
            keys = [key for key in keys if fnmatch.fnmatchcase(key, match)]
        return 0, keys

    async def scan_iter(
        self, match: str | None = None, count: int | None = None
    ) -> AsyncIterator[str]:
        _, keys = await self.scan(0, match=match, count=count)
        for key in keys:
            yield key

    # -- Connection -----------------------------------------------------------

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self.closed = True

    # -- Pipelines ------------------------------------------------------------

    def pipeline(self, transaction: bool = True) -> "_Pipeline":
        """A queued pipeline.

        `transaction` is accepted and ignored, which is honest for a
        single-process double: every command runs in order with nothing else
        interleaved, so MULTI/EXEC atomicity is already the behaviour. What is
        NOT emulated is a transaction's failure semantics, and nothing in the
        product inspects them.
        """
        return _Pipeline(self)

    # -- Lua ------------------------------------------------------------------

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        """Run one of the two scripts this double has been taught.

        Recognised by comparing `script` against the product's own constants, so
        an edit to either script disables the emulation rather than silently
        outliving it. See the module docstring.

        Arguments are stringified first, because that is what the Redis
        protocol does to every KEY and ARGV: `_ROTATE` compares `current ==
        ARGV[2]` as text and calls `tonumber` explicitly where it wants a
        number. A double comparing a Python int to a str would answer "no
        match" for a rotation that really matched.
        """
        from app.services import auth_sessions  # noqa: PLC0415 -- see docstring

        keys = [str(item) for item in keys_and_args[:numkeys]]
        args = [str(item) for item in keys_and_args[numkeys:]]
        normalised = script.strip()
        if normalised == auth_sessions._TOUCH.strip():  # noqa: SLF001
            return await self._eval_touch(keys, args)
        if normalised == auth_sessions._ROTATE.strip():  # noqa: SLF001
            return await self._eval_rotate(keys, args)
        raise RedisDoubleError(
            "this double emulates only the two Lua scripts in "
            "app/services/auth_sessions.py, recognised by their exact text, and "
            "was handed one it does not recognise. If _TOUCH or _ROTATE was "
            "edited, re-derive the Python emulation in harness/doubles/redis.py "
            "in the same change; if this is a new script, emulate it here "
            "rather than letting the double answer.\n"
            f"--- script handed to eval ---\n{normalised}"
        )

    async def _eval_touch(self, keys: list[str], args: list[str]) -> int:
        """`auth_sessions._TOUCH`: prove the session belongs to this user, then
        push BOTH deadlines out.

        The index key is touched alongside the session because a user's session
        index expiring out from under live sessions would make `revoke_all` a
        silent no-op, which is a revocation that reports success.
        """
        session_key, index_key = keys[0], keys[1]
        user, idle = args[0], args[1]
        owner = await self.hget(session_key, "user")
        if owner is None or owner != user:
            return 0
        await self.expire(session_key, int(idle))
        await self.expire(index_key, int(idle))
        return 1

    async def _eval_rotate(self, keys: list[str], args: list[str]) -> str | None:
        """`auth_sessions._ROTATE`: rotate the refresh token, or hand a racing
        tab the winner's token while the grace window is still open.

        The deadlines move only when ARGV[8] is `'1'` (the refresh came from
        real user activity); any other value rotates and leaves them alone,
        exactly as the Lua compares it.
        """
        session_key, index_key = keys[0], keys[1]
        user, old_jti, new_jti, new_token, until, now, idle, touch = (
            args[0], args[1], args[2], args[3], args[4], args[5], args[6],
            args[7],
        )
        owner = await self.hget(session_key, "user")
        if owner is None or owner != user:
            return None

        current = await self.hget(session_key, "jti")
        token: str | None
        if current == old_jti:
            await self.hset(
                session_key,
                mapping={
                    "previous_jti": current,
                    "previous_until": until,
                    "jti": new_jti,
                    "token": new_token,
                },
            )
            token = new_token
        else:
            previous_jti = await self.hget(session_key, "previous_jti")
            previous_until = await self.hget(session_key, "previous_until")
            if previous_until is None:
                # `tonumber(false)` is a Lua error, so real Redis fails the
                # whole script here rather than reading the field as zero. Said
                # out loud because `create` always writes this field, so its
                # absence means the hash was built by something else.
                raise RedisDoubleError(
                    f"session hash {session_key!r} carries no 'previous_until'; "
                    f"the real script would error rather than rotate"
                )
            if previous_jti == old_jti and float(previous_until) >= float(now):
                token = await self.hget(session_key, "token")
            else:
                return None

        if touch == "1":
            await self.expire(session_key, int(idle))
            await self.expire(index_key, int(idle))
        return token


class _Pipeline:
    """Commands queued now and run, in order, on `execute()`.

    The command methods are SYNCHRONOUS and return self, which is what
    redis-py's asyncio pipeline does and what `auth_sessions` is written
    against: it queues four commands without an await and then awaits once.

    Every supported command is written out rather than forwarded through
    `__getattr__`. A forwarding pipeline accepts a command the underlying double
    cannot run and fails at `execute()`, one layer away from the call that was
    actually wrong.
    """

    def __init__(self, client: InMemoryRedis) -> None:
        self._client = client
        self._queued: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _queue(self, command: str, *args: Any, **kwargs: Any) -> "_Pipeline":
        self._queued.append((command, args, kwargs))
        return self

    def set(
        self, key: str, value: Any, ex: int | None = None, nx: bool = False
    ) -> "_Pipeline":
        return self._queue("set", key, value, ex=ex, nx=nx)

    def get(self, key: str) -> "_Pipeline":
        return self._queue("get", key)

    def incr(self, key: str) -> "_Pipeline":
        return self._queue("incr", key)

    def expire(self, key: str, seconds: int, nx: bool = False) -> "_Pipeline":
        return self._queue("expire", key, seconds, nx=nx)

    def delete(self, *keys: str) -> "_Pipeline":
        return self._queue("delete", *keys)

    def hset(self, key: str, mapping: Mapping[str, Any]) -> "_Pipeline":
        return self._queue("hset", key, mapping=mapping)

    def sadd(self, key: str, *members: Any) -> "_Pipeline":
        return self._queue("sadd", key, *members)

    def srem(self, key: str, *members: Any) -> "_Pipeline":
        return self._queue("srem", key, *members)

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        queued, self._queued = self._queued, []
        for command, args, kwargs in queued:
            method = getattr(self._client, command)
            results.append(await method(*args, **kwargs))
        return results

    def __len__(self) -> int:
        return len(self._queued)


#: The commands this double implements, as DATA, so a test can compare the list
#: against what the product actually issues rather than trusting the docstring
#: above to have kept up with it.
SUPPORTED_COMMANDS: frozenset[str] = frozenset(
    {
        "get", "set", "incr", "incrby", "delete", "exists",
        "expire", "ttl",
        "hset", "hget",
        "sadd", "srem", "smembers",
        "scan", "scan_iter",
        "ping", "aclose",
        "pipeline", "eval",
    }
)


def supported(client: object) -> Iterable[str]:
    """The commands `client` answers, for a parity check against real Redis."""
    return sorted(name for name in SUPPORTED_COMMANDS if hasattr(client, name))
