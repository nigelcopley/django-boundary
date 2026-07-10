# Customise the terminology (merchant, organisation, club)

## Goal

Rename "tenant" everywhere it surfaces in your project, so your code and your
users see your domain's word instead. By the end you will have a model with a
field like `Product.merchant`, views that read `request.merchant`, error
messages that say "No merchant is active", and a 404 body that reads
"Merchant not found".

Boundary exposes three settings and one factory for this:

- `BOUNDARY_TENANT_FK_FIELD`: the foreign key field name on scoped models. This
  is the master switch: the other two default to it.
- `BOUNDARY_TENANT_LABEL`: the human-readable word in error messages, the FK's
  `verbose_name`, and middleware HTTP response bodies.
- `BOUNDARY_REQUEST_ATTR`: the attribute name aliased onto the request object.
- `make_tenant_mixin(fk_field)`: a factory that builds an abstract mixin whose
  FK uses the name you pass, when you want a per-model name rather than a global
  one.

For the exhaustive option table, see the
[settings reference in the README](../../README.md).

## Prerequisites

- `django-boundary` installed, with `TenantMiddleware` in `MIDDLEWARE`.
- A tenant model registered via `BOUNDARY_TENANT_MODEL`. See
  [Set up a tenant model](set-up-a-tenant-model.md) if you have not done this.
- A blank or early-stage schema. Renaming the FK field changes the column name,
  so doing this before you ship migrations is far easier (see Common pitfalls).

## Steps

### 1. Set the master switch

Most projects only need one setting. `BOUNDARY_TENANT_FK_FIELD` flows through to
both the label and the request attribute, so setting it alone renames
everything consistently.

```python
# settings.py
BOUNDARY_TENANT_MODEL = "merchants.Merchant"
BOUNDARY_TENANT_FK_FIELD = "merchant"
```

With this in place, and nothing else changed:

- `BOUNDARY_TENANT_LABEL` resolves to `"merchant"` (it defaults to the FK field).
- `BOUNDARY_REQUEST_ATTR` resolves to `"merchant"` (same default).
- New error messages read `No merchant is active in context.`
- A failed resolution returns a 404 body of `Merchant not found.`
- An inactive tenant returns a 403 body of `Merchant is inactive.`

The label is title-cased automatically for the HTTP response bodies, so you do
not need a separately capitalised setting.

### 2. Give your models the renamed FK with `make_tenant_mixin()`

The built-in `TenantMixin` always uses the field name `tenant`. To get
`Product.merchant`, build a mixin with the factory and subclass it instead.

```python
# products/models.py
from django.db import models
from boundary.models import make_tenant_mixin

MerchantMixin = make_tenant_mixin("merchant")


class Product(MerchantMixin):
    sku = models.CharField(max_length=64)
    name = models.CharField(max_length=200)
    # Product.merchant is the FK to BOUNDARY_TENANT_MODEL.
    # Product.objects auto-filters by the active tenant via the "merchant" field.
    # Product.unscoped bypasses filtering.
```

`make_tenant_mixin()` returns an abstract model that wires up the FK,
`objects = TenantManager()` (auto-filtering and auto-populate), and
`unscoped = UnscopedManager()` (the escape hatch). Auto-populate, `bulk_create`,
and `bulk_update` all work through the custom field name.

If you call it with no argument, it falls back to `BOUNDARY_TENANT_FK_FIELD`, so
these two lines are equivalent once step 1 is done:

```python
MerchantMixin = make_tenant_mixin("merchant")
MerchantMixin = make_tenant_mixin()  # reads BOUNDARY_TENANT_FK_FIELD
```

The factory also accepts keyword arguments to tune the FK, all keyword-only:

```python
MerchantMixin = make_tenant_mixin(
    "merchant",
    on_delete=models.PROTECT,   # default models.CASCADE
    related_name="products",     # default "%(app_label)s_%(class)s_set"
    db_index=True,               # default True
    null=False,                  # default False
)
```

The FK's `verbose_name` is set from `BOUNDARY_TENANT_LABEL`, so the field reads
as "merchant" in the admin and forms without extra work.

### 3. Read the tenant off the request as `request.merchant`

`TenantMiddleware` always sets `request.tenant` for backwards compatibility.
When `BOUNDARY_REQUEST_ATTR` resolves to something other than `"tenant"`, the
middleware also sets that attribute to the same object. With step 1 done,
`BOUNDARY_REQUEST_ATTR` is already `"merchant"`, so:

