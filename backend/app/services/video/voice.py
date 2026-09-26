"""Spoken answers: where the audio lives while it is transcribed, and nothing else.

THE SANCTIONED MEDIA PACKAGE, AGAIN. Assessment media is written by
`services/video/` and nowhere else (`tests/test_proctoring_no_media.py`), and a
spoken answer is assessment media for the minutes between upload and
transcript. It goes through `video/storage.put`, which encrypts at rest, at a
key under `voice-answers/`, and it is DELETED, HEAD-confirmed, once the
transcript exists (Appendix B: "Store transcript text only").

No key and no bucket crosses an API boundary: the routes answer with the
`voice_answers` id and a status word.
"""
from __future__ import annotations

import uuid

from app.core.config import get_settings
from app.services.video import storage

#: Object prefix for spoken answers. Its own prefix so its lifecycle rule (a
#: one-day expiry backstop) and its IAM grant name exactly this and nothing else.
VOICE_PREFIX = "voice-answers"

#: The recorder formats accepted, mapped to the extension Transcribe reads. A
#: browser sends parameters (`audio/webm;codecs=opus`); only the media type
#: before the first `;` decides.
CONTENT_TYPES: dict[str, str] = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/wav": "wav",
    "audio/wave": "wav",
    "audio/x-wav": "wav",
}

UNSUPPORTED_FORMAT_DETAIL = (
    "That recording format is not supported. Please record again, or type your "
    "answer instead."
)
TOO_LARGE_DETAIL = (
    "That recording is longer than an answer can be. Please record a shorter "
    "answer, or type it instead."
)
EMPTY_DETAIL = "The recording was empty. Please record again, or type your answer instead."


class VoiceUploadRefused(ValueError):
    """The upload cannot be stored. The message is candidate-safe wording."""


def media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def extension_for(content_type: str | None) -> str:
    """The Transcribe media format for this upload, or `VoiceUploadRefused`."""
    extension = CONTENT_TYPES.get(media_type(content_type))
    if extension is None:
        raise VoiceUploadRefused(UNSUPPORTED_FORMAT_DETAIL)
    return extension


def validate_audio(data: bytes, content_type: str | None) -> str:
    """Refuse an empty, oversized or unsupported recording; return its format.

    Refused above the ceiling, never truncated: a truncated recording would be
    transcribed into an answer the candidate did not finish giving.
    """
    extension = extension_for(content_type)
    if not data:
        raise VoiceUploadRefused(EMPTY_DETAIL)
    if len(data) > int(get_settings().assessment_voice_max_bytes):
        raise VoiceUploadRefused(TOO_LARGE_DETAIL)
    return extension


def audio_key(conversation_id: uuid.UUID, voice_id: uuid.UUID, extension: str) -> str:
    return f"{VOICE_PREFIX}/{conversation_id}/{voice_id}.{extension}"


def transcript_key(conversation_id: uuid.UUID, voice_id: uuid.UUID) -> str:
    """Where Transcribe writes its result when it runs in the product's region."""
    return f"{VOICE_PREFIX}/{conversation_id}/{voice_id}.transcript.json"


def store_audio(*, key: str, data: bytes, content_type: str) -> int:
    """Store the recording, encrypted at rest. Sync; call from a thread."""
    return storage.put(key=key, data=data, content_type=media_type(content_type))


def delete_verified(key: str) -> bool:
    """Delete one object and confirm with a HEAD that it is gone."""
    return storage.delete_verified(key)
