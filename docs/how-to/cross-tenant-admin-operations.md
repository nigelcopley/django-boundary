# Run cross-tenant admin operations

## Goal

Operate across every tenant at once, or against a tenant other than the one in the current context. This covers the deliberate escape hatches that boundary provides: the `unscoped` manager, the `all_regions()` context manager, the `boundary_run` and `boundary_run_all` management commands, and the row-level security (RLS) admin bypass flag.

These are sharp tools. By default boundary filters every query to the active tenant so a missing context is fail-closed. The escape hatches turn that protection off on purpose. Read the safety notes in each section before using them in production code.

## Prerequisites

- boundary is installed and `BOUNDARY_TENANT_MODEL` is set. See the [README](../../README.md) for setup.
- Your models inherit from `TenantMixin`, `TenantModel`, or a mixin produced by `make_tenant_mixin()`, so they expose both `objects` (tenant-filtered) and `unscoped` (unfiltered) managers.
- For the regional sections, `BOUNDARY_REGIONS` is configured and `boundary.routing.RegionalRouter` is in `DATABASE_ROUTERS`.
- For the RLS bypass section, you have applied the RLS policies via the migration operations. See the [README RLS section](../../README.md#row-level-security) for how policies are created.

## Steps

### 1. Read or write across all tenants with the `unscoped` manager

Every tenant-scoped model has an `unscoped` manager alongside the default `objects` manager. `unscoped` is a plain manager that does not apply the active-tenant filter, so it returns rows for all tenants regardless of context.

```python
from myapp.models import Booking

# Default manager: filtered to the active tenant (or fail-closed if none).
Booking.objects.count()

# Unscoped manager: every row, every tenant.
Booking.unscoped.count()
Booking.unscoped.filter(court=1)
total = Booking.unscoped.aggregate(total=Sum("price"))
```

Use this for platform-level reporting, analytics, and admin dashboards where you genuinely need to see across tenants.

When you create rows through `unscoped`, boundary does not auto-populate the tenant field from context. You must pass the tenant explicitly:

```python
# Correct: explicit tenant.
Booking.unscoped.create(court=1, tenant=some_tenant)

# Wrong: no tenant, no auto-populate. Raises IntegrityError on a non-null FK.
Booking.unscoped.create(court=1)
```

`unscoped.bulk_create()` behaves the same way: it skips auto-populate, so each object must already carry its tenant.

> Safety: a query on `unscoped` is a query with no isolation. Treat any code path that reaches `unscoped` as privileged. Keep it out of request handlers that serve tenant users, and prefer the tenant-filtered `objects` manager everywhere else.

### 2. Iterate across regional databases with `all_regions()`

In a multi-region deployment, tenant data is sharded across database aliases by region. A single unscoped query only hits the database it is routed to, so to aggregate across regions you must query each alias.

`all_regions()` yields the configured region aliases (or `["default"]` when `BOUNDARY_REGIONS` is unset), so you can loop over them with `.using()`:

```python
from boundary.routing import all_regions
from myapp.models import Booking

grand_total = 0
with all_regions() as aliases:
    for alias in aliases:
        grand_total += Booking.unscoped.using(alias).count()
```

To pin queries to one specific region instead of routing by the active tenant, use `specific_region()`:

```python
from boundary.routing import specific_region

with specific_region("eu-west"):
    bookings = Booking.unscoped.all()  # hits the eu-west database
```

An unknown region key in `specific_region()` falls back to the `default` alias rather than raising.

> Safety: combine `all_regions()` with `unscoped` only for cross-tenant aggregation. If you forget `.using(alias)`, you silently aggregate just one region and under-report.

### 3. Run a command for one specific tenant with `boundary_run`

`boundary_run` activates a tenant context, then calls another management command inside it. Use it to run a per-tenant job against a single tenant from the shell.

```bash
python manage.py boundary_run --tenant club-a send_reminders --dry-run
```

- `--tenant` is required. It accepts the tenant PK or slug. boundary tries PK first, then slug, and raises `CommandError` if neither matches.
- Everything after the inner command name is forwarded verbatim to that command (`--dry-run` above goes to `send_reminders`).

The inner command runs inside `TenantContext.using(tenant)`, so any boundary-aware code it executes is correctly scoped to that one tenant.

### 4. Run a command for every active tenant with `boundary_run_all`

`boundary_run_all` resolves every active tenant (`is_active=True`), then runs the inner command once per tenant, each inside its own tenant context.

```bash
# Sequentially, against all active tenants.
python manage.py boundary_run_all send_reminders

# 4 parallel workers, only EU tenants, machine-readable output.
python manage.py boundary_run_all send_reminders --parallel 4 --region eu-west --json
```

Options:

- `--parallel N`: number of concurrent workers (default `1`). Values above `1` run each tenant in a separate process via a multiprocessing pool.
- `--region REGION`: limit to tenants whose region field matches `REGION`. The field name comes from `BOUNDARY_REGION_FIELD` (default `region`).
- `--exclude PK`: skip a tenant by PK. Repeat the flag to exclude several: `--exclude 7 --exclude 9`.
- `--json`: emit one NDJSON object per tenant for piping into other tools.

Output is one line per tenant. In human mode, successes print `[OK] <slug>` to stdout and failures print `[FAIL] <slug>: <error>` to stderr. In `--json` mode each line is an object with `tenant` and `status` keys (and `error` on failure):

```json
{"tenant": "club-a", "status": "ok"}
{"tenant": "club-b", "status": "error", "error": "..."}
```

A failure in one tenant does not abort the run. Each tenant is isolated, so the loop continues and the failure is reported per tenant.

> Safety: `boundary_run_all` touches every active tenant. Test the inner command with `boundary_run --tenant <one>` first, then widen to `boundary_run_all`. When using `--parallel`, the inner command must be safe to run in multiple processes at once.

### 5. Bypass RLS for trusted maintenance work

When PostgreSQL row-level security is enabled, the database itself rejects rows outside the active tenant, even for the `unscoped` manager, unless the connection is a superuser or the admin bypass flag is set. boundary installs an admin bypass policy that lifts isolation when the `app.boundary_admin` session variable is `'true'`.

```python
from django.db import connection

with connection.cursor() as cur:
    cur.execute("SELECT set_config('app.boundary_admin', 'true', true)")
    # Queries in this transaction now see and write rows for all tenants,
    # even with FORCE ROW LEVEL SECURITY on the table.
```

The variable name is configurable via `BOUNDARY_ADMIN_FLAG_VAR` (default `app.boundary_admin`). The third argument to `set_config` (`true`) scopes the flag to the current transaction, so it clears automatically on commit or rollback.

> Safety: the admin flag disables the database's last line of defence. Set it only inside a tightly scoped transaction for trusted maintenance, never on a connection that serves tenant traffic, and never leave it set across requests.

## Verify it worked

- `unscoped`: with two tenants each owning one row, assert `Model.unscoped.count() == 2` while a single tenant is active, and `Model.objects.count() == 1`.
- `all_regions()`: with `BOUNDARY_REGIONS` set to three regions, confirm the yielded aliases match the configured keys; with it unset, confirm you get `["default"]`.
- `boundary_run`: run it with a harmless inner command such as `python manage.py boundary_run --tenant <slug> showmigrations --list` and confirm no `CommandError`.
- `boundary_run_all`: run `python manage.py boundary_run_all showmigrations --json` and confirm one JSON line per active tenant, each with `status: ok`.
- RLS bypass: with isolation applied and rows for two tenants, set `app.boundary_admin` to `'true'` in a transaction and confirm a raw `SELECT count(*)` returns the full count instead of the per-tenant count.

## Common pitfalls

- Calling `unscoped.create()` or `unscoped.bulk_create()` without setting the tenant explicitly: auto-populate is skipped, so a non-null FK raises `IntegrityError`. Always pass `tenant=...`.
- Expecting `unscoped` to cross regions: it only queries the database it routes to. Wrap it in `all_regions()` and use `.using(alias)` to span shards.
- Reaching for `unscoped` in tenant-facing request code: this defeats isolation. Use the default `objects` manager and a proper `TenantContext` instead. See [How tenant resolution works](../explanation/how-resolution-works.md).
- Forgetting that `boundary_run_all` only targets `is_active=True` tenants. Inactive tenants are skipped silently.
- Passing inner-command flags before the inner command name in `boundary_run`. The inner command name comes first, then its arguments.
- Leaving the `app.boundary_admin` flag set outside a short transaction. Always scope it with the transaction-local form of `set_config`.

## Related

- [README](../../README.md) for the full settings reference, RLS setup, and the regional routing model.
- README sections on the [`unscoped` manager](../../README.md#models), [`all_regions` / `specific_region`](../../README.md#multi-region-with-data-residency), and the [`boundary_run` / `boundary_run_all` commands](../../README.md#management-commands).
