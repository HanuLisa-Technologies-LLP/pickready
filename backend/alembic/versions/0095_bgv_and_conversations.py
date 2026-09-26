"""Recruiter-driven background verification, and native conversations.

TWO OWNERS, TWO TABLES, AND THAT IS THE WHOLE DESIGN
------------------------------------------------------
`candidate_employments` is the CANDIDATE's claim: tenant-free, like
`bgv_inquiries` and `candidates` themselves, because a person's work history
travels with the person across applications and across customers.

`bgv_verifications` is one CUSTOMER's diligence: tenant-scoped, behind the same
RLS policy as every other tenant table, UNIQUE on (tenant, employment). Tenant
A marking an employer verified must never clear tenant B, who has done no
diligence at all.

This does NOT replace migration 0085's `bgv_inquiries`. That feature is the
candidate's own verification of their history, shared with a tenant only
through `bgv_share_consents`, and it gates nothing by design. This one is the
recruiter's, and it blocks an offer. Same words, different owners; folding them
onto one table would put two sets of rules on one row.

IMMUTABILITY IS ENFORCED BY THE DATABASE, NOT BY A DISABLED BUTTON
--------------------------------------------------------------------
`trg_candidate_employments_immutable` refuses every UPDATE and DELETE once
`candidates.employment_history_finalized_at` is stamped. The service layer
refuses first with a sentence the candidate can act on; the trigger refuses
regardless, so a future route, a data fix or a psql session cannot quietly
rewrite what an employer is being asked to confirm. A guarantee that lives only
in application code is a guarantee until the next caller.

THE CIRCULAR REFERENCE IS DELIBERATE AND IS BROKEN IN THE RIGHT PLACE
-----------------------------------------------------------------------
A verification points at its conversation and a conversation points back at its
verification, because both directions are read on hot paths. Postgres cannot
create two tables that reference each other in one statement, so `conversations`
is created first and its foreign key is added by ALTER afterwards.
"""
from alembic import op

revision = "0095_bgv_and_conversations"
down_revision = "0094_report_provenance"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "bgv_verifications",
    "conversations",
    "conversation_participants",
    "conversation_messages",
    "conversation_attachments",
)

#: Exactly `DEFAULT_PERMISSION_MATRIX`'s entry for these three, restated as
#: rows. `tests/test_capability_seed_parity.py` fails a fresh database that is
#: missing one, which is the other half of adding a capability constant.
CUSTOMER_ROLES = (
    "client",
    "recruitment_manager",
    "hr_manager",
    "recruiter",
    "hiring_manager",
)
NEW_CAPABILITIES = ("view_bgv", "manage_bgv", "use_conversations")


