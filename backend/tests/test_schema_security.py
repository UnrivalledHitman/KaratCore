"""Database-level security invariants. These tests also guard future migrations."""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, make_url, text

from app.db.database import APP_ROLE, SERVICE_ROLE, Database, UnsafeDatabaseRoleError
from app.models import metadata
from tests.support.database import (
    ADMIN_DATABASE_URL,
    PLATFORM_TABLES,
    DatabaseHandles,
    alembic_config,
    drop_database,
    migrate,
    recreate_database,
)

TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
TENANT_SCOPED_TABLES = {"tenants", "memberships"}

# (role, table) -> table-level privileges that must be granted; everything else must not be.
EXPECTED_TABLE_PRIVILEGES: dict[tuple[str, str], set[str]] = {
    (APP_ROLE, "tenants"): {"SELECT"},
    (APP_ROLE, "memberships"): {"SELECT"},
    (APP_ROLE, "webhook_events"): set(),
    (SERVICE_ROLE, "tenants"): {"SELECT", "INSERT", "UPDATE"},
    (SERVICE_ROLE, "memberships"): {"SELECT", "INSERT", "UPDATE"},
    (SERVICE_ROLE, "webhook_events"): {"SELECT", "INSERT", "UPDATE"},
}

PROBE_ROLE = "karatcore_probe"
PROBE_PASSWORD = "probe-role-test-password"

# Each setup turns the probe role into something that could bypass row-level security.
UNSAFE_ROLE_SETUPS: dict[str, list[str]] = {
    "BYPASSRLS": [f"CREATE ROLE {PROBE_ROLE} LOGIN PASSWORD '{PROBE_PASSWORD}' BYPASSRLS"],
    "member of other roles": [
        f"CREATE ROLE {PROBE_ROLE} LOGIN PASSWORD '{PROBE_PASSWORD}'",
        f"GRANT {SERVICE_ROLE} TO {PROBE_ROLE}",
    ],
    "owns relations": [
        f"CREATE ROLE {PROBE_ROLE} LOGIN PASSWORD '{PROBE_PASSWORD}'",
        "CREATE TABLE probe_owned (id integer)",
        f"ALTER TABLE probe_owned OWNER TO {PROBE_ROLE}",
    ],
}


def _drop_probe_role(owner_engine: Engine) -> None:
    with owner_engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS probe_owned"))
        connection.execute(text(f"DROP ROLE IF EXISTS {PROBE_ROLE}"))


@pytest.fixture()
def probe_role(db: DatabaseHandles) -> Iterator[None]:
    _drop_probe_role(db.owner_engine)
    try:
        yield
    finally:
        _drop_probe_role(db.owner_engine)


def test_runtime_roles_pass_the_safety_check(db: DatabaseHandles) -> None:
    db.app.verify_role()
    db.service.verify_role()


def test_superuser_connection_is_rejected(db: DatabaseHandles) -> None:
    superuser = Database(db.owner_engine, expected_role=str(db.owner_engine.url.username))

    with pytest.raises(UnsafeDatabaseRoleError, match="superuser"):
        superuser.verify_role()


def test_role_mismatch_is_rejected(db: DatabaseHandles) -> None:
    mislabelled = Database(db.app.engine, expected_role=SERVICE_ROLE)

    with pytest.raises(UnsafeDatabaseRoleError, match="expected 'karatcore_service'"):
        mislabelled.verify_role()


@pytest.mark.usefixtures("probe_role")
@pytest.mark.parametrize("problem", sorted(UNSAFE_ROLE_SETUPS))
def test_role_that_could_bypass_rls_is_rejected(db: DatabaseHandles, problem: str) -> None:
    with db.owner_engine.begin() as connection:
        for statement in UNSAFE_ROLE_SETUPS[problem]:
            connection.execute(text(statement))
    probe = Database.from_url(db.owner_engine.url.set(username=PROBE_ROLE, password=PROBE_PASSWORD), PROBE_ROLE)

    try:
        with pytest.raises(UnsafeDatabaseRoleError, match=problem):
            probe.verify_role()
    finally:
        probe.dispose()


