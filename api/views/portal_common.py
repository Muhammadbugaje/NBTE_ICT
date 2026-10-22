"""Helpers shared by the portal-door views (api/views/portal_*.py)."""
from contextlib import contextmanager

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from rest_framework import serializers

from ..exceptions import ApiError
from ..pagination import StandardPagination

_PAGED = {}


class Forbidden(ApiError):
    """403 permission_denied with the website's own explanation."""
    status_code, code = 403, "permission_denied"


@contextmanager
def website_rules():
    """Run a website service: its ValidationError becomes 400
    validation_error (the exception handler), its PermissionDenied a 403
    that keeps the service's message."""
    try:
        yield
    except DjangoPermissionDenied as exc:
        raise Forbidden(str(exc) or None) from None


def form_error(form):
    """A Django form's errors as a DRF ValidationError (fields keep their names)."""
    raise serializers.ValidationError({("non_field_errors" if k == "__all__" else k): [str(e) for e in v]
                                       for k, v in form.errors.items()})


def query(serializer_class, request):
    """Validate the query string with ``serializer_class`` (400 on errors)."""
    s = serializer_class(data=request.query_params)
    s.is_valid(raise_exception=True)
    return s.validated_data


def body(serializer_class, request):
    s = serializer_class(data=request.data)
    s.is_valid(raise_exception=True)
    return s.validated_data


def page(view, request, items, row, serializer_class):
    """Contract pagination of ``items`` (queryset or list); ``row`` shapes each one."""
    paginator = StandardPagination()
    chunk = paginator.paginate_queryset(items, request, view=view)
    return paginator.get_paginated_response(serializer_class([row(x) for x in chunk], many=True).data)


def paged(serializer_class):
    """OpenAPI shape of a paginated list of ``serializer_class``."""
    name = serializer_class.__name__.removesuffix("Serializer")
    if name not in _PAGED:
        _PAGED[name] = type(f"PortalPaginated{name}List", (serializers.Serializer,), {
            "results": serializer_class(many=True),
            "next": serializers.CharField(allow_null=True),
            "previous": serializers.CharField(allow_null=True),
            "count": serializers.IntegerField(allow_null=True),
            "Meta": type("Meta", (), {"ref_name": f"PortalPaginated{name}List"}),
        })
    return _PAGED[name]


def binary(description="The file (Content-Disposition: attachment; ?inline=1 previews PDFs and images)."):
    from drf_spectacular.types import OpenApiTypes
    from drf_spectacular.utils import OpenApiResponse

    return OpenApiResponse(OpenApiTypes.BINARY, description=description)
