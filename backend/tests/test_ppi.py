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


# ── Culture is refused (spec §6.2) ───────────────────────────────────────────

@pytest.mark.parametrize(
    "name",
    ["Culture", "Culture fit", "CULTURAL ALIGNMENT", "Company culture", "cultural add"],
)
def test_culture_is_refused_in_any_casing(name) -> None:
    assert ppi.is_forbidden_competency(name)


@pytest.mark.parametrize("name", ["Agricultural domain knowledge", "Ownership", ""])
def test_a_legitimate_competency_is_not_caught(name) -> None:
    assert not ppi.is_forbidden_competency(name)


# ── Framework generation ─────────────────────────────────────────────────────

class _StubSession:
    def __init__(self, existing: list | None = None) -> None:
        self.added: list = []
        self._existing = existing or []

    async def execute(self, *a, **k):
        rows = self._existing
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    def add_all(self, rows) -> None:
        self.added.extend(rows)

    def add(self, row) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        return None

    async def get(self, *a, **k):
        return None


def _job() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), title="Backend Engineer",
        assessment_grade="non_managerial", jd_markdown="",
        experience_min_years=3, experience_max_years=6,
        jd_json={
            "skills": ["Python", "PostgreSQL", "Kafka"],
            "responsibilities": ["Design and own service APIs", "Run the on-call rota"],
            "accountabilities": ["Latency stays within the agreed budget"],
        },
        framework_generated_at=None, framework_approved_at=None,
    )


@pytest.mark.asyncio
async def test_generated_framework_meets_the_minimum_in_every_category(monkeypatch) -> None:
    async def _chat(*a, **k):
        return (
            '{"competencies":[{"category":"primary_skill","name":"Python",'
            '"description":"Writes production Python.","required_level":"Highly Matching"}]}'
        )

    monkeypatch.setattr(ppi.llm_router, "chat_completion", _chat)
    job = _job()
    rows = await ppi.generate_framework(_StubSession(), job)
    for category in ppi.CATEGORIES:
        count = sum(1 for row in rows if row.category == category)
        assert count >= ppi.MINIMUM_PER_CATEGORY, category
    assert job.framework_generated_at is not None
    # Generation NEVER approves: the Hiring Manager does (spec §6.3).
    assert job.framework_approved_at is None


@pytest.mark.asyncio
async def test_framework_survives_a_total_llm_outage(monkeypatch) -> None:
    async def _boom(*a, **k):
        raise RuntimeError("all providers down")

    monkeypatch.setattr(ppi.llm_router, "chat_completion", _boom)
    rows = await ppi.generate_framework(_StubSession(), _job())
    for category in ppi.CATEGORIES:
        assert sum(1 for row in rows if row.category == category) >= ppi.MINIMUM_PER_CATEGORY
    # Built from the JD's own words, not from a generic template.
    primary = [row.name for row in rows if row.category == ppi.CATEGORY_PRIMARY]
    assert "Python" in primary


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
async def test_regeneration_is_refused_once_a_framework_exists(monkeypatch) -> None:
    """A Celery redelivery must not discard a framework a human has edited."""
    calls = []

    async def _chat(*a, **k):
        calls.append(1)
        return '{"competencies":[]}'

    monkeypatch.setattr(ppi.llm_router, "chat_completion", _chat)
    existing = [SimpleNamespace(id=uuid.uuid4(), category=ppi.CATEGORY_PRIMARY,
                                name="Kept", ordinal=1, is_active=True)]
    rows = await ppi.generate_framework(_StubSession(existing), _job())
    assert [row.name for row in rows] == ["Kept"]
    assert calls == []


def test_required_levels_never_offer_not_matching() -> None:
    """A job that requires nothing of a competency would not list it."""
    assert "Not Matching" not in ppi.REQUIRED_LEVEL_SCORES
    assert ppi.required_level_score("Highly Matching") == 95
    assert ppi.required_level_score("nonsense") == ppi.DEFAULT_REQUIRED_LEVEL


# ── The save gate (spec §6.3) ────────────────────────────────────────────────

def _competency(category: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(category=category, name=name, is_active=True)


def _full_framework() -> list[SimpleNamespace]:
    return [
        _competency(category, f"{category}-{index}")
        for category in ppi.CATEGORIES
        for index in range(ppi.MINIMUM_PER_CATEGORY)
    ]


def test_a_complete_framework_can_be_saved() -> None:
    ok, reason = ppi.framework_is_complete(_full_framework())
    assert ok and reason is None


def test_a_short_category_blocks_the_save_and_says_which() -> None:
    rows = [row for row in _full_framework() if not row.name.startswith(ppi.CATEGORY_SECONDARY)]
    ok, reason = ppi.framework_is_complete(rows)
    assert not ok
    assert "Secondary Skills" in reason


def test_a_hand_typed_culture_competency_blocks_the_save() -> None:
    """The Hiring Manager's Edit control can type anything, so the refusal is
    enforced at save as well as at generation."""
    rows = _full_framework() + [_competency(ppi.CATEGORY_BEHAVIOURAL, "Culture fit")]
    ok, reason = ppi.framework_is_complete(rows)
    assert not ok
    assert "Culture" in reason


# ── Per-candidate questions (spec §6.4) ──────────────────────────────────────

def test_allocation_probes_every_competency_at_least_once() -> None:
    competencies = [
        SimpleNamespace(id=uuid.uuid4(), category=category, name=f"{category}-{index}", ordinal=index + 1)
        for category in ppi.CATEGORIES
        for index in range(5)
    ]
    plan = ppi._allocate(competencies, 25)
    assert len(plan) == 25
    assert {row.name for row in plan} == {row.name for row in competencies}


def test_allocation_spends_the_remainder_on_primary_skills_first() -> None:
    competencies = [
        SimpleNamespace(id=uuid.uuid4(), category=category, name=f"{category}-{index}", ordinal=index + 1)
        for category in ppi.CATEGORIES
        for index in range(5)
    ]
    plan = ppi._allocate(competencies, 20)  # 15 competencies, 5 spare
    extras = plan[15:]
    assert all(row.category == ppi.CATEGORY_PRIMARY for row in extras)


def test_a_cxo_gets_fewer_questions_than_a_junior_candidate() -> None:
    assert ppi.ppi_question_count("cxo") < ppi.ppi_question_count("non_managerial")


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
