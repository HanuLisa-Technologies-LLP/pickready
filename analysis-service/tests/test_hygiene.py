"""The repository's anti-slop rules, applied to this tree.

The backend's `test_platform_audit.py` and `test_no_silent_degradation.py`
sweep `backend/app` only, so a new top-level directory would be outside every
existing gate. This file is the same rules for `analysis-service/`: no em dash
anywhere, no placeholder prose, no bare `except`, no `except X: pass`.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SUFFIXES = {".py", ".txt", ".md", ".ini", ".yml", ".yaml"}
EM_DASH = chr(8212)

#: Built from parts so this file does not itself trip the sweep.
PLACEHOLDER_TOKENS = (
    "TO" + "DO",
    "FIX" + "ME",
    "XX" + "X",
    "in a real " + "implementation",
    "for " + "now",
    "this is a " + "simplified",
)

#: Allowed in tests, where a test double may be called one, and nowhere else.
APP_ONLY_TOKENS = ("st" + "ub",)


def _files() -> list[pathlib.Path]:
    found = [ROOT / "Dockerfile"]
    for path in ROOT.rglob("*"):
        if "models" in path.relative_to(ROOT).parts or "__pycache__" in path.parts:
            continue
        if path.is_file() and path.suffix in SUFFIXES:
            found.append(path)
    return sorted(found)


def test_the_tree_is_not_empty() -> None:
    assert len(_files()) > 10


def test_no_em_dash_anywhere() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in _files()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if EM_DASH in line
    ]
    assert not offenders, offenders


def test_no_placeholder_prose() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}:{number} {token!r}"
        for path in _files()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for token in PLACEHOLDER_TOKENS
        if token.lower() in line.lower()
    ]
    assert not offenders, offenders


def test_no_stub_in_the_service_source() -> None:
    offenders = [
        f"{path.name}:{number} {token!r}"
        for path in (ROOT / "app").glob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for token in APP_ONLY_TOKENS
        if token in line.lower()
    ]
    assert not offenders, offenders


def test_no_bare_except_and_no_swallowed_exception() -> None:
    bare = re.compile(r"^\s*except\s*:")
    swallowed = re.compile(r"^\s*except\b[^\n]*:\s*\n\s*pass\b", re.MULTILINE)
    offenders: list[str] = []
    for path in (ROOT / "app").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for number, line in enumerate(source.splitlines(), 1):
            if bare.match(line):
                offenders.append(f"{path.name}:{number} bare except")
        if swallowed.search(source):
            offenders.append(f"{path.name} swallows an exception with pass")
    assert not offenders, offenders


def test_the_ai_text_module_states_the_caveat_in_its_docstring() -> None:
    source = (ROOT / "app" / "ai_text.py").read_text(encoding="utf-8")
    docstring = source.split('"""')[1]
    assert "UNRELIABLE" in docstring
    assert "INFORMATIONAL ONLY" in docstring
