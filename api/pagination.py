"""Page pagination for list endpoints (contract: "Pagination").

    {"results": [...], "next": url|null, "previous": url|null, "count": int}

``?page=`` and ``?page_size=`` (max 100). Feature list endpoints use it via
REST_FRAMEWORK["DEFAULT_PAGINATION_CLASS"] (generic views) or explicitly:

    paginator = StandardPagination()
    page = paginator.paginate_queryset(qs, request, view=self)
    return paginator.get_paginated_response(MySerializer(page, many=True).data)
"""
from collections import OrderedDict

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(OrderedDict([
            ("results", data),
            ("next", self.get_next_link()),
            ("previous", self.get_previous_link()),
            ("count", self.page.paginator.count),
        ]))

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "required": ["results", "next", "previous", "count"],
            "properties": {
                "results": schema,
                "next": {"type": "string", "nullable": True, "format": "uri"},
                "previous": {"type": "string", "nullable": True, "format": "uri"},
                "count": {"type": "integer", "nullable": True},
            },
        }
