# Platform Foundation — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the FastAPI backend for KaratCore's platform foundation — multi-tenant data model with Postgres RLS isolation, Clerk-based auth/roles, and Stripe-based subscription billing — so sub-project #2 (core ERP) can be built directly on top of it.

**Architecture:** A single FastAPI app organized as models (SQLAlchemy) → repositories (data access) → services (business logic) → routers (thin HTTP layer), with two Postgres session modes: tenant-scoped (RLS-enforced, used by regular API requests) and service-scoped (used only by webhook/onboarding code, which is authenticated by signature verification rather than a user's tenant membership).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Postgres (Neon), PyJWT (Clerk JWT verification), svix (Clerk webhook signature verification), Stripe SDK, pytest, pyright.

**Spec:** `docs/superpowers/specs/2026-09-14-platform-foundation-design.md`

## Global Constraints

- Full type hints throughout; `pyright` must pass with zero errors before any task is considered done.
- SQLAlchemy models, Pydantic schemas, a repository class per entity, and a service class per business-logic area — routers stay thin and only call services (spec: Code structure & type safety).
- Tenant isolation is enforced by **Postgres RLS**, not just application-level filtering (spec: Tenant isolation).
- Access-control failure modes are exact: no/invalid session → `401`; valid session, role lacks permission → `403`; valid session, resource belongs to another tenant → `404` (spec: Access control).
- Clerk and Stripe webhooks are signature-verified, idempotent, and log (never silently swallow) failures (spec: Webhook handling).
- One git branch per task/feature, merged to `main` via a PR with a description of what changed and why — never commit directly to `main` (per user instruction, see plan footer).
- Tests that touch the database run against a **real Postgres** instance (RLS behavior is a database-level guarantee and must be verified as one, not mocked). Every task below assumes a `TEST_DATABASE_URL` environment variable is set, e.g.:
  ```bash
  docker run --rm -d --name karatcore-test-db -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
  export TEST_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
  ```

---

## File Structure

```
backend/
  pyproject.toml              # deps + [tool.pyright] config
  alembic.ini
  api/
    index.py                  # Vercel ASGI entrypoint: re-exports app.main.app
  app/
    __init__.py
    main.py                   # create_app(), health check
    config.py                 # Settings (pydantic-settings), get_settings()
    db/
      base.py                 # declarative Base
      session.py               # engine, SessionLocal, get_db, set_tenant_context, set_service_context
    models/
      tenant.py
      membership.py
      subscription.py
      webhook_event.py
    schemas/
      tenant.py
      membership.py
      subscription.py
    repositories/
      base.py                  # BaseRepository[T]
      tenants.py
      memberships.py
      subscriptions.py
      webhook_events.py
    auth/
      clerk_jwt.py              # ClerkClaims, ClerkJWKSClient, verify_clerk_jwt, InvalidTokenError
      dependencies.py           # get_current_claims, TenantContext, get_tenant_context, require_role
    integrations/
      stripe_client.py          # StripeClient
    services/
      onboarding.py             # OnboardingService
      membership_sync.py        # MembershipSyncService
      billing.py                # BillingService (Stripe webhook side)
    routers/
      health.py
      webhooks_clerk.py
      webhooks_stripe.py
      billing.py
  alembic/
    env.py
    script.py.mako
    versions/
      0001_create_tenants.py
      0002_create_memberships.py
      0003_create_subscriptions.py
      0004_enable_rls.py
      0005_create_webhook_events.py
  tests/
    conftest.py
    test_health.py
    test_tenant_repository.py
    test_membership_repository.py
    test_subscription_repository.py
    test_rls_isolation.py
    test_clerk_jwt.py
    test_access_control.py
    test_stripe_client.py
    test_webhooks_clerk.py
    test_webhooks_stripe.py
    test_billing_router.py
```

---

### Task 1: Backend scaffolding

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/config.py`
- Create: `backend/app/main.py`
- Create: `backend/app/routers/health.py`
- Create: `backend/api/index.py`
- Create: `backend/.env.example`
- Test: `backend/tests/test_health.py`
- Test: `backend/tests/conftest.py` (minimal version — extended in Task 2)

**Interfaces:**
- Produces: `app.config.Settings` (fields: `database_url: str`, `clerk_jwks_url: str`, `clerk_issuer: str`, `clerk_webhook_secret: str`, `stripe_secret_key: str`, `stripe_webhook_secret: str`, `stripe_price_id: str`, `frontend_url: str`), `app.config.get_settings() -> Settings`
- Produces: `app.main.create_app() -> FastAPI`, `app.main.app: FastAPI`
- Produces: `GET /health -> {"status": "ok"}`

- [ ] **Step 1: Create the project manifest**

`backend/pyproject.toml`:
```toml
[project]
name = "karatcore-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "sqlalchemy>=2.0.36",
    "alembic>=1.14",
    "psycopg[binary]>=3.2",
    "pyjwt[crypto]>=2.9",
    "httpx>=0.27",
    "svix>=1.41",
    "stripe>=11.1",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-cov>=6.0", "pyright>=1.1.380"]

[tool.pyright]
include = ["app", "tests", "api"]
typeCheckingMode = "strict"
pythonVersion = "3.12"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
```

- [ ] **Step 2: Create `.env.example` documenting required env vars**

`backend/.env.example`:
```
DATABASE_URL=postgresql+psycopg://user:password@host:5432/karatcore
CLERK_JWKS_URL=https://your-clerk-domain.clerk.accounts.dev/.well-known/jwks.json
CLERK_ISSUER=https://your-clerk-domain.clerk.accounts.dev
CLERK_WEBHOOK_SECRET=whsec_replace_me
STRIPE_SECRET_KEY=sk_test_replace_me
STRIPE_WEBHOOK_SECRET=whsec_replace_me
STRIPE_PRICE_ID=price_replace_me
FRONTEND_URL=http://localhost:5173
```

- [ ] **Step 3: Write `app/config.py`**

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    clerk_jwks_url: str
    clerk_issuer: str
    clerk_webhook_secret: str
    stripe_secret_key: str
    stripe_webhook_secret: str
    stripe_price_id: str
    frontend_url: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Write the health router and app factory**

`backend/app/routers/health.py`:
```python
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
```

`backend/app/main.py`:
```python
from fastapi import FastAPI

from app.routers import health


def create_app() -> FastAPI:
    app = FastAPI(title="KaratCore API")
    app.include_router(health.router)
    return app


app = create_app()
```

`backend/api/index.py`:
```python
from app.main import app

__all__ = ["app"]
```

- [ ] **Step 5: Write the failing test**

`backend/tests/test_health.py`:
```python
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 6: Set up a local `.env` and run the test**

```bash
cd backend
cp .env.example .env
pip install -e ".[dev]"
pytest tests/test_health.py -v
```
Expected: PASS (this endpoint has no DB dependency, so it should pass immediately — this step confirms the scaffolding itself works, not TDD red/green).

- [ ] **Step 7: Type-check**

```bash
pyright
```
Expected: 0 errors.

- [ ] **Step 8: Commit**

```bash
git checkout -b feature/backend-scaffolding
git add backend/pyproject.toml backend/.env.example backend/app backend/api backend/tests/test_health.py
git commit -m "feat(backend): scaffold FastAPI app with health check"
```

---

### Task 2: Core data models, tenant isolation, and repositories

**Files:**
- Create: `backend/app/db/base.py`
- Create: `backend/app/db/session.py`
- Create: `backend/app/models/tenant.py`
- Create: `backend/app/models/membership.py`
- Create: `backend/app/models/subscription.py`
- Create: `backend/app/schemas/tenant.py`
- Create: `backend/app/schemas/membership.py`
- Create: `backend/app/schemas/subscription.py`
- Create: `backend/app/repositories/base.py`
- Create: `backend/app/repositories/tenants.py`
- Create: `backend/app/repositories/memberships.py`
- Create: `backend/app/repositories/subscriptions.py`
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/script.py.mako`
- Create: `backend/alembic/versions/0001_create_tenants.py`
- Create: `backend/alembic/versions/0002_create_memberships.py`
- Create: `backend/alembic/versions/0003_create_subscriptions.py`
- Create: `backend/alembic/versions/0004_enable_rls.py`
- Modify: `backend/tests/conftest.py` (add DB fixtures)
- Test: `backend/tests/test_tenant_repository.py`
- Test: `backend/tests/test_membership_repository.py`
- Test: `backend/tests/test_subscription_repository.py`
- Test: `backend/tests/test_rls_isolation.py`

**Interfaces:**
- Consumes: `app.config.get_settings()` (Task 1)
- Produces: `app.db.base.Base`
- Produces: `app.db.session.engine`, `SessionLocal`, `get_db() -> Generator[Session, None, None]`, `set_tenant_context(db: Session, tenant_id: str) -> None`, `set_service_context(db: Session) -> None`
- Produces: `app.models.tenant.Tenant`, `app.models.membership.Membership`, `app.models.subscription.Subscription`
- Produces: `app.repositories.tenants.TenantRepository`, `app.repositories.memberships.MembershipRepository`, `app.repositories.subscriptions.SubscriptionRepository` (each takes `db: Session` in `__init__`)
- Produces (test fixtures, used by every later task): `db_engine` (session-scoped), `db_session` (function-scoped, rolled back after each test)

**Design note on RLS + service writes:** onboarding and webhook processing are triggered by Clerk/Stripe signatures, not by a signed-in tenant user, so they legitimately need to write rows before a tenant context exists (e.g. creating the very first `tenants` row for a new shop). Each RLS policy therefore allows a row through when EITHER the session's `app.current_tenant_id` matches, OR the session has explicitly set `app.is_service = 'true'`. The service flag is only ever set by our own webhook/onboarding code (never derived from user input), so this does not weaken isolation for ordinary API requests.

- [ ] **Step 1: Write `app/db/base.py` and `app/db/session.py`**

`backend/app/db/base.py`:
```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

`backend/app/db/session.py`:
```python
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def set_tenant_context(db: Session, tenant_id: str) -> None:
    db.execute(text("SET LOCAL app.current_tenant_id = :tenant_id"), {"tenant_id": tenant_id})


def set_service_context(db: Session) -> None:
    db.execute(text("SET LOCAL app.is_service = 'true'"))
```

- [ ] **Step 2: Write the three models**

`backend/app/models/tenant.py`:
```python
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # Clerk organization id
    shop_name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    plan: Mapped[str] = mapped_column(String, nullable=False, default="standard")
    subscription_status: Mapped[str] = mapped_column(String, nullable=False, default="trialing")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

`backend/app/models/membership.py`:
```python
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id", name="uq_membership_user_tenant"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)  # owner | accountant | staff
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

`backend/app/models/subscription.py`:
```python
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), unique=True, nullable=False)
    stripe_customer_id: Mapped[str] = mapped_column(String, nullable=False)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="trialing")
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 3: Write the Pydantic schemas**

`backend/app/schemas/tenant.py`:
```python
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TenantCreate(BaseModel):
    id: str
    shop_name: str
    slug: str


class TenantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    shop_name: str
    slug: str
    plan: str
    subscription_status: str
    created_at: datetime
```

`backend/app/schemas/membership.py`:
```python
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MembershipCreate(BaseModel):
    user_id: str
    tenant_id: str
    role: str


class MembershipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: str
    tenant_id: str
    role: str
    created_at: datetime
```

`backend/app/schemas/subscription.py`:
```python
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: str
    stripe_customer_id: str
    stripe_subscription_id: str | None
    status: str
    current_period_end: datetime | None
```

- [ ] **Step 4: Write the repository base class and the three repositories**

`backend/app/repositories/base.py`:
```python
from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

ModelType = TypeVar("ModelType")


class BaseRepository(Generic[ModelType]):
    def __init__(self, db: Session, model: type[ModelType]) -> None:
        self.db = db
        self.model = model

    def get_by_id(self, id_: object) -> ModelType | None:
        return self.db.get(self.model, id_)

    def create(self, obj: ModelType) -> ModelType:
        self.db.add(obj)
        self.db.flush()
        return obj

    def list_all(self) -> list[ModelType]:
        return list(self.db.scalars(select(self.model)))
```

`backend/app/repositories/tenants.py`:
```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenant import Tenant
from app.repositories.base import BaseRepository


class TenantRepository(BaseRepository[Tenant]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Tenant)

    def get_by_slug(self, slug: str) -> Tenant | None:
        return self.db.scalar(select(Tenant).where(Tenant.slug == slug))
```

`backend/app/repositories/memberships.py`:
```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.membership import Membership
from app.repositories.base import BaseRepository


class MembershipRepository(BaseRepository[Membership]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Membership)

    def get_by_user_and_tenant(self, user_id: str, tenant_id: str) -> Membership | None:
        return self.db.scalar(
            select(Membership).where(Membership.user_id == user_id, Membership.tenant_id == tenant_id)
        )

    def upsert(self, user_id: str, tenant_id: str, role: str) -> Membership:
        existing = self.get_by_user_and_tenant(user_id, tenant_id)
        if existing is not None:
            existing.role = role
            self.db.flush()
            return existing
        return self.create(Membership(user_id=user_id, tenant_id=tenant_id, role=role))

    def delete(self, user_id: str, tenant_id: str) -> None:
        existing = self.get_by_user_and_tenant(user_id, tenant_id)
        if existing is not None:
            self.db.delete(existing)
            self.db.flush()
```

`backend/app/repositories/subscriptions.py`:
```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.subscription import Subscription
from app.repositories.base import BaseRepository


class SubscriptionRepository(BaseRepository[Subscription]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Subscription)

    def get_by_tenant(self, tenant_id: str) -> Subscription | None:
        return self.db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))

    def get_by_stripe_subscription_id(self, stripe_subscription_id: str) -> Subscription | None:
        return self.db.scalar(
            select(Subscription).where(Subscription.stripe_subscription_id == stripe_subscription_id)
        )
