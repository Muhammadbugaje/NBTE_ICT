"""workspace/ - the website's "My workspace" page (dashboards self_service)."""
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import serializers_assets_staff as s
from ..services.assets_staff import workspace
from .common import AUTHED_ERRORS


class WorkspaceView(APIView):
    @extend_schema(operation_id="workspace", responses={200: s.WorkspaceSerializer, **AUTHED_ERRORS})
    def get(self, request):
        return Response(s.WorkspaceSerializer(workspace(request.user, request)).data)
