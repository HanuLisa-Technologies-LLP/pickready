"""The agent action ledger (migration 0090, RPN-AI-UP-001 W5.1).

WHY A LEDGER AND NOT A LOG
---------------------------
`dispatch()` hands work to a runner and `workers/status.py` answers "what stage
is this run at". Neither can answer the only question that matters after a
timeout on an outbound POST: DID THE EFFECT HAPPEN. A log line written after
the call cannot answer it either, because the process that would have written
it is the one that died.

So the row is written BEFORE the call, it carries the idempotency key the
effect is keyed on, and its state machine has a value for "we do not know".

UNKNOWN IS THE POINT, AND IT IS A THIRD VALUE
-----------------------------------------------
`send_email` returning `{"status": "failed"}` for a permanent failure it
deliberately did not retry is a run that succeeded and an email that did not
arrive: a two-valued model of a three-valued world. A timeout on a POST is
neither success nor failure. Retrying a FAILED action is correct; retrying an
UNKNOWN action issues the side effect a second time.

UNKNOWN is therefore resolved by READING BACK -- this ledger, and the
provider's own idempotency support -- and never by a blind retry. The state
machine in `services/agent_actions/ledger.py` refuses the retry structurally:
there is no UNKNOWN to RUNNING edge, so a caller cannot reach one by
forgetting.

WHAT EACH TIMESTAMP MEANS, BECAUSE FOUR OF THEM IS THREE TOO MANY IF THEY BLUR
-------------------------------------------------------------------------------
    requested_at   the row was written. The request exists.
    sealed_at      the action was committed to execution. For an irreversible
                   action this is the moment a human approval was recorded, and
                   the adapter is unreachable until it is stamped.
    committed_at   the external effect is known to have happened.
    verified_at    the effect was confirmed by reading the world back, which is
                   the only way an UNKNOWN is ever resolved.

`requested_at` deliberately replaces the usual `created_at`: two columns for
one fact is two columns that eventually disagree.

RLS MIRRORS `candidate_updates` (0079) ON WRITES
--------------------------------------------------
A recruiter's own session writes these rows, because the actions being
recorded are the ones their portal starts: an assessment invitation, a report.
So the WITH CHECK admits a tenant-scoped insert attributed to that same
tenant, rather than being bypass-only.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin

# ── The state machine, named once ────────────────────────────────────────────
# Uppercase, matching `workers/status.py`'s run states, because an operator
# reads both in the same breath and a lowercase third vocabulary would be one
# more thing to translate.

#: Requested and recorded. Nothing has been called.
ACTION_PENDING = "PENDING"
#: The adapter has been invoked and has not answered yet.
ACTION_RUNNING = "RUNNING"
#: The external effect happened, and we know it.
ACTION_SUCCEEDED = "SUCCEEDED"
#: The attempt DEFINITELY had no effect. This is the only failure that is safe
#: to retry, and that is the whole reason it is distinct from UNKNOWN.
ACTION_FAILED = "FAILED"
#: The attempt MAY have had an effect. Resolved by reading back, never retried.
ACTION_UNKNOWN = "UNKNOWN"
#: A succeeded effect that was afterwards compensated. Terminal: the effect
#: happened and was undone, so re-running this key would not be a retry, it
#: would be a second effect.
ACTION_ROLLED_BACK = "ROLLED_BACK"

ACTION_STATES: tuple[str, ...] = (
    ACTION_PENDING,
    ACTION_RUNNING,
    ACTION_SUCCEEDED,
    ACTION_FAILED,
    ACTION_UNKNOWN,
    ACTION_ROLLED_BACK,
)

# ── Risk, which decides whether the adapter is reachable at all ──────────────

#: The effect can be undone by the product without anyone outside noticing.
RISK_REVERSIBLE = "reversible"
#: Somebody outside the product experiences the effect immediately and cannot
#: experience it being undone: an email that left, a report that was delivered.
#: Gated at the adapter until a commit point is stamped.
RISK_IRREVERSIBLE = "irreversible"

RISK_CLASSES: tuple[str, ...] = (RISK_REVERSIBLE, RISK_IRREVERSIBLE)

# ── How the action came to be allowed ────────────────────────────────────────

#: Allowed by standing policy. Reversible actions only.
AUTHORIZATION_POLICY = "policy"
#: A person decided. The only authorization an irreversible action accepts, and
#: it must carry the approval it rests on.
AUTHORIZATION_HUMAN = "human_approval"

AUTHORIZATIONS: tuple[str, ...] = (AUTHORIZATION_POLICY, AUTHORIZATION_HUMAN)


class AgentAction(Base, UUIDPKMixin):
    """One side-effecting action, recorded before it is attempted."""

    __tablename__ = "agent_actions"
    __table_args__ = (
        # The read-back sweep asks for every action whose outcome is not known,
        # oldest first. Without this it is a sequential scan over every action
        # the platform has ever taken, run on a schedule.
        Index("ix_agent_actions_unresolved", "state", "requested_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    #: Which agent asked. `services/tools/permissions.py` names them.
    agent: Mapped[str] = mapped_column(String(50), nullable=False)
    #: Which tool or adapter performs the effect.
    tool: Mapped[str] = mapped_column(String(100), nullable=False)
    #: Derived from STABLE LOGICAL INPUTS. See `agent_actions/idempotency.py`.
    idempotency_key: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True
    )
    #: A digest of the arguments the effect was requested with. Not for dedupe
    #: (the key does that) but to catch a key REUSED for different arguments,
    #: which is a caller bug that would otherwise read as a successful no-op.
    args_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_class: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: Nullable because a scheduled sweep has no user, and ON DELETE SET NULL
    #: for the same reason `jobs.created_by` is: an action already taken is not
    #: undone by the requester's account being removed.
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    authorization: Mapped[str] = mapped_column(String(30), nullable=False)
    #: The approval this action rests on. Deliberately carries no foreign key:
    #: the approval RECORD belongs to the policy layer, and a foreign key here
    #: would make this ledger unwritable whenever that layer is mid-change.
    approval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=ACTION_PENDING
    )
    #: The provider's own identifier for the effect: a message id, an object
    #: key, a row id. What a read-back looks for.
    external_id: Mapped[str | None] = mapped_column(String(200))
    #: How many times the adapter has been invoked for this action.
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def outcome_is_known(self) -> bool:
        """Whether the world state this row describes has been established.

        PENDING is known (nothing was called). RUNNING is NOT: a process that
        died between the invoke and the answer leaves exactly that row, and
        treating it as safe to retry is the duplicate this table exists to
        prevent.
        """
        return self.state not in (ACTION_RUNNING, ACTION_UNKNOWN)