```

- [ ] **Step 5: Initialize Alembic and write the migrations**

```bash
cd backend
alembic init alembic
```

Edit `backend/alembic/env.py` so `target_metadata` points at our models and the URL comes from `Settings`:
```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db.base import Base
from app.models import membership, subscription, tenant  # noqa: F401 (registers models on Base.metadata)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=get_settings().database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`backend/alembic/versions/0001_create_tenants.py`:
```python
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("shop_name", sa.String, nullable=False),
        sa.Column("slug", sa.String, nullable=False, unique=True),
        sa.Column("plan", sa.String, nullable=False, server_default="standard"),
        sa.Column("subscription_status", sa.String, nullable=False, server_default="trialing"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("tenants")
```

`backend/alembic/versions/0002_create_memberships.py`:
```python
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String, nullable=False),
        sa.Column("tenant_id", sa.String, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("role", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_membership_user_tenant"),
    )


def downgrade() -> None:
    op.drop_table("memberships")
```

`backend/alembic/versions/0003_create_subscriptions.py`:
```python
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String, sa.ForeignKey("tenants.id"), nullable=False, unique=True),
        sa.Column("stripe_customer_id", sa.String, nullable=False),
        sa.Column("stripe_subscription_id", sa.String, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="trialing"),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("subscriptions")
```

