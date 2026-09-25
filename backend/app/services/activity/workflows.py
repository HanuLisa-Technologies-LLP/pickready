"""What each AI workflow declares about itself. The future-proofing surface.

PROVENANCE
----------
`ai-upgrade-spec-doc.md` "Case 3" section 20: a developer adding a new AI
feature declares its task, its meaningful stages, its completion state and its
error state, and gets the standard activity UI without writing a component.

This table IS that declaration. Adding a workflow is:

  1. a `TASK_*` constant in `events.py` and a member of `TASK_KINDS`;
  2. an entry here naming the milestones the workflow really reaches;
  3. one `Phrase` per milestone plus the three terminal kinds, in `phrasing.py`;
  4. an `ActivityStream` at the emission point, and nothing on the frontend.

WHY THE STAGES ARE DECLARED AND NOT INFERRED
---------------------------------------------
`tests/test_ai_activity.py` walks this table and fails the build when a declared
stage has no phrase, when a workflow has no completion, failure or cancellation
phrase, or when a task has no default line. That is what keeps the generic
fallback in Case 3 section 19 a genuine last resort rather than the sentence a
half-finished workflow quietly ships with. It is the same shape as
`matching_progress`'s existing rule that a declared stage must be emitted: a
declaration is a promise the test suite holds you to.

The order is the order the workflow actually passes through the milestones, so
a reader can be shown the whole plan at once and watch it fill in, rather than
watching steps appear one at a time and wondering how many are left.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.activity import events as ev


@dataclass(frozen=True)
class Workflow:
    """One AI workflow that reports activity."""

    task: str
    #: What the operation is called where a heading is needed. Not the module,
    #: not the dispatched task name: what the user asked for.
    label: str
    #: The meaningful milestones, in the order the workflow reaches them.
    stages: tuple[str, ...]


WORKFLOWS: dict[str, Workflow] = {
    ev.TASK_JOB_CANDIDATE_MATCHING: Workflow(
        task=ev.TASK_JOB_CANDIDATE_MATCHING,
        label="Matching candidates to this job",
        stages=(
            ev.TASK_STARTED,
            ev.REQUIREMENTS_IDENTIFIED,
            ev.INPUT_PREPARED,
            ev.DOCUMENT_PARSED,
            ev.SEMANTIC_SEARCH_COMPLETED,
            ev.KEYWORD_SEARCH_COMPLETED,
            ev.CANDIDATE_POOL_ASSEMBLED,
            ev.VALIDATION_CHECKED,
            ev.SKILLS_COMPARED,
            ev.EVIDENCE_GROUNDED,
            ev.RESULTS_RECORDED,
        ),
    ),
    ev.TASK_ASSESSMENT_REPORT: Workflow(
        task=ev.TASK_ASSESSMENT_REPORT,
        label="Writing the PRISM Report",
        stages=(
            ev.TASK_STARTED,
            ev.DOCUMENT_PARSED,
            ev.REQUIREMENTS_IDENTIFIED,
            ev.SKILLS_COMPARED,
            ev.GAPS_IDENTIFIED,
            ev.RECOMMENDATIONS_GENERATED,
            ev.RESULTS_RECORDED,
        ),
    ),
}

#: Every workflow must be able to say all three of these, whatever else it
#: declares. A workflow with no failure phrase is a workflow whose error state
#: is the previous sentence still sitting on screen, which Case 3 section 25
#: names as the thing to prevent.
REQUIRED_TERMINAL_KINDS: tuple[str, ...] = (
    ev.TASK_COMPLETED,
    ev.TASK_FAILED,
    ev.TASK_CANCELLED,
)


def workflow_for(task: str) -> Workflow:
    """The declaration for a task. Raises rather than inventing one."""
    try:
        return WORKFLOWS[task]
    except KeyError as exc:
        raise ev.UnknownTask(task) from exc
