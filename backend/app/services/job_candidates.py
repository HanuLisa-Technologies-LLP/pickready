"""Ranked candidate list for the job detail page (2026-07-27 spec §2).

The candidate table lives INLINE on the job page — there is no separate review
screen — so this service owns the one thing that page cannot do for itself:
deciding the order.

WHY THE SORT IS SERVER-SIDE
---------------------------
The table is paginated at 25 rows. If the browser sorted each page, a candidate
could appear on two pages (or on none) as soon as scores changed between
requests, because page 2 would be cut from a differently-ordered list than page
1. Ordering therefore happens once, in SQL, with a total order — including an
explicit tiebreak — so page boundaries are stable.

THE GRADE-DRIVEN ORDER (spec §2.3)
----------------------------------
  non_managerial : skills -> experience -> behavioural
  everything else: skills -> behavioural -> experience

The reasoning is the spec's: for an individual contributor, demonstrated
experience separates two candidates with similar skills; at managerial grade
and above, how someone works with people matters more than another year of it.

THE THREE SORT KEYS
-------------------
`skills` and `experience` come from `job_candidate_links.match_breakdown_json`,
written by the matching pipeline. The behavioural key is NOT stored on the link
— it is the mean of the report's PPI Behavioural Competency scores, so it is
derived here from `report_dimensions`. A candidate with no report yet sorts
last on that key rather than being dropped: retrieval and scoring state must
never decide who is *visible* (claude.md — every linked candidate is scored,
and every linked candidate is listed).

NUMBERS NEVER LEAVE THIS MODULE. The scores above are ORDER BY inputs only; the
payload this service returns carries word labels and comments exclusively.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.matching import ranking_payload

#: Spec §2.4 — 25 rows per page.
PAGE_SIZE = 25

#: Maximum page size a caller may request. Bounded so a hand-crafted
#: `?page_size=100000` cannot turn one request into a full-table scan.
MAX_PAGE_SIZE = 100

#: SQL fragments for the three sort keys. Kept as named pieces so
#: `order_by_clause` composes them rather than concatenating raw strings from
#: caller input — nothing user-supplied ever reaches the ORDER BY.
_SORT_KEYS: dict[str, str] = {
    "skills": "(l.match_breakdown_json->'skills_match'->>'score')::float",
    "experience": "(l.match_breakdown_json->'experience_relevance'->>'score')::float",
    "behavioural": "pfi.pfi_score",
}

#: Grade -> ordered sort-key names.
_GRADE_SORT_ORDER: dict[str, tuple[str, ...]] = {
    "non_managerial": ("skills", "experience", "behavioural"),
    "managerial": ("skills", "behavioural", "experience"),
    "leadership": ("skills", "behavioural", "experience"),
    "cxo": ("skills", "behavioural", "experience"),
}

_DEFAULT_SORT = _GRADE_SORT_ORDER["managerial"]

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


def profile_age(link_created_at, posting_start) -> str:
    """Pure counterpart of `_PROFILE_AGE_SQL`, for callers holding the values.

    The two MUST agree — a row the SQL calls old and this function calls new
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


#: Human labels for the Level column. Never shown as a raw enum value.
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


def sort_keys_for_grade(grade: str | None) -> tuple[str, ...]:
    """The ordered sort-key names for `grade`. Pure; unit-tested directly."""
    return _GRADE_SORT_ORDER.get(grade or "non_managerial", _DEFAULT_SORT)


def order_by_clause(grade: str | None) -> str:
    """Build the ORDER BY body for `grade`.

    Every key is DESC NULLS LAST so an unscored candidate sinks rather than
    floating to the top on a NULL. The trailing `l.created_at, l.id` is what
    makes the order TOTAL: without it, two candidates with identical scores
    could swap places between page 1 and page 2 and one of them would vanish
    from the paginated result.
    """
    parts = [f"{_SORT_KEYS[key]} DESC NULLS LAST" for key in sort_keys_for_grade(grade)]
    parts.extend(["l.created_at ASC", "l.id ASC"])
    return ", ".join(parts)


