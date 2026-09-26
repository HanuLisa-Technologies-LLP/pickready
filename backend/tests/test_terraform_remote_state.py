"""Every Terraform environment has remote state, and no two share a key.

THE DEFECT THIS FILE REPLACES
------------------------------
`infra/environments/staging` and `infra/environments/production` had no
`backend.tf`. Both carried the block COMMENTED OUT inside `main.tf` with a note
saying the state bucket had to be bootstrapped by hand first. That reasoning was
correct on the day it was written. The bucket was bootstrapped on 2026-09-04,
pilot has been applying against it ever since, and the comment stayed.

A missing backend block does not fail. `terraform init` with no backend
initialises the LOCAL one, and `.github/workflows/deploy.yml` runs a bare
`terraform init -input=false` on an ephemeral runner. So the first
`AWS_DEPLOY_ENABLED` apply would have planned the entire environment from EMPTY
state: create everything, collide on the globally unique S3 and ECR names, and
discard the state file when the job ended. Nothing recovers from that except
importing every resource by hand.

THE SECOND PROPERTY IS THE ONE THAT IS EASY TO BREAK LATER
-----------------------------------------------------------
`backend.tf` is going to be created by copying a sibling. The value that must
NOT come across is the `key`: two environments sharing one state key means the
second apply reads the first's state, concludes every resource has moved, and
proposes destroying a whole environment. The key is asserted distinct here
rather than trusted to review.

This file is the fix. The two `backend.tf` files are only today's instance of
it, which is why the assertions are written over whatever environments exist
rather than over a list of names.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
ENVIRONMENTS_DIR = REPO / "infra" / "environments"

# A directory is an environment root when it has a `main.tf`. Not when it is
# named in a list here: a list is what goes stale the first time somebody adds a
# fourth environment, and a fourth environment with no remote state is exactly
# the failure above.
# DOT-PREFIXED DIRECTORIES ARE SCRATCH, NOT ENVIRONMENTS, and skipping them is
# load bearing rather than tidy. `infra/plan-offline.sh` copies each
# environment into `environments/.<env>-offline-plan` to plan it against a dummy
# account, and removes them afterwards. Those copies carry a `main.tf` and no
# `backend.tf` by design, because the offline plan deliberately uses a LOCAL
# backend. `pathlib.glob` matches leading dots (shell globbing does not), so
# without this filter the whole module passed or failed depending on whether
# anybody had run a plan recently, and it would have reported the offline
# scratch copy as a fourth environment shipping with no remote state.
ENVIRONMENT_ROOTS = sorted(
    path.parent
    for path in ENVIRONMENTS_DIR.glob("*/main.tf")
    if not path.parent.name.startswith(".")
)

# `backend "s3" { ... }`, non-greedy to the first closing brace at the start of
# a line, which is how every file in this tree is formatted (`terraform fmt` is
# a CI gate, so the shape is enforced elsewhere).
BACKEND_BLOCK = re.compile(r'backend\s+"s3"\s*\{(.*?)\n\s*\}', re.DOTALL)
KEY_ARGUMENT = re.compile(r'^\s*key\s*=\s*"([^"]+)"', re.MULTILINE)


def _backend_body(root: pathlib.Path) -> str:
    source = (root / "backend.tf").read_text(encoding="utf-8")
    match = BACKEND_BLOCK.search(source)
    assert match, f'{root.name}/backend.tf declares no `backend "s3"` block.'
    return match.group(1)


def test_there_are_environment_roots_to_check() -> None:
    """A glob that matches nothing passes every parametrised test below."""
    assert len(ENVIRONMENT_ROOTS) >= 2, (
        f"Found {len(ENVIRONMENT_ROOTS)} environment roots under "
        f"{ENVIRONMENTS_DIR}. Either the path moved or the glob is wrong, and "
        "an empty collection makes every assertion in this file vacuous."
    )


@pytest.mark.parametrize("root", ENVIRONMENT_ROOTS, ids=lambda p: p.name)
def test_every_environment_root_declares_remote_state(root: pathlib.Path) -> None:
    """No `backend.tf` means `terraform init` silently uses local state."""
    backend = root / "backend.tf"
    assert backend.is_file(), (
        f"infra/environments/{root.name}/ has a main.tf and no backend.tf.\n\n"
        "Terraform does not warn about this. `terraform init` initialises the "
        "LOCAL backend, and the deploy workflow runs on an ephemeral runner, so "
        "an apply starts from empty state, tries to create the whole "
        "environment, collides on globally unique bucket and repository names, "
        "and then throws the state away. Copy a sibling's backend.tf and give "
        "it a key of its own."
    )


@pytest.mark.parametrize("root", ENVIRONMENT_ROOTS, ids=lambda p: p.name)
def test_the_backend_is_in_its_own_file(root: pathlib.Path) -> None:
    """`main.tf` must not carry a backend block, live or commented out.

    Two reasons, and the second is the one that bit this repository.

    `infra/plan-offline.sh` plans a COPY of the directory with `backend.tf`
    omitted, which is what lets a plan run with no credentials and no network. A
    backend block in `main.tf` cannot be omitted that way.

    And a COMMENTED-OUT backend block is worse than none: it reads to a reviewer
    as configuration that exists, so the absence of real remote state looks
    reviewed rather than missed.
    """
    source = (root / "main.tf").read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in source.splitlines()
        if re.search(r'backend\s+"s3"', line)
    ]
    assert not offenders, (
        f'infra/environments/{root.name}/main.tf mentions a `backend "s3"` '
        f"block: {offenders}. It belongs in backend.tf, uncommented. A "
        "commented-out backend block reads as configured and is not."
    )


@pytest.mark.parametrize("root", ENVIRONMENT_ROOTS, ids=lambda p: p.name)
def test_remote_state_is_encrypted_and_locked(root: pathlib.Path) -> None:
    """Encryption at rest, and a lock so two applies cannot run at once."""
    body = _backend_body(root)

    assert re.search(r"^\s*encrypt\s*=\s*true", body, re.MULTILINE), (
        f"{root.name}/backend.tf does not set `encrypt = true`. The state file "
        "holds every resource identifier in the environment and, for some "
        "resources, generated secrets."
    )

    locked = re.search(r"^\s*dynamodb_table\s*=", body, re.MULTILINE) or re.search(
        r"^\s*use_lockfile\s*=\s*true", body, re.MULTILINE
    )
    assert locked, (
        f"{root.name}/backend.tf configures no state lock (neither "
        "`dynamodb_table` nor `use_lockfile = true`). Without one, two applies "
        "can run against one state file and the loser's writes are lost."
    )


def test_no_two_environments_share_a_state_key() -> None:
    """The one value that must never be copied between these files.

    Sharing a key means the second environment's apply reads the first's state,
    finds every resource pointing somewhere else, and proposes replacing an
    entire environment. Terraform reports that as an ordinary plan.
    """
    keys: dict[str, str] = {}
    for root in ENVIRONMENT_ROOTS:
        body = _backend_body(root)
        match = KEY_ARGUMENT.search(body)
        assert match, f"{root.name}/backend.tf sets no `key`."
        key = match.group(1)
        assert key not in keys, (
            f"infra/environments/{root.name} and infra/environments/{keys[key]} "
            f"both store state at key '{key}'.\n\n"
            "One of them was copied from the other and the key came with it. "
            "The second apply would read the first environment's state and "
            "propose destroying it."
        )
        keys[key] = root.name

    # And the key names the environment it belongs to. Not a correctness
    # requirement on its own, but a key of `staging/terraform.tfstate` under
    # `production/` is distinct AND wrong, which the check above cannot see.
    for key, name in keys.items():
        assert key.startswith(f"{name}/"), (
            f"infra/environments/{name} stores state at '{key}', which does not "
            f"begin with '{name}/'. Distinct but mislabelled state is harder to "
            "spot than shared state, because nothing ever disagrees."
        )
