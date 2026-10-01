"""Seed rows directly as the schema owner (bypasses RLS) so tests control exact state."""

from datetime import UTC, datetime

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.models import Membership, Tenant

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def seed_tenant(
    owner_engine: Engine,
    tenant_id: str,
    *,
    name: str = "Test Jewellers",
    slug: str | None = None,
    deleted_at: datetime | None = None,
    clerk_synced_at: datetime | None = None,
) -> None:
    with Session(owner_engine) as session:
        session.add(
            Tenant(id=tenant_id, name=name, slug=slug, deleted_at=deleted_at, clerk_synced_at=clerk_synced_at)
        )
        session.commit()


def seed_membership(
    owner_engine: Engine,
    tenant_id: str,
    user_id: str,
    *,
    clerk_role: str = "member",
    sub_role: str = "staff",
    removed_at: datetime | None = None,
    clerk_synced_at: datetime = EPOCH,
) -> None:
    with Session(owner_engine) as session:
        session.add(
            Membership(
                tenant_id=tenant_id,
                user_id=user_id,
                clerk_role=clerk_role,
                sub_role=sub_role,
                removed_at=removed_at,
                clerk_synced_at=clerk_synced_at,
            )
        )
        session.commit()
