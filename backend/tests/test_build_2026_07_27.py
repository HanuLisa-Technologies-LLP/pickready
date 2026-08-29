"""Unit tests for the 2026-07-27 build spec.

Covers the pure, decision-making logic added by this release — the parts where
a wrong answer is silent rather than loud:

  * LLM task-type routing, round-robin balancing, and the graph's retry edge
  * the grade-driven candidate sort and its pagination guarantees
  * the six-month retake boundary
  * the per-user permission overlay
  * the matching word-label projection (the "no numbers" boundary)
  * per-job JD section resolution (override vs inherited)
  * radar band geometry

Everything here is DB-free and deterministic.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


# ── LLM router: task-type routing ────────────────────────────────────────────
#
# THIS SECTION SHRANK ON 2026-08-28, and what it lost is worth naming so a
# reader does not go looking for a deleted guarantee.
#
# It used to assert that every task named a chain of three providers, that
# `rotate_within_provider` cycled the keys inside a tier, that `_build_chain`
# ordered tiers before balancing within them, and that
# `probe_each_provider_first` could reach every tier inside the retry budget.
# Every one of those described machinery for routing around three unreliable
# free tiers, and spec-doc5 Part B removed the machinery along with the tiers.
#
# What survives here is the half that was never about routing: a task type must
# resolve, and a typo must fail loudly rather than silently picking something.
# The graph's retry edge moved to `tests/test_llm_router.py`, where the rest of
# the single-vendor loop is exercised, and the model assignment lives in
# `tests/test_llm_task_routing.py`.

from app.config import llm_providers as providers
from app.services import llm_router


def test_every_spec_task_type_resolves_to_a_model() -> None:
    """The five spec task types plus the two legacy hints must all route."""
    for task in (
        "jd_generation",
        "technical_questions",
        "behavioral_assessment",
        "report_synthesis",
        "email_composition",
        "rerank",
        "extraction",
    ):
        assert providers.model_for(task) in providers.ALLOWED_MODELS, task
        assert providers.provider_order(task) == [providers.PROVIDER], task


def test_unknown_task_type_raises_rather_than_guessing() -> None:
    with pytest.raises(ValueError, match="Unknown LLM task_type"):
        providers.provider_order("summarise_the_vibes")


def test_account_level_failures_are_distinguished_from_rate_limits() -> None:
    """A revoked credential and a burst are different problems.

    One is fixed by an operator and never by waiting; the other is fixed by
    waiting and never by an operator. Treating them alike means either burning
    the retry budget on a dead key or condemning a healthy one over a burst.
    """
    import httpx

    def _err(status: int) -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        return httpx.HTTPStatusError(
            "x", request=request, response=httpx.Response(status, request=request)
        )

    for status in (401, 403):
        assert llm_router.is_account_level_failure(_err(status)) is True
    assert llm_router.is_account_level_failure(_err(429)) is False
    assert llm_router.is_account_level_failure(_err(500)) is False
    assert llm_router.is_account_level_failure(ValueError("not http")) is False


# ── Grade-driven candidate sort (spec §2.3) ──────────────────────────────────

from app.services import job_candidates as jc


def test_non_managerial_sorts_experience_above_behavioural() -> None:
    assert jc.sort_keys_for_grade("non_managerial") == (
        "skills", "experience", "behavioural",
    )


@pytest.mark.parametrize("grade", ["managerial", "leadership", "cxo"])
def test_managerial_and_above_sort_behavioural_above_experience(grade: str) -> None:
    assert jc.sort_keys_for_grade(grade) == ("skills", "behavioural", "experience")


def test_unknown_or_missing_grade_falls_back_without_raising() -> None:
    assert jc.sort_keys_for_grade(None) == jc.sort_keys_for_grade("non_managerial")
    assert jc.sort_keys_for_grade("archduke") == jc.sort_keys_for_grade("managerial")


def test_order_by_is_a_total_order() -> None:
    """Without the id tiebreak, two equally-scored candidates could swap places
    between page 1 and page 2 and one of them would vanish from the results."""
    clause = jc.order_by_clause("non_managerial")
    assert clause.endswith("l.created_at ASC, l.id ASC")
    # Every score key sinks NULLs, so an unscored candidate never floats up.
    assert clause.count("DESC NULLS LAST") == 3


def test_order_by_reflects_the_grade() -> None:
    non_mgr = jc.order_by_clause("non_managerial")
    mgr = jc.order_by_clause("cxo")
    assert non_mgr.index("experience_relevance") < non_mgr.index("pfi.pfi_score")
    assert mgr.index("pfi.pfi_score") < mgr.index("experience_relevance")


def test_normalize_page_clamps_instead_of_rejecting() -> None:
    """A bad page number should not 422 a table."""
    assert jc.normalize_page(None, None) == (1, jc.PAGE_SIZE)
    assert jc.normalize_page(0, 25) == (1, 25)
    assert jc.normalize_page(-5, 25) == (1, 25)
    assert jc.normalize_page(3, 10) == (3, 10)
    # A hand-crafted huge page_size cannot turn one request into a table scan.
    assert jc.normalize_page(1, 100_000) == (1, jc.MAX_PAGE_SIZE)
    assert jc.normalize_page(1, 0) == (1, 1)


def test_page_size_is_25_per_the_spec() -> None:
    assert jc.PAGE_SIZE == 25


def _page(total: int, page: int, rows: int) -> jc.RankedPage:
    return jc.RankedPage(rows=[{}] * rows, total=total, page=page, page_size=25)


def test_ranked_page_reports_showing_x_to_y_of_z() -> None:
    assert (_page(33, 1, 25).range_start, _page(33, 1, 25).range_end) == (1, 25)
    assert (_page(33, 2, 8).range_start, _page(33, 2, 8).range_end) == (26, 33)
    assert _page(33, 1, 25).total_pages == 2
    assert _page(33, 2, 8).has_next is False
    assert _page(33, 2, 8).has_previous is True


def test_empty_page_reports_a_zero_range_not_a_phantom_row() -> None:
    empty = _page(0, 1, 0)
    assert (empty.range_start, empty.range_end, empty.total_pages) == (0, 0, 0)
    assert empty.has_next is False


def test_grade_label_never_shows_a_raw_enum() -> None:
    assert jc.grade_label("cxo") == "CXO"
    assert jc.grade_label("non_managerial") == "Non-managerial"
    assert jc.grade_label(None) == "Non-managerial"
    assert "_" not in jc.grade_label("leadership")


# ── Six-month retake rule (spec §5.1) ────────────────────────────────────────

from app.services import retake

_NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def test_no_prior_assessment_is_a_first_assessment() -> None:
    assert retake.classify_age(None, _NOW) == (retake.DECISION_FIRST_ASSESSMENT, None)


@pytest.mark.parametrize("days", [0, 1, 90, 182])
def test_recent_assessment_is_reused(days: int) -> None:
    decision, age = retake.classify_age(_NOW - timedelta(days=days), _NOW)
    assert decision == retake.DECISION_REUSE
    assert age == days


def test_the_boundary_day_is_a_retake_not_a_reuse() -> None:
    """Exactly six months old must NOT be reused — the window is the
    strictly-less-than side, so the rule never quietly extends itself."""
    assert retake.classify_age(
        _NOW - timedelta(days=retake.RETAKE_WINDOW_DAYS), _NOW
    )[0] == retake.DECISION_RETAKE
    assert retake.classify_age(
        _NOW - timedelta(days=retake.RETAKE_WINDOW_DAYS - 1), _NOW
    )[0] == retake.DECISION_REUSE


def test_naive_timestamps_are_read_as_utc() -> None:
    """A stored value with no tzinfo is UTC in this database; reading it as
    local time would shift the boundary by hours."""
    naive = (_NOW - timedelta(days=10)).replace(tzinfo=None)
    assert retake.classify_age(naive, _NOW) == (retake.DECISION_REUSE, 10)


def test_a_future_timestamp_is_not_treated_as_evidence_of_recency() -> None:
    decision, age = retake.classify_age(_NOW + timedelta(days=30), _NOW)
    assert (decision, age) == (retake.DECISION_REUSE, 0)


def test_nothing_travels_between_jobs_under_ppi() -> None:
    """Every section of a report is scoped to the job it was written for.

    Skills-vs-JD always was. Since 2026-07-30 the PPI framework is generated
    from each job's own JD, so Primary Skills, Secondary Skills, Behavioural
    Competencies and the technical bank are all job-scoped too. Carrying any of
    them onto another job would assert a grade against criteria the candidate
    was never assessed on.
    """
    assert retake.PORTABLE_CATEGORIES == frozenset()


def test_retake_decision_explains_itself_to_the_candidate() -> None:
    reuse = retake.RetakeDecision(decision=retake.DECISION_REUSE, age_days=30)
    # Reuse is retired: a recent assessment is acknowledged, but the candidate
    # still answers this role's own questions and is told why.
    assert "written for each specific role" in (reuse.message() or "")
    assert reuse.requires_new_assessment is True

    redo = retake.RetakeDecision(decision=retake.DECISION_RETAKE, age_days=400)
    assert "fresh one" in (redo.message() or "")
    assert redo.requires_new_assessment is True

    # A first assessment needs no preamble.
    first = retake.RetakeDecision(decision=retake.DECISION_FIRST_ASSESSMENT)
    assert first.message() is None
    assert first.requires_new_assessment is True


# ── Per-user permission overlay (spec §7.1) ──────────────────────────────────

from app.services import rbac
from app.services import capabilities as caps


def test_user_override_beats_the_tenant_row_in_both_directions() -> None:
    assert rbac.resolve_permission({"create_job": True}, {}, "create_job", {"create_job": False}) is False
    assert rbac.resolve_permission({}, {"create_job": False}, "create_job", {"create_job": True}) is True


def test_a_sparse_overlay_lets_untouched_capabilities_track_the_role() -> None:
    """This is the whole point of a sparse overlay: a later change to the role
    matrix must still reach everyone the HR Head did not explicitly pin."""
    overlay = {"publish_job": False}
    assert rbac.resolve_permission({}, {"create_job": True}, "create_job", overlay) is True
    assert rbac.resolve_permission({}, {"publish_job": True}, "publish_job", overlay) is False


def test_no_overlay_behaves_exactly_as_before() -> None:
    for overlay in (None, {}):
        assert rbac.resolve_permission({}, {"create_job": True}, "create_job", overlay) is True
        assert rbac.resolve_permission({}, {}, "create_job", overlay) is False


def test_sanitize_drops_unknown_capabilities() -> None:
    """A typo must never sit in the database looking like a grant."""
    cleaned = rbac.sanitize_overrides(
        {"publish_job": True, "pubish_job": True, "not_a_capability": False}
    )
    assert cleaned == {"publish_job": True}


def test_sanitize_coerces_json_ish_values() -> None:
    cleaned = rbac.sanitize_overrides(
        {"create_job": "true", "publish_job": "false", "view_dashboard": 0}
    )
    assert cleaned == {"create_job": True, "publish_job": False, "view_dashboard": False}


def test_sanitize_tolerates_a_non_object() -> None:
    for junk in (None, [], "manage_staff", 7):
        assert rbac.sanitize_overrides(junk) == {}


def test_new_spec_capabilities_are_registered_and_granted() -> None:
    assert caps.EDIT_COMPANY_PROFILE in caps.ALL_CAPABILITIES
    assert caps.PUBLISH_JOB in caps.ALL_CAPABILITIES
    from app.models.enums import Role

    for role in (Role.hr_manager, Role.recruiter, Role.hiring_manager):
        matrix = caps.DEFAULT_PERMISSION_MATRIX[role]
        assert matrix[caps.PUBLISH_JOB] is True
        assert matrix[caps.EDIT_COMPANY_PROFILE] is True


def test_capability_set_resolution_applies_the_overlay() -> None:
    resolved = rbac.resolve_capability_set(
        tenant_rows={},
        global_rows={"create_job": True, "publish_job": True},
        capabilities=["create_job", "publish_job", "manage_staff"],
        user_overrides={"publish_job": False, "manage_staff": True},
    )
    assert resolved == ["create_job", "manage_staff"]


# ── Matching word labels: the "no numbers" boundary (spec §2.2) ──────────────

from app.services import matching


def test_matching_label_bands_are_inclusive_upward() -> None:
    """claude.md rule 8: a score landing exactly on a boundary takes the
    HIGHER band. Four grades since 2026-07-30 (spec §10.2)."""
    assert matching.matching_label(9.0) == "Highly Matching"      # 90
    assert matching.matching_label(7.5) == "Matching"             # 75
    assert matching.matching_label(6.0) == "Moderately Matching"  # 60
    assert matching.matching_label(5.9) == "Not Matching"
    assert matching.matching_label(4.0) == "Not Matching"
    assert matching.matching_label(1) == "Not Matching"
    assert matching.matching_label(10) == "Highly Matching"


def test_matching_label_is_none_for_no_score() -> None:
    assert matching.matching_label(None) is None
    assert matching.matching_label(True) is None      # bool is not a score
    assert matching.matching_label("high") is None


def test_ranking_payload_publishes_labels_and_never_a_score() -> None:
    breakdown = {
        "skills_match": {"score": 9, "comment": "c " * 26},
        "experience_relevance": {"score": 6, "comment": "c " * 26},
        "role_alignment": {"score": 4, "comment": "c " * 26},
        "education_fit": {"score": 10, "comment": "c " * 26},
        "overall": {"score": 7.6, "comment": "c " * 26},
    }
    payload = matching.ranking_payload(breakdown)
    assert payload["ranking_status"] == "ready"
    assert payload["skills_match_label"] == "Highly Matching"
    assert payload["role_alignment_label"] == "Not Matching"
    assert payload["overall_label"] == "Matching"
    # The boundary: no numeric score reaches the client.
    assert not any("score" in key for key in payload)
    assert all(not isinstance(v, (int, float)) for v in payload.values())


def test_unscored_link_is_an_explicit_state_not_a_silent_blank() -> None:
    payload = matching.ranking_payload(None)
    assert payload["ranking_status"] == "not_scored"
    for key in matching.RANKING_COMMENT_KEYS.values():
        assert payload[key] is None
    for key in matching.RANKING_LABEL_KEYS.values():
        assert payload[key] is None


# ── Per-job JD sections (spec §3.1/§3.2) ─────────────────────────────────────

from app.api.jobs import resolve_jd_sections


def _job(**kw):
    return SimpleNamespace(**{"about_company": None, "work_life": None, "benefits": None, **kw})


def test_a_job_with_no_override_reads_through_to_the_company() -> None:
    """A job created before migration 0016 has all three NULL — it must keep
    rendering the company's text rather than going blank."""
    company = {"about_company": "We build X", "work_life": "Remote", "benefits": "Health"}
    resolved, overridden = resolve_jd_sections(_job(), company)
    assert resolved == company
    assert overridden == []


