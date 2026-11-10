"""leave/ - annual leave. On the website leave is a workflow request of type
``annual-leave`` (the leave app keeps the dates, balances and attendance
sync), so applying, cancelling and approving go through the workflow:

* balance / overview   leave.services.dashboard_data + balance_for
* apply                api.services.requests.create (the type's dynamic form
                       -> workflow.services.raise_request_checked, which runs
                       leave.services.validate_application + sync_application)
* preview              leave.services.validate_application + working_days +
                       resumption_date + balance_for
* cancel               workflow.services.cancel_request (requester only)
* approvals            dashboards.action_queue.actionable_requests filtered to
                       annual-leave + leave.services.request_summary; approve /
                       reject / return use requests/<request_id>/approve/ etc.
* holidays             portal.services.live_holidays (what this user sees)
"""
from datetime import date

from django.utils import timezone

from .work import iso, user_ref

LEAVE_CODE = "annual-leave"


def staff_of(user):
    from staff.models import Staff

    return Staff.objects.filter(user=user, is_deleted=False).select_related("department").first()


def balance_item(balance):
    lt = balance.get("leave_type")
    is_annual = lt is None or lt.code == "annual"
    return {"leave_type": LEAVE_CODE if is_annual else lt.code, "name": "Annual leave" if is_annual else lt.name,
            "year": balance["year"],
            "entitlement": str(balance["entitlement"]), "used": str(balance["used"]),
            "pending": str(balance["pending"]), "remaining": str(balance["remaining"]),
            "is_set": balance["is_set"], "entitlement_source": balance["entitlement_source"]}


def leave_type_available():
    from workflow.models import RequestType

    return RequestType.objects.filter(code=LEAVE_CODE, is_active=True).first()


def application_item(app, user):
    from leave.services import working_days
    from workflow.models import Request

    from .requests import current_step_name

    req = app.request
    return {"id": app.pk, "request_id": req.pk, "reference": req.reference, "start_date": app.start_date,
            "end_date": app.end_date, "working_days": working_days(app.start_date, app.end_date, user=app.staff.user),
            "leave_type": app.leave_type.code, "leave_type_name": app.leave_type.name,
            "status": req.status, "status_display": req.get_status_display(),
            "current_step": current_step_name(req), "created_at": iso(app.created_at),
            "can_cancel": req.requester_id == user.pk and req.status not in Request.CLOSED_STATUSES}


def overview(user, year=None):
    from leave.services import balance_for, balances_for, dashboard_data

    staff = staff_of(user)
    if staff is None:
        return {"staff_linked": False, "can_apply": False, "balances": [], "current": None, "upcoming": None,
                "resumption_date": None, "days_until_upcoming": None}
    data = dashboard_data(user)
    year = year or timezone.localdate().year
    balance = data["balance"] if year == data["balance"]["year"] else balance_for(staff, year)
    return {
        "staff_linked": True,
        "can_apply": leave_type_available() is not None,
        "balances": [balance_item(balance)] + [balance_item(b) for b in balances_for(staff, year)
                                               if b["leave_type"].code != "annual"],
        "current": application_item(data["current"], user) if data["current"] else None,
        "upcoming": application_item(data["upcoming"], user) if data["upcoming"] else None,
        "resumption_date": data["resumption_date"],
        "days_until_upcoming": data["days_until_upcoming"],
    }


def applications(user, status="all"):
    from leave.models import LeaveApplication
    from leave.services import APPROVED_REQUEST_STATUSES, PENDING_REQUEST_STATUSES

    staff = staff_of(user)
    if staff is None:
        return LeaveApplication.objects.none()
    qs = (LeaveApplication.objects.filter(staff=staff, request__is_deleted=False)
          .select_related("request", "request__current_step", "request__request_type", "staff__user"))
    if status == "pending":
        qs = qs.filter(request__status__in=PENDING_REQUEST_STATUSES)
    elif status == "approved":
        qs = qs.filter(request__status__in=APPROVED_REQUEST_STATUSES)
    elif status == "closed":
        qs = qs.filter(request__status__in=("rejected", "cancelled"))
    return qs.order_by("-start_date", "-pk")


