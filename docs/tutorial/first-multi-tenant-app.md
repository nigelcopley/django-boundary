# Build your first multi-tenant app

This tutorial takes you from an empty Django project to a working
multi-tenant app. You will install django-boundary, define a tenant model,
wire up the middleware, create a tenant-scoped model, provision two tenants,
and prove that each tenant sees only its own data.

Follow the steps in order. Each one ends with a checkpoint so you know it
worked before moving on. By the end you will have a small "court booking"
app where two clubs share one database but never see each other's bookings.

## What you will build

A single Django project with:

- An `Organisation` tenant model (one row per club).
- A `Booking` model that is automatically scoped to the active organisation.
- Middleware that resolves the active organisation from the request.
- Two provisioned tenants, demonstrated in isolation from the shell.

## Prerequisites

- Python 3.10 or newer.
- PostgreSQL 14 or newer, running and reachable. Boundary's ORM filtering
  works on any database, but this tutorial uses PostgreSQL because the
  database-level Row Level Security layer requires it.
- Comfort with Django models, settings, and `manage.py`.

## Step 1: Create the project and install boundary

Create a fresh project and install Django, boundary, and a PostgreSQL driver.

```bash
mkdir courts && cd courts
python -m venv .venv
source .venv/bin/activate
pip install django django-boundary "psycopg[binary]"
django-admin startproject config .
python manage.py startapp clubs
python manage.py startapp bookings
```

You now have a `config/` settings package and two apps: `clubs` (which will
hold the tenant model) and `bookings` (which will hold tenant-scoped data).

**Checkpoint:** `pip show django-boundary` prints the installed version, and
`ls` shows `manage.py`, `config/`, `clubs/`, and `bookings/`.

## Step 2: Point Django at PostgreSQL and register the apps

Open `config/settings.py`. Replace the default SQLite `DATABASES` block with a
PostgreSQL connection, and add the three apps plus `boundary` to
`INSTALLED_APPS`.

```python
# config/settings.py
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "boundary",
    "clubs",
    "bookings",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "courts",
        "USER": "postgres",
        "PASSWORD": "postgres",
        "HOST": "localhost",
        "PORT": "5432",
    }
}
```

Create the database if it does not already exist:

```bash
createdb courts
```

**Checkpoint:** `python manage.py check` runs. You will see boundary system
check errors at this stage (for example `boundary.E001` because
`BOUNDARY_TENANT_MODEL` is not set yet). That is expected. We fix it in the
next steps.

## Step 3: Define the tenant model

A tenant is the thing data belongs to. Here, each club is a tenant. Subclass
`AbstractTenant`, which provides the fields boundary expects: `name`, `slug`,
`region`, `is_active`, `created_at`, and `updated_at`.

```python
# clubs/models.py
from django.db import models

from boundary.models import AbstractTenant


class Organisation(AbstractTenant):
    # Inherits: name, slug, region, is_active, created_at, updated_at.
    plan = models.CharField(max_length=50, default="free")
```

You only need to add fields specific to your domain. The `plan` field above
is an example; you can leave the class body empty if you have nothing to add.

**Checkpoint:** the file imports without error:
`python -c "import django; django.setup()" ` is not needed here, just move on;
the next step makes the model usable.

## Step 4: Configure boundary settings

Tell boundary which model is the tenant, and how to resolve the active tenant
from an incoming request. Add the following to the bottom of
`config/settings.py`.

```python
# config/settings.py

# Dotted "app_label.ModelName" path to your tenant model.
BOUNDARY_TENANT_MODEL = "clubs.Organisation"

# Strict mode raises if you query a tenant-scoped model with no active
# tenant. Keep this on: it catches accidental cross-tenant leaks during
# development. It is the default, shown here for clarity.
BOUNDARY_STRICT_MODE = True

# Resolve the tenant from the subdomain, e.g. acme.localhost -> slug "acme".
# First resolver to return a tenant wins.
BOUNDARY_RESOLVERS = [
    "boundary.resolvers.SubdomainResolver",
]
```

`SubdomainResolver` looks up the tenant by `slug` from the request subdomain
by default. We will exercise it from the shell rather than over HTTP in this
tutorial, but configuring it now means the system checks pass.

**Checkpoint:** `python manage.py check` no longer reports `boundary.E001`.
You may still see `boundary.E004` because the middleware is not registered
yet. That is the next step.

## Step 5: Add the middleware

The middleware resolves the active tenant on every request and sets it in
context for the duration of that request. Add `TenantMiddleware` near the top
of `MIDDLEWARE`, before the session and authentication middleware.

```python
# config/settings.py
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "boundary.middleware.TenantMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
```

**Checkpoint:** `python manage.py check` passes with no boundary errors. If
you see `boundary.E006` errors about missing Row Level Security, ignore them
for now: we have not created any tenant-scoped tables yet.

## Step 6: Create a tenant-scoped model

Now add the data that belongs to each club. Subclass `TenantModel`. This adds
a non-nullable `tenant` foreign key to your `Organisation`, swaps in a manager
that filters every query by the active tenant, and auto-populates the tenant
on save.

