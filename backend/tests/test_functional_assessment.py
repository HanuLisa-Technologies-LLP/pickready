"""Assessment-engine contracts: question counts, banks, scoring, word limits."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services import functional_assessment as fa
from app.services import ppi
from app.services import technical_interview as ti
from app.services.functional_assessment import (
    PPI_QUESTION_COUNTS,
    TECHNICAL_QUESTION_COUNTS,
    _fallback_remark_25,
    _fallback_remark_45,
    _unanswered_remark,
    answers_by_key,
    assessment_graph,
    build_radar_charts,
    rating_label,
    technical_question_count,
    word_count,
)

GRADES = ("non_managerial", "managerial", "leadership", "cxo")


# ── Question counts ──────────────────────────────────────────────────────────

def test_technical_counts_follow_the_grade_table() -> None:
    assert TECHNICAL_QUESTION_COUNTS == {
        "non_managerial": 20,
        "managerial": 17,
        "leadership": 15,
        "cxo": 12,
    }
    assert technical_question_count(None) == 20
    assert technical_question_count("cxo") == 12


def test_ppi_counts_follow_the_grade_table() -> None:
    """Spec §6.1. Note the direction: MORE questions for a junior candidate."""
    assert PPI_QUESTION_COUNTS == {
        "non_managerial": 25,
        "managerial": 20,
        "leadership": 15,
        "cxo": 10,
    }
    assert ppi.ppi_question_count(None) == 25
    assert ppi.ppi_question_count("cxo") == 10


@pytest.mark.parametrize("grade,tech", list(TECHNICAL_QUESTION_COUNTS.items()))
def test_blended_conversation_total(grade, tech) -> None:
    """One conversation = this candidate's technical slots + their PPI set."""
    total = tech + PPI_QUESTION_COUNTS[grade]
    assert total == tech + PPI_QUESTION_COUNTS[grade]
    if grade == "non_managerial":
        assert total == 45
    if grade == "cxo":
        assert total == 22


def test_the_retired_banks_are_gone() -> None:
    """Regression guard: PFI's fixed dimension set and the 40-question
    validation bank are both retired. Re-importing either would silently put a
    product-wide dimension list back in a per-job framework's place."""
    import importlib

    for module in ("app.services.pfi_bank", "app.services.validation_bank"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module)
    assert not hasattr(fa, "PFI_DIMENSIONS")
    assert not hasattr(fa, "behavioral_prompts")


def test_validation_is_not_asked_in_the_conversation() -> None:
    """Validation is six mandatory application fields (spec §7), not questions.

    Re-introducing them in the conversation would ask a candidate the same
    things once per application, which is what moving them out fixed.
    """
    import inspect

    from app.api import assessments

    source = inspect.getsource(assessments._conversation_prompts)
    assert "validation" not in source.lower().replace("validation_json", "")


# ── The preset bank is gone ──────────────────────────────────────────────────

def test_the_preset_technical_bank_generator_is_gone() -> None:
    """Regression guard on the 2026-08-06 withdrawal.

    A company can no longer create, edit, store or assign technical questions;
    they are written per candidate during the conversation. Re-introducing a
    per-job generator would put every applicant back on the same stored strings
    without anything in the product announcing it.
    """
    assert not hasattr(fa, "generate_question_bank")
    assert not hasattr(fa, "_question_fallback")


def test_the_preset_bank_routes_are_gone() -> None:
    """The five Company Portal routes behind the bank no longer exist.

    Asserted on the router rather than by calling them: an unregistered path
    404s, and a 404 is indistinguishable from a typo in a test. This checks that
    nothing is REGISTERED, which is the actual claim.
    """
    from app.api import assessments

    paths = {route.path for route in assessments.router.routes}
    assert "/jobs/{job_id}/questions" not in paths
    assert "/jobs/{job_id}/questions/{question_id}" not in paths
    assert "/jobs/{job_id}/finalize" not in paths


def _job(grade: str = "managerial") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), title="Engineer", level=None,
        jd_json={"skills": ["Python", "SQL", "Docker"]}, jd_markdown="",
        experience_min_years=None, experience_max_years=None,
        assessment_grade=grade, assessment_status="questions_pending_review",
        questions_generated_at=None, questions_approved_at=None,
        framework_generated_at=None, framework_approved_at=None,
    )


# ── The deterministic coverage plan ──────────────────────────────────────────
# This is what replaced the preset bank's comparability guarantee, so it is
# tested at least as hard as the bank was.

@pytest.mark.parametrize("grade,expected", list(TECHNICAL_QUESTION_COUNTS.items()))
def test_skill_plan_is_exactly_the_graded_count(grade, expected) -> None:
    plan = ti.skill_plan(_job(grade))
    assert len(plan) == expected
    assert all(skill for skill in plan)