`backend/alembic/versions/0004_enable_rls.py`:
```python
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TABLES = ["tenants", "memberships", "subscriptions"]


def upgrade() -> None:
    for table in TABLES:
        tenant_column = "id" if table == "tenants" else "tenant_id"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (
                {tenant_column} = current_setting('app.current_tenant_id', true)
                OR current_setting('app.is_service', true) = 'true'
            )
            WITH CHECK (
                {tenant_column} = current_setting('app.current_tenant_id', true)
                OR current_setting('app.is_service', true) = 'true'
            )
            """
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
```

- [ ] **Step 6: Write the test database fixtures**

`backend/tests/conftest.py`:
```python
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
)


@pytest.fixture(scope="session")
def db_engine() -> Engine:
    engine = create_engine(TEST_DATABASE_URL)
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(alembic_cfg, "head")
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine: Engine):
    connection = db_engine.connect()
    transaction = connection.begin()
    # join_transaction_mode="create_savepoint": route handlers under test call
    # db.commit() (e.g. the webhook routers). Without this, that commit would
    # commit the *outer* transaction opened above, and the rollback() below
    # would no longer undo anything — tests would leak data into each other.
    # With it, session.commit() only releases a SAVEPOINT and a new one starts
    # automatically, while the outer transaction (and our rollback of it) stays
    # in control.
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    transaction.rollback()
    connection.close()
```

- [ ] **Step 7: Write the repository tests**

`backend/tests/test_tenant_repository.py`:
```python
from app.models.tenant import Tenant
from app.repositories.tenants import TenantRepository


def test_create_and_get_by_slug(db_session) -> None:
    from app.db.session import set_service_context

    set_service_context(db_session)
    repo = TenantRepository(db_session)
    repo.create(Tenant(id="org_1", shop_name="Test Jewellers", slug="test-jewellers"))

    found = repo.get_by_slug("test-jewellers")

    assert found is not None
    assert found.id == "org_1"
```

`backend/tests/test_membership_repository.py`:
```python
from app.db.session import set_service_context
from app.models.tenant import Tenant
from app.repositories.memberships import MembershipRepository
from app.repositories.tenants import TenantRepository


def test_upsert_creates_then_updates_role(db_session) -> None:
    set_service_context(db_session)
    TenantRepository(db_session).create(Tenant(id="org_1", shop_name="Test", slug="test"))
    repo = MembershipRepository(db_session)

    repo.upsert(user_id="user_1", tenant_id="org_1", role="staff")
    assert repo.get_by_user_and_tenant("user_1", "org_1").role == "staff"

    repo.upsert(user_id="user_1", tenant_id="org_1", role="owner")
    assert repo.get_by_user_and_tenant("user_1", "org_1").role == "owner"
```

`backend/tests/test_subscription_repository.py`:
```python
from app.db.session import set_service_context
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.repositories.subscriptions import SubscriptionRepository
from app.repositories.tenants import TenantRepository


def test_get_by_tenant(db_session) -> None:
    set_service_context(db_session)
    TenantRepository(db_session).create(Tenant(id="org_1", shop_name="Test", slug="test"))
    repo = SubscriptionRepository(db_session)
    repo.create(Subscription(tenant_id="org_1", stripe_customer_id="cus_1", status="trialing"))

    found = repo.get_by_tenant("org_1")

    assert found is not None
    assert found.stripe_customer_id == "cus_1"
```

- [ ] **Step 8: Run the repository tests, expect PASS**

```bash
pytest tests/test_tenant_repository.py tests/test_membership_repository.py tests/test_subscription_repository.py -v
```

- [ ] **Step 9: Write the RLS isolation test**

`backend/tests/test_rls_isolation.py`:
```python
from sqlalchemy import text

from app.db.session import set_service_context, set_tenant_context
from app.models.tenant import Tenant
from app.repositories.tenants import TenantRepository


def test_tenant_cannot_see_another_tenants_row(db_session) -> None:
    set_service_context(db_session)
    repo = TenantRepository(db_session)
    repo.create(Tenant(id="org_a", shop_name="Shop A", slug="shop-a"))
    repo.create(Tenant(id="org_b", shop_name="Shop B", slug="shop-b"))
    db_session.execute(text("RESET app.is_service"))

    set_tenant_context(db_session, "org_a")

    visible_ids = {row.id for row in repo.list_all()}
    assert visible_ids == {"org_a"}


def test_tenant_cannot_write_row_for_another_tenant(db_session) -> None:
    set_service_context(db_session)
    TenantRepository(db_session).create(Tenant(id="org_a", shop_name="Shop A", slug="shop-a"))
    db_session.execute(text("RESET app.is_service"))

    set_tenant_context(db_session, "org_a")

    import pytest
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):
        db_session.add(Tenant(id="org_c", shop_name="Shop C", slug="shop-c"))
        db_session.flush()
```

- [ ] **Step 10: Run the RLS test, expect PASS**

```bash
pytest tests/test_rls_isolation.py -v
```
If it fails with a permissions error rather than the RLS policy behavior itself, check that the Postgres role used by `TEST_DATABASE_URL` does **not** have `BYPASSRLS` (the default `postgres` superuser role does — create a non-superuser role for tests if needed).

- [ ] **Step 11: Type-check**

```bash
pyright
```
Expected: 0 errors.

- [ ] **Step 12: Commit**

```bash
git checkout -b feature/backend-data-model-and-rls
git add backend/app/db backend/app/models backend/app/schemas backend/app/repositories backend/alembic backend/alembic.ini backend/tests/conftest.py backend/tests/test_tenant_repository.py backend/tests/test_membership_repository.py backend/tests/test_subscription_repository.py backend/tests/test_rls_isolation.py
git commit -m "feat(backend): core tenant/membership/subscription models with RLS isolation"
```

---

### Task 3: Clerk JWT verification

**Files:**
- Create: `backend/app/auth/__init__.py`
- Create: `backend/app/auth/clerk_jwt.py`
- Test: `backend/tests/test_clerk_jwt.py`

