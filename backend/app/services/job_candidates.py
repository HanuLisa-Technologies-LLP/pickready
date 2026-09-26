"""Ranked candidate list for the job detail page.

The candidate table lives INLINE on the job page, so this service owns the one
thing that page cannot do for itself: deciding the order, and saying in words
what each row's AI Match is.

WHY THE SORT IS SERVER-SIDE
---------------------------
The table is paginated at 25 rows. If the browser sorted each page, a candidate
could appear on two pages (or on none) as soon as scores changed between
requests, because page 2 would be cut from a differently-ordered list than page
1. Ordering therefore happens once, in SQL, with a total order, including an
explicit tiebreak, so page boundaries are stable.

ONE KEY, DERIVED AT READ TIME (Vivekium release, Phase 2)
---------------------------------------------------------
The order is `yukti.ranking.order_by_sql()`: Yukti's pre-assessment score,
blended with the Tatva Assessment's overall once a report exists (the tenant's
ratio, 70/30 by default), capped AFTER the blend when a Must-have failed, then
arrival, then id. It SUPERSEDES both earlier orders: the grade-driven resume
keys read out of `match_breakdown_json` (which a renamed category silently
turned into NULL for every row) and the 2026-09-04 "assessed first, stage
before score" rule (owner decision D2: one blended key, so an assessed
candidate can sit below a strong resume when the assessment went badly).

The key is never stored and never serialized. `ranked_candidates` selects it
as `rank_score` only so `yukti.projection` can turn it into one of the four
grade words; nothing numeric reaches the payload (D3, no exception).

Every linked candidate is LISTED, whatever Yukti holds for them: a pending or
not-assessed row has no key and sorts last, it is never filtered out
(claude.md: every linked candidate is scored, and every linked candidate is
listed).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import assessment_video_access as video_access
from app.services.yukti import projection, ranking

#: Spec section 2.4: 25 rows per page.
PAGE_SIZE = 25

#: Maximum page size a caller may request. Bounded so a hand-crafted
#: `?page_size=100000` cannot turn one request into a full-table scan.
MAX_PAGE_SIZE = 100

# ── Old Profiles vs New Profiles (spec §4.2) ─────────────────────────────────
# Renewing an expired job restamps `jobs.posting_start_date`, which opens a new
# 30-day window. Everyone who applied BEFORE that instant applied to the
# previous run of the job: they are Old Profiles, still fully visible (the
# candidate-data-ownership promise), just no longer part of the live intake.
#
# The distinction is DERIVED from the two timestamps rather than stored on the
# link. A stored flag would need back-filling on every renewal and would be
# wrong for any row written between the renewal and the backfill.
PROFILE_AGE_OLD = "old"
PROFILE_AGE_NEW = "new"
PROFILE_AGES: tuple[str, ...] = (PROFILE_AGE_OLD, PROFILE_AGE_NEW)

PROFILE_AGE_LABELS: dict[str, str] = {
    PROFILE_AGE_OLD: "Old Profile",
    PROFILE_AGE_NEW: "New Profile",
}

_PROFILE_AGE_SQL = (
    "CASE WHEN j.posting_start_date IS NOT NULL "
    "      AND l.created_at < j.posting_start_date "
    f"     THEN '{PROFILE_AGE_OLD}' ELSE '{PROFILE_AGE_NEW}' END"
)


# ── New Candidates (workflow section 32) ─────────────────────────────────────
#
# "New candidates who apply for the role after the initial ranking are
# displayed under a dedicated New Candidates section. This prevents newly
# arriving candidates from being invisible to the hiring team."
#
# The invisibility is real and it is a consequence of ranking. A recruiter
# works down a ranked page, selects a batch and moves on; somebody who applies
# the next morning lands wherever their score puts them, which for most people
# is page two of a list nobody opens again. Nothing is broken and nobody sees
# them.
#
# WHAT MAKES A CANDIDATE NEW: they arrived after the most recent time this job
# invited ANYONE to an assessment. That instant is when the team last acted on
# the ranking, so it is the honest boundary between "was considered in a round"
# and "has never been in front of anybody".
#
# DERIVED, NEVER STORED, for the same reason `profile_age` is: a stored flag
# needs back-filling on every selection round and is wrong for every row
# written between the round and the backfill.
#
# BEFORE THE FIRST ROUND, NOBODY IS NEW. The comparison is against a MAX over
# an empty set, which is NULL, and `l.created_at > NULL` is NULL -- falsy,
# which is exactly right. A job whose first batch has not gone out has one
# pool, not a pool plus a supplement, and marking all of it New would make the
# section noise on the day the recruiter most needs the main list.
_NEW_CANDIDATE_SQL = """
    l.created_at > (
        SELECT MAX(conv.invitation_sent_at)
          FROM assessment_conversations conv
          JOIN job_candidate_links prior ON prior.id = conv.job_candidate_link_id
         WHERE prior.job_id = l.job_id
           AND conv.invitation_sent_at IS NOT NULL
    )