def test_skill_plan_is_identical_for_every_candidate_on_a_job() -> None:
    """THE comparability guarantee for the technical half.

    Two candidates get different QUESTIONS; they must be probed on the same
    SKILLS, in the same order, or their reports cannot be compared. The plan is
    a pure function of the job, so this is a property of the code rather than of
    how carefully a caller uses it.
    """
    job = _job("non_managerial")
    assert ti.skill_plan(job) == ti.skill_plan(job)


def test_skill_plan_cycles_rather_than_repeating_one_skill() -> None:
    """A JD with three skills and twenty slots covers each of them, not the
    first one twenty times."""
    plan = ti.skill_plan(_job("non_managerial"))
    assert set(plan) >= {"Python", "SQL", "Docker"}
    assert plan[:3] == ["Python", "SQL", "Docker"]


def test_skill_plan_survives_a_jd_with_no_declared_skills() -> None:
    job = _job("cxo")
    job.jd_json = {}
    plan = ti.skill_plan(job)
    assert len(plan) == 12
    assert all(skill == "Engineer" for skill in plan)


# ── Scoring ──────────────────────────────────────────────────────────────────

def test_answers_are_grouped_by_question_key() -> None:
    competency_id = str(uuid.uuid4())
    transcript = [
        {"speaker": "agent", "domain": "technical", "question_key": "q1", "content": "Q?"},
        {"speaker": "candidate", "domain": "technical", "question_key": "q1", "content": "A1"},
        {"speaker": "candidate", "domain": "ppi", "question_key": competency_id, "content": "B1"},
        {"speaker": "candidate", "domain": "ppi", "question_key": competency_id, "content": "B2"},
    ]
    grouped = answers_by_key(transcript)
    assert grouped["q1"] == ["A1"]                       # agent turns excluded
    assert grouped[competency_id] == ["B1", "B2"]


@pytest.mark.asyncio
async def test_technical_node_scores_against_each_questions_own_rubric(monkeypatch) -> None:
    seen: list[str] = []

    async def _chat(role_hint, messages, **k):
        blob = " ".join(m["content"] for m in messages)
        seen.append(blob)
        if "scoring one assessment answer" in blob:
            return '{"score": 82, "band": "75_89"}'
        return "Evidence from the candidate's own answer supports solid applied capability here, with a concrete example, "\
               "clear reasoning, and a measurable outcome that interviewers should confirm during discussion."

    monkeypatch.setattr(fa.llm_router, "chat_completion", _chat)

    qid = uuid.uuid4()
    question = SimpleNamespace(
        id=qid, skill="Python", prompt="Explain GIL trade-offs.",
        rubric_json={"0_39": "none", "90_100": "exceptional GIL insight"},
    )
    state = {
        "session": None,
        "link": SimpleNamespace(id=uuid.uuid4()),
        "questions": [question],
        "answers": {str(qid): ["The GIL serialises bytecode execution, so I used multiprocessing."]},
        "transcript": [],
    }
    out = await fa.technical_node(state)
    assert out["technical"][0]["score"] == 82
    assert out["technical_mode"] == "llm_rubric"
    # The question's OWN rubric text reached the scorer.
    assert any("exceptional GIL insight" in blob for blob in seen)


@pytest.mark.asyncio
async def test_unanswered_technical_question_scores_low_and_says_so() -> None:
    question = SimpleNamespace(
        id=uuid.uuid4(), skill="Kafka", prompt="Describe a Kafka design.", rubric_json={},
    )
    state = {
        "session": None,
        "link": SimpleNamespace(id=uuid.uuid4()),
        "questions": [question],
        "answers": {"other": ["hello"]},
        "transcript": [],
    }
    out = await fa.technical_node(state)
    row = out["technical"][0]
    assert row["score"] == fa.UNANSWERED_SCORE < 40
    assert "did not provide an answer" in row["remark"]
    assert 25 <= word_count(row["remark"]) <= 30


@pytest.mark.asyncio
async def test_scoring_falls_back_deterministically_when_llm_is_down(monkeypatch) -> None:
    async def _boom(*a, **k):
        raise RuntimeError("no providers")

    monkeypatch.setattr(fa.llm_router, "chat_completion", _boom)
    qid = uuid.uuid4()
    question = SimpleNamespace(id=qid, skill="SQL", prompt="Explain indexes.", rubric_json={})
    state = {
        "session": None,
        "link": SimpleNamespace(id=uuid.uuid4()),
        "questions": [question],
        "answers": {str(qid): ["B-trees keep lookups logarithmic."]},
        "transcript": [],
    }
    out = await fa.technical_node(state)
    assert out["technical_mode"] == "deterministic_fallback"
    assert 0 <= out["technical"][0]["score"] <= 100


