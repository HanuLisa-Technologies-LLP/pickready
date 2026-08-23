"""Make the backend package root importable when pytest runs from anywhere."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402  -- must follow the sys.path insert


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
