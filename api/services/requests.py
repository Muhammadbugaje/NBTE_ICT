"""requests/ - the website's workflow requests (the workflow app's models are
never touched here).

Reuses: workflow.access.can_view_request (detail/actions/downloads, 404 when
false), workflow.forms.build_request_form / build_resubmit_form /
extract_form_data / validate_schema (dynamic per-type forms),
workflow.services.raise_request_checked / resubmit_checked (leave checks +
sync), approve_step / return_step / return_to_requester / reject_step /
assign_staff / mark_fulfilled / confirm_handover / cancel_request /
add_comment / add_attachment, _resolve_current_step + resolve_approver
(current actor) and build_runtime_steps (the chain),
dashboards.action_queue.actionable_requests (the "Awaiting my action" queue)
and leave.services.request_summary (leave days vs balance).

The ``actions`` flags mirror the buttons of templates/workflow/detail.html.
"""
from datetime import date

from django.db.models import Q

from .work import activity_ref, attachment_ref, iso, user_ref

LEAVE_CODE = "annual-leave"


def _ref(obj):
    return {"id": obj.pk, "name": obj.name} if obj is not None else None


def _code_ref(obj):
    return {"id": obj.pk, "code": obj.code, "name": obj.name} if obj is not None else None


# ---------------------------------------------------------------------------
# Types and their forms
# ---------------------------------------------------------------------------
def active_types():
    from workflow.models import RequestType

    return (RequestType.objects.filter(is_active=True)
            .select_related("category", "handling_department")
            .order_by("category__sort_order", "sort_order", "name"))


def schema_defs(request_type):
    from django.core.exceptions import ValidationError

    from workflow.forms import validate_schema

    if not request_type.form_schema:
        return []
    try:
        return validate_schema(request_type.form_schema)
    except ValidationError:
        return []


def field_items(request_type, requester=None, with_options=False):
    from workflow.forms import _asset_choices, _user_choices

    out = []
    for d in schema_defs(request_type):
        options = None
        if with_options:
            if d["type"] == "select":
                options = [{"value": str(o), "label": str(o)} for o in d.get("options") or []]
            elif d["type"] == "asset_picker":
                options = [{"value": v, "label": lbl}
                           for v, lbl in _asset_choices(requester, d.get("category_codes") or None)]
            elif d["type"] == "user_picker":
                options = [{"value": v, "label": lbl} for v, lbl in _user_choices(requester)]
        out.append({"key": d["key"], "label": d["label"], "type": d["type"], "required": bool(d.get("required")),
                    "help": d.get("help") or "",
                    "max_length": int(d.get("max_length") or 255) if d["type"] == "text" else None,
                    "options": options})
    return out


def type_item(t, requester=None, with_options=False):
    return {"id": t.pk, "code": t.code, "name": t.name, "description": t.description or "",
            "icon": t.icon or "", "category": _code_ref(t.category) if t.category_id else None,
            "handling_department": _ref(t.handling_department), "is_leave": t.code == LEAVE_CODE,
            "fields": field_items(t, requester, with_options)}


def form_post_data(request_type, base, values):
    """API JSON -> the POST dict build_request_form expects."""
    data = dict(base)
    for d in schema_defs(request_type):
        key = d["key"]
        if key not in values or values[key] is None:
            continue
        value = values[key]
        if d["type"] == "checkbox":
            if value in (True, "true", "on", "1", 1):
                data[key] = "on"
        elif isinstance(value, date):
            data[key] = value.isoformat()
        else:
            data[key] = str(value)
    return data


def bound_form_errors(form):
    from .work import form_errors

    # Schema fields are sent under "fields": report them as fields.<key>.
    common = {"subject", "body", "priority", "comment", "file", "file_description"}
    form_errors(form, rename={k: f"fields.{k}" for k in form.errors if k not in common and k != "__all__"})


