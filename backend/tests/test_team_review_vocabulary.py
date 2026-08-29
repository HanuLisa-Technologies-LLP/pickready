"""The Team Review verdict vocabulary is defined once and agreed everywhere.

WHAT THIS EXISTS TO CATCH
--------------------------
`ck_candidate_team_reviews_rating` spent from 2026-07-30 to 2026-08-29
enforcing the retired five-label assessment scale. It was not caught by any
test, because there was no test that compared the database's accepted set to the
code's, in either direction.

The interesting part of that history is that nothing had drifted: the CHECK, the
Pydantic literal, the API's ordering constant and the frontend all agreed with
each other on a vocabulary the rest of the product had retired. A test asserting
"the code and the database agree" would have passed happily throughout. So these
tests assert something stronger: that every one of those four places matches ONE
named source, `services/team_review.VERDICTS`, and that the vocabulary is not
`rating.GRADES`.

The last assertion is the one that would have caught the original problem from
the other side, and it is deliberately a NEGATIVE test. A Team Review verdict
and a machine grade must never become the same words, because a colleague's note
rendered in the machine's vocabulary reads as a machine grade, and the panel
exists precisely to keep a human judgment distinguishable from one.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

from app.schemas.candidates import TeamReviewIn
from app.services import rating, team_review

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = (
    _REPO_ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "0063_team_review_verdict_vocabulary.py"
)
_FRONTEND = _REPO_ROOT / "frontend"

#: The scale retired on 2026-07-30. Named here so the assertions below can say
#: what must NOT come back, rather than only what must be present.
_RETIRED = frozenset({"very_high", "high", "medium", "low", "developing"})


def _is_docstring(node: ast.Constant, tree: ast.Module) -> bool:
    """True when this string constant is the docstring of the module or of any
    function or class in it. Docstrings are prose, not renderable output."""
    for parent in ast.walk(tree):
        if isinstance(
            parent, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = getattr(parent, "body", [])
            if body and isinstance(body[0], ast.Expr) and body[0].value is node:
                return True
    return False


def _literal_members(source: str, name: str) -> frozenset[str]:
    """Pull the members of a `Name = Literal["a", "b"]` assignment out of source."""
    match = re.search(rf"^{name}\s*=\s*Literal\[(.*?)\]", source, re.M | re.S)
    assert match, f"no `{name} = Literal[...]` assignment found"
    return frozenset(re.findall(r'"([^"]+)"', match.group(1)))


# ── the code side ────────────────────────────────────────────────────────────


def test_the_pydantic_literal_is_the_named_vocabulary() -> None:
    """`TeamRating` is spelled out for mypy and OpenAPI, so it needs pinning."""
    source = (
        _REPO_ROOT / "backend" / "app" / "schemas" / "candidates.py"
    ).read_text(encoding="utf-8")
    assert _literal_members(source, "TeamRating") == frozenset(team_review.VERDICTS)


def test_the_api_ordering_constant_is_the_named_vocabulary() -> None:
    from app.api import candidates as candidates_api

    assert set(candidates_api._TEAM_RATING_ORDER) == set(team_review.VERDICTS)
    assert len(candidates_api._TEAM_RATING_ORDER) == len(team_review.VERDICTS), (
        "the ordering constant has a duplicate, which would make the panel's "
        "sort order depend on which copy is found first"
    )


@pytest.mark.parametrize("verdict", team_review.VERDICTS)
def test_every_verdict_is_accepted_by_the_request_schema(verdict: str) -> None:
    assert TeamReviewIn(rating=verdict, remarks="Spoke to them.").rating == verdict


@pytest.mark.parametrize("retired", sorted(_RETIRED))
def test_a_retired_grade_is_refused_by_the_request_schema(retired: str) -> None:
    """Refused at the edge, so a stale client cannot reach the database."""
    with pytest.raises(ValueError):
        TeamReviewIn(rating=retired, remarks="Spoke to them.")


# ── the database side ────────────────────────────────────────────────────────


def test_the_migration_declares_exactly_the_named_vocabulary() -> None:
    """Reads the migration's own literal, not the live constant it deliberately
    does not import, so this is a real comparison rather than a tautology."""
    source = _MIGRATION.read_text(encoding="utf-8")
    match = re.search(r"_NEW_VERDICTS\s*=\s*\((.*?)\)", source, re.S)
    assert match, "0063 has no _NEW_VERDICTS tuple"
    assert frozenset(re.findall(r'"([^"]+)"', match.group(1))) == frozenset(
        team_review.VERDICTS
    )


def test_the_migration_maps_every_retired_grade() -> None:
    """A retired value with no mapping would raise mid-migration on a live table."""
    source = _MIGRATION.read_text(encoding="utf-8")
    forward = re.search(r"_FORWARD\s*=\s*\{(.*?)\}", source, re.S)
    assert forward, "0063 has no _FORWARD mapping"
    pairs = dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', forward.group(1)))
    assert set(pairs) == _RETIRED, "every retired grade needs an explicit mapping"
    assert set(pairs.values()) <= set(team_review.VERDICTS)


@pytest.mark.asyncio
async def test_the_check_constraint_accepts_exactly_the_named_vocabulary() -> None:
    """The assertion the original defect needed, run against a real database.

    Reads `pg_get_constraintdef` and compares its accepted set to
    `team_review.VERDICTS`, in BOTH directions: a value the code allows and the
    constraint refuses breaks a legitimate submission, and a value the
    constraint allows and the code does not is a way for a retired vocabulary to
    sit unnoticed for a month.

    Deliberately NOT skipped when the database is unreachable, matching
    `test_db_enum_parity`'s discipline: a constraint check that quietly skips is
    the exact shape of the gap that let the retired scale survive. It fails with
    the sentence that fixes it instead.
    """
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.begin() as conn:
            definition = (
                await conn.execute(
                    text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conname = 'ck_candidate_team_reviews_rating'"
                    )
                )
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 - re-raised as an actionable failure
        raise AssertionError(
            "The test database is not reachable at "
            f"{get_settings().database_url.split('@')[-1]}. Nothing was "
            "checked. Start the stack with: docker compose -f "
            "docker-compose.test.yml up -d && (cd backend && alembic upgrade "
            f"head). Underlying error: {type(exc).__name__}: {exc}"
        ) from exc
    finally:
        await engine.dispose()
    assert definition, (
        "ck_candidate_team_reviews_rating is missing; the column would accept "
        "any string, which is how a typo becomes a stored verdict"
    )
    accepted = frozenset(re.findall(r"'([a-z_]+)'::", definition))
    assert accepted == frozenset(team_review.VERDICTS), (
        f"the CHECK accepts {sorted(accepted)} but the code produces "
        f"{sorted(team_review.VERDICTS)}"
    )
    assert not (accepted & _RETIRED), "a retired grade is still accepted"


# ── the two vocabularies must stay distinct, and stay comparable ─────────────


def test_a_verdict_is_never_a_machine_grade() -> None:
    """The negative assertion, and the reason the panel exists.

    Also covers the machine values, not just the display words: a verdict
    slugged into `highly_matching` would satisfy a labels-only check while
    reintroducing exactly the confusion this prevents.
    """
    grade_slugs = {grade.lower().replace(" ", "_") for grade in rating.GRADES}
    assert not (set(team_review.VERDICTS) & grade_slugs)
    assert not (set(team_review.VERDICT_LABELS.values()) & set(rating.GRADES))


def test_every_machine_grade_maps_to_exactly_one_verdict() -> None:
    """Totality, so the override-rate metric cannot silently skip a grade."""
    for grade in rating.GRADES:
        assert team_review.verdict_for_grade(grade) in team_review.VERDICTS
    covered = [g for grades in team_review.GRADES_FOR_VERDICT.values() for g in grades]
    assert sorted(covered) == sorted(rating.GRADES), "a grade is missing or duplicated"


def test_an_unknown_grade_raises_rather_than_being_skipped() -> None:
    with pytest.raises(ValueError):
        team_review.verdict_for_grade("Outstanding")


def test_agreement_is_computed_and_an_absent_grade_is_not_a_deviation() -> None:
    assert team_review.agrees_with_grade("pass", rating.GRADE_HIGHLY)
    assert team_review.agrees_with_grade("pass", rating.GRADE_MATCHING)
    assert not team_review.agrees_with_grade("pass", rating.GRADE_NOT)
    assert team_review.agrees_with_grade("reject", rating.GRADE_NOT)
    # A candidate with no profile written yet carries no machine opinion, so
    # there is nothing to deviate from. Counting it would inflate the metric.
    assert team_review.agrees_with_grade("hold", None)


def test_nothing_in_the_module_can_render_disapproval() -> None:
    """spec-doc6 section 8.2: measure the override rate, never nudge.

    A target that quietly discourages disagreement destroys the calibration
    signal it exists to measure, so the module must expose no wording a UI could
    surface as a warning.
    """
    source = (
        _REPO_ROOT / "backend" / "app" / "services" / "team_review.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    # Strip docstrings AND comments, and look only at what the module actually
    # exposes. The prose is where the no-nudge RULE is written down, so a
    # substring sweep over the raw file matches the rule's own statement of
    # itself. What matters is that no NAME or string literal a UI could render
    # carries disapproval.
    renderable: list[str] = [
        node.id if isinstance(node, ast.Name) else "" for node in ast.walk(tree)
    ]
    renderable += [
        node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and not _is_docstring(node, tree)
    ]
    renderable += [
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    haystack = " ".join(filter(None, renderable)).lower()
    for word in ("warn", "discourag", "should_reconsider", "flag_override", "nudge"):
        assert word not in haystack, (
            f"{word!r} appears in an identifier or a renderable string; that is a "
            "nudge, not a measurement"
        )


# ── the frontend agrees too ──────────────────────────────────────────────────


def test_the_frontend_type_and_labels_are_the_named_vocabulary() -> None:
    """The original defect reached the browser as well as the database.

    Skips rather than fails when the frontend is absent, so a backend-only
    checkout is not red for a reason it cannot fix.
    """
    types_file = _FRONTEND / "lib" / "types.ts"
    modal = _FRONTEND / "components" / "candidate-team-review-modal.tsx"
    if not types_file.exists() or not modal.exists():
        pytest.skip("frontend not present in this checkout")

    union = re.search(
        r"export type TeamRating\s*=\s*(.*?);", types_file.read_text(encoding="utf-8"), re.S
    )
    assert union, "no `export type TeamRating` in frontend/lib/types.ts"
    assert frozenset(re.findall(r'"([^"]+)"', union.group(1))) == frozenset(
        team_review.VERDICTS
    )

    modal_source = modal.read_text(encoding="utf-8")
    labels = re.search(r"RATING_LABELS[^=]*=\s*\{(.*?)\}", modal_source, re.S)
    assert labels, "no RATING_LABELS map in the Team Review modal"
    keys = frozenset(re.findall(r"^\s*([a-z_]+)\s*:", labels.group(1), re.M))
    assert keys == frozenset(team_review.VERDICTS)
    assert not (keys & _RETIRED)
