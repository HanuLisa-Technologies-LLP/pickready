"""The analysis service: speaker counting and an AI-text estimate, over HTTP.

Three routes, called only by the ReadyPick backend over the private network:

    POST /diarize   multipart field `chunk` (audio/webm or audio/wav, at most
                    MAX_CHUNK_BYTES) -> {"speaker_count": int, "speech_seconds": float}
    POST /ai-text   {"text": str} -> {"probability_ai": float, "model": str, "note": str}
                    Behind AI_TEXT_ENABLED. Informational only; see app/ai_text.py.
    GET  /health    {"status": "ok" | "degraded", "diarization": ..., "ai_text": ...}

`/health` answers 200 whenever the process is serving. Whether each component
loaded is in the BODY, because a container whose diarization is off for lack
of a token is correctly configured for what it has, and restarting it until it
grows a token would be a health check that reports a configuration decision as
a crash. `/diarize` answers 503 with the same status string when the pipeline
is not there, so the backend never mistakes "unavailable" for "one speaker".

`create_app` takes the models as an argument so the tests inject fakes and
never touch torch; the module-level `app` loads the real ones at startup.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app import diarization
from app.ai_text import AI_TEXT_NOTE, detect
from app.components import AVAILABLE, DISABLED, Components, load_components
from app.config import Settings, settings_from_env
from app.diarization import ChunkTooLong, Decoder, UndecodableAudio, diarize

__all__ = ["ACCEPTED_CONTENT_TYPES", "app", "create_app"]

#: What the browser's MediaRecorder produces (`audio/webm;codecs=opus`) and the
#: uncompressed alternative the backend's own tests send. Compared on the media
#: type alone, so a codec parameter does not change the answer.
ACCEPTED_CONTENT_TYPES = frozenset({"audio/webm", "audio/wav", "audio/x-wav", "audio/wave"})


class DiarizeOut(BaseModel):
    speaker_count: int = Field(ge=0)
    speech_seconds: float = Field(ge=0.0)


class AiTextIn(BaseModel):
    text: str


class AiTextOut(BaseModel):
    """The estimate, and the sentence that must travel with it.

    `probability_ai` is unreliable against current models and informational
    only. It never feeds a warning, a termination, a score or a ranking.
    """

    probability_ai: float = Field(ge=0.0, le=1.0)
    model: str
    note: str


class HealthOut(BaseModel):
    status: str
    diarization: str
    ai_text: str


def _media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


async def _read_bounded(upload: UploadFile, limit: int) -> bytes:
    """Read at most `limit` bytes; one byte more is a 413 before the rest is read."""
    chunk = await upload.read(limit + 1)
    if len(chunk) > limit:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"the chunk exceeds MAX_CHUNK_BYTES ({limit} bytes)",
        )
    return chunk


def create_app(
    settings: Settings | None = None,
    components: Components | None = None,
    decoder: Decoder = diarization.decode_with_torchcodec,
) -> FastAPI:
    """Build the application. With `components` given, nothing is loaded at startup."""
    resolved = settings_from_env() if settings is None else settings

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.settings = resolved
        application.state.components = (
            await run_in_threadpool(load_components, resolved) if components is None else components
        )
        application.state.inference_slots = asyncio.Semaphore(resolved.inference_concurrency)
        yield

    application = FastAPI(
        title="ReadyPick analysis service",
        description=__doc__,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @application.get("/health", response_model=HealthOut)
    async def health(request: Request) -> HealthOut:
        loaded: Components = request.app.state.components
        return HealthOut(
            status="ok" if loaded.healthy else "degraded",
            diarization=loaded.diarization_status,
            ai_text=loaded.ai_text_status,
        )

    @application.post("/diarize", response_model=DiarizeOut)
    async def diarize_chunk(request: Request, chunk: UploadFile = File(...)) -> DiarizeOut:
        """Count the speakers in one chunk. The bytes live in memory and die here."""
        loaded: Components = request.app.state.components
        current: Settings = request.app.state.settings

        if loaded.diarization_pipeline is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=loaded.diarization_status)

        media_type = _media_type(chunk.content_type)
        if media_type not in ACCEPTED_CONTENT_TYPES:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"chunk must be one of {sorted(ACCEPTED_CONTENT_TYPES)}, got {media_type or 'none'}",
            )

        data = await _read_bounded(chunk, current.max_chunk_bytes)
        if not data:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="the chunk is empty")

        try:
            async with request.app.state.inference_slots:
                result = await run_in_threadpool(
                    diarize, data, loaded.diarization_pipeline, decoder, current
                )
        except UndecodableAudio as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
        except ChunkTooLong as error:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail=str(error)) from error
        finally:
            del data
        return DiarizeOut(speaker_count=result.speaker_count, speech_seconds=result.speech_seconds)

    @application.post("/ai-text", response_model=AiTextOut)
    async def ai_text(request: Request, body: AiTextIn) -> AiTextOut:
        """An AI-text estimate for one answer. Unreliable, informational only.

        Disabled by default (`AI_TEXT_ENABLED=false`), and the response repeats
        the caveat so no consumer can read the number without it.
        """
        loaded: Components = request.app.state.components
        current: Settings = request.app.state.settings

        if loaded.ai_text_status == DISABLED:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI-text detection is disabled (AI_TEXT_ENABLED=false)",
            )
        if loaded.ai_text_detector is None or loaded.ai_text_status != AVAILABLE:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=loaded.ai_text_status)

        text = body.text.strip()
        if not text:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="text is empty")
        if len(text) > current.ai_text_max_chars:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"text exceeds AI_TEXT_MAX_CHARS ({current.ai_text_max_chars})",
            )

        async with request.app.state.inference_slots:
            result = await run_in_threadpool(detect, text, loaded.ai_text_detector, current)
        return AiTextOut(probability_ai=result.probability_ai, model=result.model, note=AI_TEXT_NOTE)

    return application


app: Any = create_app()
