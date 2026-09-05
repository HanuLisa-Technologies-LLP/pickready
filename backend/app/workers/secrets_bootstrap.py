"""Fetch this function's secrets into the environment, once per cold start.

WHY THIS EXISTS
---------------
ECS injects secrets. A task definition names `{ENV_NAME: secret ARN}` and the
ECS agent fetches each one and sets it in the container's environment before the
process starts.

**Lambda has no equivalent.** A function's execution role can be granted
`secretsmanager:GetSecretValue` and nothing will use it: Lambda's own
environment variables are the only injection mechanism it offers, and putting a
credential there is exactly what this platform refuses to do, because those
values are readable in the console, in `GetFunctionConfiguration`, and in the
Terraform state that set them.

So the function fetches them itself, at cold start, using the policy it already
holds. It is the same enumerated per-consumer policy the ECS tasks use, built
once by `infra/modules/secrets`, so there is one answer to "what may this thing
read" and it is not restated here.

The symptom when this is missing is unhelpful, which is why it is worth naming:
`Settings.database_url` falls back to its localhost default and the function
fails with `Connect call failed ('127.0.0.1', 5432)` inside a VPC where nothing
is listening on localhost at all.

WHAT IT REFUSES TO DO
---------------------
It does not guess, it does not skip, and it does not log a value.

  * The list comes from `READYPICK_SECRETS`, a JSON map of `{ENV_NAME: ARN}`
    that Terraform builds from the SAME map the ECS services use. ARNs are not
    secret; the values behind them are, and they never pass through Terraform.
  * A secret it was told to fetch and could not read RAISES. Continuing would
    hand `Settings` a default, and a default database URL or an unset model key
    is the "accepted the work and did not do it" failure this whole
    architecture exists to make impossible.
  * An environment variable that is ALREADY SET WINS. That is what lets a test,
    a local run or a deliberate override work without this module having to
    know about any of them.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

#: `{ENV_NAME: secret ARN}`, as JSON. Mirrors the `secrets` map on an ECS
#: container definition, deliberately: one shape for "what does this thing
#: read", whichever platform runs it.
SECRETS_ENV = "READYPICK_SECRETS"

_loaded = False


class SecretsUnavailable(RuntimeError):
    """A secret this function was told to read could not be read.

    Raised rather than warned. The alternative is a function that starts,
    silently uses a default, and fails somewhere further in with an error about
    localhost or an unset credential.
    """


def load_into_environment() -> int:
    """Populate `os.environ` from Secrets Manager. Returns how many were set.

    Idempotent and cheap on a warm start: the guard below means the fetch
    happens once per execution environment, which is exactly the granularity a
    Lambda cold start gives.
    """
    global _loaded
    if _loaded:
        return 0

    raw = os.environ.get(SECRETS_ENV, "").strip()
    if not raw:
        # A real answer, not a failure: the ECS entry point runs this same code
        # and its secrets are already in the environment, injected by the agent.
        _loaded = True
        return 0

    try:
        wanted = json.loads(raw)
    except ValueError as exc:
        raise SecretsUnavailable(f"{SECRETS_ENV} is not valid JSON") from exc
    if not isinstance(wanted, dict):
        raise SecretsUnavailable(f"{SECRETS_ENV} must be an object of name to ARN")

    # Only the ones not already present. An explicit environment variable is a
    # deliberate override and must win.
    missing = {name: arn for name, arn in wanted.items() if not os.environ.get(name)}
    if not missing:
        _loaded = True
        return 0

    import boto3
    from botocore.config import Config

    client = boto3.client(
        "secretsmanager",
        # Bounded, for the same reason every other client in this package is:
        # an endpoint that accepts and never answers would hang the cold start
        # until the invocation timed out, with nothing saying why.
        config=Config(
            connect_timeout=5,
            read_timeout=10,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )

    failures: list[str] = []
    for name, arn in sorted(missing.items()):
        try:
            value = client.get_secret_value(SecretId=arn)["SecretString"]
        except Exception as exc:  # noqa: BLE001 -- collected and re-raised below
            # The NAME and the exception class. Never the ARN's contents, and
            # never the exception's message, which can quote a request payload.
            failures.append(f"{name}: {type(exc).__name__}")
            continue
        os.environ[name] = value

    if failures:
        raise SecretsUnavailable(
            "could not read " + ", ".join(failures) + ". The execution role's "
            "policy is built from `service_secrets` in infra/modules/secrets; a "
            "secret named here and absent there is the usual cause."
        )

    # `Settings` is `lru_cache`d, so anything that read it before now holds the
    # defaults. Clearing is what makes the order of imports stop mattering.
    from app.core.config import get_settings

    get_settings.cache_clear()

    _loaded = True
    logger.info("secrets.loaded count=%d names=%s", len(missing), sorted(missing))
    return len(missing)


def reset() -> None:
    """Forget that the fetch happened. Tests only."""
    global _loaded
    _loaded = False
