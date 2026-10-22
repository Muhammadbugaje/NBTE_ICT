"""Portal door: resource persons (engagements and findings, service history,
profile & CV) and the institution's read-only inspections list — JSON
shapes of what ``inspection.portal_views`` shows. Every query goes through
``inspection.services`` (rp_* helpers filter by the signed-in person)."""
from django.db.models import Q
from django.http import Http404
from django.utils import timezone

from .portal import humanize


def engagements_queryset(rp, params):
    """inspection.portal_views.engagements' filters."""
    from inspection import services
    from inspection.models import QAMapping

    qs = services.rp_engagements(rp)
    status = params.get("status") or "all"
    if status == "live":
        qs = qs.filter(status__in=QAMapping.LIVE)
    elif status == "released":
        qs = qs.filter(status="released")
    if params.get("duty"):
        qs = qs.filter(duty=params["duty"])
    today = timezone.localdate()
    if params.get("when") == "upcoming":
        qs = qs.filter(to_date__gte=today)
    elif params.get("when") == "past":
        qs = qs.filter(to_date__lt=today)
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(case__institution__name__icontains=q)
                       | Q(qa_request_programme__programme__name__icontains=q))
    return qs


def engagement_or_404(rp, pk):
    from inspection import services

    m = services.rp_engagements(rp).filter(pk=pk).first()
    if m is None:
        raise Http404
    return m


def status_label(m):
    """The engagements page's chip text."""
    if m.status == "released":
        return "Completed"
    if m.status == "proposed":
        return "Awaiting confirmation"
    return m.get_status_display()


def my_role(m):
    """The role on the visit team (the home screen's rule)."""
    from inspection.models import InspectionTeamMember

    role = (InspectionTeamMember.unscoped.filter(qa_mapping=m).exclude(role_on_team="")
            .values_list("role_on_team", flat=True).first())
    return role or m.get_duty_display()


def engagement_row(m):
    from inspection import services

    can_write, why = services.findings_open(m)
    programme = m.qa_request_programme.programme
    return {"id": m.pk, "programme": {"id": programme.pk, "name": str(programme)},
            "institution": m.case.institution.name, "duty": m.duty, "duty_label": m.get_duty_display(),
            "from_date": m.from_date, "to_date": m.to_date, "status": m.status, "status_label": status_label(m),
            "role": my_role(m), "findings": m.findings or "", "can_write_findings": bool(can_write),
            "findings_closed_reason": "" if can_write else str(why)}


def engagement_detail_payload(m):
    from inspection import services
    from inspection.models import VISIT_DUTIES

    out = engagement_row(m)
    visit = None
    if m.duty in VISIT_DUTIES:
        insp = services.live_inspection(m.case)
        if insp is not None:
            visit = {"reference": insp.reference, "kind": insp.kind, "kind_label": insp.get_kind_display(),
                     "scheduled_start": insp.scheduled_start, "scheduled_end": insp.scheduled_end,
                     "status": insp.status, "status_label": humanize(insp.status)}
    out.update({"case_reference": m.case.reference, "visit": visit})
    return out


def history_queryset(rp, params):
    """inspection.portal_views.history's filters."""
    from inspection import services

    qs = services.rp_service(rp)
    if params.get("duty"):
        qs = qs.filter(duty=params["duty"])
    if str(params.get("year", "")).isdigit():
        qs = qs.filter(served_from__year=int(params["year"]))
    if params.get("work") == "visits":
        qs = qs.filter(visit_count__gt=0)
    elif params.get("work") == "ssq":
        qs = qs.filter(ssq_count__gt=0)
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(institution__name__icontains=q) | Q(programme__name__icontains=q))
    return qs


def history_row(r):
    return {"id": r.pk, "served_from": r.served_from, "served_to": r.served_to, "duty": r.duty,
            "duty_label": r.get_duty_display(), "is_correction": bool(r.voids_id),
            "institution": r.institution.name if r.institution_id else "",
            "programme": r.programme.name if r.programme_id else "", "role": r.role_on_team or "",
            "visit_count": r.visit_count, "ssq_count": r.ssq_count,
            "allowance_status": "paid" if r.allowance_paid else "pending"}


def history_summary(rp, params):
    from inspection import services
    from inspection.models import DUTY_CHOICES

    rows = list(history_queryset(rp, params))
    return {"visits": sum(r.visit_count for r in rows), "ssqs": sum(r.ssq_count for r in rows),
            "years": sorted({r.served_from.year for r in services.rp_service(rp)}, reverse=True),
            "duties": [{"value": v, "label": str(lab)} for v, lab in DUTY_CHOICES]}


def profile_payload(rp):
    """inspection.portal_views.profile."""
    from inspection import services
    from inspection.models import ResourcePersonFile

    lapsed = services.rules.nomination_problems(rp) if services.rules.no_longer_eligible(rp) else []
    return {
        "id": rp.pk, "full_name": rp.full_name, "email": rp.email, "phone": rp.phone or "",
        "status": rp.status, "status_label": rp.get_status_display(), "source": rp.source,
        "source_label": rp.get_source_display(), "rank": rp.rank or "", "rank_label": humanize(rp.rank),
        "qualifications": rp.qualifications or "",
        "disciplines": [{"name": d.discipline.name, "is_primary": bool(d.is_primary)}
                        for d in rp.disciplines.select_related("discipline")],
        "lapsed": [str(p) for p in lapsed],
        "files": [{"id": f.pk, "kind": f.kind, "kind_label": f.get_kind_display(),
                   "name": f.original_name or f.file.name.rsplit("/", 1)[-1], "uploaded_at": f.created_at}
                  for f in services.rp_files(rp)],
        "file_kinds": [{"value": v, "label": str(lab)} for v, lab in ResourcePersonFile.KIND_CHOICES],
        "editable_fields": [],
    }


def inspections_queryset(user, params):
    """inspection.portal_views.institution_inspections' filters."""
    from inspection import services

    qs = services.institution_inspections(user)
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(case__reference__icontains=q))
    today = timezone.localdate()
    if params.get("when") == "upcoming":
        qs = qs.filter(scheduled_start__gte=today)
    elif params.get("when") == "past":
        qs = qs.filter(scheduled_start__lt=today)
    if str(params.get("year", "")).isdigit():
        qs = qs.filter(scheduled_start__year=int(params["year"]))
    return qs


def inspection_row(i):
    return {"id": i.pk, "reference": i.reference, "kind": i.kind, "kind_label": i.get_kind_display(),
            "scheduled_start": i.scheduled_start, "scheduled_end": i.scheduled_end,
            "actual_start": i.actual_start, "actual_end": i.actual_end, "status": i.status,
            "status_label": "Visit recorded" if i.status == "closed" else humanize(i.status)}
