"""dashboard/executive/ (staff door): the website's leadership dashboards
(api/docs/dashboard.md)."""
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from ..serializers_dashboard import ExecutiveDashboardSerializer
from ..services import dashboard as svc
from .common import AUTHED_ERRORS


class CanViewExecutiveDashboard(BasePermission):
    """dashboards.views._guard of the director / deputy / ES / chairman boards."""

    def has_permission(self, request, view):
        return svc.can_view(request.user)


class ExecutiveDashboardView(APIView):
    permission_classes = [*api_settings.DEFAULT_PERMISSION_CLASSES, CanViewExecutiveDashboard]

    @extend_schema(operation_id="dashboard_executive", responses={200: ExecutiveDashboardSerializer,
                                                                  **AUTHED_ERRORS})
    def get(self, request):
        """KPIs, charts and lists of the leadership board I may open on the
        website (403 permission_denied for everyone else)."""
        return Response(ExecutiveDashboardSerializer(svc.executive_payload(request.user, request)).data)
