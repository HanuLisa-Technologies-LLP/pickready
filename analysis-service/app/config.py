"""Every configurable value of the analysis service, read from the environment.

WHY ONE FROZEN OBJECT
---------------------
The backend reads its thresholds through `services/proctoring/config.py` for a
stated reason: a number a module carries on its own drifts from the one an
operator thinks is in force. The same discipline holds here. Nothing in
`diarization.py`, `ai_text.py` or `main.py` carries a literal ceiling; each
reads a field of `Settings`, and each field has exactly one documented default
in `DEFAULTS` below, which `README.md` reproduces as a table.

The model identifiers and revisions live here too, and that is deliberate: a
revision pinned in a loader is a revision nobody sees in a config review, and
the diarization pipeline is gated on Hugging Face, so which commit was accepted
is a fact worth keeping in one place.

A malformed value fails at import with the variable named. A boolean that
silently read "maybe" as false would be a feature flag nobody can trust.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["DEFAULTS", "Settings", "settings_from_env"]

#: The documented default of every environment variable the service reads.
#: Keys are the exact variable names. `README.md` carries the same table with
#: the reason beside each; `tests/test_config.py` asserts the two agree.
DEFAULTS: dict[str, str] = {
    # Empty means diarization is unavailable, stated as such at /health.
    "HUGGINGFACE_TOKEN": "",
    # The AI-text detector ships DISABLED: it is unreliable against current
    # models and informational only when it is on.
    "AI_TEXT_ENABLED": "false",
    # Where the pre-downloaded weights live. The image sets /models and runs
    # the hub client offline against it; a local checkout uses ./models.
    "MODEL_CACHE_DIR": "models",
    "DIARIZATION_MODEL": "pyannote/speaker-diarization-3.1",
    "DIARIZATION_REVISION": "84fd25912480287da0247647c3d2b4853cb3ee5d",
    "SEGMENTATION_MODEL": "pyannote/segmentation-3.0",
    "SEGMENTATION_REVISION": "e66f3d3b9eb0873085418a7b813d3b369bf160bb",
    # The speaker-embedding model the 3.1 pipeline config names. Not gated.
    # The specification pins the two repositories above and is silent on this
    # one; the revision is upstream main as resolved on 2026-09-02, recorded
    # here so it stops moving.
    "EMBEDDING_MODEL": "pyannote/wespeaker-voxceleb-resnet34-LM",
    "EMBEDDING_REVISION": "837717ddb9ff5507820346191109dc79c958d614",
    "AI_TEXT_MODEL": "openai-community/roberta-base-openai-detector",
    "AI_TEXT_REVISION": "6cba99c003b711c7fe94f8a3aa2be35a792cb6fa",
    # An upload larger than this is refused before it is read in full. Matches
    # the backend's proctoring_audio_max_chunk_bytes (2 MiB).
    "MAX_CHUNK_BYTES": "2097152",
    # A chunk is about fifteen seconds; twice that is the ceiling on decoded
    # duration, refused after decoding rather than guessed from the byte count.
    "MAX_CHUNK_SECONDS": "30",
    # What the decoder resamples to. The pyannote models were trained at 16 kHz.
    "TARGET_SAMPLE_RATE": "16000",
    # An answer longer than this is refused; the detector reads at most
    # AI_TEXT_MAX_TOKENS of it anyway, and a request body has to end somewhere.
    "AI_TEXT_MAX_CHARS": "20000",
    # RoBERTa's context window.
    "AI_TEXT_MAX_TOKENS": "512",
    # How many model calls may run at once. CPU inference on a small task does
    # not benefit from parallelism, and two diarizations in flight double the
    # peak memory of the task.
    "INFERENCE_CONCURRENCY": "1",
    # 0 leaves torch's own thread count in place.
    "TORCH_THREADS": "0",
}

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})


def _read(environ: Mapping[str, str], name: str) -> str:
    return environ.get(name, DEFAULTS[name]).strip()


def _bool(environ: Mapping[str, str], name: str) -> bool:
    raw = _read(environ, name).lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ValueError(f"{name} must be a boolean (true/false), got {raw!r}")


def _int(environ: Mapping[str, str], name: str, minimum: int) -> int:
    raw = _read(environ, name)
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from error
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}")
    return value


def _float(environ: Mapping[str, str], name: str, minimum: float) -> float:
    raw = _read(environ, name)
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a number, got {raw!r}") from error
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    huggingface_token: str
    ai_text_enabled: bool
    model_cache_dir: str
    diarization_model: str
    diarization_revision: str
    segmentation_model: str
    segmentation_revision: str
    embedding_model: str
    embedding_revision: str
    ai_text_model: str
    ai_text_revision: str
    max_chunk_bytes: int
    max_chunk_seconds: float
    target_sample_rate: int
    ai_text_max_chars: int
    ai_text_max_tokens: int
    inference_concurrency: int
    torch_threads: int

    @property
    def ai_text_model_id(self) -> str:
        """The identifier the /ai-text response reports: repository at revision."""
        return f"{self.ai_text_model}@{self.ai_text_revision}"


def settings_from_env(environ: Mapping[str, str] | None = None) -> Settings:
    """Build `Settings` from `environ` (the process environment by default)."""
    env = os.environ if environ is None else environ
    return Settings(
        huggingface_token=_read(env, "HUGGINGFACE_TOKEN"),
        ai_text_enabled=_bool(env, "AI_TEXT_ENABLED"),
        model_cache_dir=_read(env, "MODEL_CACHE_DIR"),
        diarization_model=_read(env, "DIARIZATION_MODEL"),
        diarization_revision=_read(env, "DIARIZATION_REVISION"),
        segmentation_model=_read(env, "SEGMENTATION_MODEL"),
        segmentation_revision=_read(env, "SEGMENTATION_REVISION"),
        embedding_model=_read(env, "EMBEDDING_MODEL"),
        embedding_revision=_read(env, "EMBEDDING_REVISION"),
        ai_text_model=_read(env, "AI_TEXT_MODEL"),
        ai_text_revision=_read(env, "AI_TEXT_REVISION"),
        max_chunk_bytes=_int(env, "MAX_CHUNK_BYTES", minimum=1),
        max_chunk_seconds=_float(env, "MAX_CHUNK_SECONDS", minimum=1.0),
        target_sample_rate=_int(env, "TARGET_SAMPLE_RATE", minimum=8000),
        ai_text_max_chars=_int(env, "AI_TEXT_MAX_CHARS", minimum=1),
        ai_text_max_tokens=_int(env, "AI_TEXT_MAX_TOKENS", minimum=8),
        inference_concurrency=_int(env, "INFERENCE_CONCURRENCY", minimum=1),
        torch_threads=_int(env, "TORCH_THREADS", minimum=0),
    )
