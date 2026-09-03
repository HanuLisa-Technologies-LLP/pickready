"""/health reports each component by name and never hides an absence."""
from __future__ import annotations

from app.components import AVAILABLE, DISABLED, Components, load_components
from tests.conftest import FakeDetector, FakePipeline, make_client, make_components, make_settings


def test_healthy_when_diarization_is_available_and_ai_text_is_disabled(client) -> None:
    assert client.get("/health").json() == {
        "status": "ok",
        "diarization": AVAILABLE,
        "ai_text": DISABLED,
    }


def test_the_missing_token_is_reported_in_the_exact_wording() -> None:
    components = make_components(pipeline=None, diarization_status="unavailable: HUGGINGFACE_TOKEN missing")
    with make_client(components) as client:
        body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["diarization"] == "unavailable: HUGGINGFACE_TOKEN missing"
    assert body["ai_text"] == DISABLED


def test_health_answers_200_even_when_degraded() -> None:
    components = make_components(pipeline=None, diarization_status="unavailable: HUGGINGFACE_TOKEN missing")
    with make_client(components) as client:
        assert client.get("/health").status_code == 200


def test_an_enabled_ai_text_that_failed_to_load_degrades_health() -> None:
    components = make_components(
        pipeline=FakePipeline(),
        detector=None,
        ai_text_status="unavailable: the model is not in the cache",
    )
    with make_client(components) as client:
        body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["ai_text"].startswith("unavailable:")


def test_all_available_is_ok() -> None:
    components = make_components(pipeline=FakePipeline(), detector=FakeDetector(), ai_text_status=AVAILABLE)
    with make_client(components) as client:
        assert client.get("/health").json()["status"] == "ok"


def test_loading_without_a_token_records_the_reason_instead_of_raising() -> None:
    """The real loader path, short of any model: no token, and the flag off."""
    loaded: Components = load_components(make_settings(HUGGINGFACE_TOKEN=""))
    assert loaded.diarization_pipeline is None
    assert loaded.diarization_status == "unavailable: HUGGINGFACE_TOKEN missing"
    assert loaded.ai_text_detector is None
    assert loaded.ai_text_status == DISABLED
    assert loaded.healthy is False


def test_the_docs_routes_are_not_served() -> None:
    """An internal service on a private network publishes no interactive docs."""
    with make_client(make_components(pipeline=FakePipeline())) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
