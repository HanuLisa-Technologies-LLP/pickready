"""One run's scratch space, and the reason it is a class rather than a dict.

A bare dict is what every stage would reach into and mutate, and the first
question anybody asks when an agent misbehaves -- what did the executor actually
receive from the planner -- becomes unanswerable because everything wrote
everywhere. `WorkingMemory` records WHICH stage wrote each key, which turns that
question into a lookup.

It dies with the run. Nothing here is persisted: what deserved to outlive the
run is an episodic trace or an experience learning, and both are explicit.

THE RUN'S TENANT IS RECORDED, AND A MISMATCHED WRITE RAISES
-------------------------------------------------------------
Dying with the run means working memory cannot leak across tenants over time.
It can still be MIXED within one run, which is the shape of the defect a
future orchestrator would produce by reusing a run for a second candidate at a
second customer. `tenant_id` on the memory and `provenance` on a write are what
make that a raise instead of a plausible value in a prompt: the same argument
`require` already makes about a missing key, applied to a key belonging to
somebody else.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.services.memory.provenance import Provenance


@dataclass
class WorkingMemory:
    """Per-run state, with provenance for every key."""

    #: The tenant this run is for. None for platform work with no customer.
    tenant_id: uuid.UUID | str | None = None
    values: dict[str, Any] = field(default_factory=dict)
    #: key -> the stage that last wrote it.
    written_by: dict[str, str] = field(default_factory=dict)
    #: key -> the provenance the writing stage declared, when it declared one.
    sourced_by: dict[str, Provenance] = field(default_factory=dict)

    def put(
        self,
        key: str,
        value: Any,
        *,
        stage: str = "unknown",
        provenance: Provenance | None = None,
    ) -> None:
        """Write one key. A value from another tenant is refused, not stored.

        The check runs before the assignment, so a rejected write leaves
        nothing behind for a later stage to read.
        """
        if (
            provenance is not None
            and self.tenant_id is not None
            and str(provenance.tenant_id) != str(self.tenant_id)
        ):
            raise ValueError(
                f"working memory is for tenant {self.tenant_id}; "
                f"{key!r} was produced for {provenance.tenant_id}"
            )
        self.values[key] = value
        self.written_by[key] = stage
        if provenance is not None:
            self.sourced_by[key] = provenance

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

    def sources(self) -> dict[str, str]:
        """key -> "source@version", for the keys whose writer declared one.

        Separate from `provenance` above, which answers "which STAGE wrote
        this". Both questions are asked and they have different answers: the
        stage is where in the run it happened, the source is what the value was
        derived from and therefore what a revocation would have to name.
        """
        return {
            key: f"{origin.source}@{origin.source_version or 'unversioned'}"
            for key, origin in self.sourced_by.items()
        }

    def snapshot(self) -> dict[str, str]:
        """Key -> writing stage and value TYPE. Never the values themselves.

        Safe to log: the values are resumes and transcripts.
        """
        return {
            key: f"{self.written_by.get(key, '?')}:{type(value).__name__}"
            for key, value in self.values.items()
        }
