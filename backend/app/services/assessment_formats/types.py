"""The six question formats, their payload shapes and their answer shapes.

ONE VOCABULARY, PINNED THREE TIMES: here, by the database CHECK migration 0076
writes on `candidate_questions.question_type` and `assessment_answers.question_type`,
and by `tests/test_db_enum_parity.py`, which reads the CHECK back out of the
catalog and compares it with `QUESTION_TYPES`.

THE PAYLOAD HOLDS THE ANSWER KEY, SO IT NEVER LEAVES THE SERVER UNPROJECTED.
`candidate_view` is the only function that turns a stored payload into what a
candidate may see, and it is written as an allowlist per type: an MCQ's options
without the correct ids and in the candidate's own order, a fill-blank's
template without the accepted answers, a coding question's language and
starter code without the expected approach. A field added to a payload later is
absent from the candidate's view until somebody names it here, which is the
direction that fails safely.

ANSWER SHAPES are validated with pydantic at the API boundary
(`schemas/assessments.ConversationMessageIn`) and again here against the
question they answer, because an option id the payload does not contain is a
defect in the client, not a wrong answer.
"""
from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "EVIDENCE_BASED",
    "MCQ_SINGLE",
    "MCQ_MULTI",
    "FILL_BLANK",
    "CODING",
    "SHORT_ANSWER",
    "QUESTION_TYPES",
    "OBJECTIVE_TYPES",
    "SUBJECTIVE_TYPES",
    "SUPPORTING_TYPES",
    "TEXT_TYPES",
    "EVIDENCE_SUB_TYPES",
    "MCQ_OPTIONS_MIN",
    "MCQ_OPTIONS_MAX",
    "MCQ_OPTIONS_DEFAULT",
    "CODING_LANGUAGES",
    "McqOption",
    "McqSinglePayload",
    "McqMultiPayload",
    "FillBlank",
    "FillBlankPayload",
    "CodingPayload",
    "EvidencePayload",
    "TextAnswer",
    "McqSingleAnswer",
    "McqMultiAnswer",
    "FillBlankAnswer",
    "CodingAnswer",
    "PAYLOAD_MODELS",
    "ANSWER_MODELS",
    "parse_payload",
    "parse_answer",
    "candidate_view",
    "option_order",
    "is_structured",
]

EVIDENCE_BASED = "evidence_based"
MCQ_SINGLE = "mcq_single"
MCQ_MULTI = "mcq_multi"
FILL_BLANK = "fill_blank"
CODING = "coding"
SHORT_ANSWER = "short_answer"

QUESTION_TYPES: tuple[str, ...] = (
    EVIDENCE_BASED,
    MCQ_SINGLE,
    MCQ_MULTI,
    FILL_BLANK,
    CODING,
    SHORT_ANSWER,
)

#: Scored deterministically on submission, server-side (section 6.1).
OBJECTIVE_TYPES: frozenset[str] = frozenset({MCQ_SINGLE, MCQ_MULTI, FILL_BLANK})
#: AI-evaluated after submission, against the stored rubric (section 6.2).
SUBJECTIVE_TYPES: frozenset[str] = frozenset({EVIDENCE_BASED, CODING, SHORT_ANSWER})
#: The minority of the assessment, by construction (section 1).
SUPPORTING_TYPES: frozenset[str] = frozenset({MCQ_SINGLE, MCQ_MULTI, FILL_BLANK, CODING})
#: Answered in prose. These go through the conversational machinery
#: (classification, re-asks, follow-ups, per-turn rewriting); the others are
#: delivered verbatim, because their answer key was written with them.
TEXT_TYPES: frozenset[str] = frozenset({EVIDENCE_BASED, SHORT_ANSWER})

#: Evidence question sub-types (section 2.1).
EVIDENCE_SUB_TYPES: tuple[str, ...] = (
    "project_deep_dive",
    "decision_justification",
    "claim_substantiation",
    "failure_trade_off",
    "scope_clarification",
)

MCQ_OPTIONS_MIN = 3
MCQ_OPTIONS_MAX = 6
MCQ_OPTIONS_DEFAULT = 4
MCQ_MULTI_MIN_OPTIONS = 4
MCQ_MULTI_MIN_CORRECT = 2

#: The languages the coding editor offers. A question's payload names one of
#: these (or a subset in `language_options`); the client never invents one.
CODING_LANGUAGES: tuple[str, ...] = (
    "python",
    "javascript",
    "typescript",
    "java",
    "go",
    "csharp",
    "cpp",
    "sql",
    "plaintext",
)

MAX_TEXT_ANSWER_CHARS = 10_000
MAX_CODE_CHARS = 20_000
MAX_BLANK_CHARS = 200


# ── Payloads ─────────────────────────────────────────────────────────────────


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class McqOption(_Strict):
    id: str = Field(min_length=1, max_length=8)
    text: str = Field(min_length=1, max_length=500)


def _ids(options: list[McqOption]) -> list[str]:
    ids = [option.id for option in options]
    if len(set(ids)) != len(ids):
        raise ValueError("option ids must be unique")
    return ids


