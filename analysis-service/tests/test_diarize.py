"""The /diarize contract and the speaker-count mapping.

The backend reads exactly two fields, `speaker_count` and `speech_seconds`,
and decides "second voice" on `speaker_count >= 2`. Each status code below is
one the backend's audio handler has to distinguish: a 503 means audio
monitoring is unavailable and is said so on the report; a 4xx means this chunk
was bad and the session carries on.
"""
from __future__ import annotations

import pytest

from app.components import AVAILABLE
from app.diarization import DiarizationResult, summarise
from tests.conftest import (
    FakeAnnotation,
    FakeDecoder,
    FakeDiarizeOutput,
    FakePipeline,
    make_client,
    make_components,
    make_settings,
    upload,
)


def test_one_speaker_maps_to_a_count_of_one(client, pipeline) -> None:
    response = client.post("/diarize", files=upload(b"\x1aE\xdf\xa3fake-webm"))
    assert response.status_code == 200, response.text
    assert response.json() == {"speaker_count": 1, "speech_seconds": 9.5}
    assert len(pipeline.calls) == 1
    assert pipeline.calls[0]["sample_rate"] == 16000


@pytest.mark.parametrize(
    ("speakers", "expected"),
    [
        ([], 0),
        (["SPEAKER_00"], 1),
        (["SPEAKER_00", "SPEAKER_01"], 2),
        (["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"], 3),
    ],
)
def test_speaker_count_is_the_number_of_distinct_labels(speakers, expected) -> None:
    components = make_components(pipeline=FakePipeline(speakers=speakers, speech_seconds=3.25))
    with make_client(components) as client:
        response = client.post("/diarize", files=upload(b"audio"))
    assert response.status_code == 200
    assert response.json() == {"speaker_count": expected, "speech_seconds": 3.25}


def test_summarise_reads_both_pyannote_return_shapes() -> None:
    annotation = FakeAnnotation(["a", "b"], 4.0)
    assert summarise(annotation) == DiarizationResult(speaker_count=2, speech_seconds=4.0)
    assert summarise(FakeDiarizeOutput(annotation)) == DiarizationResult(2, 4.0)


def test_wav_is_accepted_and_the_codec_parameter_is_ignored(client) -> None:
    assert client.post("/diarize", files=upload(b"RIFF", "audio/wav")).status_code == 200
    assert client.post("/diarize", files=upload(b"RIFF", "audio/x-wav")).status_code == 200
    assert client.post("/diarize", files=upload(b"x", "audio/webm;codecs=opus")).status_code == 200


def test_an_unaccepted_media_type_is_415_and_never_decoded(client, decoder, pipeline) -> None:
    response = client.post("/diarize", files=upload(b"%PDF-1.7", "application/pdf"))
    assert response.status_code == 415
    assert "audio/webm" in response.json()["detail"]
    assert decoder.received == []
    assert pipeline.calls == []


def test_a_missing_chunk_field_is_422(client) -> None:
    assert client.post("/diarize", files={"audio": ("a.webm", b"x", "audio/webm")}).status_code == 422


def test_an_empty_chunk_is_422(client, decoder) -> None:
    response = client.post("/diarize", files=upload(b""))
    assert response.status_code == 422
    assert response.json()["detail"] == "the chunk is empty"
    assert decoder.received == []


def test_an_oversized_chunk_is_413_before_it_is_decoded() -> None:
    decoder = FakeDecoder()
    settings = make_settings(MAX_CHUNK_BYTES="16")
    with make_client(make_components(pipeline=FakePipeline()), decoder=decoder, settings=settings) as client:
        assert client.post("/diarize", files=upload(b"x" * 16)).status_code == 200
        response = client.post("/diarize", files=upload(b"x" * 17))
    assert response.status_code == 413
    assert "MAX_CHUNK_BYTES" in response.json()["detail"]
    assert decoder.received == [b"x" * 16]


def test_undecodable_bytes_are_422(pipeline) -> None:
    with make_client(make_components(pipeline=pipeline), decoder=FakeDecoder(fail=True)) as client:
        response = client.post("/diarize", files=upload(b"not audio"))
    assert response.status_code == 422
    assert "could not be decoded" in response.json()["detail"]
    assert pipeline.calls == []


def test_audio_longer_than_the_ceiling_is_413_and_never_reaches_the_model(pipeline) -> None:
    settings = make_settings(MAX_CHUNK_SECONDS="30")
    with make_client(make_components(pipeline=pipeline), decoder=FakeDecoder(seconds=31.0), settings=settings) as client:
        response = client.post("/diarize", files=upload(b"long"))
    assert response.status_code == 413
    assert "31.0s" in response.json()["detail"]
    assert pipeline.calls == []


def test_without_a_token_diarize_is_503_with_the_health_wording() -> None:
    message = "unavailable: HUGGINGFACE_TOKEN missing"
    components = make_components(pipeline=None, diarization_status=message)
    with make_client(components) as client:
        response = client.post("/diarize", files=upload(b"audio"))
    assert response.status_code == 503
    assert response.json()["detail"] == message


def test_the_legacy_annotation_return_shape_is_counted_the_same_way() -> None:
    legacy = FakePipeline(speakers=["x", "y"], speech_seconds=1.0, legacy=True)
    with make_client(make_components(pipeline=legacy, diarization_status=AVAILABLE)) as client:
        assert client.post("/diarize", files=upload(b"audio")).json()["speaker_count"] == 2
