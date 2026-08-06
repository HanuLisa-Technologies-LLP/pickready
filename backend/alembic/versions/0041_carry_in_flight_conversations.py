"""Carry in-flight assessments across the per-candidate technical question move.

THE HAZARD THIS CLOSES
----------------------
Migration 0040 moved technical questions from a per-JOB bank
(`technical_questions`) to per-CANDIDATE rows (`candidate_technical_questions`).
`api/assessments._conversation_prompts` builds the conversation from the new
table, and for anyone already mid-assessment that table is EMPTY.

Follow what that does, because none of it announces itself:

  * the blended prompt list loses its technical half, so it becomes shorter than
    the `next_question_index` the candidate has already reached;
  * `respond` sees `index >= len(prompts)` and answers 409 "Conversation is
    already complete" -- to a candidate who is not finished;
  * `charge_completed` fires, so the customer is billed a full credit for an
    interview that was cut off;
  * `run_functional_assessment` then scores a transcript whose technical answers
    are keyed to `technical_questions` ids that nothing looks up any more, so
    every technical dimension grades Not Matching on evidence the candidate
    actually gave.

A COMPLETED conversation is in the same position until its report is written,
and a report is immutable once it is. So this backfills every link that has a
conversation row at all, not only the unfinished ones.

WHAT IT DOES
------------
1. Copies the job's active bank into per-link rows for every link with a
   conversation, preserving `ordinal`, `skill`, `prompt` and `rubric_json`. The
   blend is therefore the same length, in the same order, asking the same
   questions -- a candidate on question 31 of 45 comes back to question 31 of 45.

2. Re-keys that conversation's existing messages from the old
   `technical_questions` id to the new row's id, so `answers_by_key` still finds
   every answer already given and the scorer still reaches the rubric each one
   was asked against.

`generated_at` is left NULL, which is truthful: these questions were written by
the old per-job generator, not by the per-candidate agent, and NULL is exactly
what the product uses to mean "the candidate did not read a written question".

WHY THE NEW IDS ARE DERIVED RATHER THAN RANDOM
----------------------------------------------
`md5(link_id || technical_question_id)::uuid` is stable, so step 2 can compute
the same id step 1 inserted without carrying a mapping table between them. It
also makes the whole migration idempotent: re-running inserts nothing new and
re-keys nothing twice. This is an identifier, not a security primitive.

FORWARD-ONLY IN EFFECT
----------------------
`downgrade` removes the rows it added and restores the original question keys,
so a rollback to 0040 is clean. It cannot un-ask a question, but nothing here
asked one.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0041_carry_in_flight"
down_revision = "0040_candidate_technical"
branch_labels = None
depends_on = None


#: The derived id, written once so the INSERT and the UPDATE cannot drift.
DERIVED_ID = "md5(l.id::text || tq.id::text)::uuid"


def upgrade() -> None:
    # ── 1. Give every in-flight (and just-finished) assessment its own rows ───
    #
    # `NOT EXISTS` rather than ON CONFLICT: a link that already has per-link
    # rows was created after 0040 by `technical_interview.ensure_slots` and its
    # plan must not be half-overwritten with bank rows.
    op.execute(
        f"""
        INSERT INTO candidate_technical_questions (
            id, tenant_id, job_id, job_candidate_link_id,
            ordinal, skill, prompt, rubric_json, generated_at, created_at
        )
        SELECT
            {DERIVED_ID},
            l.tenant_id,
            l.job_id,
            l.id,
            tq.ordinal,
            tq.skill,
            tq.prompt,
            tq.rubric_json,
            NULL,
            now()
        FROM job_candidate_links l
        JOIN assessment_conversations c ON c.job_candidate_link_id = l.id
        JOIN technical_questions tq
          ON tq.job_id = l.job_id AND tq.is_active IS TRUE
        WHERE NOT EXISTS (
            SELECT 1 FROM candidate_technical_questions x
            WHERE x.job_candidate_link_id = l.id
        )
        """
    )

    # ── 2. Point the existing transcript at the rows that now carry the rubrics ─
    #
    # Scoped to the conversation's OWN link, which matters: a job's bank is
    # shared across every applicant, so an unscoped update would rewrite one
    # candidate's messages with another candidate's row ids.
    op.execute(
        f"""
        UPDATE assessment_messages m
        SET question_key = ({DERIVED_ID})::text
        FROM assessment_conversations c
        JOIN job_candidate_links l ON l.id = c.job_candidate_link_id
        JOIN technical_questions tq ON tq.job_id = l.job_id
        WHERE m.conversation_id = c.id
          AND m.question_key = tq.id::text
        """
    )

    # ── 3. The same for a follow-up still outstanding when the deploy landed ──
    #
    # `pending_question_key` is what the NEXT request answers under. Left
    # unmapped, that candidate's probe answer would be filed under an id no
    # scorer looks up, and the richest answer in the interview would be dropped.
    op.execute(
        f"""
        UPDATE assessment_conversations c
        SET pending_question_key = ({DERIVED_ID})::text
        FROM job_candidate_links l, technical_questions tq
        WHERE l.id = c.job_candidate_link_id
          AND tq.job_id = l.job_id
          AND c.pending_question_key = tq.id::text
        """
    )


def downgrade() -> None:
    # Reverse order: put the keys back BEFORE the rows they were derived from
    # are removed, or the derivation has nothing to match against.
    op.execute(
        f"""
        UPDATE assessment_conversations c
        SET pending_question_key = tq.id::text
        FROM job_candidate_links l, technical_questions tq
        WHERE l.id = c.job_candidate_link_id
          AND tq.job_id = l.job_id
          AND c.pending_question_key = ({DERIVED_ID})::text
        """
    )
    op.execute(
        f"""
        UPDATE assessment_messages m
        SET question_key = tq.id::text
        FROM assessment_conversations c
        JOIN job_candidate_links l ON l.id = c.job_candidate_link_id
        JOIN technical_questions tq ON tq.job_id = l.job_id
        WHERE m.conversation_id = c.id
          AND m.question_key = ({DERIVED_ID})::text
        """
    )
    op.execute(
        f"""
        DELETE FROM candidate_technical_questions q
        USING job_candidate_links l, technical_questions tq
        WHERE l.id = q.job_candidate_link_id
          AND tq.job_id = l.job_id
          AND q.id = {DERIVED_ID}
        """
    )