class McqSinglePayload(_Strict):
    options: list[McqOption] = Field(min_length=MCQ_OPTIONS_MIN, max_length=MCQ_OPTIONS_MAX)
    correct_option_id: str

    @model_validator(mode="after")
    def _key_is_an_option(self) -> "McqSinglePayload":
        if self.correct_option_id not in _ids(self.options):
            raise ValueError("correct_option_id is not one of the options")
        return self


class McqMultiPayload(_Strict):
    options: list[McqOption] = Field(min_length=MCQ_MULTI_MIN_OPTIONS, max_length=MCQ_OPTIONS_MAX)
    correct_option_ids: list[str] = Field(min_length=MCQ_MULTI_MIN_CORRECT)
    #: The only scoring the product implements (section 2.3). Stored so a
    #: report written today still says how it was scored if a second rule is
    #: ever added.
    scoring: str = "partial"
    #: How many the candidate is told to select, or None for "select all that
    #: apply".
    select_count: int | None = None

    @model_validator(mode="after")
    def _keys_are_options(self) -> "McqMultiPayload":
        ids = _ids(self.options)
        if len(set(self.correct_option_ids)) != len(self.correct_option_ids):
            raise ValueError("correct_option_ids must be unique")
        if not set(self.correct_option_ids) <= set(ids):
            raise ValueError("correct_option_ids are not all options")
        if len(self.correct_option_ids) >= len(ids):
            raise ValueError("every option cannot be correct")
        if self.scoring != "partial":
            raise ValueError("scoring must be 'partial'")
        if self.select_count is not None and self.select_count != len(self.correct_option_ids):
            raise ValueError("select_count must equal the number of correct options")
        return self


class FillBlank(_Strict):
    index: int = Field(ge=0)
    accepted: list[str] = Field(min_length=1, max_length=20)
    case_sensitive: bool = False

    @field_validator("accepted")
    @classmethod
    def _accepted_are_words(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if not cleaned:
            raise ValueError("a blank needs at least one accepted answer")
        return cleaned


#: The blank marker in a template. Three underscores, as the specification
#: writes it; each occurrence is one blank, in order.
BLANK_MARKER = "___"


class FillBlankPayload(_Strict):
    template: str = Field(min_length=BLANK_MARKER.__len__() + 1, max_length=2000)
    blanks: list[FillBlank] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def _blanks_match_markers(self) -> "FillBlankPayload":
        markers = self.template.count(BLANK_MARKER)
        indices = sorted(blank.index for blank in self.blanks)
        if indices != list(range(markers)):
            raise ValueError(
                f"template has {markers} blank markers but blanks are indexed {indices}"
            )
        return self


class CodingPayload(_Strict):
    language: str
    starter_code: str = Field(default="", max_length=MAX_CODE_CHARS)
    constraints: str = Field(default="", max_length=2000)
    #: What a correct solution looks like, for the evaluator. NEVER shown to
    #: the candidate; `candidate_view` drops it.
    expected_approach: str = Field(min_length=1, max_length=4000)
    #: Languages the candidate may choose between, when the question permits
    #: a choice. Empty means only `language`.
    language_options: list[str] = Field(default_factory=list, max_length=len(CODING_LANGUAGES))

    @model_validator(mode="after")
    def _languages_are_known(self) -> "CodingPayload":
        for language in [self.language, *self.language_options]:
            if language not in CODING_LANGUAGES:
                raise ValueError(f"unknown coding language {language!r}")
        return self


class EvidencePayload(_Strict):
    sub_type: str
    #: Where in the resume the anchor came from, as a locator string the
    #: generator wrote ("employment_history[1]", "projects[0]"). Provenance
    #: for the recruiter view, never shown to the candidate.
    anchor_source: str = Field(default="", max_length=200)
    follow_up_permitted: bool = True

    @field_validator("sub_type")
    @classmethod
    def _sub_type_is_known(cls, value: str) -> str:
        if value not in EVIDENCE_SUB_TYPES:
            raise ValueError(f"unknown evidence sub-type {value!r}")
        return value


class ShortAnswerPayload(_Strict):
    """A short-answer question carries no payload. Modelled so every type has
    exactly one payload model and a stray key is refused rather than stored."""


PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    EVIDENCE_BASED: EvidencePayload,
    MCQ_SINGLE: McqSinglePayload,
    MCQ_MULTI: McqMultiPayload,
    FILL_BLANK: FillBlankPayload,
    CODING: CodingPayload,
    SHORT_ANSWER: ShortAnswerPayload,
}


# ── Answers ──────────────────────────────────────────────────────────────────


class TextAnswer(_Strict):
    text: str = Field(min_length=1, max_length=MAX_TEXT_ANSWER_CHARS)


class McqSingleAnswer(_Strict):
    selected_option_id: str = Field(min_length=1, max_length=8)


class McqMultiAnswer(_Strict):
    selected_option_ids: list[str] = Field(min_length=0, max_length=MCQ_OPTIONS_MAX)

    @field_validator("selected_option_ids")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("selected_option_ids must be unique")
        return value


