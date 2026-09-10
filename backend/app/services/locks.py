"""One run at a time, across processes. The lock this codebase did not have.

A tree-wide search for `pg_advisory_lock`, `FOR UPDATE` and `with_for_update`
returned nothing before this module: every concurrency guarantee in the product
rested either on a UNIQUE constraint refusing the second write, or on nothing.

WHY `coalescing.py` DOES NOT COVER THIS
-----------------------------------------
It says so itself, in its own docstring: "IN PROCESS, NOT DISTRIBUTED, AND THAT
IS THE WHOLE DESIGN ... a plain dict of asyncio tasks that cannot outlive the
process holding it." It removes N concurrent calls inside ONE process, which is
the common waste. The duplicate this module prevents is not in one process at
all: `Route.ECS` starts one Fargate task per dispatch, so two dispatches for one
application are two containers that share nothing but the database.

WHAT A UNIQUE CONSTRAINT ALREADY BUYS, AND WHAT IT DOES NOT
-------------------------------------------------------------
`uq_functional_report_link` means two concurrent scoring runs cannot produce two
reports: the second INSERT is refused. That is a real protection and this module
does not replace it. Three things survive it, and they are the reason this
exists:

  1. BOTH RUNS SPEND THE WHOLE CHAIN. Miti's five evaluators and Siddhi's
     synthesis are model calls measured in minutes. The constraint fires at
     COMMIT, after every one of them has been paid for.
  2. THE LOSER RAISES, and `max_attempts=2` then runs the entire chain a second
     time. One duplicate dispatch costs up to three full scoring runs.
  3. THE RETRY OVERWRITES A REPORT SOMEBODY MAY BE READING. On the retry the
     row now exists, so the writer takes the UPDATE branch and rewrites a
     report that was already delivered. `claude.md` states reports are
     immutable and that a retake writes a NEW report alongside the old one; an
     in-place rewrite driven by a race is that rule failing silently.

WHY `try` AND NEVER A WAIT
---------------------------
`pg_try_advisory_xact_lock` returns immediately. A waiting lock would hold a
connection for the minutes the other run takes, against a `db.t4g.micro` whose
connection budget is already the tightest resource in this deployment, and it
would do it to eventually perform work that had just been performed. The right
answer for the second caller is to RETURN, because the first caller is doing
exactly the work it came to do.

WHY THE TRANSACTION-SCOPED VARIANT
------------------------------------
`pg_try_advisory_xact_lock` releases on COMMIT or ROLLBACK, with no `finally` to
forget and no leak when a container is killed mid-run. The session-scoped
variant needs an explicit unlock, and a task that dies between lock and unlock
leaves the application unscorable until the connection is reaped.

It costs no extra connection: the caller is already inside a transaction that
stays open for the whole task, so the lock is held on a connection the run was
holding anyway.

THE KEY COMES FROM STABLE LOGICAL INPUTS
------------------------------------------
`claude.md`: "An idempotency key comes from stable logical inputs, never a
timestamp and never a per-attempt UUID." The same rule applies here for the same
reason: a key that varied per attempt would let a retry take a lock the original
still holds, which is a lock that never excludes anything.

The namespace is part of the hash so two unrelated features cannot collide on
one 64-bit space by both keying on the same application id.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: Namespaces. Named constants rather than free strings at the call site: these
#: are hashed into the key, so a typo does not fail loudly, it silently takes a
#: DIFFERENT lock and excludes nobody.
SCORING = "functional_assessment.scoring"
#: One matching run per JOB. Same reasoning as SCORING one level up: five UI
#: and pipeline call sites dispatch `pickready.run_matching`, Route.ECS gives
#: each its own container, and two staff members clicking "Run AI matching"
#: in the same minute used to buy two full runs of the model chain writing
#: the same rows. The subject is the job id.
MATCHING = "matching.run"

#: Postgres advisory lock keys are signed 64-bit. BLAKE2b rather than Python's
#: `hash()`, which is salted per process by default: two containers would
#: compute different keys for the same application and neither would ever see
#: the other's lock. That failure is invisible in every single-process test.
_DIGEST_BYTES = 8
_SIGNED_64_OFFSET = 1 << 63


def advisory_key(namespace: str, subject: str | uuid.UUID) -> int:
    """The stable signed 64-bit key for (namespace, subject).

    Deterministic across processes, releases and Python versions, which is the
    entire requirement: a lock key that is not reproducible on another host is
    not a lock.
    """
    digest = hashlib.blake2b(
        f"{namespace}:{subject}".encode(), digest_size=_DIGEST_BYTES
    ).digest()
    return int.from_bytes(digest, "big", signed=False) - _SIGNED_64_OFFSET


async def try_advisory_lock(
    session: AsyncSession, namespace: str, subject: str | uuid.UUID
) -> bool:
    """Take the lock if it is free. True when THIS caller now holds it.

    False means another transaction holds it, which for every caller in this
    product means another run is already doing the work.

    The lock is bound to the transaction `session` is currently in and is
    released by its COMMIT or ROLLBACK. A caller that commits mid-task releases
    it early, which is why the one caller that exists commits once, at the end.
    """
    key = advisory_key(namespace, subject)
    return bool(
        (
            await session.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}
            )
        ).scalar()
    )


@asynccontextmanager
async def single_run(
    session: AsyncSession, namespace: str, subject: str | uuid.UUID
) -> AsyncIterator[bool]:
    """`async with single_run(...) as acquired:` -- and CHECK `acquired`.

    Yields the boolean rather than raising or skipping the body, deliberately.
    A context manager that silently skipped its body would make "the work ran"
    and "the work was already running" indistinguishable at the call site, and
    this platform's rule is that a degradation is RECORDED, never silent. The
    caller decides what to log and what to return.
    """
    acquired = await try_advisory_lock(session, namespace, subject)
    if not acquired:
        logger.info(
            "locks.not_acquired namespace=%s subject=%s another run holds it",
            namespace,
            subject,
        )
    yield acquired
