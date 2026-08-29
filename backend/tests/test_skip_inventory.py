"""The skip inventory: what is allowed to be skipped, and nothing else.

WHY THIS FILE EXISTS
---------------------
A skipped test is a guarantee that is not being enforced, and a skip reports one
word away from PASSED in a summary line. This project has already paid for that
twice. The previous phase found six secret-hygiene assertions reporting SKIPPED
after the script they read was deleted, so nothing was enforcing secret hygiene
at all. This phase found seventy-nine more: every database integration test in
the suite answered "no database reachable" because a native PostgreSQL service
held the port Docker published on, so the suite was green while
`POST /jobs/{id}/apply` was refused by a CHECK constraint for every candidate on
every tenant.

Neither was a bug in a test. Both were the absence of anybody asking why the
number was what it was. This file asks, on every run.

HOW IT WORKS
-------------
`docs/SKIPS.md` is the DECLARED inventory: one row per skip, with its category
and its reason. This module compares that declaration against what the session
actually skipped and fails the run on any difference, naming the specific test
that appeared or disappeared rather than a count that moved.

It is registered as a plugin from `tests/conftest.py`, because the comparison
can only happen once every test has reported and a test function cannot observe
the session that contains it.

Regenerate the observed set with:

    RPN_SKIP_DUMP=/tmp/skips.md python -m pytest -q

which writes the table body in the exact format `docs/SKIPS.md` expects.
"""
from __future__ import annotations

import os
import pathlib
import re

import pytest

