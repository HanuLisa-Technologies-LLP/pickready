"""What a person is shown of a Yukti reading: words, tags and provenance lines.

Everything a recruiter, an email or a PRISM Report reads about AI Matching
comes through here, and nothing here returns a number. The internal pre score,
the blended key, the tenant's ratio and the six-part structure stay on the
server (D2, D3): this module turns them into

* ONE grade word (`rating`'s four words) or a status word ("Not checked yet",
  "Not assessed"),
* EVIDENCE TAGS, each a short text and a polarity, with the skill names
  resolved from the job's CURRENT skills at read time, so renaming a skill
  changes the label a recruiter reads and never the score or the order,
* PROVENANCE LINES, plain sentences saying where the grade came from, what was
  read, and whether the reading is out of date.

A tag never carries its quote to the client: the quote is the grounding record,
kept for the server's own checks, and a resume passage is not something the
ranked table needs in order to say "Python".

STALENESS IS DERIVED, never stored. A reading is out of date when the job's
skills (the contract digest) changed after it, or when the application now
points at a different resume than the one that was read.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import assessment_contract, hiring_pipeline
from app.services.yukti import config, ranking

__all__ = [
    "JobSkillsView",
    "ai_match_fields",
    "ai_score_summary",
    "applicant_label",
    "evidence_tags",
    "job_skills_view",
    "pre_grade_word",
    "provenance_words",
    "skill_list_phrase",
    "status_word",
    "strengths_for_prompt",
]

# ── Words ────────────────────────────────────────────────────────────────────

STATUS_WORD_PENDING = "Not checked yet"
STATUS_WORD_NOT_ASSESSED = "Not assessed"

APPLICANT_LABEL_DATABANK = "Databank, not an applicant"
APPLICANT_LABEL_SOURCED = "Sourced, not an applicant"

LINE_PENDING = "AI Matching has not read this resume yet."
LINE_LEGACY = "Checked before evidence tags existed. Run AI Matching to refresh."
LINE_SKILLS_CHANGED = "The skills changed after this check. Run AI Matching to refresh."
LINE_RESUME_CHANGED = "The resume changed after this check. Run AI Matching to refresh."
LINE_LAST_ATTEMPT_FAILED = (
    "The latest AI check could not be completed, so this is the previous result."
)
LINE_CAPPED = (
    "Held at Moderately Matching or below: a Must-have skill was not "
    "demonstrated in the assessment."
)
LINE_ASSESSMENT_ALONE = (
    "Tatva Assessment: decides this grade on its own, because the resume "
    "check has no result."
)
LINE_ANSWERS_NOT_APPLIED = "Application answers: not given, this candidate has not applied."
LINE_ANSWERS_NONE = "Application answers: none that could be compared."
#: A `not_assessed` row whose stored reason this module does not know. Stated
#: plainly rather than guessed at: every writer goes through
#: `scoring.failed_outcome`, which refuses an unknown reason, so reaching this
#: means a row was written some other way.
LINE_NO_RESULT = "The resume check has no result."

#: Why a link is `not_assessed`, in words. Keyed by the stored reason.
FAILURE_LINES: Mapping[str, str] = {
    config.FAILURE_MODEL_UNAVAILABLE: (
        "The AI check could not be completed. It is retried on the next AI "
        "Matching run."
    ),
    config.FAILURE_MODEL_OUTPUT_INVALID: (
        "The AI check could not be completed. It is retried on the next AI "
        "Matching run."
    ),
    config.FAILURE_NO_RESUME: "No resume is attached to this application.",
    config.FAILURE_NO_RESUME_TEXT: "No readable resume text.",
    config.FAILURE_NO_GROUNDED_EVIDENCE: (
        "The resume check found nothing it could confirm against this job's "
        "skills or role."
    ),
}

#: What each model-read part is called in a provenance line. Coarse on purpose:
#: a recruiter is told what was READ, never the structure, the weights or the
#: part names (D2). Both skill buckets read as "skills".
_PART_WORDS: Mapping[str, str] = {
    config.COMPONENT_MUST_HAVE: "skills",
    config.COMPONENT_NICE_TO_HAVE: "skills",
    config.COMPONENT_EXPERIENCE: "experience",
    config.COMPONENT_ROLE_FIT: "the role",
    config.COMPONENT_COMPANY_NEED: "the needs the hiring team named",
}


def _join(words: Sequence[str]) -> str:
    """"a", "a and b", "a, b and c"."""
    if len(words) <= 1:
        return "".join(words)
    return f"{', '.join(words[:-1])} and {words[-1]}"


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)


def status_word(status: str | None) -> str | None:
    """The word a row shows when it has no grade: pending or not assessed."""
    if status == config.STATUS_NOT_ASSESSED:
        return STATUS_WORD_NOT_ASSESSED
    if status in (None, config.STATUS_PENDING):
        return STATUS_WORD_PENDING
    return None


def applicant_label(pipeline_status: str | None, source_type: str | None) -> str | None:
    """"Databank, not an applicant" / "Sourced, not an applicant", or None.

    Read from the pipeline STATUS, not the procurement type alone: a sourced
    candidate who then applies is an applicant, and the label must stop the
    moment that is true (owner ruling: databank discovery links as `sourced`
    and is labelled as such, CONTRACT v1).
    """
    if hiring_pipeline.normalize(pipeline_status) != hiring_pipeline.SOURCED:
        return None
    if source_type == "databank":
        return APPLICANT_LABEL_DATABANK
    return APPLICANT_LABEL_SOURCED


def pre_grade_word(status: str | None, pre_score: float | None) -> str | None:
    """The PRE-assessment grade word: the resume check alone, never blended."""
    if status not in ranking.RANKED_STATUSES:
        return None
    return ranking.grade_word(pre_score)


# ── The job's skills, read once per page ────────────────────────────────────


@dataclass(frozen=True)
class JobSkillsView:
    """The job's current skill names by id and its current contract digest."""

    names: Mapping[str, str]
    digest: str


