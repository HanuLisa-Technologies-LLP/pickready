"""Record the wording a candidate actually read for a base question.

WHY
---
`services/interviewer.compose_next_question` says the next scripted question
the way an interviewer would say it at that point in the conversation,
conditioned on the transcript so far. That wording has to be persisted, for the
same structural reason `pending_prompt` does: it is generated when answer N is
submitted and shown on request N+1, and nothing holds the conversation between
two stateless HTTP requests.

THE REAL REASON IT IS A COLUMN AND NOT A RETURN VALUE
-----------------------------------------------------
The agent message is written on the request that CARRIES the answer, not on the
request that showed the question. Without somewhere to keep the delivered
wording, `respond` would show the composed question and then log the stored one,
and `assessment_messages` -- which is the transcript every scorer reads, and the
memory the interviewer itself reads back on the next turn -- would be a record
of a conversation that never happened. The candidate would be answering one
question and the report would be written against the text of another.

WHAT IT DOES NOT CHANGE
-----------------------
Delivery is WORDING only. `next_question_index` is untouched, so completion and
therefore billing fire after exactly the same set of base questions; the
`question_key` is untouched, so `answers_by_key` groups exactly as before; and
`interviewer._substance_preserved` refuses any rewrite that dropped a specific
term, because a base question is scored against its own stored rubric and a
rewrite that quietly changed the question would be graded against a rubric for
a question nobody was asked.

NULL IS THE SAFE STATE
----------------------
NULL means "no rewrite available, use the stored text" -- an LLM outage, a
timeout, a rewrite that failed validation, or a conversation that started before
this migration. That is the product's previous behaviour and always a correct
thing to ask, which is why the column is nullable with no default and no
backfill: an in-flight conversation simply continues as it did.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# Kept under 32 characters: `alembic_version.version_num` is VARCHAR(32), and a
# longer id fails at the UPDATE after the DDL has already run.
revision = "0039_delivered_prompt"
down_revision = "0038_conversation_follow_up"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assessment_conversations",
        sa.Column("delivered_prompt", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assessment_conversations", "delivered_prompt")
