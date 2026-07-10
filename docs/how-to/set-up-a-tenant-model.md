# Set up a tenant model

## Goal

Define the tenant model that every scoped row points to, register it with
`BOUNDARY_TENANT_MODEL`, and choose between subclassing `AbstractTenant` or
supplying your own fully custom model.

## Prerequisites

- `django-boundary` installed and in `INSTALLED_APPS`.
- A Django app to hold the tenant model (the examples use an app called
  `tenants`).
- Decided what a "tenant" means in your domain: an organisation, a workspace,
  a merchant, a department, and so on. This becomes one row per tenant.

You do not need the middleware or any scoped models in place yet. The tenant
model is the foundation everything else references.

## Steps

### 1. Choose your approach

There are two ways to define the tenant model. Both produce a concrete model
that `BOUNDARY_TENANT_MODEL` points at.

- **Subclass `AbstractTenant`** when you are happy with the conventional fields
  (`name`, `slug`, `region`, `is_active`, `created_at`, `updated_at`). This is
  the fastest path and what the resolvers and region routing expect by default.
- **Write a fully custom model** when you need different field names, an
  existing table, or fields boundary does not provide. Boundary places no
  hard requirement on the tenant model's shape: it only needs a primary key.
  The default resolvers and region routing read specific fields, so you must
  either keep those field names or point the relevant settings at your own.

### 2a. Minimal tenant model with `AbstractTenant`

`AbstractTenant` is an abstract base. Subclass it and add nothing more for the
common case.

```python
# tenants/models.py
from boundary.models import AbstractTenant


class Organisation(AbstractTenant):
    pass
```

This gives you these fields, defined on `AbstractTenant`:

| Field        | Type            | Notes                                    |
| ------------ | --------------- | ---------------------------------------- |
| `name`       | `CharField`     | `max_length=200`. Used by `__str__`.     |
| `slug`       | `SlugField`     | `unique=True`. Used by `SubdomainResolver` by default. |
| `region`     | `CharField`     | `max_length=50`, `blank=True`, `default=""`. Read by region routing. |
| `is_active`  | `BooleanField`  | `default=True`.                          |
| `created_at` | `DateTimeField` | `auto_now_add=True`.                     |
| `updated_at` | `DateTimeField` | `auto_now=True`.                         |

The base also sets `Meta.ordering = ["name"]` and a `__str__` that returns
`self.name`. You can add your own fields alongside these:

```python
# tenants/models.py
from django.db import models

from boundary.models import AbstractTenant


class Organisation(AbstractTenant):
    billing_email = models.EmailField(blank=True)
    plan = models.CharField(max_length=20, default="free")
```

For the full field reference, see the
[AbstractTenant section in the README](../../README.md#abstracttenant).

### 2b. Fully custom tenant model (no `AbstractTenant`)

If `AbstractTenant`'s fields do not fit, define a plain `models.Model`. Boundary
treats whatever you point `BOUNDARY_TENANT_MODEL` at as the tenant.

```python
# tenants/models.py
from django.db import models


class Account(models.Model):
    company_name = models.CharField(max_length=200)
    handle = models.SlugField(unique=True)
    data_region = models.CharField(max_length=50, default="")
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.company_name
```

Because the default resolvers and region routing read specific field names, tell
boundary which of your fields to use:

```python
# settings.py

# SubdomainResolver looks up the tenant by this field. Default: "slug".
BOUNDARY_SUBDOMAIN_FIELD = "handle"

# Region routing reads this field off the tenant. Default: "region".
BOUNDARY_REGION_FIELD = "data_region"
```

There is no setting for `is_active`; boundary does not filter on it for you.
If you want inactive tenants excluded, enforce that in your resolver or
provisioning logic.

See the [settings reference](../reference/settings.md) for the complete list of
fields the resolvers and routing read.

### 3. Register the model

Point `BOUNDARY_TENANT_MODEL` at your concrete model using the
`"app_label.ModelName"` dotted string, exactly as you would for
`AUTH_USER_MODEL`.

```python
# settings.py
BOUNDARY_TENANT_MODEL = "tenants.Organisation"
```

This setting is required. Boundary raises a system check error
(`boundary.E001`) at startup if it is missing or does not resolve.

### 4. Create and run migrations

The tenant model is a normal Django model, so it needs a migration.

```bash
python manage.py makemigrations tenants
python manage.py migrate
```

### 5. Resolve the model in code with `get_tenant_model()`

Never import the concrete tenant model directly into reusable code. Use the
helper, which resolves `BOUNDARY_TENANT_MODEL` lazily through the app registry,
the same way `django.contrib.auth.get_user_model()` works.

```python
from boundary.conf import get_tenant_model

Tenant = get_tenant_model()
org = Tenant.objects.create(name="Acme", slug="acme")
```

`get_tenant_model()` raises `LookupError` if `BOUNDARY_TENANT_MODEL` is unset,
so it doubles as a clear failure if configuration is missing.

## Verify it worked

Run a shell and confirm the model resolves and creates rows:

```python
python manage.py shell
>>> from boundary.conf import get_tenant_model
>>> Tenant = get_tenant_model()
>>> Tenant
<class 'tenants.models.Organisation'>
>>> org = Tenant.objects.create(name="Acme", slug="acme")
>>> str(org)
'Acme'
>>> org.is_active        # AbstractTenant default
True
```

Then run the system checks; a correctly registered model produces no
`boundary.E001`:

```bash
python manage.py check
```

## Common pitfalls

- **Setting `BOUNDARY_TENANT_MODEL` to a class instead of a string.** It must be
  the dotted `"app_label.ModelName"` path, not the imported class.
- **Importing the concrete tenant model in shared or package code.** This
  creates import-order and circular-import problems. Use `get_tenant_model()`.
- **Forgetting field-name settings on a custom model.** Without
  `AbstractTenant`, the default `SubdomainResolver` still looks for a `slug`
  field and region routing still reads a `region` field. Override
  `BOUNDARY_SUBDOMAIN_FIELD` and `BOUNDARY_REGION_FIELD` if your names differ.
- **Expecting `is_active` to filter automatically.** It does not. Boundary
  stores the flag but leaves enforcement to your code.
- **Adding `AbstractTenant` to `INSTALLED_APPS` migrations.** It is abstract and
  has no table of its own; only your concrete subclass gets a migration.

## Related

- [README: AbstractTenant](../../README.md#abstracttenant): full field
  reference.
- [Settings reference](../reference/settings.md): every `BOUNDARY_` option,
  including `BOUNDARY_SUBDOMAIN_FIELD` and `BOUNDARY_REGION_FIELD`, with
  defaults and trade-offs.
- [Add boundary to an existing app](./add-boundary-to-an-existing-app.md): once
  the tenant model exists, point scoped models at it with `TenantModel`,
  `TenantMixin`, or `make_tenant_mixin()`.
