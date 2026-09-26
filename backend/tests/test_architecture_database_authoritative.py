"""AD-1: the database is authoritative; A2A artifacts are provenance and
hand-off records only (docs/spec/ARCHITECTURE.md, CONTRACT v4 item 4).

A decision recorded only in prose is one the next change quietly reverses, so
the two structural halves of it are pinned here:

  * The artifact, provenance and envelope modules cannot TOUCH the database.
    They import nothing from SQLAlchemy, the ORM models or the session factory,
    so none of them can become a second store by accident: an artifact is an
    in-process description of rows that already exist.
  * No table is an artifact store. No ORM table is named for artifacts, and no
    column is shaped to hold an artifact payload. A table like that would be a
    second answer to "what are this job's criteria" beside `job_competencies`,
    outside the erasure cascade, and it would disagree with the rows exactly
    after a human edit.

Offline: this reads source and ORM metadata and opens no connection.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

import app.models  # noqa: F401  -- registers every mapped table on Base.metadata
from app.models.base import Base

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

#: The A2A layer. Each must stay unable to read or write a table.
A2A_MODULES: tuple[str, ...] = (
    "services/agents/artifacts.py",
    "services/agents/provenance.py",
    "services/agents/envelope.py",
)

#: Import roots that would give a module a path to the database.
_DATABASE_ROOTS: tuple[str, ...] = ("sqlalchemy", "asyncpg", "app.models", "app.core.db")

#: Column names that would mean an artifact payload is being kept.
_PAYLOAD_COLUMNS = frozenset({"artifact_payload", "artifact_json", "payload_artifact"})


def _imported_modules(source: str) -> set[str]:
    """Every module a source file imports, at ANY depth in the file, so a lazy
    import inside a function is seen exactly like a module-level one."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _reaches_the_database(modules: set[str]) -> list[str]:
    return sorted(
        name
        for name in modules
        if any(name == root or name.startswith(root + ".") for root in _DATABASE_ROOTS)
    )


@pytest.mark.parametrize("rel", A2A_MODULES)
def test_the_a2a_layer_cannot_touch_the_database(rel: str) -> None:
    source = (APP / rel).read_text(encoding="utf-8")
    offenders = _reaches_the_database(_imported_modules(source))
    assert not offenders, (
        f"{rel} imports {offenders}. The A2A layer describes rows that already "
        "exist; a path to the database is how it becomes a second store "
        "(docs/spec/ARCHITECTURE.md AD-1)."
    )


def test_the_detector_sees_a_lazy_database_import() -> None:
    """The sweep above is only as good as this: a function-level import is the
    way a cycle-avoiding change would add one, and it must still be seen."""
    source = "def f():\n    from app.models.job import Job\n    return Job\n"
    assert _reaches_the_database(_imported_modules(source)) == [
        "app.models.job",
        "app.models.job.Job",
    ]
    assert _reaches_the_database(_imported_modules("import sqlalchemy as sa\n")) == [
        "sqlalchemy"
    ]
    assert _reaches_the_database(_imported_modules("import app.services.rating\n")) == []


def test_no_table_is_an_artifact_store() -> None:
    tables = sorted(Base.metadata.tables)
    assert tables, "no tables registered; the sweep below would pass vacuously"
    named = [name for name in tables if "artifact" in name.lower()]
    assert not named, (
        f"{named} looks like an artifact store. Artifacts are rebuilt from the "
        "tables on demand and cited by id; they are never kept "
        "(docs/spec/ARCHITECTURE.md AD-1)."
    )


def test_no_column_holds_an_artifact_payload() -> None:
    offenders = sorted(
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.name.lower() in _PAYLOAD_COLUMNS
    )
    assert not offenders, (
        f"{offenders} would keep an artifact payload beside the rows it "
        "describes, outside the erasure cascade (docs/spec/ARCHITECTURE.md AD-1)."
    )


def test_the_decision_is_written_where_this_test_says_it_is() -> None:
    doc = APP.parents[1] / "docs" / "spec" / "ARCHITECTURE.md"
    text = doc.read_text(encoding="utf-8")
    assert "AD-1. The database is authoritative." in text
    assert "test_architecture_database_authoritative.py" in text
