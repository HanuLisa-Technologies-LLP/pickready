"""AI personalized outreach email composition (FR-5.2 / FR-5.3).

When staff select top candidates and click "proceed to next round", this
service composes a warm, professional, personalized email (candidate name, job
role, company). It returns the CONTENT only ({subject, html, text}); a separate
Celery task sends it via SMTP — this module is a pure service (no DB, no send).

The LLM is routed through `llm_router` with provider/key fallback (claude.md
rule 9). The model is asked for a plain-text subject + body; this module builds
the HTML itself and HTML-escapes every interpolated value, so a model that
emits stray markup can never inject unescaped HTML. If the provider chain is
unavailable, a clean deterministic templated email is returned — never raises.
"""
from __future__ import annotations

import html
import json
import logging
import re
from typing import Any

from app.services import llm_router

logger = logging.getLogger(__name__)

_ROLE_HINT = "extraction"  # content generation, not latency-sensitive

_SYSTEM_PROMPT = (
    "You are a warm, professional recruitment coordinator writing a short "
    "outreach email to a candidate. Keep it concise (2-3 short paragraphs), "
    "personable, and free of hype. Address the candidate by name, reference "
    "the specific job role and company, and — for a 'next_round' invitation — "
    "invite them to continue to the next round of the process.\n"
    "Respond with JSON ONLY, exactly this shape:\n"
    '{"subject": "<email subject line>", '
    '"body": "<plain-text email body, paragraphs separated by blank lines; '
    'do NOT include a signature block or any HTML>"}\n'
    "No prose outside the JSON."
)


# ── Field extraction ─────────────────────────────────────────────────────────


def _candidate_name(candidate: dict) -> str:
    for key in ("name", "full_name", "display_name"):
        v = candidate.get(key)
        if v and str(v).strip():
            return str(v).strip()
    first = str(candidate.get("first_name") or "").strip()
    last = str(candidate.get("last_name") or "").strip()
    combined = " ".join(p for p in (first, last) if p)
    if combined:
        return combined
    email = str(candidate.get("email") or "").strip()
    if email:
        return email.split("@")[0]
    return "there"


def _job_role(job: dict) -> str:
    for key in ("role", "title", "job_title", "name"):
        v = job.get(key)
        if v and str(v).strip():
            return str(v).strip()
    return "the role"


def _company_name(company: dict) -> str:
    for key in ("name", "company_name", "display_name"):
        v = company.get(key)
        if v and str(v).strip():
            return str(v).strip()
    return "our company"


def _apply_link(job: dict, candidate: dict) -> str | None:
    for source in (job, candidate):
        for key in ("apply_link", "link", "url"):
            v = source.get(key)
            if v and str(v).strip():
                return str(v).strip()
    return None


# ── HTML assembly (all interpolation escaped here) ───────────────────────────


def _paragraphs(body: str) -> list[str]:
    """Split a plain-text body into paragraphs on blank lines."""
    blocks = re.split(r"\n\s*\n", body.strip())
    return [re.sub(r"\s*\n\s*", " ", b.strip()) for b in blocks if b.strip()]


def _build_html(body: str, apply_link: str | None) -> str:
    """Build a simple, safe HTML email. Every dynamic value is HTML-escaped."""
    paras = _paragraphs(body) or [body.strip()]
    parts = [
        f'<p style="margin:0 0 16px;line-height:1.5;">{html.escape(p)}</p>'
        for p in paras
    ]
    if apply_link:
        safe_href = html.escape(apply_link, quote=True)
        parts.append(
            '<p style="margin:24px 0;">'
            f'<a href="{safe_href}" '
            'style="display:inline-block;padding:10px 20px;background:#111;'
            'color:#fff;text-decoration:none;border-radius:6px;">'
            "Continue to the next round</a></p>"
        )
    inner = "\n".join(parts)
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        f'color:#111;max-width:560px;margin:0 auto;">{inner}</div>'
    )


def _build_text(body: str, apply_link: str | None) -> str:
    text = body.strip()
    if apply_link:
        text = f"{text}\n\nContinue to the next round: {apply_link}"
    return text


# ── Deterministic fallback ───────────────────────────────────────────────────


