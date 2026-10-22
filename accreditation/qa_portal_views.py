"""Portal pages for SOP-02 (Phase 3): QA requests, the SSQ workspace and queries.

    QA requests  · list (filters) · new (type → eligible programmes) · detail
                   (add / drop / replace programme, cancel, submit; R-25)
    SSQ workspace · one section at a time (HTMX, no full reloads), autosave,
                   prefill badges, evidence upload or re-use of accepted
                   documents, academic-department return path, submit
    Queries      · list · detail and respond

Institution data is read through ``Model.objects.for_user(user)`` only
(Master Plan §4). Another institution's ids answer 404.
"""
from __future__ import annotations

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

import config
from institutions.models import Institution, InstitutionDocument, Programme
from qa_core.portal_views import _shell_context, institution_required
from qa_core.ui import is_htmx, paginate, toast

from . import qa_services as svc
from . import ssq as engine
from .models import Evidence, QARequest, QARequestProgramme, QAType, Query, Response, SSQ, Submission


def _msg(exc):
    return " ".join(getattr(exc, "messages", None) or [str(exc)])


def _inst(request):
    return Institution.objects.for_user(request.user).select_related("handling_department").first()


def _req_or_404(request, pk):
    req = (QARequest.objects.for_user(request.user).select_related("qa_type", "institution").filter(pk=pk).first())
    if req is None:
        raise Http404
    return req


def _ssq_or_404(request, req, ssq_pk):
    ssq = (SSQ.objects.for_user(request.user)
           .select_related("template", "qa_request_programme__programme__level",
                           "qa_request_programme__programme__category", "assigned_department_user")
           .filter(pk=ssq_pk, qa_request_programme__qa_request=req).first())
    if ssq is None:
        raise Http404
    return ssq


# ===========================================================================
# QA requests
# ===========================================================================
@institution_required
@require_GET
def requests_list(request):
    user = request.user
    inst = _inst(request)
    qs = QARequest.objects.for_user(user).select_related("qa_type").order_by("-created_at")
    show = request.GET.get("show", "open")
    if show == "open":
        qs = qs.exclude(status__in=("cancelled", "decided"))
    elif show == "closed":
        qs = qs.filter(status__in=("cancelled", "decided"))
    if request.GET.get("type"):
        qs = qs.filter(qa_type__code=request.GET["type"])
    # Phase 6 (§20): search, a third facet and 25 rows with "Show all".
    q = (request.GET.get("q") or "").strip()
    if q:
        from django.db.models import Q

        qs = qs.filter(Q(reference__icontains=q) | Q(request_programmes__programme__name__icontains=q)).distinct()
    if request.GET.get("level"):
        qs = qs.filter(request_programmes__programme__level__code=request.GET["level"],
                       request_programmes__status="active").distinct()
    page = paginate(request, qs)
    rows = page["rows"]
    for r in rows:
        r.public = svc.public_status(r)
        r.programmes = list(QARequestProgramme.objects.for_user(user).filter(qa_request=r, status="active")
                            .select_related("programme__level"))
    from institutions.models import ProgrammeLevel

    ctx = _shell_context(request, "qa_requests", inst=inst, rows=rows, page=page, show=show, filters=request.GET,
                         types=QAType.objects.filter(is_active=True).order_by("sort_order"),
                         levels=ProgrammeLevel.objects.filter(is_active=True).exclude(code="admin"),
                         can_create=svc.can_manage_requests(user, inst) and inst.status == Institution.APPROVED,
                         is_department=svc.is_department_user(user))
    if is_htmx(request) and request.headers.get("HX-Target") == "qa-req-rows":
        return render(request, "qa_portal/qa_requests/_rows.html", ctx)
    return render(request, "qa_portal/qa_requests/list.html", ctx)


def _new_context(request, inst, qa_type=None, **extra):
    types = []
    for t in QAType.objects.filter(is_active=True).order_by("sort_order"):
        ok, why = svc.type_availability(inst, t)
        types.append({"type": t, "available": ok, "reason": why})
    programmes = svc.eligible_programmes(inst, qa_type) if qa_type is not None else []
    asked = extra.pop("asked", {})
    for row in programmes:  # Phase 8: the streams box per programme
        row["streams"] = asked.get(row["programme"].pk) or svc.default_streams(row["programme"], qa_type)
    return _shell_context(request, "qa_requests", inst=inst, types=types, qa_type=qa_type, programmes=programmes,
                          window=svc.window_months(), max_streams=svc.max_streams(), **extra)