def _competency(category: str, name: str, level: int = 82, ordinal: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), category=category, name=name,
        description=f"What {name} measures.", required_level=level, ordinal=ordinal,
    )


@pytest.mark.asyncio
async def test_ppi_node_scores_every_framework_entry(monkeypatch) -> None:
    async def _chat(role_hint, messages, **k):
        blob = " ".join(m["content"] for m in messages)
        if "scoring one assessment answer" in blob:
            return '{"score": 68}'
        return (
            "The candidate's answers describe a specific delivery they personally owned, naming the constraint they hit, "
            "the option they rejected and why, and the outcome that followed. An interviewer should press for a second "
            "example to confirm the pattern holds under different pressure and a different team."
        )

    monkeypatch.setattr(fa.llm_router, "chat_completion", _chat)
    competencies = [
        _competency(ppi.CATEGORY_PRIMARY, "Distributed systems", 95),
        _competency(ppi.CATEGORY_SECONDARY, "Observability", 67),
        _competency(ppi.CATEGORY_BEHAVIOURAL, "Ownership", 82),
    ]
    # Real prose, not the placeholder ["a", "b"] this fixture used to carry.
    # `services/answer_quality` now refuses a non-answer before it can reach the
    # rubric, so a single-character placeholder routes to the unanswered branch
    # and scores UNANSWERED_SCORE. That is the guard working; the fixture simply
    # has to look like something a candidate would type.
    answers = {
        str(row.id): [
            f"I owned the {row.name.lower()} work on our payments platform and "
            "drove it from design through to production rollout.",
            "The hardest part was migrating live traffic without downtime, so we "
            "shadowed reads for two weeks before cutting over.",
        ]
        for row in competencies
    }
    out = await fa.ppi_node(
        {"session": None, "link": SimpleNamespace(id=uuid.uuid4()),
         "competencies": competencies, "answers": answers, "transcript": []}
    )
    assert len(out["ppi"]) == 3
    assert {row["category"] for row in out["ppi"]} == set(ppi.CATEGORIES)
    assert all(row["score"] == 68 for row in out["ppi"])
    # The job's requirement travels onto the report row, so the radar can plot
    # both shapes even after the job's framework is later edited.
    assert [row["required_level"] for row in out["ppi"]] == [95, 67, 82]
    # PPI remarks are 45-50 words, doubled from the original 25-30 (spec §10.5).
    assert all(45 <= word_count(row["remark"]) <= 50 for row in out["ppi"])


@pytest.mark.asyncio
async def test_unprobed_competency_is_graded_as_no_evidence() -> None:
    competency = _competency(ppi.CATEGORY_PRIMARY, "Kafka")
    out = await fa.ppi_node(
        {"session": None, "link": SimpleNamespace(id=uuid.uuid4()),
         "competencies": [competency], "answers": {}, "transcript": []}
    )
    row = out["ppi"][0]
    assert row["score"] == fa.UNANSWERED_SCORE
    assert "no usable evidence" in row["remark"]
    assert 45 <= word_count(row["remark"]) <= 50


# ── Validation: captured, never scored (spec §7) ─────────────────────────────

@pytest.mark.asyncio
async def test_validation_node_carries_the_application_fields_verbatim() -> None:
    submitted = {
        "current_ctc": "18 LPA",
        "expected_ctc": "26 LPA",
        "notice_period": "60 days",
        "joining_date": "2026-09-01",
        "document_readiness": "All documents ready",
        "role_interest": "I want to work on larger distributed systems.",
    }
    out = await fa.validation_node(
        {"link": SimpleNamespace(id=uuid.uuid4(), validation_json=submitted)}
    )
    value = out["validation"]
    assert value["captured"] is True
    for key, expected in submitted.items():
        assert value[key] == expected
    # Nothing rates, scores or interprets it.
    assert "score" not in value and "rating" not in value and "grade" not in value
    labels = [field["label"] for field in value["fields"]]
    assert "Why does this role interest you?" in labels


@pytest.mark.asyncio
async def test_validation_node_is_explicit_when_nothing_was_collected() -> None:
    """Applications submitted before 2026-07-30 predate the mandatory fields."""
    out = await fa.validation_node(
        {"link": SimpleNamespace(id=uuid.uuid4(), validation_json=None)}
    )
    assert out["validation"]["captured"] is False
    assert out["validation"]["current_ctc"] is None


# ── Grades and word-count contracts (CLAUDE.md hard rule) ────────────────────

