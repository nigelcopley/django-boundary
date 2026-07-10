# Choose and order resolvers

## Goal

Pick the right built-in resolver for how your clients identify a tenant, order them correctly with `BOUNDARY_RESOLVERS`, and add a custom resolver when none of the built-ins fit.

A resolver answers one question per request: which tenant does this request belong to? The middleware walks `BOUNDARY_RESOLVERS` in order, calls `resolve(request)` on each, and stops at the first resolver that returns a tenant. First match wins.

## Prerequisites

- `boundary.middleware.TenantMiddleware` installed in `MIDDLEWARE`.
- `BOUNDARY_TENANT_MODEL` set to your tenant model (for example `"clubs.Club"`).
- The middleware setup is covered in the [README quick start](../../README.md#quick-start).

## Steps

### 1. Understand each built-in resolver

All built-ins live in `boundary.resolvers` and subclass `BaseResolver`. Each `resolve(request)` returns a tenant instance or `None` to pass control to the next resolver. None of them raise on a miss.

| Resolver | Reads from | Lookup | Setting |
|----------|-----------|--------|---------|
| `SubdomainResolver` | First label of the host (`club-a.example.com`) | `SUBDOMAIN_FIELD` on the tenant model | `BOUNDARY_SUBDOMAIN_FIELD` (default `"slug"`) |
| `HeaderResolver` | An HTTP header | UUID pk, then raw pk, then slug | `BOUNDARY_HEADER_NAME` (default `"X-Tenant-ID"`) |
| `JWTClaimResolver` | The `Authorization: Bearer` token payload | tenant pk from a claim, signature NOT validated | `BOUNDARY_JWT_CLAIM` (default `"tenant_id"`) |
| `SessionResolver` | The Django session | tenant pk from a session key | `BOUNDARY_SESSION_KEY` (default `"boundary_tenant_id"`) |
| `ExplicitResolver` | `request.boundary_tenant` set by upstream code | direct attribute read, no DB query | none |

When to use each:

- **SubdomainResolver**: public-facing multi-tenant apps where each tenant has its own subdomain. Requires a host with at least three labels (`slug.domain.tld`); a bare `example.com` returns `None`. This is the default when `BOUNDARY_RESOLVERS` is unset.
- **HeaderResolver**: internal or service-to-service APIs where a trusted client names the tenant. It tries the header value as a UUID pk, then as a raw pk, then as a slug (`SUBDOMAIN_FIELD`), so both `X-Tenant-ID: <uuid>` and `X-Tenant-ID: club-a` work. Treat the header as a trust boundary: anyone who can set it picks the tenant.
- **JWTClaimResolver**: APIs that already authenticate with a bearer token carrying the tenant in a claim. It base64-decodes the JWT payload and reads the claim, but does NOT verify the signature, so put real token verification (for example DRF auth or an upstream gateway) in front of it. The claim value must be the tenant pk.
- **SessionResolver**: server-rendered apps where the user selects or is assigned a tenant and you store its pk in `request.session[BOUNDARY_SESSION_KEY]`.
- **ExplicitResolver**: when other code (a different middleware, a test, a management command path) has already set `request.boundary_tenant`. It is a pure attribute read with no DB lookup, useful as a first-priority override or in test setups.

### 2. Configure the order

`BOUNDARY_RESOLVERS` is a list of dotted class paths. Order is precedence: the middleware returns the first non-`None` result.

```python
# settings.py
BOUNDARY_RESOLVERS = [
    "boundary.resolvers.ExplicitResolver",   # honour explicit overrides first
    "boundary.resolvers.SubdomainResolver",  # then public subdomain
    "boundary.resolvers.SessionResolver",    # then logged-in session choice
]
```

Order by trust and specificity. Put the most authoritative source first and the broadest fallback last.

### 3. Set per-resolver options

Each resolver reads its own `BOUNDARY_` setting. Override only the ones whose resolver you use.

```python
# settings.py
BOUNDARY_SUBDOMAIN_FIELD = "slug"          # tenant field for subdomain and header-slug lookups
BOUNDARY_HEADER_NAME = "X-Tenant-ID"       # header HeaderResolver reads
BOUNDARY_JWT_CLAIM = "org_id"              # claim JWTClaimResolver reads
BOUNDARY_SESSION_KEY = "boundary_tenant_id"  # session key SessionResolver reads
```

Note that `HeaderResolver`'s slug fallback uses `BOUNDARY_SUBDOMAIN_FIELD`, not a separate setting.

The full settings table is in the [settings reference](../reference/settings.md).

### 4. Write a custom resolver

Subclass `BaseResolver` and implement `resolve(self, request)`. Return a tenant or `None`. Call `self.get_tenant_model()` to get the configured tenant model rather than importing it directly, and never let `resolve` raise: log and return `None` on error.

```python
# myapp/resolvers.py
import logging

from boundary.resolvers import BaseResolver

logger = logging.getLogger(__name__)


class PathPrefixResolver(BaseResolver):
    """Resolve tenant from a /t/<slug>/ URL prefix."""

    def resolve(self, request):
        parts = request.path.split("/")
        if len(parts) < 3 or parts[1] != "t":
            return None

        slug = parts[2]
        TenantModel = self.get_tenant_model()
        try:
            return TenantModel.objects.get(slug=slug, is_active=True)
        except TenantModel.DoesNotExist:
            return None
        except Exception:
            logger.exception("PathPrefixResolver failed for slug=%s", slug)
            return None
```

Register it by dotted path:

```python
# settings.py
BOUNDARY_RESOLVERS = [
    "myapp.resolvers.PathPrefixResolver",
    "boundary.resolvers.SubdomainResolver",
]
```

## Verify it worked

Call the resolver directly with a Django `RequestFactory`, the same way the test suite does.

```python
import pytest
from django.test import RequestFactory

from boundary.resolvers import SubdomainResolver


@pytest.mark.django_db
def test_subdomain_resolution(tenant_a):
    request = RequestFactory().get("/", HTTP_HOST="club-a.example.com")
    assert SubdomainResolver().resolve(request) == tenant_a
```

To confirm ordering end to end, send a real request through the middleware and read the resolved tenant off the request. After successful resolution the middleware sends the `tenant_resolved` signal with `tenant`, `resolver`, and `request`; if nothing matches and `BOUNDARY_REQUIRED` is `True` it sends `tenant_resolution_failed` and returns a 404. The resolved tenant is available as `request.tenant` (and as `request.<BOUNDARY_REQUEST_ATTR>` when you have customised that).

## Common pitfalls

- **Trusting `HeaderResolver` on public endpoints.** Any client can set the header, so any client can choose the tenant. Use it only behind a trusted boundary, and do not place it ahead of `SubdomainResolver` on public-facing apps.
- **Assuming `JWTClaimResolver` validates the token.** It decodes the payload without verifying the signature. Authenticate the token separately before relying on the resolved tenant.
- **Subdomain lookups on a two-label host.** `SubdomainResolver` returns `None` for `example.com` because it needs at least three labels. Local development on `localhost` will not resolve; use a `*.localhost` style host or a different resolver in dev.
- **Wrong lookup field.** `SubdomainResolver` and the `HeaderResolver` slug fallback both look up `BOUNDARY_SUBDOMAIN_FIELD` (default `"slug"`). If your tenant key column has another name, set it once via `BOUNDARY_SUBDOMAIN_FIELD`.
- **Claim or session value is not the pk.** `JWTClaimResolver` and `SessionResolver` look up the tenant by primary key. Store the tenant pk, not its slug, in the claim or session.
- **Letting `resolve` raise.** A raised exception is logged and skipped per resolver, but you lose control over the fallback. Catch and return `None` yourself.

## Related

- [README: Resolvers](../../README.md#resolvers): full resolver table and resolver-cache behaviour.
- [Settings reference](../reference/settings.md): all `BOUNDARY_` settings and defaults.
- [README: Signals](../../README.md#signals): `tenant_resolved` and `tenant_resolution_failed`.