def create(user, request_type, values):
    from workflow import services
    from workflow.forms import build_request_form, extract_form_data

    from .work import run

    data = form_post_data(request_type, {"subject": values["subject"], "body": values["body"],
                                         "priority": values.get("priority") or "normal"}, values.get("fields") or {})
    form = build_request_form(request_type, user, data=data)
    if not form.is_valid():
        bound_form_errors(form)
    form_data = extract_form_data(form, request_type)
    return run(services.raise_request_checked, requester=user, request_type=request_type,
               subject=form.cleaned_data["subject"], body=form.cleaned_data["body"],
               priority=form.cleaned_data["priority"], form_data=form_data)


def resubmit(req, user, values, file=None, file_description=""):
    from workflow import services
    from workflow.forms import build_resubmit_form, extract_form_data

    from .work import run

    data = form_post_data(req.request_type, {"subject": values["subject"], "body": values["body"],
                                             "comment": values.get("comment") or ""}, values.get("fields") or {})
    form = build_resubmit_form(req, user, data=data)
    if not form.is_valid():
        bound_form_errors(form)
    form_data = extract_form_data(form, req.request_type)
    return run(services.resubmit_checked, req, user, subject=form.cleaned_data["subject"],
               body=form.cleaned_data["body"], comment=form.cleaned_data.get("comment") or "",
               form_data=form_data, file=file, file_description=file_description)


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------
def list_items(user, params):
    """mine / assigned: workflow.views.my_requests / assigned_to_me;
    awaiting: the "Awaiting my action" queue (requests part)."""
    from workflow.models import Request

    scope = params.get("scope") or "mine"
    if scope == "awaiting":
        from dashboards.action_queue import actionable_requests

        items = actionable_requests(user)
        q = (params.get("q") or "").strip().lower()
        if q:
            items = [r for r in items if q in r.subject.lower() or q in r.reference.lower()]
        if params.get("type"):
            items = [r for r in items if r.request_type.code == params["type"]]
        return items
    base = Request.objects.filter(requester=user) if scope == "mine" else Request.objects.filter(assigned_to=user)
    qs = base.select_related("request_type", "requester", "receiving_department", "assigned_to", "current_step")
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(subject__icontains=q) | Q(reference__icontains=q))
    status = params.get("status") or "all"
    if status == "active":
        qs = qs.exclude(status__in=Request.CLOSED_STATUSES)
    elif status in dict(Request.STATUS_CHOICES):
        qs = qs.filter(status=status)
    if params.get("type"):
        qs = qs.filter(request_type__code=params["type"])
    return qs.order_by("-created_at")


def current_step_name(req):
    step = getattr(req, "active_step", None)
    if step is None and not req.is_closed:
        if req.current_step_id and req.current_step is not None:
            step = req.current_step
        else:
            from workflow import services

            step = services._resolve_current_step(req)
    return step.name if step is not None and not req.is_closed else ""


def list_item(req):
    return {
        "id": req.pk, "reference": req.reference, "subject": req.subject, "status": req.status,
        "status_display": req.get_status_display(), "priority": req.priority,
        "request_type": _code_ref(req.request_type), "requester": user_ref(req.requester),
        "receiving_department": _ref(req.receiving_department), "assigned_to": user_ref(req.assigned_to),
        "current_step": current_step_name(req),
        "created_at": iso(req.created_at), "updated_at": iso(req.updated_at),
    }


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------
def get_visible(user, pk):
    """The request if this user may open it (workflow.access), else None."""
    from workflow.access import can_view_request
    from workflow.models import Request

    req = (Request.objects.select_related("requester", "request_type", "receiving_department", "assigned_to",
                                          "current_step", "returned_by").filter(pk=pk).first())
    if req is None or not can_view_request(user, req):
        return None
    return req


def _chain(req, current):
    from workflow import services
    from workflow.models import Request

    steps = services.build_runtime_steps(req)
    cur_key = services._step_key(current) if current is not None else None
    idx = next((i for i, st in enumerate(steps) if services._step_key(st) == cur_key), None)
    out = []
    for i, st in enumerate(steps):
        if req.status == Request.COMPLETED:
            state = "done"
        elif idx is None:
            state = "upcoming"
        else:
            state = "done" if i < idx else ("current" if i == idx else "upcoming")
        out.append({"name": st.name, "kind": st.kind, "state": state})
    return out


