"""Per-run resource limits, and the sandbox ceilings they must fit under.

TWO FENCES, AND THIS IS THE FIRST ONE. Every submission carries explicit CPU,
wall, memory, stack, process and file-size limits, so a candidate program is
bounded by what the application asked for. The sandbox host carries its own
MAX_* caps (`infra/modules/code_sandbox/judge0.conf.tftpl`), which bound it
again if a request ever asked for more. `HOST_MAXIMA` mirrors those caps and
`tests/test_code_execution_provider.py` parses the template and compares, so
the two cannot drift into a state where every request is refused by the host.

A limit that exceeds a ceiling is REFUSED, never clamped: clamping would run
the program under limits nobody configured and report the result as if they
had been.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings
from app.services.code_execution.errors import ExecutionNotConfigured
from app.services.code_execution.languages import language_spec

__all__ = ["ExecutionLimits", "HOST_MAXIMA", "for_language"]

_KB_PER_MB = 1024


@dataclass(frozen=True)
class ExecutionLimits:
    """The limits one run is executed under. Units are in the field names."""

    cpu_seconds: float
    cpu_extra_seconds: float
    wall_seconds: float
    memory_kb: int
    stack_kb: int
    max_processes: int
    max_file_kb: int
    max_output_chars: int


#: The sandbox host's own caps, keyed by `ExecutionLimits` field. Mirrors the
#: MAX_* values in `judge0.conf.tftpl`; a parity test reads both.
HOST_MAXIMA: dict[str, float] = {
    "cpu_seconds": 5.0,
    "cpu_extra_seconds": 1.0,
    "wall_seconds": 10.0,
    "memory_kb": 524288,
    "stack_kb": 131072,
    "max_processes": 120,
    "max_file_kb": 4096,
}


def for_language(key: str) -> ExecutionLimits:
    """The configured limits for one language, with its CPU multiplier applied.

    The multiplier scales CPU and wall time together: a JVM start is slower on
    both clocks, and scaling one alone would turn a correct Java solution into
    a wall-time failure.
    """
    spec = language_spec(key)
    settings = get_settings()
    factor = settings.code_execution_cpu_multiplier_map.get(spec.key, 1.0)
    limits = ExecutionLimits(
        cpu_seconds=round(settings.code_execution_cpu_seconds * factor, 3),
        cpu_extra_seconds=settings.code_execution_cpu_extra_seconds,
        wall_seconds=round(settings.code_execution_wall_seconds * factor, 3),
        memory_kb=settings.code_execution_memory_mb * _KB_PER_MB,
        stack_kb=settings.code_execution_stack_kb,
        max_processes=settings.code_execution_max_processes,
        max_file_kb=settings.code_execution_max_file_kb,
        max_output_chars=settings.code_execution_max_output_chars,
    )
    over = [
        name
        for name, ceiling in HOST_MAXIMA.items()
        if getattr(limits, name) > ceiling
    ]
    if over:
        raise ExecutionNotConfigured(
            f"the configured {', '.join(over)} for {spec.key} exceed the sandbox "
            "maxima; lower the CODE_EXECUTION_* setting or raise the host cap",
            reason="limits_exceed_host",
        )
    return limits
