"""Platform tables with forced row-level security and least-privilege grants.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None

TABLES = ("tenants", "memberships", "webhook_events")


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clerk_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "memberships",
        sa.Column(
            "tenant_id",
            sa.Text(),
            sa.ForeignKey("tenants.id", name="fk_memberships_tenant_id"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Text(), primary_key=True),
        sa.Column("clerk_role", sa.Text(), nullable=False),
        sa.Column("sub_role", sa.Text(), nullable=False, server_default="staff"),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clerk_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("clerk_role IN ('admin', 'member')", name="ck_memberships_clerk_role"),
        sa.CheckConstraint("sub_role IN ('accountant', 'staff')", name="ck_memberships_sub_role"),
    )
    op.create_table(
        "webhook_events",
        sa.Column("source", sa.Text(), primary_key=True),
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("source IN ('clerk')", name="ck_webhook_events_source"),
    )

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE: the table owner is subject to the policies too.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY service_access ON {table} FOR ALL TO karatcore_service USING (true) WITH CHECK (true)"
        )

    op.execute(
        "CREATE POLICY tenant_isolation ON tenants FOR ALL TO karatcore_app "
        "USING (id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (id = current_setting('app.tenant_id', true))"
    )
    op.execute(
        "CREATE POLICY tenant_isolation ON memberships FOR ALL TO karatcore_app "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )

    # karatcore_app: read its own tenant's platform rows; the only write is an
    # owner changing a member's accountant/staff sub-role.
    op.execute("GRANT SELECT ON tenants, memberships TO karatcore_app")
    op.execute("GRANT UPDATE (sub_role, updated_at) ON memberships TO karatcore_app")
    # karatcore_service: webhooks and provisioning. No DELETE: tenants and
    # memberships are tombstoned, never removed.
    op.execute("GRANT SELECT, INSERT, UPDATE ON tenants, memberships, webhook_events TO karatcore_service")


def downgrade() -> None:
    op.drop_table("webhook_events")
    op.drop_table("memberships")
    op.drop_table("tenants")
