"""Tenant email-template rendering + .ics building (ESD §11/§12, FR-8.5).

PickReady ships no fixed email copy — each tenant maintains editable,
versioned templates (EmailTemplate rows). Rendering picks the tenant's
highest active version by name; if the tenant has no template yet, a
deliberately minimal default keeps the pipeline functional (the product
requirement is "no *shipped* templates", not "silently drop the email" —
the defaults below are bare functional placeholders, not branded copy).
"""
from __future__ import annotations

import html as _html
import re
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EmailTemplate

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

# ASSUMPTION: minimal fallback bodies when a tenant hasn't authored templates
# yet (PRD §5 says no fixed templates are *shipped*; an outreach/verification
# email still has to say something before the tenant edits theirs).
DEFAULT_TEMPLATES: dict[str, tuple[str, str]] = {
    # OTP delivery must work for EVERY user — including platform-level users
    # (Owner/super_admin) who have no tenant and therefore no tenant-authored
    # templates. The body carries the code; it is never logged (ESD §16).
    "otp": (
        "Your PickReady verification code",
        "Your PickReady one-time password is {{code}}. It is valid for "
        "{{ttl_minutes}} minutes.\n\n"
        "If you did not request this code, you can ignore this email.",
    ),
    "outreach": (
        "Information request regarding a role at {{company_name}}",
        "Dear {{candidate_name}},\n\n"
        "We are considering you for a role at {{company_name}}. Please complete "
        "your candidate page (personal details, updated resume, and "
        "questionnaire) using this link:\n\n{{outreach_link}}\n\n"
        "Regards,\n{{company_name}} Recruitment Team",
    ),
    "verification": (
        "Employment verification request for {{candidate_name}}",
        "Dear HR Team,\n\n"
        "{{candidate_name}} has listed your organisation as a previous "
        "employer. Please verify their employment details using this secure "
        "form:\n\n{{verification_link}}\n\n"
        "Alternatively, you may reply directly to this email with the "
        "details.\n\nRegards,\n{{company_name}} Recruitment Team",
    ),
    "interview_invite": (
        "Interview invitation — {{job_title}} at {{company_name}}",
        "Dear {{candidate_name}},\n\n"
        "You are invited to an interview for the {{job_title}} position at "
        "{{company_name}}, scheduled for {{scheduled_at}}. A calendar invite "
        "is attached.\n\nRegards,\n{{company_name}} Recruitment Team",
    ),
}


def substitute(template: str, context: dict[str, Any]) -> str:
    """Replace {{placeholder}} tokens; unknown placeholders render as ''."""
    return _PLACEHOLDER_RE.sub(
        lambda m: str(context.get(m.group(1), "")), template
    )


def text_to_html(body: str) -> str:
    """Wrap a rendered plain-text body as minimal, safe HTML for Mailtrap.

    Mailtrap's Sending API expects an ``html`` field; our templates are authored
    as plain text (ESD §11), so we HTML-escape and convert newlines to <br> to
    preserve layout without introducing an HTML templating layer. The plain-text
    body is still sent alongside as ``text``.
    """
    escaped = _html.escape(body).replace("\n", "<br>\n")
    return f'<div style="font-family:sans-serif;white-space:normal">{escaped}</div>'


async def render(
    session: AsyncSession,
    tenant_id: uuid.UUID | str | None,
    template_name: str,
    context: dict[str, Any],
) -> tuple[str, str]:
    """Render (subject, body) for a tenant's template by name.

    Uses the highest active version of the tenant's template; falls back to
    the minimal default when the tenant has none.

    ROOT-CAUSE FIX (2026-07-23): tenant_id may legitimately be None —
    platform-level emails (e.g. the Owner/super_admin OTP) have no tenant.
    This used to crash with ValueError('badly formed hexadecimal UUID string')
    from uuid.UUID(str(None)), which was the real reason "Resend key present
    but no email sent". None/unparseable tenant_id now skips the tenant
    template lookup and renders the built-in default instead of crashing.
    """
    parsed_tenant_id: uuid.UUID | None = None
    if tenant_id is not None:
        try:
            parsed_tenant_id = uuid.UUID(str(tenant_id))
        except (ValueError, AttributeError, TypeError):
            parsed_tenant_id = None  # invalid id → default template, no crash

    row = None
    if parsed_tenant_id is not None:
        row = (
            await session.execute(
                select(EmailTemplate)
                .where(
                    EmailTemplate.tenant_id == parsed_tenant_id,
                    EmailTemplate.name == template_name,
                    EmailTemplate.is_active.is_(True),
                )
                .order_by(EmailTemplate.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    if row is not None:
        subject_tmpl, body_tmpl = row.subject, row.body
    elif template_name in DEFAULT_TEMPLATES:
        subject_tmpl, body_tmpl = DEFAULT_TEMPLATES[template_name]
    else:
        raise ValueError(
            f"No template named {template_name!r} for tenant and no default exists"
        )
    return substitute(subject_tmpl, context), substitute(body_tmpl, context)


# ── .ics builder for interview invites (ESD §12) ────────────────────────────

def build_ics(
    uid: str,
    summary: str,
    starts_at: datetime,
    duration_minutes: int = 60,
    organizer_email: str | None = None,
    attendee_emails: list[str] | None = None,
    description: str = "",
    location: str = "",
) -> bytes:
    """Build an .ics calendar attachment (icalendar) for an interview invite.

    Sent only via the tenant's verified sending domain — never through a
    Google/Outlook Calendar API (explicit non-goal).
    """
    from icalendar import Calendar, Event, vCalAddress, vText

    cal = Calendar()
    cal.add("prodid", "-//PickReady//Interview Scheduling//EN")
    cal.add("version", "2.0")
    cal.add("method", "REQUEST")

    event = Event()
    event.add("uid", uid)
    event.add("summary", summary)
    event.add("dtstart", starts_at)
    event.add("dtend", starts_at + timedelta(minutes=duration_minutes))
    event.add("dtstamp", datetime.now(starts_at.tzinfo) if starts_at.tzinfo else datetime.utcnow())
    if description:
        event.add("description", description)
    if location:
        event.add("location", location)
    if organizer_email:
        organizer = vCalAddress(f"MAILTO:{organizer_email}")
        event["organizer"] = organizer
    for email in attendee_emails or []:
        attendee = vCalAddress(f"MAILTO:{email}")
        attendee.params["ROLE"] = vText("REQ-PARTICIPANT")
        attendee.params["RSVP"] = vText("TRUE")
        event.add("attendee", attendee, encode=0)
    cal.add_component(event)
    return cal.to_ical()
