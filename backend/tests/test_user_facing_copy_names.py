"""The words a reader sees name the product's own concepts, and only those.

CONTRACT v4 item 5 (the Vivekium release): recruiter-visible copy uses JD,
SWOT, Skills, AI Match, Tatva Assessment, PRISM Report and Proctoring Report.
It does not say PPI (the retired name of the Tatva Assessment), matrix,
framework or matching categories (the internal shapes behind Skills), the AI
Score (the pre-assessment check is the AI Match), and it no longer explains a
retake, because there is none (`test_retake_removed.py`).

The CODE keeps its names: `ppi`, `job_competencies`, the `/framework` routes,
the `ai_score` payload key. A route is quoted in links already in inboxes and
a key is read from stored immutable reports, so those are identifiers, not
copy, and nothing here reads an identifier. What is swept is PROSE:

* every string literal inside an `HTTPException(detail=...)` under `app/api`,
  which the frontend renders verbatim;
* every non-docstring string literal in the modules that WRITE the copy a
  reader sees: the PRISM Report's serializer and PDF, the gap analysis
  sentences, the candidate Updates catalogue, the empty-state catalogue, the
  AI activity phrasing and the lifecycle email renderers. Log-call arguments
  are skipped (an operator reads them), and so is any string with no space,
  which is an identifier or a key rather than a sentence.

The frontend half, JSX text and user-visible attributes, is
`frontend/lib/user-facing-copy.test.ts`.
"""
from __future__ import annotations

import ast
import pathlib
import re

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

#: The retired or internal names, as whole words. Case-insensitive except PPI,
#: which is an acronym and would otherwise match inside nothing useful anyway.
FORBIDDEN = (
    re.compile(r"\bPPI\b"),
    re.compile(r"\bretak(e|es|en|ing)\b", re.I),
    re.compile(r"\bmatri(x|ces)\b", re.I),
    re.compile(r"\bframeworks?\b", re.I),
    re.compile(r"\bmatching categor(y|ies)\b", re.I),
    re.compile(r"\bAI Scores?\b", re.I),
)

#: Modules whose string literals ARE the copy. Named, not globbed, so the list
#: is reviewed when it changes.
COPY_MODULES = (
    "services/prism_view.py",
    "services/report_pdf.py",
    "services/gap_analysis.py",
    "services/candidate_updates.py",
    "services/generation_sufficiency.py",
    "services/activity/phrasing.py",
    "services/email_render.py",
    "services/email_templates.py",
    "services/lifecycle_email.py",
    "services/siddhi/delivery.py",
)

_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}


def _docstring_ids(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add(id(body[0].value))
    return found


def _log_argument_ids(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _LOG_METHODS
        ):
            for argument in node.args:
                found.update(id(inner) for inner in ast.walk(argument))
    return found


def _hits(text: str) -> list[str]:
    return [match.group(0) for pattern in FORBIDDEN for match in pattern.finditer(text)]


def _prose_constants(node: ast.AST) -> list[ast.Constant]:
    return [
        inner
        for inner in ast.walk(node)
        if isinstance(inner, ast.Constant)
        and isinstance(inner.value, str)
        and " " in inner.value
    ]


def test_no_api_error_detail_uses_a_retired_name() -> None:
    offending: list[str] = []
    swept = 0
    for path in sorted((APP / "api").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != "HTTPException":
                continue
            for keyword in node.keywords:
                if keyword.arg != "detail":
                    continue
                for constant in _prose_constants(keyword.value):
                    swept += 1
                    for hit in _hits(constant.value):
                        offending.append(
                            f"{path.relative_to(APP).as_posix()}:{constant.lineno}: {hit}"
                        )
    # A sweep over nothing passes; that is the failure it exists to catch.
    assert swept > 200, swept
    assert not offending, offending


def test_no_copy_module_writes_a_retired_name() -> None:
    offending: list[str] = []
    for relative in COPY_MODULES:
        path = APP / relative
        assert path.exists(), f"{relative} moved: update COPY_MODULES"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        skipped = _docstring_ids(tree) | _log_argument_ids(tree)
        for constant in _prose_constants(tree):
            if id(constant) in skipped:
                continue
            for hit in _hits(constant.value):
                offending.append(f"{relative}:{constant.lineno}: {hit}")
    assert not offending, offending


def test_the_catalogues_are_clean_as_served() -> None:
    """The same rule read from the VALUES the product serves, so a string
    assembled at import time from pieces is still checked whole."""
    from app.services import generation_sufficiency
    from app.services.siddhi import delivery

    served = list(generation_sufficiency.EMPTY_STATE_COPY.values())
    served.append(delivery.PDF_BLOCKED_REASON)
    offending = [(text, _hits(text)) for text in served if _hits(text)]
    assert not offending, offending


def test_the_sweep_catches_what_it_claims_to() -> None:
    """The patterns, pinned in both directions: the retired names are caught
    as words, and the product's own names are not."""
    for bad in (
        "The PPI Assessment Report is not ready",
        "A retake creates a new report",
        "Save the matrix first",
        "the job's framework",
        "the matching categories",
        "AI Score",
    ):
        assert _hits(bad), bad
    for good in (
        "The PRISM Report is not ready yet.",
        "Save the skills on this job before AI Matching can rank candidates.",
        "Tatva Assessment",
        "AI Match",
        "Proctoring Report",
        "SWOT",
    ):
        assert not _hits(good), good
