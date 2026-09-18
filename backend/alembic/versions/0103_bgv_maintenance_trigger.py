"""Narrow the employment-history finality for the last-two rule.

Revision ID: 0103_bgv_maintenance_trigger
Revises: 0102_bgv_form_columns

Vivekium feature 5, owner-ruled final. The 0095 trigger made a finalised
history immutable against UPDATE and DELETE. The brief requires the history
to gain the candidate's newest employer and auto-drop the oldest, so:

* UPDATE still ALWAYS raises on a finalised history: no row's content is
  ever edited, which is the half of the 2026-09-12 decision that survives.
* DELETE raises UNLESS the transaction carries `app.bgv_maintenance = 'on'`,
  the GUC only `services/bgv_maintenance.append_employer` sets, and sets
  transaction-locally, so the one sanctioned delete is the cap's auto-drop.
* Erasure is unchanged: when the candidates row is already gone (a cascade
  from Delete My Profile or the consent sweep), `finalized` reads NULL and
  the delete proceeds, exactly as before.
"""
from __future__ import annotations

from alembic import op

revision = "0103_bgv_maintenance_trigger"
down_revision = "0102_bgv_form_columns"
branch_labels = None
depends_on = None

_NEW_FUNCTION = """
CREATE OR REPLACE FUNCTION candidate_employment_is_final()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    finalized TIMESTAMPTZ;
BEGIN
    SELECT employment_history_finalized_at INTO finalized
      FROM candidates
     WHERE id = COALESCE(OLD.candidate_id, NEW.candidate_id);

    IF finalized IS NOT NULL THEN
        IF TG_OP = 'DELETE'
           AND current_setting('app.bgv_maintenance', true) = 'on' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION
            'candidate_employments is final for candidate % and cannot be % after submission',
            COALESCE(OLD.candidate_id, NEW.candidate_id), lower(TG_OP)
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$
"""

_OLD_FUNCTION = """
CREATE OR REPLACE FUNCTION candidate_employment_is_final()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    finalized TIMESTAMPTZ;
BEGIN
    SELECT employment_history_finalized_at INTO finalized
      FROM candidates
     WHERE id = COALESCE(OLD.candidate_id, NEW.candidate_id);

    IF finalized IS NOT NULL THEN
        RAISE EXCEPTION
            'candidate_employments is final for candidate % and cannot be % after submission',
            COALESCE(OLD.candidate_id, NEW.candidate_id), lower(TG_OP)
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$
"""


def upgrade() -> None:
    op.execute(_NEW_FUNCTION)


def downgrade() -> None:
    op.execute(_OLD_FUNCTION)
