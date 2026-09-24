# CLAUDE.md section draft: Phase 7 WP-B6 (route decisions and the billing page)

Draft for the orchestrator to fold into the release's single top section.
Written in the file's own voice; nothing here edits `claude.md` directly.

## A ROUTE NOTHING CALLS IS DELETED OR DECLARED, NEVER LEFT

The route audit found a report library, an Owner console that duplicated the
Provider Portal, a raw-numbers calibration view, a divergence queue, a
telemetry counter, a duplicate outreach decorator, a manual web-search reset,
the company approval-levels and email-template editors, a grant-access route
and a public billing config route, none of which any screen called. A route
with no caller looks exactly like one somebody is about to build a screen for,
so nobody deletes it and nobody wires it. Every one is now DELETED or WIRED:

- **Deleted**: `/reports/catalog` and `/reports/generate` with
  `services/reports/`; `GET /admin/tenants`, `GET/PUT/PATCH
  /admin/tenants/{id}`, `GET /admin/staff`, `POST /admin/staff-invites`,
  `GET/PUT /admin/permissions`; `GET /dashboard/.../calibration` (it returned
  the raw D1-D5 numbers to a client) and `GET /dashboard/calibration/divergences`
  with `calibration_view`, `log_calibration_view`, `divergences`,
  `override_rate`; `POST /telemetry/rating-comments-view/{link_id}`;
  `POST /outreach/send-email`; `POST /bd/ai-reach/web-search/reset`;
  `PUT /companies/me/approval-levels`; `GET/POST/PUT
  /companies/me/email-templates`; `POST /candidates/links/{id}/grant-access`;
  `GET /billing/config`. `tests/test_dead_routes_removed.py` pins them against
  the route table, the capability constants, the migrated database and a
  whitespace-normalised sweep.
- **Wired**: `GET /billing/ledger` is the billing page's Credit statement,
  paged; `POST /billing/cancel` is the page's Cancel subscription control.
- **Declared**: `tests/test_route_callers.py` requires every admin, billing
  and telemetry route to have a frontend caller, or an entry in
  `OPERATOR_SURFACE` (no screen by design: the audit-log read, the RBAC 7.1
  Super Admin transfer, the retyped-name tenant delete, the LLM stats read) or
  `EXTERNAL_CALLERS` (the Razorpay webhook), each with a sentence of reason.
  A declared operator route that a screen starts calling FAILS too: the
  declaration would be a false statement about who uses it.

A calibration DIVERGENCE is still recorded (`calibration.raise_divergence`,
`calibration_records` plus an audit row). What went is the read surface, not
the record. **SUPERSEDES** the 2026-08-29 D8 paragraph describing an audited
raw-numbers view restricted to Super Admin and HR Manager: no route returns
the raw numbers to a client any more, which is what D3 requires.

## THE APPROVAL CHAIN IS DELETED, NOT DORMANT

`approval_fsm` keeps only the direct-publish record `POST /jobs/{id}/publish`
writes: every level of the old chain logged as an explicit `skipped`
`job_approvals` row and the `ratified_at` stamp every visibility check reads.
The planner, its persistence wrappers, its typed errors, the company levels
route and two capabilities (`approve_job`, `configure_approval_levels`) are
gone; `manage_email_templates` went with the email-template editor, its only
reader. **Migration `0129_route_scrap` deletes all three capabilities'
`role_permissions` rows, global AND per-tenant**, because a capability and
its seeding are one change and so is its removal; its downgrade re-seeds the
global grants and says in its docstring why per-tenant rows cannot come back.

"The FSM is dormant, not deleted" (2026-07-24, restated in `capabilities.py`)
is **SUPERSEDED**: that sentence kept a second, unreachable publication path
alive for two months. `companies.approval_levels_config` stays as a column: it
holds data a customer typed.

`_can_see_pre_ratified` (api/jobs.py) now reads CREATE_JOB or PUBLISH_JOB,
where it read the two deleted capabilities: somebody who may publish a draft
has to be able to read it.

## A FAILURE TO RECORD A WEBHOOK IS NOT A DUPLICATE

The Razorpay webhook dedupes by inserting the delivery into `webhook_events`,
UNIQUE on (provider, event_id). It caught ANY failure of that insert with
`except Exception` and answered 200 `{"status": "duplicate"}`, so a DataError
or a lost connection told Razorpay a paid `subscription.charged` had been
handled and it was never retried. **The dedupe is now `INSERT ... ON CONFLICT
ON CONSTRAINT uq_webhook_events_provider_id DO NOTHING RETURNING id`**: the
database absorbs the duplicate and nothing else, and every other failure
answers 5xx, which is the retry that gets the charge granted. Same shape
`conversations` already uses. `tests/test_billing_webhook_errors.py` provokes
the failure for real (an event type wider than its column) and was
mutation-checked against the old handler.

## THE KEY ID NEVER CAME FROM GET /billing/config

The 2026-07-28 line "The browser gets the Key ID from `GET /billing/config` at
runtime" is **SUPERSEDED**, and was false before this release: nothing called
that route. The browser receives the Key ID on the responses that open
Checkout (`/subscribe`, `/purchase`) and on `/overview`. The route, its
schema, the one tenant-free cache key in the billing module
(`tests/test_cache_tenant_keying.GLOBAL_BY_DESIGN` lost its billing entry) and
`TTL_PRICING_PLANS` are deleted, and the comments that repeated the claim in
`.env.example`, `core/config.py`, `frontend/.dockerignore`,
`infra/docker-compose.yml` and `infra/modules/ecs` are corrected.

## A STATEMENT, NOT A NUMBER, AND A WAY OUT OF A SUBSCRIPTION

- **The Credit statement pages `GET /billing/ledger`** 25 rows at a time,
  asking for one row more than a page to know whether an older page exists
  (no count query). It names every event in words, including the `expiry`
  rows the frontend type union had been missing since 2026-09-22, and it
  NEVER renders the application a consumption row refers to: the row's link
  id is for reconciliation, and a statement line naming a candidate would put
  candidate data on a page every `view_billing` holder reads. The grant row's
  label is "Credits added": a credit pack purchase is a grant too, so the old
  "Monthly top up" was wrong for every pack.
- **Cancel subscription** is behind `manage_billing` (the route's own gate),
  shown only while a subscription is live, and behind `ConfirmButton`. The
  dialog promises exactly what the route does: no renewal after the current
  period, no further charge, credits kept.

## THE ONE OWNER WRITE THAT SURVIVED THE SCRAP HAD NEVER WORKED

- **`POST /admin/tenants` (the Provider's "create customer") raised
  AttributeError after its flush** on every call: its cache invalidation read
  `body.tenant_id` and `body.entries`, fields `TenantCreateIn` does not have,
  copied from the permission editor this release deletes. No customer could be
  onboarded from the console. It had no test; `tests/test_admin_create_tenant.py`
  now reads the tenant back from a second session.
- **Its invitation, the grant's release of held assessments and the
  failed-payment email are dispatched AFTER THE COMMIT**
  (`dispatch_after_commit`), so a rolled-back request mails nothing about a
  row that was never stored. Their `LEGACY_CALL_SITES` entries are gone.
