"""Every proctoring threshold lives in one place (proctoring spec section 0.6).

    "All thresholds must live in a single configuration module, not scattered
     as magic numbers."

Two halves. The first is that `ProctoringConfig` is built from Settings and
the client projection is a subset of it, so the browser and the server work
from one set of numbers. The second is a sweep: no module under
`services/proctoring/` other than `config.py` compares anything against a
numeric literal, and none assigns a float at module level. A rule written as
`if count >= 2` is a threshold nobody can change without a deploy, and this
package has thirty of them that somebody will want to tune.
"""
from __future__ import annotations

import ast
import pathlib
from dataclasses import fields

from app.core.config import get_settings
from app.services.proctoring import config as proctoring_config

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "proctoring"

#: Literals a comparison may legitimately name: emptiness, a unit, a sign.
_HARMLESS = {0, 1, -1}

#: `phrasing.py` is exempt, and the reason is that its numbers ARE its words.
#: "about half a minute" is forty-five seconds; the boundary and the phrase are
#: one fact, and a deployment that moved the boundary without rewriting the
#: sentence would produce a report that lies about a duration. Nothing in that
#: module decides anything about a candidate: it renders what the rule modules
#: already decided, and those are swept.
_VOCABULARY_ONLY = frozenset({"phrasing.py"})


def test_every_config_field_is_read_from_settings() -> None:
    settings = get_settings()
    config = proctoring_config.get_config()
    for field in fields(proctoring_config.ProctoringConfig):
        assert getattr(config, field.name) == getattr(settings, f"proctoring_{field.name}"), field.name


def test_the_client_projection_is_a_subset_and_agrees_with_the_server() -> None:
    config = proctoring_config.get_config()
    client = proctoring_config.client_config()
    assert set(client) == set(proctoring_config.CLIENT_FIELDS)
    for name, value in client.items():
        assert getattr(config, name) == value, name
    # The counter ceiling is in the projection: the persistent indicator shows
    # "warnings used of N" and must show the same N the server enforces.
    assert "max_warnings" in client


def test_audio_availability_follows_the_service_url() -> None:
    config = proctoring_config.get_config()
    assert config.audio_analysis_available == bool(config.analysis_service_url)


def _numeric(node: ast.AST) -> int | float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _numeric(node.operand)
        return -inner if inner is not None else None
    return None


def test_no_module_but_config_compares_against_a_numeric_literal() -> None:
    offenders: list[str] = []
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name in ("config.py", "__init__.py") or path.name in _VOCABULARY_ONLY:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            for operand in (node.left, *node.comparators):
                value = _numeric(operand)
                if value is not None and value not in _HARMLESS:
                    offenders.append(f"{path.name}:{node.lineno} compares against {value!r}")
    assert not offenders, (
        "A threshold in a comparison is a number nobody can tune. Read it from "
        "`config.get_config()` instead:\n  " + "\n  ".join(offenders)
    )


def test_no_module_but_config_holds_a_float_constant() -> None:
    """A float at module level in this package is a threshold in disguise."""
    offenders: list[str] = []
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name in ("config.py", "__init__.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                value = _numeric(node.value)
                if isinstance(value, float):
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, offenders


def test_the_sweep_would_catch_a_literal_threshold() -> None:
    """A guard on the guard: the detector recognises the shape it exists for."""
    tree = ast.parse("if count >= 2:\n    pass\n")
    found = [
        _numeric(operand)
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        for operand in (node.left, *node.comparators)
    ]
    assert 2 in found