def _asked_streams(request, ids):
    """{programme pk: raw value} from the streams_<pk> inputs."""
    return {pk: request.POST.get(f"streams_{pk}", request.GET.get(f"streams_{pk}")) for pk in ids
            if (request.POST.get(f"streams_{pk}") or request.GET.get(f"streams_{pk}")) not in (None, "")}


@institution_required
@require_GET
def request_estimate(request):
    """Phase 8: the live fee estimate on the new-request page (HTMX). It
    recalculates as the institution ticks programmes and types streams."""
    user = request.user
    inst = _inst(request)
    if not svc.can_manage_requests(user, inst):
        raise Http404
    qa_type = QAType.objects.filter(code=request.GET.get("type") or "", is_active=True).first()
    ids = [int(i) for i in request.GET.getlist("programmes") if str(i).isdigit()]
    programmes = list(Programme.objects.for_user(user).filter(institution=inst, pk__in=ids))
    asked = _asked_streams(request, ids)
    pairs, problems = [], []
    for p in programmes:
        try:
            n = svc._streams_for(p, qa_type, asked.get(p.pk)) if qa_type is not None else 1
        except ValidationError as exc:
            problems.append(_msg(exc))
            n = svc.default_streams(p, qa_type)
        pairs.append((p, n))
    estimate = svc.fee_estimate(inst, qa_type, pairs) if (qa_type is not None and pairs) else None
    return render(request, "qa_portal/qa_requests/_fee_estimate.html",
                  {"estimate": estimate, "problems": problems, "qa_type": qa_type, "count": len(pairs)})


@institution_required
@require_http_methods(["GET", "POST"])
def request_new(request):
    user = request.user
    inst = _inst(request)
    if not svc.can_manage_requests(user, inst):
        raise Http404
    code = request.POST.get("type") or request.GET.get("type") or ""
    qa_type = QAType.objects.filter(code=code, is_active=True).first() if code else None
    if request.method == "POST":
        ids = [int(i) for i in request.POST.getlist("programmes") if str(i).isdigit()]
        programmes = list(Programme.objects.for_user(user).filter(institution=inst, pk__in=ids))
        asked = _asked_streams(request, [p.pk for p in programmes])
        try:
            if qa_type is None:
                raise ValidationError("Choose a QA type.")
            req = svc.create_request(inst, qa_type, programmes, user=user, streams=asked)
        except (ValidationError, PermissionDenied) as exc:
            response = render(request, "qa_portal/qa_requests/new.html",
                              _new_context(request, inst, qa_type, error=_msg(exc), chosen=ids,
                                           asked={k: v for k, v in asked.items()}), status=422)
            return toast(response, "error", "Not created", _msg(exc))
        url = reverse("qa_portal:qa_request", args=[req.pk])
        if is_htmx(request):
            response = HttpResponse(status=204)
            response["HX-Location"] = '{"path": "%s", "target": "#qa-main"}' % url
            return toast(response, "success", f"Request {req.reference} created",
                         "Check the programmes, then submit it.")
        return redirect(url)
    ctx = _new_context(request, inst, qa_type)
    if is_htmx(request) and request.headers.get("HX-Target") == "qa-new-programmes":
        return render(request, "qa_portal/qa_requests/_new_programmes.html", ctx)
    return render(request, "qa_portal/qa_requests/new.html", ctx)


