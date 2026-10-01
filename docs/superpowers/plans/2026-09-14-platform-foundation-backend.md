# Platform Foundation — Backend Implementation Plan

Revised 2026-10-01. Supersedes the original plan, which was written for the
full SaaS scope (Stripe billing, trials, reconciliation).

**Goal:** Build the FastAPI backend for KaratCore's platform foundation —
tenant isolation with Postgres RLS, Clerk-based auth and roles, team
management, and deployment — so the core ERP can be built directly on top.

**Spec:** `docs/superpowers/specs/2026-09-14-platform-foundation-design.md`.
The spec is the authority; this plan lists the order of work and the
interfaces each task hands to the next. The code itself lives in each task's
branch, not in this document.

**Tech stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2,
psycopg 3, PyJWT, svix, pytest, pyright (strict), ruff.

## Global constraints

- `ruff check .`, `pyright` and `pytest` pass with zero errors before a task
  is done. Run all three from `backend/`.
- Tests run against a real Postgres. `TEST_DATABASE_URL` must be a superuser
  connection; the suite creates and drops its own `karatcore_test` database:

  ```bash
  docker run --rm -d --name karatcore-test-db -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
  export TEST_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
  ```

- Tenant isolation is enforced by RLS. Runtime code never connects as the
  schema owner.
- One branch per task, merged through a pull request. Branches are stacked:
  each task's branch starts from the previous task's branch, and pull
  requests are merged in task order.
- No billing code. The only trace of it is the `AccessPolicy` port.

## File structure (end state)

```
backend/
  pyproject.toml
  alembic.ini
  server.py                     # entrypoint: builds the container from the environment
  vercel.json
  alembic/
    env.py
    versions/0001_database_roles.py
    versions/0002_platform_tables.py
  app/
    main.py                     # create_app(container)
    config.py                   # Settings
    container.py                # AppContainer, build_container
    core/        clock.py, errors.py, webhooks.py
    db/          base.py, database.py
    models/      tenant.py, membership.py, webhook_event.py
    auth/        session_token.py
    identity/    directory.py, clerk_payloads.py, webhooks.py, webhook_service.py
    tenancy/     roles.py, identity_store.py, access.py, entitlements.py, team.py
    api/         container.py, dependencies.py, errors.py
    routers/     health.py, me.py, team.py, clerk_webhooks.py
  tests/
    conftest.py
    support/     database.py, factories.py, tokens.py, clerk_webhooks.py, fakes.py, app.py
    test_*.py
.github/workflows/backend.yml
```

## Tasks

### Task 1 — Backend scaffolding (done)

Branch `feature/backend-scaffolding`, pull request #1. FastAPI app with
`GET /health`, pyright strict.

### Task 2 — Database roles, schema and RLS

Branch `feature/database-rls`.

- Migrations `0001` (runtime roles) and `0002` (tables, forced RLS, policies,
  grants).
- Models `Tenant`, `Membership`, `WebhookEvent`.
- `app/db/database.py`: `APP_ROLE`, `SERVICE_ROLE`,
  `Database(engine, expected_role)` with `from_url`, `session()`,
  `verify_role()`, `dispose()`; `bind_tenant(session, tenant_id)`;
  `UnsafeDatabaseRoleError`, `TenantBindingError`.
- Test support: `tests/support/database.py` (`DatabaseHandles`, database
  lifecycle), `tests/support/factories.py` (`seed_tenant`, `seed_membership`).
- Adds ruff to the toolchain.

Tests: RLS isolation; schema security invariants (privilege matrix, policies,
forced RLS, role guard rejections, model/migration drift, migration round trip).

### Task 3 — Session token verification

Branch `feature/session-auth`.

- `app/auth/session_token.py`: `SessionPrincipal(user_id, session_id,
  organization, impersonator_id, issued_at)`, `ActiveOrganization(id, role,
  slug)`, `SessionTokenVerifier` protocol, `ClerkSessionTokenVerifier`,
  `InvalidSessionTokenError`.
- Test support: `tests/support/tokens.py` (`TokenFactory`).

Tests: valid tokens; every rejected-claim case; wrong key; `alg: none`;
HS256 signed with the public key; constructor validation.

### Task 4 — Clerk webhook parsing and application composition

Branch `feature/app-composition`.

- `app/identity/`: `DirectoryOrganization`, `DirectoryMember`,
  `normalize_role`; Clerk payload models; `IdentityEvent` and the
  `IdentityWebhookParser` protocol; `ClerkWebhookParser`.
- `app/core/`: `Clock`/`SystemClock`; `DomainError` and subclasses
  (`UnauthenticatedError` 401, `ForbiddenError` 403, `NotFoundError` 404,
  `SubscriptionRequiredError` 402, `ConflictError` 409); webhook errors.
- `app/config.py` (`Settings`), `app/container.py` (`AppContainer`,
  `build_container`), `app/main.py` (`create_app(container)`),
  `app/api/errors.py`, `server.py`. Removes `api/index.py`.
- `app/tenancy/entitlements.py`: `AccessDecision`, `AccessPolicy` protocol,
  `OpenAccessPolicy`.

Tests: webhook parser (signature, staleness, shapes); error mapping; CORS;
settings parsing; real container wiring; `server` import from the environment.

### Task 5 — Tenant access

Branch `feature/tenant-access`.

- `app/tenancy/roles.py`: `ClerkRole`, `SubRole`, `Role`, `effective_role`.
- `app/tenancy/identity_store.py`: `IdentityStore` with `ensure_tenant`,
  `apply_organization`, `delete_organization`, `apply_membership`,
  `remove_membership`.
- `app/tenancy/access.py`: `TenantContext(tenant, user_id, role, session)`,
  `TenantAccessResolver.resolve(principal, session)`.
- `app/api/dependencies.py`: `get_principal`, `get_tenant_context`,
  `RequireRole` (`require_owner`, `require_member`), `require_access`.
- `app/routers/me.py`: `GET /me`.

Tests: `IdentityStore` last-write-wins rules; every access-control row of the
spec through HTTP; just-in-time provisioning including concurrent first
requests; role requirements; the entitlement gate with a denying policy.

### Task 6 — Clerk webhook endpoint

Branch `feature/clerk-webhooks`.

- `app/identity/webhook_service.py`: `IdentityWebhookService.handle(event)`
  → claims the event and applies it in one transaction.
- `app/routers/clerk_webhooks.py`: `POST /webhooks/clerk`.

Tests: each event type's resulting rows; duplicate delivery; out-of-order
delivery; bad signature; failure rolls back the claim and returns 500.

### Task 7 — Team API

Branch `feature/team-api`.

- `app/tenancy/team.py`: `TeamService.list_members()`,
  `TeamService.set_sub_role(user_id, sub_role)`.
- `app/routers/team.py`: `GET /team/members`, `PATCH /team/members/{user_id}`.

Tests: owner can list and change; accountant and staff get 403; removed or
other-tenant member is 404; admin target is 409; invalid sub-role is 422.

### Task 8 — Deployment and CI

Branch `feature/deployment-ci`.

- `backend/vercel.json`, `.python-version`, `.env.example`.
- `.github/workflows/backend.yml`: ruff, pyright, pytest with a Postgres
  service container.
- `backend/README.md`: local setup, running tests, creating the runtime
  roles, running migrations, environment variables, Clerk configuration.

## After this plan

Frontend foundation (sign-in, shop creation, team page), then sub-project #2.
