"""AI personalized outreach email composition (FR-5.2 / FR-5.3).

When staff select top candidates and click "proceed to next round", this
service composes a warm, professional, personalized email (candidate name, job
role, company). It returns the CONTENT only ({subject, html, text}); a separate
Celery task sends it via SMTP  -  this module is a pure service (no DB, no send).

The LLM is routed through `llm_router` with provider/key fallback (claude.md
rule 9). The model is asked for a plain-text subject + body; this module builds
the HTML itself and HTML-escapes every interpolated value, so a model that
emits stray markup can never inject unescaped HTML. If the provider chain is
unavailable, a clean deterministic templated email is returned  -  never raises.
"""
from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path
from typing import Any

from langchain_core.prompts import PromptTemplate

from app.services import llm_router
from app.prompts import registry

logger = logging.getLogger(__name__)

# Task-type routing: outreach copy is short, but it is prose a candidate reads
# over the client's name, so it runs on the reasoning tier rather than the cheaper one
# (config/llm_providers.MODEL_FOR_TASK, where that choice is argued).
_ROLE_HINT = "email_composition"

# Word-count discipline (sprint requirement): every outreach body lands in
# [WORD_MIN, WORD_MAX]. Enforcement is layered  -  ask the model for it, validate,
# allow ONE corrective regeneration, then deterministically pad/trim so the
# guarantee never depends on the LLM. The corrective regeneration shares the
# single retry budget with JSON-shape repair: at most 2 LLM calls per email.
WORD_MIN = 150
WORD_MAX = 200

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "email_generation.txt"
_EMAIL_PROMPT = PromptTemplate.from_template(_PROMPT_PATH.read_text(encoding="utf-8"))