def _detail_context(request, req, **extra):
    user = request.user
    inst = req.institution
    rows = list(QARequestProgramme.objects.for_user(user).filter(qa_request=req)
                .select_related("programme__level", "replaced_by__programme").order_by("status", "programme__name"))
    active_ids = {r.programme_id for r in rows if r.status == "active"}
    candidates = []
    if svc.can_change_programmes(req, "add_programme") or svc.can_change_programmes(req, "replace_programme"):
        for item in svc.eligible_programmes(inst, req.qa_type):
            if item["programme"].pk not in active_ids and item["eligible"]:
                candidates.append(item["programme"])
    ssqs = []
    for row in rows:
        if row.status != "active":
            continue
        current = SSQ.objects.for_user(user).filter(qa_request_programme=row, is_current=True).first()
        if current is not None:
            progress = engine.section_progress(current.template, current.data or {}, svc.evidence_counts(current))
            current.done = sum(p["filled"] for p in progress)
            current.total = sum(p["total"] for p in progress)
            current.public = svc.ssq_public_status(current)
            current.row = row
            ssqs.append(current)
    from django.contrib.contenttypes.models import ContentType

    from payments.models import PaymentObligation

    obligations = list(PaymentObligation.objects.for_user(user).filter(
        source_type=ContentType.objects.get_for_model(QARequest), source_id=str(req.pk)).order_by("-created_at"))
    manage = svc.can_manage_requests(user, inst)
    ctx = {
        "req": req, "inst": inst, "rows": rows, "ssqs": ssqs, "candidates": candidates, "obligations": obligations,
        "public_status": svc.public_status(req), "manage": manage,
        "can_add": manage and svc.can_change_programmes(req, "add_programme"),
        "can_drop": manage and svc.can_change_programmes(req, "drop_programme"),
        "can_replace": manage and svc.can_change_programmes(req, "replace_programme"),
        "can_cancel": manage and svc.is_cancellable(req), "r25": svc.cancellable_stage_labels(req),
        "queries": list(Query.objects.for_user(user).filter(qa_request=req).order_by("-created_at")),
        "board": _board(req),
        "max_streams": svc.max_streams(),
    }
    if req.status == "draft":  # Phase 8: what the fee will be, from the streams asked for
        ctx["estimate"] = svc.fee_estimate(inst, req.qa_type, [(r.programme, r.requested_streams) for r in rows
                                                              if r.status == "active"])
    ctx.update(extra)
    return _shell_context(request, "qa_requests", **ctx)


STEPS = [("draft", "Draft"), ("submitted", "Submitted"), ("fee", "QA fee"), ("ssq", "Self-study (SSQ)"),
         ("review", "NBTE review")]
STEP_OF = {"draft": "draft", "submitted": "submitted", "programme_clearance": "submitted", "fee_due": "fee",
           "payment_pending_confirmation": "fee", "fee_confirmed": "fee", "ssq_in_progress": "ssq",
           "ssq_submitted": "review", "in_workflow": "review", "decided": "review"}


def _board(req):
    current = STEP_OF.get(req.status)
    if current is None:
        return []
    out, seen = [], False
    for code, label in STEPS:
        if code == current:
            out.append({"label": label, "state": "current"})
            seen = True
        else:
            out.append({"label": label, "state": "todo" if seen else "done"})
    return out


def _detail_response(request, req, *, error="", ok=None, status=None):
    req.refresh_from_db()
    response = render(request, "qa_portal/qa_requests/detail.html", _detail_context(request, req, error=error),
                      status=status or (422 if error else 200))
    if error:
        return toast(response, "error", "Not done", error)
    if ok:
        return toast(response, "success", *ok)
    return response


@institution_required
@require_GET
def request_detail(request, pk):
    req = _req_or_404(request, pk)
    return render(request, "qa_portal/qa_requests/detail.html", _detail_context(request, req))


def _manage_or_404(request, req):
    if not svc.can_manage_requests(request.user, req.institution):
        raise Http404


@institution_required
@require_POST
def request_submit(request, pk):
    req = _req_or_404(request, pk)
    _manage_or_404(request, req)
    try:
        svc.submit(req, user=request.user, declaration=request.POST.get("declaration") == "on")
    except (ValidationError, PermissionDenied) as exc:
        return _detail_response(request, req, error=_msg(exc))
    req.refresh_from_db()
    if req.status == "programme_clearance":
        ok = ("Request submitted", "NBTE first clears the new programme(s); the fee follows.")
    elif req.fee_note:
        ok = ("Request submitted", "NBTE will issue the QA fee shortly.")
    else:
        ok = ("Request submitted", "Your QA fee is on the Payments page.")
    return _detail_response(request, req, ok=ok)


@institution_required
@require_POST
def request_cancel(request, pk):
    req = _req_or_404(request, pk)
    _manage_or_404(request, req)
    try:
        svc.cancel(req, user=request.user, reason=request.POST.get("reason", ""))
    except (ValidationError, PermissionDenied) as exc:
        return _detail_response(request, req, error=_msg(exc))
    return _detail_response(request, req, ok=("Request cancelled", "Any unpaid bill was cancelled too."))


