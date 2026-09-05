"""Response schemas for the client-portal video access routes (2026-09-05
Client Candidate Dashboard, Executive Profile & Proctored Video Specification,
sections 13, 15-18).

TWO THINGS THESE SCHEMAS MUST NEVER CARRY (spec section 13): the S3 bucket
name and the raw object keys. Neither type has a field for either, and
`tests/test_video_access.py` sweeps the field sets to keep it that way. The
presigned URL a delivery response carries is the ONE sanctioned way an object
location leaves the backend: time-limited, single-purpose and useless once
expired.

No candidate score, grade or rank appears here. `duration_seconds` and
`compressed_size_bytes` are operational media facts, not assessment numbers.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel


class VideoAccessOut(BaseModel):
    """The Executive Profile / dashboard view of one application's video."""

    job_candidate_link_id: uuid.UUID
    #: Null when no recording exists (every conversational session today).
    recording_id: uuid.UUID | None = None

    #: 'conversational' | 'video_interview', or null before any session opens.
    assessment_mode: str | None = None
    #: "Video interview" / "Conversational" / "Not started".
    assessment_mode_label: str

    #: "Ready" / "Processing" / "Failed" / "No recording" -- the four dashboard
    #: words, never a lifecycle-machine identifier.
    video_status: str
    #: One client-facing sentence explaining the word above.
    video_status_detail: str

    #: Set only once the recording is ready; null is "not known yet", never 0.
    duration_seconds: float | None = None
    compressed_size_bytes: int | None = None

    #: Whether POST .../preview will mint a URL right now.
    preview_available: bool = False
    #: Preview availability AND the candidate's download consent
    #: (`retain_video_consent`). False keeps the record view-only.
    download_available: bool = False
    #: Why download is withheld while preview works, so the UI explains
    #: instead of showing a dead button. Null when download is available or
    #: nothing is previewable anyway.
    download_blocked_reason: str | None = None

    #: Whether the staff retry endpoint would accept this recording, so the
    #: Failed state can offer "Retry processing" without ever offering a 409.
    can_retry: bool = False


class VideoDeliveryOut(BaseModel):
    """One minted delivery URL (preview or download).

    The URL is a bearer token: it is returned once, expires on the server's
    clock, and is never stored. `expires_in_seconds` is served so the player
    can refresh before a long viewing session outlives its URL.
    """

    url: str
    expires_in_seconds: int
    #: "inline" for the player, "attachment" for a browser download.
    disposition: str
    #: The attachment filename (ids only, no candidate PII); null for preview.
    filename: str | None = None