def normalize_page(page: int | None, page_size: int | None) -> tuple[int, int]:
    """Coerce caller pagination into a safe (page, page_size).

    Pages are 1-INDEXED (spec §2.4 asks for one convention, consistently
    applied; 1-indexed is what the UI shows, so the API speaks the same
    language). Anything below 1, or a page size outside 1..MAX_PAGE_SIZE, is
    clamped rather than rejected — a bad page number should not 422 a table.
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
        """1-indexed index of the first row on this page (0 when empty) — the
        "Showing X-Y of Z" header."""
        return 0 if not self.rows else (self.page - 1) * self.page_size + 1

    @property
    def range_end(self) -> int:
        return 0 if not self.rows else self.range_start + len(self.rows) - 1


# The behavioural key: mean PPI Behavioural Competency score per link. Computed
# in a CTE so a candidate with no report LEFT JOINs to NULL and sorts last,
# instead of being filtered out of the table entirely.
#
# `behavioural` is the PPI category (2026-07-30). `behavioral` is the retired
# PFI spelling and is still matched, because reports written before the PPI
# release carry it and would otherwise silently sort to the bottom of every
# list as though the candidate had never been assessed.
_PFI_CTE = """
    WITH pfi AS (
        SELECT r.job_candidate_link_id AS link_id,
               AVG(d.score)::float     AS pfi_score
        FROM functional_skills_reports r
        JOIN report_dimensions d
          ON d.report_id = r.id AND d.category IN ('behavioural', 'behavioral')
        GROUP BY r.job_candidate_link_id
    )
