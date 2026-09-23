"""One whitespace-normalised source sweep, shared by every removal test.

WHY IT IS SHARED
----------------
The 2026-09-09 feature-removal sweep read one LINE at a time until 2026-09-23, so a
mention wrapped across a newline never matched a pattern containing a space,
and one sat in `workers/tasks.py` for two weeks while every run was green. The
fix was to normalise whitespace first and map offsets back to a line. Every
removal sweep written since needs exactly that, and a second copy of the loop
is how the next one would be written with the blind spot back in it. So the
loop lives here once and each sweep supplies only its patterns, its roots and
its declared exemptions.

WHAT A SWEEP COVERS BY DEFAULT
------------------------------
The places live code and configuration live: the backend's `app/`, `tests/`,
`scripts/` and `harness/`, the frontend's `app/`, `components/` and `lib/`,
`infra/`, the repository `scripts/`, `.github/` and `.env.example`. It skips
`alembic/versions/` (a migration is a dated record of what the schema WAS) and
`docs/history/` (dated provenance), and every exemption beyond those is named
by the calling test with its reason, never inferred by a heuristic.
"""
from __future__ import annotations

import functools
import pathlib
import re
import subprocess
from collections.abc import Iterable

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent

#: Text files a sweep reads. Binary artifacts cannot carry a live reference.
SWEPT_SUFFIXES = frozenset(
    {
        ".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".yaml", ".yml",
        ".tf", ".tfvars", ".sh", ".toml", ".cfg", ".ini", ".txt", ".example",
    }
)

DEFAULT_ROOTS: tuple[pathlib.Path, ...] = (
    BACKEND / "app",
    BACKEND / "tests",
    BACKEND / "scripts",
    BACKEND / "harness",
    REPO / "frontend" / "app",
    REPO / "frontend" / "components",
    REPO / "frontend" / "lib",
    REPO / "infra",
    REPO / "scripts",
    REPO / ".github",
    REPO / ".env.example",
)

_NEVER_SWEPT_PARTS = frozenset({"__pycache__", "node_modules", ".terraform", ".next"})


@functools.lru_cache(maxsize=1)
def _repository_files() -> frozenset[pathlib.Path]:
    """Tracked files plus untracked files git does NOT ignore.

    A gitignored file is generated output nobody reviews in a diff (an offline
    `terraform plan` dump, a build tree), so a hit in one is not a live
    reference. Asked of git rather than hardcoded, the same rule the impeccable
    gate follows, so the exclusion cannot drift from `.gitignore`. A new file
    that is not yet committed IS swept: it is exactly what a sweep exists to
    catch. There is no fallback when git is unavailable; a sweep that silently
    widened or narrowed its scope would be a blind spot.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    return frozenset((REPO / name).resolve() for name in listed.split("\0") if name)


def _files(roots: Iterable[pathlib.Path]) -> Iterable[pathlib.Path]:
    for root in roots:
        if root.is_file():
            yield root
            continue
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in SWEPT_SUFFIXES:
                continue
            if path.resolve() not in _repository_files():
                continue
            parts = set(path.parts)
            if _NEVER_SWEPT_PARTS & parts:
                continue
            if {"alembic", "versions"} <= parts or {"docs", "history"} <= parts:
                continue
            yield path


def normalised(text: str) -> tuple[str, list[int]]:
    """Collapse every whitespace run to one space; keep a map back to offsets."""
    flat: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(text):
        if character.isspace():
            if flat and flat[-1] == " ":
                continue
            flat.append(" ")
        else:
            flat.append(character)
        offsets.append(index)
    return "".join(flat), offsets


def sweep(
    pattern: re.Pattern[str],
    *,
    exempt: Iterable[pathlib.Path] = (),
    roots: Iterable[pathlib.Path] = DEFAULT_ROOTS,
) -> list[str]:
    """Every `path:line: match` for `pattern` in the swept tree.

    `exempt` is a set of FILES or DIRECTORIES the calling test names with a
    reason beside each. A directory exempts everything under it.
    """
    exempt_paths = [p.resolve() for p in exempt]
    hits: list[str] = []
    for path in _files(roots):
        resolved = path.resolve()
        if any(resolved == e or e in resolved.parents for e in exempt_paths):
            continue
        # No decode fallback: a swept text file that is not UTF-8 is a file
        # this sweep cannot vouch for, and skipping it would be a blind spot.
        text = path.read_text(encoding="utf-8")
        flat, offsets = normalised(text)
        for match in pattern.finditer(flat):
            line = text.count("\n", 0, offsets[match.start()]) + 1
            hits.append(f"{path.relative_to(REPO)}:{line}: {match.group(0)}")
    return hits
