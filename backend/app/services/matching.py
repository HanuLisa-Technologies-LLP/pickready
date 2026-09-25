"""AI Matching: find the candidates for one job and have Yukti read each one.

WHAT THIS MODULE IS NOW (Phase 2 WP-B, 2026-09-25)
--------------------------------------------------
The RUN, and nothing that judges. `run_matching` gathers the pool and hands
every (link, the resume that link was made with) pair to
`yukti.scoring.score_links`, which reads the resume against the job's SAVED
skills and writes the link's Yukti columns. The retired matcher's scoring half
(the category prompt, the 25-30 word padding, the deterministic pre-screen
substitute score, the same-tenant "success patterns", the unread `ai_score`
artifact, the tier and the longevity nudge) is DELETED rather than bypassed.
What is left, in order:

1. Gate. A closed, archived or unpublished job is not matched, and neither is
   a job whose Skills step was never saved (`assessment_contract.
   skills_saved`): there is nothing to read a resume AGAINST, and the old
   fallback onto four generic categories ranked candidates on criteria nobody
   chose (audit Part 1 #13).
2. The JD embedding, from `yukti.inputs.jd_text` (no `level`, no `reportees`,
   no compensation). An embedding outage skips the semantic stage, SAYS SO on
   the progress payload (`degraded_reasons`), and the run continues on
   keywords.
3. Retrieval returns CANDIDATES, never profiles. The pool used to be keyed by
   profile id, so one person with two resumes could be scored twice, and the
   "best" retrieval profile was scored against a link made with a different
   one (audit Part 1 #12). Now a linked candidate is ALWAYS read from
   `link.profile_id`, the resume their application carries, whatever resume
   retrieval found.
4. Databank discovery stays platform-wide over CONSENTING candidates (owner
   ruling), and a link it mints enters at `sourced` with its history row
   through `hiring_pipeline.start_sourced`: being found in a databank is not
   applying (audit Part 1 #5). The resume read is the candidate's MAIN resume
   when it has text, else the one retrieval found.
5. `score_links` for the whole pool, one commit. A model failure is
   "Not assessed" on that link, never a substitute score, and a transient
   failure never overwrites a good earlier reading (`scoring.apply_outcome`).

Retrieval is a ranking prior and never an eligibility gate: every non-archived
link on the job is read, whether or not retrieval surfaced it (the 2026-07-26
rule, unchanged).

THE LEGACY READ SIDE AT THE BOTTOM IS HISTORY
---------------------------------------------
`ranking_payload`, `client_breakdown`, `matching_label` and the word helpers
still PROJECT the frozen `match_breakdown_json` for readers owned by other
work packages (the ranked table, the lifecycle emails, the verification
critic, two scripts). Nothing writes a breakdown any more. Phase 2 WP-C and
WP-E move those readers onto the Yukti columns and WP-F deletes the section.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobCandidateLink, LinkSource, Profile
from app.models.candidate import SOURCE_TYPE_DATABANK
from app.services import assessment_contract, hiring_pipeline, locks, matching_categories, rating
from app.services.embeddings import EmbeddingError, embed
from app.services.hiring import ontology
from app.services.yukti import config as yukti_config
from app.services.yukti import inputs as yukti_inputs
from app.services.yukti import scoring as yukti_scoring

if TYPE_CHECKING:
    from app.services import matching_progress

logger = logging.getLogger(__name__)

DEFAULT_TOP_N = 50

#: The history remark on every link AI Matching mints from the databank.
DATABANK_REMARK = "Found in the Vivekium databank by AI Matching"

# ── The sentences a recruiter reads when a run cannot proceed or degrades ──
# Server words, rendered verbatim by the job page (the one-author rule the
# read-only notice follows): the page never composes its own explanation.
SKILLS_NOT_SAVED = (
    "Save the skills on this job before AI Matching can rank candidates."
)
JOB_NOT_OPEN = (
    "AI Matching runs only on a published job that is not closed or archived."
)
ALREADY_RUNNING = (
    "Another AI Matching run for this job is already in progress. Its "
    "results will appear on this page when it finishes."
)
EMBEDDING_DEGRADED = (
    "The embedding service was unavailable, so this run found candidates by "
    "keywords only."
)


def _not_assessed_sentence(count: int) -> str:
    return (
        f"The AI check could not be completed for {count} candidate(s). They "
        "read Not assessed and are retried on the next AI Matching run."
    )


def _kept_prior_sentence(count: int) -> str:
    return (
        f"The AI check could not be completed for {count} candidate(s) this "
        "time, so their earlier result is still shown."
    )


# ── Retrieval ────────────────────────────────────────────────────────────────


def _vector_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


def _keyword_query_terms(job: Job) -> list[str]:
    """The JD's own skill terms PLUS what a candidate may have called the same
    work (spec-doc6 §4.6, RPN-PHIL-001 §58).

    ADDITIVE, never substitutive, which is `ontology.expand`'s own contract. The
    JD's words come first and the equivalents follow, so a resume using the JD's
    exact vocabulary still ranks on it; what changes is that a resume saying
    "semantic technologies" against a JD asking for "graph database" stops
    scoring a zero on the lexical stage. Retrieval is a ranking prior and never
    decides who is scored, so a wrong expansion here costs a position in a list
    and can never cost a candidate their assessment.
    """
    jd = job.jd_json or {}
    skills = [s for s in (jd.get("skills") or []) if isinstance(s, str)]
    terms = [*skills]
    if job.title:
        terms.append(job.title)
    return ontology.expand(terms)


_TSQUERY_WORD = re.compile(r"[A-Za-z0-9]+")


def _tsquery(terms: Sequence[str]) -> str:
    """An OR tsquery over the expanded terms.

    `plainto_tsquery` ANDs, and ANDing is wrong for this stage in a way that is
    silent: a JD naming eight skills matched only a resume containing all eight,
    so the lexical half almost never fired, and fusion still returned the
    semantic hits so retrieval LOOKED like it worked. `services/rag/retrieval`
    found this on the live index and fixed it there; this is the same fix on the
    other lexical retriever, for the same reason, that precision is fusion's job
    and not this retriever's.

    ANDing also actively fights the ontology: every equivalent added to the
    query would make the conjunction harder to satisfy, so expansion would
    NARROW the pool it exists to widen.

    Input is reduced to alphanumeric words before it reaches `to_tsquery`, which
    parses operators out of its argument. "ci/cd" and "gd&t" would otherwise be
    a syntax error rather than a search.
    """
    words: list[str] = []
    for term in terms:
        for word in _TSQUERY_WORD.findall(str(term or "").casefold()):
            if len(word) > 1:
                words.append(word)
    return " | ".join(dict.fromkeys(words))


@dataclass(frozen=True)
class Found:
    """One CANDIDATE retrieval surfaced, and the profile that ranked them."""

    candidate_id: uuid.UUID
    profile_id: uuid.UUID


@dataclass(frozen=True)
class Linked:
    """One live application on the job: the link and the resume it carries."""

    link_id: uuid.UUID
    candidate_id: uuid.UUID
    profile_id: uuid.UUID | None


async def _semantic_stage(
    session: AsyncSession, job_id: uuid.UUID, jd_vec: str, top_n: int
) -> list[Found]:
    """Top-N CANDIDATES by cosine distance, each with their closest profile.

    The profile is a RANK signal only. It is read as the candidate's resume
    only for a databank candidate with no usable main resume; a linked
    candidate is always read from their link's own profile.
    """
    rows = await session.execute(
        text(
            """
            SELECT candidate_id, profile_id FROM (
                SELECT DISTINCT ON (p.candidate_id)
                       p.candidate_id,
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
            ORDER BY dist, candidate_id
            LIMIT :top_n
            """
        ),
        {"jd_vec": jd_vec, "job_id": str(job_id), "top_n": top_n},
    )
    return [Found(r.candidate_id, r.profile_id) for r in rows]


async def _keyword_stage(
    session: AsyncSession, job_id: uuid.UUID, query_terms: Sequence[str], top_n: int
) -> list[Found]:
    """Top-N CANDIDATES by full-text rank over resume_tsv (catches exact
    terms -- tool names, certifications -- that embeddings can miss)."""
    tsquery = _tsquery(query_terms)
    if not tsquery:
        return []
    rows = await session.execute(
        text(
            """
            SELECT candidate_id, profile_id FROM (
                SELECT DISTINCT ON (p.candidate_id)
                       p.candidate_id,
                       p.id AS profile_id,
                       ts_rank(p.resume_tsv, to_tsquery('english', :q)) AS rank
                FROM profiles p
                JOIN candidates c ON c.id = p.candidate_id
                WHERE p.resume_tsv @@ to_tsquery('english', :q)
                  AND (
                    c.consent_databank = true
                    OR EXISTS (
                        SELECT 1 FROM job_candidate_links l
                        WHERE l.job_id = :job_id AND l.candidate_id = c.id
                    )
                  )
                ORDER BY p.candidate_id, rank DESC
            ) ranked
            ORDER BY rank DESC, candidate_id
            LIMIT :top_n
            """
        ),
        {"q": tsquery, "job_id": str(job_id), "top_n": top_n},
    )
    return [Found(r.candidate_id, r.profile_id) for r in rows]


async def _linked_stage(session: AsyncSession, job_id: uuid.UUID) -> list[Linked]:
    """EVERY live application on this job, with the profile it was made with.

    Retrieval is capped at top_n and cannot see a profile whose embedding is
    NULL or whose resume_tsv misses the JD's terms. A candidate who applied, or
    whom a recruiter uploaded, must never be left unread because retrieval
    could not see them, so every non-archived link joins the pool. No cap: the
    pool is bounded by the job's own applicant count.
    """
    rows = await session.execute(
        text(
            """
            SELECT l.id AS link_id, l.candidate_id, l.profile_id
            FROM job_candidate_links l
            WHERE l.job_id = :job_id AND l.archived_at IS NULL
            ORDER BY l.created_at, l.id
            """
        ),
        {"job_id": str(job_id)},
    )
    return [Linked(r.link_id, r.candidate_id, r.profile_id) for r in rows]


async def _backfill_missing_embeddings(
    session: AsyncSession, profile_ids: Sequence[uuid.UUID]
) -> None:
    """Repair the semantic stage's input for linked profiles it would skip.

    Two causes of a NULL `profiles.embedding`, and one repair each:

    * The resume text IS stored but the embedding is not (the parse kept its
      result through an embedding outage, `resume_parsing`). Embedded here. An
      embedding outage now is logged with its traceback and the run goes on:
      the link is still READ by Yukti (retrieval never decides who is read),
      it just cannot rank on meaning this time.
    * There is no resume text: `pickready.parse_resume` is re-queued for a
      profile whose stored file is complete. A dispatch failure RAISES, like
      every dispatch: a run that silently failed to re-queue a parse would
      leave that resume unread with nothing saying why.
    """
    if not profile_ids:
        return
    from app.services.resume_storage import profile_has_resume  # noqa: PLC0415
    from app.workers.dispatch import dispatch  # noqa: PLC0415

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
        except EmbeddingError:
            logger.warning(
                "matching.embedding_backfill_unavailable profiles=%d",
                len(embeddable),
                exc_info=True,
            )
        else:
            for profile, vector in zip(embeddable, vectors):
                profile.embedding = vector
            await session.flush()
            logger.info("matching.embeddings_backfilled count=%d", len(embeddable))

    for profile in rows:
        if (profile.resume_text or "").strip():
            continue
        if not profile_has_resume(profile):
            logger.warning(
                "matching.profile_unparseable profile_id=%s: no stored resume "
                "file to parse; Yukti reads it as no_resume_text",
                profile.id,
            )
            continue
        dispatch("pickready.parse_resume", args=[str(profile.id)])
        logger.info("matching.parse_resume_requeued profile_id=%s", profile.id)


async def _databank_profiles(
    session: AsyncSession, found: dict[uuid.UUID, uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """candidate -> the profile Yukti reads, for UNLINKED databank candidates.

    Consent is re-checked HERE, from the candidate row, rather than trusted
    from the retrieval query: this is the line that turns a search hit into a
    link on a customer's job. The resume read is the candidate's MAIN resume
    when it carries text (the one they maintain), else the profile retrieval
    ranked them on.
    """
    if not found:
        return {}
    rows = await session.execute(
        text(
            """
            SELECT c.id AS candidate_id,
                   c.main_profile_id,
                   COALESCE(btrim(p.resume_text, E' \\t\\r\\n') <> '', false) AS main_has_text
            FROM candidates c
            LEFT JOIN profiles p ON p.id = c.main_profile_id
            WHERE c.consent_databank = true
              AND c.id = ANY(CAST(:ids AS uuid[]))
            """
        ),
        {"ids": [str(cid) for cid in found]},
    )
    chosen: dict[uuid.UUID, uuid.UUID] = {}
    for row in rows:
        if row.main_profile_id is not None and row.main_has_text:
            chosen[row.candidate_id] = row.main_profile_id
        else:
            chosen[row.candidate_id] = found[row.candidate_id]
    return chosen


# ── The run ──────────────────────────────────────────────────────────────────


async def run_matching(
    session: AsyncSession,
    job_id: uuid.UUID | str,
    top_n: int = DEFAULT_TOP_N,
    progress: "matching_progress.Progress | None" = None,
) -> int:
    """Run AI Matching for one job. Returns the number of links Yukti scored.

    `progress` is the recruiter-facing stage display (services/matching_progress)
    and is optional in the strong sense: every call on it is a no-op when it is
    absent, and none of them can raise into this function. Each stage is
    emitted BY the pipeline at the point the pipeline reaches it, so the display
    cannot describe work that did not happen, and a degraded run says why in
    `degraded_reasons` rather than looking like a full one.

    ONE commit, at the end. The per-link Yukti locks `score_links` takes are
    transaction scoped, so they are held until exactly that commit.
    """
    from app.services import matching_progress  # noqa: PLC0415

    job_id = uuid.UUID(str(job_id))
    reporter = progress or matching_progress.Progress()
    reporter.start("understanding")
    job = await session.get(Job, job_id)
    if job is None:
        raise ValueError(f"Job {job_id} not found")

    # ── ONE RUN PER JOB, ACROSS PROCESSES (services/locks). Taken BEFORE the
    #    first vendor call, because a duplicate refused after the JD embedding
    #    has already been paid for is a report, not a guard. Route.ECS gives
    #    every dispatch its own container, so in-process coalescing cannot see
    #    this duplicate, and the task's own transaction holds until the final
    #    commit, so the xact-scoped lock covers the whole run with no unlock
    #    to forget. The second caller RETURNS: the first run is doing exactly
    #    the work it came to do, and its results reach the same rows. ──
    if not await locks.try_advisory_lock(session, locks.MATCHING, job_id):
        logger.info("matching.already_running job_id=%s returning", job_id)
        reporter.skip("understanding", ALREADY_RUNNING)
        return 0

    if job.ratified_at is None or job.closed_at is not None or job.archived_at is not None:
        logger.info("matching.job_not_open job_id=%s returning", job_id)
        reporter.skip("understanding", JOB_NOT_OPEN)
        return 0
    if not await assessment_contract.skills_saved(session, job.id):
        logger.info("matching.skills_not_saved job_id=%s returning", job_id)
        reporter.skip("understanding", SKILLS_NOT_SAVED)
        return 0

    # ── The JD embedding (stored on the jobs row for reuse). An outage skips
    #    the semantic stage and the run continues on keywords, SAYING so. ──
    reporter.start("planning")
    jd_vec: str | None = None
    reporter.start("jd_embedding")
    try:
        jd_embedding = (await embed([yukti_inputs.jd_text(job)]))[0]
    except EmbeddingError:
        logger.warning(
            "matching.embeddings_unavailable job_id=%s, keyword-only retrieval",
            job_id,
            exc_info=True,
        )
        reporter.skip("jd_embedding", EMBEDDING_DEGRADED)
        reporter.degrade(EMBEDDING_DEGRADED)
    else:
        jd_vec = _vector_literal(jd_embedding)
        await session.execute(
            text("UPDATE jobs SET embedding = CAST(:v AS vector) WHERE id = :id"),
            {"v": jd_vec, "id": str(job_id)},
        )
        reporter.finish("jd_embedding")

    # ── Repair the retrieval input for every live application first. ──
    reporter.start("preparing_candidates")
    linked = await _linked_stage(session, job_id)
    await _backfill_missing_embeddings(
        session, [row.profile_id for row in linked if row.profile_id is not None]
    )
    reporter.finish("preparing_candidates")

    # ── Retrieval, by CANDIDATE. ──
    if jd_vec:
        reporter.start("semantic_retrieval")
        semantic = await _semantic_stage(session, job_id, jd_vec, top_n)
        reporter.finish(
            "semantic_retrieval", f"{len(semantic)} candidate(s) matched on meaning."
        )
    else:
        semantic = []
        reporter.skip(
            "semantic_retrieval",
            "Skipped: the job description could not be embedded for this run.",
        )
    reporter.start("keyword_retrieval")
    keyword = await _keyword_stage(session, job_id, _keyword_query_terms(job), top_n)
    reporter.finish(
        "keyword_retrieval", f"{len(keyword)} candidate(s) matched on named skills."
    )

    # ── Fusion: one entry per PERSON, union order preserved, then every live
    #    application retrieval never surfaced. ──
    reporter.start("fusion")
    retrieved: dict[uuid.UUID, uuid.UUID] = {}
    for hit in (*semantic, *keyword):
        retrieved.setdefault(hit.candidate_id, hit.profile_id)
    pool: list[uuid.UUID] = list(
        dict.fromkeys([*retrieved, *(row.candidate_id for row in linked)])
    )
    if not pool:
        reporter.finish("fusion", "No candidates are linked to this job yet.")
        await session.commit()
        return 0

    links = (
        (
            await session.execute(
                select(JobCandidateLink).where(JobCandidateLink.job_id == job_id)
            )
        )
        .scalars()
        .all()
    )
    links_by_candidate = {link.candidate_id: link for link in links}
    unlinked = {
        cid: retrieved[cid] for cid in pool if cid not in links_by_candidate and cid in retrieved
    }
    databank = await _databank_profiles(session, unlinked)

    ordered: list[JobCandidateLink] = []
    minted = 0
    for candidate_id in pool:
        link = links_by_candidate.get(candidate_id)
        if link is not None:
            # An archived application was taken off this job by a person, and
            # retrieval finding the same candidate again does not put them back.
            if link.archived_at is None:
                ordered.append(link)
            continue
        profile_id = databank.get(candidate_id)
        if profile_id is None:
            # Not linked and not a consenting databank candidate.
            continue
        link = JobCandidateLink(
            tenant_id=job.tenant_id,
            job_id=job_id,
            candidate_id=candidate_id,
            profile_id=profile_id,
            source=LinkSource.databank,
            source_type=SOURCE_TYPE_DATABANK,
        )
        await hiring_pipeline.start_sourced(session, link, remarks=DATABANK_REMARK)
        links_by_candidate[candidate_id] = link
        ordered.append(link)
        minted += 1
    reporter.finish(
        "fusion",
        f"{len(ordered)} candidate(s) to check, including every candidate "
        "linked to this job.",
    )
    if minted:
        logger.info("matching.databank_linked job_id=%s links=%d", job_id, minted)

    # ── The resume each link carries, and nothing else (audit Part 1 #12). ──
    profile_ids = [link.profile_id for link in ordered if link.profile_id is not None]
    profiles = {
        p.id: p
        for p in (
            (await session.execute(select(Profile).where(Profile.id.in_(profile_ids))))
            .scalars()
            .all()
        )
    } if profile_ids else {}
    pairs = [(link, profiles.get(link.profile_id)) for link in ordered]

    answered = sum(1 for link in ordered if link.validation_json)
    reporter.start("validation_fit")
    reporter.finish(
        "validation_fit",
        f"{answered} of {len(ordered)} candidate(s) answered the application questions.",
    )

    reporter.start("scoring")
    reporter.scored(0, len(pairs))
    summary = await yukti_scoring.score_links(
        session, job, pairs, on_progress=reporter.scored
    )
    reporter.finish("scoring")

    reporter.start("grounding")
    dropped = sum(
        len(outcome.provenance.get("ungrounded") or ())
        for outcome in summary.outcomes.values()
    )
    reporter.finish(
        "grounding",
        f"{dropped} claim(s) could not be found in the resume they cited and were dropped."
        if dropped
        else "Every tag kept was found in the resume it cites.",
    )

    transient = [
        link_id
        for link_id, outcome in summary.outcomes.items()
        if outcome.is_transient_failure and link_id not in summary.kept_prior
    ]
    if transient:
        reporter.degrade(_not_assessed_sentence(len(transient)))
    if summary.kept_prior:
        reporter.degrade(_kept_prior_sentence(len(summary.kept_prior)))

    reporter.start("saving")
    await session.commit()
    scored = summary.count(yukti_config.STATUS_SCORED) - len(summary.kept_prior)
    reporter.finish("saving", f"{scored} candidate(s) checked against the skills.")
    logger.info(
        "matching.complete job_id=%s pool=%d scored=%d not_assessed=%d "
        "kept_prior=%d skipped_locked=%d",
        job_id,
        len(ordered),
        scored,
        summary.count(yukti_config.STATUS_NOT_ASSESSED) - len(summary.kept_prior),
        len(summary.kept_prior),
        len(summary.skipped_locked),
    )
    return scored


async def score_profile(session: AsyncSession, profile_id: uuid.UUID | str) -> int:
    """Read one freshly parsed resume against every job it is applied to.

    `pickready.yukti_score_profile`, dispatched by the parse AFTER its commit.
    It replaces the pre-screen grade the parse used to write, so a new
    applicant is ranked without a recruiter re-running AI Matching. A link is
    read when it carries THIS profile, is not archived, and its job is
    published, not closed, not archived and has saved skills; anything else is
    left for the run that opens it. Per job, because the job context (skills,
    SWOT needs, JD) is built once per job. Commits once; returns the number of
    links scored.
    """
    profile_id = uuid.UUID(str(profile_id))
    profile = await session.get(Profile, profile_id)
    if profile is None:
        raise ValueError(f"Profile {profile_id} not found")
    rows = (
        await session.execute(
            select(JobCandidateLink, Job)
            .join(Job, Job.id == JobCandidateLink.job_id)
            .where(
                JobCandidateLink.profile_id == profile_id,
                JobCandidateLink.archived_at.is_(None),
                Job.ratified_at.is_not(None),
                Job.closed_at.is_(None),
                Job.archived_at.is_(None),
            )
            .order_by(JobCandidateLink.created_at, JobCandidateLink.id)
        )
    ).all()
    scored = 0
    for link, job in rows:
        if not await assessment_contract.skills_saved(session, job.id):
            continue
        summary = await yukti_scoring.score_links(session, job, [(link, profile)])
        scored += summary.count(yukti_config.STATUS_SCORED) - len(summary.kept_prior)
    await session.commit()
    logger.info(
        "matching.profile_scored profile_id=%s links=%d scored=%d",
        profile_id,
        len(rows),
        scored,
    )
    return scored


# ═════════════════════════════════════════════════════════════════════════════
# LEGACY READ SIDE: HISTORY ONLY, DELETED BY PHASE 2 WP-F
# ═════════════════════════════════════════════════════════════════════════════
# Projections of the frozen `match_breakdown_json` the retired matcher wrote.
# Nothing in this module writes one any more. They survive only while their
# readers (owned by other work packages: the ranked table, lifecycle emails,
# the verification critic, the eval and seed scripts) move to the Yukti
# columns; a reader here must not gain a new caller.

COMMENT_MIN_WORDS = 25
COMMENT_MAX_WORDS = 30

# A "word" is any whitespace-delimited token containing at least one
# alphanumeric character -- bare dashes, bullets and stray punctuation don't
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

def word_count(text: str | None) -> int:
    """Count words in `text` -- whitespace-delimited tokens with a letter/digit.

    Pure and side-effect free.
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
    for the last token that ends a clause (`. , ; : –`) at or after
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

    Pure and side-effect free.
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
        # Filler exhausted (caller passed a short custom filler) -- recycle the
        # built-in clauses so the guarantee still holds.
        j = 0
        while _tokens_word_count(tokens) < min_words:
            tokens = tokens + _PAD_CLAUSES[j % len(_PAD_CLAUSES)].split()
            j += 1
            if j > 2 * len(_PAD_CLAUSES):  # pragma: no cover -- defensive
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


def enforce_breakdown_comments(breakdown: dict) -> dict:
    """Coerce every comment in a breakdown into the 25-30 word contract.

    Nothing in the product writes a breakdown any more; the mock-data seed
    still builds one and calls this, until WP-F rewrites that seed.
    """
    for field in (*breakdown_keys(breakdown), "overall"):
        block = breakdown.get(field)
        if not isinstance(block, dict):
            continue
        block["comment"] = enforce_word_range(block.get("comment"))
    return breakdown

#: The four categories the retired matcher scored every job on, read by the
#: projections below for a breakdown stored before per-job categories existed.
PARAMETERS: tuple[str, ...] = matching_categories.LEGACY_KEYS


def compute_overall_score(
    scores: dict[str, int | float], keys: tuple[str, ...] = PARAMETERS
) -> float:
    """Unweighted mean of the 1-10 category scores, rounded to 1 decimal.

    Pure Python math (banker's rounding over IEEE-754 doubles) -- the overall
    score is NEVER taken from the LLM. Internal only: it orders the candidate
    list and assigns a tier, and never crosses the API boundary.
    """
    keys = tuple(keys) or PARAMETERS
    return round(sum(float(scores[key]) for key in keys) / len(keys), 1)


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
    comments and labels are null and `ranking_status` is "not_scored" -- an
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
    from a degraded retrieval one) -- it never needs 1-10 parameter scores or
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

