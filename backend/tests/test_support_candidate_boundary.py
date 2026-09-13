"""A support thread is about the CUSTOMER. No candidate data reaches one.

WHAT THIS INHERITED
---------------------
The deleted third-party sync (claude.md, 2026-09-10) spent its whole design on
a closed allowlist stopping candidate material being projected to a vendor. The
vendor is gone; the rule is not. Moving support inside the product removed the
outbound projection entirely, which is strictly stronger, and this file keeps
the remaining half honest: nothing AUTOMATED may compose a support message out
of assessment, report or candidate material.

WHAT IS AND IS NOT ENFORCEABLE, SAID PLAINLY
----------------------------------------------
`support_messages.body` is free text a human types. Nothing can validate it, and
a filter that caught the word "score" and missed "they got a 4" would be worse
than no filter, because it would read as protection. So the boundary asserted
here is STRUCTURAL rather than lexical:

  * the product's own catalogue of support copy carries no candidate
    substitution and no grade word;
  * the notification templates carry no message body at all, so the one copy
    that leaves the product by email cannot carry pasted material either;
  * no module on the support write path imports the candidate, assessment or
    report models;
  * neither table has a candidate-shaped column to join on.

The third is the one that would actually break. Somebody adding a "attach the
candidate's report to the ticket" convenience is not being careless; they are
being helpful, and the import is where it becomes visible.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
APP = REPO / "app"

#: The delivered grades. None may appear in product-authored support copy: a
#: support surface is client-facing, the no-numbers rule applies in full, and a
#: grade written in prose is still a grade.
GRADE_WORDS = (
    "highly matching",
    "moderately matching",
    "not matching",
)

#: Model and service families that describe a CANDIDATE rather than a customer.
#: A module on the support write path importing one of these is the signal.
CANDIDATE_MODULES = (
    "app.models.candidate",
    "app.models.assessment",
    "app.models.project",
    "app.models.evidence",
    "app.models.proctoring",
    "app.services.functional_assessment",
    "app.services.miti",
    "app.services.siddhi",
    "app.services.ppi",
)

#: Every module that can write a `support_messages` row. ENUMERATED rather than
#: discovered, so a new writer has to be added here deliberately and whoever
#: adds it reads this file.
SUPPORT_WRITE_PATH = (
    APP / "api" / "support.py",
    APP / "services" / "support.py",
)


def _imported_modules(path: pathlib.Path) -> set[str]:
    """Every module this file imports, at module level OR inside a function.

    Function-level imports count. This codebase uses them deliberately to break
    cycles (`from app.services.miti import live` sits inside `synthesis_node`
    for exactly that reason), so a check that only read the top of a file would
    miss precisely the ones written that way.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


@pytest.mark.parametrize("path", SUPPORT_WRITE_PATH, ids=lambda p: p.name)
def test_the_support_write_path_imports_nothing_candidate_shaped(
    path: pathlib.Path,
) -> None:
    """Asserted by the import graph, the way the proctoring isolation test and
    the judge isolation test both do it, rather than by reading strings.

    An import is the cheapest honest signal here: a module cannot put a
    candidate's grade into a support message without first being able to reach
    one, and reaching one means importing something on this list.
    """
    imported = _imported_modules(path)
    offending = sorted(
        name
        for name in imported
        for banned in CANDIDATE_MODULES
        if name == banned or name.startswith(banned + ".")
    )
    assert not offending, (
        f"{path.name} reaches candidate material: {offending}. A support thread "
        "is about the CUSTOMER. If something genuinely needs to travel, it is a "
        "link the reader follows while signed in, never a projection into a "
        "ticket."
    )


def test_no_product_authored_support_copy_names_a_grade() -> None:
    """`SYSTEM_COPY` is the only catalogue of message text this product
    authors. Swept over the whole catalogue rather than checked where it is
    used, because a rule enforced at a call site is a rule the next entry
    breaks."""
    from app.services.support import SYSTEM_COPY

    for key, text in SYSTEM_COPY.items():
        lowered = text.lower()
        for grade in GRADE_WORDS:
            assert grade not in lowered, f"{key} names the grade {grade!r}"
        assert not any(char.isdigit() for char in text), key


def test_the_notification_templates_carry_no_message_body() -> None:
    """The email says a message arrived and where to read it, never what it
    said.

    This is what makes the boundary hold even for text a human pasted: an email
    is the one copy of a message this product cannot recall, so the body stays
    behind a sign-in. Asserted over the template SOURCE, so adding
    `{{message_body}}` to be helpful fails here rather than in somebody's inbox.
    """
    from app.services.email_render import DEFAULT_TEMPLATES

    for key in ("support_reply_to_customer", "support_message_for_staff"):
        subject, body = DEFAULT_TEMPLATES[key]
        placeholders = set(
            re.findall(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", subject + body)
        )
        assert placeholders <= {"company_name", "subject_line", "support_url"}, (
            f"{key} substitutes {sorted(placeholders)}; the message body and "
            "anything candidate-shaped must stay behind a sign-in"
        )


def test_the_support_tables_have_no_candidate_column() -> None:
    """A foreign key to a candidate would make the boundary a convention.

    There is no `candidate_id`, no `job_candidate_link_id` and no report
    reference on either table, and there must not be: the moment one exists,
    joining it into a rendered thread is a one-line change nobody reviews as a
    boundary change.
    """
    from app.models.support import SupportMessage, SupportThread

    for model in (SupportThread, SupportMessage):
        names = {column.name for column in model.__table__.columns}
        offending = sorted(
            name
            for name in names
            if re.search(r"candidate|assessment|report|profile|score|grade", name)
        )
        assert not offending, f"{model.__tablename__}: {offending}"
