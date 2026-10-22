"""staff/ - the staff directory (staff door). See api/services/assets_staff.py."""
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import serializers_assets_staff as s
from ..pagination import StandardPagination
from ..services import assets_staff as svc
from ..services.work import paginate
from .common import AUTHED_ERRORS, errors


class DirectoryView(APIView):
    pagination_class = StandardPagination

    @extend_schema(operation_id="staff_directory", parameters=[s.DirectoryQuerySerializer],
                   responses={200: s.DirectoryStaffCardSerializer(many=True), **errors(400), **AUTHED_ERRORS})
    def get(self, request):
        """The staff I may see on the website's staff list (all departments,
        my department, or only myself - staff.permissions.staff_visible_to)."""
        q = s.DirectoryQuerySerializer(data=request.query_params)
        q.is_valid(raise_exception=True)
        qs = svc.directory(request.user, (q.validated_data.get("q") or "").strip(),
                           q.validated_data.get("department"))
        return paginate(self, request, qs, s.DirectoryStaffCardSerializer, lambda st: svc.staff_card(st, request))


class ProfileView(APIView):
    @extend_schema(operation_id="staff_profile", responses={200: s.StaffProfileSerializer,
                                                           **errors(404), **AUTHED_ERRORS})
    def get(self, request, pk):
        from staff import permissions

        st = get_object_or_404(permissions.staff_visible_to(request.user), pk=pk)
        return Response(s.StaffProfileSerializer(svc.staff_profile(st, request.user, request)).data)


class DepartmentsView(APIView):
    pagination_class = StandardPagination

    @extend_schema(operation_id="staff_departments", responses={200: s.DepartmentSerializer(many=True),
                                                               **AUTHED_ERRORS})
    def get(self, request):
        """The department filter of the staff list (only mine unless I see
        every department)."""
        return paginate(self, request, svc.departments(request.user), s.DepartmentSerializer,
                        lambda d: {"id": d.pk, "code": d.code, "name": d.name})