def test_rating_labels_follow_the_four_grade_table() -> None:
    assert rating_label(90) == "Highly Matching"
    assert rating_label(75) == "Matching"
    assert rating_label(60) == "Moderately Matching"
    assert rating_label(59) == "Not Matching"
    assert rating_label(0) == "Not Matching"


def test_deterministic_fallbacks_honor_word_contracts() -> None:
    for name in ("Java", "Learning agility", "Enterprise vision & strategy"):
        assert 25 <= word_count(_fallback_remark_25(name)) <= 30
        assert 25 <= word_count(_unanswered_remark(name, 25)) <= 30
        assert 45 <= word_count(_fallback_remark_45(name)) <= 50
        assert 45 <= word_count(_unanswered_remark(name, 45)) <= 50


def test_remarks_end_in_complete_sentences() -> None:
    for value in (
        _fallback_remark_25("Java"),
        _unanswered_remark("Java", 25),
        _fallback_remark_45("Java"),
        _unanswered_remark("Java", 45),
    ):
        assert value.rstrip().endswith(".")


# ── Radar charts (spec §10.4) ────────────────────────────────────────────────

def _dimensions() -> list[dict]:
    return [
        {"category": ppi.CATEGORY_PRIMARY, "name": "Python", "score": 92, "required_level": 95, "ordinal": 1},
        {"category": ppi.CATEGORY_PRIMARY, "name": "SQL", "score": 70, "required_level": 82, "ordinal": 2},
        {"category": ppi.CATEGORY_SECONDARY, "name": "Docker", "score": 55, "required_level": 67, "ordinal": 1},
        {"category": ppi.CATEGORY_BEHAVIOURAL, "name": "Ownership", "score": 80, "required_level": 82, "ordinal": 1},
        {"category": fa.CATEGORY_TECHNICAL, "name": "APIs", "score": 65, "required_level": None, "ordinal": 1},
        {"category": "matching", "name": "Skills match", "score": 88, "required_level": None, "ordinal": 1},
    ]


def test_four_charts_in_the_documented_order() -> None:
    charts = build_radar_charts(_dimensions())
    assert [chart["key"] for chart in charts] == [
        "overall", ppi.CATEGORY_PRIMARY, ppi.CATEGORY_SECONDARY, ppi.CATEGORY_BEHAVIOURAL,
    ]


def test_every_axis_plots_both_shapes_as_words() -> None:
    from app.services.rating import GRADES as SCALE

    for chart in build_radar_charts(_dimensions()):
        for axis in chart["axes"]:
            assert axis["requirement_band"] in SCALE
            assert axis["candidate_band"] in SCALE
            assert 1 <= axis["requirement_index"] <= len(SCALE)
            assert 1 <= axis["candidate_index"] <= len(SCALE)


def test_the_overall_chart_never_invents_a_requirement_for_technical() -> None:
    """Technical items carry no job-requirement level, so plotting them would
    have to fabricate one. They are excluded (see OVERALL_AXES)."""
    overall = build_radar_charts(_dimensions())[0]
    assert [axis["axis"] for axis in overall["axes"]] == [
        "Primary Skills", "Secondary Skills", "Behavioural Competencies",
    ]


def test_no_chart_carries_a_score() -> None:
    for chart in build_radar_charts(_dimensions()):
        for axis in chart["axes"]:
            assert "score" not in axis and "required_level" not in axis


# ── Suggested interview questions (spec §10.3) ───────────────────────────────

def test_between_eight_and_ten_suggested_questions() -> None:
    questions = fa._suggested_questions(_dimensions())
    assert 8 <= len(questions) <= 10
    assert len(set(questions)) == len(questions)


def test_suggested_questions_anchor_on_the_weakest_items_first() -> None:
    questions = fa._suggested_questions(_dimensions())
    # Docker graded lowest of the assessed items, so it leads.
    assert "Docker" in questions[0]
    # The AI Score parameters are a resume snapshot and never anchor a probe.
    assert not any("Skills match" in question for question in questions)


# ── Graph shape (spec §9) ────────────────────────────────────────────────────

def test_graph_has_two_parallel_scorers_joining_at_synthesis() -> None:
    graph = assessment_graph.get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("__start__", "technical_scoring") in edges
    assert ("__start__", "ppi_scoring") in edges
    assert ("__start__", "validation_capture") in edges
    assert ("technical_scoring", "report_synthesis") in edges
    assert ("ppi_scoring", "report_synthesis") in edges
    assert ("validation_capture", "report_synthesis") in edges
    # The retired third scorer must not come back: validation is captured, and
    # `validation_capture` touches no model at all.
    assert "behavioral_scoring" not in {source for source, _ in edges}
