from django.conf import settings
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from ..authentication import ApiJWTAuthentication
from ..permissions import DocsAccess
from ..serializers import HealthSerializer


class HealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(operation_id="health", responses={200: HealthSerializer}, auth=[])
    def get(self, request):
        return Response({"status": "ok", "min_app_version": getattr(settings, "API_MIN_APP_VERSION", "1.0.0"),
                         "server_time": timezone.localtime().isoformat()})


# Docs: open while DEBUG; in production only for a signed-in superuser (a
# website session is accepted here, read-only GETs, so no CSRF concern).
_DOCS = {"authentication_classes": [ApiJWTAuthentication, SessionAuthentication],
         "permission_classes": [DocsAccess]}


class SchemaView(SpectacularAPIView):
    authentication_classes = _DOCS["authentication_classes"]
    permission_classes = _DOCS["permission_classes"]
    throttle_classes = []


class DocsView(SpectacularSwaggerView):
    authentication_classes = _DOCS["authentication_classes"]
    permission_classes = _DOCS["permission_classes"]
    throttle_classes = []
