"""The one part of the tool policy that is DATA: a tenant asking for more.

WHY THIS IS A TABLE WHEN THE REST IS A PYTHON CONSTANT
-------------------------------------------------------
Risk class and agent capability are constants for the Layer 1 reason: a table
has an UPDATE, an UPDATE eventually gets an admin screen, and an admin screen
makes tool permissions client editable, at which point the policy is
decorative. A customer wanting a human to approve something the platform would
otherwise run automatically is a different question. It is a property of that
customer's own risk appetite, it changes without a deploy, and it can only ever
NARROW what the platform does.

THE SHAPE IS WHAT MAKES IT SAFE, NOT THE VALIDATION
----------------------------------------------------
A row's EXISTENCE is the requirement. There is no `requires_approval` boolean,
because a boolean has a False, and a False row would be a tenant switching OFF
a requirement the platform declared -- which would make the Python constant
decorative through the back door. `agent_tool_approval_rules` has no column
that can express "less", so no migration, no admin screen and no support
override can ever write it. Same argument `hiring/layers.py` makes about
INVARIANTS: a lower layer may tune within declared bounds and may never
suspend.

A TENANT WITH NO ROWS IS THE DEFAULT, NOT A GAP
------------------------------------------------
An empty result means the platform baseline applies, exactly as an absent
`role_permissions` tenant row means the global template applies. The empty
state is the documented answer rather than a missing one.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.tools.policy import RiskClass

#: The vocabulary the table's CHECK constraint pins, restated here so a row
#: carrying something else is visible as a defect rather than silently matching
#: nothing.
KNOWN_RISK_CLASSES: frozenset[str] = frozenset(risk.value for risk in RiskClass)


async def required_risk_classes(
    session: AsyncSession, tenant_id: uuid.UUID | str | None
) -> frozenset[str]:
    """Risk classes this tenant requires a human to approve, beyond the baseline.

    Raises rather than degrading. A policy input that could not be read is not
    a policy decision: returning an empty set on a failed read would run a call
    automatically that the customer had explicitly asked to hold, and would
    leave nothing anywhere saying so. The executor lets the failure become a
    tool failure, which is the loud direction.

    The tenant filter is written out even though every caller uses the
    RLS-aware session, because app level filtering is defence in depth and the
    policy is not the place to rely on exactly one of the two.
    """
    if tenant_id is None:
        return frozenset()
    rows = await session.execute(
        text(
            "SELECT risk_class FROM agent_tool_approval_rules "
            "WHERE tenant_id = :tenant_id"
        ),
        {"tenant_id": str(tenant_id)},
    )
    return frozenset(str(row[0]) for row in rows)