async def job_skills_view(db: AsyncSession, job_id: uuid.UUID) -> JobSkillsView:
    """Read ONCE per page or per email batch, never per row: the contract the
    candidates are measured against (snapshot when locked, else live rows)."""
    contract = await assessment_contract.load_contract(db, job_id)
    return JobSkillsView(
        names={str(skill.id): skill.name for skill in contract.skills},
        digest=contract.digest,
    )


# ── Tags ─────────────────────────────────────────────────────────────────────


def _tag_text(tag: Mapping[str, Any], names: Mapping[str, str]) -> str | None:
    if tag.get("kind") == config.TAG_KIND_SKILL:
        return names.get(str(tag.get("skill_id")))
    text = tag.get("text")
    if not isinstance(text, str):
        return None
    return text.strip() or None


def evidence_tags(tags_json: Any, names: Mapping[str, str]) -> list[dict[str, Any]]:
    """The stored tags as `{text, polarity, shown_in_row}`, positives first.

    A skill tag's text is the skill's CURRENT name. A tag whose skill is no
    longer on the job is dropped rather than shown under a name nobody can find
    on the job any more; the row already reads as out of date in that case,
    because removing a skill changes the contract digest. The first
    `MAX_TAGS_SHOWN_IN_ROW` are flagged for the row; the dialog shows them all.
    """
    if not isinstance(tags_json, list):
        return []
    positives: list[dict[str, Any]] = []
    negatives: list[dict[str, Any]] = []
    for tag in tags_json:
        if not isinstance(tag, Mapping):
            continue
        text = _tag_text(tag, names)
        if not text:
            continue
        polarity = tag.get("polarity")
        if polarity == config.POLARITY_POSITIVE:
            positives.append({"text": text, "polarity": polarity})
        elif polarity == config.POLARITY_NEGATIVE:
            negatives.append({"text": text, "polarity": polarity})
    ordered = positives + negatives
    for index, tag in enumerate(ordered):
        tag["shown_in_row"] = index < config.MAX_TAGS_SHOWN_IN_ROW
    return ordered


