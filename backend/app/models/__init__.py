"""Importing this package registers every model on ``Base.metadata``."""

from app.db.base import Base
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.webhook_event import WebhookEvent

metadata = Base.metadata

__all__ = ["Membership", "Tenant", "WebhookEvent", "metadata"]
