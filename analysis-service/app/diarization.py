"""Speaker counting over one audio chunk, entirely in memory.

THE INVARIANT THIS MODULE EXISTS TO KEEP (proctoring spec sections 2, 3.4, 10)
-------------------------------------------------------------------------------
The audio chunk is the only media that leaves a candidate's browser. It arrives
as bytes, is wrapped in an `io.BytesIO`, is decoded and diarized from that
buffer, and the buffer is closed and deleted before the result is returned.
Nothing here opens a file for writing, creates a temporary file, or hands the
bytes to anything that would. `tests/test_no_disk.py` proves it by making every
write path raise during a request, and by reading this module's source.

WHAT IS PINNED, AND HOW
-----------------------
`pyannote/speaker-diarization-3.1` is loaded at the revision the settings name.
Its `config.yaml` names the segmentation and embedding models by repository id
only, which would resolve to whatever those repositories' `main` points at on
the day the cache was filled. So the pipeline is built from the config DICT
with those two entries rewritten to `{"checkpoint": ..., "revision": ...}`,
which is the form pyannote's own model getter accepts. Three revisions, all
from `Settings`, none from a branch name.

The hub client runs OFFLINE in the image (`HF_HUB_OFFLINE=1`): the weights were
put there by `scripts/download_models.py` at build time, and a weight that is
missing is reported at `/health` as unavailable rather than fetched on the fly
by a service that is supposed to have no reason to reach the internet.
"""
from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.config import Settings

__all__ = [
    "ChunkTooLong",
    "DecodedAudio",
    "DiarizationResult",
    "DiarizationUnavailable",
    "UndecodableAudio",
    "decode_with_torchcodec",
    "diarize",
    "load_pipeline",
    "summarise",
]


class DiarizationUnavailable(RuntimeError):
    """The pipeline cannot be loaded. The message is what `/health` reports."""


class UndecodableAudio(ValueError):
    """The bytes are not audio the decoder understands."""


class ChunkTooLong(ValueError):
    """The decoded audio exceeds `Settings.max_chunk_seconds`."""


@dataclass(frozen=True)
class DecodedAudio:
    """One mono waveform, already resampled to the target rate.

    `waveform` is whatever tensor type the decoder produced; the contract with
    the pipeline is pyannote's `{"waveform": (channel, time), "sample_rate"}`
    and this module never inspects the values. `num_samples` is carried
    separately so the duration guard needs no tensor library.
    """

    waveform: Any
    sample_rate: int
    num_samples: int

    @property
    def seconds(self) -> float:
        return self.num_samples / self.sample_rate


@dataclass(frozen=True)
class DiarizationResult:
    speaker_count: int
    speech_seconds: float


class Pipeline(Protocol):
    """What `diarize` needs from a pyannote pipeline: a call over the waveform."""

    def __call__(self, file: dict[str, Any]) -> Any: ...


Decoder = Callable[[io.BytesIO, int], DecodedAudio]


def decode_with_torchcodec(buffer: io.BytesIO, target_sample_rate: int) -> DecodedAudio:
    """Decode WebM/Opus or WAV from an in-memory buffer to 16 kHz mono.

    torchcodec is already a dependency of pyannote.audio 4 and reads from a
    file-like object directly, so no second media library and no path on disk
    are involved. Imported inside the function so the HTTP contract can be
    tested on a machine with no torch at all.
    """
    from torchcodec.decoders import AudioDecoder

    try:
        decoder = AudioDecoder(buffer, sample_rate=target_sample_rate, num_channels=1)
        samples = decoder.get_all_samples()
    except Exception as error:  # torchcodec raises RuntimeError and ValueError for bad input
        raise UndecodableAudio(f"the chunk could not be decoded as audio: {error}") from error

    waveform = samples.data
    if waveform.dim() != 2 or waveform.shape[0] != 1:
        raise UndecodableAudio(
            f"expected one channel after downmixing, got shape {tuple(waveform.shape)}"
        )
    return DecodedAudio(
        waveform=waveform,
        sample_rate=int(samples.sample_rate),
        num_samples=int(waveform.shape[1]),
    )


def load_pipeline(settings: Settings) -> Pipeline:
    """Load the pinned diarization pipeline, or raise `DiarizationUnavailable`.

    Refuses without a token even though the image runs offline: the gated
    models' licence is accepted per account, and a deployment that could run
    them with no account attached would be one nobody had accepted it for.
    """
    if not settings.huggingface_token:
        raise DiarizationUnavailable("unavailable: HUGGINGFACE_TOKEN missing")

    try:
        import torch
        import yaml
        from huggingface_hub import hf_hub_download
        from pyannote.audio import Pipeline as PyannotePipeline
    except ImportError as error:
        raise DiarizationUnavailable(f"unavailable: {error}") from error

    if settings.torch_threads:
        torch.set_num_threads(settings.torch_threads)

    try:
        config_path = hf_hub_download(
            settings.diarization_model,
            "config.yaml",
            revision=settings.diarization_revision,
            cache_dir=settings.model_cache_dir,
            token=settings.huggingface_token,
        )
    except Exception as error:
        raise DiarizationUnavailable(
            f"unavailable: {settings.diarization_model}@{settings.diarization_revision} "
            f"is not in the model cache ({settings.model_cache_dir}); rebuild the image "
            f"with the huggingface_token build secret ({type(error).__name__}: {error})"
        ) from error

    with open(config_path, encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    params = config["pipeline"]["params"]
    params["segmentation"] = {
        "checkpoint": settings.segmentation_model,
        "revision": settings.segmentation_revision,
    }
    params["embedding"] = {
        "checkpoint": settings.embedding_model,
        "revision": settings.embedding_revision,
    }

    try:
        pipeline = PyannotePipeline.from_pretrained(
            config,
            token=settings.huggingface_token,
            cache_dir=settings.model_cache_dir,
        )
    except Exception as error:
        raise DiarizationUnavailable(
            f"unavailable: the pipeline failed to load ({type(error).__name__}: {error})"
        ) from error
    if pipeline is None:
        raise DiarizationUnavailable("unavailable: pyannote returned no pipeline for the config")
    return pipeline


def summarise(output: Any) -> DiarizationResult:
    """Reduce a pipeline output to the two numbers the backend reads.

    pyannote 4 returns a `DiarizeOutput` whose `speaker_diarization` is the
    `Annotation`; the legacy path returns the `Annotation` itself. Both are the
    library's own types and both are handled here, once.
    """
    annotation = getattr(output, "speaker_diarization", output)
    speakers = annotation.labels()
    speech = annotation.get_timeline().support().duration()
    return DiarizationResult(speaker_count=len(speakers), speech_seconds=float(speech))


def diarize(chunk: bytes, pipeline: Pipeline, decoder: Decoder, settings: Settings) -> DiarizationResult:
    """Count speakers in `chunk`, in memory, and destroy the buffer.

    The order is the guarantee: decode from the `BytesIO`, close and delete it,
    then run the model over the waveform tensor and delete that too. The caller
    holds the only other reference to `chunk` and drops it on return.
    """
    buffer = io.BytesIO(chunk)
    try:
        decoded = decoder(buffer, settings.target_sample_rate)
    finally:
        buffer.close()
        del buffer

    if decoded.seconds > settings.max_chunk_seconds:
        raise ChunkTooLong(
            f"the chunk is {decoded.seconds:.1f}s of audio; the ceiling is "
            f"{settings.max_chunk_seconds:g}s"
        )

    try:
        output = pipeline({"waveform": decoded.waveform, "sample_rate": decoded.sample_rate})
    finally:
        del decoded
    return summarise(output)
