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


# ===========================================================================
# v2.1 Phase D: apply for any leave type (F4, F5)
# ===========================================================================
from django.core.exceptions import PermissionDenied, ValidationError  # noqa: E402
from django.http import HttpResponse  # noqa: E402
from django.views.decorators.http import require_POST  # noqa: E402

from . import access as leave_access  # noqa: E402
from . import services as leave_services  # noqa: E402
from .forms import HolidayForm, LeaveApplyForm, LeaveTypeForm  # noqa: E402
from .models import LeaveType  # noqa: E402


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _my_staff(user):
    return Staff.objects.filter(user=user, is_deleted=False).first()


def _apply_context(request, form, staff):
    lt = form.selected_type()
    year = timezone.localdate().year
    balance = leave_services.balance_for(staff, year, lt) if (staff and lt) else None
    return {"form": form, "leave_type": lt, "staff": staff, "balance": balance, "year": year,
            "types": list(form.fields["leave_type"].queryset)}


def _preview_for(request, data, staff):
    """Preview dict, or {"error": message} - never raises."""
    lt_code = data.get("leave_type")
    if staff is None or not data.get("start_date"):
        return None
    if not (data.get("days") or data.get("end_date")):
        return None
    try:
        return leave_services.preview(request.user, {
            "leave_type": lt_code, "start_date": data.get("start_date"),
            "end_date": data.get("end_date"), "days": data.get("days")})
    except ValidationError as exc:
        return {"error": " ".join(exc.messages)}


@login_required
def apply(request):
    """/leave/apply/ - one page for every leave type; the type decides the
    fields (dates or a number of days) and the rules shown."""
    staff = _my_staff(request.user)
    if request.method == "POST":
        form = LeaveApplyForm(request.POST, request.FILES, staff=staff)
        if form.is_valid():
            try:
                req = leave_services.apply_for_leave(request.user, form.form_data(),
                                                     reason=form.cleaned_data.get("reason") or "",
                                                     document=form.cleaned_data.get("document"))
            except ValidationError as exc:
                form.add_error(None, " ".join(exc.messages))
            else:
                messages.success(request, f"{form.cleaned_data['leave_type'].name} application {req.reference} "
                                          "sent for approval.")
                return redirect("workflow:detail", pk=req.pk)
    else:
        initial = {"leave_type": request.GET.get("type") or LeaveType.ANNUAL}
        form = LeaveApplyForm(initial=initial, staff=staff)
    ctx = _apply_context(request, form, staff)
    ctx["preview"] = _preview_for(request, request.POST, staff) if request.method == "POST" else None
    ctx["request_type_open"] = leave_services.leave_request_type() is not None
    return render(request, "leave/apply.html", ctx)


@login_required
def apply_fields(request):
    """HTMX: the type changed - swap the mode fields, rules and balance."""
    staff = _my_staff(request.user)
    form = LeaveApplyForm(initial={"leave_type": request.GET.get("leave_type"),
                                   "start_date": request.GET.get("start_date") or None},
                          staff=staff)
    ctx = _apply_context(request, form, staff)
    ctx["preview"] = _preview_for(request, request.GET, staff)
    return render(request, "leave/_apply_fields.html", ctx)


@login_required
def apply_preview(request):
    """HTMX: live end date, counted days, resumption date and balance after."""
    staff = _my_staff(request.user)
    return render(request, "leave/_apply_preview.html",
                  {"preview": _preview_for(request, request.GET, staff)})


# ===========================================================================
# v2.1 Phase D: Settings -> Leave types (F2) and Holidays (F7, T-CONS-5)
# ===========================================================================
def _retarget(response, target):
    """Invalid HTMX form: re-render the editor in place (no page swap)."""
    response["HX-Retarget"] = target
    response["HX-Reswap"] = "innerHTML"
    return response


def _require(ok):
    if not ok:
        raise PermissionDenied("Only HR (or someone granted the permission) manages this.")


@login_required
def leave_types(request):
    _require(leave_access.can_manage_leave_types(request.user))
    edit = None
    edit_pk = request.GET.get("edit")
    if edit_pk == "new":
        edit = LeaveTypeForm(initial={"input_mode": LeaveType.MODE_DATES, "counting": LeaveType.COUNT_WORKING,
                                      "paid": True, "is_active": True})
    elif edit_pk and edit_pk.isdigit():
        obj = get_object_or_404(LeaveType, pk=int(edit_pk))
        edit = LeaveTypeForm(instance=obj)
    return render(request, "leave/types.html", _types_ctx(edit))


def _types_ctx(edit_form=None, saved=None):
    types = list(LeaveType.objects.all())
    used = {row["leave_type"]: row["n"] for row in LeaveApplication.objects.values("leave_type")
            .annotate(n=models_count("pk"))}
    for t in types:
        t.used_count = used.get(t.pk, 0)
    return {"types": types, "edit_form": edit_form, "saved": saved,
            "active_count": sum(1 for t in types if t.is_active)}


