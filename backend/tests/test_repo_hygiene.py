"""Local tooling stays out of the tree, and a removed feature stays out of config.

WHY A TEST FOR .gitignore
-------------------------
Phase 0 (2026-09-23) found `.codex/` and thirteen third-party `.claude/skills`
directories untracked AND unignored on the release machine: one `git add -A`
would have committed about 4MB of other people's code with its own licences.
The ignore rules were added then. A rule nobody asserts is a rule the next
edit to `.gitignore` removes without noticing, so this asks git itself, with
`git check-ignore --no-index`, which answers from the patterns whether or not
the directory exists on this machine (it does not in CI).

The paths are read from `tools/design-tools.manifest.json`, the pinned record
of the vendored tools, rather than restated here: a skill added to the
manifest is covered the day it is added.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
MANIFEST = REPO / "tools" / "design-tools.manifest.json"

#: Local state of other agents and of this release's planning, never product.
LOCAL_ONLY = (".codex/", ".claude/vivekium-release/")


def _git_or_skip() -> str:
    git = shutil.which("git")
    if git is None or not (REPO / ".git").exists():
        pytest.skip("not a git checkout (an image build has no .git); nothing to ask")
    return git


def _ignored(git: str, path: str) -> bool:
    # A probe FILE inside the directory: a directory pattern matches its
    # contents, and asking about a file is what `git add -A` would ask.
    probe = path.rstrip("/") + "/probe.txt"
    result = subprocess.run(
        [git, "check-ignore", "--no-index", "-q", probe],
        cwd=REPO, capture_output=True, timeout=30, check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"git check-ignore failed: {result.stderr!r}")
    return result.returncode == 0


def _manifest_paths() -> list[str]:
    tools = json.loads(MANIFEST.read_text(encoding="utf-8"))["tools"]
    paths = sorted({p for tool in tools for p in tool.get("installed_paths", [])})
    assert paths, "the manifest lists no installed paths; this test would pass vacuously"
    return paths


def test_every_vendored_skill_path_is_ignored() -> None:
    git = _git_or_skip()
    unignored = [p for p in _manifest_paths() if not _ignored(git, p)]
    assert not unignored, f"vendored third-party paths not gitignored: {unignored}"


@pytest.mark.parametrize("path", LOCAL_ONLY)
def test_local_agent_state_is_ignored(path: str) -> None:
    assert _ignored(_git_or_skip(), path), path


def test_nothing_under_an_ignored_tool_path_is_tracked() -> None:
    """Ignoring a path does not untrack what was committed before the rule."""
    git = _git_or_skip()
    tracked = subprocess.run(
        [git, "ls-files", "--", *_manifest_paths(), *LOCAL_ONLY],
        cwd=REPO, capture_output=True, text=True, timeout=30, check=True,
    ).stdout.split()
    assert not tracked, tracked


def test_coverage_config_does_not_name_a_removed_feature() -> None:
    """`.coveragerc` still named the Company DNA package as a coverage target
    two weeks after the package was deleted (2026-09-09). The removal sweep in
    `test_company_dna_removed.py` reads Python only, so config was its blind
    spot. It also listed a migration script deleted on 2026-09-24."""
    text = (REPO / "backend" / ".coveragerc").read_text(encoding="utf-8")
    assert not re.search(r"company[ _-]?dna", text, re.I)
    assert "migrate_resumes_to_gcs" not in text
