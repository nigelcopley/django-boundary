# Provision and deprovision tenants

## Goal

Create and remove tenants from the command line using `boundary_provision` and
`boundary_deprovision`, run provisioning and deprovisioning side effects through
the `BOUNDARY_POST_PROVISION_HOOK` and `BOUNDARY_PRE_DEPROVISION_HOOK` hooks, and
export a tenant's data to NDJSON before deleting it.

## Prerequisites

- A registered tenant model: `BOUNDARY_TENANT_MODEL` set and migrated. See
  [Set up a tenant model](./set-up-a-tenant-model.md).
- At least one model made tenant-scoped if you want export and cascade deletion
  to find rows. Deprovision discovers scoped models by walking the app registry
  for `TenantMixin` subclasses (`issubclass(model, TenantMixin)`). This means
  models subclassing `TenantMixin` directly, or via `TenantModel`, are found.
  Models built with `make_tenant_mixin()` (custom FK-field models) are **not**
  `TenantMixin` subclasses, so their rows are not counted, exported, or
  cascade-deleted by `boundary_deprovision`. Delete those rows separately, or
  rely on the database FK `ON DELETE CASCADE` when the tenant row is removed.
- The tenant model's fields. `boundary_provision` writes `name`, `slug`, and
  optionally `region` directly. If your model uses different field names (a fully
  custom model without `AbstractTenant`), pass them through `--extra-fields`
  rather than `--name` / `--slug`.

## Steps

### 1. Provision a tenant

`boundary_provision` calls `TenantModel.objects.create(...)` and prints the new
tenant's primary key to stdout.

```bash
python manage.py boundary_provision --name "Club A" --slug "club-a"
```

The command requires `--name` and `--slug`. `--region` is optional and is only
passed to `create()` when non-empty:

```bash
python manage.py boundary_provision --name "EU Club" --slug "eu-club" --region eu-west
```

Capture the printed PK for scripting:

```bash
TENANT_PK=$(python manage.py boundary_provision --name "Club A" --slug "club-a")
```

### 2. Set fields beyond name, slug, and region with `--extra-fields`

`--extra-fields` takes a JSON object. Its keys are merged into the `create()`
kwargs, so they must match real field names on your tenant model. Use it for
custom fields (for example `plan`, `billing_email`) or for the canonical fields
when your custom model does not use `name` / `slug` / `region`.

```bash
python manage.py boundary_provision \
  --name "Pro Club" \
  --slug "pro-club" \
  --extra-fields '{"plan": "pro", "billing_email": "ops@proclub.example"}'
```

Invalid JSON raises a `CommandError`:

```
CommandError: Invalid JSON in --extra-fields: ...
```

`--extra-fields` defaults to `"{}"`, so you can omit it.

### 3. Run a side effect after provisioning with `BOUNDARY_POST_PROVISION_HOOK`

Point `BOUNDARY_POST_PROVISION_HOOK` at a dotted path to a callable. After the
tenant is created, the command imports it with `import_string` and calls it with
the new tenant instance: `hook(tenant)`. The hook runs before the PK is printed.

```python
# settings.py
BOUNDARY_POST_PROVISION_HOOK = "tenants.hooks.post_provision"
```

```python
# tenants/hooks.py
def post_provision(tenant):
    # Seed default rows, send a welcome email, kick off a Celery job, and so on.
    # Boundary does not activate tenant context for you here, so set it
    # yourself if you need scoped writes.
    from boundary.context import TenantContext

    with TenantContext.using(tenant):
        # ... create per-tenant defaults via scoped managers ...
        ...
```

The hook receives the saved tenant, so `tenant.pk` is populated. If the setting
is unset (the default is `None`), no hook runs.

### 4. Preview a deprovision with `--dry-run`

`boundary_deprovision` resolves the tenant, then reports every tenant-scoped
model and how many rows it would delete, without deleting anything. The tenant is
matched by PK first, then by `slug`.

```bash
python manage.py boundary_deprovision --tenant club-a --dry-run
```

Output is prefixed with `[DRY RUN]`:

```
[DRY RUN] Would delete tenant: Club A
  Booking: 12 rows
  Invoice: 3 rows
```

An unresolvable identifier raises a `CommandError` ("Tenant not found: ...").

Note: the pre-deprovision hook (next step) still runs during a dry run, because
it executes before the dry-run branch. Keep that hook side-effect-free, or guard
its behaviour, if you rely on `--dry-run` being read-only.

### 5. Run a side effect before deletion with `BOUNDARY_PRE_DEPROVISION_HOOK`

`BOUNDARY_PRE_DEPROVISION_HOOK` mirrors the provision hook. It is imported and
called as `hook(tenant)` before any rows are counted, exported, or deleted. Use
it to revoke external resources, cancel subscriptions, or archive to cold
storage.

```python
# settings.py
BOUNDARY_PRE_DEPROVISION_HOOK = "tenants.hooks.pre_deprovision"
```

```python
# tenants/hooks.py
def pre_deprovision(tenant):
    # Cancel billing, delete storage buckets, notify owners, and so on.
    ...
```

