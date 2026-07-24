"""Tests for AI personalized outreach email composition (FR-5.2 / FR-5.3).

The llm_router is monkeypatched so NO network call is made. Covers: valid LLM
output -> subject/html/text with the candidate name present; the apply link
rendered; the deterministic template fallback when the LLM is unavailable; and
that interpolated values are HTML-escaped (injection safety).
"""
import pytest

from app.services import outreach_content

_CANDIDATE = {"name": "Ada Lovelace", "email": "ada@example.com"}
_JOB = {"title": "Staff Engineer"}
_COMPANY = {"name": "Hanulisa Technologies"}


def _assert_email_shape(email: dict):
    assert set(email.keys()) == {"subject", "html", "text"}
    assert all(isinstance(email[k], str) and email[k] for k in email)


@pytest.mark.asyncio
async def test_outreach_valid_llm_output(monkeypatch):
    async def _ok(*a, **k):
        return (
            '{"subject": "Next steps, Ada!", '
            '"body": "Hi Ada Lovelace,\\n\\nWe would love to invite you to the '
            'next round for the Staff Engineer role at Hanulisa Technologies."}'
        )

    monkeypatch.setattr(outreach_content.llm_router, "chat_completion", _ok)
    email = await outreach_content.generate_outreach_email(_CANDIDATE, _JOB, _COMPANY)
    _assert_email_shape(email)
    assert email["subject"] == "Next steps, Ada!"
    assert "Ada Lovelace" in email["text"]
    assert "Ada Lovelace" in email["html"]
    assert "<p" in email["html"]


@pytest.mark.asyncio
async def test_outreach_falls_back_when_unavailable(monkeypatch):
    async def _boom(*a, **k):
        raise outreach_content.llm_router.LLMUnavailableError("all down")

    monkeypatch.setattr(outreach_content.llm_router, "chat_completion", _boom)
    email = await outreach_content.generate_outreach_email(_CANDIDATE, _JOB, _COMPANY)
    _assert_email_shape(email)
    # Deterministic template personalizes name, role, company.
    assert "Ada Lovelace" in email["text"]
    assert "Staff Engineer" in email["text"]
    assert "Hanulisa Technologies" in email["text"]
    assert "next round" in email["text"].lower()


@pytest.mark.asyncio
async def test_outreach_apply_link_rendered(monkeypatch):
    async def _boom(*a, **k):
        raise outreach_content.llm_router.LLMUnavailableError("down")

    monkeypatch.setattr(outreach_content.llm_router, "chat_completion", _boom)
    job = {"title": "Staff Engineer", "apply_link": "https://apply.example.com/x"}
    email = await outreach_content.generate_outreach_email(_CANDIDATE, job, _COMPANY)
    assert "https://apply.example.com/x" in email["text"]
    assert 'href="https://apply.example.com/x"' in email["html"]


@pytest.mark.asyncio
async def test_outreach_html_escapes_interpolated_values(monkeypatch):
    async def _boom(*a, **k):
        raise outreach_content.llm_router.LLMUnavailableError("down")

    monkeypatch.setattr(outreach_content.llm_router, "chat_completion", _boom)
    candidate = {"name": "<script>alert('x')</script>"}
    company = {"name": "Acme & Sons <b>"}
    email = await outreach_content.generate_outreach_email(candidate, _JOB, company)
    # The raw injection must NOT appear unescaped in the HTML.
    assert "<script>" not in email["html"]
    assert "&lt;script&gt;" in email["html"]
    assert "Acme &amp; Sons" in email["html"]


@pytest.mark.asyncio
async def test_outreach_escapes_llm_body(monkeypatch):
    # Even if the model returns markup in the body, the HTML builder escapes it.
    async def _evil(*a, **k):
        return '{"subject": "Hi", "body": "Hello <img src=x onerror=alert(1)>"}'

    monkeypatch.setattr(outreach_content.llm_router, "chat_completion", _evil)
    email = await outreach_content.generate_outreach_email(_CANDIDATE, _JOB, _COMPANY)
    assert "<img" not in email["html"]
    assert "&lt;img" in email["html"]


@pytest.mark.asyncio
async def test_outreach_corrective_retry(monkeypatch):
    calls = {"n": 0}

    async def _prose_then_json(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return "Sure, here you go!"
        return '{"subject": "Onward", "body": "Hi Ada Lovelace, great news."}'

    monkeypatch.setattr(outreach_content.llm_router, "chat_completion", _prose_then_json)
    email = await outreach_content.generate_outreach_email(_CANDIDATE, _JOB, _COMPANY)
    assert calls["n"] == 2
    _assert_email_shape(email)
    assert email["subject"] == "Onward"


@pytest.mark.asyncio
async def test_outreach_name_from_email_when_no_name(monkeypatch):
    async def _boom(*a, **k):
        raise outreach_content.llm_router.LLMUnavailableError("down")

    monkeypatch.setattr(outreach_content.llm_router, "chat_completion", _boom)
    email = await outreach_content.generate_outreach_email(
        {"email": "grace@example.com"}, _JOB, _COMPANY
    )
    assert "grace" in email["text"]


@pytest.mark.asyncio
async def test_outreach_unexpected_error_falls_back(monkeypatch):
    async def _explode(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(outreach_content.llm_router, "chat_completion", _explode)
    email = await outreach_content.generate_outreach_email(_CANDIDATE, _JOB, _COMPANY)
    _assert_email_shape(email)
    assert "Ada Lovelace" in email["text"]
