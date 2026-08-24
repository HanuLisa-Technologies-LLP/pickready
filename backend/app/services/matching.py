"""Hybrid ranking pipeline (ESD §8.2, scoring per API contract REVISION 2):

1. Semantic stage    — pgvector cosine (`<=>`, HNSW) JD-vs-profile, top-N.
2. Keyword stage     — Postgres ts_rank over profiles.resume_tsv using the
                       JD's skills/keywords, over the same eligible pool.
3. 4-parameter LLM scoring — union of (1)+(2): the LLM (rerank chain,
                       Groq-first) rates each profile on skills_match,
                       experience_relevance, role_alignment and education_fit,
                       each an integer 1-10 + comment, plus a genuinely
                       holistic 5th overall comment. There is NO weighting
                       between the four (spec 2026-07-30): the internal overall
                       is their plain mean, computed in Python and never
                       trusted from the LLM. Retrieval stages (1)+(2) are prior
                       signal only; embeddings never become the score.
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
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobCandidateLink, LinkSource, Profile
from app.services import llm_router, matching_categories, rating
from app.services.embeddings import EmbeddingError, embed
# Track A owns tier assignment (signature: assign_tier(score: float) -> Tier).
from app.services.tiers import assign_tier
from app.prompts import registry

logger = logging.getLogger(__name__)

DEFAULT_TOP_N = 50
_RERANK_BATCH_SIZE = 10
_RESUME_SNIPPET_CHARS = 1500

#: The router task type every scoring call in this module is made under. Named
#: once because the `ai_score` artifact reports the routing policy the
#: explanation was written under, and a second literal would let the artifact
#: describe a chain the call was not actually made on.
_SCORING_TASK = "rerank"

# Deterministic-fallback band (used when the LLM chain is fully unavailable):
# retrieval rank is mapped into [_FALLBACK_MIN, _FALLBACK_MAX] so ordering is
# preserved but the ceiling stays below the "Highly Matching" boundary — a
# fallback score never fabricates a top-tier match (8×10 = 80 = Moderately).
_FALLBACK_MIN = 4
_FALLBACK_MAX = 8

# ── Comment word-range contract ─────────────────────────────────────────────
# Every stored comment (the four parameters + the holistic overall) must be
# 25-30 words. The LLM is *asked* for that range, the response is validated in
# Python, one corrective regeneration pass is made, and anything still outside
# the range is deterministically repaired before storage. A comment outside
# 25-30 words is never persisted.
COMMENT_MIN_WORDS = 25
COMMENT_MAX_WORDS = 30

# A "word" is any whitespace-delimited token containing at least one
# alphanumeric character — bare dashes, bullets and stray punctuation don't
# count, which matches how a human counts the words in a sentence.
_WORD_TOKEN_RE = re.compile(r"[0-9A-Za-z]")

# Deterministic, neutral continuation clauses used to repair an under-length
# comment when the model will not cooperate. Ordered; appended whole so the
# result still reads as English, then trimmed back to COMMENT_MAX_WORDS.
_PAD_CLAUSES: tuple[str, ...] = (
    "Confirm remaining details during structured screening.",
    "Reviewers should confirm this evidence during a structured screening conversation.",
    "Reviewers should read the full resume alongside the job description before "
    "deciding on next steps.",
    "Reviewers should examine the full resume alongside this job description, "
    "then validate role-specific evidence during a structured screening conversation.",
    "This assessment reflects the submitted profile only; reviewers should confirm "
    "role-specific evidence, recent outcomes, working context, and career motivation "
    "during a structured screening conversation.",
)

# Deterministic retrieval-only comments, used when the whole LLM chain is
# unavailable. They are honest about being similarity-derived, are each 25-30
# words, and deliberately avoid any "unavailable" placeholder wording — the
# machine-readable signal is breakdown["scoring_mode"] == "retrieval_fallback".
_FALLBACK_COMMENTS: dict[str, str] = {
    "skills_match": (
        "Skill overlap here is inferred from resume and job-description similarity "
        "rather than a detailed reading, so treat it as a preliminary signal and "
        "confirm the specific tools against the resume."
    ),
    "experience_relevance": (
        "Experience relevance is estimated from overall document similarity rather "
        "than a function-by-function comparison, so please confirm seniority, scope, "
        "and comparable delivery directly from the candidate's employment history."
    ),
    "role_alignment": (
        "Role alignment is derived from retrieval similarity between the job "
        "description and this profile, so verify the candidate's actual designation, "
        "reporting line, and core duties before advancing them to the next stage."
    ),
    "education_fit": (
        "Education fit is approximated from retrieval similarity and has not been "
        "checked qualification by qualification, so confirm degree level, "
        "specialisation, and any certifications the role requires against the resume."
    ),
    "overall": (
        "This candidate was ranked by resume and job-description similarity alone, "
        "so the placement is a preliminary retrieval signal; review the resume in "
        "full before shortlisting, rejecting, or scheduling any interview."
    ),
}

# Retained for backwards compatibility with older stored rows / callers that
# look for the historical placeholder. It is NEVER written any more.
_AI_UNAVAILABLE_COMMENT = "AI scoring unavailable, deterministic retrieval-based score."


def word_count(text: str | None) -> int:
    """Count words in `text` — whitespace-delimited tokens with a letter/digit.

    Pure and side-effect free; unit-tested in tests/test_matching.py.
    """
    if not text:
        return 0
    return sum(1 for tok in str(text).split() if _WORD_TOKEN_RE.search(tok))


def _tokens_word_count(tokens: Sequence[str]) -> int:
    return sum(1 for tok in tokens if _WORD_TOKEN_RE.search(tok))


def _tidy(text: str) -> str:
    """Normalise whitespace and make sure the comment ends as a sentence."""
    out = " ".join(text.split()).rstrip(" ,;:-,–")
    if out and out[-1] not in ".!?":
        out += "."
    return out


def _trim_to(tokens: list[str], min_words: int, max_words: int) -> list[str]:
    """Cut `tokens` down to at most `max_words`, preferring a clause boundary.

    Walks forward until the next word would exceed `max_words`, then looks back
    for the last token that ends a clause (`. , ; : — –`) at or after
    `min_words` and cuts there instead, so the repaired comment ends cleanly.
    """
    kept: list[str] = []
    for tok in tokens:
        if _WORD_TOKEN_RE.search(tok) and _tokens_word_count(kept) + 1 > max_words:
            break
        kept.append(tok)
    for i in range(len(kept) - 1, -1, -1):
        if kept[i].rstrip('"\')')[-1:] in ".,;:,–" and _tokens_word_count(kept[: i + 1]) >= min_words:
            return kept[: i + 1]
    return kept


def enforce_word_range(
    text: str | None,
    min_words: int = COMMENT_MIN_WORDS,
    max_words: int = COMMENT_MAX_WORDS,
    filler: Sequence[str] | None = None,
) -> str:
    """Return `text` deterministically coerced to `min_words`-`max_words` words.

    Too long  -> trimmed at the latest clause boundary that still satisfies the
                 minimum (hard word cut if there is no boundary).
    Too short -> extended with whole neutral clauses from `filler`, then trimmed.

    Pure and side-effect free; unit-tested in tests/test_matching.py.
    """
    if min_words < 1 or max_words < min_words:
        raise ValueError("invalid word range")
    tokens = " ".join((text or "").split()).split()
    clauses = list(filler if filler is not None else _PAD_CLAUSES)
    current_words = _tokens_word_count(tokens)
    if current_words < min_words:
        # Prefer ONE complete sentence whose length lands inside the band. This
        # preserves readable prose; the old append-until-long-enough approach
        # could trim the second sentence halfway through.
        candidates = [
            clause
            for clause in clauses
            if min_words
            <= current_words + word_count(clause)
            <= max_words
        ]
        if candidates:
            best = min(
                candidates,
                key=lambda clause: current_words + word_count(clause),
            )
            return _tidy(" ".join(tokens + best.split()))
    i = 0
    while _tokens_word_count(tokens) < min_words and i < len(clauses):
        tokens = tokens + clauses[i].split()
        i += 1
    if _tokens_word_count(tokens) > max_words:
        tokens = _trim_to(tokens, min_words, max_words)
    if _tokens_word_count(tokens) < min_words:
        # Filler exhausted (caller passed a short custom filler) — recycle the
        # built-in clauses so the guarantee still holds.
        j = 0
        while _tokens_word_count(tokens) < min_words:
            tokens = tokens + _PAD_CLAUSES[j % len(_PAD_CLAUSES)].split()
            j += 1
            if j > 2 * len(_PAD_CLAUSES):  # pragma: no cover — defensive
                break
        if _tokens_word_count(tokens) > max_words:
            tokens = _trim_to(tokens, min_words, max_words)
    return _tidy(" ".join(tokens))


def breakdown_keys(breakdown: dict | None) -> tuple[str, ...]:
    """The category keys a stored breakdown was actually scored on.

    Read OFF THE ROW rather than from a module constant, and that is what makes
    every function below work on a job with its own category list without any of
    them being told what that list is. A breakdown knows what it was scored on;
    a constant only knows what the product scored on the day it was written, and
    under Draft v4 no two jobs need agree.

    `overall` is excluded because it is the computed aggregate rather than a
    category, and `scoring_mode` because it is a string.
    """
    if not isinstance(breakdown, dict):
        return ()
    return tuple(
        key
        for key, value in breakdown.items()
        if key != "overall" and isinstance(value, dict)
    )


def comment_fields_out_of_range(breakdown: dict) -> dict[str, int]:
    """{field: word_count} for every comment outside the 25-30 word contract."""
    bad: dict[str, int] = {}
    for field in (*breakdown_keys(breakdown), "overall"):
        block = breakdown.get(field)
        comment = block.get("comment") if isinstance(block, dict) else None
        n = word_count(comment)
        if not (COMMENT_MIN_WORDS <= n <= COMMENT_MAX_WORDS):
            bad[field] = n
    return bad


def enforce_breakdown_comments(breakdown: dict) -> dict:
    """Coerce every comment in a breakdown into the 25-30 word contract.

    The last line of defence: called on every breakdown -- LLM-scored or
    deterministic-fallback -- immediately before it is persisted.
    """
    for field in (*breakdown_keys(breakdown), "overall"):
        block = breakdown.get(field)
        if not isinstance(block, dict):
            continue
        block["comment"] = enforce_word_range(block.get("comment"))
    return breakdown

# ── 4-parameter scoring ─────────────────────────────────────────────────────
# NO WEIGHTS (client decision, 2026-07-30: "make sure there are no mathematical
# weightage for giving these AI comments").
#
# The four parameters previously carried 0.35 / 0.30 / 0.20 / 0.15. Two things
# were wrong with that. The weights were shown to the client as "35% role-fit
# weighting" beside each remark, which is a number reaching a client. And a
# fixed weighting asserts that skills matter 2.3x more than education for
# EVERY role in the product, which is an arithmetic the four AI comments do not
# perform and cannot defend when a customer asks why.
#
# Each category is judged, graded and commented entirely on its own terms.
# The internal overall is their plain mean, and exists only to order a
# candidate list and assign a tier -- it is never displayed, weighted or not.
#
# THE LIST IS PER JOB (Draft v4, spec 3.2). `matching_categories` generates it
# at job creation and the recruiter finalises it; the scoring path is handed the
# job's own keys and the read path takes them off the stored breakdown. What
# remains here is the FALLBACK: the four keys the product scored every job on
# before the change, used for a job whose categories were never generated so
# that nothing about an existing job's ranking moves underneath it.
PARAMETERS: tuple[str, ...] = matching_categories.LEGACY_KEYS

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


async def _linked_stage(session: AsyncSession, job_id: uuid.UUID) -> list[uuid.UUID]:
    """Profile ids of EVERY candidate explicitly linked to this job.

    Stages 1 and 2 are *retrieval* — they are capped at top_n and they silently
    drop any profile whose `embedding` is NULL or whose `resume_tsv` does not
    match the JD terms (an unparsed resume matches neither). A candidate who
    was explicitly linked to the job — they applied, or a recruiter uploaded
    them — must never be left unscored because retrieval could not see them,
    so their profile is always added to the scoring pool. No top_n cap here:
    the pool is bounded by the job's own applicant count.
    """
    rows = await session.execute(
        text(
            """
            SELECT DISTINCT ON (l.candidate_id) p.id AS profile_id
            FROM job_candidate_links l
            JOIN profiles p ON p.id = l.profile_id
            WHERE l.job_id = :job_id AND l.archived_at IS NULL
            ORDER BY l.candidate_id, l.created_at DESC
            """
        ),
        {"job_id": str(job_id)},
    )
    return [r.profile_id for r in rows]


async def _backfill_missing_embeddings(
    session: AsyncSession, profile_ids: Sequence[uuid.UUID]
) -> None:
    """Repair the semantic stage's input for profiles it would otherwise skip.

    Two distinct causes of a NULL `profiles.embedding`:
      * resume text IS present (parsing ran) but the embedding was never
        written — embed it here; `embed()` is cheap and already degrades to a
        deterministic dev vector when BGE_M3_ENDPOINT is unset.
      * no resume text at all — re-queue the EXISTING `pickready.parse_resume`
        task (never a new one) for profiles whose Cloudinary metadata is
        complete enough for it to succeed. A profile with an incomplete asset
        is logged, not re-queued, so the task does not crash-loop.

    Never raises: a backfill failure must not abort a matching run.
    """
    if not profile_ids:
        return
    from app.services.resume_storage import profile_has_resume

    rows = (
        (
            await session.execute(
                select(Profile).where(
                    Profile.id.in_(list(profile_ids)),
                    Profile.embedding.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return
    embeddable = [p for p in rows if (p.resume_text or "").strip()]
    if embeddable:
        try:
            vectors = await embed([p.resume_text for p in embeddable])
            for profile, vector in zip(embeddable, vectors):
                profile.embedding = vector
            await session.flush()
            logger.info("matching.embeddings_backfilled count=%d", len(embeddable))
        except Exception as exc:  # noqa: BLE001 — never abort a run
            logger.warning(
                "matching.embedding_backfill_failed error=%s", type(exc).__name__
            )

    unparsed = [p for p in rows if not (p.resume_text or "").strip()]
    if not unparsed:
        return
    from app.workers.celery_app import celery_app

    for profile in unparsed:
        if not profile_has_resume(profile):
            logger.warning(
                "matching.profile_unparseable profile_id=%s, no complete resume "
                "asset; it can be scored only from its questionnaire aspects",
                profile.id,
            )
            continue
        try:
            celery_app.send_task("pickready.parse_resume", args=[str(profile.id)])
            logger.info("matching.parse_resume_requeued profile_id=%s", profile.id)
        except Exception as exc:  # noqa: BLE001 — broker down must not abort a run
            logger.warning(
                "matching.parse_resume_enqueue_failed profile_id=%s error=%s",
                profile.id, type(exc).__name__,
            )


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


def compute_overall_score(
    scores: dict[str, int | float], keys: tuple[str, ...] = PARAMETERS
) -> float:
    """Unweighted mean of the 1-10 category scores, rounded to 1 decimal.

    Pure Python math (banker's rounding over IEEE-754 doubles) -- the overall
    score is NEVER taken from the LLM. Internal only: it orders the candidate
    list and assigns a tier, and never crosses the API boundary.
    Unit-tested in tests/test_scoring.py.
    """
    keys = tuple(keys) or PARAMETERS
    return round(sum(float(scores[key]) for key in keys) / len(keys), 1)


def _fallback_param_score(rank_index: int, total: int) -> int:
    """Map a 0-based retrieval rank into the deterministic fallback band.

    Best-ranked profile → _FALLBACK_MAX, worst → _FALLBACK_MIN, linear in
    between. Deterministic and monotonic so ordering follows the semantic +
    keyword retrieval signal when no LLM score is available.
    """
    if total <= 1:
        return _FALLBACK_MAX
    frac = rank_index / (total - 1)  # 0.0 (best) .. 1.0 (worst)
    span = _FALLBACK_MAX - _FALLBACK_MIN
    return int(round(_FALLBACK_MAX - span * frac))


def _fallback_comment(key: str, name: str) -> str:
    """A retrieval-only comment for one category.

    The four legacy categories keep their hand-written comments, which say
    plainly and in 25-30 words what a similarity-derived placement is and is
    not. A category this release cannot have known about gets a generated
    sentence in the same register: honest that the placement came from document
    similarity, and specific enough to name the category the reader is looking
    at.
    """
    if key in _FALLBACK_COMMENTS:
        return _FALLBACK_COMMENTS[key]
    return (
        f"{name} here is estimated from overall similarity between this resume and "
        "the job description rather than a detailed reading, so confirm it directly "
        "against the candidate's own document before deciding."
    )


def _fallback_breakdown(
    rank_index: int,
    total: int,
    categories: tuple[tuple[str, str, str], ...] | None = None,
) -> dict:
    """A full breakdown in the contract shape from retrieval rank alone.

    Comments are real, readable 25-30 word notes that state plainly that the
    ranking is similarity-derived; `scoring_mode` carries the machine-readable
    flag so the UI/audit can tell an LLM score from a degraded one (claude.md
    rule 9 — degrade, never crash).
    """
    score = _fallback_param_score(rank_index, total)
    resolved = categories or tuple(
        (key, key.replace("_", " ").capitalize(), "") for key in PARAMETERS
    )
    keys = tuple(key for key, _, _ in resolved)
    breakdown: dict[str, Any] = {
        key: {"score": score, "comment": _fallback_comment(key, name)}
        for key, name, _ in resolved
    }
    breakdown["overall"] = {
        "score": compute_overall_score({key: score for key in keys}, keys),
        "comment": _FALLBACK_COMMENTS["overall"],
    }
    breakdown["scoring_mode"] = "retrieval_fallback"
    return enforce_breakdown_comments(breakdown)


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


def _validate_entry(entry: Any, keys: tuple[str, ...] = PARAMETERS) -> dict | None:
    """Validate one LLM result entry into the contract's breakdown shape
    (overall computed in Python). Returns None when malformed."""
    if not isinstance(entry, dict):
        return None
    keys = tuple(keys) or PARAMETERS
    breakdown: dict[str, Any] = {}
    for param in keys:
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
        "score": compute_overall_score(
            {key: breakdown[key]["score"] for key in keys}, keys
        ),
        "comment": overall_comment.strip(),
    }
    breakdown["scoring_mode"] = "llm"
    return breakdown


# ── Comments-only view for the UI (numbers never leak to the review screen) ──
# The API keeps returning `breakdown` (scores included) for internal/audit use,
# but the five comments are ALSO surfaced under these exact, flat keys so the
# frontend can render text without ever touching a number.
RANKING_COMMENT_KEYS: dict[str, str] = {
    "skills_match": "skills_match_comment",
    "experience_relevance": "experience_comment",
    "role_alignment": "role_alignment_comment",
    "education_fit": "education_comment",
    "overall": "overall_comment",
}

RANKING_STATUS_READY = "ready"
RANKING_STATUS_NOT_SCORED = "not_scored"

#: Where each comment's word LABEL is published. Same five fields as
#: RANKING_COMMENT_KEYS, so the UI reads `<field>_label` beside `<field>_comment`.
RANKING_LABEL_KEYS: dict[str, str] = {
    "skills_match": "skills_match_label",
    "experience_relevance": "experience_label",
    "role_alignment": "role_alignment_label",
    "education_fit": "education_label",
    "overall": "overall_label",
}

# ── The four client-facing grades (spec §10.2) ───────────────────────────────
# claude.md's hard rule is that rated output is WORDS ONLY and stored numeric
# scores never reach a client. This is the projection that makes that possible:
# the internal 1-10 parameter score is converted to a grade HERE, server-side,
# and only the grade crosses the API boundary.
#
# The scale itself lives in `services/rating.py`, which the PPI Assessment
# reads from too. Two parallel five-label scales used to be maintained here and
# in functional_assessment, kept in step by hand and by comment; there is now
# one scale, and "Matching" means exactly the same thing wherever it appears.
MATCHING_LABELS: tuple[str, ...] = rating.GRADES

MATCHING_LABEL_HIGHLY = rating.GRADE_HIGHLY
MATCHING_LABEL_MATCHING = rating.GRADE_MATCHING
MATCHING_LABEL_MODERATE = rating.GRADE_MODERATELY
MATCHING_LABEL_NONE = rating.GRADE_NOT


def matching_label(score: float | int | None) -> str | None:
    """Grade for a parameter score. `score` is the internal 1-10 value (or the
    1-10-scaled overall mean); None in, None out.

    Delegates to `services.rating`, which owns the one four-grade scale the
    whole product reads. Kept as a name here because a good deal of the
    codebase already imports it from this module.
    """
    return rating.grade_for_ten(score)


#: Display names for the keys this module has always known, so a breakdown
#: stored before the per-job lists existed still renders a readable heading
#: rather than a slug. A category generated for a job carries its own name.
_LEGACY_DISPLAY_NAMES: dict[str, str] = {
    key: name for key, name, _ in matching_categories.DEFAULT_CATEGORIES
}


def _display_name(key: str) -> str:
    return _LEGACY_DISPLAY_NAMES.get(key) or key.replace("_", " ").capitalize()


def ranking_payload(breakdown: dict | None) -> dict[str, Any]:
    """Flat, comments-only projection of a stored breakdown for API responses.

    Always returns every key. When the link has not been scored yet the five
    comments and labels are null and `ranking_status` is "not_scored" — an
    explicit state the UI can distinguish from "scored but empty", instead of a
    silent null. Comments are re-checked against the 25-30 word contract on the
    way out, so even a legacy row can never render out of range.

    Each comment is accompanied by its WORD LABEL, derived here from the
    internal score. The score itself is deliberately not in the output: this is
    the boundary at which numbers stop (claude.md hard rule).
    """
    out: dict[str, Any] = {
        "ranking_status": RANKING_STATUS_NOT_SCORED,
        **{key: None for key in RANKING_COMMENT_KEYS.values()},
        **{key: None for key in RANKING_LABEL_KEYS.values()},
        "categories": [],
    }
    if not isinstance(breakdown, dict) or not breakdown:
        return out

    # `categories` is the payload a client should render: one entry per category
    # this candidate was ACTUALLY scored on, in the order the job's list holds
    # them. It exists because the job's categories are no longer the product's
    # (spec 3.2), so a fixed set of flat fields can no longer describe a
    # breakdown -- a job with a Compensation fit category has a comment with
    # nowhere to go, and a job scored before a category was added has a flat
    # field with nothing behind it.
    #
    # The flat `*_comment` / `*_label` fields are still emitted for the four
    # long-standing keys, and deliberately: every existing client reads them,
    # and they are correct whenever the job kept those categories. They are
    # DEPRECATED. A client should move to `categories`.
    for field in breakdown_keys(breakdown):
        block = breakdown.get(field)
        if not isinstance(block, dict):
            continue
        comment = enforce_word_range(block.get("comment"))
        label = matching_label(block.get("score"))
        out["categories"].append(
            {
                "key": field,
                "name": block.get("name") or _display_name(field),
                "comment": comment,
                "label": label,
            }
        )
        if field in RANKING_COMMENT_KEYS:
            out[RANKING_COMMENT_KEYS[field]] = comment
            out[RANKING_LABEL_KEYS[field]] = label

    overall = breakdown.get("overall")
    if isinstance(overall, dict):
        out[RANKING_COMMENT_KEYS["overall"]] = enforce_word_range(overall.get("comment"))
        out[RANKING_LABEL_KEYS["overall"]] = matching_label(overall.get("score"))
    out["ranking_status"] = RANKING_STATUS_READY
    return out


def client_breakdown(breakdown: dict | None) -> dict | None:
    """The stored breakdown with every internal numeric score removed.

    claude.md hard rule: stored numeric scores are internal ranking data and
    must never be returned by a client-facing API. The review screen needs the
    five comments (and `scoring_mode`, so the UI/audit can tell an LLM score
    from a degraded retrieval one) — it never needs 1-10 parameter scores or
    the internal overall. The `breakdown` key is kept in the response shape for
    backwards compatibility; only the numbers are stripped out of it.
    """
    if not isinstance(breakdown, dict) or not breakdown:
        return None
    out: dict[str, Any] = {}
    for key, value in breakdown.items():
        if isinstance(value, dict):
            block = {k: v for k, v in value.items() if k != "score"}
            if block:
                out[key] = block
        elif not isinstance(value, (int, float)) or isinstance(value, bool):
            out[key] = value
    return out or None


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


_WORD_RULE = (
    f"EVERY comment you write, all category comments AND overall_comment "
    f", MUST be between {COMMENT_MIN_WORDS} and {COMMENT_MAX_WORDS} words. Not "
    f"fewer than {COMMENT_MIN_WORDS}, not more than {COMMENT_MAX_WORDS}. Count "
    "the words before you emit each comment. Write full, specific sentences "
    "naming concrete skills, employers, titles or qualifications from the "
    "candidate's profile, do not pad with filler, and do not truncate."
)

def _scoring_system_prompt(categories: tuple[tuple[str, str, str], ...]) -> str:
    """The scoring prompt for ONE job's category list.

    Rendered per call rather than at import, because the categories are now a
    property of the job. The prompt names each category by the KEY the response
    must use and by the NAME a human gave it, and spells out the exact JSON
    shape: a model asked for "the categories" in prose returns whatever keys it
    likes, and `_validate_entry` would then reject the whole batch.
    """
    listing = "\n".join(
        f"{index}. {key}, {name}. {description}".rstrip(". ") + "."
        for index, (key, name, description) in enumerate(categories, 1)
    )
    shape = ", ".join(
        f'"{key}": {{"score": <int 1-10>, "comment": "<25-30 words>"}}'
        for key, _, _ in categories
    )
    return registry.render(
        "matching_scoring_system",
        word_rule=_WORD_RULE,
        categories=listing,
        return_shape=(
            '{"profile_id": "<uuid>", '
            + shape
            + ', "overall_comment": "<holistic, 25-30 words>"}'
        ),
    )


def _extract_valid(
    raw: str,
    wanted_ids: set[uuid.UUID],
    keys: tuple[str, ...] = PARAMETERS,
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
        breakdown = _validate_entry(entry, keys)
        if breakdown is not None:
            got[pid] = breakdown
    return got, wanted_ids - set(got)


def _safe_profile_summary(profile: Profile) -> dict:
    """_profile_summary, but a single malformed profile never aborts the batch
    — it falls back to a minimal id-only summary and is scored on what's there.
    """
    try:
        return _profile_summary(profile)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "matching.profile_summary_failed profile_id=%s error=%s",
            profile.id, type(exc).__name__,
        )
        return {"profile_id": str(profile.id)}


async def _score_batch(
    session: AsyncSession,
    jd_text: str,
    batch: list[Profile],
    rank_by_id: dict[uuid.UUID, int],
    total: int,
    customer_success_patterns: list[dict[str, Any]] | None = None,
    categories: tuple[tuple[str, str, str], ...] | None = None,
) -> dict[uuid.UUID, dict]:
    """Score one batch, with ONE corrective regeneration pass.

    The corrective pass covers both failure modes together: entries that were
    missing/malformed, and entries whose comments broke the
    25-30 word contract (the model is told exactly which fields were wrong and
    the word count it produced). Anything still out of range afterwards is
    repaired deterministically by `enforce_breakdown_comments` — a comment
    outside 25-30 words is never returned from here. Profiles still malformed
    after the retry are skipped with a logged warning; if the whole LLM
    provider chain is unavailable, every profile gets a deterministic
    retrieval-rank fallback breakdown instead of crashing.
    """
    payload: dict[str, Any] = {
        "job_description": jd_text,
        "candidates": [_safe_profile_summary(p) for p in batch],
    }
    if customer_success_patterns:
        payload["customer_success_patterns"] = customer_success_patterns
    resolved = categories or tuple(
        (key, name, description)
        for key, name, description in matching_categories.DEFAULT_CATEGORIES
        if key in PARAMETERS
    )
    keys = tuple(key for key, _, _ in resolved)
    user = json.dumps(payload, default=str)
    messages = [
        {"role": "system", "content": _scoring_system_prompt(resolved)},
        {"role": "user", "content": user},
    ]
    try:
        raw = await llm_router.chat_completion(
            _SCORING_TASK, messages, response_format_json=True, session=session
        )
    except llm_router.LLMUnavailableError:
        logger.warning(
            "matching.llm_unavailable, deterministic fallback for %d profiles", len(batch)
        )
        return {
            p.id: _fallback_breakdown(
                rank_by_id.get(p.id, total - 1), total, resolved
            )
            for p in batch
        }
    results, missing = _extract_valid(raw, {p.id for p in batch}, keys)

    # Which scored entries broke the word contract? {profile_id: {field: count}}
    bad_words = {
        pid: bad
        for pid, bd in results.items()
        if (bad := comment_fields_out_of_range(bd))
    }

    if missing or bad_words:
        parts: list[str] = []
        if missing:
            parts.append(
                "These profile_ids were missing or malformed: "
                f"{sorted(str(p) for p in missing)}. Every one of these keys "
                f"must be present, {sorted(keys)}, each with an integer score "
                "between 1 and 10 and a comment, and overall_comment must be a "
                "non-empty holistic sentence."
            )
        if bad_words:
            detail = "; ".join(
                f"{pid}: "
                + ", ".join(f"{field} was {n} words" for field, n in sorted(bad.items()))
                for pid, bad in sorted(bad_words.items(), key=lambda kv: str(kv[0]))
            )
            parts.append(
                "These comments broke the mandatory "
                f"{COMMENT_MIN_WORDS}-{COMMENT_MAX_WORDS} word rule, {detail}. "
                "Rewrite each listed comment so it is between "
                f"{COMMENT_MIN_WORDS} and {COMMENT_MAX_WORDS} words, keeping the "
                "same score and the same substance; add concrete detail from the "
                "candidate's profile rather than filler."
            )
        wanted_again = set(missing) | set(bad_words)
        corrective = (
            " ".join(parts)
            + " Re-emit a complete, valid JSON response in the exact schema for "
            f"ONLY these profile_ids: {sorted(str(p) for p in wanted_again)}."
        )
        retry_messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": corrective},
        ]
        try:
            raw_retry = await llm_router.chat_completion(
                _SCORING_TASK, retry_messages, response_format_json=True, session=session
            )
        except llm_router.LLMUnavailableError:
            # Chain went down on the corrective retry — skip the still-missing
            # profiles (the ones that DID score keep their real scores).
            raw_retry = ""
        retried, still_missing = _extract_valid(raw_retry, wanted_again, keys)
        for pid, bd in retried.items():
            # Only accept a regenerated entry if it is no worse on the word
            # contract than what we already had (a first-pass entry is never
            # replaced by a more broken one).
            if pid in results and len(comment_fields_out_of_range(bd)) >= len(
                bad_words.get(pid, {})
            ):
                continue
            results[pid] = bd
        for pid in still_missing & set(missing):
            logger.warning(
                "matching.profile_skipped profile_id=%s reason=malformed_llm_scores",
                pid,
            )

    # Last line of defence: deterministic repair so a stored comment is ALWAYS
    # 25-30 words, whatever the model did.
    for pid, bd in results.items():
        remaining = comment_fields_out_of_range(bd)
        if remaining:
            logger.warning(
                "matching.comment_word_count_repaired profile_id=%s fields=%s",
                pid, remaining,
            )
            enforce_breakdown_comments(bd)
    return results


async def _llm_score(
    session: AsyncSession,
    jd_text: str,
    profiles: list[Profile],
    customer_success_patterns: list[dict[str, Any]] | None = None,
    categories: tuple[tuple[str, str, str], ...] | None = None,
) -> dict[uuid.UUID, dict]:
    """Score every shortlisted profile on this JOB'S matching categories.

    Returns {profile_id: breakdown} where breakdown matches the contract's
    "Matching results" JSON block (overall.score computed in Python). When the
    LLM chain is unavailable, breakdowns are the deterministic retrieval-rank
    fallback, flagged `scoring_mode = "retrieval_fallback"`."""
    total = len(profiles)
    # profiles arrive in retrieval-union order (semantic-first, then keyword) —
    # position is the fallback rank signal.
    rank_by_id = {p.id: i for i, p in enumerate(profiles)}
    results: dict[uuid.UUID, dict] = {}
    for i in range(0, total, _RERANK_BATCH_SIZE):
        batch = profiles[i : i + _RERANK_BATCH_SIZE]
        results.update(
            await _score_batch(
                session,
                jd_text,
                batch,
                rank_by_id,
                total,
                customer_success_patterns,
                categories,
            )
        )
    return results


def _success_pattern(profile: Profile, job_title: str, department: str | None) -> dict:
    """A PII-minimized calibration summary from a profile that progressed.

    Only job-relevant evidence is retained. Names, contact details, resume
    prose, compensation and every unrecognized parsed field are excluded.
    """
    parsed = _strip_compensation(profile.parsed_fields_json or {})
    aspects = (
        _strip_compensation(profile.aspects_json)
        if isinstance(profile.aspects_json, dict)
        else {}
    )
    pattern: dict[str, Any] = {
        "prior_job_title": job_title,
        "prior_job_department": department,
        "skills": parsed.get("skills", []),
        "total_experience_years": parsed.get("total_experience_years"),
        "education": parsed.get("education", []),
        "employment_history": parsed.get("employment_history", []),
    }
    if aspects.get(_ASPECT_ROLE_KEY):
        pattern["current_designation_and_duties"] = aspects[_ASPECT_ROLE_KEY]
    return pattern


async def _customer_success_patterns(
    session: AsyncSession,
    job: Job,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Return recent same-customer profiles that reached a positive stage.

    The latest status per link is considered, so a later rejection cannot
    remain a positive example. This signal is deliberately optional and
    fail-soft; matching still works when no history exists.
    """
    try:
        rows = await session.execute(
            text(
                """
                SELECT p.id AS profile_id, j.title, j.department
                FROM job_candidate_links l
                JOIN profiles p ON p.id = l.profile_id
                JOIN jobs j ON j.id = l.job_id
                JOIN LATERAL (
                    SELECT ps.status, ps.at
                    FROM pipeline_status ps
                    WHERE ps.job_candidate_link_id = l.id
                    ORDER BY ps.at DESC
                    LIMIT 1
                ) latest ON true
                WHERE l.tenant_id = :tenant_id
                  AND l.job_id <> :job_id
                  AND l.archived_at IS NULL
                  AND latest.status IN ('shortlisted', 'offered', 'joined')
                ORDER BY latest.at DESC
                LIMIT :limit
                """
            ),
            {
                "tenant_id": str(job.tenant_id),
                "job_id": str(job.id),
                "limit": limit,
            },
        )
        result = []
        for row in rows:
            profile = await session.get(Profile, row.profile_id)
            if profile is not None:
                result.append(_success_pattern(profile, row.title, row.department))
        return result
    except Exception as exc:  # noqa: BLE001 - calibration must never block matching
        logger.warning(
            "matching.customer_patterns_unavailable tenant_id=%s error=%s",
            job.tenant_id,
            type(exc).__name__,
        )
        return []


# ── Yukti's A2A hand-off: the `ai_score` artifact (spec 16.5) ────────────────
#
# WHY THIS IS BUILT HERE AND NOT DERIVED FROM THE STORED ROW
# -----------------------------------------------------------
# `match_breakdown_json` is the product's own record and it is what the AI Score
# section of a PRISM report is rendered from. It carries the internal 1-10
# category score, which is precisely the value that may never reach a client;
# `client_breakdown` and `ranking_payload` are the two projections that strip it
# on the way out.
#
# The artifact is a THIRD thing, built from the breakdown rather than being it,
# for two separate reasons:
#
#   * it has to carry engineering metadata the stored row has no business
#     holding -- which retrieval stage surfaced this candidate and at what rank,
#     which category list was in force, which routing policy the explanation was
#     written under. Putting any of that on `match_breakdown_json` would leave it
#     one projection bug away from a client, and note what `client_breakdown`
#     actually does: it removes NUMBERS, not FIELDS. A `{"semantic_rank": 3}`
#     block would walk straight through it into an API response.
#   * it has to carry NO assessment number at all. A grade crosses this boundary
#     as a WORD, for the same reason `ppi._requirement_word` converts one: the
#     point at which an integer stops being convertible is the point at which
#     somebody renders it, and Siddhi -- the declared consumer of `ai_score` --
#     is the agent that writes the document a client keeps.
#
# So nothing below writes back, and nothing below is read by the pipeline. With
# the whole block deleted a matching run produces byte-identical rows, which is
# the only useful sense of "additive".

#: The `ai_score` CONTRACT version, and deliberately not a re-score counter.
#:
#: Matching re-runs and overwrites `match_breakdown_json` in place, so the
#: product keeps no history of how many times a candidate has been scored and
#: there is no column a counter could be derived from. A counter invented here
#: would read 1 forever while looking like it could read more, which is the same
#: lie a `framework_generated_at` stamp with no competency rows behind it told.
#: What actually tells two runs apart travels in the payload: `jd_version`
#: fingerprints the job description the run scored against, and `scored_at` says
#: when the run happened.
AI_SCORE_ARTIFACT_VERSION = 1

#: How a candidate reached the scorer. `exact_match` is the specification's name
#: for what stage 2 is -- Postgres `ts_rank` over `resume_tsv`, which matches the
#: JD's literal skill terms -- and it is named for the question a consumer asks
#: ("was this a meaning match or a word match?") rather than after the
#: implementation, because the implementation is the half that may be replaced.
RETRIEVAL_SEMANTIC = "semantic"
RETRIEVAL_EXACT_MATCH = "exact_match"
RETRIEVAL_LINKED = "linked"


def _retrieval_evidence(
    profile_id: uuid.UUID,
    *,
    semantic_ids: Sequence[uuid.UUID],
    keyword_ids: Sequence[uuid.UUID],
    linked_ids: Sequence[uuid.UUID],
    fusion_order: Sequence[uuid.UUID],
    top_n: int,
    semantic_ran: bool,
) -> dict[str, Any]:
    """How THIS candidate reached the scorer, and by which stage.

    Recorded per candidate rather than once per run because an artifact has to
    be readable on its own: a consumer holding one candidate's `ai_score` and a
    run-level summary filed somewhere else cannot answer "was this person a
    meaning match or a word match" without the other half, and the whole reason
    the boundary is typed is that a consumer must not have to go looking.

    A rank here is A POSITION IN A RETRIEVAL LIST, not an assessment of a
    person. It says the pipeline read this resume third; it says nothing about
    how good it is, because retrieval is a ranking prior and never decides who
    gets scored. That distinction is why these integers may live in an artifact
    while a category score may not.
    """
    semantic = list(semantic_ids)
    keyword = list(keyword_ids)
    fusion = list(fusion_order)
    return {
        RETRIEVAL_SEMANTIC: {
            "hit": profile_id in semantic,
            "rank": semantic.index(profile_id) if profile_id in semantic else None,
            # Told apart from "did not match", because they mean opposite things.
            # A run whose embedding service was down ranked on words alone, and a
            # consumer reading a bare `hit: false` would record that as evidence
            # this resume failed to match on meaning.
            "stage_ran": bool(semantic_ran),
        },
        RETRIEVAL_EXACT_MATCH: {
            "hit": profile_id in keyword,
            "rank": keyword.index(profile_id) if profile_id in keyword else None,
            "stage_ran": True,
        },
        # Not a retrieval hit at all, and it outranks both. A candidate linked to
        # the job is scored whether either stage surfaced them or not, which is
        # the rule retrieval must never be allowed to override.
        RETRIEVAL_LINKED: profile_id in set(linked_ids),
        "fusion_rank": fusion.index(profile_id) if profile_id in fusion else None,
        "fusion_strategy": "union, semantic order first, then keyword, then linked",
        "pool_size": len(fusion),
        "top_n": int(top_n),
    }


def _resume_evidence_refs(profile: Profile) -> tuple[str, ...]:
    """The resume this conclusion was drawn from, as IDENTIFIERS ONLY.

    Ids, exactly as `swot_evidence` records its sources: provenance has to be
    showable without re-disclosing the document, and a resume is a file a
    candidate uploaded rather than text this product may hand onward. The digest
    is included because it is the one ref that survives a re-upload under a new
    object id and still answers "was it this exact document".
    """
    refs = [f"profiles:{profile.id}"]
    if profile.resume_sha256:
        refs.append(f"resume_sha256:{profile.resume_sha256}")
    if profile.resume_public_id:
        refs.append(f"resume_object:{profile.resume_public_id}")
    return tuple(refs)


def _resume_sections(profile: Profile) -> list[str]:
    """Which parts of the resume the scorer was actually given.

    `_profile_summary` builds the prompt from exactly these fields, so this is
    the difference between a category graded off a parsed employment history and
    one graded off raw resume text. It is also what `resume_parsed` is answered
    from: an empty list means nothing survived parsing, and `yukti_gate` refuses
    to let a grade written from an unparsed file pass as evidenced.
    """
    parsed = profile.parsed_fields_json if isinstance(profile.parsed_fields_json, dict) else {}
    aspects = profile.aspects_json if isinstance(profile.aspects_json, dict) else {}
    present: list[str] = []
    for name, value in (
        ("skills", parsed.get("skills")),
        ("total_experience_years", parsed.get("total_experience_years")),
        ("education", parsed.get("education")),
        ("employment_history", parsed.get("employment_history")),
        ("resume_text", profile.resume_text),
        ("current_designation_and_duties", aspects.get(_ASPECT_ROLE_KEY)),
    ):
        if value:
            present.append(name)
    return present


def _model_metadata(breakdown: dict) -> dict[str, Any]:
    """Which model wrote the explanation, as far as this pass can honestly say.

    `llm_router.chat_completion` returns a string, so the provider and the key
    that actually answered are not available at this call site, and naming one
    would be a claim nobody checked -- the same shape as a stamp asserting work
    that did not happen. So `provider` is present and NULL rather than absent,
    because an omitted key reads as "nobody thought about it", and what IS
    knowable is recorded beside it: the routing policy the call was made under,
    and `scoring_mode`, which is the ground truth about whether a model was
    involved at all.
    """
    from app.config import llm_providers  # noqa: PLC0415

    order = list(llm_providers.provider_order(_SCORING_TASK))
    return {
        "task_type": _SCORING_TASK,
        # The one field a consumer MUST read. A `retrieval_fallback` breakdown
        # was ordered by document similarity and never read by a model, and a
        # report presenting it as a judgement would be presenting a degradation
        # as a result.
        "scoring_mode": str(breakdown.get("scoring_mode") or "unknown"),
        "provider": None,
        "provider_order": order,
        "candidate_models": {p: llm_providers.PROVIDER_MODELS.get(p) for p in order},
        "temperature": llm_providers.temperature_for(_SCORING_TASK),
    }


def _ai_score_payload(
    job: Job,
    profile: Profile,
    link: JobCandidateLink,
    breakdown: dict,
    *,
    categories: Sequence[tuple[str, str, str]],
    retrieval: dict[str, Any],
) -> dict[str, Any]:
    """The typed hand-off for one candidate, built from validated fields only.

    Every graded field crosses as a WORD, through the same `matching_label`
    conversion `ranking_payload` performs on the way to a client. The
    explanation crosses through the same `enforce_word_range` too, which buys a
    property worth having: the justification a consumer reads is the identical
    string the recruiter read on the review screen, so an agent downstream can
    never be shown a different reason from the one a human was shown.

    A category the breakdown has no block for is NOT emitted as an empty entry.
    It is named under `provenance.categories_unscored` instead, because
    `yukti_gate` counts `categories` to decide whether the pass covered the job,
    and a padded list would let a run that scored three of five categories
    report a complete one.
    """
    from app.services.agents import identity  # noqa: PLC0415
    from app.services import swot_intake  # noqa: PLC0415

    evidence_refs = list(_resume_evidence_refs(profile))
    sections = _resume_sections(profile)
    graded: list[dict[str, Any]] = []
    unscored: list[str] = []
    for key, name, description in categories:
        block = breakdown.get(key)
        if not isinstance(block, dict):
            unscored.append(key)
            continue
        graded.append(
            {
                "key": key,
                "name": name,
                # What the category demands of a resume. The AI Score carries no
                # required LEVEL -- a resume-only pass has no job-requirement
                # shape to compare against, which is why `DimensionOut`'s
                # `required_level` is null for every AI Score item -- so stating
                # one here would invent the very number this boundary exists to
                # keep out.
                "requirement": description,
                "grade": matching_label(block.get("score")),
                "explanation": enforce_word_range(block.get("comment")),
                # The gate refuses a graded category that cites nothing. These
                # are document ids: which resume the conclusion was drawn from,
                # never a quoted line out of it.
                "evidence": list(evidence_refs),
                "evidence_sections": list(sections),
                "evidence_basis": {
                    RETRIEVAL_SEMANTIC: bool(
                        (retrieval.get(RETRIEVAL_SEMANTIC) or {}).get("hit")
                    ),
                    RETRIEVAL_EXACT_MATCH: bool(
                        (retrieval.get(RETRIEVAL_EXACT_MATCH) or {}).get("hit")
                    ),
                    RETRIEVAL_LINKED: bool(retrieval.get(RETRIEVAL_LINKED)),
                },
            }
        )

    overall = breakdown.get("overall") if isinstance(breakdown.get("overall"), dict) else {}
    return {
        # ── what `gates.yukti_gate` reads ────────────────────────────────────
        "categories": graded,
        "resume_parsed": bool(sections),
        # Nothing about the person is concluded beyond what the resume states.
        # An empty list rather than an absent key: the gate walks this, and a
        # missing key would make the same claim by accident instead of on
        # purpose. Anything a future pass infers belongs here, where the gate
        # will refuse it if it names a protected attribute.
        "inferred_fields": [],
        # ── scope, carried on the payload as well as on the envelope ─────────
        "candidate_id": str(profile.candidate_id),
        "job_id": str(job.id),
        "profile_id": str(profile.id),
        "job_candidate_link_id": str(link.id) if link.id is not None else None,
        # ── the holistic 5th comment, which is `match_rationale` ─────────────
        "overall": {
            "grade": matching_label(overall.get("score")),
            "explanation": enforce_word_range(overall.get("comment")),
        },
        "evidence_refs": evidence_refs,
        "resume_sections": sections,
        "retrieval": retrieval,
        "model": _model_metadata(breakdown),
        "provenance": {
            "producer": identity.YUKTI,
            # Said out loud because it is the line matching must not cross: this
            # is judged from resume text alone, before the candidate has spoken
            # to anything, and a consumer must never read it as verified depth.
            "pass": "resume_only",
            "scored_at": datetime.now(timezone.utc).isoformat(),
            "categories_expected": [key for key, _, _ in categories],
            "categories_unscored": unscored,
            # Read off the row rather than inferred from the key names: null
            # means the recruiter has not saved the list yet, so the candidate
            # was ranked against a proposal rather than a finalised list.
            "categories_finalised_at": (
                job.matching_categories_finalized_at.isoformat()
                if getattr(job, "matching_categories_finalized_at", None)
                else None
            ),
            "source_type": getattr(link, "source_type", None),
        },
        "jd_version": swot_intake.jd_version(job),
        "artifact_version": AI_SCORE_ARTIFACT_VERSION,
    }


def _publish_one_ai_score(
    job: Job,
    profile: Profile,
    link: JobCandidateLink,
    breakdown: dict,
    *,
    categories: Sequence[tuple[str, str, str]],
    stages: dict[str, Any],
    correlation_id: str | None = None,
):
    """Run Yukti's gate, then publish this candidate's `ai_score` artifact.

    Returns None rather than raising on ANY failure, and that direction is the
    whole reason the function has this shape. Matching is a live path that
    worked before artifacts existed and that a recruiter watches run. By the
    time this is called the rows are committed, so an exception escaping here
    would report a finished run as a failure and hand the Celery retry policy a
    job it would redo from the top -- re-embedding the JD and re-spending the
    model calls -- to produce identical rows.

    The local import is INSIDE the guard, unlike Bodha's and Sutra's, and that
    is deliberate rather than a divergence: those two publish from a request
    handler that has already flushed its own work, while this one runs at the
    tail of a committed background run, so an ImportError has to cost the
    hand-off and not the run.

    Per CANDIDATE rather than per run, for the same reason a databank bulk
    upload allows partial success: one unreadable profile must not discard the
    other forty-nine candidates' hand-offs.

    The gate's verdict travels as `validated` and is NOT a publish veto. A job
    still on the four legacy categories fails `MIN_MATCHING_CATEGORIES` every
    single time; refusing to publish would leave Siddhi unable to tell a
    candidate scored on a short list from one who was never matched at all.
    """
    try:
        from app.services.agents import (  # noqa: PLC0415
            artifacts,
            envelope as run_envelope,
            gates,
            identity,
        )

        retrieval = _retrieval_evidence(profile.id, **stages)
        payload = _ai_score_payload(
            job, profile, link, breakdown, categories=categories, retrieval=retrieval
        )
        verdict = gates.run_gate(identity.YUKTI, payload)
        envelope = run_envelope.Envelope.for_run(
            tenant_id=str(job.tenant_id),
            agent_id=identity.YUKTI,
            task_type=_SCORING_TASK,
            interactive=False,
            job_id=str(job.id),
            candidate_id=str(profile.candidate_id),
            workflow_id=correlation_id,
            context_version=payload["jd_version"],
        )
        payload["correlation_id"] = envelope.workflow_id
        artifact = artifacts.publish(
            producer=identity.YUKTI,
            artifact_type="ai_score",
            payload=payload,
            tenant_id=str(job.tenant_id),
            job_id=str(job.id),
            candidate_id=str(profile.candidate_id),
            version=AI_SCORE_ARTIFACT_VERSION,
            source_refs=(*_resume_evidence_refs(profile), f"jobs:{job.id}"),
            validated=verdict.passed,
        )
        # Identifiers, counts and a boolean. No comment, no grade, no resume
        # line: this line is read from far more places than the review screen is.
        logger.info(
            "matching.ai_score_artifact_published job_id=%s candidate_id=%s "
            "artifact_id=%s validated=%s confidence=%s",
            job.id,
            profile.candidate_id,
            artifact.artifact_id,
            verdict.passed,
            verdict.confidence,
        )
        return artifact
    except Exception:
        logger.warning(
            "matching.ai_score_artifact_publish_failed job_id=%s profile_id=%s",
            getattr(job, "id", None),
            getattr(profile, "id", None),
            exc_info=True,
        )
        return None


def publish_ai_scores(
    job: Job,
    scored: Sequence[tuple[Profile, JobCandidateLink, dict]],
    *,
    categories: Sequence[tuple[str, str, str]],
    stages: dict[str, Any],
    correlation_id: str | None = None,
) -> list[Any]:
    """One `ai_score` artifact per scored candidate.

    This function contains no statement that can raise: every per-candidate
    publish is guarded, and what is left is a loop, a comparison and a list
    append. That is the property, not an accident -- `run_matching` calls this
    AFTER its commit, and a raise here would turn a run whose work is already
    saved into a run that reports failure.
    """
    published: list[Any] = []
    workflow_id = correlation_id
    for profile, link, breakdown in scored:
        artifact = _publish_one_ai_score(
            job,
            profile,
            link,
            breakdown,
            categories=categories,
            stages=stages,
            correlation_id=workflow_id,
        )
        if artifact is None:
            continue
        # One workflow id across the whole run, so "what did this matching run
        # publish" is one query rather than N unrelated ones. Minted by the
        # first successful publish rather than here, because minting it here
        # would need the agents package outside the guard that makes this safe.
        workflow_id = workflow_id or artifact.payload.get("correlation_id")
        published.append(artifact)
    return published


async def run_matching(
    session: AsyncSession,
    job_id: uuid.UUID | str,
    top_n: int = DEFAULT_TOP_N,
    progress: "matching_progress.Progress | None" = None,
) -> int:
    """Run the full hybrid pipeline for one job. Returns the number of links scored.

    `progress` is the recruiter-facing stage display (services/matching_progress)
    and is optional in the strong sense: every call on it is a no-op when it is
    absent, and none of them can raise into this function. The reasoning shown
    on the page is emitted BY the pipeline at the point the pipeline reaches
    each stage, rather than narrated by a model, so it cannot describe work that
    did not happen -- including the degraded paths, which mark their stage
    `skipped` and say why instead of showing a completed one.
    """
    from app.services import matching_progress

    job_id = uuid.UUID(str(job_id))
    reporter = progress or matching_progress.Progress()
    reporter.start("understanding")
    job = await session.get(Job, job_id)
    if job is None:
        raise ValueError(f"Job {job_id} not found")

    # ── JD embedding (stored on the jobs row for reuse; column added in
    #    migration). If the embedding service is unavailable, the semantic
    #    stage is skipped and matching degrades to keyword-only ranking rather
    #    than crashing the whole run. ──
    jd_text = _jd_text(job)
    reporter.start("planning")
    jd_vec: str | None = None
    reporter.start("jd_embedding")
    try:
        jd_embedding = (await embed([jd_text]))[0]
        jd_vec = _vector_literal(jd_embedding)
        await session.execute(
            text("UPDATE jobs SET embedding = CAST(:v AS vector) WHERE id = :id"),
            {"v": jd_vec, "id": str(job_id)},
        )
        reporter.finish("jd_embedding")
    except EmbeddingError:
        logger.warning(
            "matching.embeddings_unavailable job_id=%s, keyword-only ranking", job_id
        )
        reporter.skip(
            "jd_embedding",
            "The embedding service was unavailable, so this run ranks on keywords alone.",
        )

    # ── Repair the retrieval input before running the stages: a profile with a
    #    NULL embedding is invisible to stage 1, and an unparsed resume has an
    #    empty resume_tsv so it is invisible to stage 2 as well. ──
    reporter.start("preparing_candidates")
    linked_ids = await _linked_stage(session, job_id)
    await _backfill_missing_embeddings(session, linked_ids)
    reporter.finish("preparing_candidates")

    # ── Stages 1 + 2, deduplicated union, then EVERY explicitly-linked
    #    candidate (retrieval is a ranking prior, never an eligibility gate). ──
    if jd_vec:
        reporter.start("semantic_retrieval")
        semantic_ids = await _semantic_stage(session, job_id, jd_vec, top_n)
        reporter.finish(
            "semantic_retrieval",
            f"{len(semantic_ids)} resume(s) matched on meaning.",
        )
    else:
        semantic_ids = []
        reporter.skip(
            "semantic_retrieval",
            "Skipped: the job description could not be embedded for this run.",
        )
    reporter.start("keyword_retrieval")
    keyword_ids = await _keyword_stage(session, job_id, _keyword_query_terms(job), top_n)
    reporter.finish(
        "keyword_retrieval", f"{len(keyword_ids)} resume(s) matched on named skills."
    )
    # Union order is the deterministic-fallback rank signal: retrieval hits
    # first (best first), then linked candidates retrieval never surfaced.
    reporter.start("fusion")
    profile_ids: list[uuid.UUID] = list(
        dict.fromkeys([*semantic_ids, *keyword_ids, *linked_ids])
    )
    if not profile_ids:
        reporter.finish("fusion", "No candidates are linked to this job yet.")
        await session.commit()
        return 0
    reporter.finish(
        "fusion",
        f"{len(profile_ids)} candidate(s) to score, including every candidate "
        "linked to this job.",
    )

    profiles = (
        (await session.execute(select(Profile).where(Profile.id.in_(profile_ids))))
        .scalars()
        .all()
    )
    profiles_by_id = {p.id: p for p in profiles}
    # keep union order
    profiles = [profiles_by_id[pid] for pid in profile_ids if pid in profiles_by_id]

    # ── Stage 3: LLM scoring against THIS JOB'S matching categories (raises
    #    LLMUnavailableError only if the whole chain is exhausted -- the Celery
    #    task's retry policy handles that; individual malformed profiles are
    #    skipped with a warning) ──
    #
    # The category list is read ONCE, here, and applied to every candidate in
    # the run. That is spec 3.2's "applies automatically to every candidate
    # sourced for that job, with no further human step per candidate": there is
    # no per-candidate configuration to get wrong because there is no
    # per-candidate decision at all.
    categories = tuple(await matching_categories.resolved_categories(session, job_id))
    customer_patterns = await _customer_success_patterns(session, job)
    reporter.start(
        "scoring",
        f"Assessing {len(profiles)} candidate(s) against "
        f"{len(categories)} matching categor{'y' if len(categories) == 1 else 'ies'}.",
    )
    reporter.scored(0, len(profiles))
    breakdowns = await _llm_score(
        session, jd_text, profiles, customer_patterns, categories
    )
    reporter.scored(len(breakdowns), len(profiles))
    reporter.finish("scoring")
    reporter.start("remarks")

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
    scored_links: list[tuple[Profile, JobCandidateLink, dict]] = []
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
            # Belt and braces: nothing reaches storage outside 25-30 words.
            breakdown = enforce_breakdown_comments(breakdowns[profile.id])
            overall = breakdown["overall"]["score"]
            # match_score stays 0-100 (overall × 10) so sorting/dashboard and
            # the tier boundary rule are unchanged.
            link.match_score = round(overall * 10, 1)
            # HR-visible, never candidate-visible — the holistic 5th comment.
            link.match_rationale = breakdown["overall"]["comment"]
            link.tier = assign_tier(link.match_score)
            scored_links.append((profile, link, breakdown))
            scored += 1

    # match_breakdown_json is intentionally NOT on the SQLAlchemy model (same
    # pattern as jobs.embedding) — flush so new links get ids, then write the
    # breakdown via raw SQL.
    reporter.finish("remarks")
    reporter.start("saving")
    await session.flush()
    for _profile, link, breakdown in scored_links:
        await session.execute(
            text(
                "UPDATE job_candidate_links "
                "SET match_breakdown_json = CAST(:breakdown AS jsonb) "
                "WHERE id = :id"
            ),
            {"breakdown": json.dumps(breakdown), "id": str(link.id)},
        )

    await session.commit()
    reporter.finish("saving", f"{scored} candidate(s) rated.")

    # ── Yukti's hand-off to Siddhi (spec 16.5) ──────────────────────────────
    # AFTER the commit, deliberately: an artifact describes rows that exist, and
    # a run whose work is saved is a successful run whatever becomes of the
    # hand-off. `publish_ai_scores` cannot raise, so this line cannot cost the
    # return value; it reads nothing back and writes nothing, so deleting it
    # changes no stored value, no grade, no tier and no ordering.
    #
    # The retrieval stages are handed over as the raw id lists this run already
    # holds. Indexing them per candidate happens inside the guard, so a bug in
    # the provenance arithmetic cannot reach a recruiter either.
    publish_ai_scores(
        job,
        scored_links,
        categories=categories,
        stages={
            "semantic_ids": semantic_ids,
            "keyword_ids": keyword_ids,
            "linked_ids": linked_ids,
            "fusion_order": profile_ids,
            "top_n": top_n,
            "semantic_ran": bool(jd_vec),
        },
    )
    return scored
