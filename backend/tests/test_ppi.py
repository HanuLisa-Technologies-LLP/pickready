"""PPI framework generation, per-candidate questions, and the four-grade scale."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services import ppi, rating
from app.services import application_validation as av


# ── The one rating scale (spec §10.2) ────────────────────────────────────────

def test_exactly_four_grades_best_to_worst() -> None:
    assert rating.GRADES == (
        "Highly Matching",
        "Matching",
        "Moderately Matching",
        "Not Matching",
    )


def test_bands_are_inclusive_upward() -> None:
    """CLAUDE.md rule 8: a score landing exactly on a boundary takes the
    HIGHER band. The cut-points are unchanged from the retired five-label
    scale, so a report written before this release regrades identically."""
    assert rating.grade_for_percent(90) == "Highly Matching"
    assert rating.grade_for_percent(89.9) == "Matching"
    assert rating.grade_for_percent(75) == "Matching"
    assert rating.grade_for_percent(74.9) == "Moderately Matching"
    assert rating.grade_for_percent(60) == "Moderately Matching"
    assert rating.grade_for_percent(59.9) == "Not Matching"
    assert rating.grade_for_percent(0) == "Not Matching"


def test_none_in_none_out_and_a_bool_is_not_a_score() -> None:
    assert rating.grade_for_percent(None) is None
    assert rating.grade_for_percent(True) is None
    assert rating.grade_for_percent("high") is None
    assert rating.grade_for_ten(None) is None
    assert rating.grade_for_ten(False) is None


def test_the_ten_point_scale_agrees_with_the_hundred_point_scale() -> None:
    for tenth in range(0, 101):
        assert rating.grade_for_ten(tenth / 10.0) == rating.grade_for_percent(tenth)


def test_band_index_is_a_radius_not_a_score() -> None:
    assert rating.band_index_for("Highly Matching") == 4
    assert rating.band_index_for("Not Matching") == 1
    # A report from an older build still draws.
    assert rating.band_index_for("Very High") == 1
    assert rating.band_index_for(None) == 1


# ── Culture is refused (spec §5) ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "name",
    ["Culture", "Culture fit", "CULTURAL ALIGNMENT", "Company culture", "cultural add"],
)
def test_culture_is_refused_in_any_casing(name) -> None:
    assert ppi.is_forbidden_competency(name)


@pytest.mark.parametrize("name", ["Agricultural domain knowledge", "Ownership", ""])
def test_a_legitimate_competency_is_not_caught(name) -> None:
    assert not ppi.is_forbidden_competency(name)


# ── The three aspects (spec §5) ──────────────────────────────────────────────

def test_the_matrix_has_three_aspects_in_report_order() -> None:
    assert ppi.CATEGORIES == ("must_have", "nice_to_have", "behavioural")
    assert ppi.CATEGORY_LABELS[ppi.CATEGORY_MUST_HAVE] == "Must-have"
    assert ppi.CATEGORY_LABELS[ppi.CATEGORY_NICE_TO_HAVE] == "Nice-to-have"


def test_the_retired_aspect_names_are_gone() -> None:
    """Must-have and Nice-to-have are RENAMES, not new aspects alongside the old
    ones. Two vocabularies would mean every read path had to accept either."""
    assert not hasattr(ppi, "CATEGORY_PRIMARY")
    assert not hasattr(ppi, "CATEGORY_SECONDARY")


# ── Matrix generation ────────────────────────────────────────────────────────

class _StubSession:
    """A session stub that answers both shapes `generate_framework` uses.

    `.all()` for the existing matrix and `.first()` for the SWOT intake. Both
    are modelled rather than only the one under test, because a stub that
    raises AttributeError on an unexercised path makes an unrelated code change
    look like a test failure.
    """

    def __init__(self, existing: list | None = None, swot=None) -> None:
        self.added: list = []
        self._existing = existing or []
        self._swot = swot

    async def execute(self, statement, *a, **k):
        rows = self._existing
        swot = self._swot
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(
                all=lambda: rows, first=lambda: swot
            )
        )

    def add_all(self, rows) -> None:
        self.added.extend(rows)

    def add(self, row) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        return None

    async def get(self, *a, **k):
        return None


def _job(grade: str = "non_managerial") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), title="Backend Engineer",
        assessment_grade=grade, jd_markdown="",
        experience_min_years=3, experience_max_years=6,
        jd_json={
            "skills": ["Python", "PostgreSQL", "Kafka"],
            "responsibilities": ["Design and own service APIs", "Run the on-call rota"],
            "accountabilities": ["Latency stays within the agreed budget"],
        },
        framework_generated_at=None, framework_approved_at=None,
        question_target=None, swot_completed_at=None,
    )


@pytest.mark.asyncio
async def test_a_generated_matrix_covers_every_aspect(monkeypatch) -> None:
    """There is NO minimum item count in Draft v4 (spec §5.2).

    What is still structural is coverage: every aspect is graded, remarked and
    charted on each report, so none of the three may come back empty.
    """
    async def _chat(*a, **k):
        return (
            '{"competencies":[{"category":"must_have","name":"Python",'
            '"description":"Writes production Python.","required_level":"Highly Matching"}]}'
        )

    monkeypatch.setattr(ppi.llm_router, "chat_completion", _chat)
    job = _job()
    rows = await ppi.generate_framework(_StubSession(), job)
    assert {row.category for row in rows} == set(ppi.CATEGORIES)
    assert job.framework_generated_at is not None
    # The question count is resolved WITH the matrix and stored on the job, so
    # every candidate on it answers the same number.
    assert job.question_target == ppi.resolve_question_target(
        job.assessment_grade, len(rows)
    )
    # Generation NEVER approves: the Hiring Manager does (spec §5.3).
    assert job.framework_approved_at is None


@pytest.mark.asyncio
async def test_a_short_generation_is_no_longer_padded(monkeypatch) -> None:
    """The old floor of five per aspect manufactured names like
    "Kafka (further core)" to reach it, and that filler landed on the one screen
    a human is required to review. With no minimum, three items is a valid
    answer and stays three items."""
    async def _chat(*a, **k):
        return (
            '{"competencies":['
            '{"category":"must_have","name":"Python","required_level":"Highly Matching"},'
            '{"category":"nice_to_have","name":"Terraform","required_level":"Matching"},'
            '{"category":"behavioural","name":"Ownership","required_level":"Matching"}]}'
        )

    monkeypatch.setattr(ppi.llm_router, "chat_completion", _chat)
    rows = await ppi.generate_framework(_StubSession(), _job())
    assert [row.name for row in rows] == ["Python", "Terraform", "Ownership"]
    assert not any("(" in row.name for row in rows)


@pytest.mark.asyncio
async def test_a_matrix_never_exceeds_the_grades_question_ceiling(monkeypatch) -> None:
    """Every item is probed at least once, so the grade's question ceiling is
    also the matrix's ceiling (spec §5.4)."""
    async def _chat(*a, **k):
        items = ",".join(
            f'{{"category":"must_have","name":"Skill {index}",'
            f'"required_level":"Matching"}}'
            for index in range(60)
        )
        return '{"competencies":[' + items + "]}"

    monkeypatch.setattr(ppi.llm_router, "chat_completion", _chat)
    job = _job("cxo")
    rows = await ppi.generate_framework(_StubSession(), job)
    assert len(rows) <= ppi.max_questions("cxo") + len(ppi.CATEGORIES)
    ok, _ = ppi.matrix_is_complete(list(rows), "cxo")
    # Whatever the model returned, what is SAVED must be saveable.
    assert len(rows) <= ppi.max_questions("cxo") or not ok


