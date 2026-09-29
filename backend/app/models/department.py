"""A tenant's departments (the leadership release, 2026-09-29, spec 13).

WHY A TABLE AND NOT THE STRING THE PRODUCT ALREADY HAD
------------------------------------------------------
`jobs.department` was free text, and free text is not a security boundary: a
Functional Head of "Engineering" must not lose a job typed "engineering " or
gain one typed "Engineering & Finance". A department is now a ROW, a job
names it by `jobs.department_id` and a Functional Head by
`users.department_id`, and `services/department_access` compares ids, never
strings.

`normalized_name` is the key a name is matched on (`departments.normalize`:
case folded, whitespace collapsed), UNIQUE per tenant, so "Engineering" and
" engineering" are one department and a second spelling can never mint a
second boundary. `name` keeps the spelling a person chose.

`is_active` retires a department from the pickers without deleting it: a job
and a Functional Head that name a retired department keep naming it, because
deleting the row would either orphan the job (a Functional Head silently
losing it) or cascade it away.

Composite UNIQUE (id, tenant_id) exists for the two same-tenant foreign keys
migration 0133 declares from `jobs` and `users`: a job or a person can only
ever name a department of their OWN tenant, enforced by the database rather
than by a handler that remembers to check.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin


class CompanyDepartment(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "company_departments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "normalized_name", name="uq_company_departments_tenant_name"
        ),
        UniqueConstraint("id", "tenant_id", name="uq_company_departments_id_tenant"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
