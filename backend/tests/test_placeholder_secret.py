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


def _generated_secret_names() -> list[str]:
    """The secrets Terraform MINTS rather than waits for a human to supply.

    Read from the Terraform for the same reason `_secret_names` is: a list kept
    by hand here would drift, and the direction it drifts in is a generated
    secret that nothing is checking.
    """
    source = TERRAFORM_VARS.read_text(encoding="utf-8")
    start = source.index('variable "generated_secret_names"')
    block = source[start : source.index("\n}", start)]
    default = block[block.index("default") :]
    return re.findall(r'"([A-Z0-9_]+)"', default)


def _version_block(name: str) -> str:
    """One version resource, WITH ITS COMMENTS REMOVED.

    The same trade `test_deploy_secret_hygiene._code` makes. The generated
    resource's comment NAMES the `ignore_changes` it deliberately does not
    have, and a substring search over the raw text matches the explanation.
    Deleting the explanation to satisfy a test would be the wrong repair: it is
    the most valuable thing in the block.
    """
    source = (TERRAFORM_VARS.parent / "main.tf").read_text(encoding="utf-8")
    start = source.index(f'resource "aws_secretsmanager_secret_version" "{name}"')
    block = source[start : source.index("\n}\n", start)]
    return re.sub(r"^\s*#.*$", "", block, flags=re.MULTILINE)


def test_the_terraform_seeds_a_version_and_never_overwrites_it() -> None:
    """Two properties, and the second is the one that costs money to get wrong.

    A `secret_string` Terraform owned would revert a real value on the next
    apply, silently: the plan reads as a one-line change to a sensitive
    attribute, which shows as `(sensitive value)`.

    AMENDED 2026-09-17. There are TWO version resources now, and the invariant
    this file exists for is unchanged: every secret has a version before any
    task starts against it. What split is OWNERSHIP. A credential a vendor
    issued is a human's to put in and Terraform's to leave alone, which is what
    `ignore_changes` says. `INBOUND_WEBHOOK_SECRET` has no issuer outside this
    platform -- it is a value one half shows to the other -- so Terraform mints
    it, and there `ignore_changes` would be the bug.
    """
    placeholder = _version_block("placeholder")

    assert "for_each = local.supplied" in placeholder, (
        "the placeholder version is not created for every human-supplied "
        "secret; the ones it misses stop their whole task from starting"
    )
    assert "ignore_changes = [secret_string]" in placeholder, (
        "Terraform owns the secret value and will revert whatever a human put "
        "there on the next apply"
    )


def test_the_generated_secrets_carry_a_real_value_and_terraform_keeps_it() -> None:
    """The other half of the same guarantee.

    A generated secret left on the sentinel is worse than an unconfigured
    vendor key. `INBOUND_WEBHOOK_SECRET` empty means
    `POST /verification/inbound-email` stays open to anybody who knows a thread
    token, and thread tokens travel by email.
    """
    generated = _version_block("generated")

    assert "for_each = local.generated" in generated
    assert "random_password.generated[each.key].result" in generated, (
        "the generated version does not carry the minted value, so the secret "
        "would hold nothing"
    )
    assert "ignore_changes" not in generated, (
        "Terraform is the authority for a value it minted. Ignoring changes "
        "means a replaced password never reaches Secrets Manager, so the API "
        "keeps checking the old value while the relay sends the new one."
    )


def test_the_two_halves_partition_the_secret_list() -> None:
    """Neither a secret with two versions racing for AWSCURRENT, nor one with
    none at all."""
    names = set(_secret_names())
    generated = set(_generated_secret_names())

    assert generated, "nothing is generated; the split would be decoration"
    assert generated <= names, (
        f"{sorted(generated - names)} is generated with no container to hold it"
    )

    source = (TERRAFORM_VARS.parent / "main.tf").read_text(encoding="utf-8")
    assert "supplied = toset([" in source, "`local.supplied` is gone"
    assert "if !contains(var.generated_secret_names, name)" in source, (
        "`local.supplied` no longer excludes the generated names, so a "
        "generated secret would get a sentinel version as well as its real one"
    )
