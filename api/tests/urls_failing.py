"""Test-only URLconf: views that always fail, to check the error envelope."""
from django.http import Http404
from django.urls import include, path
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView


class BoomView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        raise RuntimeError("secret internals: db password is hunter2")


class MissingView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        raise Http404("no such thing")


class DeniedView(APIView):
    def get(self, request):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied


urlpatterns = [
    path("api/v1/boom/", BoomView.as_view()),
    path("api/v1/missing/", MissingView.as_view()),
    path("api/v1/denied/", DeniedView.as_view()),
    path("api/v1/", include("api.urls_staff", namespace="api")),
]
