"""Candidate-facing job relevance — which published roles to show a candidate
on the New Jobs board.

The candidate board used to list every published job across every tenant. The
client's requirement (2026-07-27) is that a candidate sees only roles relevant
to THEM, derived from their profile: their main resume, its parsed skills, and
their advanced profile form. Anything else they want is reachable through the
search box, which deliberately bypasses relevance filtering entirely.

This is a candidate-side READ ranking. It is explicitly NOT the recruiter-side
matching pipeline in `services/matching.py`, and it must never decide who gets
scored (claude.md hard rule: every non-archived link on a job enters the scoring
pool). It only decides ordering and visibility on one screen.

Signals, in order of preference:
  1. Cosine similarity between `jobs.embedding` and the candidate's main
     `profiles.embedding` (pgvector, both written by the matching pipeline).
  2. Keyword overlap between the candidate's text (resume text + parsed skills +
     profile-form answers) and the job's title / level / declared skills.

Degradation is the point: no embedding, no resume, or an empty profile all fall
back to the next signal, and a candidate with NO profile signal at all sees the
newest jobs rather than an empty page.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import Profile
from app.models.job import Job
from app.services.candidate_profile_form import searchable_text

logger = logging.getLogger(__name__)

#: Cosine-similarity floor for "relevant". 0.30 is deliberately permissive —
#: a candidate seeing a slightly off-target role is a far smaller failure than a
#: candidate seeing an empty board.
SEMANTIC_FLOOR = 0.30

#: Keyword-overlap floor, as a fraction of the job's distinctive terms matched.
KEYWORD_FLOOR = 0.12

#: Never show fewer than this many jobs when any exist, whatever the scores say.
MIN_RESULTS = 6

_WORD_RE = re.compile(r"[a-z0-9+#.]{2,}")

#: Terms too common in job text to carry any signal.
_STOPWORDS = frozenset(
    """
    a an and are as at be by for from has have in is it its of on or that the to
    with will you your our we they this these those be been being do does not
    role job work working team teams company year years experience experienced
    strong good excellent ability able skills skill using use used new
    engineer engineering developer development manager management senior junior
    mid lead level department india remote hybrid onsite full time part
    """.split()
)


def _terms(value: str | None) -> set[str]:
    if not value:
        return set()
    return {w for w in _WORD_RE.findall(value.lower()) if w not in _STOPWORDS}


def _flatten(value: Any) -> str:
    """Everything in a JD / parsed-fields blob as one lowercase string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        return " ".join(_flatten(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten(v) for v in value)
    return str(value)


@dataclass(frozen=True)
class CandidateSignal:
    """Everything we know about a candidate for relevance purposes."""

    profile_id: uuid.UUID | None
    has_embedding: bool
    terms: frozenset[str]

    @property
    def is_empty(self) -> bool:
        """No usable signal — the caller should not filter anything out."""
        return not self.has_embedding and len(self.terms) < 3


def candidate_signal(
    profile: Profile | None, profile_form: dict[str, Any] | None
) -> CandidateSignal:
    """Build the keyword/embedding signal from the candidate's own record."""
    parts: list[str] = []
    profile_id: uuid.UUID | None = None
    has_embedding = False
    if profile is not None:
        profile_id = profile.id
        has_embedding = profile.embedding is not None
        # Resume text can be very long; the distinctive terms saturate quickly
        # and the tail is boilerplate (addresses, references, declarations).
        parts.append((profile.resume_text or "")[:20000])
        parts.append(_flatten(profile.parsed_fields_json))
    parts.append(searchable_text(profile_form))
    return CandidateSignal(
        profile_id=profile_id,
        has_embedding=has_embedding,
        terms=frozenset(_terms(" ".join(part for part in parts if part))),
    )


def _job_terms(job: Job) -> set[str]:
    jd = job.jd_json or {}
    # Weight the parts a candidate would themselves scan for: the title, the
    # declared skills, and the role summary. Full responsibility prose is
    # included but dilutes rather than drives the score.
    return _terms(
        " ".join(
            [
                job.title or "",
                job.level or "",
                job.department or "",
                _flatten(jd.get("skills")),
                _flatten(jd.get("role")),
                _flatten(jd.get("education")),
            ]
        )
    )


def keyword_score(signal: CandidateSignal, job: Job) -> float:
    """Fraction of the job's distinctive terms the candidate's profile covers."""
    job_terms = _job_terms(job)
    if not job_terms or not signal.terms:
        return 0.0
    return len(job_terms & signal.terms) / len(job_terms)


async def semantic_scores(
    session: AsyncSession, profile_id: uuid.UUID, job_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, float]:
    """Cosine similarity (1 - distance) per job, for jobs that have an embedding.

    Jobs whose embedding is NULL are simply absent from the result — the caller
    falls back to keyword scoring for those rather than treating them as a
    zero match, which would hide every job on a fresh install.
    """
    if not job_ids:
        return {}
    try:
        rows = await session.execute(
            text(
                """
                SELECT j.id AS job_id,
                       1 - (j.embedding <=> p.embedding) AS similarity
                  FROM jobs j
                  CROSS JOIN profiles p
                 WHERE p.id = :profile_id
                   AND p.embedding IS NOT NULL
                   AND j.embedding IS NOT NULL
                   AND j.id = ANY(CAST(:job_ids AS uuid[]))
                """
            ),
            {"profile_id": str(profile_id), "job_ids": [str(j) for j in job_ids]},
        )
    except Exception as exc:  # pragma: no cover - degrade, never crash a board read
        logger.warning("job_relevance.semantic_unavailable error=%s", type(exc).__name__)
        return {}
    return {row.job_id: float(row.similarity) for row in rows}


def matches_search(job: Job, query: str) -> bool:
    """Free-text search over the job's candidate-visible fields.

    Search is a deliberate ESCAPE HATCH from relevance: a candidate who types a
    role name must find it whether or not their profile says they are a fit.
    """
    needle = query.strip().lower()
    if not needle:
        return True
    haystack = " ".join(
        [
            job.title or "",
            job.department or "",
            job.level or "",
            _flatten((job.jd_json or {}).get("skills")),
            _flatten((job.jd_json or {}).get("role")),
        ]
    ).lower()
    # Every whitespace-separated token must appear, so "python senior" narrows
    # rather than widens. Substring matching keeps "eng" finding "engineer".
    return all(token in haystack for token in needle.split())


@dataclass(frozen=True)
class RankedJob:
    job: Job
    score: float
    relevant: bool


async def rank_jobs(
    session: AsyncSession,
    jobs: Iterable[Job],
    signal: CandidateSignal,
) -> list[RankedJob]:
    """Score and order published jobs for one candidate, best first.

    `relevant` marks the jobs the board shows by default. The ordering is always
    returned in full so the caller can decide how much to reveal.
    """
    jobs = list(jobs)
    if not jobs:
        return []

    semantic: dict[uuid.UUID, float] = {}
    if signal.profile_id is not None and signal.has_embedding:
        semantic = await semantic_scores(session, signal.profile_id, [j.id for j in jobs])

    ranked: list[RankedJob] = []
    for job in jobs:
        similarity = semantic.get(job.id)
        if similarity is not None:
            ranked.append(RankedJob(job, similarity, similarity >= SEMANTIC_FLOOR))
        else:
            overlap = keyword_score(signal, job)
            # Keyword scores are on a different scale to cosine similarity;
            # they are never mixed into a single ordering bucket with them
            # beyond this normalisation, which keeps semantic hits on top.
            ranked.append(RankedJob(job, overlap, overlap >= KEYWORD_FLOOR))

    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked


def visible(ranked: Sequence[RankedJob], signal: CandidateSignal) -> list[Job]:
    """The jobs to actually show, honouring the never-empty-board guarantee."""
    if signal.is_empty:
        # No profile signal at all: relevance would be a coin toss, so show
        # everything and let the candidate search.
        return [item.job for item in ranked]
    shown = [item.job for item in ranked if item.relevant]
    if len(shown) < MIN_RESULTS:
        shown = [item.job for item in ranked[:MIN_RESULTS]]
    return shown
