"""Hybrid ranking pipeline (ESD §8.2):

1. Semantic stage    — pgvector cosine (`<=>`, HNSW) JD-vs-profile, top-N.
2. Keyword stage     — Postgres ts_rank over profiles.resume_tsv using the
                       JD's skills/keywords, over the same eligible pool.
3. LLM re-rank stage — union of (1)+(2), batch-scored 0-100 with rationale via
                       the llm_router `rerank` chain (Groq-first).
4. Tier assignment   — app.services.tiers.assign_tier (inclusive-upward
                       boundaries, claude.md rule 8).

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
import uuid
from typing import Any, Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobCandidateLink, LinkSource, Profile
from app.services import llm_router
from app.services.embeddings import embed
# Track A owns tier assignment (signature: assign_tier(score: float) -> Tier).
from app.services.tiers import assign_tier

DEFAULT_TOP_N = 50
_RERANK_BATCH_SIZE = 10
_RESUME_SNIPPET_CHARS = 1500

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


def _profile_summary(profile: Profile) -> dict:
    """Compact, compensation-stripped candidate summary for the re-rank prompt."""
    parsed = _strip_compensation(profile.parsed_fields_json or {})
    snippet = (profile.resume_text or "")[:_RESUME_SNIPPET_CHARS]
    return {
        "profile_id": str(profile.id),
        "skills": parsed.get("skills", []),
        "total_experience_years": parsed.get("total_experience_years"),
        "education": parsed.get("education", []),
        "employment_history": parsed.get("employment_history", []),
        "resume_excerpt": snippet,
    }


def _parse_rerank_response(raw: str) -> list[dict]:
    """Accept either a bare JSON array or {"results": [...]} (json_object
    response modes require an object at the top level on some providers)."""
    data = json.loads(raw)
    if isinstance(data, dict):
        for key in ("results", "candidates", "scores"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            raise ValueError("re-rank response object has no results array")
    if not isinstance(data, list):
        raise ValueError("re-rank response is not a list")
    return data


async def _llm_rerank(
    session: AsyncSession, jd_text: str, profiles: list[Profile]
) -> dict[uuid.UUID, tuple[float, str]]:
    """Batch-score profiles against the JD. Returns {profile_id: (score, rationale)}."""
    results: dict[uuid.UUID, tuple[float, str]] = {}
    system = (
        "You are a recruitment matching engine. Score each candidate profile "
        "against the job description on a 0-100 scale for contextual fit "
        "(skills, seniority, domain, education). Respond with JSON only: "
        '{"results": [{"profile_id": "<uuid>", "score": <0-100 number>, '
        '"rationale": "<one or two sentences>"}]} — one entry per candidate, '
        "no extra keys, no prose outside the JSON."
    )
    for i in range(0, len(profiles), _RERANK_BATCH_SIZE):
        batch = profiles[i : i + _RERANK_BATCH_SIZE]
        user = json.dumps(
            {
                "job_description": jd_text,
                "candidates": [_profile_summary(p) for p in batch],
            },
            default=str,
        )
        raw = await llm_router.chat_completion(
            "rerank",
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format_json=True,
            session=session,
        )
        try:
            entries = _parse_rerank_response(raw)
        except (json.JSONDecodeError, ValueError):
            # One malformed batch response shouldn't sink the whole run —
            # profiles in this batch simply keep no score this round.
            continue
        valid_ids = {p.id for p in batch}
        for entry in entries:
            try:
                pid = uuid.UUID(str(entry["profile_id"]))
                score = float(entry["score"])
            except (KeyError, ValueError, TypeError):
                continue
            if pid not in valid_ids:
                continue
            score = max(0.0, min(100.0, score))
            results[pid] = (score, str(entry.get("rationale") or ""))
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

    # ── Stage 3: LLM re-rank (raises LLMUnavailableError only if the whole
    #    chain is exhausted — the Celery task's retry policy handles that) ──
    scores = await _llm_rerank(session, jd_text, profiles)

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

        if profile.id in scores:
            score, rationale = scores[profile.id]
            link.match_score = score
            link.match_rationale = rationale  # HR-visible, never candidate-visible
            link.tier = assign_tier(score)
            scored += 1

    await session.commit()
    return scored
