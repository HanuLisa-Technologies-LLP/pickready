"""Make the backend package root importable when pytest runs from anywhere."""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# ── Where the suite's infrastructure lives ───────────────────────────────────
#
# `docker-compose.test.yml` is this suite's declared infrastructure, and these
# are its addresses. `setdefault`, never assignment: CI exports its own
# DATABASE_URL and `scripts/test.sh` exports these same values explicitly, and
# an override here would silently point a deliberate configuration somewhere
# else.
#
# WHY THIS IS NOT `localhost:5432`. `app/core/config.py` defaults the DSN to the
# conventional port, and on the workstation this was written on a native Windows
# `postgresql-x64-13` service was already listening there. Docker's published
# port bound alongside it and lost, so a plain `pytest` reached PostgreSQL 13
# with a password nobody had, and 71 integration tests answered "no database
# reachable" and reported SKIPPED for months. The suite was green while
# `POST /jobs/{id}/apply` was refused by a CHECK constraint for every candidate
# on every tenant. A default that points at the stack this repository ships is
# the difference between a plain `pytest` proving something and proving nothing.
#
# Deliberately NOT defaulted: S3_TEST_ENDPOINT_URL. Setting it makes the storage
# tests demand a running MinIO and FAIL when it is absent, which is right for the
# canonical run and wrong for somebody running one file; unset, they use `moto`.
# `scripts/test.sh` sets it, so the graded run always exercises a real S3 server.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://readypick_test:readypick_test@127.0.0.1:55432/readypick_test",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6381/0")
# Not a secret: it signs tokens for one test run against a tmpfs database. Long
# enough to clear the 32-byte HMAC recommendation so a run is not buried in
# InsecureKeyLengthWarning.
os.environ.setdefault("JWT_SECRET", "readypick-test-suite-signing-key-not-a-secret")

# Background work is RECORDED, not run. There is no Lambda in front of a test
# run, and running the tasks in-process would make every route test that
# happens to enqueue one depend on a model provider, an SMTP server and a
# minute of wall clock.
#
# This is the honest version of what the Celery suite did by accident: it
# published to a Redis broker with no worker behind it, so the message went
# nowhere and nothing said so. `record` goes nowhere too, and keeps the list,
# which is what lets a test assert WHICH task a route dispatched instead of
# monkeypatching the transport and proving only that the call site ran.
os.environ.setdefault("TASK_DISPATCH_BACKEND", "record")

import pytest  # noqa: E402  -- must follow the sys.path insert

#: The skip gate (spec-doc6 3.3). It has to be a PLUGIN and not a test, because
#: the comparison it makes is against the whole session and a test function
#: cannot observe the session that contains it. Registering it here is the only
#: place pytest will pick up its `pytest_sessionfinish` hook. The module also
#: holds ordinary tests, which run normally; a plugin and a test module are not
#: mutually exclusive.
pytest_plugins = ("tests.test_skip_inventory",)


@pytest.fixture(autouse=True)
def _clear_recorded_dispatches():
    """Each test sees only its own dispatches.

    The recorder is process-global, so without this a test asserting "exactly
    one email was dispatched" passes or fails depending on what ran before it,
    which is the order-dependent failure that reproduces on CI and not locally.
    """
    from app.workers import dispatch

    dispatch.clear_recorded()
    yield
    dispatch.clear_recorded()


@pytest.fixture(autouse=True)
def _reset_provider_breaker():
    """Clear the router's provider-level write-off between tests.

    It is process-global and time-based, so a test that provokes a 402 leaves
    the whole provider skipped for fifteen minutes -- long enough to reach every
    later test in the session. That failure is order-dependent, so it reproduces
    on CI and not locally, which is the expensive kind.
    """
    from app.services import llm_router

    llm_router.clear_provider_breaker()
    yield
    llm_router.clear_provider_breaker()
