# KaratCore Platform Foundation — Design Spec

Date: 2026-09-14 (revised 2026-10-01)
Status: Revised after an architecture hardening pass and a scope change
(showcase project, SaaS-ready). Supersedes the original 2026-09-14 version.

## Context

KaratCore is an ERP for gold/jewellery stores in India. It is built as an
end-to-end showcase project: a deployed, working product that demonstrates
multi-tenant backend design, a typed React frontend, and real domain features.

It is **SaaS-ready, not SaaS yet**. Everything that would be expensive to
retrofit (tenant isolation, organisations as shops, roles, an entitlement gate)
is built now. Everything commercial (subscriptions, trials, a paywall) is
deferred and is purely additive; see "SaaS upgrade path".

Sub-projects, in build order:

1. **Platform foundation** (this spec) — multi-tenancy, auth, roles, team
   management, tenant isolation, deployment.
2. **Core ERP MVP** — inventory by karat and weight, daily gold rate, invoicing
   with GST and making charges, old-gold exchange, customer ledger.
3. **Showcase polish** — seeded demo shop with one-click login, reports,
   README with architecture diagram and screenshots.
4. **SaaS upgrade** — subscription billing, trials, paywall, reconciliation.

This spec covers **#1 only**.

## Goals

- A user signs in, creates a shop, and invites staff.
- Each shop's data is isolated from every other shop's by the database itself,
  not only by application code.
- Three roles exist within a shop — Owner, Accountant, Staff — enforced on the
  backend.
- An owner can view the team and change a member between Accountant and Staff.
- Sub-project #2 can be built on top without revisiting tenancy or auth, and
  sub-project #4 can add billing without changing existing code beyond one gate.

## Non-goals