def test_a_job_override_wins_and_is_reported_as_such() -> None:
    company = {"about_company": "We build X", "work_life": "Remote", "benefits": "Health"}
    job = _job(about_company="This team builds Y")
    resolved, overridden = resolve_jd_sections(job, company)
    assert resolved["about_company"] == "This team builds Y"
    assert resolved["work_life"] == "Remote"       # still inherited
    assert overridden == ["about_company"]


def test_a_whitespace_only_override_reads_through_rather_than_rendering_blank() -> None:
    company = {"about_company": "We build X", "work_life": None, "benefits": None}
    resolved, overridden = resolve_jd_sections(_job(about_company="   "), company)
    assert resolved["about_company"] == "We build X"
    assert overridden == []


def test_sections_are_none_when_neither_layer_has_text() -> None:
    resolved, overridden = resolve_jd_sections(
        _job(), {"about_company": None, "work_life": None, "benefits": None}
    )
    assert set(resolved.values()) == {None}
    assert overridden == []


# ── Radar geometry (spec §10.4) ──────────────────────────────────────────────

from app.services.functional_assessment import (
    RADAR_BANDS,
    RADAR_SERIES,
    band_index_for,
    build_radar_charts,
)
from app.services import ppi as ppi_service


def test_band_index_runs_innermost_to_outermost() -> None:
    assert band_index_for("Not Matching") == 1
    assert band_index_for("Highly Matching") == len(RADAR_BANDS)
    assert band_index_for("Moderately Matching") == 2


