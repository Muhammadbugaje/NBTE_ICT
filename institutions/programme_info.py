"""Programme facts shared by the Programmes tab, the Programme detail page
(internal and portal) and the institutional record (Phase 8).

* :func:`bodies_map` — the professional bodies that regulate each programme,
  through the NBTE-wide ``ProgrammeBodyMapping`` (programme name + level).
* :func:`open_work` — the QA request or case each programme is in now, with
  the stage it is at and the internal page where it is handled.
* :func:`expiry` — days to expiry and a tone for the countdown.
* :func:`detail` — everything about one programme a staff user may see.

Every read that leaves this module for a view is scoped by the caller's user
(``for_user`` or the app's ``visible_*`` helper).
"""
# scoped-by-caller
from __future__ import annotations

import datetime

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import ProgrammeBodyMapping, programme_key

SOON_DAYS = 180


# ===========================================================================
# Professional bodies
# ===========================================================================
def bodies_map(programmes):
    """{programme.pk: [ProfessionalBody]} — one query for every programme."""
    programmes = list(programmes)
    keys = {programme_key(p.name) for p in programmes}
    rows = (ProgrammeBodyMapping.objects.filter(programme_key__in=keys, is_active=True, body__is_active=True)
            .select_related("body"))
    by_key = {}
    for m in rows:
        by_key.setdefault(m.programme_key, []).append(m)
    out = {}
    for p in programmes:
        bodies = {m.body for m in by_key.get(programme_key(p.name), []) if m.level_id in (None, p.level_id)}
        out[p.pk] = sorted(bodies, key=lambda b: b.name)
    return out


def programmes_of_body(body, user):
    """Every programme (across the institutions ``user`` may see) that ``body``
    regulates."""
    from .models import Programme

    mappings = list(ProgrammeBodyMapping.objects.filter(body=body, is_active=True))
    if not mappings:
        return Programme.objects.for_user(user).none()
    names_by_key = {}
    for name in set(Programme.unscoped.values_list("name", flat=True)):
        names_by_key.setdefault(programme_key(name), set()).add(name)
    q = Q()
    for m in mappings:
        part = Q(name__in=sorted(names_by_key.get(m.programme_key, {m.programme_name})))
        if m.level_id:
            part &= Q(level_id=m.level_id)
        q |= part
    return (Programme.objects.for_user(user).filter(q).exclude(status="dropped")
            .select_related("institution", "level", "category").order_by("institution__name", "name"))


# ===========================================================================
# Open work (the request / case a programme is in)
# ===========================================================================
def _stage_of(subject, flow_code):
    from qa_core import flow

    instance = flow.instance_for(subject, flow_code)
    if instance is None:
        return None, ""
    stages = list(instance.flow_version.stages.filter(code__in=instance.current_stages or []))
    if not stages:
        return None, ""
    stage = stages[0]
    return stage.sop_stage_no, stage.label


def open_work(programmes, user=None):
    """{programme.pk: {"kind", "reference", "stage_no", "stage", "url", "obj"}}."""
    from accreditation.models import QACase, QARequestProgramme
    from accreditation.qa_services import OPEN_STATUSES

    ids = [p.pk for p in programmes]
    if not ids:
        return {}
    rows = (QARequestProgramme.unscoped.filter(programme_id__in=ids, status=QARequestProgramme.ACTIVE)
            .select_related("qa_request", "qa_request__qa_type").order_by("-qa_request__created_at"))
    out = {}
    for row in rows:
        if row.programme_id in out:
            continue
        req = row.qa_request
        case = QACase.unscoped.filter(qa_request=req).first()
        if case is not None and case.completed_at is None and case.status not in ("closed", "completed"):
            no, label = _stage_of(case, "qa_case")
            out[row.programme_id] = {
                "kind": "case", "reference": case.reference, "stage_no": no,
                "stage": label or case.status.replace("_", " "), "obj": case,
                "url": reverse("qa_case:detail", args=[case.pk]),
                "type": req.qa_type.name if req.qa_type_id else "QA case",
                "action": f"Open the QA case at stage {no}" if no else "Open the QA case"}
        elif req.status in OPEN_STATUSES:
            no, label = _stage_of(req, req.flow_code)
            out[row.programme_id] = {
                "kind": "request", "reference": req.reference, "stage_no": no,
                "stage": label or req.status.replace("_", " "), "obj": req,
                "url": reverse("qa_req:detail", args=[req.pk]),
                "type": req.qa_type.name if req.qa_type_id else "QA request",
                "action": f"Open the QA request ({label or req.status.replace('_', ' ')})"}
    return out