@pytest.mark.asyncio
async def test_the_matrix_survives_a_total_llm_outage(monkeypatch) -> None:
    async def _boom(*a, **k):
        raise RuntimeError("all providers down")

    monkeypatch.setattr(ppi.llm_router, "chat_completion", _boom)
    rows = await ppi.generate_framework(_StubSession(), _job())
    assert {row.category for row in rows} == set(ppi.CATEGORIES)
    # Built from the JD's own words, not from a generic template.
    must_have = [row.name for row in rows if row.category == ppi.CATEGORY_MUST_HAVE]
    assert "Python" in must_have


@pytest.mark.asyncio
async def test_the_swot_intake_reaches_the_generator(monkeypatch) -> None:
    """Spec §5.2: the matrix comes from the JD AND the SWOT intake together."""
    seen: list[str] = []

    async def _chat(role_hint, messages, **k):
        seen.append(" ".join(m["content"] for m in messages))
        return (
            '{"competencies":[{"category":"must_have","name":"Incident command",'
            '"required_level":"Highly Matching"}]}'
        )

    monkeypatch.setattr(ppi.llm_router, "chat_completion", _chat)
    swot = SimpleNamespace(
        strengths=["Runs a calm incident bridge"],
        weaknesses=["People here freeze during a live outage"],
        opportunities=[],
        threats=[],
        captured=lambda: {
            "strengths": ["Runs a calm incident bridge"],
            "weaknesses": ["People here freeze during a live outage"],
            "opportunities": [],
            "threats": [],
        },
        is_empty=lambda: False,
    )
    await ppi.generate_framework(_StubSession(swot=swot), _job())
    assert any("freeze during a live outage" in blob for blob in seen)


