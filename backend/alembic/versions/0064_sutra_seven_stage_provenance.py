"""Sutra's seven stages, Bodha's session state, and one correlation id per job.

Revision ID: 0064_sutra_seven_stage
Revises: 0063_team_review_verdicts
Create Date: 2026-08-29

WHAT THIS IS FOR
----------------
spec-doc6 §4.3 activates the job-setup pipeline and makes one requirement of it
that the schema could not previously hold:

    "Traceability is a product requirement, not a log line. Every item in the
     frozen matrix stores which Layer 1 / Layer 2 / Layer 3 input produced its
     weight and the multiplier each contributed."

`job_competencies` stored a name, a description and a required level. The seven
stages produce five more things per item -- the observable-evidence statement,
the evidence sources, the assessment method, the weight with its four terms, and
the threshold -- and a matrix that keeps only the name has run the stages and
thrown away six sevenths of what they produced. A reviewer asking "why is this
weighted the way it is" could then only be answered by re-running the pipeline,
which is not an answer: the inputs may have changed since.

`job_swot_intakes` stored four arrays and a transcript. Runbook §18.3's seven
probes, §18.5's best-performer test and §18.4's situation classification are all
session state that has to survive a page reload, and the situation type in
particular is the single most expensive thing at intake to get wrong. Holding it
only in memory would mean re-classifying on every request, which is how a
confirmed classification silently becomes an unconfirmed one.

ADDITIVE, AND SAFE UNDER A ROLLING DEPLOY
------------------------------------------
Every column is added NULLABLE, or NOT NULL with a server default, and no
existing column, constraint or index is changed or dropped. An old pod that has
never heard of these columns keeps inserting and updating exactly as it did:
its INSERTs name their columns explicitly (SQLAlchemy always does), so the new
ones take their defaults, and its UPDATEs touch columns that still exist.

The one CHECK added (`ck_job_swot_intakes_situation_key`) admits NULL, which is
what every pre-existing row carries and what an old pod will keep writing.

WHY `jobs.correlation_id` IS HERE AND NOT A LOG FIELD
------------------------------------------------------
spec-doc6 §4.1 requires "a correlation ID issued at job creation ... traceable
through Bodha, Sutra, Yukti, Vaada, Miti and Siddhi, and ... in every audit row
and log line for that flow". A value that lives only in log lines cannot be
joined to an audit row months later, and `audit_log.correlation_id` (0061) has
no source to copy from without this column. Issued once, at creation, never
rewritten.

WHY THE WEIGHT IS `double precision` AND THE THRESHOLD IS JSONB
----------------------------------------------------------------
The weight is one number and is compared and ordered, so it is a column. The
threshold is three values that are always read together and never queried apart
(`independence_required`, `level`, `max_age_days`), so it is one JSONB document
rather than three columns nothing will ever filter on independently.

NOTHING HERE CROSSES A CLIENT BOUNDARY. `weight`, `threshold_json` and
`provenance_json` are internal ranking data of exactly the kind
`report_dimensions.required_level` has always been. The API projects the matrix
through `ppi._matrix_item`, which converts to words; the provenance the Hiring
Manager reads is rendered as sentences by `hiring.scorecard.plain_provenance`.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0064_sutra_seven_stage"
down_revision = "0063_team_review_verdicts"
branch_labels = None
depends_on = None

#: The six §18.4 situation types, as literals. A migration describes the schema
#: at its own point in history: importing `hiring.situations.SITUATIONS` would
#: make this CHECK silently change meaning the day a seventh type is added,
#: which is precisely when a reviewer would want the migration to be stable.
#: `tests/test_job_setup_live.py` asserts this set equals the module's today.
_SITUATION_KEYS = (
    "gap_fill",
    "turnaround",
    "scale_up",
    "greenfield",
    "steady_state",
    "succession",
)

#: Bodha's session phases, in order. Persisted because the session is resumable
#: and a phase recomputed from the arrays would send a manager who genuinely had
#: nothing to add back round to the same question forever, which is the reason
#: `area_index` is already a column rather than a derivation.
_SWOT_PHASES = ("areas", "force_ranking", "best_performer", "situation", "rework", "complete")


def _values(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{name}'" for name in names)


def upgrade() -> None:
    # ── The seven stages, on the matrix item ─────────────────────────────────
    op.add_column(
        "job_competencies",
        sa.Column("dimension", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "job_competencies",
        sa.Column("observable_evidence", sa.Text(), nullable=True),
    )
    op.add_column(
        "job_competencies",
        sa.Column("evidence_sources", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "job_competencies",
        sa.Column("assessment_method", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "job_competencies",
        sa.Column("weight", sa.Float(), nullable=True),
    )
    op.add_column(
        "job_competencies",
        sa.Column("threshold_json", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "job_competencies",
        sa.Column("disqualifier", sa.Text(), nullable=True),
    )
    # The acceptance criterion of spec-doc6 §4.3, as a column: which Layer 1 /
    # Layer 2 / Layer 3 input produced this weight and what each contributed.
    op.add_column(
        "job_competencies",
        sa.Column("provenance_json", postgresql.JSONB(), nullable=True),
    )
    # The hiring manager's own sentence, quoted, so the review screen can show
    # them the words that produced the criterion beside the criterion.
    op.add_column(
        "job_competencies",
        sa.Column("swot_origin", sa.Text(), nullable=True),
    )
    op.add_column(
        "job_competencies",
        sa.Column("anchor_key", sa.String(length=80), nullable=True),
    )
    # §20.3: competencies are ranked 1..n and the weight derives from the rank.
    # Stored so the ordering a hiring manager force-ranked is recoverable after
    # the weights have been clamped and renormalised on top of it.
    op.add_column(
        "job_competencies",
        sa.Column("force_rank", sa.Integer(), nullable=True),
    )

    # ── Bodha's session state ───────────────────────────────────────────────
    op.add_column(
        "job_swot_intakes",
        sa.Column(
            "phase",
            sa.String(length=20),
            nullable=False,
            server_default="areas",
        ),
    )
    op.add_column(
        "job_swot_intakes",
        sa.Column("situation_key", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "job_swot_intakes",
        sa.Column(
            "situation_confirmed_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "job_swot_intakes",
        sa.Column(
            "probes_asked",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
    )
    # Three-valued on purpose: NULL means §18.5's best-performer test has not
    # been put yet, which is a different state from "no" and must never be read
    # as a pass. `swot_quality.excludes_best_performer` keeps the same three
    # values in Python.
    op.add_column(
        "job_swot_intakes",
        sa.Column("best_performer_excluded", sa.Boolean(), nullable=True),
    )
    # The last `swot_quality.review` verdict: which §18.5 rules refused, which
    # checks are still outstanding, what the signals proposed.
    op.add_column(
        "job_swot_intakes",
        sa.Column(
            "quality_json",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "job_swot_intakes",
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_job_swot_intakes_situation_key",
        "job_swot_intakes",
        f"situation_key IS NULL OR situation_key IN ({_values(_SITUATION_KEYS)})",
    )
    op.create_check_constraint(
        "ck_job_swot_intakes_phase",
        "job_swot_intakes",
        f"phase IN ({_values(_SWOT_PHASES)})",
    )

    # ── One correlation id per job, issued at creation ───────────────────────
    op.add_column(
        "jobs", sa.Column("correlation_id", sa.String(length=64), nullable=True)
    )
    # Backfilled from the primary key rather than left NULL. A job created
    # before this migration still has a flow to trace, and its own id is the one
    # value guaranteed to be unique and already recorded on every audit row that
    # names it. New jobs get a freshly minted id at creation.
    op.execute(
        "UPDATE jobs SET correlation_id = 'job-' || replace(id::text, '-', '') "
        "WHERE correlation_id IS NULL"
    )
    op.create_index(
        "ix_jobs_correlation_id", "jobs", ["correlation_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_correlation_id", table_name="jobs")
    op.drop_column("jobs", "correlation_id")

    op.drop_constraint("ck_job_swot_intakes_phase", "job_swot_intakes", type_="check")
    op.drop_constraint(
        "ck_job_swot_intakes_situation_key", "job_swot_intakes", type_="check"
    )
    for column in (
        "correlation_id",
        "quality_json",
        "best_performer_excluded",
        "probes_asked",
        "situation_confirmed_at",
        "situation_key",
        "phase",
    ):
        op.drop_column("job_swot_intakes", column)

    for column in (
        "force_rank",
        "anchor_key",
        "swot_origin",
        "provenance_json",
        "disqualifier",
        "threshold_json",
        "weight",
        "assessment_method",
        "evidence_sources",
        "observable_evidence",
        "dimension",
    ):
        op.drop_column("job_competencies", column)
