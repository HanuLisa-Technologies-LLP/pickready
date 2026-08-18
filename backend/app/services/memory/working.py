"""One run's scratch space, and the reason it is a class rather than a dict.

A bare dict is what every stage would reach into and mutate, and the first
question anybody asks when an agent misbehaves -- what did the executor actually
receive from the planner -- becomes unanswerable because everything wrote
everywhere. `WorkingMemory` records WHICH stage wrote each key, which turns that
question into a lookup.

It dies with the run. Nothing here is persisted: what deserved to outlive the
run is an episodic trace or an experience learning, and both are explicit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkingMemory:
    """Per-run state, with provenance for every key."""

    values: dict[str, Any] = field(default_factory=dict)
    #: key -> the stage that last wrote it.
    written_by: dict[str, str] = field(default_factory=dict)

    def put(self, key: str, value: Any, *, stage: str = "unknown") -> None:
        self.values[key] = value
        self.written_by[key] = stage

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def require(self, key: str) -> Any:
        """Fetch a key a stage cannot proceed without.

        Raising beats returning None: a stage that silently continues without
        its input produces a plausible output built from nothing, which is the
        hardest kind of wrong to notice.
        """
        if key not in self.values:
            raise KeyError(f"working memory has no {key!r}; written keys: {sorted(self.values)}")
        return self.values[key]

    def provenance(self) -> dict[str, str]:
        return dict(self.written_by)

    def snapshot(self) -> dict[str, str]:
        """Key -> writing stage and value TYPE. Never the values themselves.

        Safe to log: the values are resumes and transcripts.
        """
        return {
            key: f"{self.written_by.get(key, '?')}:{type(value).__name__}"
            for key, value in self.values.items()
        }