@pytest.mark.parametrize(
    "jd_json",
    [
        # A job created from `jd_markdown` alone: the per-section columns are
        # derived and can be empty, so the whole pool is the job title.
        {"skills": [], "responsibilities": [], "accountabilities": []},
        {},
        {"skills": ["Python"]},
        {"skills": ["Python", "FastAPI"], "responsibilities": ["Build APIs"]},
        {"skills": ["a", "b", "c", "d"]},
        {"skills": ["a", "b", "c", "d", "e"]},
        # Duplicates collapse to one distinct term, which is the shape that
        # actually hung the old padding loop: the pool is non-empty but has
        # nothing new to offer.
        {"skills": ["Python", "python", "PYTHON"]},
    ],
)
def test_a_thin_jd_still_covers_every_aspect_and_terminates(jd_json) -> None:
    """Regression, production 2026-08-01.

    The old `_fallback_framework` padded to a per-aspect floor by cycling the JD
    pool and appending only when the generated name was new. Once every pool
    term had been used it regenerated names it had already rejected and made no
    further progress, so any JD with fewer distinct terms than the floor spun
    forever. The Celery task held a worker slot until the 600s soft time limit
    and then autoretried five times, starving every other assessment task behind
    it -- which is what "assessments are not available" looked like.

    Draft v4 removed the floor that forced the padding, so the loop is gone
    rather than merely bounded. This still asserts termination and coverage,
    because the shape of JD that triggered it has not gone anywhere.

    pytest-timeout is not a dependency here, so termination is asserted by the
    test simply returning: a hang fails the run by never finishing.
    """
    job = _job()
    job.jd_json = jd_json
    rows = ppi._ensure_every_aspect(
        ppi._normalise(ppi._fallback_framework(job), maximum_total=28), job, None
    )
    assert {row["category"] for row in rows} == set(ppi.CATEGORIES)
    # Every name must be distinct within its aspect, or the matrix saves fewer
    # rows than it counted.
    keys = [(row["category"], row["name"].casefold()) for row in rows]
    assert len(keys) == len(set(keys))
    assert not any(
        row["category"] == ppi.CATEGORY_BEHAVIOURAL
        and ppi.is_forbidden_competency(row["name"])
        for row in rows
    )


@pytest.mark.asyncio
async def test_a_thin_jd_survives_a_total_llm_outage(monkeypatch) -> None:
    """The same regression, through the real entry point."""
    async def _boom(*a, **k):
        raise RuntimeError("all providers down")

    monkeypatch.setattr(ppi.llm_router, "chat_completion", _boom)
    job = _job()
    job.jd_json = {"skills": [], "responsibilities": [], "accountabilities": []}
    rows = await ppi.generate_framework(_StubSession(), job)
    assert {row.category for row in rows} == set(ppi.CATEGORIES)


@pytest.mark.asyncio
async def test_a_generated_culture_competency_is_dropped_not_stored(monkeypatch) -> None:
    """Dropped rather than rejected: refusing the whole generation over one bad
    entry would leave the recruiter staring at an empty screen."""
    async def _chat(*a, **k):
        return (
            '{"competencies":['
            '{"category":"behavioural","name":"Culture fit","required_level":"Matching"},'
            '{"category":"behavioural","name":"Ownership","required_level":"Matching"}]}'
        )

    monkeypatch.setattr(ppi.llm_router, "chat_completion", _chat)
    rows = await ppi.generate_framework(_StubSession(), _job())
    assert not any(ppi.is_forbidden_competency(row.name) for row in rows)
    assert "Ownership" in [row.name for row in rows]


@pytest.mark.asyncio
async def test_regeneration_is_refused_once_a_matrix_exists(monkeypatch) -> None:
    """A Celery redelivery must not discard a matrix a human has edited."""
    calls = []

    async def _chat(*a, **k):
        calls.append(1)
        return '{"competencies":[]}'

    monkeypatch.setattr(ppi.llm_router, "chat_completion", _chat)
    existing = [SimpleNamespace(id=uuid.uuid4(), category=ppi.CATEGORY_MUST_HAVE,
                                name="Kept", ordinal=1, is_active=True)]
    rows = await ppi.generate_framework(_StubSession(existing), _job())
    assert [row.name for row in rows] == ["Kept"]
    assert calls == []


def test_required_levels_never_offer_not_matching() -> None:
    """A job that requires nothing of an item would not list it."""
    assert "Not Matching" not in ppi.REQUIRED_LEVEL_SCORES
    assert ppi.required_level_score("Highly Matching") == 95
    assert ppi.required_level_score("nonsense") == ppi.DEFAULT_REQUIRED_LEVEL


# ── The save gate (spec §5.3) ────────────────────────────────────────────────

