"""Value types shared by the assessment pipeline stages.

Frozen dataclasses only: no ORM row, no session, no model call. A stage hands
the next one values, so no later stage can reach back through a live object
into the database a previous stage read.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True)
class AnswerRecord:
    """One candidate answer, LOCATED: which message it is, which question it
    answered, and the turn it was given on.

    `text` is carried so the evidence writer can decide, while the text is in
    hand, whether the answer is substantive and whether it disclaims the skill.
    It never reaches the ledger: the ledger stores a LOCATOR to the message,
    because a copy of the sentence in a table anyone with database access can
    read would be a quiet route around the capability that guards the
    transcript (`view_review_screen`).
    """

    message_id: uuid.UUID
    #: The `question_key` the answer was filed under: a `candidate_questions`
    #: id as a string on every current conversation. A follow-up reuses its
    #: parent's key, so both answers are evidence for the same question.
    question_key: str
    turn: int
    text: str
    answered_at: datetime | None


class ProvenanceRecorder:
    """What produced the text of one report: model calls, prompts, templates.

    Threaded through Miti and Siddhi for one scoring run (PLAN-p5 section 3.1).
    The one mutable value in this module, deliberately: it is an accumulator a
    run appends to, never state a later stage reads to decide anything.

    A model call is recorded ONLY after the call returned a value the
    deterministic critic ACCEPTED (the callers enforce that), so
    `model_id()` and `prompt_version()` never claim a call that did not
    happen. A report whose every line came from a template carries None for
    both, and `templates()` says where the templates were used.
    """

    def __init__(self) -> None:
        self._models: list[str] = []
        self._prompts: list[str] = []
        self._templates: list[str] = []

    def model_call(self, task_type: str, prompt_name: str | None) -> None:
        from app.config import llm_providers
        from app.prompts import registry

        model = llm_providers.model_for(task_type)
        if model not in self._models:
            self._models.append(model)
        if prompt_name:
            label = f"{prompt_name}@{registry.version(prompt_name)}"
            if label not in self._prompts:
                self._prompts.append(label)

    def template(self, where: str) -> None:
        if where not in self._templates:
            self._templates.append(where)

    def models(self) -> tuple[str, ...]:
        return tuple(self._models)

    def prompt_versions(self) -> tuple[str, ...]:
        return tuple(self._prompts)

    def templates(self) -> tuple[str, ...]:
        return tuple(self._templates)

    def model_id(self) -> str | None:
        """`functional_skills_reports.model_id`: the models that wrote, or None."""
        return ";".join(self._models) or None

    def prompt_version(self) -> str | None:
        """`functional_skills_reports.prompt_version`: the registry labels, or None."""
        return "; ".join(self._prompts) or None

    def as_json(self) -> dict[str, list[str]]:
        """INTERNAL provenance (`generation_provenance_json`), never served."""
        return {
            "models": list(self._models),
            "prompts": list(self._prompts),
            "templates": list(self._templates),
        }


#: The report's `scoring_mode` for a run in which Miti graded every skill.
MODE_MITI = "miti"
#: The FINAL attempt's mode when some skill still could not be assessed
#: (PLAN-p5 P5-D4): those skills are written "Not assessed" and the report is
#: always routed to a person.
MODE_MITI_PARTIAL = "miti_partial"

#: What one scoring run ended as (`functional_assessment.RunResult.status`).
RUN_WRITTEN = "written"
RUN_NOT_ASSESSED = "not_assessed"
RUN_ALREADY_WRITTEN = "already_written"


@dataclass(frozen=True)
class AssessmentInputs:
    """Everything the later stages read, gathered ONCE by stage 1.

    `job`, `link`, `conversation` and the question and structured-answer rows
    are the rows stage 1 loaded, read by attribute and never written through:
    Miti reads `.id`, `.tenant_id`, `.title`, `.candidate_id` and the question
    columns, and nothing after stage 1 issues a query through them. The
    transcript is read from the DATABASE, never from a caller-assembled list,
    so the answers graded are the answers stored.
    """

    job: Any
    link: Any
    conversation: Any
    #: `jobs.assessment_grade`, locked on the contract (D5).
    grade: str
    #: This candidate's own `candidate_questions`, each rubric with its question.
    questions: tuple[Any, ...]
    #: {question key: [answer text]} in turn order: what every scorer reads.
    answers: Mapping[str, list[str]]
    #: {question key: [AnswerRecord]}: where each answer lives.
    locators: Mapping[str, list[AnswerRecord]]
    #: {question id: AssessmentAnswer}: objective scores, evaluation records.
    structured: Mapping[str, Any]
    #: {question id: CodingEvidence}: the 70/30 coding result per coding answer.
    coding: Mapping[str, Any] = field(default_factory=dict)
    #: The candidate's own name parts, removed from every evaluator excerpt.
    subject_names: tuple[str, ...] = ()
    #: The Validation section, the application's fields VERBATIM, unrated.
    validation: Mapping[str, Any] = field(default_factory=dict)
