"""Why a Vaada session ended, recorded on the row instead of in a log line.

Revision ID: 0116_assessment_end_reason
Revises: 0115_drishti_functional_head

The assessment has stopped early on evidence coverage since 2026-08-23.
`ppi.conversation_may_close` is the decider, `interviewer.ConversationState`
reports which of six named stop conditions hold, and the question ceiling is a
ceiling rather than a target. None of that was DURABLE. The decision existed as
an `assessments.conversation_state` log line, so answering "did this candidate
stop early, or did we run out of questions" meant finding a CloudWatch entry
for one conversation id, and answering it for a cohort was not possible at all.

Those two endings are not the same event and must not read the same in the
table. Finishing at question fourteen of twenty is the feature working;
finishing at fourteen of fourteen is an interview that had nothing left to ask.
`completed_at` cannot tell them apart, and rule 8 applies in its own words: a
timestamp is not evidence of what happened.

ONE NULLABLE COLUMN AND A CHECK, and nothing is backfilled. A row written
before this revision has no recorded reason, and inferring one from
`next_question_index` against a question count that may itself have changed
would manufacture provenance for a decision nobody can now observe. NULL means
not recorded; it never means "no reason", which is also why a video-mode
session and a proctoring termination leave it NULL rather than being given a
word that claims a stopping decision they never made.

The CHECK enumerates `interviewer.STOP_CONDITIONS` in full rather than only the
two reasons the handler writes today, because the rule it states is that a
reason is one of Vaada's named stop conditions. Narrowing it to what is
currently reachable would turn the next legitimate reason into a production
write refused by a constraint nobody remembered, which is the same trap the
`PipelineStatus` enum records for a column that accepts a value the code cannot
read back.

Literals rather than imported constants, the convention every earlier seed and
constraint migration follows: a historical migration's effect must not shift
when a code constant is renamed. `tests/test_vaada_end_reason` compares this
list against the model and against the vocabulary, so the restatement is
checked rather than trusted.

Downgrade drops the constraint and the column. Nothing else reads either, so
there is no data to preserve beyond the reasons themselves, which are
observations rather than state the product depends on.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0116_assessment_end_reason"
down_revision = "0114_bgv_delivery_and_documents"
branch_labels = None
depends_on = None


#: `interviewer.STOP_CONDITIONS` as of this revision, in its own order.
END_REASONS: tuple[str, ...] = (
    "floor_reached",
    "every_dimension_covered",
    "no_probe_outstanding",
    "no_conflict_outstanding",
    "evidence_sufficient",
    "prompts_exhausted",
)

CONSTRAINT_NAME = "ck_assessment_conversations_end_reason"


def upgrade() -> None:
    op.add_column(
        "assessment_conversations",
        sa.Column("end_reason", sa.String(40), nullable=True),
    )
    allowed = ", ".join(f"'{reason}'" for reason in END_REASONS)
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "assessment_conversations",
        f"end_reason IS NULL OR end_reason IN ({allowed})",
    )


def downgrade() -> None:
    op.drop_constraint(
        CONSTRAINT_NAME, "assessment_conversations", type_="check"
    )
    op.drop_column("assessment_conversations", "end_reason")
