"""Critic for a lifecycle email: is it addressed to this person, about this move.

AN EMAIL IS THE ONE AGENT OUTPUT THAT CANNOT BE RECALLED
---------------------------------------------------------
A bad remark on a report is read by a recruiter who can discount it. A bad
email has already reached a candidate's inbox. That asymmetry is why this
critic is stricter about small things than the others: a missing link is HIGH
here and would be medium anywhere else, because "click the link below" with no
link below is a message that wasted somebody's time and cannot be edited after
the fact.

THE TRANSITION CHECK IS THE LOAD-BEARING ONE
--------------------------------------------
Each stage carries a promise -- `assessment_completed` means a report exists --
and the emails reference those promises in their copy. An email drafted for a
transition the FSM would refuse is therefore not merely mislabelled: it states
something about the application that is not true. `hiring_pipeline` is asked
rather than a list restated here, so a stage added to the FSM cannot be one this
critic silently keeps rejecting.

RULES INHERITED, NOT INVENTED
-----------------------------
No em dash in a user-facing string, in either language, and the character class
is built from `chr(8212)` so a repo-wide dash sweep cannot rewrite the code that
detects it. No number that describes the assessment. Both are standing product
rules; this critic is one more place they are enforced, not a new opinion.
"""
from __future__ import annotations

import re
from typing import Any

from app.services import conversation_guardrails, hiring_pipeline, lifecycle_email
from app.services.verification import base, generic_language

# Built from the code point, never typed literally: a sweep that rewrites every
# em dash in the repository would otherwise rewrite the detector too.
EM_DASH = chr(8212)

#: Subject lines longer than this are truncated by mail clients mid-word.
_SUBJECT_MAX_CHARS = 120


def verify_draft(
    *,
    email_type: str,
    subject: str,
    body: str,
    context: dict[str, Any] | None = None,
    current_status: str | None = None,
    target_status: str | None = None,
) -> base.Verdict:
    """Verify one drafted email before a human is offered the chance to edit it.

    `context` is the same dict `lifecycle_email.draft` builds, so the link check
    can reuse `lifecycle_email.link_defects` rather than reimplementing which
    URL each email type is required to carry.
    """
    findings: list[base.Finding] = []
    context = context or {}

    findings.extend(_transition_findings(current_status, target_status))
    findings.extend(_subject_findings(subject))
    findings.extend(_body_findings(body, context))
    findings.extend(_link_findings(email_type, context, body))
    findings.extend(_personalisation_findings(body, context))

    return base.verdict("email", findings)


def _transition_findings(
    current_status: str | None, target_status: str | None
) -> list[base.Finding]:
    if not target_status:
        return []
    if current_status is None:
        # Nothing to check against. Silence is correct: a caller that does not
        # know the current stage is not asserting a wrong one.
        return []
    if hiring_pipeline.can_transition(current_status, target_status):
        return []
    return [
        base.high(
            "invalid_transition",
            "target_status",
            (
                f"{current_status} cannot move to {target_status}; this email "
                "states something about the application that is not true"
            ),
            (
                "draft the email for a stage the application can actually "
                "reach from "
                f"{current_status}: "
                + ", ".join(sorted(hiring_pipeline.allowed_transitions(current_status)))
            ),
        )
    ]


def _subject_findings(subject: str) -> list[base.Finding]:
    text = str(subject or "").strip()
    if not text:
        return [
            base.high(
                "missing_subject",
                "subject",
                "the draft has no subject line",
                "write a subject line naming the role",
            )
        ]
    findings: list[base.Finding] = []
    if len(text) > _SUBJECT_MAX_CHARS:
        findings.append(
            base.medium(
                "subject_too_long",
                "subject",
                f"the subject is {len(text)} characters",
                f"shorten the subject to under {_SUBJECT_MAX_CHARS} characters",
            )
        )
    if EM_DASH in text:
        findings.append(_em_dash_finding("subject"))
    return findings


def _body_findings(body: str, context: dict[str, Any]) -> list[base.Finding]:
    text = str(body or "").strip()
    if not text:
        return [
            base.high(
                "missing_body",
                "body",
                "the draft has no body",
                "write the message body",
            )
        ]

    findings: list[base.Finding] = []
    if EM_DASH in text:
        findings.append(_em_dash_finding("body"))

    if conversation_guardrails.contains_forbidden_number(text):
        findings.append(
            base.high(
                "number_leaked",
                "body",
                "the body states a score, percentage, rank or band",
                "remove every score, percentage and rank; an email never "
                "carries an assessment number",
            )
        )

    if _unfilled_placeholders(text):
        findings.append(
            base.high(
                "unfilled_placeholder",
                "body",
                "the body still contains a template placeholder",
                "fill every placeholder with the real value before sending",
            )
        )

    findings.extend(generic_language.findings(text, location="body"))
    return findings


_PLACEHOLDER = re.compile(r"\{\{?\s*[a-z_][a-z0-9_]*\s*\}?\}|\[(?:NAME|ROLE|COMPANY|DATE)\]", re.IGNORECASE)


def _unfilled_placeholders(text: str) -> bool:
    return bool(_PLACEHOLDER.search(text))


def _link_findings(
    email_type: str, context: dict[str, Any], body: str
) -> list[base.Finding]:
    """Reuse the sender's own link contract rather than restating it.

    `lifecycle_email.link_defects` already knows which URL each email type must
    carry and that it must be the one from context rather than one the model
    invented. Asking it here means the critic cannot disagree with the repair
    path that runs immediately after.
    """
    try:
        defects = lifecycle_email.link_defects(email_type, context, str(body or ""))
    except Exception:  # noqa: BLE001 -- an unknown email type is checked elsewhere
        return []
    return [
        base.high(
            "link_defect",
            "body",
            defect,
            "include exactly the link supplied in the context, unmodified",
        )
        for defect in defects
    ]


def _personalisation_findings(
    body: str, context: dict[str, Any]
) -> list[base.Finding]:
    """The candidate's name and the role should appear, in their real form.

    Low severity on purpose. A perfectly good email can open "Hello," and a
    critic that treats a missing first name as disqualifying would spend a
    regeneration on a message that was already fine. It is recorded because
    personalisation is one of the metrics the evaluation dataset tracks, and a
    metric with nothing feeding it never moves.
    """
    text = str(body or "")
    findings: list[base.Finding] = []

    name = str(context.get("candidate_name") or "").strip()
    first = name.split()[0] if name else ""
    if first and first.lower() not in text.lower():
        findings.append(
            base.low(
                "missing_recipient_name",
                "body",
                "the body never addresses the candidate by name",
                f"address the candidate as {first}",
            )
        )

    role = str(context.get("job_title") or "").strip()
    if role and role.lower() not in text.lower():
        findings.append(
            base.low(
                "missing_role",
                "body",
                "the body never names the role",
                f"name the role: {role}",
            )
        )
    return findings


def _em_dash_finding(location: str) -> base.Finding:
    return base.high(
        "em_dash",
        location,
        "the text contains an em dash",
        "replace the em dash with a comma, a colon or a full stop",
    )