# ===========================================================================
# Expiry
# ===========================================================================
def expiry(programme, today=None):
    today = today or timezone.localdate()
    if not programme.expiry_date or programme.status == "dropped":
        return {"days": None, "tone": "", "label": ""}
    days = (programme.expiry_date - today).days
    if days < 0:
        return {"days": days, "tone": "is-past", "label": f"expired {abs(days)} day{'s' if abs(days) != 1 else ''} ago"}
    tone = "is-soon" if days <= SOON_DAYS else ""
    return {"days": days, "tone": tone, "label": "expires today" if days == 0 else f"{days} day{'s' if days != 1 else ''} left"}


def approval_type_name(programme):
    if any(f.name == "approval_type" for f in type(programme)._meta.get_fields()):
        obj = getattr(programme, "approval_type", None)
        if obj is not None:
            return str(getattr(obj, "name", obj))
    return ""


# ===========================================================================
# Everything about one programme (internal)
# ===========================================================================
def _optional_model(app_label, name):
    from django.apps import apps

    try:
        return apps.get_model(app_label, name)
    except LookupError:
        return None


def detail(programme, user):
    from accreditation import case_services as cs
    from accreditation.models import SSQ, Query, QACase, QARequest, QARequestProgramme
    from inspection import services as insp
    from qa_core.models import TimelineEvent

    inst = programme.institution
    rows = list(QARequestProgramme.objects.for_user(user).filter(programme=programme)
                .select_related("qa_request", "qa_request__qa_type").order_by("-qa_request__created_at"))
    request_ids = [r.qa_request_id for r in rows]
    requests = list(QARequest.objects.for_user(user).filter(pk__in=request_ids).select_related("qa_type")
                    .order_by("-created_at"))
    ssqs = list(SSQ.objects.for_user(user).filter(qa_request_programme__programme=programme)
                .select_related("qa_request_programme__qa_request").order_by("-created_at", "-version"))
    cases = list(cs.visible_cases(user).filter(qa_request_id__in=request_ids).order_by("-created_at"))
    for case in cases:
        case.stage_no, case.stage_label = _stage_of(case, "qa_case")
    inspections = list(insp.visible_inspections(user).filter(case__qa_request_id__in=request_ids)
                       .order_by("-scheduled_start"))
    reports = list(insp.visible_reports(user).filter(qa_request_programme__programme=programme)
                   .select_related("inspection").order_by("-pk"))
    ssq_ct = ContentType.objects.get_for_model(SSQ)
    prog_ct = ContentType.objects.get_for_model(type(programme))
    queries = list(Query.objects.for_user(user).filter(institution=inst).filter(
        Q(content_type=ssq_ct, object_id__in=[str(s.pk) for s in ssqs])
        | Q(content_type=prog_ct, object_id=str(programme.pk))
        | Q(subject_label__icontains=programme.name)).order_by("-created_at"))
    complaints = []
    try:
        from complaints import services as complaint_services

        complaints = list(complaint_services.visible_to(user).filter(institution=inst).filter(
            Q(related_type=prog_ct, related_id=str(programme.pk))
            | Q(related_type=ContentType.objects.get_for_model(QARequest), related_id__in=[str(i) for i in request_ids])
            | Q(related_label__icontains=programme.name) | Q(subject__icontains=programme.name))
            .order_by("-created_at")[:50])
    except Exception:  # complaints app not installed in a slim deployment
        complaints = []
    decisions = []
    for app_label, name in (("accreditation", "Decision"), ("accreditation", "OutcomeLetter")):
        model = _optional_model(app_label, name)
        if model is None:
            continue
        fields = {f.name for f in model._meta.get_fields()}
        qs = getattr(model, "objects")
        qs = qs.for_user(user) if hasattr(qs, "for_user") else qs.all()
        if "programme" in fields:
            qs = qs.filter(programme=programme)
        elif "qa_request_programme" in fields:
            qs = qs.filter(qa_request_programme__programme=programme)
        elif "case" in fields:
            qs = qs.filter(case__in=[c.pk for c in cases])
        else:
            continue
        for obj in qs[:50]:
            decisions.append({"kind": model._meta.verbose_name.capitalize(), "obj": obj,
                              "date": getattr(obj, "created_at", None)})
    # The timeline filtered to this programme: events about it or about the
    # requests, SSQs, cases and inspections it was part of, or naming it.
    subjects = Q(content_type=prog_ct, object_id=str(programme.pk))
    for model, pks in ((QARequestProgramme, [r.pk for r in rows]), (SSQ, [s.pk for s in ssqs]),
                       (QACase, [c.pk for c in cases])):
        if pks:
            subjects |= Q(content_type=ContentType.objects.get_for_model(model), object_id__in=[str(x) for x in pks])
    subjects |= Q(public_text__icontains=programme.name) | Q(internal_text__icontains=programme.name)
    events = TimelineEvent.objects.for_user(user).filter(institution=inst).filter(subjects)
    if not can_see_internal(user, inst):
        events = events.filter(visibility="public")
    history = list(events.filter(Q(event_code__startswith="programme") | Q(event_code__contains="decision")
                                 | Q(event_code__contains="accredit")).order_by("-at", "-id")[:50])
    return {"rows": rows, "requests": requests, "ssqs": ssqs, "cases": cases, "inspections": inspections,
            "reports": reports, "queries": queries, "complaints": complaints, "decisions": decisions,
            "events": events.select_related("actor").order_by("-at", "-id"), "history": history}


