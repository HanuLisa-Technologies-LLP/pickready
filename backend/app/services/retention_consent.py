"""Candidate data-retention consent (Consent & Privacy spec, 2026-09-05).

Two consents, captured per candidate on their own profile and stored on the
`candidates` row beside the databank consent they extend:

  * ``retain_assessment_consent``: retain the completed assessment (the PRISM
    Report and its underlying record) for future jobs, or for this job only.
  * ``retain_video_consent``: retain any consented video record for future
    jobs, or for this job only.

Semantics of each stored value:

  * ``True``  means retain for future jobs.
  * ``False`` means this job only.
  * ``NULL``  means the candidate was never asked, and is treated exactly as
    ``False``: the safe direction. Absence of consent is never consent.

The spec (Consent & Privacy, "How it would work") wires the flag straight into
client-portal permission logic: **Download is enabled if Yes, View-only if
No.** On-screen viewing is unchanged either way; only the taking-a-copy verb
is gated. These helpers are that permission logic, in one place, so the PDF
route, the report serializer and the future video-delivery path cannot drift
apart on what a NULL means.

The helpers are pure functions over the candidate row. They accept ``None``
for the candidate itself because a report can outlive a deleted candidate
record, and a record that can no longer answer "did they consent?" must read
as "no".
"""
from __future__ import annotations

from typing import Any


def _explicitly_true(value: Any) -> bool:
    """True only for a stored, explicit ``True``. None and False both refuse."""
    return value is True


def assessment_download_allowed(candidate: Any) -> bool:
    """May a client-portal user DOWNLOAD this candidate's assessment report?

    Reads ``candidates.retain_assessment_consent``. True only when the
    candidate explicitly consented to retention for future jobs; False for an
    explicit "this job only" and for a candidate who was never asked (NULL),
    which the spec's safe direction treats identically. Viewing on screen is
    not gated here and stays allowed.
    """
    return _explicitly_true(getattr(candidate, "retain_assessment_consent", None))


def video_download_allowed(candidate: Any) -> bool:
    """May a client-portal user DOWNLOAD this candidate's video record?

    Reads ``candidates.retain_video_consent`` with the same rule as
    :func:`assessment_download_allowed`: an explicit True enables download,
    an explicit False or a never-asked NULL keeps the record view-only.
    Stable import surface for the video delivery path.
    """
    return _explicitly_true(getattr(candidate, "retain_video_consent", None))
