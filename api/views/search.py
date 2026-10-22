"""search/ (staff door): the website's global search (api/docs/search.md)."""
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import serializers_search as s
from ..services import search as svc
from .common import AUTHED_ERRORS, errors


class SearchView(APIView):
    @extend_schema(operation_id="search", parameters=[s.SearchQuerySerializer],
                   responses={200: s.SearchResponseSerializer, **errors(400), **AUTHED_ERRORS})
    def get(self, request):
        """Search everything I may see on the website, grouped by kind
        (empty groups are left out)."""
        q = s.SearchQuerySerializer(data=request.query_params)
        q.is_valid(raise_exception=True)
        v = q.validated_data
        return Response(s.SearchResponseSerializer(svc.search(request.user, v["q"], v["limit"], request)).data)