def can_see_internal(user, inst):
    return bool(user.is_superuser or user.has_perm("qa_core.view_timeline_internal")
                or user.department_id == inst.handling_department_id)


def jumps(programme, info, work):
    """Buttons that take the officer to the step that is active now."""
    out = []
    if work:
        out.append({"label": work["action"], "url": work["url"], "primary": True})
    for ssq in info["ssqs"]:
        if ssq.is_current and ssq.status == "submitted":
            out.append({"label": f"Review the SSQ (version {ssq.version})",
                        "url": reverse("qa_req:ssq", args=[ssq.pk]), "primary": not out})
            break
    for inspection in info["inspections"]:
        if inspection.status == "scheduled":
            out.append({"label": f"Open the visit {inspection.reference} "
                                 f"({inspection.scheduled_start:%d %b %Y})",
                        "url": reverse("qa_insp:inspection", args=[inspection.pk]), "primary": False})
            break
    for report in info["reports"]:
        if report.status and report.status not in ("director_endorsed", "closed"):
            out.append({"label": "Open the post-visit report", "url": reverse("qa_insp:report", args=[report.pk]),
                        "primary": False})
            break
    open_queries = [q for q in info["queries"] if q.status == "open"]
    if open_queries:
        out.append({"label": f"Open query {open_queries[0].reference}",
                    "url": reverse("qa_req:query", args=[open_queries[0].pk]), "primary": False})
    return out


def days_label(date):
    if not date:
        return ""
    if isinstance(date, datetime.datetime):
        date = date.date()
    return date.strftime("%d %b %Y")
