"""Give the assessment conversation somewhere to hold an adaptive follow-up.

WHY
---
`api/assessments.respond` was an index into a pre-generated list: append the
fixed prompt, store the answer, increment `next_question_index`, return the next
prompt. No LLM call happened during the conversation at all, so there was no
agent in the loop to have memory, and a vague answer was followed by the next
scripted question regardless of what the candidate had just said.

Adaptive follow-ups need state that survives between two HTTP requests, because
the follow-up is generated when answer N is submitted and answered on request
N+1. These four columns are that state.

WHY NOT JUST APPEND TO THE PROMPT LIST
--------------------------------------
The prompt list is derived per request from the job's framework and this
candidate's questions. Its LENGTH is load-bearing in two places that have
nothing to do with conversation flow:

  * completion, and therefore BILLING -- `charge_completed` fires the moment
    `next_question_index >= len(prompts)`;
  * `answers_by_key`, which groups candidate answers by `question_key` for
    scoring.

Growing the list would move when a customer is charged and would introduce keys
the scorer has never seen. So a follow-up deliberately does NOT extend the list
and does NOT advance the index. It is answered under the SAME `question_key` as
the question that produced it, which means its answer joins that question's
existing group and every scorer, rubric and report row keeps working untouched.

BOUNDEDNESS IS A COLUMN, NOT A CONVENTION
-----------------------------------------
`follow_ups_used` is persisted rather than counted from the transcript. An
interview that can ask "one more thing" has to be provably finite: with a stored
counter the ceiling holds even if the transcript is rewritten, a request is
retried, or a follow-up message fails to persist. Total turns are therefore
bounded by len(prompts) + MAX_FOLLOW_UPS_PER_CONVERSATION, always.

`pending_question_key` is what makes the answer land in the right group, and
`pending_domain` keeps the technical/PPI split intact for the fan-out scorers.

Nullable with no default beyond the counter: an in-flight conversation created
before this migration simply has no pending follow-up and continues exactly as
it did.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0038_conversation_follow_up"
down_revision = "0037_mark_demo_tenants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assessment_conversations",
        sa.Column("pending_prompt", sa.Text(), nullable=True),
    )
    op.add_column(
        "assessment_conversations",
        sa.Column("pending_question_key", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "assessment_conversations",
        sa.Column("pending_domain", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "assessment_conversations",
        sa.Column(
            "follow_ups_used",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("assessment_conversations", "follow_ups_used")
    op.drop_column("assessment_conversations", "pending_domain")
    op.drop_column("assessment_conversations", "pending_question_key")
    op.drop_column("assessment_conversations", "pending_prompt")