If unset (default `None`), no hook runs.

### 6. Export tenant data to NDJSON with `--export`

`--export PATH` streams every tenant-scoped row to a newline-delimited JSON file
before deletion. Each line is one row, queried through the model's `unscoped`
manager so strict mode does not block the read. Rows are streamed with
`.iterator(chunk_size=...)`; tune the chunk size with `--batch-size` (default
`1000`).

```bash
python manage.py boundary_deprovision \
  --tenant club-a \
  --export club-a-backup.ndjson \
  --batch-size 5000 \
  --yes
```

Each line has this shape: a `_model` key (`"app_label.ModelName"`), a `_pk` key
(stringified primary key), and one entry per concrete field, keyed by the field's
`attname` (so foreign keys appear as `<field>_id`). All values are stringified;
`None` is preserved as JSON `null`.

```json
{"_model": "bookings.Booking", "_pk": "42", "id": "42", "court": "1", "tenant_id": "7"}
```

Only models with at least one matching row produce lines, so an empty export file
means the tenant had no scoped data. The file is written before the tenant and
its rows are deleted.

### 7. Delete the tenant

Without `--export`, deprovision counts, confirms, then deletes. Each scoped
model's rows are removed via `model.unscoped.filter(tenant=tenant).delete()`,
then the tenant row itself is deleted. On success it prints
`Tenant <pk> deleted.`

By default the command prompts interactively, listing affected models and asking
you to type `yes`. Skip the prompt in scripts and CI with `--yes`:

```bash
python manage.py boundary_deprovision --tenant club-a --yes
```

Typical full lifecycle: export, then delete, non-interactively:

```bash
python manage.py boundary_deprovision \
  --tenant club-a \
  --export club-a-backup.ndjson \
  --yes
```

## Verify it worked

Provision and confirm the PK and field values:

```bash
python manage.py shell
>>> from boundary.conf import get_tenant_model
>>> from django.core.management import call_command
>>> call_command("boundary_provision", name="Club A", slug="club-a", region="eu-west")
>>> Tenant = get_tenant_model()
>>> t = Tenant.objects.get(slug="club-a")
>>> t.region
'eu-west'
```

Dry-run a deprovision and confirm the tenant still exists:

```bash
python manage.py boundary_deprovision --tenant club-a --dry-run
python manage.py shell -c "from boundary.conf import get_tenant_model; print(get_tenant_model().objects.filter(slug='club-a').exists())"
# True
```

Export, then confirm the NDJSON line count matches the row count and each line
parses:

```bash
python manage.py boundary_deprovision --tenant club-a --export out.ndjson --yes
wc -l out.ndjson
python -c "import json; [json.loads(l) for l in open('out.ndjson')]; print('ok')"
```

After a real deletion, confirm the tenant is gone:

```bash
python manage.py shell -c "from boundary.conf import get_tenant_model; print(get_tenant_model().objects.filter(slug='club-a').exists())"
# False
```

## Common pitfalls

- **Passing class kwargs that are not real fields.** `--extra-fields` keys go
  straight into `Model.objects.create()`. An unknown key raises a `TypeError`
  from Django, not a friendly message.
- **Using `--name` / `--slug` on a custom model that lacks those fields.**
  `boundary_provision` always passes `name` and `slug`. If your tenant model uses
  different names, that create call fails. Add the right fields via
  `--extra-fields` and supply placeholder `--name` / `--slug` values only if your
  model also accepts them, or extend the command.
- **Expecting `--dry-run` to be fully read-only.** The pre-deprovision hook runs
  before the dry-run check, so a hook with side effects executes even on a dry
  run. Make the hook idempotent or side-effect-free.
- **Assuming export captures non-scoped data.** Only models subclassing
  `TenantMixin` (and not abstract) are walked. Plain models, m2m through-rows
  that are not scoped, and the tenant row's own attributes (beyond the model
  scan) are not in the NDJSON. The tenant record itself is deleted but not
  exported as a row.
- **Forgetting `--yes` in automation.** Without it the command blocks on
  `input()` waiting for you to type `yes`; in a non-interactive pipeline this
  hangs or fails.
- **Relying on typed values in the NDJSON.** Every field value is stringified
  (`str(val)`), so numbers, booleans, and dates are exported as strings. Re-cast
  on import.
- **Large tenants and memory.** Export streams with `--batch-size`; lower it if
  rows are wide and memory is tight, raise it to reduce query round-trips.

## Related

- [README: Management Commands](../../README.md#management-commands): quick
  reference for all four commands, including `boundary_run` and
  `boundary_run_all`.
- [README: Settings Reference](../../README.md#settings-reference): full table
  including `BOUNDARY_POST_PROVISION_HOOK` and `BOUNDARY_PRE_DEPROVISION_HOOK`.
- [Set up a tenant model](./set-up-a-tenant-model.md): define and register the
  model these commands operate on.
- [Run Celery tasks with tenant context](./run-celery-tasks-with-tenant-context.md):
  for activating tenant context inside provisioning hooks that write scoped
  rows.