def test_an_unknown_band_lands_innermost_rather_than_raising() -> None:
    """A report written by an older build must still draw."""
    assert band_index_for("Very High") == 1     # the retired PFI scale
    assert band_index_for("") == 1


def _radar_rows():
    return [
        {"category": ppi_service.CATEGORY_MUST_HAVE, "name": f"Primary {i}",
         "score": 90 - i * 10, "required_level": 95, "ordinal": i + 1}
        for i in range(5)
    ] + [
        {"category": ppi_service.CATEGORY_NICE_TO_HAVE, "name": f"Secondary {i}",
         "score": 70, "required_level": 67, "ordinal": i + 1}
        for i in range(5)
    ] + [
        {"category": ppi_service.CATEGORY_BEHAVIOURAL, "name": f"Behaviour {i}",
         "score": 80, "required_level": 82, "ordinal": i + 1}
        for i in range(5)
    ]


def test_every_framework_entry_is_plotted() -> None:
    """Dropping one would make two candidates' charts incomparable, which is
    the one property the per-job framework exists to guarantee."""
    charts = {chart["key"]: chart for chart in build_radar_charts(_radar_rows())}
    for category in ppi_service.CATEGORIES:
        assert len(charts[category]["axes"]) == 5
    assert len(charts["overall"]["axes"]) == 3


