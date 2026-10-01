"""Engines and sessions for the two runtime Postgres roles, plus tenant binding.

Every runtime connection uses one of two dedicated roles (see
alembic/versions/0001_database_roles.py):

- ``karatcore_app``: user-facing API requests. RLS restricts it to the tenant
  bound on the session via :func:`bind_tenant`.
- ``karatcore_service``: webhooks and cron. It has no tenant restriction on the
  platform tables and no grants on anything else.

Tenant binding uses ``set_config('app.tenant_id', ..., is_local => true)``, which
lasts for one transaction only. It is re-applied at the start of every
transaction by an ``after_begin`` listener, so a ``commit()`` in the middle of
a request never silently drops the tenant context. Transaction-local settings
are also what makes this safe behind PgBouncer in transaction-pooling mode
(Neon's pooled endpoint).
"""

import threading

from sqlalchemy import URL, Connection, Engine, create_engine, event, text
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker
from sqlalchemy.pool import NullPool

APP_ROLE = "karatcore_app"
SERVICE_ROLE = "karatcore_service"

_TENANT_INFO_KEY = "karatcore.tenant_id"

_ROLE_SAFETY_QUERY = text(
    """
    SELECT
        r.rolname,
        r.rolsuper,
        r.rolbypassrls,
        EXISTS (
            SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member = r.oid
        ) AS is_member_of_other_roles,
        EXISTS (
            SELECT 1 FROM pg_catalog.pg_class c
            WHERE c.relowner = r.oid AND c.relnamespace = 'public'::regnamespace
        ) AS owns_relations
    FROM pg_catalog.pg_roles r
    WHERE r.rolname = current_user
    """
)


class UnsafeDatabaseRoleError(RuntimeError):
    """The connection's role could bypass row-level security. Refuse to serve traffic."""


class TenantBindingError(RuntimeError):
    """A session was bound to an empty tenant id or rebound to a different tenant."""


class Database:
    """One engine connected as one runtime role, handing out ORM sessions."""

    def __init__(self, engine: Engine, expected_role: str) -> None:
        self._engine = engine
        self._expected_role = expected_role
        self._sessionmaker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        event.listen(self._sessionmaker, "after_begin", _apply_bound_tenant)
        self._role_verified = False
        self._verify_lock = threading.Lock()

    @classmethod
    def from_url(cls, url: str | URL, expected_role: str) -> "Database":
        # NullPool: serverless instances must not each hold a pool of idle
        # connections; Neon's pooler does the pooling.
        # prepare_threshold=None: no server-side prepared statements, which
        # transaction-mode PgBouncer cannot track across server connections.
        engine = create_engine(
            url,
            poolclass=NullPool,
            connect_args={"prepare_threshold": None, "connect_timeout": 10},
        )
        return cls(engine, expected_role)

    @property
    def engine(self) -> Engine:
        return self._engine

    def session(self) -> Session:
        self.verify_role()
        return self._sessionmaker()

    def verify_role(self) -> None:
        """Fail closed unless the connected role is exactly the expected, unprivileged role."""
        if self._role_verified:
            return
        with self._verify_lock:
            if self._role_verified:
                return
            with self._engine.connect() as connection:
                row = connection.execute(_ROLE_SAFETY_QUERY).one()
            role_name: str = row[0]
            problems: list[str] = []
            if role_name != self._expected_role:
                problems.append(f"connected as {role_name!r}, expected {self._expected_role!r}")
            if row[1]:
                problems.append("role is a superuser")
            if row[2]:
                problems.append("role has BYPASSRLS")
            if row[3]:
                problems.append("role is a member of other roles")
            if row[4]:
                problems.append("role owns relations in schema public")
            if problems:
                raise UnsafeDatabaseRoleError("; ".join(problems))
            self._role_verified = True

    def dispose(self) -> None:
        self._engine.dispose()


def bind_tenant(session: Session, tenant_id: str) -> None:
    """Scope every current and future transaction of ``session`` to ``tenant_id``."""
    if not tenant_id:
        raise TenantBindingError("tenant_id must be a non-empty string")
    bound = session.info.get(_TENANT_INFO_KEY)
    if bound is not None and bound != tenant_id:
        raise TenantBindingError("session is already bound to a different tenant")
    session.info[_TENANT_INFO_KEY] = tenant_id
    if session.in_transaction():
        _set_tenant_setting(session.connection(), tenant_id)


def _apply_bound_tenant(session: Session, transaction: SessionTransaction, connection: Connection) -> None:
    tenant_id = session.info.get(_TENANT_INFO_KEY)
    if isinstance(tenant_id, str):
        _set_tenant_setting(connection, tenant_id)


def _set_tenant_setting(connection: Connection, tenant_id: str) -> None:
    connection.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": tenant_id})
