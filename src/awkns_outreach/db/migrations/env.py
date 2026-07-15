"""Alembic environment. Pulls the URL from the app settings and targets the
ORM metadata so `alembic revision --autogenerate` works against Postgres."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from awkns_outreach.config import settings
from awkns_outreach.db.session import Base
from awkns_outreach.db import models  # noqa: F401  (register tables on Base)

config = context.config
# configparser (used internally by alembic.config.Config) treats "%" as the
# start of a %(name)s interpolation token, so a literal "%" in the URL (e.g.
# from a percent-encoded password) must be escaped as "%%" before it's stored.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