```python
# bookings/models.py
from django.db import models

from boundary.models import TenantModel


class Booking(TenantModel):
    # Inherited from TenantModel:
    #   tenant   -> ForeignKey to clubs.Organisation (auto-populated on save)
    #   objects  -> TenantManager, auto-filters by the active tenant
    #   unscoped -> plain Manager, sees every tenant's rows
    court = models.IntegerField()
    start_time = models.DateTimeField()

    def __str__(self):
        return f"Court {self.court} at {self.start_time:%Y-%m-%d %H:%M}"
```

You do not declare the `tenant` field yourself. `TenantModel` provides it,
pointed at whatever `BOUNDARY_TENANT_MODEL` names.

**Checkpoint:** `python manage.py makemigrations` reports new migrations for
both `clubs` and `bookings`, and the `bookings` migration includes a `tenant`
foreign key on `Booking`.

## Step 7: Run migrations

Create and apply the database tables.

```bash
python manage.py makemigrations
python manage.py migrate
```

**Checkpoint:** `migrate` finishes without errors. The `clubs_organisation`
and `bookings_booking` tables now exist in the `courts` database.

## Step 8: Provision two tenants

Use the `boundary_provision` management command to create tenant rows. It
prints the new tenant's primary key on success.

```bash
python manage.py boundary_provision --name "Acme Tennis" --slug acme
python manage.py boundary_provision --name "Globex Sports" --slug globex
```

Each command prints a primary key, for example:

```
1
2
```

**Checkpoint:** two organisations exist. Confirm with:

```bash
python manage.py shell -c "from clubs.models import Organisation; print(list(Organisation.objects.values_list('slug', flat=True)))"
```

You should see `['acme', 'globex']`.

## Step 9: Demonstrate isolation in the shell

This is the payoff. Open a shell and create bookings under each tenant, then
prove each tenant sees only its own.

```bash
python manage.py shell
```

```python
from django.utils import timezone

from boundary.context import TenantContext
from clubs.models import Organisation
from bookings.models import Booking

acme = Organisation.objects.get(slug="acme")
globex = Organisation.objects.get(slug="globex")

# Create two bookings for Acme. Note we never set booking.tenant: it is
# auto-populated from the active context on save.
with TenantContext.using(acme):
    Booking.objects.create(court=1, start_time=timezone.now())
    Booking.objects.create(court=2, start_time=timezone.now())

# Create one booking for Globex.
with TenantContext.using(globex):
    Booking.objects.create(court=1, start_time=timezone.now())

# Each tenant sees only its own rows.
with TenantContext.using(acme):
    print("Acme sees:", Booking.objects.count())      # -> 2

with TenantContext.using(globex):
    print("Globex sees:", Booking.objects.count())    # -> 1
```

You should see:

```
Acme sees: 2
Globex sees: 1
```

The same `Booking.objects.count()` call returns a different result depending
on the active tenant. No `filter(tenant=...)` anywhere. That is automatic
ORM-level isolation.

### Confirm strict mode is protecting you

Still in the shell, query with no active tenant. Strict mode refuses rather
than silently returning every club's data.

```python
from boundary.exceptions import TenantNotSetError

try:
    Booking.objects.count()
except TenantNotSetError as exc:
    print("Blocked:", exc)
```

You should see a `Blocked:` message explaining that no tenant is active. This
is the safety net that stops an accidental unscoped query from leaking data
across tenants in production.

### See across all tenants on purpose

For genuine cross-tenant work (platform analytics, an admin dashboard), use
the `unscoped` manager. It bypasses filtering and is never the default, so you
have to ask for it explicitly.

```python
print("All bookings, all clubs:", Booking.unscoped.count())   # -> 3
```

You should see `3`. Exit the shell with `exit()`.

## You did it

You now have a working multi-tenant Django app:

- One `Organisation` tenant model.
- A `Booking` model scoped automatically to the active tenant.
- Middleware that resolves the tenant per request.
- Two tenants whose data is isolated at the ORM layer, with strict mode
  catching unscoped queries.

## Where to go next

Now that the basics work, layer in the production concerns:

- [Set up a tenant model](../how-to/set-up-a-tenant-model.md) for the full
  range of tenant model options, including using a plain model instead of
  `AbstractTenant`.
- [Choose and order resolvers](../how-to/choose-and-order-resolvers.md) to
  resolve tenants from headers, JWT claims, or sessions, and to understand
  why resolver ordering is a security decision.
- [Write tenant-safe tests](../how-to/write-tenant-safe-tests.md) using
  `set_tenant()`, `tenant_factory()`, and `TenantTestMixin`.
- [Run Celery tasks with tenant context](../how-to/run-celery-tasks-with-tenant-context.md)
  so background jobs operate on the right tenant.
- [Deploy multi-region](../how-to/deploy-multi-region.md) to route each
  tenant's data to a geographically distinct database for residency
  compliance.

For database-level enforcement (Row Level Security), the full settings table,
and the complete API surface, see the
[README](../../README.md). RLS is the second layer of defence that catches
raw SQL and ORM bugs the application layer might miss; add it before going to
production.
