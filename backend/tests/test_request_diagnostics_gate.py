"""The request-diagnostics middleware must default to OFF and stay off.

WHAT THIS PREVENTS, WHICH ALREADY HAPPENED
--------------------------------------------
`app/main.py` guarded the timing middleware with `if not
get_settings().is_production`. `is_production` is `environment ==
"production"`, and the deployment that serves readypick.ai runs
`ENVIRONMENT=pilot`. So the gate was open on the one environment it was written
to close: every API response carried `Server-Timing` with a SQL duration and
`X-Query-Count`, and `X-Debug-SQL: 1` was accepted from anonymous callers,
while `instrumentation.py` stated in its own docstring that the middleware was
"unreachable in production because [it] is not installed there".

Grepping the tests for `timing_middleware`, `Server-Timing` or `X-Query-Count`
returned NOTHING before this file, which is how it shipped and stayed.

THE THIRD DEFECT FROM ONE ROOT CAUSE
--------------------------------------
The unsigned Razorpay webhook and the exposed API documentation were the first
two, and both were repaired at their call site. This test exists so the repair
is a PROPERTY rather than a third individual patch: the gate must be an
explicit deployment setting that defaults to safe, and must not be re-derived
from any release-channel name.

`serves_over_https` is not a valid substitute either, and the test says so
explicitly, because it is TRUE of staging where these diagnostics are wanted.
"""
from __future__ import annotations

import inspect


def test_the_gate_defaults_to_off() -> None:
    """An environment nobody has thought about must be the safe one.

    Read from the model's declared default rather than from a constructed
    Settings instance, because the process running the suite may have the
    variable set and would then assert nothing.
    """
    from app.core.config import Settings

    field = Settings.model_fields["expose_request_diagnostics"]
    assert field.default is False, (
        "request diagnostics default to ON, so any deployment that has not "
        "explicitly opted out publishes Server-Timing with a SQL duration and "
        "accepts X-Debug-SQL from anonymous callers"
    )


def test_the_middleware_is_not_gated_on_a_release_channel_name() -> None:
    """The specific regression: `is_production` deciding a safety property.

    Asserted over the SOURCE of the guard rather than by building two apps,
    because the failure is a future edit reaching for the convenient property
    again, and that edit is visible in the text long before it is visible in
    behaviour.
    """
    from app import main

    source = inspect.getsource(main)
    # Find the line that installs the timing middleware, then walk back to the
    # `if` that governs it. The module mentions `is_production` in prose, so a
    # naive "not in source" check would fail on its own explanation.
    lines = source.splitlines()
    install = [
        index
        for index, line in enumerate(lines)
        if "dispatch=timing_middleware" in line
    ]
    assert install, "the timing middleware is no longer installed by main.py"

    guard = None
    for index in range(install[0], -1, -1):
        stripped = lines[index].strip()
        if stripped.startswith("if ") and stripped.endswith(":"):
            guard = stripped
            break
    assert guard is not None, "no `if` guards the timing middleware at all"

    assert "expose_request_diagnostics" in guard, (
        f"the timing middleware is guarded by {guard!r} rather than by the "
        "explicit opt-in. Whatever it reads now, it must not be a property "
        "derived from the environment NAME."
    )
    assert "is_production" not in guard, (
        "the guard reads `is_production` again. That is `environment == "
        "'production'`, and the deployment serving readypick.ai runs "
        "ENVIRONMENT=pilot, so this gate would be open in production."
    )
    assert "serves_over_https" not in guard, (
        "`serves_over_https` is true of staging, where these diagnostics are "
        "wanted. It is not a substitute for an explicit per-deployment setting."
    )
