"""Phase B v2.1 HR endpoints (staff door), reusing the web services:
    GET staff/promotions/?tab=eligible|overdue|all   (staff.promote_staff; B.9)
    GET staff/<pk>/access/                           (role + effective permissions; B.8)
Same access rules as the web pages."""
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from ..pagination import StandardPagination
from ..services.work import paginate
from .common import AUTHED_ERRORS, errors


class PromotionQuerySerializer(serializers.Serializer):
    tab = serializers.ChoiceField(choices=["eligible", "overdue", "all"], required=False, default="eligible")
    q = serializers.CharField(required=False, allow_blank=True)
    department = serializers.IntegerField(required=False)
    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100)


class PromotionRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    employee_no = serializers.CharField()
    name = serializers.CharField()
    department = serializers.CharField(allow_blank=True)
    designation = serializers.CharField(allow_blank=True)
    grade = serializers.CharField(allow_blank=True)
    present_appointment = serializers.DateField(allow_null=True)
    next_designation = serializers.CharField(allow_null=True, help_text="Only the next step is allowed (D2).")
    next_grade = serializers.CharField(allow_null=True)
    eligible = serializers.BooleanField()
    overdue = serializers.BooleanField()
    due_on = serializers.DateField(allow_null=True)


class AccessPermissionSerializer(serializers.Serializer):
    label = serializers.CharField()
    name = serializers.CharField()


class AccessSerializer(serializers.Serializer):
    role = serializers.CharField(allow_null=True)
    role_rank = serializers.IntegerField(allow_null=True)
    groups = serializers.ListField(child=serializers.CharField())
    superuser = serializers.BooleanField()
    permissions = AccessPermissionSerializer(many=True)


class PromotionsView(APIView):
    pagination_class = StandardPagination

    @extend_schema(operation_id="staff_promotions", parameters=[PromotionQuerySerializer],
                   responses={200: PromotionRowSerializer(many=True), **errors(400), **AUTHED_ERRORS})
    def get(self, request):
        """Eligible / Overdue / All lists of the promotions page (B.9)."""
        from django.db.models import Q

        from staff.lifecycle_views import scoped_staff
        from staff.promotions import eligibility, eligible_staff, overdue_staff

        if not request.user.has_perm("staff.promote_staff"):
            raise PermissionDenied("Needs staff.promote_staff.")
        q = PromotionQuerySerializer(data=request.query_params)
        q.is_valid(raise_exception=True)
        qs = scoped_staff(request.user)
        text = (q.validated_data.get("q") or "").strip()
        if text:
            qs = qs.filter(Q(first_name__icontains=text) | Q(last_name__icontains=text) | Q(employee_no__icontains=text))
        if q.validated_data.get("department"):
            qs = qs.filter(department_id=q.validated_data["department"])
        tab = q.validated_data.get("tab") or "eligible"
        qs = {"eligible": eligible_staff, "overdue": overdue_staff}.get(tab, lambda x: x)(qs)
        qs = qs.order_by("department__name", "-designation__rank", "last_name")

        def shape(s):
            e = eligibility(s)
            return {"id": s.pk, "employee_no": s.employee_no, "name": s.full_name,
                    "department": s.department.name if s.department_id else "",
                    "designation": s.designation.name if s.designation_id else "", "grade": s.grade_level,
                    "present_appointment": s.date_present_appointment,
                    "next_designation": e.next_step.designation.name if e.next_step else None,
                    "next_grade": (e.next_step.grade or None) if e.next_step else None,
                    "eligible": e.eligible, "overdue": e.overdue, "due_on": e.due_on}
        return paginate(self, request, qs, PromotionRowSerializer, shape)


class AccessView(APIView):
    @extend_schema(operation_id="staff_access", responses={200: AccessSerializer, **errors(404), **AUTHED_ERRORS})
    def get(self, request, pk):
        """Role and effective permissions of a staff member (the profile's Access tab)."""
        from staff import permissions, role_ui

        st = get_object_or_404(permissions.staff_visible_to(request.user), pk=pk)
        account = st.user if st.user_id else None
        allowed = bool(account) and (permissions.can_view_hr_fields(request.user, st)
                                     or account.pk == request.user.pk or role_ui.can_manage_roles(request.user))
        if not allowed:
            raise PermissionDenied("You can't see this person's access.")
        a = role_ui.access_summary(account)
        return Response(AccessSerializer({
            "role": a["role"].name if a["role"] else None,
            "role_rank": a["role"].rank if a["role"] else None,
            "groups": [g.name for g in a["groups"]], "superuser": a["superuser"],
            "permissions": [p for _, perms in a["by_app"] for p in perms],
        }).data)