@institution_required
@require_POST
def request_add(request, pk):
    req = _req_or_404(request, pk)
    _manage_or_404(request, req)
    programme = Programme.objects.for_user(request.user).filter(pk=request.POST.get("programme") or 0,
                                                                 institution=req.institution).first()
    try:
        if programme is None:
            raise ValidationError("Choose a programme.")
        svc.add_programme(req, programme, user=request.user, streams=request.POST.get("streams") or None)
    except (ValidationError, PermissionDenied) as exc:
        return _detail_response(request, req, error=_msg(exc))
    return _detail_response(request, req, ok=(f"{programme} added", ""))


@institution_required
@require_POST
def request_streams(request, pk, row):
    """Phase 8: change the streams asked for on a draft request."""
    req = _req_or_404(request, pk)
    _manage_or_404(request, req)
    row = _row_or_404(request, req, row)
    try:
        svc.set_streams(req, row, request.POST.get("streams"), user=request.user)
    except (ValidationError, PermissionDenied) as exc:
        return _detail_response(request, req, error=_msg(exc))
    return _detail_response(request, req, ok=(f"{row.programme}: {row.requested_streams} stream(s)", ""))


def _row_or_404(request, req, row_pk):
    row = (QARequestProgramme.objects.for_user(request.user).select_related("programme")
           .filter(pk=row_pk, qa_request=req).first())
    if row is None:
        raise Http404
    return row


@institution_required
@require_POST
def request_drop(request, pk, row):
    req = _req_or_404(request, pk)
    _manage_or_404(request, req)
    row = _row_or_404(request, req, row)
    try:
        svc.drop_programme(req, row, user=request.user, reason=request.POST.get("reason", ""))
    except (ValidationError, PermissionDenied) as exc:
        return _detail_response(request, req, error=_msg(exc))
    return _detail_response(request, req, ok=(f"{row.programme} dropped from the request", ""))


@institution_required
@require_POST
def request_replace(request, pk, row):
    req = _req_or_404(request, pk)
    _manage_or_404(request, req)
    row = _row_or_404(request, req, row)
    new = Programme.objects.for_user(request.user).filter(pk=request.POST.get("programme") or 0,
                                                           institution=req.institution).first()
    try:
        if new is None:
            raise ValidationError("Choose the replacement programme.")
        svc.replace_programme(req, row, new, user=request.user, reason=request.POST.get("reason", ""))
    except (ValidationError, PermissionDenied) as exc:
        return _detail_response(request, req, error=_msg(exc))
    return _detail_response(request, req, ok=(f"{row.programme} replaced by {new}", ""))


# ===========================================================================
# SSQ workspace
# ===========================================================================
def _ws_context(request, req, ssq, section_key=None, **extra):
    user = request.user
    template = ssq.template
    sections = template.sections()
    keys = [s["key"] for s in sections]
    if section_key not in keys:
        section_key = keys[0] if keys else None
    section = next((s for s in sections if s["key"] == section_key), None)
    programme = ssq.qa_request_programme.programme
    counts = svc.evidence_counts(ssq)
    progress = engine.section_progress(template, ssq.data or {}, counts)
    editable = svc.editable_sections(ssq, user)
    meta = ssq.meta or {}
    prefilled = set(meta.get("prefilled", [])) - set(meta.get("edited", []))
    evidence = list(Evidence.objects.for_user(user).filter(ssq=ssq).exclude(status="removed")
                    .select_related("reused_from__kind"))
    by_field = {}
    for ev in evidence:
        by_field.setdefault(ev.field_key, []).append(ev)
    fields = []
    if section is not None:
        for field in section.get("fields", []):
            f = dict(field)
            value = (ssq.data or {}).get(field["key"])
            f["value"] = value
            f["choices"] = engine.option_pairs(field)
            if field["type"] == "catalogue":
                qty = value or {}
                f["items"] = [{"item": item, "quantity": qty.get(str(item.pk), "")}
                              for item in engine.catalogue_items(field, programme)]
            if field["type"] == "table":
                f["rows"] = list(value or [])
            if field["type"] == "file":
                f["evidence"] = by_field.get(field["key"], [])
                f["reusable"] = svc.reusable_documents(ssq, field)
            f["prefilled"] = field["key"] in prefilled
            f["edited"] = field["key"] in set(meta.get("edited", []))
            fields.append(f)
    problems = svc.submission_problems(ssq) if ssq.is_current else {}
    role = getattr(getattr(user, "role", None), "code", "")
    submission = Submission.objects.for_user(user).filter(ssq=ssq).order_by("-version").first()
    ctx = {
        "req": req, "ssq": ssq, "template": template, "programme": programme, "sections": sections,
        "section": section, "section_key": section_key, "fields": fields, "progress": progress,
        "editable": section_key in editable, "editable_sections": editable,
        "problems": problems, "problem_count": len(problems), "public": svc.ssq_public_status(ssq),
        "problem_list": _problem_list(sections, problems)[:6],
        "is_admin": svc.can_manage_requests(user, req.institution),
        "is_department": svc.is_department_user(user),
        "can_submit": role in svc.submit_roles() and ssq.status in ("draft", "submitted_by_department")
        and ssq.is_current and not svc.is_department_user(user),
        "department_users": svc.department_users(req.institution) if svc.can_manage_requests(user, req.institution)
        else [],
        "department_sections": [s for s in sections if s.get("department", True)],
        "autosave_ms": config.get("qa.ssq.autosave_delay_ms", 900), "submission": submission,
        "versions": list(SSQ.objects.for_user(user).filter(qa_request_programme=ssq.qa_request_programme)
                         .order_by("-version")),
        "max_mb": config.get("qa.documents.max_upload_mb", 10),
        "queries": list(Query.objects.for_user(user).filter(qa_request=req).exclude(status="closed")),
        "can_send_department": svc.can_manage_requests(user, req.institution) and ssq.is_current
        and ssq.status in ("draft", "submitted_by_department"),
    }
    index = keys.index(section_key) if section_key in keys else 0
    ctx["prev_section"] = sections[index - 1] if section is not None and index > 0 else None
    ctx["next_section"] = sections[index + 1] if section is not None and index + 1 < len(sections) else None
    if not ssq.is_current or ssq.status in ("submitted", "accepted", "returned", "analysed"):
        ctx["snapshot_sections"] = (submission.rendered_snapshot if submission is not None
                                    else engine.render_snapshot(template, ssq.data or {}, programme=programme))
        ctx["evidence_by_field"] = by_field
    ctx.update(extra)
    current = ctx.pop("section")
    out = _shell_context(request, "qa_requests", **ctx)
    out["section"] = current  # the SSQ section (the shell's own `section` only marks dashboard/account)
    return out