def test_each_axis_carries_both_shapes_as_words_never_a_score() -> None:
    for chart in build_radar_charts(_radar_rows()):
        for axis in chart["axes"]:
            assert axis["requirement_band"] in RADAR_BANDS
            assert axis["candidate_band"] in RADAR_BANDS
            assert "score" not in axis


def test_the_legend_names_the_two_shapes_by_word() -> None:
    assert RADAR_SERIES == ("Job Requirement", "Candidate Assessment")


# ── Lifecycle email drafting (spec §6) ───────────────────────────────────────

from app.services import lifecycle_email
from app.models.email_log import EMAIL_TYPES, EMAIL_TYPE_PROMPTS
from app import prompts


def test_every_email_type_has_a_prompt_file() -> None:
    available = set(prompts.available())
    for email_type in EMAIL_TYPES:
        assert EMAIL_TYPE_PROMPTS[email_type] in available, email_type


def test_every_prompt_renders_with_only_its_declared_defaults() -> None:
    """A placeholder the caller never supplies would otherwise ship a literal
    "{next_steps}" to a candidate's inbox."""
    import re

    # `{{ }}` in a prompt is an escaped literal brace (the JSON output example)
    # and legitimately survives .format() as `{ }`. What must NOT survive is an
    # unfilled PLACEHOLDER — `{identifier}`.
    placeholder = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
    base = {"candidate_name": "A", "job_title": "B", "company_name": "C"}
    for email_type in EMAIL_TYPES:
        ctx = {**lifecycle_email._PROMPT_DEFAULTS.get(email_type, {}), **base}
        rendered = prompts.render(EMAIL_TYPE_PROMPTS[email_type], **ctx)
        assert not placeholder.search(rendered), email_type