**Interfaces:**
- Consumes: none (standalone; no DB)
- Produces: `app.auth.clerk_jwt.ClerkClaims` (fields: `sub: str`, `org_id: str | None`, `org_role: str | None`), `app.auth.clerk_jwt.InvalidTokenError(Exception)`, `app.auth.clerk_jwt.ClerkJWKSClient` (constructor `(jwks_url: str)`, method `get_signing_key(kid: str) -> RSAPublicKey`), `app.auth.clerk_jwt.verify_clerk_jwt(token: str, jwks_client: ClerkJWKSClient, issuer: str) -> ClerkClaims`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_clerk_jwt.py`:
```python
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

from app.auth.clerk_jwt import ClerkJWKSClient, InvalidTokenError, verify_clerk_jwt

ISSUER = "https://test.clerk.accounts.dev"


class FakeJWKSClient(ClerkJWKSClient):
    def __init__(self, public_key: RSAPublicKey) -> None:
        self._public_key = public_key

    def get_signing_key(self, kid: str) -> RSAPublicKey:
        return self._public_key


def _make_keypair() -> tuple[RSAPrivateKey, RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _sign(private_key: RSAPrivateKey, claims: dict[str, object]) -> str:
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})


def test_verify_clerk_jwt_returns_claims_for_valid_token() -> None:
    private_key, public_key = _make_keypair()
    token = _sign(private_key, {"sub": "user_1", "org_id": "org_1", "org_role": "org:owner", "iss": ISSUER})

    claims = verify_clerk_jwt(token, FakeJWKSClient(public_key), ISSUER)

    assert claims.sub == "user_1"
    assert claims.org_id == "org_1"
    assert claims.org_role == "org:owner"


def test_verify_clerk_jwt_rejects_wrong_issuer() -> None:
    private_key, public_key = _make_keypair()
    token = _sign(private_key, {"sub": "user_1", "iss": "https://someone-else.clerk.accounts.dev"})

    with pytest.raises(InvalidTokenError):
        verify_clerk_jwt(token, FakeJWKSClient(public_key), ISSUER)


def test_verify_clerk_jwt_rejects_token_signed_by_different_key() -> None:
    _, real_public_key = _make_keypair()
    other_private_key, _ = _make_keypair()
    token = _sign(other_private_key, {"sub": "user_1", "iss": ISSUER})

    with pytest.raises(InvalidTokenError):
        verify_clerk_jwt(token, FakeJWKSClient(real_public_key), ISSUER)
```

- [ ] **Step 2: Run the tests, expect FAIL (module doesn't exist yet)**

```bash
pytest tests/test_clerk_jwt.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.auth.clerk_jwt'`.

- [ ] **Step 3: Write `app/auth/clerk_jwt.py`**

```python
import json

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt.algorithms import RSAAlgorithm
from pydantic import BaseModel


class InvalidTokenError(Exception):
    pass


class ClerkClaims(BaseModel):
    sub: str
    org_id: str | None = None
    org_role: str | None = None


class ClerkJWKSClient:
    def __init__(self, jwks_url: str) -> None:
        self.jwks_url = jwks_url
        self._cache: dict[str, RSAPublicKey] = {}

    def get_signing_key(self, kid: str) -> RSAPublicKey:
        if kid not in self._cache:
            response = httpx.get(self.jwks_url, timeout=5.0)
            response.raise_for_status()
            for key in response.json()["keys"]:
                if key["kid"] == kid:
                    self._cache[key["kid"]] = RSAAlgorithm.from_jwk(json.dumps(key))  # type: ignore[assignment]
            if kid not in self._cache:
                raise InvalidTokenError(f"Unknown key id: {kid}")
        return self._cache[kid]


def verify_clerk_jwt(token: str, jwks_client: ClerkJWKSClient, issuer: str) -> ClerkClaims:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("Malformed token") from exc

    kid = header.get("kid")
    if not kid:
        raise InvalidTokenError("Token header missing key id")

    signing_key = jwks_client.get_signing_key(kid)
    try:
        payload = jwt.decode(token, key=signing_key, algorithms=["RS256"], issuer=issuer, options={"verify_aud": False})
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc

    return ClerkClaims(sub=payload["sub"], org_id=payload.get("org_id"), org_role=payload.get("org_role"))
```

- [ ] **Step 4: Run the tests, expect PASS**

```bash
pytest tests/test_clerk_jwt.py -v
```

- [ ] **Step 5: Type-check**

```bash
pyright
```
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git checkout -b feature/backend-clerk-jwt-verification
git add backend/app/auth/__init__.py backend/app/auth/clerk_jwt.py backend/tests/test_clerk_jwt.py
git commit -m "feat(backend): verify Clerk-issued JWTs against JWKS"
```

---

### Task 4: Access control (tenant/role resolution)

**Files:**
- Create: `backend/app/auth/dependencies.py`
- Test: `backend/tests/test_access_control.py`

**Interfaces:**
- Consumes: `ClerkClaims`, `ClerkJWKSClient`, `verify_clerk_jwt`, `InvalidTokenError` (Task 3); `get_db`, `set_tenant_context` (Task 2); `MembershipRepository` (Task 2); `get_settings` (Task 1)
- Produces: `app.auth.dependencies.get_current_claims`, `app.auth.dependencies.TenantContext` (fields: `tenant_id: str`, `user_id: str`, `role: str`), `app.auth.dependencies.get_tenant_context`, `app.auth.dependencies.require_role(*roles: str)`

- [ ] **Step 1: Write `app/auth/dependencies.py`**

```python
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.auth.clerk_jwt import ClerkClaims, ClerkJWKSClient, InvalidTokenError, verify_clerk_jwt
from app.config import get_settings
from app.db.session import get_db, set_tenant_context
from app.repositories.memberships import MembershipRepository

_jwks_client: ClerkJWKSClient | None = None


def get_jwks_client() -> ClerkJWKSClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = ClerkJWKSClient(get_settings().clerk_jwks_url)
    return _jwks_client


def get_current_claims(
    jwks_client: Annotated[ClerkJWKSClient, Depends(get_jwks_client)],
    authorization: Annotated[str | None, Header()] = None,
) -> ClerkClaims:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ")
    try:
        return verify_clerk_jwt(token, jwks_client, get_settings().clerk_issuer)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@dataclass
class TenantContext:
    tenant_id: str
    user_id: str
    role: str


def get_tenant_context(
    claims: Annotated[ClerkClaims, Depends(get_current_claims)],
    db: Annotated[Session, Depends(get_db)],
) -> TenantContext:
    if not claims.org_id:
        raise HTTPException(status_code=403, detail="No active organization selected")

    set_tenant_context(db, claims.org_id)
    membership = MembershipRepository(db).get_by_user_and_tenant(claims.sub, claims.org_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="Not found")

    return TenantContext(tenant_id=claims.org_id, user_id=claims.sub, role=membership.role)


def require_role(*roles: str):
    def dependency(ctx: Annotated[TenantContext, Depends(get_tenant_context)]) -> TenantContext:
        if ctx.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return ctx

    return dependency
```

- [ ] **Step 2: Write the tests using a throwaway test app**

`backend/tests/test_access_control.py`:
```python
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth.clerk_jwt import ClerkClaims
from app.auth.dependencies import TenantContext, get_current_claims, get_db, get_tenant_context, require_role
from app.db.session import set_service_context
from app.models.tenant import Tenant
from app.repositories.memberships import MembershipRepository
from app.repositories.tenants import TenantRepository

