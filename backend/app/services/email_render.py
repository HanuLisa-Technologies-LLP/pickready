"""Tenant email-template rendering + .ics building (ESD §11/§12, FR-8.5).

ReadyPick ships no fixed email copy — each tenant maintains editable,
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
        "Your ReadyPick verification code",
        "Your ReadyPick one-time password is {{code}}. It is valid for "
        "{{ttl_minutes}} minutes.\n\n"
        "If you did not request this code, you can ignore this email.",
    ),
    "outreach": (
        "Information request regarding a role at {{company_name}}",
        "Dear {{candidate_name}},\n\n"
        "We are considering you for a role at {{company_name}}. Please complete "
        "your candidate page (personal details, updated resume, and "
        "questionnaire) using this link:\n\n{{outreach_link}}\n\n"
        "Regards,\n{{company_name}} People Team",
    ),
    # AI/manual outreach is composed and approved in the UI before it reaches
    # the worker. Keep a built-in pass-through so delivery remains available
    # even before a tenant-specific template row has been created.
    "outreach_direct": (
        "{{subject}}",
        "{{body}}",
    ),
    "verification": (
        "Employment verification request for {{candidate_name}}",
        "Dear HR Team,\n\n"
        "{{candidate_name}} has listed your organisation as a previous "
        "employer. Please verify their employment details using this secure "
        "form:\n\n{{verification_link}}\n\n"
        "Alternatively, you may reply directly to this email with the "
        "details.\n\nRegards,\n{{company_name}} People Team",
    ),
    # Billing. A failed charge must never be silent: credits simply stop
    # arriving, and the first the customer would otherwise hear of it is a
    # refused invitation. Deliberately carries no amount and no card detail.
    "payment_failed": (
        "Your ReadyPick payment did not go through",
        "Hello,\n\n"
        "We could not process this month's subscription payment for "
        "{{company_name}}. Your credit balance is unchanged, and any credits "
        "already in your pool remain available.\n\n"
        "Update your payment method to keep new assessment invitations "
        "flowing:\n\n{{billing_url}}\n\n"
        "Regards,\nReadyPick",
    ),
    # Master Directive Part 5 §4 — the two credit-balance warning tiers. The
    # figures are computed at send time by the worker; the copy states balance,
    # estimated assessments remaining, and the top-up link, per §4.1's table.
    "credit_warning_low": (
        "Credits running low, {{company_name}}",
        "Hello,\n\n"
        "Credits running low. You have {{balance_credits}} credits remaining. "
        "At current usage, this covers approximately "
        "{{estimated_assessments}} more assessments.{{stem_note}}\n\n"
        "Top up now to keep your pipeline moving:\n\n{{billing_url}}\n\n"
        "Regards,\nReadyPick",
    ),
    "credit_warning_critical": (
        "Critical: only {{balance_credits}} credits remaining",
        "Hello,\n\n"
        "Critical: only {{balance_credits}} credits remaining for "
        "{{company_name}}. Some assessments may not complete. At current "
        "usage, this covers approximately {{estimated_assessments}} more "
        "assessments.{{stem_note}}\n\n"
        "Top up immediately:\n\n{{billing_url}}\n\n"
        "Regards,\nReadyPick",
    ),
    # Master Directive Part 5 §7.3 — the GST invoice email that accompanies a
    # settled credit-pack purchase. The invoice itself is a PDF attachment
    # rendered by the worker; the body only confirms the top-up and points at
    # the billing page, where the invoice stays downloadable.
    "credit_invoice": (
        "Your ReadyPick credit purchase and invoice {{invoice_number}}",
        "Hello,\n\n"
        "Your credit purchase for {{company_name}} is confirmed. "
        "{{credits_total}} credits have been added to your account and never "
        "expire.\n\n"
        "Invoice {{invoice_number}} (total Rs. {{total_inr}} incl. GST) is "
        "attached, and remains available from your billing page:\n\n"
        "{{billing_url}}\n\n"
        "Regards,\nReadyPick",
    ),
    "interview_invite": (
        "Interview invitation, {{job_title}} at {{company_name}}",
        "Dear {{candidate_name}},\n\n"
        "You are invited to an interview for the {{job_title}} position at "
        "{{company_name}}, scheduled for {{scheduled_at}}. A calendar invite "
        "is attached.\n\nRegards,\n{{company_name}} People Team",
    ),
    # ── Names that MUST exist here because a caller sends them ───────────────
    # `render` raises ValueError when a template name resolves to neither a
    # tenant row nor a default, and that raise happens inside the Celery task,
    # AFTER the API has already answered 200. Three names were being sent with
    # no default and no seeded row, so those invitations were discarded with no
    # email_log row, no audit_log row, and nothing the user could see. The
    # invariant is enforced by tests/test_email_delivery.py, which walks every
    # literal name passed to pickready.send_email in backend/app.
    #
    # api/verification.py sends this exact name; the "outreach" entry above
    # kept the older name and was never reached.
    "candidate_outreach": (
        "Information request regarding the {{job_title}} role at {{company_name}}",
        "Dear {{candidate_name}},\n\n"
        "We are considering you for the {{job_title}} role at "
        "{{company_name}}. Please complete your candidate page (personal "
        "details, updated resume, and questionnaire) using this link:\n\n"
        "{{outreach_url}}\n\n"
        "Regards,\n{{company_name}} People Team",
    ),
    # api/admin.py, when the platform owner creates a customer.
    "client_invite": (
        "Your {{tenant_name}} workspace on ReadyPick is ready",
        "Hello,\n\n"
        "A ReadyPick workspace has been created for {{tenant_name}}. Accept "
        "your invitation and sign in here:\n\n{{invite_link}}\n\n"
        "You will sign in with Google or with an email and password, "
        "ReadyPick never asks you to set a separate password.\n\n"
        "Regards,\nReadyPick",
    ),
    # api/companies.py seeds a tenant-EDITABLE row for this name on first use,
    # but a default belongs here too: the seeding and the send are separate
    # steps, and a missing row must degrade to generic copy rather than to a
    # silently lost invitation.
    "staff_invite": (
        "You have been invited to {{company_name}} on ReadyPick",
        "Hi {{full_name}},\n\n"
        "{{invited_by}} has invited you to join {{company_name}} on ReadyPick "
        "as a {{role_label}}.\n\n"
        "Accept your invitation here:\n\n{{invite_link}}\n\n"
        "You will sign in with Google or with an email and password, "
        "ReadyPick never asks you to set a separate password.\n\n"
        "This link expires on {{expires_on}}.\n\n"
        "Regards,\nThe {{company_name}} team",
    ),
}


def substitute(template: str, context: dict[str, Any]) -> str:
    """Replace {{placeholder}} tokens; unknown placeholders render as ''."""
    return _PLACEHOLDER_RE.sub(
        lambda m: str(context.get(m.group(1), "")), template
    )


def text_to_html(body: str) -> str:
    """Render safe, readable letter HTML from an approved plain-text body.

    Blank-line paragraphs remain paragraphs, standalone links become clear
    call-to-action buttons, list lines become real bullets and ``**bold**``
    emphasis is supported after HTML escaping. The text MIME alternative is
    still sent unchanged.
    """
    def inline(value: str) -> str:
        escaped = _html.escape(value)
        escaped = re.sub(
            r"\*\*(.+?)\*\*",
            r"<strong style=\"font-weight:700;color:#111827\">\1</strong>",
            escaped,
        )
        escaped = re.sub(
            r"(https?://[^\s<]+)",
            r'<a href="\1" style="color:#6d28d9;text-decoration:underline">\1</a>',
            escaped,
        )
        return escaped

    blocks: list[str] = []
    paragraphs = [
        part.strip()
        for part in re.split(r"\n\s*\n", body.strip())
        if part.strip()
    ]
    for paragraph in paragraphs:
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        if len(lines) == 1 and re.fullmatch(r"https?://\S+", lines[0]):
            url = _html.escape(lines[0], quote=True)
            blocks.append(
                '<p style="margin:24px 0;text-align:center">'
                f'<a href="{url}" style="display:inline-block;background:#6d28d9;'
                'color:#ffffff;text-decoration:none;font-weight:700;padding:12px 20px;'
                'border-radius:9px">Open secure link</a></p>'
            )
            continue
        if all(line.startswith(("- ", "* ")) for line in lines):
            items = "".join(
                f'<li style="margin:0 0 8px">{inline(line[2:])}</li>'
                for line in lines
            )
            blocks.append(
                f'<ul style="margin:0 0 18px;padding-left:22px">{items}</ul>'
            )
            continue
        content = "<br>".join(inline(line) for line in lines)
        blocks.append(f'<p style="margin:0 0 18px">{content}</p>')

    content_html = "".join(blocks)
    return (
        '<div style="margin:0;background:#f5f3ff;padding:28px 12px">'
        '<div style="max-width:640px;margin:0 auto;overflow:hidden;border:1px solid #e5e7eb;'
        'border-radius:14px;background:#ffffff">'
        '<div style="padding:20px 28px;border-bottom:1px solid #ede9fe;'
        'font-family:Arial,sans-serif;font-size:20px;font-weight:800;color:#111827">'
        'ReadyPick<span style="color:#7c3aed">.</span></div>'
        '<div style="padding:28px;font-family:Arial,sans-serif;font-size:15px;'
        f'line-height:1.65;color:#374151">{content_html}</div>'
        '<div style="padding:16px 28px;background:#fafafa;font-family:Arial,sans-serif;'
        'font-size:12px;line-height:1.5;color:#6b7280">'
        'This message was sent through a secure ReadyPick workflow.'
        '</div></div></div>'
    )


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
    cal.add("prodid", "-//ReadyPick//Interview Scheduling//EN")
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