def _problem_list(sections, problems):
    """[(section, field)] for the first missing answers, in questionnaire order."""
    out = []
    for section in sections:
        for field in section.get("fields", []):
            if field["key"] in problems:
                out.append({"section": section, "field": field})
    return out


def _ws_render(request, req, ssq, section_key=None, *, status=200, **extra):
    ctx = _ws_context(request, req, ssq, section_key, **extra)
    target = request.headers.get("HX-Target") if is_htmx(request) else ""
    if target == "ssq-section":
        ctx["section_partial"] = True
        return render(request, "qa_portal/ssq/_section.html", ctx, status=status)
    if target == "ssq-workspace":
        return render(request, "qa_portal/ssq/_workspace.html", ctx, status=status)
    return render(request, "qa_portal/ssq/workspace.html", ctx, status=status)


@institution_required
@require_GET
def ssq_workspace(request, pk, ssq_pk):
    req = _req_or_404(request, pk)
    ssq = _ssq_or_404(request, req, ssq_pk)
    return _ws_render(request, req, ssq, request.GET.get("section"))


def _collect_section(request, ssq, section_key):
    """Raw values for every (non-file) field of one section from the POST."""
    section = next((s for s in ssq.template.sections() if s["key"] == section_key), None)
    if section is None:
        raise ValidationError("Unknown section.")
    post = request.POST
    values = {}
    for field in section.get("fields", []):
        key, ftype = field["key"], field["type"]
        name = f"f__{key}"
        if ftype == "file":
            continue
        prefix = name + "__"
        # A field the form did not send is left alone (a partial post never
        # blanks other answers); multi-choice and table inputs carry a
        # "__present" marker so "nothing ticked" / "no rows" still saves.
        present = name in post or f"{name}__present" in post or any(k.startswith(prefix) for k in post)
        if not present:
            continue
        if ftype == "multi_choice":
            values[key] = post.getlist(name)
        elif ftype == "catalogue":
            values[key] = {k[len(prefix):]: v for k, v in post.items() if k.startswith(prefix) and k[len(prefix):]
                           .isdigit()}
        elif ftype == "table":
            rows = {}
            for k, v in post.items():
                if not k.startswith(prefix):
                    continue
                index, _, col = k[len(prefix):].partition("__")
                if index.isdigit() and col:
                    rows.setdefault(int(index), {})[col] = v
            values[key] = [rows[i] for i in sorted(rows)]
        else:
            values[key] = post.get(name, "")
    return values


