"""A percent-encoded password must survive `alembic/env.py`.

WHAT BROKE
----------
`env.py` hands the DSN to `config.set_main_option("sqlalchemy.url", ...)`, which
writes into a ConfigParser. ConfigParser's default interpolation reads `%` as
the start of a `%(name)s` reference, and a DSN is a URL, so a password
containing any character that needs percent-encoding arrives as `%2A` or `%7C`
and ConfigParser raises

    ValueError: invalid interpolation syntax in '...' at position 51

before a single migration runs.

WHY IT IS THE NORMAL CASE, NOT AN UNLUCKY ONE
---------------------------------------------
RDS generates master passwords from a character set that includes `*`, `|`, `~`
and others that all percent-encode. The first migration of the pilot
environment failed on exactly this, on a password nobody chose. Any deployment
that lets AWS manage the master password will hit it.

The fix is ConfigParser's own escape: double the `%`. SQLAlchemy receives the
single `%` back, so the DSN it connects with is unchanged, which is the half
this file has to prove as well.
"""
from __future__ import annotations

import ast
import configparser
import pathlib
import urllib.parse

ENV_PY = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "env.py"

#: The shape RDS actually produced, reduced to the characters that matter.
AWKWARD_PASSWORD = "53~fwbaTqgFL8t*gN.W|LgCOLLgA"


def _dsn(password: str) -> str:
    quoted = urllib.parse.quote(password, safe="")
    return f"postgresql+asyncpg://readypick_admin:{quoted}@db.internal:5432/readypick"


def test_the_generated_password_really_does_percent_encode() -> None:
    """A guard on the guard. If this stopped being true the tests below would
    pass while proving nothing."""
    assert "%" in _dsn(AWKWARD_PASSWORD)


def test_configparser_refuses_the_raw_dsn() -> None:
    """The failure being prevented, demonstrated rather than described."""
    parser = configparser.ConfigParser()
    parser.add_section("alembic")
    try:
        parser.set("alembic", "sqlalchemy.url", _dsn(AWKWARD_PASSWORD))
    except ValueError:
        return
    raise AssertionError(
        "ConfigParser accepted a percent-encoded DSN. If interpolation is off "
        "by default now, the escaping in env.py is no longer load bearing and "
        "this file should say so rather than asserting it."
    )


def test_the_escaped_dsn_round_trips_to_the_original() -> None:
    """The other half: escaping must not change what SQLAlchemy connects with.

    A fix that made the migration run against a different DSN would be worse
    than the crash, because it would run.
    """
    original = _dsn(AWKWARD_PASSWORD)
    parser = configparser.ConfigParser()
    parser.add_section("alembic")
    parser.set("alembic", "sqlalchemy.url", original.replace("%", "%%"))
    assert parser.get("alembic", "sqlalchemy.url") == original


def test_env_py_escapes_before_setting_the_option() -> None:
    """Read out of the source, because the module cannot be imported here: it
    runs `context.config` at import and needs an Alembic run context."""
    tree = ast.parse(ENV_PY.read_text(encoding="utf-8"))

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "set_main_option"
    ]
    assert len(calls) == 1, f"expected one set_main_option call, found {len(calls)}"

    value = calls[0].args[1]
    assert (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr == "replace"
    ), "the DSN is passed to set_main_option without escaping its percent signs"

    literals = [
        node.value for node in ast.walk(value) if isinstance(node, ast.Constant)
    ]
    assert "%" in literals and "%%" in literals, (
        f"the replace call does not double a percent sign: {literals}"
    )