def strengths_for_prompt(tags_json: Any, names: Mapping[str, str]) -> list[str]:
    """The skills the resume EVIDENCED, by current name, for an email prompt.

    Positive SKILL tags only: never a grade word, never a model-written tag,
    never a number, so a candidate cannot reconstruct an internal rating from
    the wording they receive.
    """
    if not isinstance(tags_json, list):
        return []
    found = (
        names.get(str(tag.get("skill_id")))
        for tag in tags_json
        if isinstance(tag, Mapping)
        and tag.get("kind") == config.TAG_KIND_SKILL
        and tag.get("polarity") == config.POLARITY_POSITIVE
    )
    return _dedupe(name for name in found if name)


def skill_list_phrase(names: Sequence[str]) -> str | None:
    """"Python, SQL and Kafka", or None for no names."""
    return _join(list(names)) or None


# ── Provenance ───────────────────────────────────────────────────────────────


def _resume_check_line(provenance: Mapping[str, Any]) -> str:
    present = provenance.get("components_present")
    parts = _dedupe(
        _PART_WORDS[part]
        for part in (present if isinstance(present, list) else [])
        if part in _PART_WORDS
    )
    if not parts:
        return "Resume check: read against this job."
    return f"Resume check: {_join(parts)}, read from the resume."


_CTC_PHRASES: Mapping[str, str] = {
    "Within range": "expected pay within range",
    "Below range": "expected pay below range",
    "Above range": "expected pay above range",
}


def _notice_phrase(word: str) -> str:
    if word == "Immediate":
        return "available immediately"
    if word == "Serving notice":
        return "serving notice"
    return f"notice period {word[:1].lower()}{word[1:]}"


def _answers_line(provenance: Mapping[str, Any], *, applied: bool) -> str:
    parts = provenance.get("validation_parts")
    parts = parts if isinstance(parts, Mapping) else {}
    phrases: list[str] = []
    ctc = parts.get(config.VALIDATION_CTC)
    if isinstance(ctc, str) and ctc:
        phrases.append(_CTC_PHRASES.get(ctc, f"expected pay {ctc.lower()}"))
    notice = parts.get(config.VALIDATION_NOTICE)
    if isinstance(notice, str) and notice:
        phrases.append(_notice_phrase(notice))
    documents = parts.get(config.VALIDATION_DOCUMENTS)
    if isinstance(documents, str) and documents:
        phrases.append(f"{documents[:1].lower()}{documents[1:]}")
    if phrases:
        return f"Application answers: {_join(phrases)}."
    return LINE_ANSWERS_NONE if applied else LINE_ANSWERS_NOT_APPLIED


def is_stale(
    status: str | None,
    provenance: Any,
    *,
    read_profile_id: Any,
    current_profile_id: Any,
    current_digest: str,
) -> tuple[bool, list[str]]:
    """(needs a refresh, the lines that say why). Derived, never stored."""
    if status == config.STATUS_LEGACY:
        return True, [LINE_LEGACY]
    if status != config.STATUS_SCORED:
        return False, []
    lines: list[str] = []
    digest = provenance.get("contract_digest") if isinstance(provenance, Mapping) else None
    if digest != current_digest:
        lines.append(LINE_SKILLS_CHANGED)
    if str(read_profile_id or "") != str(current_profile_id or ""):
        lines.append(LINE_RESUME_CHANGED)
    return bool(lines), lines