@institution_required
@require_POST
def ssq_save(request, pk, ssq_pk):
    """Autosave of one section (the whole section's inputs, posted by the
    workspace script after a pause in typing). Returns the save status and
    out-of-band updates for the field errors, the section progress and the
    submit panel."""
    req = _req_or_404(request, pk)
    ssq = _ssq_or_404(request, req, ssq_pk)
    section_key = request.POST.get("section", "")
    try:
        values = _collect_section(request, ssq, section_key)
        errors = svc.save_fields(ssq, values, user=request.user)
    except PermissionDenied as exc:
        response = render(request, "qa_portal/ssq/_save_status.html",
                          {"state": "error", "message": _msg(exc) or "You cannot edit this section now."}, status=403)
        return response
    except ValidationError as exc:
        return render(request, "qa_portal/ssq/_save_status.html", {"state": "error", "message": _msg(exc)},
                      status=422)
    ssq.refresh_from_db()
    ctx = _ws_context(request, req, ssq, section_key, field_errors=errors, oob=True,
                      state="partial" if errors else "saved", saved_at=timezone.localtime(ssq.last_saved_at))
    return render(request, "qa_portal/ssq/_save_result.html", ctx, status=200)


def _field_response(request, req, ssq, field_key, *, error="", ok=None):
    section_key = next((s["key"] for s in ssq.template.sections()
                        if any(f["key"] == field_key for f in s.get("fields", []))), None)
    ssq.refresh_from_db()
    ctx = _ws_context(request, req, ssq, section_key, upload_error={field_key: error} if error else {},
                      section_partial=True)
    response = render(request, "qa_portal/ssq/_section.html", ctx, status=422 if error else 200)
    response["HX-Retarget"] = "#ssq-section"
    response["HX-Reswap"] = "innerHTML"
    if error:
        return toast(response, "error", "File not attached", error)
    return toast(response, "success", *ok) if ok else response


@institution_required
@require_POST
def ssq_upload(request, pk, ssq_pk, field):
    req = _req_or_404(request, pk)
    ssq = _ssq_or_404(request, req, ssq_pk)
    upload = request.FILES.get("file")
    try:
        if upload is None:
            raise ValidationError("Choose a file to upload.")
        ev = svc.attach_upload(ssq, field, upload, user=request.user)
    except (ValidationError, PermissionDenied) as exc:
        return _field_response(request, req, ssq, field, error=_msg(exc))
    return _field_response(request, req, ssq, field, ok=("File attached", ev.display_name))


@institution_required
@require_POST
def ssq_reuse(request, pk, ssq_pk, field):
    req = _req_or_404(request, pk)
    ssq = _ssq_or_404(request, req, ssq_pk)
    doc = InstitutionDocument.objects.for_user(request.user).filter(pk=request.POST.get("document") or 0).first()
    try:
        if doc is None:
            raise ValidationError("Choose one of your accepted documents.")
        svc.reuse_document(ssq, field, doc, user=request.user)
    except (ValidationError, PermissionDenied) as exc:
        return _field_response(request, req, ssq, field, error=_msg(exc))
    return _field_response(request, req, ssq, field, ok=("Document re-used", f"{doc.kind.name} v{doc.version}"))


@institution_required
@require_POST
def ssq_evidence_remove(request, pk, ssq_pk, ev):
    req = _req_or_404(request, pk)
    ssq = _ssq_or_404(request, req, ssq_pk)
    evidence = Evidence.objects.for_user(request.user).filter(pk=ev, ssq=ssq).exclude(status="removed").first()
    if evidence is None:
        raise Http404
    try:
        svc.remove_evidence(evidence, user=request.user)
    except (ValidationError, PermissionDenied) as exc:
        return _field_response(request, req, ssq, evidence.field_key, error=_msg(exc))
    return _field_response(request, req, ssq, evidence.field_key, ok=("File removed", ""))


