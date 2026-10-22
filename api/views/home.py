from drf_spectacular.utils import PolymorphicProxySerializer, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from ..serializers import InstitutionHomeSerializer, ResourcePersonHomeSerializer, StaffHomeSerializer
from ..services.home import home_payload
from .common import AUTHED_ERRORS

SHAPES = {"staff": StaffHomeSerializer, "institution": InstitutionHomeSerializer,
          "resource_person": ResourcePersonHomeSerializer}


class HomeView(APIView):
    """The home screen; its shape depends on the signed-in user's type."""

    @extend_schema(operation_id="home", responses={
        200: PolymorphicProxySerializer(component_name="Home", serializers=list(SHAPES.values()),
                                        resource_type_field_name=None),
        **AUTHED_ERRORS})
    def get(self, request):
        user = request.user
        serializer = SHAPES.get(user.user_type, StaffHomeSerializer)
        return Response(serializer(home_payload(user, request)).data)
