"""The platform security email: fixed templates, the platform sender, SES.

WHY THIS IS NOT `pickready.send_email`
--------------------------------------
Every other email in the product can carry a tenant: a tenant's own template
rows, a tenant's corporate sender, a Reply-To a recruiter's thread chose. A
security code must carry none of that (auth spec 10.3). So this module has:

* FIXED TEMPLATES, defined here and nowhere else. No `email_templates` row is
  read, so no tenant can reword a security email, and the context a caller
  passes is checked key by key against the template's own list: an unknown
  key, or a code that is not six digits, is refused before anything renders.
* ONE SENDER, `PLATFORM_SECURITY_SENDER_EMAIL` / `_NAME` (default
  noreply@readypick.ai, "Vivekium"; register I-20), resolved by
  `email_sender_resolution.security_identity`. Never a tenant sender and
  never a From address taken from a payload; the task signature has no field
  for one.
* SES ONLY, no other transport exists to fall back to.
* NO LOGGED CONTEXT. The context holds the code; nothing here writes it to a
  log, an audit row or an exception message.

PRIORITY
--------
The spec asks that a security email is not stuck behind bulk mail. Dispatch
has no shared queue to be stuck in: every dispatch is its own Lambda invoke
(`workers/dispatch`), so this task never waits for an outreach batch. A
separate queue is a later change to the transport, not to this module.
"""
from __future__ import annotations

import re
from typing import Any

from app.core.config import get_settings

__all__ = [
    "SECURITY_EMAIL_TASK",
    "TEMPLATES",
    "SecurityEmailRefused",
    "deliver",
    "dispatch_security_email",
    "render",
    "validate",
]

SECURITY_EMAIL_TASK = "pickready.send_security_email"

_CODE_RE = re.compile(r"^\d{6}$")


class SecurityEmailRefused(RuntimeError):
    """A security email that must not be sent as asked. Never retried."""


def _ttl_minutes() -> str:
    return str(max(1, get_settings().security_code_ttl_seconds // 60))


def _login_url(surface: str) -> str:
    base = get_settings().frontend_url.rstrip("/")
    return f"{base}/company/login" if surface == "company" else f"{base}/login"


#: name -> (subject, body, required keys, optional keys). The body is plain
#: text with `{key}` fields; `render` fills them from a checked context and
#: from values it computes itself (the lifetime, a sign-in URL). No em dash.
TEMPLATES: dict[str, tuple[str, str, frozenset[str], frozenset[str]]] = {
    "security_code_registration": (
        "Your Vivekium security code",
        "Hello,\n\n"
        "Use this security code to verify your company email address on "
        "Vivekium:\n\n"
        "**{code}**\n\n"
        "The code expires in {ttl_minutes} minutes. No account is created "
        "until it is entered.\n\n"
        "If you did not start a company registration, you can ignore this "
        "email.\n\n"
        "Vivekium",
        frozenset({"code"}),
        frozenset(),
    ),
    "security_code_password": (
        "Your Vivekium security code",
        "Hello,\n\n"
        "Use this security code to {action_phrase} your Vivekium password:\n\n"
        "**{code}**\n\n"
        "The code expires in {ttl_minutes} minutes.\n\n"
        "If you did not ask for this, your password has not changed and you "
        "can ignore this email. Nobody from Vivekium will ever ask you for "
        "this code.\n\n"
        "Vivekium",
        frozenset({"code", "action"}),
        frozenset(),
    ),
    "account_exists_notice": (
        "Your Vivekium account",
        "Hello,\n\n"
        "Someone asked to register this email address on Vivekium. This "
        "address already has an account, so nothing new was created.\n\n"
        "If it was you, sign in here:\n\n{login_url}\n\n"
        "If you do not remember your password, choose Forgot password on "
        "that page. If it was not you, you do not need to do anything.\n\n"
        "Vivekium",
        frozenset(),
        frozenset({"surface"}),
    ),
}

_ACTION_PHRASES = {"change": "change", "reset": "reset"}
_SURFACES = {"company", "candidate"}


def validate(template: str, context: dict[str, Any]) -> dict[str, str]:
    """The context, checked against the template's own key list.

    Raises `SecurityEmailRefused` for an unknown template, a missing or
    unknown key, or a value outside what the template accepts. The message
    names the KEY, never the value, because the value may be a code.
    """
    if template not in TEMPLATES:
        raise SecurityEmailRefused(f"unknown security email template {template!r}")
    _subject, _body, required, optional = TEMPLATES[template]
    if not isinstance(context, dict):
        raise SecurityEmailRefused("security email context must be a mapping")
    keys = set(context)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise SecurityEmailRefused(f"{template}: missing {sorted(missing)}")
    if unknown:
        raise SecurityEmailRefused(f"{template}: unexpected {sorted(unknown)}")
    clean: dict[str, str] = {}
    if "code" in context:
        if not isinstance(context["code"], str) or not _CODE_RE.match(context["code"]):
            raise SecurityEmailRefused(f"{template}: the code is not six digits")
        clean["code"] = context["code"]
    if "action" in context:
        if context["action"] not in _ACTION_PHRASES:
            raise SecurityEmailRefused(f"{template}: action must be change or reset")
        clean["action"] = context["action"]
    if "surface" in context:
        if context["surface"] not in _SURFACES:
            raise SecurityEmailRefused(f"{template}: surface must be company or candidate")
        clean["surface"] = context["surface"]
    return clean


def render(template: str, context: dict[str, Any]) -> tuple[str, str]:
    """(subject, plain-text body) for a checked context."""
    clean = validate(template, context)
    subject, body, _required, _optional = TEMPLATES[template]
    values = {
        "code": clean.get("code", ""),
        "action_phrase": _ACTION_PHRASES.get(clean.get("action", ""), ""),
        "ttl_minutes": _ttl_minutes(),
        "login_url": _login_url(clean.get("surface", "company")),
    }
    return subject, body.format(**values)


async def deliver(*, to: str, subject: str, html: str, text: str) -> str | None:
    """Send through SES from the platform security sender.

    Raises the shared delivery taxonomy (`services/delivery_errors`) from the
    transport, so the task retries a transient failure and stops on a
    permanent one.
    """
    from app.services import ses_service
    from app.services.email_sender_resolution import security_identity

    identity = security_identity()
    return await ses_service.send_email_async(
        from_email=identity.email,
        from_name=identity.name,
        to=to,
        subject=subject,
        html=html,
        text=text,
        correlation={"kind": "security"},
    )


def dispatch_security_email(
    to: str, template: str, context: dict[str, Any], *, session: Any = None
) -> None:
    """Validate, then dispatch `pickready.send_security_email`.

    With `session`, the dispatch waits for that session's COMMIT (a row the
    same request wrote, claude.md rule 4); without one it goes now. The
    context is validated HERE as well as in the task, so a malformed call
    fails in the request that made it rather than in a worker log.
    """
    validate(template, context)
    from app.workers.dispatch import dispatch, dispatch_after_commit

    args = [to, template, dict(context)]
    if session is not None:
        dispatch_after_commit(session, SECURITY_EMAIL_TASK, args=args)
    else:
        dispatch(SECURITY_EMAIL_TASK, args=args)