def models_count(field):
    from django.db.models import Count
    return Count(field)


@login_required
def leave_type_edit(request, pk=None):
    """GET: the editor (fragment for HTMX); POST: save and swap list + editor."""
    _require(leave_access.can_manage_leave_types(request.user))
    obj = get_object_or_404(LeaveType, pk=pk) if pk else None
    if request.method == "POST":
        form = LeaveTypeForm(request.POST, instance=obj)
        if form.is_valid():
            saved = form.save()
            messages.success(request, f"{saved.name} saved. Staff can apply for it straight away."
                             if saved.is_active else f"{saved.name} saved (closed for applications).")
            if _is_htmx(request):
                return render(request, "leave/_types_body.html", _types_ctx(None, saved))
            return redirect("leave:types")
        if _is_htmx(request):
            return _retarget(render(request, "leave/_type_editor.html", {"edit_form": form}), "#lt-editor")
        return render(request, "leave/types.html", _types_ctx(form))
    form = LeaveTypeForm(instance=obj) if obj else LeaveTypeForm(
        initial={"input_mode": LeaveType.MODE_DATES, "counting": LeaveType.COUNT_WORKING, "paid": True,
                 "is_active": True})
    if _is_htmx(request):
        return render(request, "leave/_type_editor.html", {"edit_form": form})
    return render(request, "leave/types.html", _types_ctx(form))


def _year_param(request):
    try:
        y = int(request.GET.get("year") or request.POST.get("year") or timezone.localdate().year)
        return y if 1900 <= y <= 9999 else timezone.localdate().year
    except (TypeError, ValueError):
        return timezone.localdate().year


def _holidays_ctx(year, edit_form=None, saved=None):
    from attendance.holidays import year_list

    rows = year_list(year)
    return {"year": year, "holidays": rows, "edit_form": edit_form, "saved": saved,
            "to_confirm": sum(1 for h in rows if h.to_confirm),
            "years": [year - 1, year, year + 1]}


@login_required
def holidays(request):
    _require(leave_access.can_manage_holidays(request.user))
    year = _year_param(request)
    edit = None
    if request.GET.get("edit") == "new":
        edit = HolidayForm()
    elif (request.GET.get("edit") or "").isdigit():
        from portal.models import Holiday
        edit = HolidayForm(instance=get_object_or_404(Holiday, pk=int(request.GET["edit"])))
    return render(request, "leave/holidays.html", _holidays_ctx(year, edit))


@login_required
def holiday_edit(request, pk=None):
    from portal.models import Holiday, Scope

    _require(leave_access.can_manage_holidays(request.user))
    obj = get_object_or_404(Holiday, pk=pk) if pk else None
    year = _year_param(request)
    if request.method == "POST":
        form = HolidayForm(request.POST, instance=obj)
        if form.is_valid():
            h = form.save(commit=False)
            if obj is None:
                h.visibility = Scope.STAFF  # awarded to all staff (F7)
            h.save()
            messages.success(request, f"{h.name} saved. Attendance on {h.date_start:%d %b %Y}"
                             + (f" to {h.date_end:%d %b %Y}" if h.date_end != h.date_start else "")
                             + " now shows Holiday instead of Absent.")
            year = h.date_start.year
            if _is_htmx(request):
                return render(request, "leave/_holidays_body.html", _holidays_ctx(year, None, h))
            return redirect(f"{reverse_lazy_str('leave:holidays')}?year={year}")
        if _is_htmx(request):
            return _retarget(render(request, "leave/_holiday_editor.html", {"edit_form": form, "year": year}),
                             "#hol-editor")
        return render(request, "leave/holidays.html", _holidays_ctx(year, form))
    form = HolidayForm(instance=obj)
    if _is_htmx(request):
        return render(request, "leave/_holiday_editor.html", {"edit_form": form, "year": year})
    return render(request, "leave/holidays.html", _holidays_ctx(year, form))


@login_required
@require_POST
def holiday_delete(request, pk):
    from portal.models import Holiday

    _require(leave_access.can_manage_holidays(request.user))
    h = get_object_or_404(Holiday, pk=pk)
    year = h.date_start.year
    name = h.name
    h.delete()
    messages.success(request, f"{name} removed. Absences recorded on that day are restored.")
    if _is_htmx(request):
        return render(request, "leave/_holidays_body.html", _holidays_ctx(year))
    return redirect(f"{reverse_lazy_str('leave:holidays')}?year={year}")


def reverse_lazy_str(name):
    from django.urls import reverse
    return reverse(name)


__all__ = ["HttpResponse"]
