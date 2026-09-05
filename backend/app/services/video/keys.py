"""S3 object keys for assessment videos. IDS ONLY, NEVER PII.

Dual-mode spec section 5 and video spec section 9: the object key must not
expose candidate PII, so every key is built from the conversation id and the
recording id and nothing else. This module is the ONLY key builder, so the
rule cannot be broken at a call site, and `tests/test_dual_mode_assessment.py`
asserts no email or name shape survives in a generated key.

    assessment-raw/{conversation_id}/{recording_id}/raw.<ext>
    assessment-raw/{conversation_id}/{recording_id}/audio.wav
    assessment-compressed/{conversation_id}/{recording_id}/assessment.mp4
"""
from __future__ import annotations

import uuid

RAW_PREFIX = "assessment-raw"
COMPRESSED_PREFIX = "assessment-compressed"

#: Container extensions a browser MediaRecorder legitimately produces.
_SOURCE_EXTENSIONS = {"webm", "mp4", "mkv", "ogg"}


def _extension(source_format: str | None) -> str:
    """A safe file extension from the MediaRecorder MIME type.

    Derived from a fixed table rather than the client string, so a hostile
    MIME type cannot smuggle path characters into an object key.
    """
    subtype = (source_format or "").split(";")[0].split("/")[-1].strip().lower()
    return subtype if subtype in _SOURCE_EXTENSIONS else "webm"


def raw_key(
    conversation_id: uuid.UUID, recording_id: uuid.UUID, source_format: str | None
) -> str:
    return f"{RAW_PREFIX}/{conversation_id}/{recording_id}/raw.{_extension(source_format)}"


def audio_key(conversation_id: uuid.UUID, recording_id: uuid.UUID) -> str:
    return f"{RAW_PREFIX}/{conversation_id}/{recording_id}/audio.wav"


def compressed_key(conversation_id: uuid.UUID, recording_id: uuid.UUID) -> str:
    return f"{COMPRESSED_PREFIX}/{conversation_id}/{recording_id}/assessment.mp4"
