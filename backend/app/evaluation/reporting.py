"""The envelope every eval result travels in, and the stamp it cannot omit.

WHY AN ENVELOPE RATHER THAN A BARE DICT
----------------------------------------
Two rules have to hold on every number this package emits, and both are the
kind that a call site forgets:

  1. THE DATASET VERSION IS STAMPED ON THE RESULT. A score that drops has three
     candidate explanations -- the model regressed, the rubric changed, or the
     golden set changed -- and only the stamp separates the third from the other
     two. `EvalReport` refuses to be built without it, so a result cannot exist
     unattributed.
  2. AN UNMEASURED QUANTITY RENDERS AS `unavailable` WITH A REASON, NEVER 0.0.
     Every value in a report is a `metrics.Measurement`, and an unavailable one
     serialises with no numeric key at all. There is nothing for a dashboard,
     a threshold or a diff to read a zero out of.

Enforcing both here rather than in each script is what makes them properties of
the layer instead of properties of somebody's memory. It is the same move the
agent tool models made for compensation stripping and the four-grade scale.

Provenance: RPN-AI-UP-001 W7.5, and the standing rule in `app/evaluation/
metrics.py` and `app/scripts/eval_agents.py` that quality metrics report as
UNAVAILABLE while no expert-labelled dataset exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.evaluation.metrics import Measurement


@dataclass(frozen=True)
class EvalReport:
    """One eval surface's answer, stamped and honest about what it could not do.

    `measurements` holds `Measurement` values only. A raw float is REFUSED at
    construction: a float has no way to say "unavailable", so admitting one
    would reopen the exact hole this type closes.
    """

    surface: str
    dataset_version: str
    produced_by: str
    measurements: dict[str, Measurement] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.surface.strip():
            raise ValueError("an eval report must name its surface")
        if not self.dataset_version.strip():
            raise ValueError(
                "an eval report must carry its dataset version; an unstamped "
                "result cannot distinguish a model regression from a set change"
            )
        if not self.produced_by.strip():
            raise ValueError("an eval report must name what produced it")
        for key, value in self.measurements.items():
            if not isinstance(value, Measurement):
                raise TypeError(
                    f"measurement {key!r} is a {type(value).__name__}, not a "
                    "Measurement. A bare number cannot report itself as "
                    "unavailable, which is the whole reason this type exists."
                )

    @property
    def unavailable(self) -> tuple[str, ...]:
        return tuple(
            sorted(key for key, value in self.measurements.items() if not value.available)
        )

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "surface": self.surface,
            "dataset_version": self.dataset_version,
            "produced_by": self.produced_by,
            "measurements": {
                key: value.as_dict() for key, value in sorted(self.measurements.items())
            },
        }
        if self.context:
            body["context"] = self.context
        if self.notes:
            body["notes"] = list(self.notes)
        return body


def render_lines(report: EvalReport) -> list[str]:
    """The human-readable form, with unavailable rows shown rather than dropped.

    A dropped row is worse than a stated absence: the reader assumes the metric
    was fine and the printer was terse. Every measurement gets a line, and an
    unavailable one carries its reason on that line.
    """
    lines = [
        f"{report.surface}  (dataset {report.dataset_version}, via {report.produced_by})",
        "",
    ]
    width = max((len(key) for key in report.measurements), default=0)
    for key, value in sorted(report.measurements.items()):
        if value.available and value.interval is not None:
            body = (
                f"{value.value:.4f}  95% CI "
                f"[{value.interval.low:.4f}, {value.interval.high:.4f}]"
            )
            if value.sample_size is not None:
                body += f"  n={value.sample_size}"
        else:
            body = f"unavailable  ({value.unavailable_reason})"
        lines.append(f"  {key.ljust(width)}  {body}")
        if value.note:
            lines.append(f"  {' ' * width}  note: {value.note}")
    for note in report.notes:
        lines.extend(["", f"  {note}"])
    return lines