def _form_data(values):
    """Form data for leave.services from the API body (v2.1: any type; a
    days-mode type takes ``days`` instead of ``end_date``)."""
    data = {"start_date": values["start_date"].isoformat()}
    if values.get("leave_type"):
        data["leave_type"] = values["leave_type"]
    if values.get("days"):
        data["days"] = values["days"]
    if values.get("end_date"):
        data["end_date"] = values["end_date"].isoformat()
    return data


def apply(user, values):
    from rest_framework import serializers

    from .requests import create

    leave_type = leave_type_available()
    if leave_type is None:
        raise serializers.ValidationError({"non_field_errors": ["Leave applications are closed."]})
    if not values.get("leave_type") and not values.get("days"):
        # v2 body: annual leave, dates only (kept unchanged for the mobile app)
        start, end = values["start_date"], values["end_date"]
        subject = (values.get("subject") or "").strip() or "Annual leave application"
        body = (values.get("body") or "").strip() or f"Annual leave from {start:%d %b %Y} to {end:%d %b %Y}."
        req = create(user, leave_type, {"subject": subject, "body": body,
                                        "priority": values.get("priority") or "normal",
                                        "fields": {"start_date": start, "end_date": end}})
        return req.annual_leave_application
    from leave import services as leave_services

    from .work import run

    req = run(leave_services.apply_for_leave, user, _form_data(values), reason=values.get("body") or "",
              require_document=False)  # no upload in the app: HR asks on the request
    return req.annual_leave_application


def preview(user, start, end, leave_type=None, days=None):
    from leave.services import balance_for, check_application, counted_days, resumption_date

    from .work import run

    values = {"start_date": start, "end_date": end, "leave_type": leave_type, "days": days}
    check = run(check_application, user, _form_data(values))
    start, end, lt = check.start_date, check.end_date, check.leave_type
    first_year_end = min(end, date(start.year, 12, 31))
    days_in_start_year = counted_days(start, first_year_end, lt, user=user)
    balance = balance_for(check.staff, start.year, lt)
    before = after = None
    over = False
    if balance["is_set"]:
        before = balance["remaining"]
        after = before - days_in_start_year
        over = after < 0
    return {"start_date": start, "end_date": end, "working_days": check.days,
            "resumption_date": resumption_date(end, user=user),
            "remaining_before": str(before) if before is not None else None,
            "remaining_after": str(after) if after is not None else None, "is_over_balance": over,
            "leave_type": lt.code, "leave_type_name": lt.name}


def cancel(user, app, reason=""):
    from workflow import services

    from .work import run

    return run(services.cancel_request, app.request, user, comment=reason)


def approvals(user):
    from dashboards.action_queue import actionable_requests
    from leave.services import request_summary

    out = []
    for req in actionable_requests(user):
        if req.request_type.code != LEAVE_CODE:
            continue
        summary = request_summary(req)
        app = summary["application"] if summary else None
        dept = req.requester.department if req.requester.department_id else None
        out.append({
            "request_id": req.pk, "reference": req.reference, "requester": user_ref(req.requester),
            "department": {"id": dept.pk, "name": dept.name} if dept else None,
            "start_date": app.start_date if app else None, "end_date": app.end_date if app else None,
            "requested_days": str(summary["requested_days"]) if summary else None,
            "has_over_balance": bool(summary and summary["has_over_balance"]),
            "step": req.active_step.name,
            "waiting_since": iso(req.current_step_started_at or req.created_at),
        })
    return out


def holidays(user, year):
    from portal.services import live_holidays

    return list(live_holidays(user=user, from_date=date(year, 1, 1), to_date=date(year, 12, 31),
                              include_recurring=True))


def holiday_item(h):
    return {"id": h.pk, "name": h.name, "kind": h.kind, "date_start": h.date_start, "date_end": h.date_end,
            "is_recurring": h.is_recurring, "description": h.description or ""}