test_app = FastAPI()


@test_app.get("/protected")
def protected_route(ctx: Annotated[TenantContext, Depends(require_role("owner", "accountant"))]) -> dict[str, str]:
    return {"tenant_id": ctx.tenant_id, "role": ctx.role}


def test_missing_token_returns_401() -> None:
    client = TestClient(test_app)
    response = client.get("/protected")
    assert response.status_code == 401


def test_no_active_org_returns_403(db_session) -> None:
    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[get_current_claims] = lambda: ClerkClaims(sub="user_1", org_id=None)

    client = TestClient(test_app)
    response = client.get("/protected", headers={"Authorization": "Bearer x"})

    assert response.status_code == 403
    test_app.dependency_overrides.clear()


def test_org_with_no_membership_returns_404(db_session) -> None:
    set_service_context(db_session)
    TenantRepository(db_session).create(Tenant(id="org_1", shop_name="Test", slug="test"))

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[get_current_claims] = lambda: ClerkClaims(sub="stranger", org_id="org_1")

    client = TestClient(test_app)
    response = client.get("/protected", headers={"Authorization": "Bearer x"})

    assert response.status_code == 404
    test_app.dependency_overrides.clear()


def test_role_not_permitted_returns_403(db_session) -> None:
    set_service_context(db_session)
    TenantRepository(db_session).create(Tenant(id="org_1", shop_name="Test", slug="test"))
    MembershipRepository(db_session).upsert(user_id="user_1", tenant_id="org_1", role="staff")

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[get_current_claims] = lambda: ClerkClaims(sub="user_1", org_id="org_1")

    client = TestClient(test_app)
    response = client.get("/protected", headers={"Authorization": "Bearer x"})

    assert response.status_code == 403
    test_app.dependency_overrides.clear()


def test_permitted_role_returns_200(db_session) -> None:
    set_service_context(db_session)
    TenantRepository(db_session).create(Tenant(id="org_1", shop_name="Test", slug="test"))
    MembershipRepository(db_session).upsert(user_id="user_1", tenant_id="org_1", role="owner")

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[get_current_claims] = lambda: ClerkClaims(sub="user_1", org_id="org_1")

    client = TestClient(test_app)
    response = client.get("/protected", headers={"Authorization": "Bearer x"})

    assert response.status_code == 200
    assert response.json() == {"tenant_id": "org_1", "role": "owner"}
    test_app.dependency_overrides.clear()
```

- [ ] **Step 3: Run the tests, expect PASS**

```bash
pytest tests/test_access_control.py -v
```

- [ ] **Step 4: Type-check**

```bash
pyright
```
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git checkout -b feature/backend-access-control
git add backend/app/auth/dependencies.py backend/tests/test_access_control.py
git commit -m "feat(backend): tenant/role access control with 401/403/404 semantics"
```

---

### Task 5: Stripe client wrapper

**Files:**
- Create: `backend/app/integrations/__init__.py`
- Create: `backend/app/integrations/stripe_client.py`
- Test: `backend/tests/test_stripe_client.py`

**Interfaces:**
- Consumes: `app.config.get_settings()` (Task 1)
- Produces: `app.integrations.stripe_client.StripeClient` with methods `create_customer(email: str, name: str) -> str`, `start_trial_subscription(customer_id: str, trial_days: int = 14) -> dict[str, object]`, `create_checkout_session(customer_id: str, success_url: str, cancel_url: str) -> str`

- [ ] **Step 1: Write the failing tests (mocking the `stripe` SDK)**

`backend/tests/test_stripe_client.py`:
```python
from unittest.mock import MagicMock, patch

from app.integrations.stripe_client import StripeClient


def test_create_customer_returns_customer_id() -> None:
    with patch("app.integrations.stripe_client.stripe.Customer.create") as mock_create:
        mock_create.return_value = MagicMock(id="cus_123")
        client = StripeClient(api_key="sk_test", price_id="price_123")

        customer_id = client.create_customer(email="owner@shop.test", name="Test Jewellers")

        assert customer_id == "cus_123"
        mock_create.assert_called_once_with(email="owner@shop.test", name="Test Jewellers")


def test_start_trial_subscription_passes_trial_days() -> None:
    with patch("app.integrations.stripe_client.stripe.Subscription.create") as mock_create:
        mock_create.return_value = {"id": "sub_123", "status": "trialing"}
        client = StripeClient(api_key="sk_test", price_id="price_123")

        result = client.start_trial_subscription(customer_id="cus_123", trial_days=14)

        assert result["id"] == "sub_123"
        mock_create.assert_called_once_with(
            customer="cus_123", items=[{"price": "price_123"}], trial_period_days=14
        )


def test_create_checkout_session_returns_url() -> None:
    with patch("app.integrations.stripe_client.stripe.checkout.Session.create") as mock_create:
        mock_create.return_value = MagicMock(url="https://checkout.stripe.com/session_abc")
        client = StripeClient(api_key="sk_test", price_id="price_123")

        url = client.create_checkout_session(
            customer_id="cus_123", success_url="https://app.test/ok", cancel_url="https://app.test/cancel"
        )

        assert url == "https://checkout.stripe.com/session_abc"
```

- [ ] **Step 2: Run the tests, expect FAIL**

```bash
pytest tests/test_stripe_client.py -v
```
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `app/integrations/stripe_client.py`**

```python
import stripe


class StripeClient:
    def __init__(self, api_key: str, price_id: str) -> None:
        self._price_id = price_id
        self._stripe = stripe
        self._stripe.api_key = api_key

    def create_customer(self, email: str, name: str) -> str:
        customer = self._stripe.Customer.create(email=email, name=name)
        return customer.id

    def start_trial_subscription(self, customer_id: str, trial_days: int = 14) -> dict[str, object]:
        return self._stripe.Subscription.create(
            customer=customer_id, items=[{"price": self._price_id}], trial_period_days=trial_days
        )

    def create_checkout_session(self, customer_id: str, success_url: str, cancel_url: str) -> str:
        session = self._stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": self._price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return session.url
```

- [ ] **Step 4: Run the tests, expect PASS**

```bash
pytest tests/test_stripe_client.py -v
```

- [ ] **Step 5: Type-check**

```bash
pyright
```
Expected: 0 errors (the `stripe` package ships partial types; add `# type: ignore[import-untyped]` on the `import stripe` line if pyright can't resolve its types, or add `stripe` to a `pyright` `reportMissingTypeStubs = false` override in `pyproject.toml` if that's cleaner).

- [ ] **Step 6: Commit**

```bash
git checkout -b feature/backend-stripe-client
git add backend/app/integrations backend/tests/test_stripe_client.py
git commit -m "feat(backend): Stripe client wrapper for customers, trials, and checkout"
```

---

### Task 6: Clerk webhooks, onboarding, and membership sync

**Files:**
- Create: `backend/app/models/webhook_event.py`
- Create: `backend/app/repositories/webhook_events.py`
- Create: `backend/app/services/__init__.py`
- Create: `backend/app/services/onboarding.py`
- Create: `backend/app/services/membership_sync.py`
- Create: `backend/app/routers/webhooks_clerk.py`
- Create: `backend/alembic/versions/0005_create_webhook_events.py`
- Modify: `backend/app/main.py` (register the new router)
- Test: `backend/tests/test_webhooks_clerk.py`

