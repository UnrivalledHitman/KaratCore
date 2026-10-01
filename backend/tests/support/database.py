"""Test database lifecycle: a fresh database per test session, real runtime roles.

``TEST_DATABASE_URL`` must point at a Postgres superuser connection (the
``postgres`` maintenance database is fine). The suite creates and drops its
own ``karatcore_test`` database and connects to it as karatcore_app and
karatcore_service exactly like production does.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Engine, create_engine, make_url, text
from sqlalchemy.pool import NullPool

from app.db.database import APP_ROLE, SERVICE_ROLE, Database

BACKEND_DIR = Path(__file__).resolve().parents[2]
ADMIN_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
)
TEST_DATABASE_NAME = "karatcore_test"
TEST_ROLE_PASSWORDS = {APP_ROLE: "app-role-test-password", SERVICE_ROLE: "service-role-test-password"}
PLATFORM_TABLES = ("tenants", "memberships", "webhook_events")


@dataclass(frozen=True)
class DatabaseHandles:
    owner_engine: Engine
    app: Database
    service: Database


def url_to_string(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def recreate_database(admin_url: URL, name: str) -> None:
    engine = create_engine(admin_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        engine.dispose()


def drop_database(admin_url: URL, name: str) -> None:
    engine = create_engine(admin_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        engine.dispose()


def alembic_config(owner_url: URL) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["database_url"] = url_to_string(owner_url)
    config.attributes["configure_logger"] = False
    return config


def migrate(owner_url: URL, revision: str = "head") -> None:
    command.upgrade(alembic_config(owner_url), revision)


def enable_role_logins(owner_engine: Engine) -> None:
    with owner_engine.begin() as connection:
        for role, password in TEST_ROLE_PASSWORDS.items():
            connection.execute(text(f"ALTER ROLE {role} WITH LOGIN PASSWORD '{password}'"))


def role_url(owner_url: URL, role: str) -> URL:
    return owner_url.set(username=role, password=TEST_ROLE_PASSWORDS[role])


def open_database_handles() -> DatabaseHandles:
    admin_url = make_url(ADMIN_DATABASE_URL)
    recreate_database(admin_url, TEST_DATABASE_NAME)
    owner_url = admin_url.set(database=TEST_DATABASE_NAME)
    migrate(owner_url)
    owner_engine = create_engine(owner_url, poolclass=NullPool)
    enable_role_logins(owner_engine)
    return DatabaseHandles(
        owner_engine=owner_engine,
        app=Database.from_url(role_url(owner_url, APP_ROLE), APP_ROLE),
        service=Database.from_url(role_url(owner_url, SERVICE_ROLE), SERVICE_ROLE),
    )


def close_database_handles(handles: DatabaseHandles) -> None:
    handles.app.dispose()
    handles.service.dispose()
    handles.owner_engine.dispose()
    drop_database(make_url(ADMIN_DATABASE_URL), TEST_DATABASE_NAME)


def truncate_platform_tables(owner_engine: Engine) -> None:
    with owner_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {', '.join(PLATFORM_TABLES)}"))