def _ws_action(request, pk, ssq_pk, action, ok):
    req = _req_or_404(request, pk)
    ssq = _ssq_or_404(request, req, ssq_pk)
    try:
        result = action(ssq)
    except (ValidationError, PermissionDenied) as exc:
        response = _ws_render(request, req, ssq, request.POST.get("section"), status=422, error=_msg(exc))
        return toast(response, "error", "Not done", _msg(exc))
    target = result if isinstance(result, SSQ) else ssq
    target.refresh_from_db()
    if target.pk != ssq.pk:
        url = reverse("qa_portal:ssq", args=[req.pk, target.pk])
        if not is_htmx(request):
            return redirect(url)
        response = HttpResponse(status=204)
        response["HX-Location"] = '{"path": "%s", "target": "#qa-main"}' % url
        return toast(response, "success", *ok)
    response = _ws_render(request, req, target, request.POST.get("section"))
    return toast(response, "success", *ok)


@institution_required
@require_POST
def ssq_department(request, pk, ssq_pk):
    from accounts.models import User

    req = _req_or_404(request, pk)
    dept_user = User.objects.filter(pk=request.POST.get("department_user") or 0,
                                    institution=req.institution).first()
    return _ws_action(request, pk, ssq_pk, lambda s: svc.return_to_department(
        s, user=request.user, department_user=dept_user, sections=request.POST.getlist("sections"),
        remark=request.POST.get("remark", "")), ("Sent to the academic department",
                                                 "They can edit only the sections you chose."))


@institution_required
@require_POST
def ssq_recall(request, pk, ssq_pk):
    return _ws_action(request, pk, ssq_pk, lambda s: svc.recall_from_department(s, user=request.user),
                      ("Taken back from the department", ""))


@institution_required
@require_POST
def ssq_department_submit(request, pk, ssq_pk):
    return _ws_action(request, pk, ssq_pk, lambda s: svc.department_submit(s, user=request.user),
                      ("Sent back to your institution administrator", "They review it and submit it to NBTE."))


@institution_required
@require_POST
def ssq_submit(request, pk, ssq_pk):
    return _ws_action(request, pk, ssq_pk, lambda s: svc.submit_ssq(
        s, user=request.user, declaration=request.POST.get("declaration") == "on"),
        ("SSQ submitted to NBTE", "A copy was frozen as your submission; NBTE reviews it now."))


@institution_required
@require_POST
def ssq_revise(request, pk, ssq_pk):
    return _ws_action(request, pk, ssq_pk, lambda s: svc.revise_ssq(s, user=request.user),
                      ("New version started", "Your answers and files were carried over. Fix what NBTE asked."))


# ===========================================================================
# Queries
# ===========================================================================
@institution_required
@require_GET
def queries(request):
    qs = Query.objects.for_user(request.user).select_related("qa_request").order_by("-created_at")
    status = request.GET.get("status", "active")
    if status == "active":
        qs = qs.filter(status__in=("open", "answered"))
    elif status in ("open", "answered", "closed"):
        qs = qs.filter(status=status)
    # Phase 6 (§20): search and two more facets, 25 rows with "Show all".
    q = (request.GET.get("q") or "").strip()
    if q:
        from django.db.models import Q

        qs = qs.filter(Q(reference__icontains=q) | Q(title__icontains=q) | Q(subject_label__icontains=q)
                       | Q(qa_request__reference__icontains=q))
    about = request.GET.get("about", "")
    if about == "request":
        qs = qs.filter(qa_request__isnull=False)
    elif about == "other":
        qs = qs.filter(qa_request__isnull=True)
    due = request.GET.get("due", "")
    now = timezone.now()
    if due == "overdue":
        qs = qs.filter(status="open", due_at__lt=now)
    elif due == "week":
        qs = qs.filter(status="open", due_at__gte=now, due_at__lt=now + datetime.timedelta(days=7))
    page = paginate(request, qs)
    ctx = _shell_context(request, "queries", rows=page["rows"], page=page, status=status, filters=request.GET,
                         open_count=Query.objects.for_user(request.user).filter(status="open").count())
    if is_htmx(request) and request.headers.get("HX-Target") == "qa-query-rows":
        return render(request, "qa_portal/queries/_rows.html", ctx)
    return render(request, "qa_portal/queries/list.html", ctx)


def _query_context(request, query, **extra):
    ctx = {"query": query, "responses": list(Response.objects.for_user(request.user).filter(query=query)
                                             .select_related("responded_by")),
           "can_respond": query.status != Query.CLOSED,
           "max_mb": config.get("qa.documents.max_upload_mb", 10)}
    ctx.update(extra)
    return _shell_context(request, "queries", **ctx)


