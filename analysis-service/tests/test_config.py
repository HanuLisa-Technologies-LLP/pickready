"""Every setting has a documented default, and a malformed value fails by name."""
from __future__ import annotations

import pathlib
import re

import pytest

from app.config import DEFAULTS, Settings, settings_from_env

README = pathlib.Path(__file__).resolve().parents[1] / "README.md"


def test_the_defaults_alone_build_a_settings_object() -> None:
    settings = settings_from_env({})
    assert isinstance(settings, Settings)
    assert settings.huggingface_token == ""
    assert settings.ai_text_enabled is False
    assert settings.max_chunk_bytes == 2 * 1024 * 1024
    assert settings.max_chunk_seconds == 30.0
    assert settings.target_sample_rate == 16000
    assert settings.inference_concurrency == 1
    assert settings.diarization_revision == "84fd25912480287da0247647c3d2b4853cb3ee5d"
    assert settings.segmentation_revision == "e66f3d3b9eb0873085418a7b813d3b369bf160bb"
    assert settings.ai_text_revision == "6cba99c003b711c7fe94f8a3aa2be35a792cb6fa"
    assert settings.ai_text_model_id == "openai-community/roberta-base-openai-detector@6cba99c003b711c7fe94f8a3aa2be35a792cb6fa"


def test_every_settings_field_comes_from_a_documented_variable() -> None:
    """A field with no DEFAULTS entry is a value read from nowhere anybody can see."""
    fields = set(Settings.__dataclass_fields__)
    documented = {name.lower() for name in DEFAULTS}
    assert fields == documented, {
        "undocumented fields": sorted(fields - documented),
        "unused variables": sorted(documented - fields),
    }


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " True "])
def test_true_spellings(raw: str) -> None:
    assert settings_from_env({"AI_TEXT_ENABLED": raw}).ai_text_enabled is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", ""])
def test_false_spellings(raw: str) -> None:
    assert settings_from_env({"AI_TEXT_ENABLED": raw}).ai_text_enabled is False


def test_an_ambiguous_boolean_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="AI_TEXT_ENABLED must be a boolean"):
        settings_from_env({"AI_TEXT_ENABLED": "maybe"})


@pytest.mark.parametrize(
    ("name", "raw", "message"),
    [
        ("MAX_CHUNK_BYTES", "abc", "must be an integer"),
        ("MAX_CHUNK_BYTES", "0", "must be at least 1"),
        ("MAX_CHUNK_SECONDS", "0.5", "must be at least 1"),
        ("TARGET_SAMPLE_RATE", "4000", "must be at least 8000"),
        ("INFERENCE_CONCURRENCY", "0", "must be at least 1"),
        ("TORCH_THREADS", "-1", "must be at least 0"),
        ("AI_TEXT_MAX_TOKENS", "4", "must be at least 8"),
    ],
)
def test_out_of_range_numbers_are_refused_by_name(name: str, raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=f"{name} {message}"):
        settings_from_env({name: raw})


def test_the_readme_table_matches_the_code_defaults() -> None:
    """The README is where an operator reads the defaults; it must not drift."""
    text = README.read_text(encoding="utf-8")
    rows = dict(re.findall(r"^\| `([A-Z_]+)` \| `([^`]*)` \|", text, re.MULTILINE))
    assert rows, "the README variable table was not found"
    documented = {name: ("" if value == "(empty)" else value) for name, value in rows.items()}
    assert documented == DEFAULTS
