"""The /ai-text contract, the flag, and the caveat that must travel with the number."""
from __future__ import annotations

import pytest

from app.ai_text import AI_TEXT_NOTE, AiTextUnavailable, ai_label_index
from app.components import AVAILABLE, DISABLED
from tests.conftest import FakeDetector, make_client, make_components, make_settings


def test_disabled_by_default_is_a_503_that_names_the_flag() -> None:
    with make_client(make_components(ai_text_status=DISABLED)) as client:
        response = client.post("/ai-text", json={"text": "An answer of some length."})
    assert response.status_code == 503
    assert response.json()["detail"] == "AI-text detection is disabled (AI_TEXT_ENABLED=false)"


def test_enabled_returns_the_probability_the_model_id_and_the_caveat() -> None:
    detector = FakeDetector(probability=0.73)
    settings = make_settings(AI_TEXT_ENABLED="true")
    components = make_components(detector=detector, ai_text_status=AVAILABLE)
    with make_client(components, settings=settings) as client:
        response = client.post("/ai-text", json={"text": "  The candidate's answer.  "})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["probability_ai"] == 0.73
    assert body["model"] == "openai-community/roberta-base-openai-detector@6cba99c003b711c7fe94f8a3aa2be35a792cb6fa"
    assert body["note"] == AI_TEXT_NOTE
    assert "informational only" in body["note"].lower()
    assert "unreliable" in body["note"].lower()
    assert detector.texts == ["The candidate's answer."], "the text is stripped, not otherwise altered"


def test_the_probability_is_clamped_to_the_unit_interval() -> None:
    components = make_components(detector=FakeDetector(probability=1.7), ai_text_status=AVAILABLE)
    with make_client(components) as client:
        assert client.post("/ai-text", json={"text": "x"}).json()["probability_ai"] == 1.0


def test_blank_text_is_422() -> None:
    components = make_components(detector=FakeDetector(), ai_text_status=AVAILABLE)
    with make_client(components) as client:
        response = client.post("/ai-text", json={"text": "   \n"})
    assert response.status_code == 422
    assert response.json()["detail"] == "text is empty"


def test_text_over_the_ceiling_is_413() -> None:
    detector = FakeDetector()
    components = make_components(detector=detector, ai_text_status=AVAILABLE)
    with make_client(components, settings=make_settings(AI_TEXT_MAX_CHARS="10")) as client:
        assert client.post("/ai-text", json={"text": "x" * 10}).status_code == 200
        response = client.post("/ai-text", json={"text": "x" * 11})
    assert response.status_code == 413
    assert "AI_TEXT_MAX_CHARS" in response.json()["detail"]
    assert detector.texts == ["x" * 10]


def test_a_missing_text_field_is_422() -> None:
    components = make_components(detector=FakeDetector(), ai_text_status=AVAILABLE)
    with make_client(components) as client:
        assert client.post("/ai-text", json={"answer": "x"}).status_code == 422


def test_an_enabled_but_unloadable_detector_is_503_with_its_reason() -> None:
    reason = "unavailable: openai-community/roberta-base-openai-detector@6cba99c003b711c7fe94f8a3aa2be35a792cb6fa is not in the model cache (models)"
    with make_client(make_components(detector=None, ai_text_status=reason)) as client:
        response = client.post("/ai-text", json={"text": "x"})
    assert response.status_code == 503
    assert response.json()["detail"] == reason


@pytest.mark.parametrize(
    ("id2label", "expected"),
    [
        ({0: "Fake", 1: "Real"}, 0),
        ({0: "Real", 1: "Fake"}, 1),
        ({"0": "LABEL_A", "1": "fake"}, 1),
    ],
)
def test_the_machine_written_index_is_read_by_label_name(id2label, expected) -> None:
    assert ai_label_index(id2label) == expected


@pytest.mark.parametrize("id2label", [{0: "LABEL_0", 1: "LABEL_1"}, {0: "Fake", 1: "Fake"}, {}])
def test_a_config_without_exactly_one_fake_label_refuses_to_load(id2label) -> None:
    with pytest.raises(AiTextUnavailable, match="exactly one 'Fake' label"):
        ai_label_index(id2label)
