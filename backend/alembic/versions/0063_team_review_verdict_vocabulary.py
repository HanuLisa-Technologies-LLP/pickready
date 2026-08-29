"""Team Review stores a DECISION verdict, not a retired assessment grade.

Revision ID: 0063_team_review_verdicts
Revises: 0062_embedding_provenance
Create Date: 2026-08-29

WHAT WAS WRONG, PRECISELY
--------------------------
`ck_candidate_team_reviews_rating` enforced the five-label scale the product
retired on 2026-07-30:

    CHECK (rating IN ('very_high','high','medium','low','developing'))

It is tempting to call that drift from `services/rating.py`, and it is not.
Nothing had drifted: the CHECK, the Pydantic literal in `schemas/candidates.py`,
`_TEAM_RATING_ORDER` in `api/candidates.py`, and the frontend labels and types
all agreed with each other. A reviewer was never OFFERED a current grade, so a
reviewer was never refused one; Pydantic would have answered 422 long before the
database saw anything.

The five-label scale survived here alone, deliberately, with the reason recorded
at the call site in `components/candidate-team-review-modal.tsx`: the four
grades are what an agent outputs ABOUT a candidate, and rendering a colleague's
note on the same words would make it read as a machine grade. That reasoning is
correct and is preserved. What was wrong is the vocabulary it protected.

WHY PASS / HOLD / REJECT
-------------------------
`docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md` Column 7 specifies "Checkbox
verdicts: Pass / Hold / Reject". That document is precedence rank 4 under
spec-doc6 section 0.2 and is the authority for this exact surface, and no
higher-ranked document contradicts it here.

The change is a change of KIND, not of granularity. An assessment scale is
ordinal and answers "how good"; a decision vocabulary is categorical and answers
"what now". `hold` is not a relabelled `medium`; it means "I am not deciding
yet". Reading the old scale as five ordered rungs and the new one as three is
the wrong comparison.

The full argument, and the override-rate mapping that keeps the two vocabularies
COMPARABLE without making them identical, live in `services/team_review.py`.
`tests/test_team_review_vocabulary.py` asserts this CHECK's accepted set equals
that module's `VERDICTS`, so the database and the code cannot drift apart again.

NO BACKFILL IS EXECUTED, AND THE MIGRATION STILL HANDLES ROWS
--------------------------------------------------------------
`candidate_team_reviews` held 0 rows in the development database on 2026-08-29,
and the product owner confirmed the surface is not yet deployed, so there are no
rows anywhere to remap. That is the reason no mapping runs, and it is a fact
about today rather than a property of the migration.

So the remap is written anyway and guarded. If a row exists, it is mapped
explicitly; if a row carries a value the mapping does not cover, the migration
RAISES rather than dropping it or coercing it to a default. An unmapped human
observation silently rewritten is exactly the failure this project has already
paid for once, and a CHECK swap is a one-way door: the old value is gone the
moment the constraint is replaced.

The mapping, stated once so it is reviewable:

    very_high, high  -> pass      the reviewer was positive
    medium           -> hold      the reviewer was undecided
    low, developing  -> reject    the reviewer was negative

`medium -> hold` is the only judgment call. `medium` on an ordinal scale is a
weak-positive; `hold` is an abstention. Mapping it to `pass` would manufacture a
decision nobody made, so it maps to the abstention, which is the reading that
asserts less about what the reviewer meant.

ROLLING-DEPLOY SAFETY
---------------------
This migration is NOT additive, and saying so plainly matters more than the
label. Replacing a CHECK narrows what the table accepts, so during a rolling
deploy an old pod still validating the five-label literal would have its writes
rejected by the new constraint.

That is survivable here only because the table is empty and the surface is not
deployed, so no old pod is writing to it. On a table with live writers this
would need the two-phase form: add the new constraint as NOT VALID, deploy the
code, then validate and drop the old one. Do not copy this migration's shape
onto a table that has traffic.
"""
from alembic import op
import sqlalchemy as sa

revision = "0063_team_review_verdicts"
down_revision = "0062_embedding_provenance"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_candidate_team_reviews_rating"
_TABLE = "candidate_team_reviews"

#: Kept as a literal rather than imported from `services.team_review`. A
#: migration must describe the schema at ITS point in history and keep doing so
#: after the application moves on; importing the live constant would make this
#: file silently change meaning the day someone adds a fourth verdict.
#: `tests/test_team_review_vocabulary.py` asserts the two agree TODAY, which is
#: the guarantee that is actually wanted.
_NEW_VERDICTS = ("pass", "hold", "reject")
_OLD_GRADES = ("very_high", "high", "medium", "low", "developing")

#: See the module docstring for why `medium` maps to `hold`.
_FORWARD = {
    "very_high": "pass",
    "high": "pass",
    "medium": "hold",
    "low": "reject",
    "developing": "reject",
}

#: Deliberately lossy, and `downgrade` says so. `pass` cannot recover whether
#: the reviewer meant `very_high` or `high`, so it takes the weaker of the two:
#: restoring a stronger endorsement than a person actually gave would be an
#: invention, and the weaker direction asserts less.
_BACKWARD = {"pass": "high", "hold": "medium", "reject": "low"}


def _values(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{name}'" for name in names)


def _remap(mapping: dict[str, str], accepted: tuple[str, ...]) -> None:
    """Map every row, and refuse loudly on anything the mapping does not cover."""
    bind = op.get_bind()
    unmapped = bind.execute(
        sa.text(
            f"SELECT DISTINCT rating FROM {_TABLE} "  # noqa: S608 - names are module constants
            "WHERE rating <> ALL(:known)"
        ),
        {"known": list(mapping)},
    ).scalars().all()
    if unmapped:
        raise RuntimeError(
            f"{_TABLE}.rating holds {sorted(unmapped)!r}, which this migration "
            f"has no mapping for. Expected only {sorted(mapping)!r}. Refusing to "
            "guess: a human observation rewritten to the wrong verdict is worse "
            "than a failed migration, and replacing the CHECK destroys the "
            "original value. Add the mapping deliberately and re-run."
        )
    for old_value, new_value in mapping.items():
        bind.execute(
            sa.text(f"UPDATE {_TABLE} SET rating = :new WHERE rating = :old"),  # noqa: S608
            {"new": new_value, "old": old_value},
        )
    remaining = bind.execute(
        sa.text(
            f"SELECT count(*) FROM {_TABLE} WHERE rating <> ALL(:accepted)"  # noqa: S608
        ),
        {"accepted": list(accepted)},
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            f"{remaining} row(s) in {_TABLE} still fall outside {accepted!r} after "
            "remapping. The new CHECK would reject them. Not proceeding."
        )


def upgrade() -> None:
    _remap(_FORWARD, _NEW_VERDICTS)
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT, _TABLE, f"rating IN ({_values(_NEW_VERDICTS)})"
    )


def downgrade() -> None:
    _remap(_BACKWARD, _OLD_GRADES)
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT, _TABLE, f"rating IN ({_values(_OLD_GRADES)})"
    )
