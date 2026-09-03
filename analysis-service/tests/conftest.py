"""Fakes for the two models, so no test needs torch or a downloaded weight.

The fakes stand in for pyannote's pipeline output and for the decoder. What
they do NOT stand in for is the orchestration in `app/diarization.py` and
`app/main.py`: every request in these tests runs the real `diarize`, the real
bounded read, the real content-type gate and the real error mapping. Only the
model call and the media decode are replaced.
"""
from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.components import AVAILABLE, DISABLED, Components
from app.config import Settings, settings_from_env
from app.diarization import DecodedAudio, UndecodableAudio
from app.main import create_app

DEFAULT_SAMPLE_RATE = 16000


class FakeTimeline:
    def __init__(self, duration: float) -> None:
        self._duration = duration

    def support(self) -> FakeTimeline:
        return self

    def duration(self) -> float:
        return self._duration


class FakeAnnotation:
    """The two calls `summarise` makes on a pyannote `Annotation`."""

    def __init__(self, speakers: list[str], speech_seconds: float) -> None:
        self._speakers = speakers
        self._speech_seconds = speech_seconds

    def labels(self) -> list[str]:
        return list(self._speakers)

    def get_timeline(self) -> FakeTimeline:
        return FakeTimeline(self._speech_seconds)


@dataclass
class FakeDiarizeOutput:
    """pyannote 4's return shape: the annotation sits on `speaker_diarization`."""

    speaker_diarization: FakeAnnotation


@dataclass
class FakePipeline:
    speakers: list[str] = field(default_factory=lambda: ["SPEAKER_00"])
    speech_seconds: float = 9.5
    legacy: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, file: dict[str, Any]) -> Any:
        self.calls.append(file)
        annotation = FakeAnnotation(self.speakers, self.speech_seconds)
        return annotation if self.legacy else FakeDiarizeOutput(annotation)


class Waveform:
    """A marker the fake decoder hands to the fake pipeline. Never inspected."""


@dataclass
class FakeDecoder:
    seconds: float = 15.0
    fail: bool = False
    buffers: list[io.BytesIO] = field(default_factory=list)
    received: list[bytes] = field(default_factory=list)

    def __call__(self, buffer: io.BytesIO, target_sample_rate: int) -> DecodedAudio:
        assert isinstance(buffer, io.BytesIO), "the decoder must receive an in-memory buffer"
        self.buffers.append(buffer)
        self.received.append(buffer.getvalue())
        if self.fail:
            raise UndecodableAudio("the chunk could not be decoded as audio")
        return DecodedAudio(
            waveform=Waveform(),
            sample_rate=target_sample_rate,
            num_samples=int(self.seconds * target_sample_rate),
        )


@dataclass
class FakeDetector:
    probability: float = 0.25
    texts: list[str] = field(default_factory=list)

    def __call__(self, text: str) -> float:
        self.texts.append(text)
        return self.probability


def make_settings(**overrides: str) -> Settings:
    return settings_from_env(overrides)


def make_components(
    pipeline: FakePipeline | None = None,
    diarization_status: str = AVAILABLE,
    detector: FakeDetector | None = None,
    ai_text_status: str = DISABLED,
) -> Components:
    return Components(
        diarization_pipeline=pipeline,
        diarization_status=diarization_status,
        ai_text_detector=detector,
        ai_text_status=ai_text_status,
    )


def make_client(
    components: Components,
    decoder: FakeDecoder | None = None,
    settings: Settings | None = None,
) -> TestClient:
    application = create_app(
        settings=settings or make_settings(),
        components=components,
        decoder=decoder or FakeDecoder(),
    )
    return TestClient(application)


@pytest.fixture
def pipeline() -> FakePipeline:
    return FakePipeline()


@pytest.fixture
def decoder() -> FakeDecoder:
    return FakeDecoder()


@pytest.fixture
def client(pipeline: FakePipeline, decoder: FakeDecoder) -> Iterator[TestClient]:
    with make_client(make_components(pipeline=pipeline), decoder=decoder) as test_client:
        yield test_client


def upload(data: bytes, content_type: str = "audio/webm") -> dict[str, tuple[str, bytes, str]]:
    return {"chunk": ("chunk.webm", data, content_type)}