def _competency(category: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(category=category, name=name, is_active=True)


def _small_matrix() -> list[SimpleNamespace]:
    return [_competency(category, f"{category}-1") for category in ppi.CATEGORIES]


def test_a_three_item_matrix_can_be_saved() -> None:
    """One item per aspect is enough. Draft v4 removed the floor of five, and
    this is the assertion that the removal is real rather than aspirational."""
    ok, reason = ppi.matrix_is_complete(_small_matrix(), "non_managerial")
    assert ok and reason is None


def test_an_empty_aspect_blocks_the_save_and_says_which() -> None:
    rows = [row for row in _small_matrix() if row.category != ppi.CATEGORY_NICE_TO_HAVE]
    ok, reason = ppi.matrix_is_complete(rows, "non_managerial")
    assert not ok
    assert "Nice-to-have" in reason


def test_a_matrix_above_the_grade_ceiling_blocks_the_save_and_says_how_many() -> None:
    """Every item is probed at least once, so a matrix bigger than the grade
    allows questions would grade a candidate on criteria nobody asked them
    about. The refusal names the number to remove rather than truncating."""
    ceiling = ppi.max_questions("cxo")
    rows = _small_matrix() + [
        _competency(ppi.CATEGORY_MUST_HAVE, f"Extra {index}")
        for index in range(ceiling)
    ]
    ok, reason = ppi.matrix_is_complete(rows, "cxo")
    assert not ok
    assert str(len(rows) - ceiling) in reason
    # The same matrix is perfectly saveable at a grade that asks more questions.
    assert ppi.matrix_is_complete(rows, "non_managerial")[0] is (
        len(rows) <= ppi.max_questions("non_managerial")
    )


def test_a_hand_typed_culture_competency_blocks_the_save() -> None:
    """The Hiring Manager's Edit control can type anything, so the refusal is
    enforced at save as well as at generation."""
    rows = _small_matrix() + [_competency(ppi.CATEGORY_BEHAVIOURAL, "Culture fit")]
    ok, reason = ppi.matrix_is_complete(rows, "non_managerial")
    assert not ok
    assert "Culture" in reason


# ── Per-candidate questions (spec §5.6) ──────────────────────────────────────

def _matrix(per_aspect: int = 5) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(id=uuid.uuid4(), category=category,
                        name=f"{category}-{index}", ordinal=index + 1)
        for category in ppi.CATEGORIES
        for index in range(per_aspect)
    ]


def test_allocation_probes_every_item_at_least_once() -> None:
    competencies = _matrix()
    plan = ppi._allocate(competencies, 20, "non_managerial")
    assert len(plan) == 20
    assert {row.name for row in plan} == {row.name for row in competencies}


def test_allocation_spends_the_remainder_on_the_most_weighted_aspect() -> None:
    """The typical split is illustrative and nothing enforces it, but it is
    what decides where a SPARE question goes: whichever aspect the client's
    table asks the most of."""
    competencies = _matrix()
    plan = ppi._allocate(competencies, 20, "non_managerial")  # 15 items, 5 spare
    extras = plan[15:]
    split = ppi.typical_split("non_managerial")
    heaviest = max(ppi.CATEGORIES, key=lambda category: split[category][1])
    assert all(row.category == heaviest for row in extras)


def test_a_cxo_gets_fewer_questions_than_a_junior_candidate() -> None:
    assert ppi.max_questions("cxo") < ppi.max_questions("non_managerial")
    assert ppi.min_questions("cxo") < ppi.min_questions("non_managerial")


# ── Mandatory application fields (spec §7) ───────────────────────────────────

def _complete() -> dict:
    return {
        "current_ctc": "18 LPA",
        "expected_ctc": "26 LPA",
        "notice_period": "60 days",
        "joining_date": "2026-09-01",
        "document_readiness": "All documents ready",
        "role_interest": "I want to work on larger distributed systems at scale.",
    }


def test_a_complete_submission_has_nothing_missing() -> None:
    assert av.missing_fields(_complete()) == []


@pytest.mark.parametrize("key", av.MANDATORY_KEYS)
def test_every_field_is_mandatory(key) -> None:
    payload = _complete()
    payload.pop(key)
    assert av.missing_fields(payload)


def test_a_one_word_answer_on_interest_is_not_enough() -> None:
    payload = {**_complete(), "role_interest": "money"}
    assert av.missing_fields(payload)


def test_unknown_keys_are_dropped_rather_than_stored() -> None:
    """This blob renders straight into the report, so it accepts exactly the
    fields the form defines and nothing a caller invents."""
    stored = av.normalise({**_complete(), "internal_note": "<script>", "score": 9})
    assert set(stored) == set(av.MANDATORY_KEYS)


def test_the_open_text_field_reaches_the_report_verbatim() -> None:
    words = "I have followed this team's work on streaming for two years."
    stored = av.normalise({**_complete(), "role_interest": words})
    assert stored["role_interest"] == words
