"""The reusable email template registry (Corporate Email System spec section 7).

A FIXED catalogue in code, not a prompt and not a table: these are the
spec-listed reusable templates a recruiter can send verbatim with variables
filled in. They deliberately do NOT replace the AI-drafted lifecycle email
path (`services/lifecycle_email` + `api/emails`), which stays the personalised
branch; this registry is the send-one-template-to-everyone alternative the
spec asks for. Nor do they touch email_render's own built-in transactional
drafts, which are the platform's outage path rather than recruiter-facing
choices. (That symbol is deliberately not named here: the silent-degradation
ratchet greps module TEXT for it, and a docstring mention reads the same as a
reach.)

RENDERING REFUSES RATHER THAN GUESSES. An unknown template name, a variable
outside the declared vocabulary, or a referenced variable the caller did not
supply all raise: a half-substituted email with a literal "{{candidate_name}}"
in it, or one silently missing its assessment link, is worse than an error the
sender sees. No em dash appears in any string here, and
`tests/test_email_senders.py` sweeps the whole catalogue rather than trusting
this sentence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "ALLOWED_VARIABLES",
    "EmailTemplate",
    "TEMPLATES",
    "UnknownTemplate",
    "UnknownVariable",
    "MissingVariable",
    "render",
    "list_templates",
]

#: The whole variable vocabulary (spec section 7). A template may reference
#: only these, and a caller may supply only these.
ALLOWED_VARIABLES: frozenset[str] = frozenset(
    {
        "candidate_name",
        "company_name",
        "assessment_name",
        "assessment_link",
        "interview_date",
        "interview_time",
        "recruiter_name",
        "job_title",
    }
)

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


class UnknownTemplate(KeyError):
    """No template registered under that name."""


class UnknownVariable(ValueError):
    """A variable outside ALLOWED_VARIABLES, in a template or a context."""


class MissingVariable(ValueError):
    """The template references a variable the caller did not supply."""


@dataclass(frozen=True)
class EmailTemplate:
    key: str
    label: str
    subject: str
    body: str

    @property
    def variables(self) -> frozenset[str]:
        """The variables this template actually references."""
        return frozenset(
            _PLACEHOLDER_RE.findall(self.subject)
            + _PLACEHOLDER_RE.findall(self.body)
        )


def _template(key: str, label: str, subject: str, body: str) -> EmailTemplate:
    tpl = EmailTemplate(key=key, label=label, subject=subject, body=body)
    stray = tpl.variables - ALLOWED_VARIABLES
    if stray:
        raise UnknownVariable(
            f"template {key!r} references undeclared variables: {sorted(stray)}"
        )
    return tpl


#: The spec's nine reusable templates, in its own order.
TEMPLATES: dict[str, EmailTemplate] = {
    tpl.key: tpl
    for tpl in (
        _template(
            "assessment_invitation",
            "Assessment Invitation",
            "Assessment invitation from {{company_name}}",
            "Hi {{candidate_name}},\n\n"
            "You have been invited to complete the {{assessment_name}} "
            "assessment for the {{job_title}} role.\n\n"
            "Assessment link:\n{{assessment_link}}\n\n"
            "Regards,\n{{company_name}} Recruitment Team",
        ),
        _template(
            "interview_invitation",
            "Interview Invitation",
            "Interview invitation, {{job_title}} at {{company_name}}",
            "Hi {{candidate_name}},\n\n"
            "You are invited to an interview for the {{job_title}} role at "
            "{{company_name}}, on {{interview_date}} at {{interview_time}}.\n\n"
            "Please reply to confirm your availability.\n\n"
            "Regards,\n{{recruiter_name}}\n{{company_name}} Recruitment Team",
        ),
        _template(
            "interview_reminder",
            "Interview Reminder",
            "Reminder: your interview with {{company_name}}",
            "Hi {{candidate_name}},\n\n"
            "This is a reminder of your interview for the {{job_title}} role "
            "at {{company_name}}, on {{interview_date}} at "
            "{{interview_time}}.\n\n"
            "Regards,\n{{recruiter_name}}\n{{company_name}} Recruitment Team",
        ),
        _template(
            "assessment_reminder",
            "Assessment Reminder",
            "Reminder: complete your {{company_name}} assessment",
            "Hi {{candidate_name}},\n\n"
            "A reminder that your {{assessment_name}} assessment for the "
            "{{job_title}} role is waiting for you.\n\n"
            "Assessment link:\n{{assessment_link}}\n\n"
            "Regards,\n{{company_name}} Recruitment Team",
        ),
        _template(
            "selection",
            "Selection",
            "Good news about your application to {{company_name}}",
            "Hi {{candidate_name}},\n\n"
            "Congratulations. You have been selected for the {{job_title}} "
            "role at {{company_name}}. Our team will contact you shortly with "
            "the next steps.\n\n"
            "Regards,\n{{recruiter_name}}\n{{company_name}} Recruitment Team",
        ),
        _template(
            "rejection",
            "Rejection",
            "An update on your application to {{company_name}}",
            "Hi {{candidate_name}},\n\n"
            "Thank you for the time you invested in applying for the "
            "{{job_title}} role at {{company_name}}. After careful review, we "
            "will not be moving forward with your application at this time.\n\n"
            "We wish you every success in your search.\n\n"
            "Regards,\n{{company_name}} Recruitment Team",
        ),
        _template(
            "password_reset",
            "Password Reset",
            "Reset your ReadyPick sign-in",
            "Hi {{candidate_name}},\n\n"
            "We received a request to reset the sign-in for your ReadyPick "
            "account. If this was you, follow the instructions on the sign-in "
            "page. If it was not, you can safely ignore this email.\n\n"
            "Regards,\nReadyPick",
        ),
        _template(
            "account_verification",
            "Account Verification",
            "Verify your ReadyPick account",
            "Hi {{candidate_name}},\n\n"
            "Welcome to ReadyPick. Please verify your account by following "
            "the link in your sign-in flow to finish setting things up.\n\n"
            "Regards,\nReadyPick",
        ),
        _template(
            "application_update",
            "Application Update",
            "An update on your {{job_title}} application",
            "Hi {{candidate_name}},\n\n"
            "There is an update on your application for the {{job_title}} "
            "role at {{company_name}}. Sign in to your candidate portal to "
            "see the details.\n\n"
            "Regards,\n{{company_name}} Recruitment Team",
        ),
    )
}


def list_templates() -> list[EmailTemplate]:
    """Registry order, which is the spec's order."""
    return list(TEMPLATES.values())


def render(template_key: str, variables: dict[str, str]) -> tuple[str, str]:
    """Render (subject, body) for one template, refusing rather than guessing.

    Raises `UnknownTemplate`, `UnknownVariable` (a supplied key outside the
    declared vocabulary) or `MissingVariable` (the template references
    something the caller did not supply).
    """
    template = TEMPLATES.get(template_key)
    if template is None:
        raise UnknownTemplate(template_key)

    stray = set(variables) - ALLOWED_VARIABLES
    if stray:
        raise UnknownVariable(
            f"unknown template variables: {sorted(stray)}; allowed: "
            f"{sorted(ALLOWED_VARIABLES)}"
        )
    missing = {
        name
        for name in template.variables
        if not str(variables.get(name, "")).strip()
    }
    if missing:
        raise MissingVariable(
            f"template {template_key!r} needs values for: {sorted(missing)}"
        )

    def _sub(text: str) -> str:
        return _PLACEHOLDER_RE.sub(
            lambda m: str(variables[m.group(1)]), text
        )

    return _sub(template.subject), _sub(template.body)