**Interfaces:**
- Consumes: `TenantRepository`, `SubscriptionRepository`, `MembershipRepository`, `set_service_context` (Task 2); `StripeClient` (Task 5); `get_db`, `get_settings` (Tasks 1–2)
- Produces: `app.repositories.webhook_events.WebhookEventRepository` with `is_processed(event_id: str) -> bool`, `mark_processed(event_id: str, source: str) -> None`
- Produces: `app.services.onboarding.OnboardingService.handle_org_created(db: Session, org_id: str, org_name: str, org_slug: str, owner_user_id: str, owner_email: str) -> Tenant`
- Produces: `app.services.membership_sync.MembershipSyncService.upsert(db: Session, org_id: str, user_id: str, role: str) -> None`, `.remove(db: Session, org_id: str, user_id: str) -> None`
- Produces: `POST /webhooks/clerk` (svix-signature verified, idempotent)

- [ ] **Step 1: Add the `webhook_events` table**

`backend/app/models/webhook_event.py`:
```python
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False)  # "clerk" | "stripe"
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

`backend/alembic/versions/0005_create_webhook_events.py`:
```python
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("webhook_events")
```

Note: `webhook_events` intentionally has **no** RLS policy — it is platform-level bookkeeping, not tenant data.

`backend/app/repositories/webhook_events.py`:
```python
from sqlalchemy.orm import Session

from app.models.webhook_event import WebhookEvent


class WebhookEventRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def is_processed(self, event_id: str) -> bool:
        return self.db.get(WebhookEvent, event_id) is not None

    def mark_processed(self, event_id: str, source: str) -> None:
        self.db.add(WebhookEvent(id=event_id, source=source))
        self.db.flush()
```

- [ ] **Step 2: Write the onboarding and membership-sync services**

`backend/app/services/onboarding.py`:
```python
from sqlalchemy.orm import Session

from app.integrations.stripe_client import StripeClient
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.repositories.memberships import MembershipRepository
from app.repositories.subscriptions import SubscriptionRepository
from app.repositories.tenants import TenantRepository


class OnboardingService:
    def __init__(self, stripe_client: StripeClient) -> None:
        self._stripe = stripe_client

    def handle_org_created(
        self, db: Session, org_id: str, org_name: str, org_slug: str, owner_user_id: str, owner_email: str
    ) -> Tenant:
        tenants = TenantRepository(db)
        tenant = tenants.create(Tenant(id=org_id, shop_name=org_name, slug=org_slug))

        customer_id = self._stripe.create_customer(email=owner_email, name=org_name)
        trial = self._stripe.start_trial_subscription(customer_id=customer_id)

        SubscriptionRepository(db).create(
            Subscription(
                tenant_id=org_id,
                stripe_customer_id=customer_id,
                stripe_subscription_id=str(trial["id"]),
                status=str(trial["status"]),
            )
        )
        MembershipRepository(db).upsert(user_id=owner_user_id, tenant_id=org_id, role="owner")

        return tenant
```

`backend/app/services/membership_sync.py`:
```python
from sqlalchemy.orm import Session

from app.repositories.memberships import MembershipRepository

_CLERK_ROLE_MAP = {"org:owner": "owner", "org:accountant": "accountant", "org:staff": "staff"}


class MembershipSyncService:
    def upsert(self, db: Session, org_id: str, user_id: str, clerk_role: str) -> None:
        role = _CLERK_ROLE_MAP.get(clerk_role, "staff")
        MembershipRepository(db).upsert(user_id=user_id, tenant_id=org_id, role=role)

    def remove(self, db: Session, org_id: str, user_id: str) -> None:
        MembershipRepository(db).delete(user_id=user_id, tenant_id=org_id)
```

- [ ] **Step 3: Write the webhook router**

`backend/app/routers/webhooks_clerk.py`:
```python
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from svix.webhooks import Webhook, WebhookVerificationError

from app.config import get_settings
from app.db.session import get_db, set_service_context
from app.integrations.stripe_client import StripeClient
from app.repositories.webhook_events import WebhookEventRepository
from app.services.membership_sync import MembershipSyncService
from app.services.onboarding import OnboardingService

router = APIRouter()


def _verify(payload: bytes, headers: dict[str, str]) -> dict[str, Any]:
    webhook = Webhook(get_settings().clerk_webhook_secret)
    try:
        return webhook.verify(payload, headers)  # type: ignore[return-value]
    except WebhookVerificationError as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook signature") from exc


