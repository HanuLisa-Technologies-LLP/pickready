"""The unpopulated-secret sentinel, and the two halves that have to agree.

WHY THERE IS A SENTINEL AT ALL
------------------------------
ECS fetches every secret named in a task definition BEFORE the container
starts. A secret that exists but has no version stops the whole service:

    ResourceNotFoundException: Secrets Manager can't find the specified secret
    value for staging label: AWSCURRENT

and the service reports "unable to place a task", which reads like a network or
IAM problem and is neither. One unpopulated credential takes the API down.

That is the wrong failure for this product. A missing model key is supposed to
cost the generative path and leave the deterministic fallback; a missing Tavily
key is supposed to cost the internet segment of AI Reach; a missing SMTP
password is supposed to be a loud warning at startup. All of those are written
and tested. None of them run if the task cannot start.

Secrets Manager refuses an empty `SecretString`, so `infra/modules/secrets`
seeds every secret with a sentinel instead, and `app.core.config` turns that
sentinel back into "" before anything reads it.

WHAT THIS FILE PROTECTS
-----------------------
Two strings in two languages have to be identical, and the failure if they
drift is invisible in the worst way: the application would receive a NON-EMPTY
value for an unconfigured credential, decide it was configured, and fail later
at a provider with a 401 that reads like a revoked key.
"""
from __future__ import annotations

import pathlib
import re

from app.core.config import PLACEHOLDER_SECRET, Settings, get_settings

TERRAFORM_VARS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "infra"
    / "modules"
    / "secrets"
    / "variables.tf"
)


def _terraform_placeholder() -> str:
    source = TERRAFORM_VARS.read_text(encoding="utf-8")
    start = source.index('variable "placeholder_value"')
    block = source[start : source.index("\n}", start)]
    match = re.search(r'default\s*=\s*"([^"]+)"', block)
    assert match, "placeholder_value has no default in the Terraform"
    return match.group(1)


def test_the_two_definitions_are_the_same_string() -> None:
    """The drift this prevents is silent: a mismatch makes an unconfigured
    credential read as configured."""
    assert PLACEHOLDER_SECRET == _terraform_placeholder()


def test_the_sentinel_cannot_be_mistaken_for_a_credential() -> None:
    """It is read by a human in the console before it is read by anything else.

    No punctuation that would let it parse as a URL, a JSON document or a
    base64 blob, and a name that says what it is.
    """
    assert PLACEHOLDER_SECRET.isupper()
    assert "NOT_CONFIGURED" in PLACEHOLDER_SECRET
    assert not any(character in PLACEHOLDER_SECRET for character in ':/@.{}"=')


def _secret_names() -> list[str]:
    """The secrets this platform actually creates, read from the Terraform.

    Read rather than listed, so a secret added there is covered here without
    anybody remembering to add it. That is the whole failure mode: a new secret
    with no entry in a hand-kept list arrives as a non-empty sentinel and reads
    as configured.
    """
    source = TERRAFORM_VARS.read_text(encoding="utf-8")
    start = source.index('variable "secret_names"')
    block = source[start : source.index("\n}", start)]
    default = block[block.index("default") :]
    return re.findall(r'"([A-Z0-9_]+)"', default)


def test_the_secret_list_is_read_not_guessed() -> None:
    """A guard on the guard: an empty parse would make the test below vacuous."""
    names = _secret_names()
    assert len(names) >= 10, names
    assert "DATABASE_URL" in names and "OPENAI_GPT_TERRA" in names


#: Read by the ANALYSIS SERVICE, not by the backend. It is its own application
#: with its own settings module and its own image, and it holds this one secret
#: and nothing else: all it is handed is fifteen seconds of audio and all it
#: answers is a speaker count. Named here rather than skipped by a pattern, so
#: a second such secret has to be a decision.
READ_BY_THE_ANALYSIS_SERVICE = {"HUGGINGFACE_TOKEN"}

ANALYSIS_CONFIG = (
    pathlib.Path(__file__).resolve().parents[2]
    / "analysis-service"
    / "app"
    / "config.py"
)


def test_every_secret_this_platform_creates_is_read_by_something() -> None:
    """A secret with no reader is a secret nothing uses.

    ECS would still mount it, the task would still carry it, and the value
    would go nowhere. That is not harmful on its own, but it is a grant nobody
    needs, and the enumerated per-service policies exist precisely to keep
    those out.
    """
    orphans = [
        name
        for name in _secret_names()
        if name.lower() not in Settings.model_fields
        and name not in READ_BY_THE_ANALYSIS_SERVICE
    ]
    assert not orphans, (
        f"{orphans} are created and mounted but nothing reads them"
    )


def test_the_analysis_services_secret_really_is_read_over_there() -> None:
    """The exemption above has to be earned, not asserted.

    Without this, `READ_BY_THE_ANALYSIS_SERVICE` becomes a list anybody can add
    a genuinely unused secret to in order to make the test above pass.
    """
    source = ANALYSIS_CONFIG.read_text(encoding="utf-8")
    for name in READ_BY_THE_ANALYSIS_SERVICE:
        assert name in source, (
            f"{name} is exempted as read by the analysis service, and that "
            f"service's config does not mention it"
        )


def test_the_sentinel_never_survives_into_settings(monkeypatch) -> None:
    """The one that matters.

    A surviving sentinel makes an unconfigured credential read as configured,
    and the failure moves from a documented startup warning to a 401 at a
    provider that looks like a revoked key.
    """
    names = _secret_names()
    for name in names:
        monkeypatch.setenv(name, PLACEHOLDER_SECRET)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        leaked = [
            name
            for name in names
            if getattr(settings, name.lower(), None) == PLACEHOLDER_SECRET
        ]
        assert not leaked, (
            f"the sentinel reached {leaked} as a value. Those would read as "
            "configured credentials and fail at a provider instead of taking "
            "the documented unconfigured path."
        )
    finally:
        get_settings.cache_clear()


def test_a_real_value_is_left_alone(monkeypatch) -> None:
    """The guard on the guard: a normaliser that blanked everything would pass
    the test above and break the product."""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-a-real-looking-key")
    get_settings.cache_clear()
    try:
        assert get_settings().tavily_api_key == "tvly-a-real-looking-key"
    finally:
        get_settings.cache_clear()


def test_the_terraform_seeds_a_version_and_never_overwrites_it() -> None:
    """Two properties, and the second is the one that costs money to get wrong.

    A `secret_string` Terraform owned would revert a real value on the next
    apply, silently: the plan reads as a one-line change to a sensitive
    attribute, which shows as `(sensitive value)`.
    """
    source = (TERRAFORM_VARS.parent / "main.tf").read_text(encoding="utf-8")
    start = source.index('resource "aws_secretsmanager_secret_version" "placeholder"')
    block = source[start : source.index("\n}\n", start)]

    assert "for_each = aws_secretsmanager_secret.this" in block, (
        "the placeholder version is not created for every secret; the ones it "
        "misses stop their whole task from starting"
    )
    assert "ignore_changes = [secret_string]" in block, (
        "Terraform owns the secret value and will revert whatever a human put "
        "there on the next apply"
    )