def leave_summary(req):
    if req.request_type.code != LEAVE_CODE:
        return None
    from leave.services import request_summary

    summary = request_summary(req)
    if summary is None:
        return None
    app = summary["application"]
    return {"leave_type": app.leave_type.code, "leave_type_name": app.leave_type.name,
            "start_date": app.start_date, "end_date": app.end_date,
            "requested_days": str(summary["requested_days"]), "has_over_balance": summary["has_over_balance"],
            "years": [{"year": y["year"], "requested_days": str(y["requested_days"]),
                       "remaining": str(y["remaining"]), "is_set": y["is_set"],
                       "over_balance_days": str(y["over_balance_days"]),
                       "is_over_balance": y["is_over_balance"]} for y in summary["years"]]}


def actions(req, user, step, actor):
    """templates/workflow/detail.html button conditions."""
    from workflow.models import Request

    open_ = not req.is_closed and req.status != Request.RETURNED
    can_act = bool(actor and actor.pk == user.pk) and open_
    kind = step.kind if step is not None else None
    is_requester = req.requester_id == user.pk
    return {
        "can_approve": can_act and kind == "approval",
        "can_return": can_act and kind in ("approval", "fulfillment", "closure"),
        "can_return_to_requester": can_act and kind in ("approval", "assignment"),
        "can_reject": can_act and kind == "approval",
        "can_assign": can_act and kind == "assignment",
        "can_mark_fulfilled": can_act and kind == "fulfillment",
        "can_confirm_handover": can_act and kind == "closure",
        "can_cancel": is_requester and not req.is_closed,
        "can_resubmit": is_requester and req.status == Request.RETURNED,
        "can_comment": not req.is_closed,
        "can_comment_internal": not req.is_closed and (user.is_superuser or not is_requester),
        "can_attach": not req.is_closed,
    }


def detail(req, user, request=None):
    from workflow import services

    current = services._resolve_current_step(req)
    actor = services.resolve_approver(current, req) if current else None
    is_requester = req.requester_id == user.pk
    show_internal = user.is_superuser or not is_requester
    comments = req.comments.select_related("author").order_by("created_at")
    if not show_internal:
        comments = comments.filter(is_internal=False)
    labels = {d["key"]: d["label"] for d in schema_defs(req.request_type)}
    data = list_item(req)
    data["current_step"] = current.name if current is not None and not req.is_closed else ""
    data.update({
        "body": req.body,
        "completed_at": iso(req.completed_at),
        "closed_at": iso(req.closed_at),
        "revision_count": req.revision_count or 0,
        "returned_by": user_ref(req.returned_by),
        "returned_at": iso(req.returned_at),
        "form_data": [{"key": k, "label": labels.get(k, k.replace("_", " ").title()), "value": v}
                      for k, v in (req.form_data or {}).items()],
        "step": ({"name": current.name, "kind": current.kind, "instructions": current.instructions or "",
                  "actor": user_ref(actor), "is_me": bool(actor and actor.pk == user.pk)}
                 if current is not None and not req.is_closed else None),
        "chain": _chain(req, current),
        "approvals": [{"step": a.step.name if a.step_id else "", "actor": user_ref(a.actor),
                       "actor_role": a.actor_role or "", "decision": a.decision,
                       "decision_display": a.get_decision_display(), "comment": a.comment or "",
                       "decided_at": iso(a.decided_at)}
                      for a in req.approvals.select_related("step", "actor").order_by("decided_at")],
        "comments": [{"id": c.pk, "author": user_ref(c.author), "body": c.body, "is_internal": c.is_internal,
                      "created_at": iso(c.created_at)} for c in comments],
        "attachments": [attachment_ref(a, "requests", req.pk, request)
                        for a in req.attachments.select_related("uploaded_by").order_by("-created_at")],
        "activity": [activity_ref(a) for a in req.timeline().select_related("actor")],
        "leave": leave_summary(req),
        "has_record_pdf": bool(req.record_pdf),
        "actions": actions(req, user, current, actor),
    })
    return data
