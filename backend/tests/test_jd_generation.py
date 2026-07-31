"""Tests for AI job-description generation (FR-3.3 Path A).

The llm_router is monkeypatched in every test so NO network call is made.
Covers: valid LLM JSON -> JDIn-shaped dict; prose/code-fence tolerance;
list coercion; the corrective retry; and the deterministic template fallback
when the provider chain is unavailable (must still be a valid JD shape).
"""
import pytest

from app.services import jd_generation

_JD_KEYS = {
    "description",
    "reporting_to",
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
    # `reportees` was removed entirely on 2026-07-28: it must NOT reappear.
    assert "reportees" not in jd


@pytest.mark.asyncio
async def test_generate_jd_valid_llm_json(monkeypatch):
    async def _ok(*a, **k):
        return (
            '{"description": "Build reliable recruitment platform services.", '
            '"reporting_to": "Engineering Manager", "reportees": 3, '
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


# ── The unified JD document (2026-07-28) ─────────────────────────────────────
#
# The recruiter now gets ONE Markdown document with seven fixed sections
# instead of a section-per-text-box form. These tests pin the three properties
# the rest of the platform relies on: the document round-trips back into the
# `jd_json` shape, no em dash ever survives to a candidate, and an unreachable
# provider chain still yields a usable draft.

_DOC_BRIEF = {
    "title": "Senior Backend Engineer",
    "skills": ["Python", "FastAPI", "Postgres"],
    "experience_min_years": 4,
    "experience_max_years": 7,
    "grade": "non_managerial",
    "reporting_to": "Engineering Manager",
    "department": "Engineering",
}

_GOOD_DOCUMENT = """## Description

We are hiring a Senior Backend Engineer to build recruitment software.

## Role

You own the backend platform and report to the Engineering Manager.

## Responsibilities

- Design and ship APIs
- Review code and mentor engineers

## Accountabilities

- Platform reliability

## Education

A degree in computer science or equivalent practical experience.

## Skills

- Python
- FastAPI
- Postgres

## Experience

This role suits someone with 4 to 7 years of relevant experience.
"""


def test_parse_jd_markdown_round_trips_every_section():
    parsed = jd_generation.parse_jd_markdown(_GOOD_DOCUMENT)
    assert parsed["description"].startswith("We are hiring")
    assert parsed["role"].startswith("You own the backend platform")
    assert parsed["responsibilities"] == [
        "Design and ship APIs",
        "Review code and mentor engineers",
    ]
    assert parsed["accountabilities"] == ["Platform reliability"]
    assert parsed["education"].startswith("A degree")
    assert parsed["skills"] == ["Python", "FastAPI", "Postgres"]
    assert parsed["experience_years"] == 4
    # The removed field never comes back through the parser either.
    assert "reportees" not in parsed


def test_parse_jd_markdown_tolerates_empty_and_unknown_headings():
    empty = jd_generation.parse_jd_markdown("")
    assert empty["skills"] == [] and empty["role"] is None

    odd = jd_generation.parse_jd_markdown(
        "# Description\nA role.\n\n### Compensation\nNot your business.\n"
    )
    # `#` and `###` are accepted (a recruiter edits by hand), and an unknown
    # heading is ignored rather than crashing the parse.
    assert odd["description"] == "A role."
    assert odd["skills"] == []


def test_render_jd_markdown_emits_all_seven_headings():
    document = jd_generation.render_jd_markdown(
        {"role": "Own APIs", "skills": ["Go"], "responsibilities": ["Ship"]},
        min_years=2,
        max_years=5,
    )
    for heading in jd_generation.JD_SECTIONS:
        assert f"## {heading}" in document
    assert "- Go" in document
    assert "2 to 5 years" in document


def test_experience_sentence_handles_every_band_shape():
    assert "4 to 7 years" in jd_generation.experience_sentence(4, 7)
    assert "around 5 years" in jd_generation.experience_sentence(5, 5)
    assert "around 3 years" in jd_generation.experience_sentence(3, None)
    # Neither bound known: still a complete sentence, never a dangling "None".
    assert "None" not in jd_generation.experience_sentence(None, None)


def test_strip_em_dashes_removes_every_dash_variant():
    cleaned = jd_generation.strip_em_dashes("Own the platform — end to end – always")
    assert "—" not in cleaned and "–" not in cleaned
    assert "  " not in cleaned


@pytest.mark.asyncio
async def test_generate_jd_document_uses_llm_markdown(monkeypatch):
    async def _ok(*a, **k):
        return _GOOD_DOCUMENT

    monkeypatch.setattr(jd_generation.llm_router, "chat_completion", _ok)
    result = await jd_generation.generate_jd_document(_DOC_BRIEF)

    assert result["generated_by_ai"] is True
    assert result["jd_markdown"].startswith("## Description")
    # `jd` is DERIVED from the document, so the two cannot contradict.
    assert result["jd"]["skills"] == ["Python", "FastAPI", "Postgres"]
    assert result["jd"]["reporting_to"] == "Engineering Manager"


@pytest.mark.asyncio
async def test_generate_jd_document_strips_fence_and_em_dashes(monkeypatch):
    async def _fenced(*a, **k):
        return "```markdown\n" + _GOOD_DOCUMENT.replace(
            "You own the backend platform", "You own the backend platform — fully"
        ) + "\n```"

    monkeypatch.setattr(jd_generation.llm_router, "chat_completion", _fenced)
    result = await jd_generation.generate_jd_document(_DOC_BRIEF)

    assert not result["jd_markdown"].startswith("```")
    assert "—" not in result["jd_markdown"]


@pytest.mark.asyncio
async def test_generate_jd_document_retries_then_falls_back(monkeypatch):
    calls = {"n": 0}

    async def _prose_then_document(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return "Sure! Here is a job description."
        return _GOOD_DOCUMENT

    monkeypatch.setattr(
        jd_generation.llm_router, "chat_completion", _prose_then_document
    )
    result = await jd_generation.generate_jd_document(_DOC_BRIEF)
    assert calls["n"] == 2  # one corrective retry, not an infinite loop
    assert result["generated_by_ai"] is True


@pytest.mark.asyncio
async def test_generate_jd_document_template_when_llm_unavailable(monkeypatch):
    async def _boom(*a, **k):
        raise jd_generation.llm_router.LLMUnavailableError("all providers down")

    monkeypatch.setattr(jd_generation.llm_router, "chat_completion", _boom)
    result = await jd_generation.generate_jd_document(_DOC_BRIEF)

    assert result["generated_by_ai"] is False
    assert jd_generation.TEMPLATE_NOTICE in result["jd_markdown"]
    # Still a complete, publishable-shaped document the recruiter can edit.
    for heading in jd_generation.JD_SECTIONS:
        assert f"## {heading}" in result["jd_markdown"]
    assert result["jd"]["skills"] == ["Python", "FastAPI", "Postgres"]
    assert "4 to 7 years" in result["jd_markdown"]
    assert "—" not in result["jd_markdown"]


@pytest.mark.asyncio
async def test_generate_jd_document_empty_brief_never_raises(monkeypatch):
    async def _boom(*a, **k):
        raise jd_generation.llm_router.LLMUnavailableError("down")

    monkeypatch.setattr(jd_generation.llm_router, "chat_completion", _boom)
    result = await jd_generation.generate_jd_document({})
    assert result["jd_markdown"].strip()
    assert isinstance(result["jd"]["skills"], list)
