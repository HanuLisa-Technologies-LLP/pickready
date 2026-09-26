"""Yukti part six: the application answers, deterministic code only.

Owner guardrail (Appendix A): "Validation profile fit is computed by
deterministic rules, small configurable weight." Nothing here calls a model and
nothing here is ever sent to one: the expected CTC is compared with the job's
range by the same function that writes the recruiter's CTC Match column, and
only the resulting WORD travels further, into the provenance.

ONE IMPLEMENTATION PER COMPARISON. The three answers are read through
`recruiter_columns` (`ctc_match`, `notice_period_bucket`) and the application
form's own document-readiness options, so the recruiter's table and the ranking
can never disagree about what an answer means.

A MISSING ANSWER IS EXCLUDED, NEVER ZERO. A sourced or databank candidate never
answered the application questions, and a job with no declared CTC range
supports no CTC comparison. An absent part is left out and the remaining parts
renormalise; with none present the whole of part six is excluded (score None),
because "not asked" is not "answered badly".
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from app.services import recruiter_columns
from app.services.yukti import config

__all__ = ["ValidationFit", "score"]


@dataclass(frozen=True)
class ValidationFit:
    """Part six for one application.

    `score` is 0..100, or None when no answer could be compared. `parts` maps
    each compared answer to the WORD it produced ("Within range", "Within 30
    days", "All documents ready"), which is all the provenance ever records:
    never the stated amount, never the job's range.
    """

    score: float | None
    parts: Mapping[str, str]


def _document_word(raw: Any) -> str | None:
    """The form's own option for a readiness answer, matched exactly after
    trimming and casefolding, or None."""
    if raw is None:
        return None
    wanted = " ".join(str(raw).split()).casefold()
    if not wanted:
        return None
    for option in config.VALIDATION_VALUES[config.VALIDATION_DOCUMENTS]:
        if option.casefold() == wanted:
            return option
    return None


def _words(validation: Mapping[str, Any], compensation: Any) -> dict[str, str]:
    words: dict[str, str] = {}
    ctc = recruiter_columns.ctc_match(validation.get("expected_ctc"), compensation)
    if ctc is not None:
        words[config.VALIDATION_CTC] = ctc
    notice = recruiter_columns.notice_period_bucket(validation.get("notice_period"))
    if notice is not None:
        words[config.VALIDATION_NOTICE] = notice
    documents = _document_word(validation.get("document_readiness"))
    if documents is not None:
        words[config.VALIDATION_DOCUMENTS] = documents
    return words


def score(validation_json: Any, compensation_json: Any) -> ValidationFit:
    """Part six for one application: a 0..100 score over the answered parts.

    A word the value table does not know (a notice bucket added to
    `recruiter_columns` without a value here) RAISES rather than scoring zero:
    silently valuing a new answer at nothing would move candidates down the
    ranking for an answer nobody reviewed. `tests/test_yukti_config.py`
    rebuilds every reachable word from the live option lists so this can only
    fire after a change the test would already have failed on.
    """
    if not isinstance(validation_json, dict) or not validation_json:
        return ValidationFit(None, MappingProxyType({}))
    words = _words(validation_json, compensation_json)
    if not words:
        return ValidationFit(None, MappingProxyType({}))
    weighted = 0.0
    total_weight = 0
    for part in config.VALIDATION_PARTS:
        word = words.get(part)
        if word is None:
            continue
        table = config.VALIDATION_VALUES[part]
        if word not in table:
            raise KeyError(
                f"validation part {part!r} produced {word!r}, which has no value "
                "in yukti.config.VALIDATION_VALUES"
            )
        weight = config.VALIDATION_SUBWEIGHTS[part]
        weighted += weight * table[word]
        total_weight += weight
    return ValidationFit(
        round(weighted / total_weight * 100, 1),
        MappingProxyType(dict(words)),
    )