"""

#: `?arrival=` values. `new` is the dedicated section; `considered` is the rest
#: of the table, offered so the main list can exclude the supplement rather
#: than showing every candidate twice.
ARRIVAL_NEW = "new"
ARRIVAL_CONSIDERED = "considered"
ARRIVALS: tuple[str, ...] = (ARRIVAL_NEW, ARRIVAL_CONSIDERED)


def profile_age(link_created_at, posting_start) -> str:
    """Pure counterpart of `_PROFILE_AGE_SQL`, for callers holding the values.

    The two MUST agree: a row the SQL calls old and this function calls new
    would be billed at one rate and labelled at another.
    """
    if link_created_at is None or posting_start is None:
        return PROFILE_AGE_NEW
    left = link_created_at if link_created_at.tzinfo else link_created_at.replace(tzinfo=None)
    right = posting_start if posting_start.tzinfo else posting_start.replace(tzinfo=None)
    if (left.tzinfo is None) != (right.tzinfo is None):
        # Mixed awareness means one side came from a driver that dropped the
        # zone; comparing them raises. Treat as new rather than mis-bill.
        return PROFILE_AGE_NEW
    return PROFILE_AGE_OLD if left < right else PROFILE_AGE_NEW


#: Display labels for a job grade. The ranked table no longer shows a "Level"
#: column (a grade belongs to the job, not to a candidate), but the label is
#: still how a prompt or a job summary names the grade, and four modules read
#: it from here (`hiring/sutra`, `swot_analysis`, `job_relevance`,
#: `yukti/inputs`), so it stays in this one place.
GRADE_LABELS: dict[str, str] = {
    "non_managerial": "Non-managerial",
    "managerial": "Managerial",
    "leadership": "Leadership",
    "cxo": "CXO",
}


def grade_label(grade: str | None) -> str:
    """Display label for a job grade. Unknown/NULL reads as Non-managerial,
    matching the NOT NULL default on jobs.assessment_grade."""
    return GRADE_LABELS.get(grade or "non_managerial", GRADE_LABELS["non_managerial"])


def normalize_page(page: int | None, page_size: int | None) -> tuple[int, int]:
    """Coerce caller pagination into a safe (page, page_size).

    Pages are 1-INDEXED (spec section 2.4 asks for one convention, consistently
    applied; 1-indexed is what the UI shows, so the API speaks the same
    language). Anything below 1, or a page size outside 1..MAX_PAGE_SIZE, is
    clamped rather than rejected: a bad page number should not 422 a table.
    """
    resolved_page = max(1, page if page is not None else 1)
    # `is None` rather than `or`: 0 is a VALUE (clamp it to 1), not an absence
    # (which defaults to PAGE_SIZE). Treating them the same would silently turn
    # `?page_size=0` into a full 25-row page.
    resolved_size = page_size if page_size is not None else PAGE_SIZE
    resolved_size = max(1, min(MAX_PAGE_SIZE, resolved_size))
    return resolved_page, resolved_size


@dataclass
class RankedPage:
    rows: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
    #: How many candidates on this job arrived after the last assessment round
    #: (workflow section 32). Counted over the WHOLE job, not this page: the
    #: point of the section is that these people are not on the page a
    #: recruiter is looking at.
    new_candidate_count: int = 0
    #: Whether anybody in the table (under the same archive filter) has an
    #: assessment overall, which decides the header sentence. Over the table
    #: rather than this page, so the sentence does not change between pages.
    has_assessed: bool = False
    #: The one line above the table, from `yukti.ranking.header_sentence`.
    ranking_header: str = ranking.HEADER_RESUME_ONLY

    @property
    def total_pages(self) -> int:
        if self.page_size <= 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def range_start(self) -> int:
        """1-indexed index of the first row on this page (0 when empty), the
        "Showing X-Y of Z" header."""
        return 0 if not self.rows else (self.page - 1) * self.page_size + 1

    @property
    def range_end(self) -> int:
        return 0 if not self.rows else self.range_start + len(self.rows) - 1


async def ranked_candidates(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    page: int | None = 1,
    page_size: int | None = PAGE_SIZE,
    include_archived: bool = False,
    profile_age_filter: str | None = None,
    arrival_filter: str | None = None,
) -> RankedPage:
    """One page of the job's candidate table, in `yukti.ranking` order.

    Archived applications are excluded by default (an archived row is not part
    of the ranking the recruiter is working through) but can be included for
    the audit view.

    `profile_age_filter` narrows to Old or New Profiles, and `arrival_filter`
    to the New Candidates section (workflow section 32) or to everything else.
    Both are validated against a module constant rather than interpolated, so
    nothing caller-supplied ever reaches the SQL text.

    The job's skills and contract digest are read ONCE for the page
    (`projection.job_skills_view`), never per row: a tag's text is the skill's
    CURRENT name, and a row is out of date when the digest moved after it was
    read.
    """
    resolved_page, resolved_size = normalize_page(page, page_size)
    offset = (resolved_page - 1) * resolved_size
    archived_filter = "" if include_archived else "AND l.archived_at IS NULL"
    age_filter = ""
    if profile_age_filter in PROFILE_AGES:
        age_filter = f"AND {_PROFILE_AGE_SQL} = '{profile_age_filter}'"
    arrival = ""
    if arrival_filter == ARRIVAL_NEW:
        arrival = f"AND ({_NEW_CANDIDATE_SQL})"
    elif arrival_filter == ARRIVAL_CONSIDERED:
        # `NOT (x)` would drop the rows where the comparison is NULL, which is
        # every row on a job that has never invited anybody -- i.e. the whole
        # table. COALESCE says what is meant: not-new includes not-yet-decided.
        arrival = f"AND NOT COALESCE({_NEW_CANDIDATE_SQL}, FALSE)"

    counts = (
        await session.execute(
            text(
                f"""
                SELECT
                    COUNT(*) FILTER (
                        WHERE TRUE {archived_filter} {age_filter} {arrival}
                    ) AS matching,
                    -- The New Candidates count deliberately ignores `arrival`
                    -- and the age filter: it answers "how many people are
                    -- waiting outside the list you are looking at", which a
                    -- count narrowed by the same filter could never do.
                    COUNT(*) FILTER (
                        WHERE COALESCE({_NEW_CANDIDATE_SQL}, FALSE) {archived_filter}
                    ) AS newly_arrived,
                    -- Anybody in the table with an assessment overall: the
                    -- header then describes the blended order, not a resume
                    -- check. Not narrowed by the page filters either, so the
                    -- sentence above the table does not change with them.
                    COUNT(*) FILTER (
                        WHERE rep.overall_score IS NOT NULL {archived_filter}
                    ) > 0 AS has_assessed
                FROM job_candidate_links l
                JOIN jobs j ON j.id = l.job_id
                LEFT JOIN functional_skills_reports rep
                       ON rep.job_candidate_link_id = l.id
                WHERE l.job_id = :job_id
                """
            ),
            {"job_id": str(job_id)},
        )
    ).one()
    total, new_candidate_count = int(counts[0]), int(counts[1])
    has_assessed = bool(counts[2])
    # The ratio belongs to the JOB's tenant, read once for the header even
    # when the table is empty.
    weight_pct = (
        await session.execute(
            text(
                "SELECT t.yukti_assessment_weight_pct FROM jobs j "
                "JOIN tenants t ON t.id = j.tenant_id WHERE j.id = :job_id"
            ),
            {"job_id": str(job_id)},
        )
    ).scalar_one()

    # Every fragment of the SQL text below is a module constant or a filter
    # chosen from one; no caller input reaches it. Values are bound.
    rows = (
        await session.execute(
            text(
                f"""
                SELECT
                    l.id                    AS link_id,
                    l.candidate_id          AS candidate_id,
                    l.profile_id            AS profile_id,
                    l.source                AS source,
                    l.status                AS status,
                    l.status_updated_at     AS status_updated_at,
                    l.application_source    AS application_source,
                    l.source_type           AS source_type,
                    l.archived_at           AS archived_at,
                    l.validation_json       AS validation,
                    -- Yukti: what the resume check holds for this row. The
                    -- pre score is read only through the rank key below.
                    l.yukti_status          AS yukti_status,
                    l.yukti_failure_reason  AS yukti_failure_reason,
                    l.evidence_tags_json    AS evidence_tags_json,
                    l.yukti_provenance_json AS yukti_provenance_json,
                    l.yukti_profile_id      AS yukti_profile_id,
                    -- The derived sort key, read ONLY to become a grade word
                    -- (`projection.ai_match_fields`); never serialized.
                    {ranking.rank_score_sql()} AS rank_score,
                    rep.overall_score       AS overall_score,
                    COALESCE(rep.must_have_failed, FALSE) AS must_have_failed,
                    -- The comparison inputs for the derived columns
                    -- (services/recruiter_columns): the job's declared CTC
                    -- range and its JD education sentence.
                    j.compensation_json     AS compensation,
                    j.jd_json->>'education' AS jd_education,
                    -- BGV Status: the three inputs bgv_workflow.derive_status
                    -- counts. candidate_employments is tenant-free by design
                    -- (0095); bgv_verifications is filtered to THIS job's
                    -- tenant, the same scope bgv_workflow.candidate_status
                    -- reads.
                    c.employment_background AS employment_background,
                    (
                        SELECT COUNT(*) FROM candidate_employments ce
                         WHERE ce.candidate_id = c.id
                    )                       AS bgv_employer_count,
                    (
                        SELECT COALESCE(array_agg(bv.status), '{{}}')
                          FROM bgv_verifications bv
                         WHERE bv.candidate_id = c.id
                           AND bv.tenant_id = j.tenant_id
                    )                       AS bgv_statuses,
                    -- The tenant, for the reference code. Selected rather than
                    -- taken from the session so the code is derived from the
                    -- row's own owner and cannot be built from a caller's
                    -- assumption about which tenant it is looking at.
                    j.tenant_id             AS tenant_id,
                    c.full_name             AS full_name,
                    c.email                 AS email,
                    c.profile_form_json     AS profile_form,
                    p.resume_url            AS resume_url,
                    p.resume_original_filename AS resume_filename,
                    p.resume_mime_type      AS resume_mime_type,
                    rep.id                  AS report_id,
                    rep.synthesized_at      AS report_ready_at,
                    -- The conversation's status, for the PRISM Report word.
                    -- METADATA ONLY: rows, never S3, never media work.
                    sess.status             AS conversation_status,
                    EXISTS (
                        SELECT 1 FROM proctoring_reports pr
                        JOIN proctoring_sessions psess
                          ON psess.id = pr.proctoring_session_id
                         WHERE psess.job_candidate_link_id = l.id
                    )                       AS has_proctoring_report,
                    EXISTS (
                        SELECT 1 FROM proctoring_sessions psess2
                         WHERE psess2.job_candidate_link_id = l.id
                    )                       AS has_proctoring_session,
                    {_PROFILE_AGE_SQL}      AS profile_age,
                    COALESCE({_NEW_CANDIDATE_SQL}, FALSE) AS is_new_candidate,
                    -- EXISTS, not a LEFT JOIN: old_profile_reviews is UNIQUE on
                    -- (link, reviewer), so joining it would duplicate a row for
                    -- every colleague who had also opened that profile and the
                    -- paginated table would silently show the same candidate
                    -- twice.
                    EXISTS (
                        SELECT 1 FROM old_profile_reviews rev
                         WHERE rev.job_candidate_link_id = l.id
                    )                       AS review_charged
                FROM job_candidate_links l
                JOIN candidates c ON c.id = l.candidate_id
                JOIN jobs j ON j.id = l.job_id
                -- The ratio is the JOB's tenant's, the same row the header
                -- reads, so a link can never be blended by another ratio.
                JOIN tenants t ON t.id = j.tenant_id
                LEFT JOIN profiles p ON p.id = l.profile_id
                LEFT JOIN functional_skills_reports rep
                       ON rep.job_candidate_link_id = l.id
                LEFT JOIN LATERAL (
                    -- `sess`, not `conv`: `_NEW_CANDIDATE_SQL` already uses
                    -- `conv` for its own scalar subquery over this table, and
                    -- two aliases one shadowing the other is a review trap.
                    SELECT ac.status
                    FROM assessment_conversations ac
                    WHERE ac.job_candidate_link_id = l.id
                    ORDER BY ac.created_at DESC, ac.id DESC
                    LIMIT 1
                ) sess ON TRUE
                WHERE l.job_id = :job_id {archived_filter} {age_filter} {arrival}
                ORDER BY {ranking.order_by_sql()}
                LIMIT :limit OFFSET :offset
                """
            ),
            {"job_id": str(job_id), "limit": resolved_size, "offset": offset},
        )
    ).mappings().all()

    view = await projection.job_skills_view(session, job_id)
    return RankedPage(
        rows=[_row_payload(row, view, job_id, weight_pct=weight_pct) for row in rows],
        total=total,
        page=resolved_page,
        page_size=resolved_size,
        new_candidate_count=new_candidate_count,
        has_assessed=has_assessed,
        ranking_header=ranking.header_sentence(
            has_assessed=has_assessed, weight_pct=weight_pct
        ),
    )


def _row_payload(
    row: Any,
    view: projection.JobSkillsView,
    job_id: Any = None,
    *,
    weight_pct: int | float,
) -> dict[str, Any]:
    """One table row: every field `schemas.ranking.RankedCandidateOut`
    declares, and nothing else (the schema forbids extras, so a key added
    here without a declaration fails the request instead of vanishing).

    Words only. `rank_score`, `overall_score` and the ratio are read by
    `projection.ai_match_fields` to become a grade word and provenance
    sentences, and are never copied onto the payload.
    """
    from app.models.candidate import SOURCE_TYPE_APPLIED, source_type_label
    from app.services import (
        bgv_workflow,
        hiring_pipeline,
        recruiter_columns,
        reference_code,
    )

    status = hiring_pipeline.normalize(row["status"])
    # The BGV word, derived once per row from the same three counts the offer
    # gate reads (bgv_workflow.candidate_status), fetched in the page query
    # rather than per candidate. `.get()` rather than indexing, the file's own
    # precedent: a caller holding a pre-existing row shape (test fixtures,
    # notably) reads the honest empty state rather than crashing.
    bgv_status = bgv_workflow.derive_status(
        background=row.get("employment_background"),
        employer_count=int(row.get("bgv_employer_count") or 0),
        statuses=list(row.get("bgv_statuses") or []),
    )
    source_type = row["source_type"] or SOURCE_TYPE_APPLIED
    validation = row.get("validation") if isinstance(row.get("validation"), dict) else None
    return {
        "link_id": row["link_id"],
        # Where the candidate came from, and where they are in the pipeline.
        "application_source": row["application_source"],
        # Type of Procurement. Presentation only: this column never changes how
        # a candidate is parsed, embedded, matched or assessed.
        "source_type": source_type,
        "source_type_label": source_type_label(source_type),
        # "Databank, not an applicant": somebody AI Matching found, or a
        # recruiter uploaded, has not asked for this job (owner ruling).
        "applicant_label": projection.applicant_label(status, source_type),
        "status": status,
        "stage_label": hiring_pipeline.STAGE_LABELS.get(status, status),
        "status_updated_at": row["status_updated_at"],
        # The MANUAL set: `shortlisted` is a real stage but is no longer offered
        # as something a recruiter picks (see hiring_pipeline).
        "allowed_transitions": sorted(hiring_pipeline.manual_transitions(status)),
        "allowed_transition_options": hiring_pipeline.transition_options(status),
        "candidate_id": row["candidate_id"],
        # COMPANY-JOB-CANDIDATE, rendered under the name in every surface that
        # shows this row. Derived, never stored, and one-way: see
        # services/reference_code.
        "reference_code": reference_code.reference_code(
            row["tenant_id"], job_id, row["candidate_id"]
        ),
        # The only handle the resume viewer and the download endpoint accept:
        # resumes live in PRIVATE object storage and every read goes through
        # /candidates/profiles/{id}/resume-file.
        "profile_id": row["profile_id"],
        "full_name": row["full_name"] or row["email"] or "Unnamed candidate",
        "email": row["email"],
        "source": row["source"],
        "archived_at": row["archived_at"],
        # A STORAGE URI MUST NOT CROSS AN API BOUNDARY. `profiles.resume_url`
        # is an `s3://bucket/key` reference a browser cannot fetch, so the row
        # carries the one thing it answers, a boolean, and never the URI.
        "has_resume": bool(row["resume_url"]),
        "resume_filename": row["resume_filename"],
        "resume_mime_type": row["resume_mime_type"],
        # The PRISM Report button is only actionable once a report exists.
        "has_report": row["report_id"] is not None,
        "report_ready_at": row["report_ready_at"],
        # Report availability words, derived server-side from row presence
        # alone. `.get()` rather than indexing: the absent-key state IS the
        # honest empty state. The mode and video words left this table in the
        # stage 3 final sweeps (see `schemas/ranking.py`).
        "prism_report_status": video_access.prism_status_word(
            has_report=row["report_id"] is not None,
            conversation_status=row.get("conversation_status"),
        ),
        "proctoring_report_status": video_access.proctoring_status_word(
            has_proctoring_report=bool(row.get("has_proctoring_report")),
            has_proctoring_session=bool(row.get("has_proctoring_session")),
        ),
        # Old Profile / New Profile. Presentation and billing only.
        "profile_age": row["profile_age"],
        "profile_age_label": PROFILE_AGE_LABELS.get(row["profile_age"], ""),
        # New Candidate (workflow section 32): arrived after the last
        # assessment round on this job. Presentation only.
        "is_new_candidate": bool(row["is_new_candidate"]),
        "review_charged": bool(row["review_charged"]),
        # The application answers and the profile questionnaire as an explicit
        # Q&A, paired SERVER-side against the field lists. Never rated.
        "validation_answers": validation_answers(row["validation"], row["profile_form"]),
        # The recruiter columns: derived words, never stored, all from
        # services/recruiter_columns. None means "Not stated". Declared on the
        # response schema since the Vivekium release: before it these were
        # computed here and dropped by pydantic, so they never reached a
        # browser (PLAN-p2 NF-1).
        "ctc_match_label": recruiter_columns.ctc_match(
            validation.get("expected_ctc") if validation else None,
            row.get("compensation"),
        ),
        "notice_period_label": recruiter_columns.notice_period_bucket(
            validation.get("notice_period") if validation else None,
        ),
        "education_match_label": recruiter_columns.education_match(
            row.get("profile_form"), row.get("jd_education")
        ),
        "bgv_status": bgv_status,
        "bgv_status_label": recruiter_columns.bgv_status_word(bgv_status),
        # AI Match: a grade word, evidence tags and provenance sentences.
        **projection.ai_match_fields(row, view, weight_pct=weight_pct),
    }


def validation_answers(
    submitted: Any, profile_form: Any = None
) -> list[dict[str, Any]]:
    """The mandatory application fields, THEN the full profile questionnaire,
    as (question, answer) pairs.

    Built from `application_validation.VALIDATION_FIELDS` and
    `candidate_profile_form.FORM_SECTIONS`, the same lists the apply form and
    the profile form render, so a field added to either appears here without a
    second edit. An application/profile submitted before a field existed has
    no value for it and renders as unanswered rather than being hidden: "they
    were never asked" and "they did not answer" look identical when a row is
    simply missing, and only one of those is the candidate's doing.
    """
    from app.services.application_validation import VALIDATION_FIELDS
    from app.services.candidate_profile_form import profile_form_answers

    values = submitted if isinstance(submitted, dict) else {}
    application_answers = [
        {
            "key": field["key"],
            "question": field["label"],
            "answer": (str(values.get(field["key"])).strip() or None)
            if values.get(field["key"]) is not None
            else None,
            "group": "Application",
        }
        for field in VALIDATION_FIELDS
    ]
    return application_answers + profile_form_answers(profile_form)
