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
"""
from __future__ import annotations

from app.services.memory import episodic, experience, procedural, semantic, working
from app.services.memory.experience import Learning, record_failure, record_success
from app.services.memory.working import WorkingMemory

__all__ = [
    "Learning",
    "WorkingMemory",
    "episodic",
    "experience",
    "procedural",
    "record_failure",
    "record_success",
    "semantic",
    "working",
]