def _template_content(
    name: str, role: str, company: str, apply_link: str | None, kind: str
) -> dict:
    """A clean, warm templated email used when the LLM is unavailable.

    Values are interpolated into a plain-text body; `_build_html` escapes them
    when constructing the HTML (claude.md rule 9: degrade, never crash)."""
    if kind == "next_round":
        subject = f"Next steps for the {role} role at {company}"
        body = (
            f"Hi {name},\n\n"
            f"Thank you for your interest in the {role} position at {company}. "
            "We were impressed by your background and would like to invite you "
            "to the next round of our selection process.\n\n"
            "We will be in touch shortly with the details. In the meantime, "
            "please let us know if you have any questions.\n\n"
            f"Warm regards,\n{company} Talent Team"
        )
    else:
        subject = f"An update on your application for {role} at {company}"
        body = (
            f"Hi {name},\n\n"
            f"Thank you for your interest in the {role} position at {company}. "
            "We are reaching out with an update on your application and will "
            "share next steps soon.\n\n"
            f"Warm regards,\n{company} Talent Team"
        )
    return {
        "subject": subject,
        "html": _build_html(body, apply_link),
        "text": _build_text(body, apply_link),
    }


# ── LLM result parsing ───────────────────────────────────────────────────────


def _loads_lenient(raw: str) -> Any:
    text = (raw or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise ValueError("no JSON object found in response")


def _parse_email(raw: str, apply_link: str | None) -> dict | None:
    try:
        data = _loads_lenient(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    subject = str(data.get("subject") or "").strip()
    body = str(data.get("body") or "").strip()
    if not subject or not body:
        return None
    return {
        "subject": subject,
        "html": _build_html(body, apply_link),
        "text": _build_text(body, apply_link),
    }


# ── Public API ───────────────────────────────────────────────────────────────


async def generate_outreach_email(
    candidate: dict, job: dict, company: dict, kind: str = "next_round"
) -> dict:
    """Compose a personalized outreach email.

    Personalized by candidate name, job role/title, and company name. Returns
    {"subject": str, "html": str, "text": str}. Any apply link passed via
    job["apply_link"] (or "link"/"url", on job or candidate) is rendered as a
    button in the HTML and a URL line in the text.

    Never raises on LLM/content problems — if the provider chain is unavailable
    or the output is unusable, a clean deterministic templated email is
    returned. All interpolated values are HTML-escaped in the HTML output.
    """
    candidate = candidate or {}
    job = job or {}
    company = company or {}

    name = _candidate_name(candidate)
    role = _job_role(job)
    company_name = _company_name(company)
    apply_link = _apply_link(job, candidate)

    user_payload = {
        "candidate_name": name,
        "job_role": role,
        "company": company_name,
        "kind": kind,
    }
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(user_payload, default=str)},
    ]

    def _fallback() -> dict:
        return _template_content(name, role, company_name, apply_link, kind)

    try:
        raw = await llm_router.chat_completion(
            _ROLE_HINT, messages, response_format_json=True
        )
    except llm_router.LLMUnavailableError:
        logger.warning("outreach_content.llm_unavailable — deterministic template email")
        return _fallback()
    except Exception as exc:  # noqa: BLE001 — never crash the caller on the LLM
        logger.warning("outreach_content.llm_error error=%s", type(exc).__name__)
        return _fallback()

    email = _parse_email(raw, apply_link)
    if email is not None:
        return email

    corrective = (
        "Your previous response was not valid JSON in the required shape. "
        'Re-emit ONLY a JSON object: {"subject": "<line>", "body": "<plain '
        'text, no HTML, no signature omitted>"}. No prose, no markdown.'
    )
    retry_messages = messages + [
        {"role": "assistant", "content": raw},
        {"role": "user", "content": corrective},
    ]
    try:
        raw_retry = await llm_router.chat_completion(
            _ROLE_HINT, retry_messages, response_format_json=True
        )
    except llm_router.LLMUnavailableError:
        logger.warning(
            "outreach_content.llm_unavailable_on_retry — deterministic template email"
        )
        return _fallback()
    except Exception as exc:  # noqa: BLE001
        logger.warning("outreach_content.llm_retry_error error=%s", type(exc).__name__)
        return _fallback()

    email = _parse_email(raw_retry, apply_link)
    if email is not None:
        return email

    logger.warning("outreach_content.unparseable_after_retry — deterministic template email")
    return _fallback()
