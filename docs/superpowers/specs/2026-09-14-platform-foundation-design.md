# KaratCore Platform Foundation — Design Spec

Date: 2026-09-14
Status: Approved by user, pending implementation plan

## Context

KaratCore is a multi-tenant SaaS ERP for gold/jewellery stores in India. Multiple
shops sign up independently and each gets an isolated instance of the ERP under
one account ("tenant" = one shop).

The full product is too large for a single spec, so it is split into sub-projects,
built in this order:

1. **Platform foundation** (this spec) — multi-tenancy, auth, shop signup/onboarding,
   tenant isolation, subscription billing for KaratCore itself.
2. Core ERP MVP (per tenant) — inventory (gold/silver, purity/weight/making charges),
   GST-compliant billing/POS, customer & supplier ledgers, old-gold exchange.
3. Extended modules — repair job tracking, BIS hallmark/HUID tracking, purchase
   orders, multi-branch per tenant, reports/dashboards.
4. KaratCore platform ops — usage analytics across tenants, support tooling.

This spec covers **#1 only**. Everything else is explicitly out of scope here.

## Goals

- A shop owner can sign up, create their shop account, and invite staff.
- Each shop's data is fully isolated from every other shop's data.
- KaratCore can charge shops a subscription (trial → paid) using Stripe.
- Three roles exist within a shop: Owner, Accountant, Staff, with different
  permissions, enforced on the backend.
- The foundation is stable enough that sub-project #2 (core ERP) can be built
  directly on top of it without revisiting tenancy/auth/billing decisions.

## Non-goals (deferred to later sub-projects)

- Any actual ERP functionality (inventory, invoicing, ledgers, gold rates, etc.)
- Multi-branch support within a single shop/tenant
- A platform admin dashboard for KaratCore's own staff
- Cross-tenant analytics/reporting
- Background jobs / scheduled tasks (e.g. daily gold rate refresh) — revisit
  backend hosting choice if/when these become necessary

## Architecture

- **Frontend:** Vite + React + **TypeScript** + Tailwind CSS, deployed on Vercel
  as a static SPA.
- **Backend:** FastAPI (Python), deployed as Vercel serverless functions
  (stateless request/response only — no long-running processes or websockets
  in this sub-project).
- **Database:** Postgres via Neon (Vercel Marketplace).
- **Auth:** Clerk. One Clerk **Organization** = one shop/tenant. Clerk custom
  org roles map to Owner / Accountant / Staff. Frontend uses Clerk's React SDK;
  FastAPI verifies Clerk-issued JWTs against Clerk's JWKS on every request.
- **Billing:** Stripe, for KaratCore's own subscription revenue (charging shops
  to use KaratCore). This is separate from the GST billing a shop issues to its
  own customers, which belongs to sub-project #2.
- **ORM/migrations:** SQLAlchemy + Alembic. **Validation:** Pydantic schemas.

## Code structure & type safety

- **Backend (FastAPI):** organized in classes with clear responsibilities —
  SQLAlchemy models (data), Pydantic schemas (I/O validation), a
  repository-per-entity layer (tenants, memberships, subscriptions) for data
  access, and a service layer holding business logic (onboarding, webhook
  processing, access-control resolution). Routers stay thin and delegate to
  services. Full type hints throughout, checked with **pyright** (mypy-strict
  is a viable alternative but pyright matches the LSP already installed).
- **Frontend (React):** functional components + hooks — the current React
  idiom, not forced into class components. TypeScript's `strict` mode is
  enabled for compile-time type checking. Non-component logic (the API
  client, auth/session helpers, domain logic) is organized as classes/modules
  rather than scattered functions, keeping the OOP principle where it fits
  naturally without fighting React's component model.
- Both `pyright` (backend) and `tsc --noEmit` (frontend) must pass with zero
  errors as part of the definition of done for each feature branch.

