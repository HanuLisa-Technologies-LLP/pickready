"""The code-execution port: selection, the production refusal, limits, matching.

No network and no database. The adapter's wire behaviour is in
`test_code_execution_judge0_adapter.py`; this module pins everything a domain
caller relies on WITHOUT knowing which sandbox answers.
"""
from __future__ import annotations

import pathlib
import re

import pytest
from pydantic import ValidationError

from app.core.config import CODE_EXECUTION_LANGUAGE_KEYS, Settings, get_settings
from app.services import code_execution
from app.services.code_execution import languages, limits
from app.services.code_execution.errors import (
    ExecutionNotConfigured,
    ExecutionTicketLost,
    ExecutionUnavailable,
)
from app.services.code_execution.fake import FakeProvider, ScriptedRun
from app.services.code_execution.judge0 import Judge0Provider
from app.services.code_execution.provider import (
    ExecutionOutcome,
    TestInput,
    get_provider,
    is_enabled,
    outputs_match,
    override_provider,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
JUDGE0_CONF = REPO / "infra" / "modules" / "code_sandbox" / "judge0.conf.tftpl"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """The cached settings object, with code execution in a known state."""
    current = get_settings()
    monkeypatch.setattr(current, "environment", "test")
    monkeypatch.setattr(current, "code_execution_backend", "disabled")
    monkeypatch.setattr(current, "judge0_url", "")
    monkeypatch.setattr(current, "judge0_auth_token", "")
    monkeypatch.setattr(current, "code_execution_languages", "python,java,cpp,javascript")
    monkeypatch.setattr(current, "code_execution_cpu_multipliers", "java:2.0,javascript:1.5")
    return current


# ── outputs_match ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "actual,expected,matches",
    [
        ("5\n", "5", True),
        ("5", "5\n\n\n", True),
        ("5\r\n", "5\n", True),
        ("a  \nb\t\n", "a\nb", True),
        ("1 2 3", "1 2 3", True),
        # Leading whitespace is significant: indentation can be the answer.
        ("  5", "5", False),
        ("5", "  5", False),
        ("1 2  3", "1 2 3", False),
        ("5\n\n6", "5\n6", False),
        ("", "", True),
        ("", "0", False),
    ],
)
def test_outputs_match_normalises_line_endings_and_trailing_space_only(
    actual: str, expected: str, matches: bool
) -> None:
    assert outputs_match(actual, expected) is matches


# ── Selection ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "backend,url,token,enabled",
    [
        ("disabled", "", "", False),
        ("disabled", "http://judge0.readypick.local:2358", "t", False),
        ("judge0", "", "t", False),
        ("judge0", "http://judge0.readypick.local:2358", "", False),
        ("judge0", "   ", "t", False),
        ("judge0", "http://judge0.readypick.local:2358", "t", True),
    ],
)
def test_is_enabled_needs_the_backend_the_address_and_the_token(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, backend: str, url: str, token: str, enabled: bool
) -> None:
    monkeypatch.setattr(settings, "code_execution_backend", backend)
    monkeypatch.setattr(settings, "judge0_url", url)
    monkeypatch.setattr(settings, "judge0_auth_token", token)
    assert is_enabled() is enabled
    if enabled:
        assert isinstance(get_provider(), Judge0Provider)
    else:
        with pytest.raises(ExecutionNotConfigured) as caught:
            get_provider()
        assert caught.value.reason in {"disabled", "missing_configuration"}


def test_disabled_is_the_default(settings: Settings) -> None:
    assert Settings.model_fields["code_execution_backend"].default == "disabled"
    assert is_enabled() is False


def test_the_override_is_installed_for_the_block_only(settings: Settings) -> None:
    fake = FakeProvider()
    with override_provider(fake) as installed:
        assert installed is fake
        assert get_provider() is fake
        assert is_enabled() is True
    assert is_enabled() is False
    with pytest.raises(ExecutionNotConfigured):
        get_provider()


