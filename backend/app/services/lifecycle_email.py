"""The lifecycle emails (2026-07-27 spec §6 and §4.1).

Every one is AI-drafted and editable before it is sent:

  application_confirmation  candidate applied
  assessment_invitation     recruiter selected them for the assessment
  assessment_reminder       assessment started but not finished
  assessment_complete       candidate finished the assessment
  shortlist                 moving forward
  rejected                  not proceeding
  hold                      recruiter pressed Hold
  interview_scheduled       an interview round was booked
  interview_completed       follow-up after an interview
  offer_extended            the team wants to make an offer
  joined                    they accepted — welcome
  question_bank_reminder    INTERNAL — recruiter has not reviewed a new job

There is deliberately NO email for `assessment_in_progress`: it fires when the
candidate opens the assessment, and mailing someone about something they just
did is noise (spec §4.1 marks it "backend event only").

This module is a PURE CONTENT SERVICE, matching the existing
`outreach_content` pattern: it drafts, it never sends and never writes to the
database. The API layer records the draft in `email_log` and the Celery task
delivers it (claude.md rules 4 and 5).

DEGRADATION
-----------
`draft()` never raises. If the whole LLM chain is unavailable, a deterministic
template for that email type is returned instead, flagged
`generated_by_ai=False`. Telling a candidate nothing because a provider is
rate-limited is worse than sending clear, plainer prose — and the recruiter can
edit either one before it goes out.

WORD LABELS AND SCORES
----------------------
No email ever carries a score, percentage, band, or rank; the prompts forbid it
explicitly and `strengths` is passed in as prose, never as numbers. A candidate
must not be able to reverse-engineer their internal rating from an email.
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
from typing import Any

from app import prompts
from app.models.email_log import (
    EMAIL_TYPE_APPLICATION_CONFIRMATION,
    EMAIL_TYPE_ASSESSMENT_COMPLETE,
    EMAIL_TYPE_ASSESSMENT_INVITATION,
    EMAIL_TYPE_INTERVIEW_COMPLETED,
    EMAIL_TYPE_INTERVIEW_SCHEDULED,
    EMAIL_TYPE_JOINED,
    EMAIL_TYPE_OFFER_EXTENDED,
    EMAIL_TYPE_ASSESSMENT_REMINDER,
    EMAIL_TYPE_HOLD,
    EMAIL_TYPE_QUESTION_BANK_REMINDER,
    EMAIL_TYPE_REJECTED,
    EMAIL_TYPE_SHORTLIST,
    EMAIL_TYPE_PROMPTS,
    EMAIL_TYPES,
)
from app.services import llm_router

logger = logging.getLogger(__name__)

_TASK_TYPE = "email_composition"

#: Placeholders each prompt needs, with the default used when a caller omits
#: one. Every prompt is rendered with the full set, so a missing value can
#: never leak a literal "{next_steps}" into a candidate's inbox.
_PROMPT_DEFAULTS: dict[str, dict[str, Any]] = {
    EMAIL_TYPE_APPLICATION_CONFIRMATION: {
        "next_steps": (
            "the team reviews the application, and a short online assessment "
            "follows if the role is a fit"
        ),
    },
    EMAIL_TYPE_ASSESSMENT_REMINDER: {
        "hours_elapsed": "a little while",
        "assessment_link": "",
    },
    EMAIL_TYPE_SHORTLIST: {
        "strengths": "strong, relevant experience for this role",
        "next_steps": "the team will be in touch to arrange an interview",
    },
    EMAIL_TYPE_REJECTED: {},
    EMAIL_TYPE_HOLD: {"hold_days": "10"},
    EMAIL_TYPE_QUESTION_BANK_REMINDER: {
        "recruiter_name": "there",
        "hours_elapsed": "some time",
        "job_link": "",
    },
    EMAIL_TYPE_ASSESSMENT_INVITATION: {
        "assessment_link": "",
        "duration": "about 30 to 40 minutes",
    },
    EMAIL_TYPE_ASSESSMENT_COMPLETE: {"review_days": "5 to 7"},
    EMAIL_TYPE_INTERVIEW_SCHEDULED: {
        "stage_name": "Interview",
        "scheduled_at": "a time the team will confirm",
    },
    EMAIL_TYPE_INTERVIEW_COMPLETED: {"review_days": "5 to 7"},
    EMAIL_TYPE_OFFER_EXTENDED: {
        "next_steps": (
            "the formal offer with the full terms follows separately, and the "
            "team will walk you through it"
        ),
    },
    EMAIL_TYPE_JOINED: {
        "next_steps": (
            "the onboarding team will be in touch with the practical details "
            "before your first day"
        ),
    },
}

#: Subject lines for the deterministic fallback. Kept short and factual — the
#: fallback's job is to be unmistakably clear, not to sound generated.
_FALLBACK_SUBJECTS: dict[str, str] = {
    EMAIL_TYPE_APPLICATION_CONFIRMATION: "Application received, {job_title} at {company_name}",
    EMAIL_TYPE_ASSESSMENT_REMINDER: "Your assessment for {job_title} is still open",
    EMAIL_TYPE_SHORTLIST: "Moving forward, {job_title} at {company_name}",
    EMAIL_TYPE_REJECTED: "Your application for {job_title} at {company_name}",
    EMAIL_TYPE_HOLD: "Your application for {job_title} is on hold",
    EMAIL_TYPE_QUESTION_BANK_REMINDER: "Action needed: review the {job_title} job",
    EMAIL_TYPE_ASSESSMENT_INVITATION: "Your assessment for {job_title} at {company_name}",
    EMAIL_TYPE_ASSESSMENT_COMPLETE: "Assessment received, {job_title} at {company_name}",
    EMAIL_TYPE_INTERVIEW_SCHEDULED: "Interview scheduled, {job_title} at {company_name}",
    EMAIL_TYPE_INTERVIEW_COMPLETED: "Thank you for your time, {job_title}",
    EMAIL_TYPE_OFFER_EXTENDED: "An offer from {company_name}, {job_title}",
    EMAIL_TYPE_JOINED: "Welcome to {company_name}",
}


class UnknownEmailType(ValueError):
    """Raised for an email type outside the six."""


def validate_email_type(email_type: str) -> str:
    if email_type not in EMAIL_TYPES:
        raise UnknownEmailType(
            f"unknown email_type {email_type!r}; expected one of {sorted(EMAIL_TYPES)}"
        )
    return email_type


# ── Response parsing ─────────────────────────────────────────────────────────

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def parse_draft(raw: str) -> tuple[str, str] | None:
    """Pull (subject, body) out of an LLM response.

    Tolerates a ```json fence even though every prompt forbids one — a model
    that wraps valid JSON has produced usable copy, and discarding it to fall
    back to a template would be throwing away the better email. Returns None
    when the response is not usable, which triggers the deterministic path.

    Pure and side-effect free; unit-tested in tests/test_lifecycle_email.py.
    """
    if not raw or not raw.strip():
        return None
    cleaned = _FENCE_RE.sub("", raw.strip())
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    subject = data.get("subject")
    body = data.get("body")
    if not isinstance(subject, str) or not subject.strip():
        return None
    if not isinstance(body, str) or not body.strip():
        return None
    # Subject is a header: a newline in it is header injection, and any real
    # subject line fits on one line anyway.
    subject = " ".join(subject.split())[:500]
    return subject, body.strip()


# ── Deterministic fallbacks ──────────────────────────────────────────────────

def _fallback_body(email_type: str, ctx: dict[str, Any]) -> str:
    name = ctx.get("candidate_name") or ctx.get("recruiter_name") or "there"
    job = ctx.get("job_title", "the role")
    company = ctx.get("company_name", "our team")

    if email_type == EMAIL_TYPE_APPLICATION_CONFIRMATION:
        return (
            f"Hi {name},\n\n"
            f"Thank you for applying for the {job} role at {company}. We have "
            "received your application and it is with the hiring team now.\n\n"
            f"{ctx.get('next_steps', '')}\n\n"
            "We will be in touch as soon as we have an update. If anything in "
            "your details changes in the meantime, just reply to this email.\n\n"
            f", The {company} team"
        )
    if email_type == EMAIL_TYPE_ASSESSMENT_REMINDER:
        link = ctx.get("assessment_link") or ""
        return (
            f"Hi {name},\n\n"
            f"You have an assessment in progress for the {job} role at {company}. "
            "Your answers so far are saved, so you can pick up exactly where you "
            "left off whenever suits you.\n\n"
            + (f"{link}\n\n" if link else "")
            + "If you ran into a problem with it, reply to this email and we will "
            "help.\n\n"
            f", The {company} team"
        )
    if email_type == EMAIL_TYPE_SHORTLIST:
        return (
            f"Hi {name},\n\n"
            f"Good news, you are moving forward for the {job} role at {company}. "
            "The hiring team was glad to see the experience you brought to the "
            "assessment.\n\n"
            f"{ctx.get('next_steps', '')}\n\n"
            "If you have any questions before then, just reply to this email.\n\n"
            f", The {company} team"
        )
    if email_type == EMAIL_TYPE_REJECTED:
        return (
            f"Hi {name},\n\n"
            f"Thank you for your interest in the {job} role at {company}. After "
            "reviewing your application, we will not be taking it forward on this "
            "occasion.\n\n"
            "We genuinely appreciate the time you put into applying and into the "
            "assessment, and you are very welcome to apply for future roles with "
            "us.\n\n"
            "We wish you the very best with your search.\n\n"
            f", The {company} team"
        )
    if email_type == EMAIL_TYPE_HOLD:
        return (
            f"Hi {name},\n\n"
            f"An update on your application for the {job} role at {company}: it is "
            "currently on hold rather than closed. The timeline for the role is "
            "still being settled internally.\n\n"
            f"We expect to come back to you within {ctx.get('hold_days', '10')} days "
            "with something more definite.\n\n"
            "If your own situation or availability changes in the meantime, do let "
            "us know by replying here.\n\n"
            f", The {company} team"
        )
    if email_type == EMAIL_TYPE_ASSESSMENT_INVITATION:
        link = ctx.get("assessment_link") or ""
        return (
            f"Hi {name},\n\n"
            f"The hiring team has reviewed your application for the {job} role "
            f"at {company} and would like you to complete a short assessment. "
            "It covers your technical experience and how you approach your "
            f"work, and takes {ctx.get('duration', 'about 30 to 40 minutes')}.\n\n"
            + (f"{link}\n\n" if link else "")
            + "Your answers save as you go, so you can stop and pick it up "
            "again if you need to.\n\n"
            f", The {company} team"
        )
    if email_type == EMAIL_TYPE_ASSESSMENT_COMPLETE:
        return (
            f"Hi {name},\n\n"
            f"Thank you, your assessment for the {job} role at {company} is "
            "complete and we have received it.\n\n"
            "The hiring team will review it and come back to you, usually "
            f"within {ctx.get('review_days', '5 to 7')} days.\n\n"
            "Thank you for the time you gave it.\n\n"
            f", The {company} team"
        )
    if email_type == EMAIL_TYPE_INTERVIEW_SCHEDULED:
        when = ctx.get("scheduled_at", "a time the team will confirm")
        return (
            f"Hi {name},\n\n"
            f"Your {ctx.get('stage_name', 'interview')} for the {job} role at "
            f"{company} is scheduled for {when}.\n\n"
            "It is a conversation about your experience and the work involved "
            "in the role.\n\n"
            "If that time does not work, reply and we will find another one. "
            "Any questions beforehand, just ask.\n\n"
            f", The {company} team"
        )
    if email_type == EMAIL_TYPE_INTERVIEW_COMPLETED:
        return (
            f"Hi {name},\n\n"
            f"Thank you for your time at the interview for the {job} role at "
            f"{company}.\n\n"
            "The team is discussing it internally and we will come back to you "
            f"within {ctx.get('review_days', '5 to 7')} days.\n\n"
            "If anything came up afterwards you would like to ask about, reply "
            "here.\n\n"
            f", The {company} team"
        )
    if email_type == EMAIL_TYPE_OFFER_EXTENDED:
        return (
            f"Hi {name},\n\n"
            f"We would like to offer you the {job} role at {company}. The team "
            "was genuinely impressed, and we very much hope you will join "
            "us.\n\n"
            f"{ctx.get('next_steps', '')}\n\n"
            "Take the time you need, and ask us anything before you decide.\n\n"
            f", The {company} team"
        )
    if email_type == EMAIL_TYPE_JOINED:
        return (
            f"Hi {name},\n\n"
            f"Welcome to {company}. We are delighted you are joining us as "
            f"{job}.\n\n"
            f"{ctx.get('next_steps', '')}\n\n"
            "If anything comes up before you start, reply here and we will "
            "sort it out.\n\n"
            f", The {company} team"
        )
    # question_bank_reminder (internal)
    link = ctx.get("job_link") or ""
    return (
        f"Hi {name},\n\n"
        f"The {job} job has been waiting for your review since it was created. "
        "Until it is reviewed, candidates cannot be assessed against this role.\n\n"
        + (f"{link}\n\n" if link else "")
        + ", PickReady"
    )


def fallback_draft(email_type: str, ctx: dict[str, Any]) -> tuple[str, str]:
    """A clear, deterministic email for when the LLM chain is unavailable."""
    subject = _FALLBACK_SUBJECTS[email_type].format(
        job_title=ctx.get("job_title", "the role"),
        company_name=ctx.get("company_name", "our team"),
    )
    return subject, _fallback_body(email_type, ctx)


# ── Public API ───────────────────────────────────────────────────────────────

async def draft(
    email_type: str,
    context: dict[str, Any],
    *,
    session: Any = None,
) -> dict[str, Any]:
    """Draft one lifecycle email.

    Returns {"subject", "body", "html", "generated_by_ai", "email_type"}.
    Never raises for a provider failure — a deterministic template is returned
    with `generated_by_ai=False` so the caller (and the audit row) can tell the
    two apart.
    """
    validate_email_type(email_type)
    ctx: dict[str, Any] = {**_PROMPT_DEFAULTS.get(email_type, {}), **context}
    ctx.setdefault("candidate_name", "there")
    ctx.setdefault("job_title", "the role")
    ctx.setdefault("company_name", "our team")

    generated_by_ai = False
    subject: str | None = None
    body: str | None = None

    try:
        rendered = prompts.render(EMAIL_TYPE_PROMPTS[email_type], **ctx)
    except KeyError:
        # A prompt/caller placeholder mismatch is a BUG, not a runtime
        # condition — log it loudly, but still send the candidate something.
        logger.exception(
            "lifecycle_email.prompt_placeholder_mismatch email_type=%s", email_type
        )
        rendered = None

    if rendered is not None:
        try:
            raw = await llm_router.invoke_llm(
                _TASK_TYPE,
                [
                    {"role": "system", "content": rendered},
                    {"role": "user", "content": "Return only the JSON object."},
                ],
                response_format_json=True,
                session=session,
            )
            parsed = parse_draft(raw)
            if parsed is not None:
                subject, body = parsed
                generated_by_ai = True
            else:
                logger.warning(
                    "lifecycle_email.unparseable_response email_type=%s, "
                    "using deterministic template",
                    email_type,
                )
        except llm_router.LLMUnavailableError:
            logger.warning(
                "lifecycle_email.llm_unavailable email_type=%s, "
                "using deterministic template",
                email_type,
            )

    if subject is None or body is None:
        subject, body = fallback_draft(email_type, ctx)

    return {
        "email_type": email_type,
        "subject": subject,
        "body": body,
        "html": to_html(body),
        "generated_by_ai": generated_by_ai,
    }


def to_html(body: str) -> str:
    """Wrap a plain-text body in minimal HTML.

    Every interpolated character is HTML-escaped FIRST, so a model that emits
    stray markup — or a recruiter who pastes some — can never inject unescaped
    HTML into a candidate's inbox. Same guarantee as outreach_content.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body or "") if p.strip()]
    rendered = "".join(
        "<p style=\"margin:0 0 16px;line-height:1.6\">"
        + html_lib.escape(p).replace("\n", "<br>")
        + "</p>"
        for p in paragraphs
    )
    return (
        '<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;'
        'font-size:15px;color:#111">' + rendered + "</div>"
    )