#: The four categories spec-doc6 3.3 allows, and nothing else. `unjustified` is
#: in the list so a triage pass can name one; a row that keeps the label is a
#: build failure, because the instruction is to fix it or delete it.
CATEGORIES = frozenset(
    {
        "live-credential-required",
        "platform-specific",
        "deliberate-xfail-with-issue",
        "unjustified",
    }
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SKIPS_DOC = _REPO_ROOT / "docs" / "SKIPS.md"

#: Only rows under this heading are the inventory. Everything else in the file
#: is prose, baseline tables and the latent-skip register, none of which
#: describes a skip that actually happens.
_TABLE_HEADING = "## Declared skip inventory"

#: The first cell is backtick-delimited and the pattern reads it as one unit,
#: because a parametrised test id contains pipes of its own --
#: `...[hr_manager|assign_roles]` -- and splitting the row on `|` cuts that id
#: in half. A table format that mangles the very ids it exists to track would
#: report drift on every run and teach everybody to ignore it.
_ROW = re.compile(
    r"^\|\s*`(?P<test>[^`]+)`\s*\|(?P<category>[^|]*)\|(?P<reason>.*)\|\s*$"
)

#: A run smaller than this is a subset, and a subset cannot speak for the whole
#: inventory. Set well below the suite's size and well above any one file.
_FULL_RUN_FLOOR = 1500

# Populated by the hooks below over the life of one session.
_observed: dict[str, str] = {}
_collected: set[str] = set()


# -- Parsing the declaration --------------------------------------------------


def _normalise(nodeid: str) -> str:
    """One spelling for a test id.

    pytest reports a backslash separator on Windows and a forward slash
    everywhere else. A separator is not a difference worth failing a build over,
    and a build that fails on one platform only is a build people learn to
    ignore.
    """
    return nodeid.replace("\\", "/").strip().strip("`")


def parse_declared(text: str) -> dict[str, dict[str, str]]:
    """Rows of the declared inventory table, keyed by normalised test id."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == _TABLE_HEADING:
            start = index
            break
    if start is None:
        raise AssertionError(
            f"{SKIPS_DOC} has no {_TABLE_HEADING!r} heading, so there is no "
            "inventory to compare against."
        )

    declared: dict[str, dict[str, str]] = {}
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        match = _ROW.match(stripped)
        if not match:
            # The header row and its underline carry no backticks, so they fall
            # out here rather than needing to be recognised by name.
            continue
        declared[_normalise(match.group("test"))] = {
            "category": match.group("category").strip(),
            "reason": match.group("reason").strip().rstrip("|").strip(),
        }
    return declared


def _render_row(nodeid: str, category: str, reason: str) -> str:
    return f"| `{_normalise(nodeid)}` | {category} | {reason} |"


def _declared() -> dict[str, dict[str, str]]:
    return parse_declared(SKIPS_DOC.read_text(encoding="utf-8"))


# -- The static checks, which need no session ---------------------------------


def test_the_declared_inventory_parses() -> None:
    assert SKIPS_DOC.exists(), f"{SKIPS_DOC} is missing."
    declared = _declared()
    assert declared, (
        f"{SKIPS_DOC} declares no skips at all. An empty inventory and a missing "
        "inventory look identical to this test, so say so explicitly with a row "
        "rather than by leaving the table out."
    )


def test_every_declared_category_is_one_of_the_four() -> None:
    for nodeid, row in sorted(_declared().items()):
        assert row["category"] in CATEGORIES, (
            f"{nodeid} is categorised {row['category']!r}, which is not one of "
            f"{sorted(CATEGORIES)}."
        )


def test_no_skip_is_left_categorised_unjustified() -> None:
    """spec-doc6 3.3: every unjustified skip is fixed or deleted, not filed.

    The category exists so a triage pass can name one. Leaving a row in it is
    the outcome the instruction forbids, so this is where that lands.
    """
    unjustified = sorted(
        nodeid
        for nodeid, row in _declared().items()
        if row["category"] == "unjustified"
    )
    assert not unjustified, (
        "These skips are declared 'unjustified' and must be fixed or deleted "
        "rather than recorded:\n  " + "\n  ".join(unjustified)
    )


def test_every_declared_reason_is_a_reason() -> None:
    """A category is a bucket; the reason is what somebody has to read.

    "environment" and "n/a" are how an inventory stops being an inventory.
    """
    for nodeid, row in sorted(_declared().items()):
        reason = row["reason"]
        assert len(reason) >= 20, (
            f"{nodeid}: the reason {reason!r} is too short to tell the next "
            "reader whether the skip is still warranted."
        )


# -- The session hooks, registered from tests/conftest.py ---------------------


def pytest_collection_modifyitems(items) -> None:
    _collected.update(_normalise(item.nodeid) for item in items)


def pytest_runtest_logreport(report) -> None:
    if not report.skipped:
        return
    longrepr = report.longrepr
    reason = ""
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        reason = longrepr[2]
    elif longrepr is not None:
        reason = str(longrepr)
    reason = reason.replace("Skipped: ", "").replace("\n", " ").strip()
    _observed.setdefault(_normalise(report.nodeid), reason)


def pytest_sessionfinish(session, exitstatus) -> None:
    """Fail the run if the session's skip set differs from the declaration.

    Only on a FULL run. A developer running one file would otherwise be told
    that every skip in the rest of the suite has vanished, which is true and
    useless, and the lesson people take from a check that cries wolf is to stop
    reading it.
    """
    dump_path = os.environ.get("RPN_SKIP_DUMP", "").strip()
    if dump_path:
        rows = "\n".join(
            _render_row(nodeid, "FILL-IN", reason)
            for nodeid, reason in sorted(_observed.items())
        )
        pathlib.Path(dump_path).write_text(rows + "\n", encoding="utf-8")

    if not SKIPS_DOC.exists():
        return
    if len(_collected) < _FULL_RUN_FLOOR:
        return

    declared = _declared()
    appeared = sorted(set(_observed) - set(declared))
    disappeared = sorted(set(declared) - set(_observed))
    if not appeared and not disappeared:
        return

    lines = ["", "SKIP INVENTORY DRIFT (docs/SKIPS.md)", ""]
    for nodeid in appeared:
        lines.append(f"  NEW SKIP, not declared: {nodeid}")
        lines.append(f"      reason given: {_observed[nodeid]}")
        lines.append(
            "      Either make it run, delete it, or add a row to "
            "docs/SKIPS.md with a category and a reason."
        )
    for nodeid in disappeared:
        lines.append(f"  DECLARED SKIP THAT DID NOT HAPPEN: {nodeid}")
        lines.append(
            "      It now runs, or it was deleted or renamed. Remove its row "
            "from docs/SKIPS.md."
        )
    lines.append("")
    lines.append("Rows for the observed set, in the format the table expects:")
    for nodeid, reason in sorted(_observed.items()):
        category = declared.get(nodeid, {}).get("category", "FILL-IN")
        lines.append("  " + _render_row(nodeid, category, reason))
    lines.append("")

    # Reported through the terminal AND through the exit status. Writing to the
    # reporter alone leaves a green exit code, which is the failure mode this
    # whole file exists to prevent.
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line("\n".join(lines), red=True)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
