"""No wording anywhere may imply a live vendor call has succeeded.

spec-doc6 §12.4, executed rather than promised: "Nowhere in code comments,
docs, commit messages, CLAUDE.md or the final report may any wording imply a
live vendor call has succeeded. Add a check for the phrases 'verified against
the API', 'confirmed working', 'tested live' in this phase's documentation."

There is no Anthropic key and no Voyage key in this phase. The honest framing
for everything built against a vendor is: built and tested against recorded
fixtures and a stub provider; not executed against a live provider.

WHY A REPOSITORY-WIDE SWEEP AND NOT A REVIEW HABIT
---------------------------------------------------
`tests/test_legacy_reset.py` already refuses these phrases in three files. That
is the right check in the wrong scope: the sentence that does the damage is the
one in a report, a docstring or a README that nobody thought of as "the vendor
files". The claim is not local to the code that makes the call -- it is the
kind of thing that gets written once, in a summary, at the end of a long day,
and then quoted by everybody who reads it afterwards.

THE ALLOWLIST IS NOT AN ESCAPE HATCH
-------------------------------------
Three files must contain the phrases in order to forbid them: `CLAUDE.md`,
`VERIFICATION_PENDING.md` and this module. Each is checked below to be
FORBIDDING the phrase rather than merely containing it, so the allowlist cannot
be widened into a place to hide a claim.

The sweep is asserted to have something to sweep, and the detector is asserted
to detect, because a grep over an empty file list is a green tick that means
nothing.
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The three phrases spec-doc6 §12.4 names. Matched case-insensitively, with
#: whitespace normalised, so a line break inside one does not smuggle it past.
FORBIDDEN_PHRASES = (
    "verified against the api",
    "confirmed working",
    "tested live",
)

#: Directories with nothing of this phase's authorship in them.
SKIP_DIRS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    ".next",
    "venv",
    ".venv",
    "dist",
    "build",
    ".terraform",
    "output",
    "diagnostics",
    ".codex_tmp",
    ".impeccable",
}

SCANNED_SUFFIXES = {".md", ".py", ".yml", ".yaml", ".ts", ".tsx", ".mjs", ".sh", ".txt"}

#: Files that must contain the phrases because their job is to forbid them.
#: Relative to the repository root, POSIX separators, LOWERCASED.
#:
#: Lowercased because git and this filesystem disagree about one of them: the
#: working tree holds `CLAUDE.md` and the index holds `claude.md`. On a
#: case-insensitive filesystem both open the same bytes, and an allowlist keyed
#: on the exact string would match under `rglob` and miss under `git ls-files`,
#: which is a gate that passes locally and fails in CI for a reason nobody
#: would guess.
ALLOWLIST = {
    "claude.md",
    "verification_pending.md",
    "backend/tests/test_no_live_vendor_claims.py",
    "backend/tests/test_legacy_reset.py",
}

#: A word that must appear in an allowlisted file near its use of a phrase, so
#: the allowlist cannot become a place a claim hides.
FORBIDDING_WORDS = ("forbidden", "banned", "must not", "may any wording", "refuses")


def _authored() -> list[pathlib.Path] | None:
    """Everything this repository is answerable for, when git can be asked.

    `--cached --others --exclude-standard` is tracked files PLUS untracked ones
    that are not gitignored. Both halves are load-bearing:

    The `--others` half, because a file written in the change being reviewed is
    not committed yet, and a sweep that skipped it would miss the one file most
    likely to carry a fresh claim.

    The `--exclude-standard` half, because 302 vendored design-tool files sit
    untracked-and-ignored under `tools/`. A phrase in one of them is a claim
    somebody else made; including them would fail this check on a developer's
    machine and pass in CI's fresh checkout, which is the worst of both --
    it teaches people the gate is unreliable.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return [REPO_ROOT / line for line in result.stdout.splitlines() if line.strip()]


def _files() -> list[pathlib.Path]:
    authored = _authored()
    candidates = authored if authored is not None else list(REPO_ROOT.rglob("*"))
    found: list[pathlib.Path] = []
    for path in candidates:
        if not path.is_file() or path.suffix.lower() not in SCANNED_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        found.append(path)
    return sorted(found)


ALL_FILES = _files()


def _normalised(path: pathlib.Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""
    return " ".join(raw.split()).lower()


def _relative(path: pathlib.Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix().lower()


def test_the_sweep_has_something_to_sweep() -> None:
    """A grep over an empty file list passes for the wrong reason."""
    assert len(ALL_FILES) > 300, len(ALL_FILES)
    names = {_relative(p) for p in ALL_FILES}
    assert "claude.md" in names
    assert "verification_pending.md" in names
    assert "backend/app/services/llm_router.py" in names
    assert "backend/scripts/verify_live.py" in names
    assert any(n.startswith(".github/workflows/") for n in names)


def test_no_file_claims_a_live_vendor_call_has_succeeded() -> None:
    offenders: list[str] = []
    for path in ALL_FILES:
        relative = _relative(path)
        if relative in ALLOWLIST:
            continue
        text = _normalised(path)
        for phrase in FORBIDDEN_PHRASES:
            if phrase in text:
                offenders.append(f"{relative}: {phrase!r}")
    assert not offenders, (
        "These files imply a live vendor call has succeeded. There is no "
        "Anthropic key and no Voyage key in this phase. The honest framing is "
        "'built and tested against recorded fixtures and a stub provider; not "
        "executed against a live provider'.\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("relative", sorted(ALLOWLIST))
def test_every_allowlisted_file_forbids_the_phrase_rather_than_using_it(
    relative: str,
) -> None:
    """The allowlist may only hold files whose job is to refuse the phrases."""
    path = REPO_ROOT / relative
    assert path.exists(), relative
    text = _normalised(path)
    assert any(
        phrase in text for phrase in FORBIDDEN_PHRASES
    ), f"{relative} is allowlisted and does not use any phrase; remove it"
    assert any(word in text for word in FORBIDDING_WORDS), (
        f"{relative} is allowlisted but reads as though it were making the "
        f"claim rather than forbidding it"
    )


def test_the_detector_detects(tmp_path: pathlib.Path) -> None:
    """The negative direction. A sweep nobody has watched fail is a sweep
    nobody knows works."""
    sample = tmp_path / "report.md"
    sample.write_text(
        "The Sonnet path was verified against\nthe API and is confirmed "
        "working.",
        encoding="utf-8",
    )
    text = _normalised(sample)
    # The line break inside the first phrase is the point: the sweep normalises
    # whitespace, so a wrapped claim is caught exactly like a wrapped one is
    # read.
    assert "verified against the api" in text
    assert "confirmed working" in text


def test_the_honest_framing_is_written_down_where_a_reader_will_find_it() -> None:
    """The rule needs a replacement, not only a prohibition.

    A check that says "do not write that" and nowhere says what to write
    instead gets satisfied by a synonym.
    """
    pending = _normalised(REPO_ROOT / "VERIFICATION_PENDING.md")
    assert "not executed against a live provider" in pending
    assert "recorded fixtures and a stub provider" in pending


def test_verification_results_does_not_exist() -> None:
    """Its absence IS the record that nothing has been run.

    `scripts/verify_live.py` writes `VERIFICATION_RESULTS.md` and only a real
    run against real endpoints can produce it. If this test ever fails, either
    the command was run -- in which case this phase's honesty rules and
    `VERIFICATION_PENDING.md` both need updating in the same change -- or
    somebody hand-wrote a results file, which is the precise thing spec-doc6
    D6 exists to prevent.
    """
    assert not (REPO_ROOT / "VERIFICATION_RESULTS.md").exists()
