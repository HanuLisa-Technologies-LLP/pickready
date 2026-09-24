"""The Cloud Run deployment and the Celery topology are gone from live config.

WHAT WENT, AND WHEN
-------------------
Celery was removed on 2026-09-05 (`docs/spec/BACKGROUND_WORK.md`) and the
Google Cloud Run deployment before it. Both outlived their removal in the
places an operator actually reads: `.env.example` still carried a Cloud Run
block naming `infra/gcp/deploy.sh` and a project id, the staging and
production roots still introduced the agent as "replacing the Celery worker
and beat services", `infra/railway.json` and `infra/render.yaml` still
described a `celery -A ... worker` and `beat` topology the image cannot run,
and the production README called Redis "the Celery broker". Each was deleted
or rewritten on 2026-09-24 to describe the topology that runs.

WHAT MAY STILL SAY IT
---------------------
A sentence that records the removal is provenance, not configuration. Those
are listed in `HISTORY` as (file, the exact phrase matched) with the reason,
never admitted by a heuristic such as "the line contains was removed", which
is the shape of rule that quietly admits the next live mention. The list is a
ratchet: an entry whose phrase is no longer in its file fails, so it can only
shrink.
"""
from __future__ import annotations

import re

from tests.removal_sweep import BACKEND, REPO, sweep

#: Live-config names of the two retired platforms. Built as one alternation so
#: a hit reports which phrase matched.
RETIRED = re.compile(
    r"GCP_PROJECT_ID|GCP_KEY_FILE|GCP_REGION|DEPLOY_GCP|migrate_resumes_to_gcs"
    r"|Celery broker|Celery worker|celery_app|celery -A"
)

THIS_FILE = BACKEND / "tests" / "test_gcp_and_celery_wording_removed.py"

#: (repository path, matched phrase) -> why the mention is provenance.
HISTORY: dict[tuple[str, str], str] = {
    ("backend/app/core/config.py", "celery_app"):
        "PLATFORM_TIMEZONE records that it used to live on the Celery app object",
    ("backend/app/workers/dispatch.py", "celery_app"):
        "dispatch documents the call it replaced and the signature it mirrors",
    ("backend/app/workers/runtime.py", "Celery worker"):
        "an ASSUMPTION carried over unchanged from the worker this replaced",
    ("backend/tests/test_deploy_secret_hygiene.py", "Celery worker"):
        "the docstring names what the on-demand agent replaced",
    ("backend/tests/test_task_registry.py", "celery_app"):
        "the test asserts the module is ABSENT and must name it to do so",
    ("backend/tests/test_repo_hygiene.py", "migrate_resumes_to_gcs"):
        "the test asserts `.coveragerc` does not name the deleted script",
}


def _hits() -> list[tuple[str, str, str]]:
    found = []
    for hit in sweep(RETIRED, exempt=(THIS_FILE,)):
        path, line, phrase = hit.split(":", 2)
        found.append((path.replace("\\", "/"), line, phrase.strip()))
    return found


def test_no_live_config_names_a_retired_platform() -> None:
    live = sorted(
        f"{path}:{line}: {phrase}"
        for path, line, phrase in _hits()
        if (path, phrase) not in HISTORY
    )
    assert not live, (
        "A retired platform is named outside the recorded history. Rewrite it to "
        f"describe what runs, or delete the file it configures: {live}"
    )


def test_the_history_list_only_shrinks() -> None:
    present = {(path, phrase) for path, _line, phrase in _hits()}
    stale = sorted(f"{path}: {phrase}" for path, phrase in HISTORY if (path, phrase) not in present)
    assert not stale, f"these HISTORY entries no longer occur; delete them: {stale}"


def test_the_dead_platform_configs_are_gone() -> None:
    for name in ("railway.json", "render.yaml"):
        assert not (REPO / "infra" / name).exists(), f"infra/{name} configures a topology the image cannot run"
    assert not (REPO / "infra" / "gcp").exists()
    assert not (BACKEND / "app" / "scripts" / "migrate_resumes_to_gcs.py").exists()
