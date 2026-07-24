"""Tests for AI job-description generation (FR-3.3 Path A).

The llm_router is monkeypatched in every test so NO network call is made.
Covers: valid LLM JSON -> JDIn-shaped dict; prose/code-fence tolerance;
list coercion; the corrective retry; and the deterministic template fallback
when the provider chain is unavailable (must still be a valid JD shape).
"""
import pytest

from app.services import jd_generation

_JD_KEYS = {
    "reporting_to",
    "reportees",
    "role",
    "responsibilities",
    "accountabilities",
    "education",
    "skills",
    "experience_years",
}

_SAMPLE_BRIEF = {
    "title": "Senior Backend Engineer",
    "requirements": ["Design scalable APIs", "Mentor juniors"],
    "skills": ["Python", "FastAPI", "Postgres"],
    "experience": "5",
    "company_context": "Hanulisa Technologies builds recruitment software.",
    "department": "Engineering",
    "level": "Senior",
}


def _assert_valid_jd_shape(jd: dict):
    assert isinstance(jd, dict)
    assert set(jd.keys()) == _JD_KEYS
    assert isinstance(jd["skills"], list)
    assert all(isinstance(s, str) for s in jd["skills"])
    assert isinstance(jd["responsibilities"], list)
    assert all(isinstance(s, str) for s in jd["responsibilities"])
    assert isinstance(jd["accountabilities"], list)
    assert jd["experience_years"] is None or isinstance(jd["experience_years"], int)
    assert jd["reportees"] is None or isinstance(jd["reportees"], int)


@pytest.mark.asyncio
async def test_generate_jd_valid_llm_json(monkeypatch):
    async def _ok(*a, **k):
        return (
            '{"reporting_to": "Engineering Manager", "reportees": 3, '
            '"role": "Own the backend platform.", '
            '"responsibilities": ["Build APIs", "Review code"], '
            '"accountabilities": ["Uptime"], '
            '"education": "B.E. Computer Science or equivalent", '
            '"skills": ["Python", "FastAPI"], "experience_years": 5}'
        )

    monkeypatch.setattr(jd_generation.llm_router, "chat_completion", _ok)
    jd = await jd_generation.generate_job_description(_SAMPLE_BRIEF)
    _assert_valid_jd_shape(jd)
    assert jd["reporting_to"] == "Engineering Manager"
    assert jd["reportees"] == 3
    assert jd["skills"] == ["Python", "FastAPI"]
    assert jd["experience_years"] == 5


@pytest.mark.asyncio
async def test_generate_jd_tolerates_code_fence_and_coerces(monkeypatch):
    # Model wraps JSON in a markdown fence, returns skills as a blob string and
    # experience_years as "8 years"; all must be coerced.
    async def _fenced(*a, **k):
        return (
            "```json\n"
            '{"role": "Lead the data team", '
            '"responsibilities": "Own pipelines\\nMentor analysts", '
            '"skills": "SQL, dbt, Airflow", '
            '"experience_years": "8 years", "reportees": "4"}\n'
            "```"
        )

    monkeypatch.setattr(jd_generation.llm_router, "chat_completion", _fenced)
    jd = await jd_generation.generate_job_description(_SAMPLE_BRIEF)
    _assert_valid_jd_shape(jd)
    assert jd["skills"] == ["SQL", "dbt", "Airflow"]
    assert jd["experience_years"] == 8
    assert jd["reportees"] == 4
    assert len(jd["responsibilities"]) == 2


@pytest.mark.asyncio
async def test_generate_jd_corrective_retry(monkeypatch):
    calls = {"n": 0}

    async def _prose_then_json(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return "Sure! Here is a great job description for you."
        return (
            '{"role": "Backend engineer role", '
            '"responsibilities": ["Ship features"], "skills": ["Go"]}'
        )

    monkeypatch.setattr(jd_generation.llm_router, "chat_completion", _prose_then_json)
    jd = await jd_generation.generate_job_description(_SAMPLE_BRIEF)
    assert calls["n"] == 2  # first response unparseable -> one corrective retry
    _assert_valid_jd_shape(jd)
    assert jd["skills"] == ["Go"]


@pytest.mark.asyncio
async def test_generate_jd_falls_back_to_template_when_unavailable(monkeypatch):
    async def _boom(*a, **k):
        raise jd_generation.llm_router.LLMUnavailableError("all providers down")

    monkeypatch.setattr(jd_generation.llm_router, "chat_completion", _boom)
    jd = await jd_generation.generate_job_description(_SAMPLE_BRIEF)
    _assert_valid_jd_shape(jd)
    # Template is built from the brief and clearly marked for HR review.
    assert jd_generation.TEMPLATE_NOTICE in jd["role"]
    assert "Senior Backend Engineer" in jd["role"]
    assert jd["skills"] == ["Python", "FastAPI", "Postgres"]
    # requirements become responsibilities in the template
    assert "Design scalable APIs" in jd["responsibilities"]
    assert jd["experience_years"] == 5


@pytest.mark.asyncio
async def test_generate_jd_unusable_output_falls_back(monkeypatch):
    # Valid JSON object but no role/responsibilities/skills -> template fallback.
    async def _empty(*a, **k):
        return '{"reporting_to": "Someone"}'

    monkeypatch.setattr(jd_generation.llm_router, "chat_completion", _empty)
    jd = await jd_generation.generate_job_description(_SAMPLE_BRIEF)
    _assert_valid_jd_shape(jd)
    assert jd_generation.TEMPLATE_NOTICE in jd["role"]


@pytest.mark.asyncio
async def test_generate_jd_unexpected_llm_error_falls_back(monkeypatch):
    async def _explode(*a, **k):
        raise RuntimeError("socket exploded")

    monkeypatch.setattr(jd_generation.llm_router, "chat_completion", _explode)
    jd = await jd_generation.generate_job_description(_SAMPLE_BRIEF)
    _assert_valid_jd_shape(jd)
    assert jd_generation.TEMPLATE_NOTICE in jd["role"]


@pytest.mark.asyncio
async def test_generate_jd_empty_brief_never_raises(monkeypatch):
    async def _boom(*a, **k):
        raise jd_generation.llm_router.LLMUnavailableError("down")

    monkeypatch.setattr(jd_generation.llm_router, "chat_completion", _boom)
    jd = await jd_generation.generate_job_description({})
    _assert_valid_jd_shape(jd)
