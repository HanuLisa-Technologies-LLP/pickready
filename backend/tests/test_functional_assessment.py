"""Assessment-engine contracts: question counts, banks, scoring, word limits."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services import application_validation
from app.services import functional_assessment as fa
from app.services import gap_analysis
from app.services import ppi
from app.services import rating
from app.services.functional_assessment import (
    GRADE_QUESTION_RANGES,
    _fallback_remark_25,
    _fallback_remark_45,
    _unanswered_remark,
    answers_by_key,
    assessment_graph,
    build_radar_charts,
    rating_label,
    word_count,
)

GRADES = ("non_managerial", "managerial", "leadership", "cxo")


# ── Question counts ──────────────────────────────────────────────────────────

def test_question_ranges_follow_the_grade_table() -> None:
    """Master Directive Part 3 §6 — the non-STEM column, with seniority now
    ADDING questions rather than removing them.

    A RANGE per grade, not a count. The count is resolved once per job from how
    many items that job's matrix actually holds, so two jobs at the same grade
    can legitimately ask a different number of questions while two candidates on
    ONE job never can.
    """
    assert GRADE_QUESTION_RANGES == {
        "non_managerial": (12, 18),
        "managerial": (15, 22),
        "leadership": (18, 25),
        "cxo": (18, 25),
    }
    assert ppi.STEM_GRADE_QUESTION_RANGES == {
        "non_managerial": (18, 28),
        "managerial": (22, 35),
        "leadership": (25, 38),
        "cxo": (25, 38),
    }


@pytest.mark.parametrize("grade,bounds", list(GRADE_QUESTION_RANGES.items()))
def test_the_resolved_target_never_leaves_its_grade_range(grade, bounds) -> None:
    low, high = bounds
    # A matrix smaller than the floor still asks the floor: the surplus goes to
    # the aspects the typical split weights most heavily, so a four-item matrix
    # does not become a four-question interview.
    assert ppi.resolve_question_target(grade, 1) == low
    # One question per item in between.
    assert ppi.resolve_question_target(grade, low + 1) == low + 1
    # And never more than the grade allows, whatever the matrix holds.
    assert ppi.resolve_question_target(grade, high + 50) == high


def test_an_unknown_grade_resolves_as_non_managerial() -> None:
    assert ppi.resolve_question_target(None, 15) == 15
    assert ppi.max_questions("not-a-grade") == GRADE_QUESTION_RANGES["non_managerial"][1]


def test_the_typical_splits_are_illustrative_and_fit_their_own_range() -> None:
    """Spec §5.4 calls the sub-splits illustrative, and nothing enforces them.

    What must hold is that they are not self-contradictory: a split whose floors
    already exceeded the grade's total ceiling would describe an interview the
    product refuses to run.
    """
    for grade, (low, high) in GRADE_QUESTION_RANGES.items():
        split = ppi.typical_split(grade)
        assert set(split) == set(ppi.CATEGORIES)
        assert sum(bounds[0] for bounds in split.values()) <= high
        assert sum(bounds[1] for bounds in split.values()) >= low


def test_the_standalone_technical_track_is_gone() -> None:
    """Draft v4 folded technical depth into the matrix's Must-have items.

    The module is DELETED rather than left unused: a dead generator that still
    produces a parallel question bank is one import away from a second stream
    the candidate would visibly be asked from.
    """
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.services.technical_interview")
    assert not hasattr(fa, "technical_node")
    assert not hasattr(fa, "TECHNICAL_QUESTION_COUNTS")
    assert not hasattr(fa, "technical_question_count")


def test_must_have_is_rubric_scored_and_behavioural_is_not() -> None:
    """Spec §8: one scoring agent, two methods, split by item type."""
    assert ppi.RUBRIC_SCORED_CATEGORIES == {
        ppi.CATEGORY_MUST_HAVE,
        ppi.CATEGORY_NICE_TO_HAVE,
    }
    assert ppi.CATEGORY_BEHAVIOURAL not in ppi.RUBRIC_SCORED_CATEGORIES


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
# The plan is now the MATRIX: which items are probed, in what order, and how
# many questions there are, decided before the conversation starts and identical
# for every candidate on the job. `tests/test_ppi.py` owns those assertions,
# because the matrix is that module's.


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


def _competency(category: str, name: str, level: int = 82, ordinal: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), category=category, name=name,
        description=f"What {name} measures.", required_level=level, ordinal=ordinal,
    )


def _question(competency, prompt: str, rubric: dict | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), competency_id=competency.id, prompt=prompt,
        rubric_json=rubric, ordinal=1,
    )


@pytest.mark.asyncio
async def test_a_must_have_answer_is_scored_against_its_own_questions_rubric(
    monkeypatch,
) -> None:
    """Spec §8: rubric-based for Must-have and Nice-to-have.

    The rubric that reaches the scorer must be the one written FOR THE QUESTION
    THE CANDIDATE WAS ASKED. That is the whole invariant that made generating a
    technical question mid-conversation safe.
    """
    seen: list[str] = []

    async def _chat(role_hint, messages, **k):
        blob = " ".join(m["content"] for m in messages)
        seen.append(blob)
        if "scoring one assessment answer" in blob:
            return '{"score": 82, "band": "75_89"}'
        return (
            "Evidence from the candidate's own answer supports solid applied capability here, "
            "naming the constraint they hit, the option they rejected and the outcome that "
            "followed, which an interviewer should confirm with one further worked example."
        )

    monkeypatch.setattr(fa.llm_router, "chat_completion", _chat)

    competency = _competency(ppi.CATEGORY_MUST_HAVE, "Python", 95)
    question = _question(
        competency,
        "Explain GIL trade-offs.",
        {"0_39": "none", "90_100": "exceptional GIL insight"},
    )
    out = await fa.ppi_scoring_node({
        "session": None,
        "link": SimpleNamespace(id=uuid.uuid4()),
        "competencies": [competency],
        "candidate_questions": [question],
        "answers": {
            str(question.id): [
                "The GIL serialises bytecode execution, so I used multiprocessing."
            ]
        },
        "transcript": [],
    })
    assert out["ppi"][0]["score"] == 82
    assert out["ppi"][0]["category"] == ppi.CATEGORY_MUST_HAVE
    assert out["ppi_mode"] == "llm_rubric"
    # The question's OWN rubric text reached the scorer.
    assert any("exceptional GIL insight" in blob for blob in seen)


@pytest.mark.asyncio
async def test_a_behavioural_answer_is_judged_not_scored_against_a_stored_rubric(
    monkeypatch,
) -> None:
    """Spec §8: judgement-based for Behavioural.

    A behavioural item carries no rubric of its own, so the scorer must reach
    for the shared judgement standard instead of skipping the answer.
    """
    seen: list[str] = []

    async def _chat(role_hint, messages, **k):
        blob = " ".join(m["content"] for m in messages)
        seen.append(blob)
        if "scoring one assessment answer" in blob:
            return '{"score": 71}'
        return (
            "The candidate describes a delivery they personally owned, naming the constraint "
            "they hit, the option they rejected and why, and the outcome that followed. An "
            "interviewer should press for a second example under different pressure."
        )

    monkeypatch.setattr(fa.llm_router, "chat_completion", _chat)

    competency = _competency(ppi.CATEGORY_BEHAVIOURAL, "Ownership", 82)
    question = _question(competency, "Tell me about work you saw through.", None)
    out = await fa.ppi_scoring_node({
        "session": None,
        "link": SimpleNamespace(id=uuid.uuid4()),
        "competencies": [competency],
        "candidate_questions": [question],
        "answers": {
            str(question.id): [
                "I owned the payments migration end to end and stayed on it until "
                "the last consumer had cut over cleanly."
            ]
        },
        "transcript": [],
    })
    assert out["ppi"][0]["score"] == 71
    assert any("credible situation with clear personal action" in blob for blob in seen)


@pytest.mark.asyncio
async def test_an_unanswered_item_scores_low_and_says_so() -> None:
    competency = _competency(ppi.CATEGORY_MUST_HAVE, "Kafka")
    question = _question(competency, "Describe a Kafka design.", {})
    out = await fa.ppi_scoring_node({
        "session": None,
        "link": SimpleNamespace(id=uuid.uuid4()),
        "competencies": [competency],
        "candidate_questions": [question],
        "answers": {"other": ["hello"]},
        "transcript": [],
    })
    row = out["ppi"][0]
    assert row["score"] == fa.UNANSWERED_SCORE < 40
    assert "No substantive answer" in row["remark"]
    assert 45 <= word_count(row["remark"]) <= 50


@pytest.mark.asyncio
async def test_scoring_falls_back_deterministically_when_llm_is_down(monkeypatch) -> None:
    async def _boom(*a, **k):
        raise RuntimeError("no providers")

    monkeypatch.setattr(fa.llm_router, "chat_completion", _boom)
    competency = _competency(ppi.CATEGORY_MUST_HAVE, "SQL")
    question = _question(competency, "Explain indexes.", {})
    out = await fa.ppi_scoring_node({
        "session": None,
        "link": SimpleNamespace(id=uuid.uuid4()),
        "competencies": [competency],
        "candidate_questions": [question],
        "answers": {str(question.id): ["B-trees keep lookups logarithmic."]},
        "transcript": [],
    })
    assert out["ppi_mode"] == "deterministic_fallback"
    assert 0 <= out["ppi"][0]["score"] <= 100


@pytest.mark.asyncio
async def test_every_matrix_item_is_scored_in_report_order(monkeypatch) -> None:
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
        _competency(ppi.CATEGORY_MUST_HAVE, "Distributed systems", 95),
        _competency(ppi.CATEGORY_NICE_TO_HAVE, "Observability", 67),
        _competency(ppi.CATEGORY_BEHAVIOURAL, "Ownership", 82),
    ]
    questions = [
        _question(row, f"Tell me about {row.name}.", {"0_39": "none", "90_100": "deep"})
        for row in competencies
    ]
    # Real prose, not a placeholder. `services/answer_quality` refuses a
    # non-answer before it can reach the rubric, so a single-character
    # placeholder routes to the unanswered branch and scores UNANSWERED_SCORE.
    answers = {
        str(question.id): [
            f"I owned the {competency.name.lower()} work on our payments platform "
            "and drove it from design through to production rollout.",
            "The hardest part was migrating live traffic without downtime, so we "
            "shadowed reads for two weeks before cutting over.",
        ]
        for competency, question in zip(competencies, questions)
    }
    out = await fa.ppi_scoring_node({
        "session": None,
        "link": SimpleNamespace(id=uuid.uuid4()),
        "competencies": competencies,
        "candidate_questions": questions,
        "answers": answers,
        "transcript": [],
    })
    assert len(out["ppi"]) == 3
    assert [row["category"] for row in out["ppi"]] == list(ppi.CATEGORIES)
    assert all(row["score"] == 68 for row in out["ppi"])
    # The job's requirement travels onto the report row, so the radar can plot
    # both shapes even after the job's matrix is later edited.
    assert [row["required_level"] for row in out["ppi"]] == [95, 67, 82]
    # Every PPI remark is 45-50 words (spec §9.5).
    assert all(45 <= word_count(row["remark"]) <= 50 for row in out["ppi"])


# ── Validation: captured, never scored (spec §7) ─────────────────────────────

class _CandidateSession:
    """Serves `.get(Candidate, id)` with one fixed candidate row.

    `validation_node` only ever calls `session.get`, so a fake this small is
    enough and keeps the assertions about the DECISION rather than SQLAlchemy.
    """

    def __init__(self, candidate=None) -> None:
        self._candidate = candidate

    async def get(self, _model, _id):
        return self._candidate


@pytest.mark.asyncio
async def test_validation_node_carries_the_application_fields_verbatim() -> None:
    submitted = {
        "current_ctc": "18 LPA",
        "expected_ctc": "26 LPA",
        "notice_period": "60 days",
        "document_readiness": "All documents ready",
        "role_interest": "I want to work on larger distributed systems.",
    }
    out = await fa.validation_node(
        {
            "link": SimpleNamespace(
                id=uuid.uuid4(), candidate_id=uuid.uuid4(), validation_json=submitted
            ),
            "session": _CandidateSession(),
        }
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
async def test_validation_node_also_carries_the_full_profile_questionnaire() -> None:
    """The 38-item profile form must reach the report too (2026-08-16 report):
    the Validation section was showing only the six application fields."""
    from app.services.candidate_profile_form import ALL_FIELDS

    candidate = SimpleNamespace(
        profile_form_json={
            "current_city": "Bengaluru",
            "total_experience": "5 Years",
            "bgv_consent": "Yes, I consent to a Background Verification check",
        }
    )
    out = await fa.validation_node(
        {
            "link": SimpleNamespace(
                id=uuid.uuid4(), candidate_id=uuid.uuid4(), validation_json={}
            ),
            "session": _CandidateSession(candidate),
        }
    )
    fields = out["validation"]["fields"]
    # All 38 profile items appear, not just the 6 application ones.
    assert len(fields) == 6 + len(ALL_FIELDS)
    keys = [field["key"] for field in fields]
    assert len(keys) == len(set(keys)), "no two fields may share a key"
    by_key = {field["key"]: field for field in fields}
    assert by_key["profile:current_city"]["value"] == "Bengaluru"
    assert by_key["profile:current_city"]["group"] == "Personal Details"
    assert by_key["profile:total_experience"]["value"] == "5 Years"
    # A profile field the candidate never answered still appears, unanswered.
    assert by_key["profile:last_company_name"]["value"] is None
    # The application's own `current_ctc` is a DIFFERENT key from the
    # profile's `current_ctc` -- both questionnaires ask it, and prefixing is
    # what stops one from overwriting the other.
    assert by_key["current_ctc"]["group"] == "Application"
    assert by_key["profile:current_ctc"]["group"] == "Compensation & Availability"


@pytest.mark.asyncio
async def test_validation_node_survives_a_missing_candidate_row() -> None:
    """A deleted or unlinked candidate must not crash report synthesis; the
    profile section simply renders every item as unanswered."""
    out = await fa.validation_node(
        {
            "link": SimpleNamespace(
                id=uuid.uuid4(), candidate_id=uuid.uuid4(), validation_json={}
            ),
            "session": _CandidateSession(None),
        }
    )
    assert out["validation"]["fields"]


def test_the_earliest_joining_date_is_mandatory_again() -> None:
    """RESTORED (Draft v4, spec §14, which lists it among the mandatory fields).

    It was removed on 2026-08-09 on the reasoning that it duplicated the notice
    period answered one field earlier. That reasoning is still sound and the
    client has asked for the field anyway, which is their call.

    Asserted at the field list rather than the form, because the field list is
    what the apply page renders AND what the report's Validation section reads:
    one entry here puts the question on both at once, which is the property that
    keeps the two from drifting.
    """
    assert "joining_date" in application_validation.MANDATORY_KEYS
    labels = [field["label"] for field in application_validation.VALIDATION_FIELDS]
    assert any("joining" in label.lower() for label in labels)
    assert "joining_date" in application_validation.normalise(
        {"joining_date": "2026-09-01", "current_ctc": "18 LPA"}
    )


def test_the_ctc_fields_carry_a_rupee_example(monkeypatch) -> None:
    """Spec §15: a worked example beside the box, and values in rupees.

    Held on the FIELD rather than in the form component for the same reason the
    field list is: the report renders the fields the form collected, and an
    example that exists only in the frontend is one the report cannot explain.
    """
    ctc = [
        field
        for field in application_validation.VALIDATION_FIELDS
        if field["key"] in {"current_ctc", "expected_ctc"}
    ]
    assert len(ctc) == 2
    for field in ctc:
        assert field["placeholder"] == application_validation.CTC_EXAMPLE
        assert field["currency"] == "INR"
        assert "rupees" in field["hint"].lower()
        assert application_validation.CTC_EXAMPLE in field["hint"]
    # Indian digit grouping, which is the client's own example.
    assert application_validation.CTC_EXAMPLE == "4,00,000"


def test_a_ctc_answer_is_never_refused_over_its_punctuation() -> None:
    """The example is a HINT, not a validator.

    "4 LPA", "4,00,000" and "400000" all answer the question, and refusing any
    of them would fail a real answer over formatting. Nothing scores this field,
    so it reaches the recruiter exactly as typed (spec §14).
    """
    for typed in ("4 LPA", "4,00,000", "400000", "Rs. 4 lakh"):
        stored = application_validation.normalise({"current_ctc": typed})
        assert stored["current_ctc"] == typed

def test_document_readiness_names_the_documents_it_refers_to() -> None:
    """A readiness answer that lists nothing asks the candidate to attest to a
    set they cannot see (client report, 2026-08-09)."""
    field = next(
        f
        for f in application_validation.VALIDATION_FIELDS
        if f["key"] == "document_readiness"
    )
    assert len(field["documents"]) >= 5
    joined = " ".join(field["documents"]).lower()
    for expected in ("pan", "identity", "pay slip"):
        assert expected in joined


def test_the_mandatory_fields_carry_forward_to_the_next_application() -> None:
    """Filled once, reused, still snapshotted per application."""
    previous = {
        "current_ctc": " 18 LPA ",
        "notice_period": "60 days",
        "unknown_key": "dropped",
    }
    defaults = application_validation.reusable_defaults(previous)
    assert defaults["current_ctc"] == "18 LPA"
    assert defaults["notice_period"] == "60 days"
    assert "unknown_key" not in defaults
    # A candidate with no history starts empty rather than erroring.
    assert application_validation.reusable_defaults(None) == {}


def test_the_intro_states_the_reuse_behaviour_and_breaks_no_copy_rule() -> None:
    intro = application_validation.SECTION_INTRO
    assert "mandatory" in intro
    assert "only one time" in intro
    assert chr(8212) not in intro  # no em dash in any string (platform audit)


@pytest.mark.asyncio
async def test_validation_node_is_explicit_when_nothing_was_collected() -> None:
    """Applications submitted before 2026-07-30 predate the mandatory fields."""
    out = await fa.validation_node(
        {
            "link": SimpleNamespace(
                id=uuid.uuid4(), candidate_id=uuid.uuid4(), validation_json=None
            ),
            "session": _CandidateSession(),
        }
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
        {"category": ppi.CATEGORY_MUST_HAVE, "name": "Python", "score": 92,
         "required_level": 95, "ordinal": 1, "remark": "Strong applied evidence."},
        {"category": ppi.CATEGORY_MUST_HAVE, "name": "SQL", "score": 70,
         "required_level": 82, "ordinal": 2, "remark": "Some evidence, thin on depth."},
        {"category": ppi.CATEGORY_NICE_TO_HAVE, "name": "Docker", "score": 55,
         "required_level": 67, "ordinal": 1, "remark": "Little evidence offered."},
        {"category": ppi.CATEGORY_BEHAVIOURAL, "name": "Ownership", "score": 80,
         "required_level": 82, "ordinal": 1, "remark": "Owned delivery end to end."},
        {"category": "matching", "name": "Skills present", "score": 88,
         "required_level": None, "ordinal": 1, "remark": "Resume names the stack."},
    ]


def test_four_charts_in_the_documented_order() -> None:
    charts = build_radar_charts(_dimensions())
    assert [chart["key"] for chart in charts] == [
        "overall", ppi.CATEGORY_MUST_HAVE, ppi.CATEGORY_NICE_TO_HAVE,
        ppi.CATEGORY_BEHAVIOURAL,
    ]


def test_every_axis_plots_both_shapes_as_words() -> None:
    from app.services.rating import GRADES as SCALE

    for chart in build_radar_charts(_dimensions()):
        for axis in chart["axes"]:
            assert axis["requirement_band"] in SCALE
            assert axis["candidate_band"] in SCALE
            assert 1 <= axis["requirement_index"] <= len(SCALE)
            assert 1 <= axis["candidate_index"] <= len(SCALE)


def test_the_overall_chart_plots_the_three_aspects() -> None:
    """One spoke per PPI aspect, and no fourth.

    Under Draft v4 this is a cleaner chart than it was: technical is no longer a
    separate category carrying no job-requirement level, so nothing has to be
    excluded to avoid fabricating a requirement for it.
    """
    overall = build_radar_charts(_dimensions())[0]
    assert [axis["axis"] for axis in overall["axes"]] == [
        "Must-have", "Nice-to-have", "Behavioural Competencies",
    ]


def test_the_ai_score_never_appears_on_a_chart() -> None:
    """The four charts are part of the PPI Assessment (spec §9.4).

    The AI Score is a resume snapshot with no job-requirement shape, and a chart
    that plotted it would have to invent one.
    """
    names = {
        axis["axis"]
        for chart in build_radar_charts(_dimensions())
        for axis in chart["axes"]
    }
    assert "Skills present" not in names


def test_no_chart_carries_a_score() -> None:
    for chart in build_radar_charts(_dimensions()):
        for axis in chart["axes"]:
            assert "score" not in axis and "required_level" not in axis


# ── The Must-have hard cap (spec §5.5) ───────────────────────────────────────

def test_a_not_matching_must_have_triggers_the_cap() -> None:
    rows = _dimensions() + [
        {"category": ppi.CATEGORY_MUST_HAVE, "name": "Kafka", "score": 30,
         "required_level": 95, "ordinal": 3, "remark": "No evidence."},
    ]
    assert gap_analysis.must_have_cap_applies(rows) is True


def test_a_weak_nice_to_have_or_behavioural_never_triggers_the_cap() -> None:
    """Spec §5.5 is explicit that no equivalent cap applies to the other two."""
    for category in (ppi.CATEGORY_NICE_TO_HAVE, ppi.CATEGORY_BEHAVIOURAL):
        rows = [
            {"category": ppi.CATEGORY_MUST_HAVE, "name": "Python", "score": 95,
             "required_level": 95, "ordinal": 1, "remark": "Excellent."},
            {"category": category, "name": "Something", "score": 20,
             "required_level": 67, "ordinal": 1, "remark": "No evidence."},
        ]
        assert gap_analysis.must_have_cap_applies(rows) is False


def test_the_cap_holds_the_overall_grade_down_but_never_raises_it() -> None:
    """`min`, not an assignment.

    A candidate whose aggregate already grades Not Matching must STAY Not
    Matching: a cap that SET the score would quietly promote the weakest
    candidates into the band it was written to keep the strong ones out of.

    The arithmetic moved to `miti/caps.apply` on 2026-08-29, along with the two
    Runbook controls the product never implemented, and its ceiling moved from
    74 to the 71 the Runbook states. `rating.cap_to_moderately` is DELETED, not
    deprecated: leaving it would have been a second implementation of one
    concept, capping three points higher, under the more obvious name.
    """
    from app.services.miti import caps

    assert not hasattr(rating, "cap_to_moderately")
    fired = [caps.BandCap("c", "cite", "Kafka", "why", 71)]
    assert rating.grade_for_percent(caps.apply(97, fired)) == rating.GRADE_MODERATELY
    assert rating.grade_for_percent(caps.apply(76, fired)) == rating.GRADE_MODERATELY
    # Already below the cap: untouched.
    assert caps.apply(30, fired) == 30
    assert rating.grade_for_percent(caps.apply(30, fired)) == rating.GRADE_NOT


# ── Gap Analysis & Action Plan (spec §9.6) ───────────────────────────────────

def test_gaps_are_every_item_graded_moderately_matching_or_below() -> None:
    gaps = gap_analysis.gap_items(_dimensions(), ppi.CATEGORY_MUST_HAVE)
    assert [row["name"] for row in gaps] == ["SQL"]
    # 92 grades Highly Matching, so Python is not a gap.
    assert "Python" not in {row["name"] for row in gaps}


def test_not_matching_is_ordered_before_moderately_matching() -> None:
    rows = [
        {"category": ppi.CATEGORY_MUST_HAVE, "name": "Moderate", "score": 65,
         "required_level": 82, "ordinal": 1, "remark": "Some evidence."},
        {"category": ppi.CATEGORY_MUST_HAVE, "name": "Absent", "score": 20,
         "required_level": 95, "ordinal": 2, "remark": "No evidence."},
    ]
    assert [row["name"] for row in gap_analysis.gap_items(rows, ppi.CATEGORY_MUST_HAVE)] == [
        "Absent", "Moderate",
    ]


def test_must_have_is_the_first_group() -> None:
    """Spec §9.6: Must-have is reviewed first, because it is the aspect the hard
    cap actually governs."""
    assert gap_analysis.gap_order()[0] == ppi.CATEGORY_MUST_HAVE
    assert list(gap_analysis.gap_order()) == list(ppi.CATEGORIES)


def test_a_not_matching_must_have_earns_more_than_one_probe() -> None:
    assert gap_analysis.probe_count_for(
        ppi.CATEGORY_MUST_HAVE, rating.GRADE_NOT
    ) > 1
    assert gap_analysis.probe_count_for(
        ppi.CATEGORY_MUST_HAVE, rating.GRADE_MODERATELY
    ) == 1
    assert gap_analysis.probe_count_for(
        ppi.CATEGORY_BEHAVIOURAL, rating.GRADE_NOT
    ) == 1


@pytest.mark.asyncio
async def test_the_section_states_every_empty_group_in_words(monkeypatch) -> None:
    """Spec §9.6: a group with no gaps says so, rather than rendering blank."""
    async def _boom(*a, **k):
        raise RuntimeError("no providers")

    monkeypatch.setattr(gap_analysis.llm_router, "chat_completion", _boom)
    clean = [
        {"category": category, "name": f"Item {index}", "score": 95,
         "required_level": 95, "ordinal": 1, "remark": "Strong."}
        for index, category in enumerate(ppi.CATEGORIES, 1)
    ]
    section = await gap_analysis.build_gap_analysis(None, clean, {})
    assert section["must_have_cap_applied"] is False
    for group in section["groups"]:
        assert group["items"] == []
        assert group["no_gaps_statement"]
        assert "No " in group["no_gaps_statement"]


@pytest.mark.asyncio
async def test_the_cap_is_stated_on_the_must_have_group(monkeypatch) -> None:
    """Spec §9.6: the section says the cap fired, rather than leaving the reader
    to cross-reference the Overall Assessment."""
    async def _boom(*a, **k):
        raise RuntimeError("no providers")

    monkeypatch.setattr(gap_analysis.llm_router, "chat_completion", _boom)
    rows = [
        {"category": ppi.CATEGORY_MUST_HAVE, "name": "Kafka", "score": 20,
         "required_level": 95, "ordinal": 1, "remark": "No evidence offered."},
    ]
    section = await gap_analysis.build_gap_analysis(
        None, rows, {"Kafka": [{"question": "Describe a Kafka design.",
                                "answer": "I have not used Kafka in production."}]}
    )
    assert section["must_have_cap_applied"] is True
    must_have = section["groups"][0]
    assert must_have["category"] == ppi.CATEGORY_MUST_HAVE
    assert "Moderately Matching" in must_have["cap_statement"]
    assert "Kafka" in must_have["cap_statement"]


@pytest.mark.asyncio
async def test_a_gap_reuses_its_item_remark_rather_than_rewriting_it(monkeypatch) -> None:
    async def _boom(*a, **k):
        raise RuntimeError("no providers")

    monkeypatch.setattr(gap_analysis.llm_router, "chat_completion", _boom)
    section = await gap_analysis.build_gap_analysis(None, _dimensions(), {})
    sql = next(
        item
        for group in section["groups"]
        for item in group["items"]
        if item["name"] == "SQL"
    )
    assert sql["remark"] == "Some evidence, thin on depth."


@pytest.mark.asyncio
async def test_a_degraded_probe_is_still_grounded_and_carries_no_em_dash(
    monkeypatch,
) -> None:
    """Every failure path still quotes what the candidate said.

    A probe that invented a claim would be worse than no probe, and a purely
    generic one is what this section was built to stop producing.
    """
    async def _boom(*a, **k):
        raise RuntimeError("no providers")

    monkeypatch.setattr(gap_analysis.llm_router, "chat_completion", _boom)
    evidence = {
        "SQL": [{"question": "How do you tune a slow query?",
                 "answer": "I added a covering index on the orders table"}]
    }
    section = await gap_analysis.build_gap_analysis(None, _dimensions(), evidence)
    sql = next(
        item
        for group in section["groups"]
        for item in group["items"]
        if item["name"] == "SQL"
    )
    assert sql["probes"]
    assert "covering index" in sql["probes"][0]
    assert chr(8212) not in sql["probes"][0]


@pytest.mark.asyncio
async def test_the_focus_summary_names_a_real_gap(monkeypatch) -> None:
    async def _boom(*a, **k):
        raise RuntimeError("no providers")

    monkeypatch.setattr(gap_analysis.llm_router, "chat_completion", _boom)
    section = await gap_analysis.build_gap_analysis(None, _dimensions(), {})
    assert "SQL" in section["focus_summary"]


# ── Graph shape (spec §9) ────────────────────────────────────────────────────

def test_graph_has_one_scorer_joining_validation_at_synthesis() -> None:
    """Spec §8 and §19.

    ONE scoring agent, because there is one matrix. `validation_capture` is a
    node but not a scorer: it copies the application's fields and touches no
    model. The join edge is what makes "synthesis waits for scoring" a property
    of the graph rather than a convention.
    """
    graph = assessment_graph.get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("__start__", "ppi_scoring") in edges
    assert ("__start__", "validation_capture") in edges
    assert ("ppi_scoring", "report_synthesis") in edges
    assert ("validation_capture", "report_synthesis") in edges
    # The retired scorers must not come back. `technical_scoring` was a second
    # agent for a second question bank that no longer exists, and
    # `behavioral_scoring` was a third one retired before it.
    sources = {source for source, _ in edges}
    assert "technical_scoring" not in sources
    assert "behavioral_scoring" not in sources

# ── Self-checks retired from module scope (2026-08-24) ──────────────────────
#
# `gap_analysis` and `ppi_interview` each carried module-level `assert`
# statements. Two problems, and the second is why they moved rather than being
# deleted: `python -O` strips an assert, so they protected no production image
# at all; and they READ another service module at import time, which is an
# AttributeError the moment a cycle reaches them. That happened. Here they
# actually run.


def test_must_have_is_the_first_gap_group_reviewed() -> None:
    """Spec 9.6. It is the aspect the hard cap governs, so it is read first."""
    assert gap_analysis.gap_order()[0] == ppi.CATEGORY_MUST_HAVE


def test_a_probe_is_shorter_than_an_items_remark() -> None:
    """A probe is a prompt for an interviewer, not a written assessment."""
    assert gap_analysis.PROBE_WORDS[0] <= gap_analysis.PROBE_WORDS[1]


def test_at_least_one_aspect_is_rubric_scored() -> None:
    from app.services import ppi_interview  # noqa: PLC0415

    assert ppi.RUBRIC_SCORED_CATEGORIES


def test_behavioural_is_never_rubric_scored() -> None:
    """It is graded by judgement because there is no single correct answer to
    weigh it against (spec 8)."""
    assert ppi.CATEGORY_BEHAVIOURAL not in ppi.RUBRIC_SCORED_CATEGORIES