class FillBlankAnswer(_Strict):
    #: One entry per blank, in blank order. An unanswered blank is "".
    values: list[str] = Field(min_length=1, max_length=10)

    @field_validator("values")
    @classmethod
    def _bounded(cls, value: list[str]) -> list[str]:
        return [item[:MAX_BLANK_CHARS] for item in value]


class CodingAnswer(_Strict):
    language: str
    code: str = Field(min_length=1, max_length=MAX_CODE_CHARS)

    @field_validator("language")
    @classmethod
    def _language_known(cls, value: str) -> str:
        if value not in CODING_LANGUAGES:
            raise ValueError(f"unknown coding language {value!r}")
        return value


ANSWER_MODELS: dict[str, type[BaseModel]] = {
    EVIDENCE_BASED: TextAnswer,
    SHORT_ANSWER: TextAnswer,
    MCQ_SINGLE: McqSingleAnswer,
    MCQ_MULTI: McqMultiAnswer,
    FILL_BLANK: FillBlankAnswer,
    CODING: CodingAnswer,
}


def is_structured(question_type: str) -> bool:
    """Whether the answer is a structure rather than prose."""
    return question_type not in TEXT_TYPES


def parse_payload(question_type: str, payload: dict[str, Any] | None) -> BaseModel:
    """The typed payload, or a ValueError naming what is wrong with it."""
    model = PAYLOAD_MODELS[question_type]
    return model.model_validate(payload or {})


def parse_answer(question_type: str, payload: dict[str, Any] | None, answer: dict[str, Any]) -> BaseModel:
    """The typed answer, validated against the question it answers.

    Shape first (pydantic), then membership: a selected option id must be one
    the question offered, a fill-blank answer must have one value per blank,
    a coding answer's language must be one the question permits.
    """
    parsed = ANSWER_MODELS[question_type].model_validate(answer)
    if question_type == MCQ_SINGLE:
        question = McqSinglePayload.model_validate(payload or {})
        if parsed.selected_option_id not in {option.id for option in question.options}:  # type: ignore[attr-defined]
            raise ValueError("selected option is not one of this question's options")
    elif question_type == MCQ_MULTI:
        question_multi = McqMultiPayload.model_validate(payload or {})
        offered = {option.id for option in question_multi.options}
        if not set(parsed.selected_option_ids) <= offered:  # type: ignore[attr-defined]
            raise ValueError("a selected option is not one of this question's options")
    elif question_type == FILL_BLANK:
        question_blank = FillBlankPayload.model_validate(payload or {})
        if len(parsed.values) != len(question_blank.blanks):  # type: ignore[attr-defined]
            raise ValueError("one value per blank is required")
    elif question_type == CODING:
        question_code = CodingPayload.model_validate(payload or {})
        permitted = {question_code.language, *question_code.language_options}
        if parsed.language not in permitted:  # type: ignore[attr-defined]
            raise ValueError("that language is not permitted for this question")
    return parsed


def option_order(question_id: Any, option_ids: list[str]) -> list[str]:
    """The candidate's own option order (section 2.2), deterministic per question.

    Derived from the question's id rather than drawn at random so the order the
    candidate saw is the order the recruiter's view reconstructs, without a
    stored permutation. Two candidates get different questions and therefore
    different orders; one candidate always sees the same order on a reload.
    """
    digest = hashlib.sha256(str(question_id).encode("utf-8")).digest()
    keyed = sorted(
        option_ids,
        key=lambda option_id: hashlib.sha256(digest + option_id.encode("utf-8")).digest(),
    )
    return keyed


def candidate_view(question_id: Any, question_type: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """What the candidate may see of a payload. An allowlist per type."""
    if question_type == MCQ_SINGLE:
        single = McqSinglePayload.model_validate(payload or {})
        by_id = {option.id: option.text for option in single.options}
        return {
            "options": [
                {"id": option_id, "text": by_id[option_id]}
                for option_id in option_order(question_id, list(by_id))
            ],
            "select_count": 1,
        }
    if question_type == MCQ_MULTI:
        multi = McqMultiPayload.model_validate(payload or {})
        by_id = {option.id: option.text for option in multi.options}
        return {
            "options": [
                {"id": option_id, "text": by_id[option_id]}
                for option_id in option_order(question_id, list(by_id))
            ],
            "select_count": multi.select_count,
        }
    if question_type == FILL_BLANK:
        blank = FillBlankPayload.model_validate(payload or {})
        return {
            "template": blank.template,
            "blanks": [
                {
                    "index": item.index,
                    "case_sensitive": item.case_sensitive,
                    # Sized to the expected answer (section 5.2) without
                    # revealing it: the longest accepted answer's length.
                    "expected_length": max(len(accepted) for accepted in item.accepted),
                }
                for item in sorted(blank.blanks, key=lambda item: item.index)
            ],
        }
    if question_type == CODING:
        code = CodingPayload.model_validate(payload or {})
        return {
            "language": code.language,
            "language_options": list(code.language_options) or [code.language],
            "starter_code": code.starter_code,
            "constraints": code.constraints,
        }
    # Evidence-based and short-answer questions carry nothing the candidate
    # needs beyond the prompt. The anchor is IN the prompt's wording; the
    # locator and sub-type are recruiter provenance.
    return {}
