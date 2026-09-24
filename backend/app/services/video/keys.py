"""S3 object keys for the session recording. IDS ONLY, NEVER PII.

The object key must not expose candidate PII, so every key is built from the
conversation id and the recording id and nothing else. This module is the
ONLY key builder, so the rule cannot be broken at a call site, and
`tests/test_session_recording.py` asserts no email or name shape survives in
a generated key.

    assessment-raw/{conversation_id}/{recording_id}/seg-{ordinal:04d}.<ext>
    assessment-raw/{conversation_id}/{recording_id}/raw.<ext>        (pre-0126)
    assessment-compressed/{conversation_id}/{recording_id}/assessment.mp4

The two prefixes are also the unit of everything infrastructure decides about
these objects: the application's IAM grant and the lifecycle rules in
`infra/modules/s3` name them, so a key outside them is a key the product can
neither write nor expire.
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
    """The single-object raw key recordings used before segmented upload.

    Nothing writes it now; it is built only so a row written before migration
    0126 can be read and its object deleted.
    """
    return f"{RAW_PREFIX}/{conversation_id}/{recording_id}/raw.{_extension(source_format)}"


def segment_key(
    conversation_id: uuid.UUID,
    recording_id: uuid.UUID,
    ordinal: int,
    source_format: str | None,
) -> str:
    """One segment's object. The ordinal is zero-padded so a listing sorts
    in recording order, which is the order the segments are joined in."""
    if ordinal < 0:
        raise ValueError("a segment ordinal is never negative")
    return (
        f"{RAW_PREFIX}/{conversation_id}/{recording_id}/"
        f"seg-{ordinal:04d}.{_extension(source_format)}"
    )


def compressed_key(conversation_id: uuid.UUID, recording_id: uuid.UUID) -> str:
    return f"{COMPRESSED_PREFIX}/{conversation_id}/{recording_id}/assessment.mp4"
