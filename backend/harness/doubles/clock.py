"""One controllable clock, replacing the hand rolled ones scattered in tests.

HARNESS.md section 4 lists clock movement as a fault and names this module as
"one implementation replacing the two hand rolled `FakeClock` classes". Those
two lived in the retired code-login tests (deleted 2026-09-24), were
byte-for-byte identical to each other, and each answers exactly one question:
what does `time.time()` say. That was enough for a fixed window counter and is
not enough for a harness, which has to move a DATE as well as a duration and
has to do it without the two disagreeing.

WALL TIME AND MONOTONIC TIME ARE SEPARATE, AND THAT IS THE POINT
-----------------------------------------------------------------
`set()` may move wall time BACKWARDS, because a scenario that wants to observe
a 30 day posting window from the inside has to start before it opened. A
monotonic reading may never move backwards, by definition, and the product
relies on that: `llm_router` computes its wall clock budget as
`time.monotonic() + budget`, so a monotonic clock that jumped back would make
every deadline unreachable and every attempt look affordable. So `set()` moves
the instant and leaves the monotonic counter exactly where it was, and only
`advance()` moves both. A clock that folded the two together would inject a
bug rather than a fault.

CALLABLE, BECAUSE THAT IS THE SHAPE THE PRODUCT ALREADY TAKES
--------------------------------------------------------------
A `clock=` seam takes a zero-argument callable and calls it for epoch seconds
(the retired code-login limiter was the first such seam; it was deleted on
2026-09-24). This class is callable with that exact contract, so it drops into
such a seam without an adapter, which is what makes it a replacement rather
than a third implementation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: The default instant, deliberately a fixed date rather than "now".
#:
#: A double that starts at the real current time makes every scenario that uses
#: it reproducible only on the day it was written, which is precisely the
#: property replay exists to provide (HARNESS.md section 6). Chosen after every
#: dated provenance document in the repository so a scenario's timestamps never
#: read as predating the code that produced them.
DEFAULT_INSTANT = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)

#: Where the monotonic counter starts. Arbitrary and non-zero: a monotonic
#: reading is only ever meaningful as a difference, and starting at zero makes
#: an uninitialised float look like a legitimate reading.
_MONOTONIC_ORIGIN = 10_000.0


class Clock:
    """A clock a test or a scenario moves by hand.

    Not thread safe, and deliberately so. Adding a lock would say the harness
    expects concurrent mutation of time, which no scenario does and which would
    make a replay's ordering depend on scheduling.
    """

    __slots__ = ("_instant", "_monotonic")

    def __init__(self, at: datetime | None = None) -> None:
        self._instant = _as_utc(at) if at is not None else DEFAULT_INSTANT
        self._monotonic = _MONOTONIC_ORIGIN

    # ── Reading ──────────────────────────────────────────────────────────────

    @property
    def instant(self) -> datetime:
        """The current wall time, always timezone aware and always UTC."""
        return self._instant

    def now(self) -> datetime:
        """Drop-in for `datetime.now(timezone.utc)`, which is the one form of
        the call this codebase uses: every `_now()` helper in `app/` spells it
        that way, and a naive datetime here would move a 30 day boundary by
        hours the first time it met a stored UTC column."""
        return self._instant

    def time(self) -> float:
        """Drop-in for `time.time()`: epoch seconds."""
        return self._instant.timestamp()

    def monotonic(self) -> float:
        """Drop-in for `time.monotonic()`. See the module docstring for why
        this is not derived from the instant."""
        return self._monotonic

    def __call__(self) -> float:
        """Epoch seconds, so this instance IS a `clock=` argument."""
        return self.time()

    # ── Moving ───────────────────────────────────────────────────────────────

    def advance(self, delta: timedelta | float) -> None:
        """Move forward by `delta`, given as a timedelta or as seconds.

        Both forms are accepted because both are already written here: the
        `FakeClock` classes this replaces advance by a float, and a scenario
        reasoning about a posting window advances by days. Refusing one of them
        would leave the caller converting at the call site, which is where a
        unit error goes unnoticed.

        Refuses a backwards step. Going back in time is `set()`, which is an
        explicit act; an `advance(-3600)` is almost always an arithmetic error
        in the caller, and honouring it silently would move a monotonic reading
        backwards.
        """
        seconds = delta.total_seconds() if isinstance(delta, timedelta) else float(delta)
        if seconds < 0:
            raise ValueError(
                f"advance({seconds}) would move a monotonic reading backwards. "
                f"Use set() to move wall time to an earlier instant."
            )
        self._instant = self._instant + timedelta(seconds=seconds)
        self._monotonic += seconds

    def set(self, at: datetime) -> None:
        """Move wall time to `at`, leaving the monotonic counter untouched."""
        self._instant = _as_utc(at)

    def __repr__(self) -> str:
        return f"Clock(at={self._instant.isoformat()})"


def _as_utc(value: datetime) -> datetime:
    """Normalise to an aware UTC datetime.

    A naive value is read as UTC rather than as local time, which is the same
    reading `provider_analytics._now` states its reason for: every timestamp in
    this database is UTC, and interpreting a naive one locally moves a boundary
    by hours on the workstation and by nothing on the deployed host, so the
    disagreement only ever shows up somewhere it cannot be reproduced.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
