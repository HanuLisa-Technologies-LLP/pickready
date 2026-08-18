"""Per-tool counters. Names, timings and outcomes -- never payloads.

The rule is the one `interview_telemetry` already states: an ordinary log is
far more widely readable than a trace, and a tool payload here is a real
person's resume, a real JD and a real candidate's answers. So nothing that
crosses this module carries content. What it does carry is enough to answer the
operational questions: which tool is slow, which one fails, and whether the
cache is doing anything.

In-process by design, exactly like `llm_router`'s provider stats. A counter
that needs a database write to increment is a counter that stops working during
the incident you wanted it for.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_CACHED = "cached"
STATUS_TIMEOUT = "timeout"
STATUS_REFUSED = "refused"       # permission or input validation
STATUS_BAD_OUTPUT = "bad_output"
STATUS_ERROR = "error"


@dataclass
class _ToolStats:
    calls: int = 0
    ok: int = 0
    cached: int = 0
    timeouts: int = 0
    refused: int = 0
    bad_output: int = 0
    errors: int = 0
    retries: int = 0
    total_ms: int = 0
    max_ms: int = 0
    latencies: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, float | int]:
        return {
            "calls": self.calls,
            "ok": self.ok,
            "cached": self.cached,
            "timeouts": self.timeouts,
            "refused": self.refused,
            "bad_output": self.bad_output,
            "errors": self.errors,
            "retries": self.retries,
            "success_rate": round(self.ok / self.calls, 4) if self.calls else 0.0,
            "cache_hit_rate": round(self.cached / self.calls, 4) if self.calls else 0.0,
            "avg_ms": round(self.total_ms / self.calls) if self.calls else 0,
            "max_ms": self.max_ms,
            "p95_ms": _percentile(self.latencies, 95),
        }


def _percentile(values: list[int], pct: int) -> int:
    """Nearest-rank percentile. Exact on the sample, and no dependency.

    A mean hides the case that matters -- one tool call in twenty taking eight
    seconds is invisible in an average and is precisely what a candidate
    experiences as the product hanging.
    """
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), (pct * len(ordered) + 99) // 100))
    return ordered[rank - 1]


#: Bounded so a long-lived worker cannot grow this without limit. The window is
#: recent-behaviour telemetry, not an audit trail; the audit trail is the
#: database.
_LATENCY_WINDOW = 512

_STATS: dict[str, _ToolStats] = {}


def _stats_for(tool: str) -> _ToolStats:
    return _STATS.setdefault(tool, _ToolStats())


def record(
    *,
    tool: str,
    agent: str,
    status: str,
    elapsed_ms: int,
    attempts: int = 1,
) -> None:
    """Count one completed call. `tool` and `agent` are identifiers, not data."""
    stats = _stats_for(tool)
    stats.calls += 1
    stats.retries += max(0, attempts - 1)
    stats.total_ms += elapsed_ms
    stats.max_ms = max(stats.max_ms, elapsed_ms)
    stats.latencies.append(elapsed_ms)
    if len(stats.latencies) > _LATENCY_WINDOW:
        del stats.latencies[: len(stats.latencies) - _LATENCY_WINDOW]

    if status == STATUS_OK:
        stats.ok += 1
    elif status == STATUS_CACHED:
        stats.ok += 1
        stats.cached += 1
    elif status == STATUS_TIMEOUT:
        stats.timeouts += 1
    elif status == STATUS_REFUSED:
        stats.refused += 1
    elif status == STATUS_BAD_OUTPUT:
        stats.bad_output += 1
    else:
        stats.errors += 1

    log = logger.info if status in (STATUS_OK, STATUS_CACHED) else logger.warning
    log(
        "tool=%s agent=%s status=%s attempts=%d elapsed_ms=%d",
        tool,
        agent,
        status,
        attempts,
        elapsed_ms,
    )


def tool_stats() -> dict[str, dict[str, float | int]]:
    return {tool: stats.as_dict() for tool, stats in sorted(_STATS.items())}


def reset_tool_stats() -> None:
    _STATS.clear()
