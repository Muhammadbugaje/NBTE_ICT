"""Programme detail pages (Phase 8).

* Internal (namespace "qa_inst"): /qa/institutions/<code>/programmes/<pk>/ —
  status and accreditation history, approval type and dates, streams, the
  professional bodies (linked to their pages), every QA request, SSQ, case,
  visit, report, decision / letter, query and complaint involving the
  programme, its timeline, and buttons that jump to the step active now.
* Portal (namespace "qa_portal"): /programmes/ and /programmes/<pk>/ — the
  institution's own programmes, public information only.

Every read is scoped with ``.for_user(request.user)`` (or the app's
``visible_*`` helper); a programme outside the user's scope answers 404.
"""
from __future__ import annotations

from django.db.models import Q
from django.http import Http404
from django.views.decorators.http import require_GET

from qa_core.permissions import can_view_institutions, require
from qa_core.portal_views import institution_required
from qa_core.ui import paginate, render_qa

from . import programme_info
from .models import Institution, Programme


def _programme_or_404(request, code, pk):
    inst = (Institution.objects.for_user(request.user).select_related("handling_department")
            .filter(code=code.upper()).first())
    if inst is None:
        raise Http404("No such institution in your scope.")
    programme = (Programme.objects.for_user(request.user).filter(institution=inst, pk=pk)
                 .select_related("level", "category", "category__discipline", "institution").first())
    if programme is None:
        raise Http404("No such programme.")
    return inst, programme


@require(can_view_institutions)
@require_GET
def programme_detail(request, code, pk):
    inst, programme = _programme_or_404(request, code, pk)
    info = programme_info.detail(programme, request.user)
    work = programme_info.open_work([programme], request.user).get(programme.pk)
    events = info.pop("events")
    tq = (request.GET.get("tq") or "").strip()
    if tq:
        events = events.filter(Q(public_text__icontains=tq) | Q(internal_text__icontains=tq))
    ctx = {"inst": inst, "p": programme, "info": info, "work": work,
           "bodies": programme_info.bodies_map([programme]).get(programme.pk, []),
           "expiry_info": programme_info.expiry(programme),
           "approval_type": programme_info.approval_type_name(programme),
           "jumps": programme_info.jumps(programme, info, work),
           "page": paginate(request, events), "tq": tq}
    return render_qa(request, "institutions/programme.html", ctx,
                     {"qa-panel": "institutions/_programme_panel.html",
                      "qa-prog-tl": "institutions/_programme_timeline.html"})


# ===========================================================================
# Portal: the institution's own programmes (public information only)
# ===========================================================================
@institution_required
@require_GET
def portal_programmes(request):
    from qa_core.portal_views import _shell_context

    from .portal_views import _inst

    inst = _inst(request)
    if inst is None:
        raise Http404
    qs = Programme.objects.for_user(request.user).filter(institution=inst).exclude(status="dropped") \
        .select_related("level", "category")
    f = request.GET
    q = (f.get("q") or "").strip()
    if q:
        qs = qs.filter(name__icontains=q)
    if f.get("status"):
        qs = qs.filter(status=f["status"])
    if f.get("level"):
        qs = qs.filter(level__code=f["level"])
    programmes = list(qs.order_by("name", "level__sort_order"))
    bodies = programme_info.bodies_map(programmes)
    page = paginate(request, programmes)
    for p in page["rows"]:
        p.bodies = bodies.get(p.pk, [])
        p.expiry_info = programme_info.expiry(p)
    from .models import ProgrammeLevel

    ctx = _shell_context(request, "programmes", page=page, filters=f, statuses=[
        s for s in Programme.STATUS_CHOICES if s[0] != "dropped"],
        levels=ProgrammeLevel.objects.filter(is_active=True))
    return render_qa(request, "qa_portal/programmes/list.html", ctx,
                     {"qa-prog-rows": "qa_portal/programmes/_rows.html"})


@institution_required
@require_GET
def portal_programme(request, pk):
    from accreditation.models import QARequestProgramme
    from qa_core.models import TimelineEvent
    from qa_core.portal_views import _shell_context

    from .models import BodyLetter

    from .portal_views import _inst

    inst = _inst(request)
    programme = (Programme.objects.for_user(request.user).filter(pk=pk).exclude(status="dropped")
                 .select_related("level", "category", "institution").first()) if inst is not None else None
    if programme is None or programme.institution_id != inst.pk:
        raise Http404
    rows = list(QARequestProgramme.objects.for_user(request.user).filter(programme=programme)
                .select_related("qa_request", "qa_request__qa_type").order_by("-qa_request__created_at"))
    from accreditation import qa_services

    for row in rows:
        row.public_status = qa_services.public_status(row.qa_request)
    from django.contrib.contenttypes.models import ContentType

    ct = ContentType.objects.get_for_model(Programme)
    events = list(TimelineEvent.objects.for_user(request.user).filter(visibility="public").filter(
        Q(content_type=ct, object_id=str(programme.pk)) | Q(public_text__icontains=programme.name))
        .order_by("-at", "-id")[:50])
    ctx = _shell_context(request, "programmes", p=programme, rows=rows, events=events,
                         bodies=programme_info.bodies_map([programme]).get(programme.pk, []),
                         expiry_info=programme_info.expiry(programme),
                         approval_type=programme_info.approval_type_name(programme),
                         body_letters=list(BodyLetter.objects.for_user(request.user).filter(
                             programme=programme, visible_to_institution=True).select_related("body")))
    return render_qa(request, "qa_portal/programmes/detail.html", ctx)