def test_parse_draft_accepts_plain_json() -> None:
    assert lifecycle_email.parse_draft('{"subject": "Hi", "body": "There"}') == (
        "Hi", "There",
    )


def test_parse_draft_tolerates_a_code_fence() -> None:
    """A model that wraps valid JSON has still produced usable copy — throwing
    it away to fall back to a template would be discarding the better email."""
    raw = '```json\n{"subject": "Hi", "body": "There"}\n```'
    assert lifecycle_email.parse_draft(raw) == ("Hi", "There")


def test_parse_draft_strips_newlines_from_the_subject() -> None:
    """A newline in a subject is header injection."""
    subject, _ = lifecycle_email.parse_draft(
        '{"subject": "Hi\\nBcc: evil@example.com", "body": "x"}'
    )
    assert "\n" not in subject
    assert subject == "Hi Bcc: evil@example.com"


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "not json", "[]", '{"subject": ""}', '{"body": "no subject"}',
     '{"subject": "s", "body": "   "}'],
)
def test_parse_draft_rejects_unusable_responses(raw: str) -> None:
    assert lifecycle_email.parse_draft(raw) is None


def test_fallback_draft_exists_for_every_type_and_names_the_person() -> None:
    ctx = {"candidate_name": "Priya", "job_title": "AI Engineer", "company_name": "Acme"}
    for email_type in EMAIL_TYPES:
        subject, body = lifecycle_email.fallback_draft(email_type, ctx)
        assert subject and body
        assert "{" not in subject and "{" not in body
        if email_type != "question_bank_reminder":
            assert "Priya" in body           # never "Dear Applicant"
            assert "AI Engineer" in body


def test_unknown_email_type_is_rejected() -> None:
    with pytest.raises(lifecycle_email.UnknownEmailType):
        lifecycle_email.validate_email_type("please_reconsider")


def test_to_html_escapes_interpolated_content() -> None:
    """A model that emits stray markup — or a recruiter who pastes some — must
    never inject unescaped HTML into a candidate's inbox."""
    html = lifecycle_email.to_html('<script>alert("x")</script>\n\nSecond para')
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert html.count("<p ") == 2
