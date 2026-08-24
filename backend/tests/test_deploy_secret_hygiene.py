"""No credential may be shipped as a plain environment variable.

WHAT THIS PINS, and why a comment in the deploy script was not enough.

`DATABASE_URL` was composed at deploy time and passed with `--set-env-vars`, so
the assembled DSN, password included, sat readable on every revision to anyone
holding `run.services.get`. The password itself was always in Secret Manager and
the composed value was never logged, so this was narrower than the audit that
found it claimed, but it was still a credential materialised where it did not
need to be.

It moved to `--set-secrets` on 2026-08-24. The reason it had not been a mount
before is real and still true: a name cannot be both a secret mount and an
environment variable on one Cloud Run revision, and Cloud Run rejects the whole
deploy with a type conflict. So the two halves -- leaving SECRET_EXCLUDE_RE and
leaving build_env -- are ONE change, and doing either alone breaks deploys or
silently re-exposes the DSN.

That coupling is invisible in a diff, which is why it is asserted here rather
than trusted to a comment.
"""
from __future__ import annotations

import pathlib
import re

import pytest

DEPLOY = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "deploy.sh"

#: Names that carry a credential in their VALUE. A mount is the only acceptable
#: delivery for these. `REDIS_URL` is deliberately absent: it is a host and port
#: and carries no secret, so making it a mount would be cargo cult.
CREDENTIAL_NAMES = ("DATABASE_URL", "POSTGRES_PASSWORD", "CLOUDINARY_URL")


@pytest.fixture(scope="module")
def deploy_source() -> str:
    if not DEPLOY.exists():
        pytest.skip("deploy script is not present in this checkout")
    return DEPLOY.read_text(encoding="utf-8")


def _build_env_body(source: str) -> str:
    start = source.index("build_env()")
    end = source.index("\n}", start)
    return source[start:end]


@pytest.mark.parametrize("name", CREDENTIAL_NAMES)
def test_no_credential_is_emitted_as_a_plain_env_var(deploy_source: str, name: str) -> None:
    """`build_env` is what becomes `--set-env-vars`. A credential named here is
    a credential printed onto the revision."""
    body = _build_env_body(deploy_source)
    assert f"{name}=" not in body, (
        f"{name} is emitted by build_env, which puts its value on the revision "
        "in clear. Deliver it through --set-secrets instead."
    )


def test_database_url_is_not_excluded_from_the_secret_mounts(deploy_source: str) -> None:
    """The other half of the same change. Excluded AND absent from build_env
    would mean the app simply never receives it."""
    match = re.search(r"SECRET_EXCLUDE_RE='([^']+)'", deploy_source)
    assert match, "SECRET_EXCLUDE_RE is missing"
    assert "DATABASE_URL" not in match.group(1), (
        "DATABASE_URL is excluded from --set-secrets while build_env no longer "
        "emits it, so no revision would receive a database URL at all."
    )


def test_the_secret_is_kept_in_step_with_the_password(deploy_source: str) -> None:
    """The loop that was open until 2026-08-24.

    Version 1 of the DATABASE_URL secret was a stale DSN that does not
    authenticate. It sat there for a month and nothing noticed, because nothing
    read it. Now that a revision DOES read it, a rotated POSTGRES_PASSWORD would
    leave the mounted DSN stale and the first symptom would be production
    failing to reach its database.
    """
    assert "gcloud secrets versions add DATABASE_URL" in deploy_source, (
        "nothing refreshes the DATABASE_URL secret, so a password rotation "
        "would leave the mounted DSN stale"
    )
    assert "POSTGRES_PASSWORD" in deploy_source


def test_no_credential_value_is_ever_echoed(deploy_source: str) -> None:
    """A value that reaches a log has left Secret Manager's protection."""
    for line in deploy_source.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("echo", "info", "warn", "printf")):
            continue
        # `printf '%s' "$expected" | gcloud ...` is a PIPE into the secret
        # writer, not output. Anything else naming a credential variable is.
        if "| gcloud" in stripped:
            continue
        assert not re.search(r'\$\{?(pw|expected|current|DATABASE_URL)\b', stripped), (
            f"this line can print a credential: {stripped}"
        )