def provenance_words(
    *,
    status: str | None,
    failure_reason: str | None,
    provenance: Any,
    assessed: bool,
    must_have_failed: bool,
    weight_pct: int | float,
    applied: bool,
    stale_lines: Sequence[str] = (),
) -> list[str]:
    """Where this row's grade came from, as plain sentences. No number, no
    part name, no weight: the ratio is a word and the parts are "skills",
    "experience" and "the role"."""
    record = provenance if isinstance(provenance, Mapping) else {}
    has_pre = status in ranking.RANKED_STATUSES
    lines: list[str] = []
    if assessed:
        if has_pre:
            word = ranking.weight_word(weight_pct)
            if word == "not at all":
                lines.append("Tatva Assessment: does not move this grade.")
            else:
                lines.append(f"Tatva Assessment: {word} decides this grade.")
        else:
            lines.append(LINE_ASSESSMENT_ALONE)
    if must_have_failed:
        lines.append(LINE_CAPPED)

    if status == config.STATUS_SCORED:
        lines.append(_resume_check_line(record))
        lines.append(_answers_line(record, applied=applied))
        if record.get("last_attempt_failed"):
            lines.append(LINE_LAST_ATTEMPT_FAILED)
    elif status == config.STATUS_NOT_ASSESSED:
        lines.append(FAILURE_LINES.get(failure_reason or "", LINE_NO_RESULT))
    elif status in (None, config.STATUS_PENDING):
        lines.append(LINE_PENDING)
    lines.extend(stale_lines)
    return lines


# ── The row's AI Match block ─────────────────────────────────────────────────


def ai_match_fields(
    row: Mapping[str, Any], view: JobSkillsView, *, weight_pct: int | float
) -> dict[str, Any]:
    """The ranked row's AI Match fields, from the page query's columns.

    `row["rank_score"]` is the SQL key (`ranking.rank_score_sql`), read here
    ONLY to become a word and never copied onto the payload.
    """
    status = row.get("yukti_status") or config.STATUS_PENDING
    provenance = row.get("yukti_provenance_json")
    label = ranking.grade_word(row.get("rank_score"))
    stale, stale_lines = is_stale(
        status,
        provenance,
        read_profile_id=row.get("yukti_profile_id"),
        current_profile_id=row.get("profile_id"),
        current_digest=view.digest,
    )
    pipeline_status = row.get("status")
    return {
        "ai_match_status": status,
        "ai_match_label": label,
        "ai_match_status_word": None if label is not None else status_word(status),
        "evidence_tags": (
            evidence_tags(row.get("evidence_tags_json"), view.names)
            if status == config.STATUS_SCORED
            else []
        ),
        "provenance": provenance_words(
            status=status,
            failure_reason=row.get("yukti_failure_reason"),
            provenance=provenance,
            assessed=row.get("overall_score") is not None,
            must_have_failed=bool(row.get("must_have_failed")),
            weight_pct=weight_pct,
            applied=applicant_label(pipeline_status, row.get("source_type")) is None,
            stale_lines=stale_lines,
        ),
        "ai_match_stale": stale,
    }


def ai_score_summary(link: Any, view: JobSkillsView) -> dict[str, Any]:
    """The PRISM Report's AI Score section, for Phase 5 (PLAN-p2 section 7).

    The PRE-assessment reading only (the report is ABOUT the assessment, so it
    states the resume check beside it rather than blending the two), as one
    grade word, the tag texts, and provenance lines. Deterministic, no model.
    """
    status = getattr(link, "yukti_status", None) or config.STATUS_PENDING
    provenance = getattr(link, "yukti_provenance_json", None)
    _stale, stale_lines = is_stale(
        status,
        provenance,
        read_profile_id=getattr(link, "yukti_profile_id", None),
        current_profile_id=getattr(link, "profile_id", None),
        current_digest=view.digest,
    )
    return {
        "grade_word": pre_grade_word(status, getattr(link, "yukti_pre_score", None)),
        "status_word": status_word(status),
        "tags": [
            {"text": tag["text"], "polarity": tag["polarity"]}
            for tag in (
                evidence_tags(getattr(link, "evidence_tags_json", None), view.names)
                if status == config.STATUS_SCORED
                else []
            )
        ],
        "provenance": provenance_words(
            status=status,
            failure_reason=getattr(link, "yukti_failure_reason", None),
            provenance=provenance,
            assessed=False,
            must_have_failed=False,
            weight_pct=0,
            applied=applicant_label(
                getattr(link, "status", None), getattr(link, "source_type", None)
            )
            is None,
            stale_lines=stale_lines,
        ),
    }
