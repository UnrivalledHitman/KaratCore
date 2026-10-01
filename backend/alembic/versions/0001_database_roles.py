"""Create the runtime roles karatcore_app and karatcore_service.

Both are created NOLOGIN with no privileges beyond what later migrations grant.
Operators enable LOGIN and set a password once per environment (see
backend/README.md). The roles must be created by SQL, never through the Neon
Console/API: Console-created roles join neon_superuser, which has BYPASSRLS.

Revision ID: 0001
Revises:
"""

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'karatcore_app') THEN
                CREATE ROLE karatcore_app
                    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
            END IF;
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'karatcore_service') THEN
                CREATE ROLE karatcore_service
                    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
            END IF;
        END
        $$
        """
    )
    op.execute("ALTER ROLE karatcore_app SET statement_timeout = '15s'")
    op.execute("ALTER ROLE karatcore_app SET idle_in_transaction_session_timeout = '60s'")
    op.execute("ALTER ROLE karatcore_service SET statement_timeout = '15s'")
    op.execute("ALTER ROLE karatcore_service SET idle_in_transaction_session_timeout = '60s'")
    op.execute("GRANT USAGE ON SCHEMA public TO karatcore_app, karatcore_service")


def downgrade() -> None:
    # Roles are cluster-wide and may hold privileges in other databases on the
    # same cluster, so downgrade only revokes what upgrade granted here.
    op.execute("REVOKE USAGE ON SCHEMA public FROM karatcore_app, karatcore_service")