@institution_required
@require_http_methods(["GET", "POST"])
def query_detail(request, pk):
    query = Query.objects.for_user(request.user).select_related("qa_request", "raised_by").filter(pk=pk).first()
    if query is None:
        raise Http404
    if request.method == "POST":
        try:
            svc.respond(query, user=request.user, body=request.POST.get("body", ""),
                        upload=request.FILES.get("file"))
        except (ValidationError, PermissionDenied) as exc:
            response = render(request, "qa_portal/queries/detail.html",
                              _query_context(request, query, error=_msg(exc), body=request.POST.get("body", "")),
                              status=422)
            return toast(response, "error", "Response not sent", _msg(exc))
        query.refresh_from_db()
        response = render(request, "qa_portal/queries/detail.html", _query_context(request, query))
        return toast(response, "success", "Response sent to NBTE", "The officer who asked is notified.")
    return render(request, "qa_portal/queries/detail.html", _query_context(request, query))


# ===========================================================================
# Dashboards
# ===========================================================================
def qa_action_items(request, inst):
    """Institution dashboard: what SOP-02 needs from them now."""
    user = request.user
    items = []
    if inst is None or inst.status != Institution.APPROVED:
        return items
    for q in Query.objects.for_user(user).filter(status="open").order_by("due_at")[:5]:
        items.append({"tone": "danger" if q.is_overdue else "warn", "title": f"NBTE query: {q.title}",
                      "body": f"{q.reference} · respond by {timezone.localtime(q.due_at):%d %b %Y}",
                      "url": reverse("qa_portal:query", args=[q.pk]), "cta": "Respond"})
    department = svc.is_department_user(user)
    for ssq in (SSQ.objects.for_user(user).filter(is_current=True, qa_request_programme__status="active")
                .select_related("qa_request_programme__programme", "qa_request_programme__qa_request")[:20]):
        req = ssq.qa_request_programme.qa_request
        url = reverse("qa_portal:ssq", args=[req.pk, ssq.pk])
        if ssq.status == "returned" and not department:
            items.append({"tone": "danger", "title": f"SSQ returned: {ssq.qa_request_programme.programme}",
                          "body": ssq.review_remark, "url": url, "cta": "Revise"})
        elif ssq.status == "returned_to_department" and department and ssq.assigned_department_user_id == user.pk:
            items.append({"tone": "info", "title": f"Complete SSQ sections: {ssq.qa_request_programme.programme}",
                          "body": ssq.department_remark, "url": url, "cta": "Open"})
        elif ssq.status in ("draft", "submitted_by_department") and not department:
            items.append({"tone": "info", "title": f"Complete the SSQ: {ssq.qa_request_programme.programme}",
                          "body": f"{req.reference} · {req.qa_type.name}", "url": url, "cta": "Continue"})
    if not department:
        for req in QARequest.objects.for_user(user).filter(status="draft").select_related("qa_type")[:3]:
            items.append({"tone": "info", "title": f"Submit your {req.qa_type.name} request",
                          "body": f"{req.reference} is still a draft.",
                          "url": reverse("qa_portal:qa_request", args=[req.pk]), "cta": "Open"})
    return items


def staff_dashboard_context(request):
    """The Staff Portal Dashboard: what this officer can act on, by reference
    and action only (no institution names or records — those stay on the
    internal system, where every link goes)."""
    from qa_core.hosts import internal_url

    work = svc.staff_work(request.user, limit=8)
    rows = []
    for item in work["requests"]:
        req = item["request"]
        rows.append({"title": f"{req.qa_type.name} request", "meta": req.reference,
                     "action": item["action"], "url": internal_url(request, f"/qa/requests/{req.pk}/")})
    for ssq in work["ssqs"]:
        req = ssq.qa_request_programme.qa_request
        rows.append({"title": f"Self-study questionnaire (version {ssq.version})",
                     "meta": req.reference, "action": "Review SSQ",
                     "url": internal_url(request, f"/qa/requests/ssq/{ssq.pk}/")})
    for q in work["queries"]:
        rows.append({"title": f"Query {q.reference} was answered", "meta": q.reference,
                     "action": "Read the answer", "url": internal_url(request, f"/qa/requests/queries/{q.pk}/")})
    return {"staff_rows": rows, "internal_home": internal_url(request, "/"),
            "internal_requests": internal_url(request, "/qa/requests/"),
            "internal_queue": internal_url(request, "/qa/requests/ssq/"),
            "staff_counts": {"requests": len(work["requests"]), "ssqs": len(work["ssqs"]),
                             "queries": len(work["queries"])}}
