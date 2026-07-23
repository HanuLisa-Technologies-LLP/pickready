"""Hybrid ranking pipeline (ESD §8.2, scoring per API contract REVISION 2):

1. Semantic stage    — pgvector cosine (`<=>`, HNSW) JD-vs-profile, top-N.
2. Keyword stage     — Postgres ts_rank over profiles.resume_tsv using the
                       JD's skills/keywords, over the same eligible pool.
3. 4-parameter LLM scoring — union of (1)+(2): the LLM (rerank chain,
                       Groq-first) rates each profile on skills_match (0.35),
                       experience_relevance (0.30), role_alignment (0.20) and
                       education_fit (0.15), each an integer 1-10 + comment,
                       plus a genuinely holistic 5th overall comment. The
                       overall score is a PYTHON-computed weighted average —
                       never trusted from the LLM. Retrieval stages (1)+(2)
                       are prior signal only; embeddings never become the score.
4. Tier assignment   — app.services.tiers.assign_tier over overall×10
                       (inclusive-upward boundaries, claude.md rule 8).

The full breakdown JSON is stored in job_candidate_links.match_breakdown_json
(column added in migration 0002; written via raw SQL like jobs.embedding —
intentionally not on the SQLAlchemy model). match_score stays populated as
overall×10 so sorting/dashboard are unchanged; match_rationale = the overall
comment.

Eligible pool: profiles of candidates already linked to the job, plus Databank
candidates with consent_databank = true (Aspect 40 / FR-4.2). Consenting
Databank candidates not yet linked get a link created (source=databank) —
their Profile is reused as-is, never re-verified (claude.md rule 7).

ESD §16: the re-rank chain never receives compensation data — the job's
compensation_json is never sent, and compensation-ish keys are stripped from
parsed resume fields before prompting.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobCandidateLink, LinkSource, Profile
from app.services import llm_router
from app.services.embeddings import embed
# Track A owns tier assignment (signature: assign_tier(score: float) -> Tier).
from app.services.tiers import assign_tier

logger = logging.getLogger(__name__)

DEFAULT_TOP_N = 50
_RERANK_BATCH_SIZE = 10
_RESUME_SNIPPET_CHARS = 1500

# ── 4-parameter scoring (API contract rev 2) ────────────────────────────────
WEIGHTS: dict[str, float] = {
    "skills_match": 0.35,
    "experience_relevance": 0.30,
    "role_alignment": 0.20,
    "education_fit": 0.15,
}
PARAMETERS: tuple[str, ...] = tuple(WEIGHTS)

# Aspect-numbering contract (API_CONTRACT.md rev 2): aspect 23 = current/most
# recent designation and core duties; aspects 8-13 = education & qualifications.
_ASPECT_ROLE_KEY = "23"
_EDUCATION_ASPECT_KEYS: dict[str, str] = {
    "8": "highest_degree_level",
    "9": "specialization",
    "10": "institution",
    "11": "year_of_completion",
    "12": "professional_certifications",
    "13": "additional_qualifications",
}

# ESD §16: strip anything compensation-shaped before it reaches the LLM.
_COMPENSATION_KEY_MARKERS = ("ctc", "salary", "compensation", "gross", "pay", "remuneration")


def _strip_compensation(value: Any) -> Any:
    """Recursively drop dict keys that look like compensation data."""
    if isinstance(value, dict):
        return {
            k: _strip_compensation(v)
            for k, v in value.items()
            if not any(m in k.lower() for m in _COMPENSATION_KEY_MARKERS)
        }
    if isinstance(value, list):
        return [_strip_compensation(v) for v in value]
    return value


def _vector_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


def _jd_text(job: Job) -> str:
    jd = _strip_compensation(job.jd_json or {})
    parts = [f"Job title: {job.title}"]
    if job.department:
        parts.append(f"Department: {job.department}")
    if job.level:
        parts.append(f"Level: {job.level}")
    for key in (
        "role", "responsibilities", "accountabilities", "education",
        "skills", "experience_years", "reporting_to", "reportees",
    ):
        val = jd.get(key)
        if val:
            parts.append(f"{key.replace('_', ' ').title()}: {json.dumps(val, default=str)}")
    return "\n".join(parts)


def _keyword_query_terms(job: Job) -> str:
    jd = job.jd_json or {}
    skills = jd.get("skills") or []
    terms = [s for s in skills if isinstance(s, str)]
    terms.append(job.title)
    return " ".join(terms)


async def _semantic_stage(
    session: AsyncSession, job_id: uuid.UUID, jd_vec: str, top_n: int
) -> list[uuid.UUID]:
    """Top-N profile ids by cosine distance, best profile per candidate."""
    rows = await session.execute(
        text(
            """
            SELECT profile_id FROM (
                SELECT DISTINCT ON (p.candidate_id)
                       p.id AS profile_id,
                       p.embedding <=> CAST(:jd_vec AS vector) AS dist
                FROM profiles p
                JOIN candidates c ON c.id = p.candidate_id
                WHERE p.embedding IS NOT NULL
                  AND (
                    c.consent_databank = true
                    OR EXISTS (
                        SELECT 1 FROM job_candidate_links l
                        WHERE l.job_id = :job_id AND l.candidate_id = c.id
                    )
                  )
                ORDER BY p.candidate_id, dist
            ) ranked
            ORDER BY dist
            LIMIT :top_n
            """
        ),
        {"jd_vec": jd_vec, "job_id": str(job_id), "top_n": top_n},
    )
    return [r.profile_id for r in rows]


async def _keyword_stage(
    session: AsyncSession, job_id: uuid.UUID, query_terms: str, top_n: int
) -> list[uuid.UUID]:
    """Top-N profile ids by full-text rank over resume_tsv (catches exact
    terms — tool names, certifications — that embeddings can miss)."""
    if not query_terms.strip():
        return []
    rows = await session.execute(
        text(
            """
            SELECT profile_id FROM (
                SELECT DISTINCT ON (p.candidate_id)
                       p.id AS profile_id,
                       ts_rank(p.resume_tsv, plainto_tsquery('english', :q)) AS rank
                FROM profiles p
                JOIN candidates c ON c.id = p.candidate_id
                WHERE p.resume_tsv @@ plainto_tsquery('english', :q)
                  AND (
                    c.consent_databank = true
                    OR EXISTS (
                        SELECT 1 FROM job_candidate_links l
                        WHERE l.job_id = :job_id AND l.candidate_id = c.id
                    )
                  )
                ORDER BY p.candidate_id, rank DESC
            ) ranked
            ORDER BY rank DESC
            LIMIT :top_n
            """
        ),
        {"q": query_terms, "job_id": str(job_id), "top_n": top_n},
    )
    return [r.profile_id for r in rows]


def compute_overall_score(scores: dict[str, int | float]) -> float:
    """Weighted average of the four 1-10 parameter scores, rounded to 1 decimal.

    Pure Python math (banker's rounding over IEEE-754 doubles) — the overall
    score is NEVER taken from the LLM. Unit-tested in tests/test_scoring.py.
    """
    return round(sum(WEIGHTS[p] * float(scores[p]) for p in PARAMETERS), 1)


def _coerce_param_score(value: Any) -> int | None:
    """A parameter score must be an integer 1-10 (integral JSON floats like
    8.0 are accepted; bools, fractions, and out-of-range values are not)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        score = value
    elif isinstance(value, float) and value.is_integer():
        score = int(value)
    else:
        return None
    return score if 1 <= score <= 10 else None


def _validate_entry(entry: Any) -> dict | None:
    """Validate one LLM result entry into the contract's breakdown shape
    (overall computed in Python). Returns None when malformed."""
    if not isinstance(entry, dict):
        return None
    breakdown: dict[str, Any] = {}
    for param in PARAMETERS:
        block = entry.get(param)
        if not isinstance(block, dict):
            return None
        score = _coerce_param_score(block.get("score"))
        if score is None:
            return None
        breakdown[param] = {
            "score": score,
            "comment": str(block.get("comment") or "").strip(),
        }
    # The 5th, holistic comment is required — it becomes match_rationale.
    overall_comment = entry.get("overall_comment")
    if not isinstance(overall_comment, str) or not overall_comment.strip():
        return None
    breakdown["overall"] = {
        "score": compute_overall_score({p: breakdown[p]["score"] for p in PARAMETERS}),
        "comment": overall_comment.strip(),
    }
    return breakdown


def _profile_summary(profile: Profile) -> dict:
    """Compact, compensation-stripped candidate summary for the scoring prompt.

    Aspect 23 (current designation + core duties) and aspects 8-13 (education)
    are surfaced as dedicated fields when present — resume text is fallback
    only (API contract rev 2 aspect-numbering block).
    """
    parsed = _strip_compensation(profile.parsed_fields_json or {})
    aspects_raw = profile.aspects_json if isinstance(profile.aspects_json, dict) else {}
    aspects = _strip_compensation(aspects_raw)
    summary: dict[str, Any] = {
        "profile_id": str(profile.id),
        "skills": parsed.get("skills", []),
        "total_experience_years": parsed.get("total_experience_years"),
        "education": parsed.get("education", []),
        "employment_history": parsed.get("employment_history", []),
        "resume_excerpt": (profile.resume_text or "")[:_RESUME_SNIPPET_CHARS],
    }
    if aspects.get(_ASPECT_ROLE_KEY):
        # role_alignment's primary signal: the candidate's self-reported
        # current/most recent designation and duties (catches title
        # inflation/deflation that resume text can mask).
        summary["current_designation_and_duties"] = aspects[_ASPECT_ROLE_KEY]
    education_aspects = {
        label: aspects[key]
        for key, label in _EDUCATION_ASPECT_KEYS.items()
        if aspects.get(key)
    }
    if education_aspects:
        summary["education_aspects"] = education_aspects
    return summary


def _parse_scoring_response(raw: str) -> list[dict]:
    """Accept either a bare JSON array or {"results": [...]} (json_object
    response modes require an object at the top level on some providers)."""
    data = json.loads(raw)
    if isinstance(data, dict):
        for key in ("results", "candidates", "scores"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            raise ValueError("scoring response object has no results array")
    if not isinstance(data, list):
        raise ValueError("scoring response is not a list")
    return data


_SCORING_SYSTEM_PROMPT = (
    "You are a recruitment matching engine. For EACH candidate, rate fit "
    "against the job description on exactly four parameters, each an INTEGER "
    "score from 1 (very poor fit) to 10 (excellent fit) plus a short comment:\n"
    "1. skills_match — the JD's Skills requirements vs the candidate's "
    "experience, education, and certifications. Judge semantic skill "
    "equivalence (comparable tools/frameworks count), not literal keyword "
    "overlap.\n"
    "2. experience_relevance — not just years of experience: has the candidate "
    "performed the same function, at a comparable seniority/level?\n"
    "3. role_alignment — the candidate's ACTUAL most recent designation and "
    "core duties (use the 'current_designation_and_duties' field when present; "
    "otherwise fall back to the resume) vs the JD's Role, Responsibilities, "
    "and Accountabilities. Judge duties over titles — penalize title inflation "
    "or deflation.\n"
    "4. education_fit — degree level and specialization (use the "
    "'education_aspects' field when present; otherwise fall back to resume "
    "education) vs the JD's Education requirement.\n"
    "Also write overall_comment: a genuinely holistic 1-3 sentence assessment "
    "of the candidate for this job. It must be a fresh synthesis, NOT a "
    "concatenation or restatement of the four parameter comments. Do NOT "
    "compute or output any overall score — it is computed elsewhere.\n"
    'Respond with JSON only: {"results": [{"profile_id": "<uuid>", '
    '"skills_match": {"score": <int 1-10>, "comment": "<short>"}, '
    '"experience_relevance": {"score": <int 1-10>, "comment": "<short>"}, '
    '"role_alignment": {"score": <int 1-10>, "comment": "<short>"}, '
    '"education_fit": {"score": <int 1-10>, "comment": "<short>"}, '
    '"overall_comment": "<holistic>"}]} — one entry per candidate, no extra '
    "keys, no prose outside the JSON."
)


def _extract_valid(
    raw: str, wanted_ids: set[uuid.UUID]
) -> tuple[dict[uuid.UUID, dict], set[uuid.UUID]]:
    """Pull validated breakdowns out of one LLM response. Returns
    (valid breakdowns by profile id, ids still missing/malformed)."""
    got: dict[uuid.UUID, dict] = {}
    try:
        entries = _parse_scoring_response(raw)
    except (json.JSONDecodeError, ValueError):
        return {}, set(wanted_ids)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            pid = uuid.UUID(str(entry.get("profile_id")))
        except (ValueError, TypeError, AttributeError):
            continue
        if pid not in wanted_ids or pid in got:
            continue
        breakdown = _validate_entry(entry)
        if breakdown is not None:
            got[pid] = breakdown
    return got, wanted_ids - set(got)


async def _score_batch(
    session: AsyncSession, jd_text: str, batch: list[Profile]
) -> dict[uuid.UUID, dict]:
    """Score one batch, retrying malformed entries once with a corrective
    message; profiles still malformed after the retry are skipped with a
    logged warning — a bad LLM response never crashes the batch."""
    user = json.dumps(
        {"job_description": jd_text, "candidates": [_profile_summary(p) for p in batch]},
        default=str,
    )
    messages = [
        {"role": "system", "content": _SCORING_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    raw = await llm_router.chat_completion(
        "rerank", messages, response_format_json=True, session=session
    )
    results, missing = _extract_valid(raw, {p.id for p in batch})
    if missing:
        corrective = (
            "Your previous response was missing or malformed for these "
            f"profile_ids: {sorted(str(p) for p in missing)}. Every parameter "
            "score MUST be an integer between 1 and 10, every parameter needs "
            "a comment, and overall_comment must be a non-empty holistic "
            "sentence. Re-emit a complete, valid JSON response in the exact "
            "schema for ONLY those profile_ids."
        )
        retry_messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": corrective},
        ]
        raw_retry = await llm_router.chat_completion(
            "rerank", retry_messages, response_format_json=True, session=session
        )
        retried, still_missing = _extract_valid(raw_retry, missing)
        results.update(retried)
        for pid in still_missing:
            logger.warning(
                "matching.profile_skipped profile_id=%s reason=malformed_llm_scores",
                pid,
            )
    return results


async def _llm_score(
    session: AsyncSession, jd_text: str, profiles: list[Profile]
) -> dict[uuid.UUID, dict]:
    """4-parameter scoring for all shortlisted profiles.

    Returns {profile_id: breakdown} where breakdown matches the contract's
    "Matching results" JSON block (overall.score computed in Python)."""
    results: dict[uuid.UUID, dict] = {}
    for i in range(0, len(profiles), _RERANK_BATCH_SIZE):
        results.update(await _score_batch(session, jd_text, profiles[i : i + _RERANK_BATCH_SIZE]))
    return results


async def run_matching(
    session: AsyncSession, job_id: uuid.UUID | str, top_n: int = DEFAULT_TOP_N
) -> int:
    """Run the full hybrid pipeline for one job. Returns the number of links scored."""
    job_id = uuid.UUID(str(job_id))
    job = await session.get(Job, job_id)
    if job is None:
        raise ValueError(f"Job {job_id} not found")

    # ── JD embedding (stored on the jobs row for reuse; column added in migration) ──
    jd_text = _jd_text(job)
    jd_embedding = (await embed([jd_text]))[0]
    jd_vec = _vector_literal(jd_embedding)
    await session.execute(
        text("UPDATE jobs SET embedding = CAST(:v AS vector) WHERE id = :id"),
        {"v": jd_vec, "id": str(job_id)},
    )

    # ── Stages 1 + 2, deduplicated union ──
    semantic_ids = await _semantic_stage(session, job_id, jd_vec, top_n)
    keyword_ids = await _keyword_stage(session, job_id, _keyword_query_terms(job), top_n)
    profile_ids: list[uuid.UUID] = list(dict.fromkeys([*semantic_ids, *keyword_ids]))
    if not profile_ids:
        await session.commit()
        return 0

    profiles = (
        (await session.execute(select(Profile).where(Profile.id.in_(profile_ids))))
        .scalars()
        .all()
    )
    profiles_by_id = {p.id: p for p in profiles}
    # keep union order
    profiles = [profiles_by_id[pid] for pid in profile_ids if pid in profiles_by_id]

    # ── Stage 3: 4-parameter LLM scoring (raises LLMUnavailableError only if
    #    the whole chain is exhausted — the Celery task's retry policy handles
    #    that; individual malformed profiles are skipped with a warning) ──
    breakdowns = await _llm_score(session, jd_text, profiles)

    # ── Ensure links exist for consenting Databank candidates (FR-4.2/4.4) ──
    existing_links = (
        (
            await session.execute(
                select(JobCandidateLink).where(JobCandidateLink.job_id == job_id)
            )
        )
        .scalars()
        .all()
    )
    links_by_candidate = {l.candidate_id: l for l in existing_links}

    consenting = {
        r.candidate_id
        for r in await session.execute(
            text(
                "SELECT c.id AS candidate_id FROM candidates c "
                "WHERE c.consent_databank = true AND c.id = ANY(CAST(:cids AS uuid[]))"
            ),
            {"cids": [str(p.candidate_id) for p in profiles]},
        )
    }

    scored = 0
    scored_links: list[tuple[JobCandidateLink, dict]] = []
    for profile in profiles:
        link = links_by_candidate.get(profile.candidate_id)
        if link is None:
            if profile.candidate_id not in consenting:
                # Not linked and not a consenting Databank candidate — skip.
                continue
            link = JobCandidateLink(
                tenant_id=job.tenant_id,
                job_id=job_id,
                candidate_id=profile.candidate_id,
                profile_id=profile.id,
                source=LinkSource.databank,  # existing Profile reused as-is
            )
            session.add(link)
            links_by_candidate[profile.candidate_id] = link
        if link.profile_id is None:
            link.profile_id = profile.id

        if profile.id in breakdowns:
            breakdown = breakdowns[profile.id]
            overall = breakdown["overall"]["score"]
            # match_score stays 0-100 (overall × 10) so sorting/dashboard and
            # the tier boundary rule are unchanged.
            link.match_score = round(overall * 10, 1)
            # HR-visible, never candidate-visible — the holistic 5th comment.
            link.match_rationale = breakdown["overall"]["comment"]
            link.tier = assign_tier(link.match_score)
            scored_links.append((link, breakdown))
            scored += 1

    # match_breakdown_json is intentionally NOT on the SQLAlchemy model (same
    # pattern as jobs.embedding) — flush so new links get ids, then write the
    # breakdown via raw SQL.
    await session.flush()
    for link, breakdown in scored_links:
        await session.execute(
            text(
                "UPDATE job_candidate_links "
                "SET match_breakdown_json = CAST(:breakdown AS jsonb) "
                "WHERE id = :id"
            ),
            {"breakdown": json.dumps(breakdown), "id": str(link.id)},
        )

    await session.commit()
    return scored
