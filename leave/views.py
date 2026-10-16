from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Q, Subquery
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.scoping import scope_to_department
from staff.models import Staff

from .forms import LeaveEntitlementForm
from .models import LeaveApplication, LeaveEntitlement
from .services import (
    APPROVED_REQUEST_STATUSES,
    PENDING_REQUEST_STATUSES,
    role_entitlement_for,
)


def _staff_scope(user):
    return scope_to_department(
        Staff.objects.filter(is_deleted=False, user__isnull=False)
        .select_related("user__role", "department", "state"),
        user,
    )


@login_required
@permission_required("leave.manage_leave_entitlements", raise_exception=True)
def entitlements(request):
    current_year = timezone.localdate().year
    year_value = request.POST.get("year") if request.method == "POST" else request.GET.get("year")
    try:
        year = int(year_value) if year_value else current_year
    except (TypeError, ValueError):
        messages.error(request, "Enter a valid calendar year.")
        year = current_year
    if not 1900 <= year <= 9999:
        messages.error(request, "Enter a valid calendar year.")
        year = current_year

    staff = _staff_scope(request.user)
    if request.method == "POST":
        form = LeaveEntitlementForm(request.POST)
        if form.is_valid():
            member = get_object_or_404(staff, pk=form.cleaned_data["staff_id"])
            if form.cleaned_data["days"] is None:
                LeaveEntitlement.objects.filter(
                    staff=member,
                    year=form.cleaned_data["year"],
                ).delete()
                messages.success(
                    request,
                    f"Role-based annual leave default restored for {member.full_name}.",
                )
            else:
                LeaveEntitlement.objects.update_or_create(
                    staff=member,
                    year=form.cleaned_data["year"],
                    defaults={
                        "days": form.cleaned_data["days"],
                        "updated_by": request.user,
                    },
                )
                messages.success(
                    request,
                    f"Individual annual leave override saved for {member.full_name}.",
                )
            return redirect(f"{request.path}?year={form.cleaned_data['year']}")
        messages.error(request, "Check the entitlement value and try again.")

    entitlements_by_staff = {
        item.staff_id: item
        for item in LeaveEntitlement.objects.filter(
            staff_id__in=Subquery(staff.values("pk")),
            year=year,
        )
    }
    rows = []
    for member in staff:
        entitlement = entitlements_by_staff.get(member.pk)
        form = LeaveEntitlementForm(initial={
            "staff_id": member.pk,
            "year": year,
            "days": entitlement.days if entitlement else "",
        })
        rows.append({
            "staff": member,
            "entitlement": entitlement,
            "role_entitlement": role_entitlement_for(member),
            "form": form,
        })

    return render(request, "leave/entitlements.html", {
        "rows": rows,
        "year": year,
        "current_year": current_year,
    })


@login_required
@permission_required("leave.manage_leave_entitlements", raise_exception=True)
def management(request):
    current_year = timezone.localdate().year
    staff = _staff_scope(request.user)
    applications = LeaveApplication.objects.filter(
        staff_id__in=Subquery(staff.values("pk")),
        request__is_deleted=False,  # review M16
    ).select_related(
        "staff", "staff__department", "request", "request__requester",
        "request__request_type",
    )

    search = (request.GET.get("q") or "").strip()
    if search:
        applications = applications.filter(
            Q(staff__first_name__icontains=search)
            | Q(staff__last_name__icontains=search)
            | Q(staff__employee_no__icontains=search)
            | Q(request__reference__icontains=search)
            | Q(request__subject__icontains=search)
        )

    year_value = request.GET.get("year", str(current_year))
    try:
        year = int(year_value)
        if not 1900 <= year <= 9999:
            raise ValueError
    except (TypeError, ValueError):
        year = current_year
        messages.error(request, "Enter a valid calendar year.")
    applications = applications.filter(
        start_date__year__lte=year,
        end_date__year__gte=year,
    )

    from workflow.models import Request

    status = (request.GET.get("status") or "").strip()
    valid_statuses = {value for value, _label in Request.STATUS_CHOICES}
    if status in valid_statuses:
        applications = applications.filter(request__status=status)
    else:
        status = ""

    today = timezone.localdate()
    base_applications = LeaveApplication.objects.filter(
        staff_id__in=Subquery(staff.values("pk")),
        request__is_deleted=False,  # review M16
    )
    stats = {
        "on_leave": base_applications.filter(
            start_date__lte=today,
            end_date__gte=today,
            request__status__in=APPROVED_REQUEST_STATUSES,
        ).count(),
        "upcoming": base_applications.filter(
            start_date__gt=today,
            request__status__in=APPROVED_REQUEST_STATUSES,
        ).count(),
        "awaiting": base_applications.filter(
            request__status__in=PENDING_REQUEST_STATUSES,
        ).count(),
        "staff": staff.count(),
        "entitled_days": 0,
    }
    entitlement_overrides = {
        item.staff_id: item.days
        for item in LeaveEntitlement.objects.filter(
            staff_id__in=Subquery(staff.values("pk")),
            year=year,
        )
    }
    stats["entitled_days"] = sum(
        (
            entitlement_overrides.get(member.pk)
            if member.pk in entitlement_overrides
            else role_entitlement_for(member) or 0
            for member in staff
        ),
        0,
    )

    page = Paginator(applications.order_by("-created_at", "-pk"), 50).get_page(
        request.GET.get("page") or 1
    )
    return render(request, "leave/management.html", {
        "applications": page,
        "year": year,
        "current_year": current_year,
        "search": search,
        "status": status,
        "status_choices": Request.STATUS_CHOICES,
        "stats": stats,
    })