def upgrade() -> None:
    # ── The candidate's claim ────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE candidate_employments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            candidate_id UUID NOT NULL
                REFERENCES candidates(id) ON DELETE CASCADE,
            employer_name VARCHAR(200) NOT NULL,
            designation VARCHAR(200) NOT NULL,
            started_on DATE NOT NULL,
            ended_on DATE NOT NULL,
            hr_name VARCHAR(200) NOT NULL,
            hr_email VARCHAR(320) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- "Employed from 2024 to 2019" is a typo somebody would otherwise
            -- be asked to confirm, in an email that would read as nonsense.
            CONSTRAINT ck_candidate_employments_dates CHECK (ended_on >= started_on),
            CONSTRAINT ck_candidate_employments_hr_email
                CHECK (position('@' in hr_email) > 1)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_candidate_employments_candidate"
        " ON candidate_employments (candidate_id, started_on)"
    )

    op.execute("ALTER TABLE candidates ADD COLUMN employment_background VARCHAR(20)")
    op.execute(
        "ALTER TABLE candidates ADD COLUMN employment_history_finalized_at TIMESTAMPTZ"
    )
    op.execute(
        """
        ALTER TABLE candidates ADD CONSTRAINT ck_candidates_employment_background
        CHECK (employment_background IS NULL
               OR employment_background IN ('fresher', 'experienced'))
        """
    )

    # ── Conversations, before the verification that points at one ────────────
    op.execute(
        """
        CREATE TABLE conversations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            kind VARCHAR(20) NOT NULL,
            subject VARCHAR(300) NOT NULL,
            candidate_id UUID REFERENCES candidates(id) ON DELETE CASCADE,
            bgv_verification_id UUID,
            job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            thread_token VARCHAR(64) NOT NULL,
            last_message_at TIMESTAMPTZ,
            created_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_conversations_thread_token UNIQUE (thread_token),
            CONSTRAINT uq_conversations_bgv_verification
                UNIQUE (bgv_verification_id),
            CONSTRAINT ck_conversations_kind CHECK (kind IN ('candidate', 'bgv')),
            CONSTRAINT ck_conversations_status CHECK (status IN ('open', 'closed')),
            -- A BGV thread without its verification, or a candidate thread
            -- without its candidate, is a thread nothing can authorise.
            CONSTRAINT ck_conversations_subject_present CHECK (
                (kind = 'bgv' AND bgv_verification_id IS NOT NULL)
                OR (kind = 'candidate' AND candidate_id IS NOT NULL)
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_conversations_tenant_recent"
        " ON conversations (tenant_id, last_message_at)"
    )
    op.execute(
        "CREATE INDEX ix_conversations_candidate"
        " ON conversations (tenant_id, candidate_id)"
    )

    # ── One customer's verification of one employer ──────────────────────────
    op.execute(
        """
        CREATE TABLE bgv_verifications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            candidate_id UUID NOT NULL
                REFERENCES candidates(id) ON DELETE CASCADE,
            candidate_employment_id UUID NOT NULL
                REFERENCES candidate_employments(id) ON DELETE CASCADE,
            initiated_from_link_id UUID
                REFERENCES job_candidate_links(id) ON DELETE SET NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'not_started',
            conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
            first_sent_at TIMESTAMPTZ,
            responded_at TIMESTAMPTZ,
            -- RESTRICT, alone with review_dispositions.decided_by among user
            -- references in this schema: a verification whose person was
            -- erased asserts that a human decided while being unable to say
            -- who, which is indistinguishable from the pipeline writing it.
            decided_by UUID REFERENCES users(id) ON DELETE RESTRICT,
            decided_at TIMESTAMPTZ,
            decision_note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_bgv_verification_employer
                UNIQUE (tenant_id, candidate_employment_id),
            CONSTRAINT ck_bgv_verifications_status CHECK (
                status IN ('not_started', 'pending', 'verified', 'not_verified')
            ),
            -- A decided row names its decider and when. Enforced here so the
            -- audit question "who cleared this employer" always has an answer.
            CONSTRAINT ck_bgv_verifications_decided CHECK (
                status NOT IN ('verified', 'not_verified')
                OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_bgv_verifications_tenant_candidate"
        " ON bgv_verifications (tenant_id, candidate_id)"
    )
    op.execute(
        "CREATE INDEX ix_bgv_verifications_status"
        " ON bgv_verifications (tenant_id, status)"
    )
    op.execute(
        "ALTER TABLE conversations ADD CONSTRAINT fk_conversations_bgv_verification"
        " FOREIGN KEY (bgv_verification_id)"
        " REFERENCES bgv_verifications(id) ON DELETE CASCADE"
    )

    # ── Participants, messages, attachments ──────────────────────────────────
    op.execute(
        """
        CREATE TABLE conversation_participants (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_id UUID NOT NULL
                REFERENCES conversations(id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            party VARCHAR(20) NOT NULL,
            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            external_email VARCHAR(320),
            external_name VARCHAR(200),
            last_read_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_participant_user UNIQUE (conversation_id, user_id),
            CONSTRAINT ck_participants_party CHECK (
                party IN ('recruiter', 'candidate', 'employer_hr', 'system')
            ),
            -- Exactly one identity. Neither is a row nothing can deliver to;
            -- both is two identities wearing one name.
            CONSTRAINT ck_participants_one_identity CHECK (
                (user_id IS NOT NULL AND external_email IS NULL)
                OR (user_id IS NULL AND external_email IS NOT NULL)
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_participants_conversation"
        " ON conversation_participants (conversation_id)"
    )
    op.execute(
        "CREATE INDEX ix_participants_user_unread"
        " ON conversation_participants (user_id, last_read_at)"
    )

    op.execute(
        """
        CREATE TABLE conversation_messages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_id UUID NOT NULL
                REFERENCES conversations(id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            author_party VARCHAR(20) NOT NULL,
            author_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            author_email VARCHAR(320),
            author_name VARCHAR(200),
            body TEXT NOT NULL,
            channel VARCHAR(20) NOT NULL,
            delivery_status VARCHAR(20) NOT NULL DEFAULT 'delivered',
            delivery_detail TEXT,
            client_token VARCHAR(64),
            inbound_message_id VARCHAR(400),
            email_message_id VARCHAR(400),
            email_log_id UUID REFERENCES email_log(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- Idempotency in both directions. A retried send carries the same
            -- client token; SES and SNS deliver at least once, so an inbound
            -- reply is keyed on the provider's own id.
            CONSTRAINT uq_message_client_token
                UNIQUE (conversation_id, client_token),
            CONSTRAINT uq_message_inbound_id
                UNIQUE (tenant_id, inbound_message_id),
            CONSTRAINT ck_messages_party CHECK (
                author_party IN ('recruiter', 'candidate', 'employer_hr', 'system')
            ),
            CONSTRAINT ck_messages_channel CHECK (channel IN ('chat', 'email')),
            CONSTRAINT ck_messages_delivery CHECK (
                delivery_status IN
                    ('pending', 'sent', 'delivered', 'failed', 'bounced')
            ),
            CONSTRAINT ck_messages_body_bounded CHECK (length(body) <= 20000)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_messages_conversation"
        " ON conversation_messages (conversation_id, created_at, id)"
    )
    op.execute(
        "CREATE INDEX ix_messages_tenant_created"
        " ON conversation_messages (tenant_id, created_at)"
    )

    op.execute(
        """
        CREATE TABLE conversation_attachments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            message_id UUID NOT NULL
                REFERENCES conversation_messages(id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            object_key VARCHAR(500) NOT NULL,
            filename VARCHAR(300) NOT NULL,
            content_type VARCHAR(120) NOT NULL,
            size_bytes BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_attachments_size CHECK (size_bytes > 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_attachments_message ON conversation_attachments (message_id)"
    )

    # ── Immutability, in the database ────────────────────────────────────────
    #
    # SECURITY DEFINER is deliberate and narrow: the trigger reads `candidates`
    # to find the stamp, and the app role's own RLS view of that table is not
    # what should decide whether a write is legal. The function body touches
    # exactly one column of one row and returns nothing else.
    op.execute(
        """
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
    )
    op.execute(
        """
        CREATE TRIGGER trg_candidate_employments_immutable
        BEFORE UPDATE OR DELETE ON candidate_employments
        FOR EACH ROW EXECUTE FUNCTION candidate_employment_is_final()
        """
    )

    # ── Grants and RLS ───────────────────────────────────────────────────────
    #
    # `candidate_employments` is tenant-free, exactly like `candidates` and
    # `bgv_inquiries`: it is reached through the candidate's own session or
    # through the audited bypass, and a tenant equality policy on a row with no
    # tenant would hide it from everybody.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON candidate_employments TO pickready_app"
    )

    for table in TENANT_TABLES:
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO pickready_app"
        )
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (
                tenant_id = current_setting('app.tenant_id', true)::uuid
                OR current_setting('app.bypass_rls', true) = 'on'
            )
            WITH CHECK (
                tenant_id = current_setting('app.tenant_id', true)::uuid
                OR current_setting('app.bypass_rls', true) = 'on'
            )
            """
        )

    # ── The other half of a capability constant ──────────────────────────────
    for role in CUSTOMER_ROLES:
        for capability in NEW_CAPABILITIES:
            op.execute(
                f"""
                INSERT INTO role_permissions (id, tenant_id, role, capability, allowed)
                SELECT gen_random_uuid(), NULL, '{role}', '{capability}', true
                WHERE NOT EXISTS (
                    SELECT 1 FROM role_permissions b
                     WHERE b.tenant_id IS NULL
                       AND b.role = '{role}'
                       AND b.capability = '{capability}'
                )
                """
            )


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permissions WHERE tenant_id IS NULL AND capability IN "
        "('view_bgv', 'manage_bgv', 'use_conversations')"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_candidate_employments_immutable"
        " ON candidate_employments"
    )
    op.execute("DROP FUNCTION IF EXISTS candidate_employment_is_final()")
    op.execute("DROP TABLE IF EXISTS conversation_attachments")
    op.execute("DROP TABLE IF EXISTS conversation_messages")
    op.execute("DROP TABLE IF EXISTS conversation_participants")
    op.execute(
        "ALTER TABLE conversations"
        " DROP CONSTRAINT IF EXISTS fk_conversations_bgv_verification"
    )
    op.execute("DROP TABLE IF EXISTS bgv_verifications")
    op.execute("DROP TABLE IF EXISTS conversations")
    op.execute("DROP TABLE IF EXISTS candidate_employments")
    op.execute(
        "ALTER TABLE candidates"
        " DROP CONSTRAINT IF EXISTS ck_candidates_employment_background"
    )
    op.execute(
        "ALTER TABLE candidates DROP COLUMN IF EXISTS employment_history_finalized_at"
    )
    op.execute("ALTER TABLE candidates DROP COLUMN IF EXISTS employment_background")
