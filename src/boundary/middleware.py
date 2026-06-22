"""Tenant middleware — resolves tenant from request and sets context.

Uses Django's MiddlewareMixin for WSGI + ASGI compatibility.
Wraps the request in transaction.atomic() so set_config() has effect.
"""

import logging

from django.http import HttpResponseForbidden, HttpResponseNotFound
from django.utils.deprecation import MiddlewareMixin
from django.utils.module_loading import import_string

from boundary.conf import boundary_settings
from boundary.context import TenantContext
from boundary.exceptions import TenantInactiveError, TenantResolutionError
from boundary.signals import tenant_resolution_failed, tenant_resolved

logger = logging.getLogger("boundary.middleware")


class TenantMiddleware(MiddlewareMixin):
    """Resolve tenant from request and manage context lifecycle.

    Overrides __call__ to wrap the full request in transaction.atomic()
    when BOUNDARY_WRAP_ATOMIC is True, ensuring set_config() has effect.
    """

    def __call__(self, request):
        # Resolve tenant from the configured resolver chain
        tenant, resolver = self._resolve_tenant(request)

        label = boundary_settings.TENANT_LABEL
        label_title = label[:1].upper() + label[1:]

        # No resolver matched
        if tenant is None:
            if boundary_settings.REQUIRED:
                tenant_resolution_failed.send(sender=self.__class__, request=request)
                return HttpResponseNotFound(f"{label_title} not found.")
            # BOUNDARY_REQUIRED=False — proceed without tenant
            return self.get_response(request)

        # Check is_active (BR-RES-004). Build a TenantInactiveError and hand it
        # to _handle_inactive_tenant() so subclasses can re-raise it or return a
        # custom response, while the default translates it to a 403.
        if hasattr(tenant, "is_active") and not tenant.is_active:
            logger.warning(
                "Inactive tenant rejected",
                extra={"tenant_id": str(tenant.pk)},
            )
            exc = TenantInactiveError(f"{label_title} is inactive.")
            return self._handle_inactive_tenant(request, tenant, exc)

        # Fire signal
        tenant_resolved.send(
            sender=self.__class__,
            tenant=tenant,
            resolver=resolver,
            request=request,
        )
        logger.info(
            "Tenant resolved",
            extra={
                "tenant_id": str(tenant.pk),
                "resolver_name": resolver.__class__.__name__,
            },
        )

        # Set context and wrap in transaction if configured
        request.tenant = tenant
        request_attr = boundary_settings.REQUEST_ATTR
        if request_attr and request_attr != "tenant":
            setattr(request, request_attr, tenant)

        if boundary_settings.WRAP_ATOMIC and not self._is_atomic_requests():
            from django.db import transaction

            with transaction.atomic():
                token = TenantContext.set(tenant)
                request._boundary_token = token
                try:
                    response = self.get_response(request)
                finally:
                    TenantContext.clear(token)
        else:
            token = TenantContext.set(tenant)
            request._boundary_token = token
            try:
                response = self.get_response(request)
            finally:
                TenantContext.clear(token)

        return response

    def _handle_inactive_tenant(self, request, tenant, exc):
        """Return the response for an inactive tenant.

        Override to customise (for example, redirect to a billing page). The
        default translates the raised ``TenantInactiveError`` to a 403.
        """
        label = boundary_settings.TENANT_LABEL
        label_title = label[:1].upper() + label[1:]
        return HttpResponseForbidden(f"{label_title} is inactive.")

    def _resolve_tenant(self, request):
        """Walk the resolver chain. Return (tenant, resolver) or (None, None).

        A resolver that raises is wrapped in ``TenantResolutionError``, logged,
        and skipped so the chain falls through to the next resolver (BR-RES-010).
        """
        resolver_paths = boundary_settings.RESOLVERS
        for path in resolver_paths:
            try:
                resolver_cls = import_string(path)
                resolver = resolver_cls()
                tenant = resolver.resolve(request)
                if tenant is not None:
                    return tenant, resolver
            except Exception as exc:
                error = TenantResolutionError(f"Resolver {path} failed: {exc}")
                error.__cause__ = exc
                logger.warning(
                    "Resolver raised exception",
                    extra={"resolver_name": path},
                    exc_info=True,
                )
                self._on_resolver_error(request, path, error)
        return None, None

    def _on_resolver_error(self, request, resolver_path, error):
        """Hook called when a resolver raises (after logging, before fallthrough).

        The default is a no-op so the chain falls through to the next resolver.
        Override to re-raise ``error`` (a ``TenantResolutionError``) if you want
        a failing resolver to abort resolution rather than be skipped.
        """
        return None

    @staticmethod
    def _is_atomic_requests():
        """Check if ATOMIC_REQUESTS is already enabled on the default DB."""
        from django.db import connections

        return connections["default"].settings_dict.get("ATOMIC_REQUESTS", False)
