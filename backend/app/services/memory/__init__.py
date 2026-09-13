"""Five kinds of memory, separated by how long they are true for.

  working     one run. A dict the stages hand each other. Dies with the run.
  semantic    days. Cached facts about an entity, keyed by id, invalidated by
              the write that changes them.
  episodic    a year. What happened, in the trace table. Queryable, never
              content.
  procedural  a release. Prompts, versioned on disk, so "which prompt wrote
              this report" is answerable after the prompt is improved.
  experience  indefinite. A failure pattern and the adjustment that fixed it.

WHY THE SEPARATION IS NOT BUREAUCRACY
--------------------------------------
Each has a different correctness rule, and collapsing any two produces a
specific bug this codebase can name. Cache a transcript as semantic memory and
an agent scores an assessment two answers stale. Store a learning in episodic
memory and it is never retrieved. Keep prompts unversioned and a report written
last month cannot be explained, because the prompt that wrote it no longer
exists anywhere.

EXPERIENCE MEMORY IS A HINT AND NEVER A GATE
---------------------------------------------
A learning is prepended to a prompt as guidance. It can never switch off a
deterministic criterion, relax a word range or skip a verifier. If it could, one
unlucky run would permanently lower the bar for every run after it, and the
mechanism that did it would be invisible in the code.

EVERY LAYER NAMES ITS TENANT (RPN-AI-UP-001 W3.5)
--------------------------------------------------
`provenance.py` carries the decision and the reasoning: learnings are scoped
PER TENANT rather than shared after human approval, because a tenant scope is
enforceable structurally by RLS and an approval is a process. What that means
layer by layer:

  working     the run's tenant is a field, and a write whose declared
              provenance names another tenant raises.
  semantic    the tenant is the first segment of the cache key AND is stored in
              the envelope, so a mismatched entry reads as a miss.
  episodic    the trace table has carried `tenant_id` and an RLS policy since
              0055; `health` now requires the tenant on the read side too.
  procedural  supplies the `source_version` the other layers record, so output
              written by a specific prompt version is identifiable later.
  experience  `tenant_id` is NOT NULL, in the unique key, in every query, and
              behind an RLS policy (migration 0089).
"""
from __future__ import annotations

from app.services.memory import (
    episodic,
    experience,
    procedural,
    provenance,
    semantic,
    working,
)
from app.services.memory.experience import (
    Learning,
    record_failure,
    record_success,
    revoke_learnings_from_source,
)
from app.services.memory.provenance import Provenance, ProvenanceError
from app.services.memory.working import WorkingMemory

__all__ = [
    "Learning",
    "Provenance",
    "ProvenanceError",
    "WorkingMemory",
    "episodic",
    "experience",
    "procedural",
    "provenance",
    "record_failure",
    "record_success",
    "revoke_learnings_from_source",
    "semantic",
    "working",
]
