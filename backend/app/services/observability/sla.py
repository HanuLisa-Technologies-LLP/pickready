"""Latency targets per agent, and which stage spent the time.

WHY THE TARGETS ARE WHERE THEY ARE
-----------------------------------
They are what the product does today, measured, not what it aspires to. A
threshold set aspirationally alarms constantly, gets muted, and then a real
regression arrives inside the mute. The `eval_interview` thresholds were set the
same way and the comment there says the same thing: a rate allowed to fall
silently is a rate nobody is defending.

WHY P95 AND NOT A MEAN
----------------------
One run in twenty taking eight seconds is invisible in an average and is exactly
what a candidate experiences as the product hanging.
"""
from __future__ import annotations

from dataclasses import dataclass

#: target / warning / critical, in milliseconds. Interactive agents are tighter
#: because somebody is watching a text box; the report agents are background.
SLA_MS: dict[str, tuple[int, int, int]] = {
    "ranking": (3000, 5000, 10000),
    "ppi_report": (5000, 8000, 15000),
    "email": (2000, 3000, 5000),
    "probe": (4000, 6000, 10000),
    "interviewer": (3000, 5000, 12000),
    "job_setup": (8000, 15000, 30000),
}

DEFAULT_SLA = (5000, 8000, 15000)

OK = "ok"
WARNING = "warning"
CRITICAL = "critical"


@dataclass(frozen=True)
class Assessment:
    agent_type: str
    duration_ms: int
    level: str
    target_ms: int
    #: The slowest stage, which is the only actionable part of a breach.
    bottleneck: str | None = None
    bottleneck_ms: int = 0


def thresholds(agent_type: str) -> tuple[int, int, int]:
    return SLA_MS.get(agent_type, DEFAULT_SLA)


def assess(agent_type: str, duration_ms: int, stages: list[dict] | None = None) -> Assessment:
    """Classify one run against its agent's SLA and name the slowest stage."""
    target, warning, critical = thresholds(agent_type)
    if duration_ms >= critical:
        level = CRITICAL
    elif duration_ms >= warning:
        level = WARNING
    else:
        level = OK

    bottleneck, bottleneck_ms = None, 0
    for stage in stages or ():
        elapsed = int(stage.get("duration_ms") or 0)
        if elapsed > bottleneck_ms:
            bottleneck, bottleneck_ms = str(stage.get("stage") or ""), elapsed

    return Assessment(
        agent_type=agent_type,
        duration_ms=duration_ms,
        level=level,
        target_ms=target,
        bottleneck=bottleneck or None,
        bottleneck_ms=bottleneck_ms,
    )


def percentile(values: list[int], pct: int) -> int:
    """Nearest-rank percentile. Exact on the sample and no dependency."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), (pct * len(ordered) + 99) // 100))
    return ordered[rank - 1]


def compliance(agent_type: str, durations: list[int]) -> dict[str, float | int]:
    """The reportable summary: p50, p95, and the share inside target."""
    if not durations:
        return {"runs": 0, "p50_ms": 0, "p95_ms": 0, "within_target": 0.0}
    target, _, _ = thresholds(agent_type)
    inside = sum(1 for value in durations if value < target)
    return {
        "runs": len(durations),
        "p50_ms": percentile(durations, 50),
        "p95_ms": percentile(durations, 95),
        "within_target": round(inside / len(durations), 4),
    }