- Any ERP functionality (sub-project #2).
- Subscription billing, trials, payment providers (sub-project #4).
- A reconciliation job that repairs missed webhooks (sub-project #4).
- Multi-branch support within one shop.
- A platform admin dashboard, cross-tenant reporting.
- Offline operation. The app assumes the shop is online.

## Architecture

- **Frontend:** Vite + React + TypeScript (strict) + Tailwind CSS, a static SPA
  on Vercel. Clerk's React SDK handles sign-in and organisation switching.
- **Backend:** FastAPI on Python 3.12, one Vercel function, region `bom1`
  (Mumbai). Stateless request/response only.
- **Database:** Neon Postgres in `aws-ap-south-1`, reached through Neon's
  pooled endpoint. SQLAlchemy 2.0 + Alembic. Pydantic for all I/O.
- **Auth:** Clerk. One Clerk **Organization** = one shop = one tenant. The
  Clerk instance uses only Clerk's two default organisation roles, `admin` and
  `member`. KaratCore refines `member` into Accountant or Staff in its own
  database, so no paid Clerk feature is required.

The backend is assembled at one composition root. `AppContainer` (a frozen
dataclass) holds settings, the clock, both database handles, the session-token
verifier, the Clerk webhook parser and the access policy. `create_app(container)`
builds the FastAPI app from it; `server.py` is the only place that reads the
environment. Tests build the same app around a real Postgres and a frozen clock.

## Tenant isolation

Shared database, shared schema. Every tenant-scoped table carries `tenant_id`
(the Clerk organisation id) and is protected by Postgres row-level security.

### Database roles

| Role | Used by | Can do |
| --- | --- | --- |
| schema owner | Alembic migrations only | DDL. Never used at runtime. |
| `karatcore_app` | user-facing API requests | Rows of the bound tenant only. |
| `karatcore_service` | webhooks, just-in-time provisioning | Platform tables, all tenants. |

- The two runtime roles are created by migration `0001` as `NOLOGIN`,
  `NOSUPERUSER`, `NOBYPASSRLS`. An operator enables login with SQL. They must
  not be created through the Neon Console, whose roles can bypass RLS.
- Every table has RLS **enabled and forced**, so even the table owner is
  subject to policies.
- Policies are role-targeted. `tenant_isolation` (`TO karatcore_app`) compares
  the row's tenant to `current_setting('app.tenant_id', true)` in both `USING`
  and `WITH CHECK`. `service_access` (`TO karatcore_service`) allows all rows.
  A session with no tenant bound sees nothing.
- Grants are least-privilege. Nobody has `DELETE`. `karatcore_app` has
  `SELECT` on `tenants` and `memberships`, plus column-level `UPDATE` on
  `memberships (sub_role, updated_at)`. `karatcore_service` has `SELECT`,
  `INSERT`, `UPDATE` on the platform tables and nothing else.

### Binding the tenant

`bind_tenant(session, tenant_id)` records the tenant on the SQLAlchemy session.
An `after_begin` listener runs `set_config('app.tenant_id', :id, true)` at the
start of **every** transaction, so a mid-request `commit()` cannot drop the
tenant context. The setting is transaction-local, which is what makes it safe
behind a transaction-mode pooler. A session cannot be rebound to another tenant.

Engines use `NullPool` and disable server-side prepared statements, both
required behind Neon's pooler on serverless.

### Fail-closed role guard

Before handing out its first session, each `Database` checks the connected
role and refuses to serve if it is not exactly the expected role, or is a
superuser, has `BYPASSRLS`, is a member of another role, or owns relations.
A misconfigured connection string therefore fails loudly instead of silently
disabling isolation.

## Data model

- `tenants` — `id` (Clerk organisation id), `name`, `slug`, `deleted_at`,
  `clerk_synced_at`, timestamps. Rows are never hard-deleted.
- `memberships` — `(tenant_id, user_id)` primary key, `clerk_role`
  (`admin` | `member`), `sub_role` (`accountant` | `staff`), `removed_at`
  (tombstone), `clerk_synced_at`, timestamps.
- `webhook_events` — `(source, event_id)` primary key, `event_type`,
  `received_at`. The idempotency claim for a delivered event.

Effective role: Clerk `admin` → **Owner**; otherwise the `sub_role`.

## Identity sync

Clerk is the source of truth for who belongs to which shop. Two writers bring
that state into the database, and both go through one class, `IdentityStore`,
using the `karatcore_service` role:

1. **Just-in-time provisioning.** A verified session token is itself proof of
   membership. On a request whose tenant or membership row is missing, removed
   or has a different Clerk role, the row is upserted from the token. A new
   shop therefore works immediately, before any webhook arrives.
2. **Clerk webhooks.** `organization.created/updated/deleted` and
   `organizationMembership.created/updated/deleted`.

They can run concurrently and out of order, so every write is one atomic
upsert guarded by an observation time (last write wins):

- `memberships.clerk_synced_at` is the Clerk-clock time of the last applied
  observation. Webhooks use the event timestamp; provisioning uses the token's
  `iat`. An older observation is ignored.
- Removal is a tombstone, never a `DELETE`, so a stale "member" observation
  cannot resurrect a removed user.
- A token issued before a removal must not revive the membership. Because
  `iat` has one-second resolution, provisioning may only reactivate a removed
  membership when the token is at least 30 seconds newer than the removal.
- A reactivated member starts again as Staff.
- Organisation deletion is terminal: Clerk never reuses organisation ids.

Known limit: with no reconciliation job, a webhook that Clerk fails to deliver
after all its retries leaves the team list stale until the next event. Access
itself stays correct, because it is decided from the session token.

## Authentication

The backend verifies Clerk session tokens (version 2) without a network call,
using the instance's PEM public key from configuration.

- Algorithm pinned to RS256; the key must be RSA.
- `exp`, `nbf`, `iat`, `iss`, `sub`, `sid` are required; issuer must match.
- `azp` must be one of the configured frontend origins.
- `sts` must be `active` (a pending session is rejected); `v` must be `2`.
- The active organisation comes from the `o` claim (`id`, `rol`, `slg`).

A revoked session stays valid until its token expires, at most 60 seconds.

## Access control

Per request: token → principal → tenant context (tenant id, user id, role,
tenant-bound database session).

| Situation | Response |
| --- | --- |
| Missing, malformed or invalid token | `401` with `WWW-Authenticate: Bearer` |
| No active organisation, or unknown Clerk role | `403` |
| Membership removed | `403` |
| Shop deleted | `404` |
| Role lacks permission for the endpoint | `403` |
| Resource belongs to another tenant | `404` (RLS makes it invisible) |
| Access policy denies the shop | `402` |

Errors share one body shape: `{"detail": "...", "code": "..."}`.

### Entitlement gate

Every ERP route depends on `require_access`, which asks an `AccessPolicy`
whether the shop may use the product. Today the container wires
`OpenAccessPolicy`, which always allows. Sub-project #4 replaces that one
object with a subscription-aware policy; routes do not change.

## API surface (this sub-project)

- `GET /health` — liveness.
- `GET /me` — user id, effective role, shop (id, name, slug), access decision.
- `GET /team/members` — Owner only. Active members with their roles.
- `PATCH /team/members/{user_id}` — Owner only. Sets `sub_role`. `404` if the
  member is not active in this shop, `409` if the target is an admin.
- `POST /webhooks/clerk` — Svix-signed Clerk events.

CORS allows only the configured frontend origins, without credentials (the API
uses bearer tokens, not cookies).

## Webhook handling

- The Svix signature is verified before the payload is parsed. A bad signature
  or malformed payload returns `400` and is logged.
- The event id (`svix-id`) is claimed with
  `INSERT ... ON CONFLICT DO NOTHING RETURNING` in the **same transaction** as
  the event's effects. A duplicate delivery returns `200` and does nothing.
- Any failure rolls back the claim and returns `500`, so Clerk retries.
- Event types the backend does not handle are claimed and acknowledged.

## Code structure and type safety

- **Backend:** classes with single responsibilities. Ports are `Protocol`s
  (`SessionTokenVerifier`, `IdentityWebhookParser`, `AccessPolicy`, `Clock`);
  adapters implement them (`ClerkSessionTokenVerifier`, `ClerkWebhookParser`).
  Routers are thin and delegate to services. Domain errors are exceptions
  mapped to HTTP in one place.
- **Frontend:** functional components and hooks; non-component logic (API
  client, session helpers) in classes/modules; TypeScript `strict`.
- Definition of done for every branch: `ruff check`, `pyright` (strict) and
  `pytest` pass with zero errors (backend); `tsc --noEmit` passes (frontend).

## Testing strategy

- Tests run against a **real Postgres**, connecting as the real runtime roles.
  The suite creates and drops its own database.
- RLS: cross-tenant reads and writes, unbound sessions, binding across commits.
- Schema security invariants read from the catalog: forced RLS on every table,
  exact policies, an exact privilege matrix, no drift between models and
  migrations, and a downgrade/upgrade round trip.
- Token verification: every rejected-claim case, algorithm confusion, wrong key.
- Identity sync: out-of-order and concurrent observations.
- HTTP: each access-control row in the table above, each role on each route.
- Security checks are mutation-tested during development: each guard is
  deliberately broken to confirm a test fails.

## Deployment

- Vercel project rooted at `backend/`, entrypoint `server:app`, region `bom1`.
- Environment: `DATABASE_URL` (`karatcore_app`), `SERVICE_DATABASE_URL`
  (`karatcore_service`), `CLERK_ISSUER`, `CLERK_JWT_PUBLIC_KEY`,
  `CLERK_AUTHORIZED_PARTIES`, `CLERK_WEBHOOK_SECRET`, `FRONTEND_URL`,
  `CORS_ALLOWED_ORIGINS`. The schema-owner URL (`MIGRATION_DATABASE_URL`) is
  used only when running migrations and is never given to the deployed API.
- GitHub Actions runs ruff, pyright and pytest against a Postgres service
  container on every pull request.

## Conventions for sub-project #2

Every new tenant-scoped table must:

- carry `tenant_id TEXT NOT NULL REFERENCES tenants(id)`;
- enable and force RLS, with a `tenant_isolation` policy `TO karatcore_app`;
- grant `karatcore_app` only the privileges its routes need;
- be reached only through the tenant-bound session of `TenantContext`;
- have its routes depend on a role requirement and on `require_access`.

The schema-security test enumerates all tables, so a table that misses these
fails the suite.

## SaaS upgrade path (sub-project #4, designed but not built)

The hardening pass settled this design; it is recorded here so that work
starts from it.

- A `subscriptions` table, one row per tenant, mirroring the provider's
  canonical subscription; a `trial_ends_at` on the tenant.
- A `BillingGateway` port with one provider adapter. The provider's state is
  re-read and written under a row lock on every billing webhook, rather than
  trusting event payload order.
- Billing webhooks reuse `webhook_events` (a new `source` value).
- A subscription-aware `AccessPolicy` replaces `OpenAccessPolicy`: entitled
  while the subscription is active, trialing or past due, or while the trial
  has not ended.
- A reconciliation cron replays recent provider events and re-lists Clerk
  memberships to repair missed webhooks.
- Provider choice: Razorpay is the likely fit for India (Stripe India onboards
  new accounts by invitation; recurring card payments fall under RBI e-mandate
  rules; UPI Autopay matters for this market). Decide when that work starts.

## Risks and limits

- **Token revocation window:** up to 60 seconds after sign-out or removal.
- **Stale team list:** see "Identity sync".
- **Rate limiting:** none in the application; rely on Vercel's firewall.
- **Personal data:** only Clerk user ids are stored; names and emails stay in
  Clerk. India's DPDP Act obligations grow once the ERP stores customer data.

## Development workflow

Each feature is built on its own branch and merged to `main` through a pull
request describing what changed and why. A branch is ready when its checks
pass and the feature works end to end for its slice of this spec.