def test_every_table_forces_row_level_security(db: DatabaseHandles) -> None:
    with db.owner_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                       (SELECT count(*) FROM pg_catalog.pg_policy p WHERE p.polrelid = c.oid) AS policies
                FROM pg_catalog.pg_class c
                WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
                  AND c.relname <> 'alembic_version'
                """
            )
        ).all()

    assert {row[0] for row in rows} >= set(PLATFORM_TABLES)
    for name, enabled, forced, policies in rows:
        assert enabled, f"{name}: row level security not enabled"
        assert forced, f"{name}: row level security not forced"
        assert policies > 0, f"{name}: no policies"


def test_tenant_isolation_policies_check_reads_and_writes(db: DatabaseHandles) -> None:
    with db.owner_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT tablename, cmd, qual, with_check FROM pg_catalog.pg_policies
                WHERE schemaname = 'public' AND policyname = 'tenant_isolation' AND roles = '{karatcore_app}'
                """
            )
        ).all()

    assert {row[0] for row in rows} == TENANT_SCOPED_TABLES
    for table, cmd, qual, with_check in rows:
        assert cmd == "ALL", table
        assert "app.tenant_id" in qual, table
        assert with_check == qual, table


def test_app_role_is_covered_only_by_tenant_isolation(db: DatabaseHandles) -> None:
    with db.owner_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT tablename, policyname FROM pg_catalog.pg_policies
                WHERE schemaname = 'public' AND policyname <> 'tenant_isolation'
                  AND (roles && ARRAY['karatcore_app', 'public']::name[])
                """
            )
        ).all()

    assert rows == []


@pytest.mark.parametrize(("role", "table"), sorted(EXPECTED_TABLE_PRIVILEGES))
def test_table_privileges_are_least_privilege(db: DatabaseHandles, role: str, table: str) -> None:
    with db.owner_engine.connect() as connection:
        granted = {
            privilege
            for privilege in TABLE_PRIVILEGES
            if connection.execute(
                text("SELECT has_table_privilege(:role, :table, :privilege)"),
                {"role": role, "table": f"public.{table}", "privilege": privilege},
            ).scalar_one()
        }

    assert granted == EXPECTED_TABLE_PRIVILEGES[(role, table)]


def test_privilege_matrix_covers_every_table(db: DatabaseHandles) -> None:
    assert {table for _, table in EXPECTED_TABLE_PRIVILEGES} == set(PLATFORM_TABLES)


def test_app_role_can_update_only_the_sub_role_columns(db: DatabaseHandles) -> None:
    with db.owner_engine.connect() as connection:
        columns = (
            connection.execute(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'memberships'
                    """
                )
            )
            .scalars()
            .all()
        )
        updatable = {
            column
            for column in columns
            if connection.execute(
                text("SELECT has_column_privilege(:role, 'public.memberships', :column, 'UPDATE')"),
                {"role": APP_ROLE, "column": column},
            ).scalar_one()
        }

    assert updatable == {"sub_role", "updated_at"}


def test_service_role_has_no_privileges_outside_platform_tables(db: DatabaseHandles) -> None:
    with db.owner_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT c.relname FROM pg_catalog.pg_class c
                WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p', 'v', 'm')
                  AND c.relname <> ALL(:platform_tables)
                  AND (has_table_privilege(:role, c.oid, 'SELECT') OR has_table_privilege(:role, c.oid, 'INSERT')
                       OR has_table_privilege(:role, c.oid, 'UPDATE') OR has_table_privilege(:role, c.oid, 'DELETE'))
                """
            ),
            {"platform_tables": list(PLATFORM_TABLES), "role": SERVICE_ROLE},
        ).all()

    assert rows == []


def test_models_match_migrations(db: DatabaseHandles) -> None:
    with db.owner_engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), metadata)

    assert differences == []


def test_migrations_downgrade_and_upgrade_cleanly() -> None:
    admin_url = make_url(ADMIN_DATABASE_URL)
    name = "karatcore_migration_roundtrip"
    recreate_database(admin_url, name)
    owner_url = admin_url.set(database=name)
    try:
        migrate(owner_url)
        command.downgrade(alembic_config(owner_url), "base")
        migrate(owner_url)
    finally:
        drop_database(admin_url, name)
