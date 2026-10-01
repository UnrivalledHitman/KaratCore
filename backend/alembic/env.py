"""Alembic environment. Migrations always run as the schema-owner role.

The connection URL comes from ``config.attributes["database_url"]`` when a
caller (the test suite) provides one, otherwise from the
``MIGRATION_DATABASE_URL`` environment variable. It is deliberately not read
from ``Settings``: the deployed API never has the owner credentials.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.models import metadata as target_metadata

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)


def _database_url() -> str:
    configured = config.attributes.get("database_url")
    if isinstance(configured, str):
        return configured
    url = os.environ.get("MIGRATION_DATABASE_URL")
    if not url:
        raise RuntimeError("Set MIGRATION_DATABASE_URL to the schema-owner connection string")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