@router.post("/webhooks/clerk")
async def clerk_webhook(request: Request, db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    payload = await request.body()
    headers = dict(request.headers)
    event = _verify(payload, headers)
    event_id = headers.get("svix-id")
    if not event_id:
        raise HTTPException(status_code=400, detail="Missing svix-id header")

    set_service_context(db)
    events = WebhookEventRepository(db)
    if events.is_processed(event_id):
        return {"status": "already_processed"}

    event_type = event["type"]
    data = event["data"]

    if event_type == "organization.created":
        settings = get_settings()
        stripe_client = StripeClient(api_key=settings.stripe_secret_key, price_id=settings.stripe_price_id)
        OnboardingService(stripe_client).handle_org_created(
            db,
            org_id=data["id"],
            org_name=data["name"],
            org_slug=data["slug"],
            owner_user_id=data["created_by"],
            owner_email=data.get("created_by_email", ""),
        )
    elif event_type in ("organizationMembership.created", "organizationMembership.updated"):
        MembershipSyncService().upsert(
            db,
            org_id=data["organization"]["id"],
            user_id=data["public_user_data"]["user_id"],
            clerk_role=data["role"],
        )
    elif event_type == "organizationMembership.deleted":
        MembershipSyncService().remove(
            db, org_id=data["organization"]["id"], user_id=data["public_user_data"]["user_id"]
        )

    events.mark_processed(event_id, source="clerk")
    db.commit()
    return {"status": "processed"}
```

`backend/app/main.py` (add the router):
```python
from fastapi import FastAPI

from app.routers import health, webhooks_clerk


def create_app() -> FastAPI:
    app = FastAPI(title="KaratCore API")
    app.include_router(health.router)
    app.include_router(webhooks_clerk.router)
    return app


app = create_app()
```

- [ ] **Step 4: Write the webhook tests**

`backend/tests/test_webhooks_clerk.py`:
```python
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app
from app.repositories.memberships import MembershipRepository
from app.repositories.tenants import TenantRepository

client = TestClient(app)


def _override_db(db_session):
    app.dependency_overrides[get_db] = lambda: db_session


def test_organization_created_creates_tenant_and_membership(db_session) -> None:
    _override_db(db_session)
    event = {
        "type": "organization.created",
        "data": {
            "id": "org_1",
            "name": "Test Jewellers",
            "slug": "test-jewellers",
            "created_by": "user_1",
            "created_by_email": "owner@test.com",
        },
    }

    with patch("app.routers.webhooks_clerk._verify", return_value=event), \
         patch("app.integrations.stripe_client.stripe.Customer.create", return_value=MagicMock(id="cus_1")), \
         patch(
             "app.integrations.stripe_client.stripe.Subscription.create",
             return_value={"id": "sub_1", "status": "trialing"},
         ):
        response = client.post("/webhooks/clerk", content=b"{}", headers={"svix-id": "msg_1"})

    assert response.status_code == 200
    assert TenantRepository(db_session).get_by_id("org_1") is not None
    assert MembershipRepository(db_session).get_by_user_and_tenant("user_1", "org_1").role == "owner"
    app.dependency_overrides.clear()


def test_duplicate_event_is_not_reprocessed(db_session) -> None:
    _override_db(db_session)
    event = {
        "type": "organization.created",
        "data": {
            "id": "org_2",
            "name": "Second Shop",
            "slug": "second-shop",
            "created_by": "user_2",
            "created_by_email": "owner2@test.com",
        },
    }

    with patch("app.routers.webhooks_clerk._verify", return_value=event), \
         patch("app.integrations.stripe_client.stripe.Customer.create", return_value=MagicMock(id="cus_2")) as mock_customer, \
         patch(
             "app.integrations.stripe_client.stripe.Subscription.create",
             return_value={"id": "sub_2", "status": "trialing"},
         ):
        client.post("/webhooks/clerk", content=b"{}", headers={"svix-id": "msg_2"})
        client.post("/webhooks/clerk", content=b"{}", headers={"svix-id": "msg_2"})

    mock_customer.assert_called_once()  # second delivery (same svix-id) was a no-op
    app.dependency_overrides.clear()
```

- [ ] **Step 5: Run the migration and tests**

```bash
pytest tests/test_webhooks_clerk.py -v
```
Expected: PASS.

- [ ] **Step 6: Type-check**

```bash
pyright
```
Expected: 0 errors. `svix` may need a `# type: ignore[import-untyped]` if it ships no type stubs.

- [ ] **Step 7: Commit**

```bash
git checkout -b feature/backend-clerk-webhooks-onboarding
git add backend/app/models/webhook_event.py backend/app/repositories/webhook_events.py backend/app/services backend/app/routers/webhooks_clerk.py backend/alembic/versions/0005_create_webhook_events.py backend/app/main.py backend/tests/test_webhooks_clerk.py
git commit -m "feat(backend): Clerk webhook handling with idempotent onboarding and membership sync"
```

---

### Task 7: Stripe webhooks and subscription lifecycle sync

**Files:**
- Create: `backend/app/services/billing.py`
- Create: `backend/app/routers/webhooks_stripe.py`
- Modify: `backend/app/main.py` (register the new router)
- Test: `backend/tests/test_webhooks_stripe.py`

**Interfaces:**
- Consumes: `SubscriptionRepository`, `TenantRepository`, `WebhookEventRepository`, `set_service_context` (Tasks 2, 6)
- Produces: `app.services.billing.BillingService.handle_subscription_updated(db: Session, stripe_subscription_id: str, status: str, current_period_end: datetime | None) -> None`
- Produces: `POST /webhooks/stripe` (Stripe-signature verified, idempotent)

- [ ] **Step 1: Write `app/services/billing.py`**

```python
from datetime import datetime

from sqlalchemy.orm import Session

from app.repositories.subscriptions import SubscriptionRepository
from app.repositories.tenants import TenantRepository


class BillingService:
    def handle_subscription_updated(
        self, db: Session, stripe_subscription_id: str, status: str, current_period_end: datetime | None
    ) -> None:
        subscriptions = SubscriptionRepository(db)
        subscription = subscriptions.get_by_stripe_subscription_id(stripe_subscription_id)
        if subscription is None:
            return  # unknown subscription; nothing to sync

        subscription.status = status
        subscription.current_period_end = current_period_end
        db.flush()

        tenant = TenantRepository(db).get_by_id(subscription.tenant_id)
        if tenant is not None:
            tenant.subscription_status = status
            db.flush()
```

- [ ] **Step 2: Write the webhook router**

`backend/app/routers/webhooks_stripe.py`:
```python
from datetime import datetime, timezone
from typing import Annotated

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db, set_service_context
from app.repositories.webhook_events import WebhookEventRepository
from app.services.billing import BillingService

router = APIRouter()


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    settings = get_settings()

    try:
        event = stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook signature") from exc

    set_service_context(db)
    events = WebhookEventRepository(db)
    if events.is_processed(event["id"]):
        return {"status": "already_processed"}

    if event["type"] in ("customer.subscription.updated", "customer.subscription.deleted"):
        subscription = event["data"]["object"]
        period_end_ts = subscription.get("current_period_end")
        BillingService().handle_subscription_updated(
            db,
            stripe_subscription_id=subscription["id"],
            status=subscription["status"],
            current_period_end=datetime.fromtimestamp(period_end_ts, tz=timezone.utc) if period_end_ts else None,
        )

    events.mark_processed(event["id"], source="stripe")
    db.commit()
    return {"status": "processed"}
```

`backend/app/main.py` (add the router):
```python
from fastapi import FastAPI

from app.routers import health, webhooks_clerk, webhooks_stripe


def create_app() -> FastAPI:
    app = FastAPI(title="KaratCore API")
    app.include_router(health.router)
    app.include_router(webhooks_clerk.router)
    app.include_router(webhooks_stripe.router)
    return app


app = create_app()
```

- [ ] **Step 3: Write the tests**

`backend/tests/test_webhooks_stripe.py`:
```python
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db.session import get_db, set_service_context
from app.main import app
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.repositories.subscriptions import SubscriptionRepository
from app.repositories.tenants import TenantRepository

client = TestClient(app)


def test_subscription_updated_syncs_status(db_session) -> None:
    set_service_context(db_session)
    TenantRepository(db_session).create(Tenant(id="org_1", shop_name="Test", slug="test"))
    SubscriptionRepository(db_session).create(
        Subscription(tenant_id="org_1", stripe_customer_id="cus_1", stripe_subscription_id="sub_1", status="trialing")
    )
    app.dependency_overrides[get_db] = lambda: db_session

    fake_event = {
        "id": "evt_1",
        "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_1", "status": "active", "current_period_end": 1735689600}},
    }

    with patch("app.routers.webhooks_stripe.stripe.Webhook.construct_event", return_value=fake_event):
        response = client.post("/webhooks/stripe", content=b"{}", headers={"stripe-signature": "test"})

    assert response.status_code == 200
    assert TenantRepository(db_session).get_by_id("org_1").subscription_status == "active"
    app.dependency_overrides.clear()


def test_invalid_signature_returns_400(db_session) -> None:
    app.dependency_overrides[get_db] = lambda: db_session
    import stripe

    with patch(
        "app.routers.webhooks_stripe.stripe.Webhook.construct_event",
        side_effect=stripe.error.SignatureVerificationError("bad sig", "sig_header"),
    ):
        response = client.post("/webhooks/stripe", content=b"{}", headers={"stripe-signature": "bad"})

    assert response.status_code == 400
    app.dependency_overrides.clear()
```

- [ ] **Step 4: Run the tests, expect PASS**

```bash
pytest tests/test_webhooks_stripe.py -v
```

- [ ] **Step 5: Type-check**

```bash
pyright
```
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git checkout -b feature/backend-stripe-webhooks
git add backend/app/services/billing.py backend/app/routers/webhooks_stripe.py backend/app/main.py backend/tests/test_webhooks_stripe.py
git commit -m "feat(backend): Stripe webhook handling with idempotent subscription sync"
```

---

### Task 8: Billing API router

**Files:**
- Create: `backend/app/schemas/billing.py`
- Create: `backend/app/routers/billing.py`
- Modify: `backend/app/main.py` (register the new router)
- Test: `backend/tests/test_billing_router.py`

**Interfaces:**
- Consumes: `require_role`, `get_tenant_context`, `TenantContext` (Task 4); `SubscriptionRepository` (Task 2); `StripeClient` (Task 5); `get_settings` (Task 1)
- Produces: `GET /billing/status` (any tenant member) → `SubscriptionStatus` schema; `POST /billing/checkout-session` (Owner/Accountant only) → `{"checkout_url": str}`

- [ ] **Step 1: Write the schema**

`backend/app/schemas/billing.py`:
```python
from datetime import datetime

from pydantic import BaseModel


class SubscriptionStatus(BaseModel):
    status: str
    current_period_end: datetime | None


class CheckoutSessionResponse(BaseModel):
    checkout_url: str
```

- [ ] **Step 2: Write the router**

`backend/app/routers/billing.py`:
```python
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.dependencies import TenantContext, get_tenant_context, require_role
from app.config import get_settings
from app.db.session import get_db
from app.integrations.stripe_client import StripeClient
from app.repositories.subscriptions import SubscriptionRepository
from app.schemas.billing import CheckoutSessionResponse, SubscriptionStatus

router = APIRouter(prefix="/billing")


@router.get("/status")
def get_billing_status(
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    db: Annotated[Session, Depends(get_db)],
) -> SubscriptionStatus:
    subscription = SubscriptionRepository(db).get_by_tenant(ctx.tenant_id)
    if subscription is None:
        raise HTTPException(status_code=404, detail="No subscription found")
    return SubscriptionStatus(status=subscription.status, current_period_end=subscription.current_period_end)


@router.post("/checkout-session")
def create_checkout_session(
    ctx: Annotated[TenantContext, Depends(require_role("owner", "accountant"))],
    db: Annotated[Session, Depends(get_db)],
) -> CheckoutSessionResponse:
    subscription = SubscriptionRepository(db).get_by_tenant(ctx.tenant_id)
    if subscription is None:
        raise HTTPException(status_code=404, detail="No subscription found")

    settings = get_settings()
    stripe_client = StripeClient(api_key=settings.stripe_secret_key, price_id=settings.stripe_price_id)
    checkout_url = stripe_client.create_checkout_session(
        customer_id=subscription.stripe_customer_id,
        success_url=f"{settings.frontend_url}/billing/success",
        cancel_url=f"{settings.frontend_url}/billing/cancelled",
    )
    return CheckoutSessionResponse(checkout_url=checkout_url)
```

`backend/app/main.py` (add the router):
```python
from fastapi import FastAPI

from app.routers import billing, health, webhooks_clerk, webhooks_stripe


def create_app() -> FastAPI:
    app = FastAPI(title="KaratCore API")
    app.include_router(health.router)
    app.include_router(webhooks_clerk.router)
    app.include_router(webhooks_stripe.router)
    app.include_router(billing.router)
    return app


app = create_app()
```

- [ ] **Step 3: Write the tests**

`backend/tests/test_billing_router.py`:

These tests deliberately do **not** override `get_tenant_context`/`require_role`
themselves — `require_role(...)` is a factory that returns a new function
object on every call, so a route's `Depends(require_role("owner", "accountant"))`
and a test's `require_role("owner", "accountant")` are two different objects;
`app.dependency_overrides` matches by object identity, so overriding the
test's copy would silently do nothing to the route's. Instead, override only
the two stable, shared dependencies (`get_current_claims`, `get_db`) and seed
a real `Membership` row with the role under test, letting the actual
`get_tenant_context` → `require_role` chain run for real. This is both
correct and a better test (it exercises the production dependency chain).

```python
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.auth.clerk_jwt import ClerkClaims
from app.auth.dependencies import get_current_claims
from app.db.session import get_db, set_service_context
from app.main import app
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.repositories.memberships import MembershipRepository
from app.repositories.subscriptions import SubscriptionRepository
from app.repositories.tenants import TenantRepository

client = TestClient(app)


def _seed(db_session, role: str) -> None:
    set_service_context(db_session)
    TenantRepository(db_session).create(Tenant(id="org_1", shop_name="Test", slug="test"))
    SubscriptionRepository(db_session).create(
        Subscription(tenant_id="org_1", stripe_customer_id="cus_1", status="trialing")
    )
    MembershipRepository(db_session).upsert(user_id="user_1", tenant_id="org_1", role=role)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_claims] = lambda: ClerkClaims(sub="user_1", org_id="org_1")


