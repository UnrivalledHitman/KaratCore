from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Membership(Base):
    """A Clerk organization membership plus KaratCore's accountant/staff sub-role.

    ``removed_at`` is a tombstone; ``clerk_synced_at`` is the last-write-wins
    clock (Clerk event timestamp or session-token ``iat``) of the newest Clerk
    observation applied to this row.
    """

    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint("clerk_role IN ('admin', 'member')", name="ck_memberships_clerk_role"),
        CheckConstraint("sub_role IN ('accountant', 'staff')", name="ck_memberships_sub_role"),
    )

    tenant_id: Mapped[str] = mapped_column(
        Text, ForeignKey("tenants.id", name="fk_memberships_tenant_id"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    clerk_role: Mapped[str] = mapped_column(Text)
    sub_role: Mapped[str] = mapped_column(Text, server_default="staff")
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clerk_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