Three route/page groups in the frontend:
- `(marketing)` — public signup/pricing pages
- `(onboarding)` — create shop → invite team → (post-trial) Stripe checkout
- `(app)` — the tenant-scoped ERP itself, gated on an active Clerk org +
  non-lapsed subscription status

## Tenant isolation

Shared Postgres database, shared schema. Every tenant-scoped table carries a
`tenant_id` column (= Clerk Organization ID), enforced by **Postgres
Row-Level Security (RLS)** policies — not just application-level filtering.
FastAPI sets the current tenant for each request/transaction from the verified
JWT before any query runs.

Rationale: KaratCore expects many small tenants (individual shops). Schema- or
database-per-tenant would add provisioning/migration overhead disproportionate
to each tenant's size, and would make platform-level support/reporting harder.
Shared-schema + RLS is the standard SaaS pattern at this scale and keeps
cross-tenant admin queries possible later without weakening isolation.

## Data model (this sub-project)

- `tenants` — id (= Clerk org id), shop_name, slug, plan, subscription_status,
  created_at
- `memberships` — user_id (Clerk user id), tenant_id, role
  (`owner` | `accountant` | `staff`), kept in sync with Clerk via webhooks
- `subscriptions` — tenant_id, stripe_customer_id, stripe_subscription_id,
  status, current_period_end

`memberships` duplicates what Clerk already knows about org membership/roles;
this is deliberate — it lets the backend query roles directly in SQL (e.g. for
RLS policies or joins) without a round-trip to Clerk's API on every request.

## Onboarding flow

1. User signs up via Clerk (email/password or OAuth).
2. User creates a shop (Clerk Organization) → becomes Owner.
3. Clerk webhook → FastAPI creates the `tenants` row, creates a Stripe
   customer, and starts a **14-day trial** (no card required upfront).
4. Owner can invite Accountant/Staff members via Clerk org invites; a
   corresponding `memberships` row is created/updated via webhook.
5. When the trial ends, the app prompts for payment via Stripe Checkout.
   Until payment succeeds, `subscription_status` is `past_due`/`inactive` and
   the `(app)` area shows a "reactivate billing" screen instead of the ERP.

Plan structure kept minimal for this sub-project: one paid plan + trial.
Multiple pricing tiers can be added later without changing this architecture.

## Access control

A FastAPI dependency resolves, per request: authenticated user → active
tenant → role. Failure modes:

- No/invalid/expired Clerk session → `401`
- Valid session, role lacks permission for the endpoint → `403`
- Valid session, but the requested resource belongs to a different tenant →
  `404` (do not leak existence of other tenants' resources)

Role permissions for this sub-project: only Owner and Accountant can view/manage
billing and subscription settings; Staff cannot. (Finer-grained ERP permissions
are defined in sub-project #2.)

## Webhook handling

Clerk (org/membership changes) and Stripe (subscription lifecycle) webhooks are
the two external sources of truth. Both are:

- Signature-verified before processing
- Handled idempotently (safe to receive the same event twice)
- Logged on failure rather than silently dropped — a missed webhook can lock a
  shop out of the app or leave billing state incorrect, so failures must be
  visible, not swallowed

## Testing strategy

- RLS policies tested against a real Postgres test database (not mocked) —
  the property under test ("tenant A truly cannot read tenant B's rows") is a
  database-level guarantee and must be verified as one.
- Role-permission dependency tests for each of the three roles.
- Webhook handler tests: given a Clerk/Stripe payload, assert the resulting DB
  state (including idempotency — replaying the same event changes nothing
  further).

## Development workflow

Each feature (from the implementation plan) is built on its own branch off
`main` and merged via a pull request with a description covering what changed
and why, rather than committed directly to `main`. A feature branch is ready
to merge once: pyright/tsc pass with zero errors, its tests pass, and the
feature works end-to-end for its slice of this spec.

## Open questions for sub-project #2 planning

- Exact GST/invoicing requirements and old-gold exchange valuation rules
- Whether inventory needs branch-level scoping even before multi-branch ships
  (i.e., should the schema reserve a branch dimension now to avoid a painful
  migration later)
