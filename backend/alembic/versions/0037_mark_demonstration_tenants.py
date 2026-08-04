"""Mark the permanent demonstration tenants, and never bill them.

WHY A COLUMN AND NOT A LIST OF UUIDS IN PYTHON
----------------------------------------------
Sarkar Corp, ACRM Corp and Specter & Co. are permanent demo companies. They must
behave as fully paid customers forever: unlimited credits, never gated, never
asked to pay, while billing keeps working normally for every real customer.

The obvious shortcut is a frozenset of three UUIDs in `services/credits`. This
schema deliberately keeps policy as DATA (permissions are rows, routing is a
table in config/llm_providers), and billing exemption is exactly that kind of
policy. A column also means the Provider can see WHICH tenants are exempt by
looking at the table, rather than by reading a service module, and a fourth demo
tenant later is an UPDATE rather than a release.

NAMES ARE NOT THE KEY, IDS ARE
------------------------------
The brief for this change named the third company "ACME Corp". Production has no
ACME Corp: the tenants are Sarkar Corp, ACRM Corp and Specter & Co., and there is
a fourth, Workify Corp, on a random UUID that is NOT a demo tenant and must keep
being billed normally. Matching on a name would therefore have missed the
intended tenant and, on a close-enough name, could have exempted a paying one.
The three seed UUIDs are deterministic and unambiguous, so they are the key.

WHAT `is_demo` DOES AND DOES NOT CHANGE
---------------------------------------
It does NOT stop the ledger recording usage. A demo that shows an empty billing
page proves nothing, and the requirement is that the billing UI still exists and
the logic still works. Entries are still written, so consumption is visible and
a statement still adds up.

It changes the two places that can REFUSE or ALARM:
  * `has_credit_headroom` is unconditionally true, so invitations never 402.
  * `credit_deficit` is never set, so no dunning path and no deficit banner.
Display treats the balance as unlimited rather than as whatever the ledger sums
to, which for a demo tenant that has run assessments would otherwise be a
negative number on a page that is supposed to read "fully paid".

REVERSIBLE
----------
Downgrade clears the flag and drops the column. Nothing else depends on it, and
the ledger rows it never touched are unaffected.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0037_mark_demo_tenants"
down_revision = "0036_seed_mock_company_profiles"
branch_labels = None
depends_on = None


#: Deterministic seed UUIDs. Sarkar Corp, ACRM Corp, Specter & Co.
DEMO_TENANT_IDS: tuple[str, ...] = (
    "10000000-0000-4000-8000-000000000001",
    "10000000-0000-4000-8000-000000000002",
    "10000000-0000-4000-8000-000000000003",
)


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "is_demo",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Explicit id list. A LIKE on the seed-UUID prefix would also match any
    # future 1000...-shaped id, and silently exempting a tenant from billing is
    # not a mistake that announces itself.
    op.execute(
        sa.text(
            "UPDATE tenants SET is_demo = true WHERE id IN ("
            + ", ".join(f"'{tid}'" for tid in DEMO_TENANT_IDS)
            + ")"
        )
    )


def downgrade() -> None:
    op.drop_column("tenants", "is_demo")
