"""What Yukti's model may read: the job context and one candidate's resume.

Two builders and nothing else, so the one place that decides what reaches the
prompt is two functions a reviewer can read.

THE JOB CONTEXT (`job_context`)
-------------------------------
The job's skills come from the ASSESSMENT CONTRACT (`assessment_contract.
load_contract`): the saved skills, their hidden priorities and evidence lines,
the hidden role summary, and the digest that names exactly that content. So
Yukti ranks against the same skills Vaada asks about and Miti grades, and a
stored result records WHICH contract it was computed against (staleness is
derived from that digest, never from a timestamp).

Company needs come from the SAVED Job SWOT only (`swot_analysis.is_saved`): the
Weaknesses, Opportunities and Threats sentences, each a NAMED need the model
must cite resume evidence against. A model draft nobody saved is not the
team's statement of what the company needs, which is the rule Sutra's skills
draft already follows.

NEVER READ: `jobs.compensation_json`, `jobs.level`, `jd_json.reportees`, any
customer's past hires. The job description is compensation-redacted line by
line (`compensation_guard`), because a pasted JD routinely carries a
"Compensation" section.

THE CANDIDATE INPUT (`candidate_input`)
---------------------------------------
The resume TEXT of the profile the link was made with, and nothing else: no
parsed-field dictionary (it carries employer and institution names as fields
and, from some parsers, a stated CTC), no profile-form aspects, no application
answers (those are part six, `validation_fit`, and never reach a model). The
text is anonymised (`yukti.anonymise`, name and employer and institution
blind), compensation-redacted, and cut to `RESUME_CHARS` on a LINE boundary.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import (
    assessment_contract,
    compensation_guard,
    conversation_guardrails,
    swot_analysis,
)
from app.services.assessment_contract import ContractSkill
from app.services.yukti import anonymise, config

__all__ = [
    "CandidateInput",
    "JobContext",
    "NamedNeed",
    "NotEvaluable",
    "PreparedResume",
    "candidate_input",
    "cut_on_line",
    "experience_band",
    "jd_text",
    "job_context",
    "needs_from_swot",
    "prepare_resume",
]

#: The two buckets a resume is judged on. Behavioural skills are assessed in
#: the conversation, never from a resume (owner decision D2).
JUDGED_BUCKETS: tuple[str, ...] = (
    assessment_contract.BUCKET_MUST_HAVE,
    assessment_contract.BUCKET_NICE_TO_HAVE,
)

#: The structured JD keys the EMBEDDING text reads, in order. `reportees` and
#: `experience_years` are absent on purpose: `reportees` was dropped from the
#: product on 2026-07-28 and the band comes from the job's own columns.
_JD_JSON_KEYS: tuple[str, ...] = (
    "role",
    "responsibilities",
    "accountabilities",
    "education",
    "skills",
    "reporting_to",
)


@dataclass(frozen=True)
class NamedNeed:
    """One sentence of the saved SWOT the model may cite evidence against."""

    ref: str      # "n1".. opaque to the model, stable within one run
    source: str   # "weakness" | "opportunity" | "threat"
    text: str


@dataclass(frozen=True)
class JobContext:
    """Everything about the job a Yukti prompt may carry. Built once per run."""

    job_id: uuid.UUID
    title: str
    department: str | None
    grade_label: str
    experience_band: str | None
    jd_text: str
    role_summary: str
    skills: tuple[ContractSkill, ...]        # Must-have and Nice-to-have only
    all_skill_names: tuple[str, ...]         # every bucket, for the scrub guard
    needs: tuple[NamedNeed, ...]
    contract_digest: str
    contract_version: int
    jd_redacted: bool


@dataclass(frozen=True)
class CandidateInput:
    """One resume, ready for the prompt: anonymised, CTC-free, bounded."""

    link_id: uuid.UUID
    profile_id: uuid.UUID
    resume_text: str
    redacted: bool       # compensation lines were removed
    truncated: bool      # the text was cut at RESUME_CHARS
    neutralised: bool    # an instruction-shaped passage was neutralised


@dataclass(frozen=True)
class NotEvaluable:
    """A link that cannot be read at all, and why (a `config.FAILURE_*`)."""

    link_id: uuid.UUID
    profile_id: uuid.UUID | None
    reason: str


# ── Pure helpers ─────────────────────────────────────────────────────────────


def experience_band(min_years: Any, max_years: Any) -> str | None:
    """"5 to 9 years", "5 or more years", "up to 3 years", or None."""
    if min_years is not None and max_years is not None:
        return f"{min_years} to {max_years} years"
    if min_years is not None:
        return f"{min_years} or more years"
    if max_years is not None:
        return f"up to {max_years} years"
    return None


def _grade_label(grade: str | None) -> str:
    # The one grade vocabulary. Imported lazily: `job_candidates` is the
    # ranked-table module and importing it at module scope would make every
    # reader of a Yukti constant load the ranked-table query.
    from app.services.job_candidates import grade_label  # noqa: PLC0415

    return grade_label(grade)


def cut_on_line(text: str, limit: int) -> tuple[str, bool]:
    """`text` cut to at most `limit` characters on a LINE boundary.

    Never mid-line: a model handed half a line completes it from its own
    priors, and a quote it copies from a cut line would not ground against the
    full resume either. A first line longer than the limit is the one case
    with nothing whole to keep; it is cut at the last word boundary inside the
    limit, because returning nothing would silence the resume entirely.
    """
    if len(text) <= limit:
        return text, False
    kept: list[str] = []
    used = 0
    for line in text.split("\n"):
        extra = len(line) + (1 if kept else 0)
        if used + extra > limit:
            break
        kept.append(line)
        used += extra
    if kept:
        return "\n".join(kept), True
    head = text[:limit]
    boundary = head.rfind(" ")
    return (head[:boundary] if boundary > 0 else head), True


_BLANK_RUN = re.compile(r"[ \t\u00a0]+")


def _tidy_lines(text: str) -> str:
    """Collapse runs of spaces inside each line and drop empty lines.

    The scrub replaces a name with a space, so without this a resume carries
    the ghost of every removed name as a double space the model may copy into
    a quote. Grounding normalises whitespace too; this keeps the prompt clean.
    """
    lines = (_BLANK_RUN.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


@dataclass(frozen=True)
class PreparedResume:
    """One resume as the prompt will carry it, and what was done to it."""

    text: str
    redacted: bool       # compensation lines were removed
    truncated: bool      # the text was cut at RESUME_CHARS
    neutralised: bool    # an instruction-shaped passage was neutralised


def prepare_resume(
    resume_text: str | None,
    *,
    identities: Sequence[str],
    organisations: Sequence[str],
    protected_terms: Sequence[str],
) -> PreparedResume:
    """One resume, ready for the prompt. Pure.

    ORDER MATTERS: anonymise first (the scrub needs the original line
    structure to find names), redact second (a CTC line goes whole), then the
    injection guard, tidy, and cut, so the character budget is spent on text
    that will actually be sent.

    A resume is a file a candidate uploaded, so it passes
    `conversation_guardrails.inspect_answer` exactly as a retrieved chunk does:
    an instruction-shaped passage is replaced by a visible marker and the run
    RECORDS that it happened. It is provenance, never a rejection (the
    2026-09-09 rule for hidden text), and grounding checks every quote against
    this same sanitised text, so nothing the model was not shown can ground.
    """
    scrubbed = anonymise.anonymise(
        resume_text,
        identities=identities,
        organisations=organisations,
        protected_terms=protected_terms,
    )
    redaction = compensation_guard.redact(scrubbed)
    guard = conversation_guardrails.inspect_answer(redaction.text)
    tidy = _tidy_lines(guard.sanitized)
    cut, truncated = cut_on_line(tidy, config.RESUME_CHARS)
    return PreparedResume(
        text=cut,
        redacted=redaction.removed,
        truncated=truncated,
        neutralised=guard.violation is not None,
    )


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
_BULLET = re.compile(r"^\s*(?:[-*\u2022\u25cf\u00b7]+|\d+[.)])\s*")


def needs_from_swot(
    weaknesses: str | None, opportunities: str | None, threats: str | None
) -> tuple[NamedNeed, ...]:
    """The saved SWOT's W, O and T sentences as named needs. Pure.

    Weaknesses first, because a gap the team names is the clearest statement
    of what a new hire must bring; then Opportunities, then Threats. Split on
    lines and sentence ends; a bullet marker is removed; a fragment under
    three words is not a need; a sentence over `MAX_NEED_CHARS` is dropped
    WHOLE rather than cut; a sentence stating pay is dropped (a SWOT can name a
    salary budget). At most `MAX_NEEDS`, deduplicated case-insensitively.
    Strengths are never a need: they describe what the team already has.
    """
    needs: list[NamedNeed] = []
    seen: set[str] = set()
    for source, body in zip(config.NEED_SOURCES, (weaknesses, opportunities, threats)):
        for line in str(body or "").splitlines():
            for sentence in _SENTENCE_BREAK.split(_BULLET.sub("", line)):
                clean = " ".join(sentence.split())
                if len(clean.split()) < 3 or len(clean) > config.MAX_NEED_CHARS:
                    continue
                if compensation_guard.mentions_compensation(clean):
                    continue
                key = clean.casefold()
                if key in seen:
                    continue
                seen.add(key)
                needs.append(NamedNeed(f"n{len(needs) + 1}", source, clean))
                if len(needs) >= config.MAX_NEEDS:
                    return tuple(needs)
    return tuple(needs)


def _clean_jd_value(value: Any) -> Any:
    """A structured JD value with every compensation-stating string removed."""
    value = compensation_guard.strip_keys(value)
    if isinstance(value, str):
        return compensation_guard.redact_text(value)
    if isinstance(value, list):
        return [
            _clean_jd_value(item)
            for item in value
            if not (isinstance(item, str) and compensation_guard.mentions_compensation(item))
        ]
    if isinstance(value, dict):
        return {key: _clean_jd_value(item) for key, item in value.items()}
    return value


def jd_text(job: Any) -> str:
    """The job, as text, for the JOB EMBEDDING. No compensation, no `level`.

    Replaces `matching._jd_text`, which read `jobs.level` (a pre-2026-07-28
    field no form collects) and `jd_json.reportees` (dropped the same day).
    The grade and the experience band are what the job states now.
    `scripts/reembed.py` mirrors this in SQL and `models/job.py` lists the
    columns it reads; both move with it (Phase 2 WP-B/WP-F).
    """
    jd = compensation_guard.strip_keys(dict(getattr(job, "jd_json", None) or {}))
    parts = [f"Job title: {job.title}"]
    if getattr(job, "department", None):
        parts.append(f"Department: {job.department}")
    parts.append(f"Grade: {_grade_label(getattr(job, 'assessment_grade', None))}")
    band = experience_band(
        getattr(job, "experience_min_years", None),
        getattr(job, "experience_max_years", None),
    )
    if band:
        parts.append(f"Experience: {band}")
    for key in _JD_JSON_KEYS:
        value = _clean_jd_value(jd.get(key))
        if value:
            parts.append(
                f"{key.replace('_', ' ').title()}: {json.dumps(value, default=str)}"
            )
    return compensation_guard.redact_text("\n".join(parts))


# ── The two builders ─────────────────────────────────────────────────────────


async def job_context(db: AsyncSession, job: Any) -> JobContext:
    """The job, as the Yukti prompt reads it. Read once per run."""
    contract = await assessment_contract.load_contract(db, job.id)
    swot_row = await swot_analysis.get(db, job)
    needs: tuple[NamedNeed, ...] = ()
    if swot_analysis.is_saved(swot_row):
        needs = needs_from_swot(
            swot_row.weaknesses, swot_row.opportunities, swot_row.threats
        )
    markdown = str(getattr(job, "jd_markdown", None) or "").strip()
    source = markdown or jd_text(job)
    redaction = compensation_guard.redact(source)
    jd_prompt, _ = cut_on_line(_tidy_lines(redaction.text), config.JD_CHARS)
    judged = tuple(s for s in contract.skills if s.bucket in JUDGED_BUCKETS)
    return JobContext(
        job_id=job.id,
        title=str(job.title or ""),
        department=getattr(job, "department", None) or None,
        grade_label=_grade_label(contract.grade),
        experience_band=experience_band(
            getattr(job, "experience_min_years", None),
            getattr(job, "experience_max_years", None),
        ),
        jd_text=jd_prompt,
        role_summary=compensation_guard.redact_text(contract.role_summary).strip(),
        skills=judged,
        all_skill_names=tuple(s.name for s in contract.skills),
        needs=needs,
        contract_digest=contract.digest,
        contract_version=contract.version,
        jd_redacted=redaction.removed,
    )


async def candidate_input(
    db: AsyncSession, ctx: JobContext, link: Any, profile: Any | None
) -> CandidateInput | NotEvaluable:
    """One link's resume, ready for the prompt, or why it cannot be read.

    The profile passed in MUST be the one the link was made with; the caller
    (the matching run) owns that rule, and a mismatch here is refused rather
    than read, because scoring a different resume against this application is
    the audit #12 defect.
    """
    if profile is None:
        return NotEvaluable(link.id, None, config.FAILURE_NO_RESUME)
    if getattr(link, "profile_id", None) != profile.id:
        raise ValueError(
            f"link {link.id} was made with profile {link.profile_id}, "
            f"not {profile.id}: Yukti reads the resume the application carries"
        )
    identities = await anonymise.identities(db, getattr(profile, "candidate_id", None))
    prepared = prepare_resume(
        getattr(profile, "resume_text", None),
        identities=identities,
        organisations=anonymise.organisations_from(
            getattr(profile, "parsed_fields_json", None)
        ),
        protected_terms=ctx.all_skill_names,
    )
    if not prepared.text.strip():
        return NotEvaluable(link.id, profile.id, config.FAILURE_NO_RESUME_TEXT)
    return CandidateInput(
        link_id=link.id,
        profile_id=profile.id,
        resume_text=prepared.text,
        redacted=prepared.redacted,
        truncated=prepared.truncated,
        neutralised=prepared.neutralised,
    )
