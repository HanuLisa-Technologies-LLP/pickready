"""Test doubles the harness injects at the product's real seams.

ONE IMPLEMENTATION PER CONCEPT, WHICH IS WHY THIS PACKAGE EXISTS AT ALL
------------------------------------------------------------------------
Rule 5. Before this package there were two identical `FakeClock` classes in
`tests/`, two unrelated `_FakeRedis` classes implementing different subsets of
Redis, and no object store double at all. Each was written for the one test that
needed it, each was correct for that test, and none of them could be composed
into a scenario. The cost of that duplication is not the lines: it is that four
statements about how Redis behaves can disagree with each other and with Redis,
and nothing fails when they do.

There is one clock, one Redis, one object store and one vendor transport layer
here. Where a double cannot faithfully answer something, it RAISES rather than
guessing, which is the same rule the product follows about silent fallbacks: a
double that returns a plausible wrong answer turns a harness pass into a
statement about the harness.

Nothing here reaches the network, the database or a model. A double is
constructed, handed to `harness.faults`, and torn down.
"""
from __future__ import annotations

from harness.doubles.clock import DEFAULT_INSTANT, Clock
from harness.doubles.redis import (
    SUPPORTED_COMMANDS,
    InMemoryRedis,
    RedisDoubleError,
    WrongTypeError,
)
from harness.doubles.storage import (
    FAILURE_CODES,
    FailingObjectStore,
    InMemoryObjectStore,
)
from harness.doubles.vendor import (
    OPENAI_FAULT_KINDS,
    OPENAI_HOST,
    VOYAGE_FAULT_KINDS,
    VOYAGE_HOST,
    FixtureMissing,
    VendorFixture,
    load_fixture,
    openai_responder,
    voyage_responder,
)

__all__ = [
    "Clock",
    "DEFAULT_INSTANT",
    "FAILURE_CODES",
    "FailingObjectStore",
    "FixtureMissing",
    "InMemoryObjectStore",
    "InMemoryRedis",
    "OPENAI_FAULT_KINDS",
    "OPENAI_HOST",
    "RedisDoubleError",
    "SUPPORTED_COMMANDS",
    "VOYAGE_FAULT_KINDS",
    "VOYAGE_HOST",
    "VendorFixture",
    "WrongTypeError",
    "load_fixture",
    "openai_responder",
    "voyage_responder",
]