"""


async def ranked_candidates(
    session: AsyncSession,
    job_id: uuid.UUID,
    grade: str | None,
    *,
    page: int | None = 1,
    page_size: int | None = PAGE_SIZE,
    include_archived: bool = False,
    profile_age_filter: str | None = None,
) -> RankedPage:
    """One page of the job's candidate table, ordered per `grade`.

    Archived applications are excluded by default — an archived row is not part
    of the ranking the recruiter is working through — but can be included for
    the audit view.

    `profile_age_filter` narrows to Old or New Profiles. It is validated against
    PROFILE_AGES here rather than interpolated, so nothing caller-supplied ever
    reaches the SQL text.
    """
    resolved_page, resolved_size = normalize_page(page, page_size)
    offset = (resolved_page - 1) * resolved_size
    archived_filter = "" if include_archived else "AND l.archived_at IS NULL"
    age_filter = ""
    if profile_age_filter in PROFILE_AGES:
        age_filter = f"AND {_PROFILE_AGE_SQL} = '{profile_age_filter}'"

    total = (
        await session.execute(
            text(
                f"""
                SELECT COUNT(*) FROM job_candidate_links l
                JOIN jobs j ON j.id = l.job_id
                WHERE l.job_id = :job_id {archived_filter} {age_filter}
                """
            ),
            {"job_id": str(job_id)},
        )
    ).scalar_one()

    # `order_by_clause` is composed only from module constants keyed by the
    # job's own grade — no caller input reaches the SQL text. Values are bound.
    rows = (
        await session.execute(
            text(
                f"""
                {_PFI_CTE}
                SELECT
                    l.id                    AS link_id,
                    l.candidate_id          AS candidate_id,
                    l.profile_id            AS profile_id,
                    l.source                AS source,
                    l.tier                  AS tier,
                    l.status                AS status,
                    l.status_updated_at     AS status_updated_at,
                    l.application_source    AS application_source,
                    l.source_type           AS source_type,
                    l.archived_at           AS archived_at,
                    l.match_breakdown_json  AS breakdown,
                    l.validation_json       AS validation,
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
                    {_PROFILE_AGE_SQL}      AS profile_age,
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
                LEFT JOIN profiles p ON p.id = l.profile_id
                LEFT JOIN pfi ON pfi.link_id = l.id
                LEFT JOIN functional_skills_reports rep
                       ON rep.job_candidate_link_id = l.id
                WHERE l.job_id = :job_id {archived_filter} {age_filter}
                ORDER BY {order_by_clause(grade)}
                LIMIT :limit OFFSET :offset
                """
            ),
            {"job_id": str(job_id), "limit": resolved_size, "offset": offset},
        )
    ).mappings().all()

    level = grade_label(grade)
    return RankedPage(
        rows=[_row_payload(row, level, job_id) for row in rows],
        total=int(total),
        page=resolved_page,
        page_size=resolved_size,
    )


def _row_payload(row: Any, level: str, job_id: Any = None) -> dict[str, Any]:
    """One table row. Carries the five comments and word labels — never a score.

    `ranking_payload` already re-enforces the 25-30 word contract on the way
    out and reports `ranking_status = "not_scored"` for a link the matching
    pipeline has not reached yet, so the UI can distinguish "no comment" from
    "not scored" instead of rendering a silent blank.
    """
    from app.models.candidate import SOURCE_TYPE_APPLIED, source_type_label
    from app.services import hiring_pipeline, reference_code

    status = hiring_pipeline.normalize(row["status"])
    source_type = row["source_type"] or SOURCE_TYPE_APPLIED
    return {
        "link_id": row["link_id"],
        # Where the candidate came from, and where they are in the pipeline.
        "application_source": row["application_source"],
        # Type of Procurement. Presentation only: this column never changes how
        # a candidate is parsed, embedded, matched or assessed.
        "source_type": source_type,
        "source_type_label": source_type_label(source_type),
        "status": status,
        "stage_label": hiring_pipeline.STAGE_LABELS.get(status, status),
        "status_updated_at": row["status_updated_at"],
        # The MANUAL set: `shortlisted` is a real stage but is no longer offered
        # as something a recruiter picks (see hiring_pipeline).
        "allowed_transitions": sorted(hiring_pipeline.manual_transitions(status)),
        "allowed_transition_options": hiring_pipeline.transition_options(status),
        "candidate_id": row["candidate_id"],
        # COMPANY-JOB-CANDIDATE, rendered under the name in every surface that
        # shows this row. One stable handle for "this application", because a
        # name is not unique and a UUID is not something a person can carry
        # between a screen, an email and a phone call. Derived, never stored,
        # and one-way: see services/reference_code.
        "reference_code": reference_code.reference_code(
            row["tenant_id"], job_id, row["candidate_id"]
        ),
        # The profile is what the resume viewer and the download endpoint are
        # keyed on. It was SELECTed and then dropped here, which is the whole of
        # the "resumes cannot be viewed or downloaded" report: resumes moved to
        # PRIVATE OBJECT STORAGE on 2026-08-0x, so `resume_url` is an `s3://`
        # object reference a browser cannot fetch (it was `gs://` before the AWS
        # migration) and every read now goes through
        # /candidates/profiles/{id}/resume-file. Without this id the viewer had
        # nothing to ask for and fell through to its "missing its secure profile
        # reference" panel, with the Download button pointing at an unfetchable
        # object reference.
        "profile_id": row["profile_id"],
        "full_name": row["full_name"] or row["email"] or "Unnamed candidate",
        "email": row["email"],
        "level": level,
        "source": row["source"],
        "tier": row["tier"],
        "archived_at": row["archived_at"],
        "resume_url": row["resume_url"],
        "resume_filename": row["resume_filename"],
        "resume_mime_type": row["resume_mime_type"],
        # The PPI Report button is only actionable once a report exists.
        "has_report": row["report_id"] is not None,
        "report_ready_at": row["report_ready_at"],
        # Old Profile / New Profile. Presentation and billing only: an Old
        # Profile is ranked, listed and openable exactly like a new one.
        "profile_age": row["profile_age"],
        "profile_age_label": PROFILE_AGE_LABELS.get(row["profile_age"], ""),
        "review_charged": bool(row["review_charged"]),
        # The validation questionnaire, as an explicit Q&A the recruiter can
        # read on the row (spec §29). Paired SERVER-side against the field list
        # so the questions and the answers cannot drift, and shown exactly as
        # submitted: nothing scores, interprets or judges this data, and the
        # recruiter decides whether stated interest is genuine (spec §14).
        #
        # Two sources, both server-assembled so they can never drift from their
        # form definitions: the six mandatory APPLICATION fields, then the
        # full 38-item candidate PROFILE questionnaire (2026-08-16 report — the
        # column was showing only the application's six fields, when every one
        # of the 38 profile answers a candidate fills in once and reuses across
        # every job must be visible here too).
        "validation_answers": validation_answers(row["validation"], row["profile_form"]),
        **ranking_payload(row["breakdown"]),
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
