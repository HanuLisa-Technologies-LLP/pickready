"""Alembic environment — async engine (asyncpg), metadata from app.models.

The URL always comes from app settings (DATABASE_URL), never from alembic.ini,
so migrations run identically in docker/CI/prod. Base.metadata is imported so
future `alembic revision --autogenerate` sees every model.
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.models import Base  # noqa: F401 — imports every model into metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# `%` IS DOUBLED, and this is not cosmetic.
#
# `set_main_option` writes into a ConfigParser, whose default interpolation
# reads `%` as the start of a `%(name)s` reference. A DSN is a URL and a URL
# percent-encodes, so any password containing a character that needs encoding
# arrives here as `%2A` or `%7C` and ConfigParser raises
#
#   ValueError: invalid interpolation syntax ... at position 51
#
# before a single migration runs. RDS generates passwords from a character set
# that includes several such characters, so this is the normal case rather than
# an unlucky one: it took down the first migration of the pilot environment.
#
# Doubling is ConfigParser's own escape. SQLAlchemy receives the single `%`
# back, so the DSN it connects with is unchanged.
config.set_main_option(
    "sqlalchemy.url", get_settings().database_url.replace("%", "%%")
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a DB connection."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        # Migrations are a trusted, tenant-agnostic maintenance context — the
        # same standing the app already grants Celery workers and the Super
        # Admin console — so they reach through the SAME explicit escape hatch
        # those paths use (`core/db.superadmin_scope`): app.bypass_rls = 'on'.
        #
        # Without it, every data migration that touches a tenant-scoped table
        # breaks the moment the connection role is not a superuser. In dev the
        # docker role IS a superuser (superusers bypass RLS outright, and FORCE
        # only covers the owner-but-not-superuser case), so this stayed hidden
        # until the first Cloud SQL deploy, where the app role is neither and
        # the policies finally bite. It fails two ways, and the quiet one is
        # worse: an INSERT is refused LOUDLY (0005 hiring_managers), but an
        # UPDATE simply matches zero rows and reports success — which is how
        # 0014 and 0018 came to backfill nothing and then fail a SET NOT NULL
        # against the very rows they were supposed to fix.
        #
        # This changes NOTHING about runtime enforcement: it is scoped to this
        # one migration connection, and no policy is dropped or disabled. Its
        # real value is making dev and production agree, so a migration that
        # passes locally means something.
        #
        # The sentinel tenant id is pinned for the same reason superadmin_scope
        # pins it — the policies' `app.tenant_id::uuid` cast must stay
        # well-defined, and an unset custom GUC can read back as '' rather than
        # NULL, where ''::uuid raises.
        connection.exec_driver_sql(
            "SELECT set_config('app.tenant_id',"
            " '00000000-0000-0000-0000-000000000000', false)"
        )
        connection.exec_driver_sql(
            "SELECT set_config('app.bypass_rls', 'on', false)"
        )
        context.run_migrations()


async def _run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