```python
def dashboard(request):
    merchant = request.merchant   # same object as request.tenant
    ...
```

If you only want to rename the request attribute (and nothing else), set it
explicitly:

```python
# settings.py
BOUNDARY_REQUEST_ATTR = "merchant"
```

### 4. Override the label or request attribute independently (optional)

The three settings are independent, even though two default to the FK field
name. Override them when your column name, your user-facing word, and your
request attribute should differ. For example, an FK column named `org` but a
label of "organisation" shown to users:

```python
# settings.py
BOUNDARY_TENANT_FK_FIELD = "org"            # Product.org, column org_id
BOUNDARY_TENANT_LABEL = "organisation"      # "No organisation is active..."
BOUNDARY_REQUEST_ATTR = "organisation"      # request.organisation
```

For a club-style app where the field, label, and request attribute should all
read "club", step 1 alone is enough:

```python
# settings.py
BOUNDARY_TENANT_FK_FIELD = "club"
```

## Verify it worked

### Check the field name and column

```python
>>> from products.models import Product
>>> [f.name for f in Product._meta.get_fields()]
['id', 'merchant', 'sku', 'name']      # "merchant", not "tenant"
>>> Product._meta.get_field("merchant").column
'merchant_id'
```

### Check auto-populate uses the renamed field

```python
from boundary.testing import set_tenant
from products.models import Product

with set_tenant(some_merchant):
    product = Product.objects.create(sku="A1", name="Widget")
    assert product.merchant == some_merchant
    assert product.merchant_id == some_merchant.pk
```

### Check the error message and request alias

```python
>>> from boundary.context import TenantContext
>>> TenantContext.require()      # with no tenant set
Traceback (most recent call last):
    ...
boundary.exceptions.TenantNotSetError: No merchant is active in context. ...
```

After a request flows through `TenantMiddleware`, both attributes point at the
same object:

```python
assert request.merchant is request.tenant
```

You can also confirm the model is registered and report its FK field name:

```python
>>> from boundary.models import is_tenant_model, get_tenant_fk_field
>>> is_tenant_model(Product)
True
>>> get_tenant_fk_field(Product)
'merchant'
```

## Common pitfalls

- **Renaming after migrations exist.** `BOUNDARY_TENANT_FK_FIELD` and
  `make_tenant_mixin("merchant")` determine the database column
  (`merchant_id`). Changing the name on a model that already has migrations
  produces a column rename that Django may render as a drop-and-add. Decide on
  the terminology before you ship the first migration, or write a careful
  `RenameField` migration by hand.
- **`request.tenant` never disappears.** The middleware always sets
  `request.tenant`. `BOUNDARY_REQUEST_ATTR` adds an alias; it does not remove
  the original. Code and third-party integrations that read `request.tenant`
  keep working.
- **The default mixin ignores the setting.** `TenantMixin` and `TenantModel`
  are hard-coded to the field name `tenant`. To pick up
  `BOUNDARY_TENANT_FK_FIELD`, use `make_tenant_mixin()` (with no argument to
  read the setting). Setting `BOUNDARY_TENANT_FK_FIELD` alone does not rename
  the field on models that subclass `TenantMixin`.
- **Mixing field names across a project.** If different apps use different FK
  field names, each model's manager reads its own `_boundary_fk_field`, so
  filtering still works. But your team must remember which model uses which
  name. Prefer one global `BOUNDARY_TENANT_FK_FIELD` plus
  `make_tenant_mixin()` with no argument for consistency.
- **Expecting the label to auto-capitalise everywhere.** Only the middleware
  HTTP bodies title-case the label. Error messages use it verbatim, so set
  `BOUNDARY_TENANT_LABEL` in the case you want to read in exceptions and the
  admin.

## Related

- [Set up a tenant model](set-up-a-tenant-model.md): define the model that
  `BOUNDARY_TENANT_MODEL` points at.
- [Write tenant-safe tests](write-tenant-safe-tests.md): `set_tenant` and the
  test helpers used in the verification snippets above.
- [Choose and order resolvers](choose-and-order-resolvers.md): how the
  middleware finds the tenant it sets on the request.
- [README](../../README.md): full settings reference and the
  `make_tenant_mixin()` signature.