def test_get_status_returns_subscription(db_session) -> None:
    _seed(db_session, role="staff")

    response = client.get("/billing/status")

    assert response.status_code == 200
    assert response.json()["status"] == "trialing"
    app.dependency_overrides.clear()


def test_checkout_session_forbidden_for_staff(db_session) -> None:
    _seed(db_session, role="staff")

    response = client.post("/billing/checkout-session")

    assert response.status_code == 403
    app.dependency_overrides.clear()


def test_checkout_session_allowed_for_owner(db_session) -> None:
    _seed(db_session, role="owner")

    with patch(
        "app.integrations.stripe_client.stripe.checkout.Session.create",
        return_value=MagicMock(url="https://checkout.stripe.com/abc"),
    ):
        response = client.post("/billing/checkout-session")

    assert response.status_code == 200
    assert response.json()["checkout_url"] == "https://checkout.stripe.com/abc"
    app.dependency_overrides.clear()
```

- [ ] **Step 4: Run the tests, expect PASS**

```bash
pytest tests/test_billing_router.py -v
```

- [ ] **Step 5: Run the full test suite and type-check**

```bash
pytest -v
pyright
```
Expected: all tests pass, 0 pyright errors.

- [ ] **Step 6: Commit**

```bash
git checkout -b feature/backend-billing-api
git add backend/app/schemas/billing.py backend/app/routers/billing.py backend/app/main.py backend/tests/test_billing_router.py
git commit -m "feat(backend): billing status and checkout-session endpoints"
```

---

## After each task: PR and merge

Per the project's workflow (see `docs/superpowers/specs/2026-09-14-platform-foundation-design.md`, Development workflow), each task above is its own branch. Once its steps are complete and green:

```bash
git push -u origin <branch-name>
gh pr create --title "<short imperative title>" --body "$(cat <<'EOF'
## Summary
- <what this task adds>
- <why: which spec section / requirement this satisfies>

## Test plan
- [ ] pytest passes
- [ ] pyright passes with 0 errors
EOF
)"
```

After review, merge to `main` and delete the branch. Do not start the next task's branch from an unmerged branch — branch from an up-to-date `main` each time so tasks stay independently reviewable.