#: Text in `app/prompts/outreach_email_system.txt`, loaded through the registry so a
#: wording change is a versioned diff in a prompt file rather than a string
#: literal in a module of code. What is sent is unchanged.
_SYSTEM_PROMPT = registry.render(
    "outreach_email_system", word_min=WORD_MIN, word_max=WORD_MAX
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


def _candidate_evidence(candidate: dict, key: str) -> str:
    value = str(candidate.get(key) or "").strip()
    return value or "No specific evidence was recorded for this category."


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


# ── Word-count discipline (150–200 words) ────────────────────────────────────

_SIGNOFF_RE = re.compile(
    r"^(warm regards|kind regards|regards|best regards|best|sincerely|thanks|"
    r"thank you|cheers)\b",
    re.IGNORECASE,
)

# Deterministic, content-neutral sentences used to pad a too-short body. Each
# is ~25 words; they are appended in order until the body reaches WORD_MIN.
_PADDING_SENTENCES = [
    "The team would love to hear more about the work you have led recently, "
    "the kinds of problems you most enjoy solving, and where you would like "
    "to grow next in your career.",
    "If you decide to move forward, we will walk you through every stage of "
    "the process in advance, so you always know what to expect from us and "
    "roughly when to expect it.",
    "We are glad to work around your current commitments when we schedule "
    "conversations, and we can share more detail on the team, the roadmap, "
    "and the way people work together day to day.",
    "Should anything in the role description need clarifying before you "
    "decide, simply reply to this email and one of our recruiters will come "
    "back to you on the same working day.",
    "We know that considering a new opportunity takes time and thought, so "
    "please do ask us anything that would help you weigh this one properly "
    "against everything else on your plate.",
    "Whatever you decide, we appreciate the time you have already given to "
    "this conversation and we will keep you informed rather than leaving you "
    "waiting without an update from our side.",
]


def _word_count(text: str) -> int:
    return len(text.split())


def _split_signoff(body: str) -> tuple[list[str], str | None]:
    """Split a body into its content paragraphs and an optional sign-off block."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    if len(paras) > 1 and _SIGNOFF_RE.match(paras[-1]) and _word_count(paras[-1]) <= 12:
        return paras[:-1], paras[-1]
    return paras, None


def _sentences(paragraph: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", paragraph.strip())
    return [p for p in parts if p]


def _trim_paragraphs(paras: list[str], budget: int) -> list[str]:
    """Keep whole sentences, in order, until `budget` words are used up."""
    kept: list[str] = []
    used = 0
    for para in paras:
        kept_sentences: list[str] = []
        for sentence in _sentences(para):
            n = _word_count(sentence)
            if used + n > budget and (kept or kept_sentences):
                break
            kept_sentences.append(sentence)
            used += n
        if kept_sentences:
            kept.append(" ".join(kept_sentences))
        if used >= budget:
            break
    return kept or paras[:1]


def enforce_word_count(body: str) -> str:
    """Deterministically bring an email body into [WORD_MIN, WORD_MAX] words.

    Padding appends neutral, factually-safe sentences before the sign-off;
    trimming drops whole sentences from the end. Never raises  -  this is the
    last line of defence after the model has had its one corrective attempt.
    """
    paras, signoff = _split_signoff(body)
    signoff_words = _word_count(signoff) if signoff else 0

    total = sum(_word_count(p) for p in paras) + signoff_words
    for sentence in _PADDING_SENTENCES:
        if total >= WORD_MIN:
            break
        paras.append(sentence)
        total += _word_count(sentence)

    if total > WORD_MAX:
        paras = _trim_paragraphs(paras, max(WORD_MAX - signoff_words, 1))

    return "\n\n".join(paras + ([signoff] if signoff else []))


def _within_word_range(body: str) -> bool:
    return WORD_MIN <= _word_count(body) <= WORD_MAX


# ── Deterministic fallback ───────────────────────────────────────────────────


def _template_content(
    name: str,
    role: str,
    company: str,
    apply_link: str | None,
    kind: str,
    candidate: dict,
) -> dict:
    """A clean, warm templated email used when the LLM is unavailable.

    Values are interpolated into a plain-text body; `_build_html` escapes them
    when constructing the HTML (claude.md rule 9: degrade, never crash)."""
    if kind == "next_round":
        skills = _candidate_evidence(candidate, "skills_comment")
        experience = _candidate_evidence(candidate, "experience_comment")
        subject = f"Next steps for the {role} role at {company}"
        body = (
            f"Hi {name},\n\n"
            f"Thank you for your interest in the {role} position at {company}. "
            f"Our review highlighted that {skills} We also noted that "
            f"{experience} Together, those strengths give us useful evidence "
            "that your background could translate well to this opportunity. "
            "We would like to invite you to the next round of our selection "
            "process.\n\n"
            f"The next round is a focused conversation about the {role} remit "
            f"at {company}: the scope of the work, the people you would "
            "partner with, and the outcomes the role is measured on. It is "
            "just as much a chance for you to ask us questions as it is for us "
            "to learn more about you.\n\n"
            "We will follow up shortly with scheduling options and everything "
            "you need to prepare. If there is anything you would like to know "
            "before then, or if a particular time of day suits you better, "
            "simply reply to this email and we will do our best to "
            "accommodate.\n\n"
            f"Warm regards,\n{company} Talent Team"
        )
    else:
        subject = f"An update on your application for {role} at {company}"
        body = (
            f"Hi {name},\n\n"
            f"Thank you for your interest in the {role} position at {company}. "
            "We wanted to write to you directly with an update on where your "
            "application currently stands rather than leave you waiting "
            "without news.\n\n"
            "Your profile is with our hiring team now, and we will share "
            "concrete next steps as soon as they are confirmed. In the "
            "meantime, nothing is required from you.\n\n"
            f"Warm regards,\n{company} Talent Team"
        )
    body = enforce_word_count(body)
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


def _parse_subject_body(raw: str) -> tuple[str, str] | None:
    """Return (subject, body) from a model response, or None if unusable."""
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
    return subject, body


def _assemble(subject: str, body: str, apply_link: str | None) -> dict:
    """Final assembly  -  word count is enforced here, so no path escapes it."""
    body = enforce_word_count(body)
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

    Never raises on LLM/content problems  -  if the provider chain is unavailable
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

    user_prompt = _EMAIL_PROMPT.format(
        candidate_name=name,
        job_title=role,
        company_name=company_name,
        company_culture=str(company.get("culture") or "Not provided").strip(),
        skills_comment=_candidate_evidence(candidate, "skills_comment"),
        experience_comment=_candidate_evidence(candidate, "experience_comment"),
        role_comment=_candidate_evidence(candidate, "role_comment"),
        education_comment=_candidate_evidence(candidate, "education_comment"),
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    def _fallback() -> dict:
        return _template_content(
            name, role, company_name, apply_link, kind, candidate
        )

    try:
        raw = await llm_router.chat_completion(
            _ROLE_HINT, messages, response_format_json=True
        )
    except llm_router.LLMUnavailableError:
        logger.warning("outreach_content.llm_unavailable  -  deterministic template email")
        return _fallback()
    except Exception as exc:  # noqa: BLE001  -  never crash the caller on the LLM
        logger.warning("outreach_content.llm_error error=%s", type(exc).__name__)
        return _fallback()

    parsed = _parse_subject_body(raw)
    if parsed is not None and _within_word_range(parsed[1]):
        return _assemble(parsed[0], parsed[1], apply_link)

    # ONE corrective regeneration  -  for a broken JSON shape OR a body outside
    # the 150–200 word band. Whichever failed, this is the only retry.
    if parsed is None:
        corrective = (
            "Your previous response was not valid JSON in the required shape. "
            'Re-emit ONLY a JSON object: {"subject": "<line>", "body": "<plain '
            'text, no HTML, no signature omitted>"}. No prose, no markdown.'
        )
    else:
        corrective = (
            f"Your previous body was {_word_count(parsed[1])} words. Rewrite the "
            f"email so the body is between {WORD_MIN} and {WORD_MAX} words, "
            "keeping it personalized to the same candidate, role and company. "
            'Re-emit ONLY the JSON object {"subject": ..., "body": ...}.'
        )
    retry_messages = messages + [
        {"role": "assistant", "content": raw},
        {"role": "user", "content": corrective},
    ]
    def _salvage() -> dict:
        """Prefer the model's own (merely mis-sized) content over the template."""
        if parsed is not None:
            return _assemble(parsed[0], parsed[1], apply_link)
        return _fallback()

    try:
        raw_retry = await llm_router.chat_completion(
            _ROLE_HINT, retry_messages, response_format_json=True
        )
    except llm_router.LLMUnavailableError:
        logger.warning("outreach_content.llm_unavailable_on_retry  -  salvaging")
        return _salvage()
    except Exception as exc:  # noqa: BLE001
        logger.warning("outreach_content.llm_retry_error error=%s", type(exc).__name__)
        return _salvage()

    retried = _parse_subject_body(raw_retry)
    if retried is not None:
        # Retry budget spent: whatever came back is trimmed/padded to spec.
        if not _within_word_range(retried[1]):
            logger.info(
                "outreach_content.word_count_adjusted words=%d",
                _word_count(retried[1]),
            )
        return _assemble(retried[0], retried[1], apply_link)

    logger.warning("outreach_content.unparseable_after_retry  -  salvaging")
    return _salvage()