def test_the_override_is_refused_in_production(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(RuntimeError, match="refused in production"):
        with override_provider(FakeProvider()):
            pytest.fail("the block must not run")
    assert is_enabled() is False


def test_the_fake_is_not_a_configurable_backend() -> None:
    with pytest.raises(ValidationError):
        Settings(code_execution_backend="fake")


# ── Settings validation ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "overrides",
    [
        {"code_execution_backend": "local"},
        {"code_execution_languages": "python,cobol"},
        {"code_execution_languages": ""},
        {"code_execution_cpu_multipliers": "java"},
        {"code_execution_cpu_multipliers": "java:fast"},
        {"code_execution_cpu_multipliers": "java:0.5"},
        {"code_execution_cpu_multipliers": "go:2.0"},
        {"code_execution_backend": "judge0", "judge0_language_ids": "python:71"},
        {"judge0_language_ids": "python:seventy"},
        {"judge0_language_ids": "python:71,python:72"},
        {"code_execution_cpu_seconds": 0},
        {"code_execution_memory_mb": -1},
        {"code_execution_wall_seconds": 1.0, "code_execution_cpu_seconds": 2.0},
        {"code_execution_cpu_extra_seconds": -0.1},
    ],
)
def test_a_malformed_configuration_is_refused_at_boot(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        Settings(**overrides)


def test_a_missing_url_or_token_is_not_a_boot_refusal() -> None:
    configured = Settings(code_execution_backend="JUDGE0", judge0_url="", judge0_auth_token="")
    assert configured.code_execution_backend == "judge0"


# ── Languages and limits ─────────────────────────────────────────────────────


def test_the_language_registry_matches_the_configured_vocabulary() -> None:
    assert tuple(languages.LANGUAGE_SPECS) == CODE_EXECUTION_LANGUAGE_KEYS
    assert set(CODE_EXECUTION_LANGUAGE_KEYS) == {"python", "java", "cpp", "javascript"}
    assert "Main" in languages.LANGUAGE_SPECS["java"].source_hint


def test_only_configured_languages_are_offered(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "code_execution_languages", "python,cpp")
    assert [spec.key for spec in languages.configured_languages()] == ["python", "cpp"]
    with pytest.raises(KeyError):
        languages.language_spec("java")


def test_the_cpu_multiplier_scales_cpu_and_wall_together(settings: Settings) -> None:
    python = limits.for_language("python")
    java = limits.for_language("java")
    assert (python.cpu_seconds, python.wall_seconds) == (2.0, 5.0)
    assert (java.cpu_seconds, java.wall_seconds) == (4.0, 10.0)
    assert java.memory_kb == settings.code_execution_memory_mb * 1024


def test_limits_above_the_sandbox_maxima_are_refused_not_clamped(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "code_execution_cpu_multipliers", "java:3.0")
    with pytest.raises(ExecutionNotConfigured) as caught:
        limits.for_language("java")
    assert caught.value.reason == "limits_exceed_host"


def test_every_language_fits_the_sandbox_under_the_default_settings() -> None:
    defaults = Settings()
    for key in CODE_EXECUTION_LANGUAGE_KEYS:
        factor = defaults.code_execution_cpu_multiplier_map.get(key, 1.0)
        assert defaults.code_execution_cpu_seconds * factor <= limits.HOST_MAXIMA["cpu_seconds"], key
        assert defaults.code_execution_wall_seconds * factor <= limits.HOST_MAXIMA["wall_seconds"], key
    assert defaults.code_execution_memory_mb * 1024 <= limits.HOST_MAXIMA["memory_kb"]
    assert defaults.code_execution_max_file_kb <= limits.HOST_MAXIMA["max_file_kb"]
    assert defaults.code_execution_max_processes <= limits.HOST_MAXIMA["max_processes"]


def test_host_maxima_mirror_the_sandbox_configuration_template() -> None:
    """Two fences, one set of numbers. If the host caps move, this moves too."""
    text = JUDGE0_CONF.read_text(encoding="utf-8")

    def value(name: str) -> float:
        match = re.search(rf"^{name}=([0-9.]+)\s*$", text, re.MULTILINE)
        assert match, f"{name} is not set in {JUDGE0_CONF.name}"
        return float(match.group(1))

    assert limits.HOST_MAXIMA == {
        "cpu_seconds": value("MAX_CPU_TIME_LIMIT"),
        "cpu_extra_seconds": value("MAX_CPU_EXTRA_TIME"),
        "wall_seconds": value("MAX_WALL_TIME_LIMIT"),
        "memory_kb": value("MAX_MEMORY_LIMIT"),
        "stack_kb": value("MAX_STACK_LIMIT"),
        "max_processes": value("MAX_MAX_PROCESSES_AND_OR_THREADS"),
        "max_file_kb": value("MAX_MAX_FILE_SIZE"),
    }


# ── The double ────────────────────────────────────────────────────────────────


async def test_the_fake_answers_by_lookup_and_refuses_an_unscripted_pair(settings: Settings) -> None:
    fake = FakeProvider(pending_polls=1)
    fake.script("print(5)", "2 3", ScriptedRun(outcome=ExecutionOutcome.OK, stdout="5\n"))
    limit = limits.for_language("python")

    ticket = await fake.submit(language="python", source="print(5)", tests=[TestInput("h1", "2 3")], limits=limit)
    assert await fake.collect(ticket) is None
    (result,) = await fake.collect(ticket)
    assert result.outcome is ExecutionOutcome.OK and outputs_match(result.stdout, "5")

    with pytest.raises(LookupError):
        await fake.run(
            language="python", source="print(6)", tests=[TestInput("h1", "2 3")], limits=limit, deadline_seconds=5
        )
    # Discarded on the failing path as well.
    assert len(fake.discarded) == 1

    await fake.discard(ticket)
    with pytest.raises(ExecutionTicketLost):
        await fake.collect(ticket)

    fake.unavailable = "queue_full"
    with pytest.raises(ExecutionUnavailable):
        await fake.health()


def test_the_package_reexports_the_port_and_not_the_adapter() -> None:
    assert "Judge0Provider" not in code_execution.__all__
    assert "get_provider" in code_execution.__all__
