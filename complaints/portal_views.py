"""Complaints — the portal pages (Master Plan §11, §16; PHASES.md Phase 7).

    Complaints · Raise a complaint (knowledge-base suggestions while typing,
    "about this record" prefill) · Complaint (public messages only, reply,
    confirm / reopen / withdraw) · Knowledge base

Complaints are read through ``services.visible_to`` (an institution sees its
institution's complaints, a resource person their own); anything else is
404. Internal notes are filtered out here and never reach a template.
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import F
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from qa_core.portal_views import _shell_context, portal_login_required
from qa_core.ui import htmx_fragment, is_htmx, paginate, toast

from . import services
from .models import ComplaintAttachment, ComplaintLayer, ComplaintMessage, KnowledgeArticle

EXTERNAL = ("institution", "resource_person")


def _external(request):
    if getattr(request.user, "user_type", "staff") not in EXTERNAL:
        raise Http404("Complaints are raised by institutions and resource persons.")


def _msg(exc):
    if isinstance(exc, ValidationError):
        if hasattr(exc, "message_dict"):
            return " ".join(m for msgs in exc.message_dict.values() for m in msgs)
        return " ".join(exc.messages)
    return str(exc) or "Not allowed."


def _audience(user):
    return getattr(user, "user_type", "institution")


def _read_only(request):
    return bool(getattr(request, "is_impersonating", False))


# ===========================================================================
# List
# ===========================================================================
@portal_login_required
@require_GET
def complaints(request):
    _external(request)
    qs = services.visible_to(request.user).select_related("layer", "current_department")
    status = request.GET.get("status", "open")
    if status == "open":
        qs = qs.filter(closed_at__isnull=True)
    elif status == "closed":
        qs = qs.filter(closed_at__isnull=False)
    q = (request.GET.get("q") or "").strip()
    if q:
        from django.db.models import Q

        qs = qs.filter(Q(reference__icontains=q) | Q(subject__icontains=q))
    page = paginate(request, qs.order_by("-created_at", "-id"))
    rows = page["rows"]
    stages = {s.complaint_id: s for c in rows for s in [c.open_stage()] if s is not None}
    for c in rows:
        c.public_stage = _public_stage(c)
        c.stage_row = stages.get(c.pk)
    ctx = _shell_context(request, "complaints", page=page, rows=rows, status=status, filters=request.GET,
                         open_count=services.visible_to(request.user).filter(closed_at__isnull=True).count(),
                         read_only=_read_only(request))
    if is_htmx(request) and request.headers.get("HX-Target") == "cx-rows":
        return render(request, "complaints/portal/_rows.html", ctx)
    return render(request, "complaints/portal/list.html", ctx)


def _public_stage(complaint):
    from qa_core import flow

    inst = flow.instance_for(complaint)
    return flow.public_label(inst) or complaint.status.replace("_", " ").capitalize()


# ===========================================================================
# Raise
# ===========================================================================
def _about(request):
    raw = request.GET.get("about") or request.POST.get("about") or ""
    kind, _sep, pk = raw.partition(":")
    related = services.resolve_related(request.user, kind, pk)
    return (raw if related is not None else ""), kind if related is not None else "", related


@portal_login_required
@require_http_methods(["GET", "POST"])
def raise_complaint(request):
    _external(request)
    layers = services.layers_for(request.user)
    about, kind, related = _about(request)
    suggested = services.SUGGESTED_LAYERS.get(kind, "")
    values = request.POST if request.method == "POST" else {}
    error = ""
    if request.method == "POST":
        if _read_only(request):
            raise PermissionDenied("Read-only impersonation: nothing can be raised.")
        layer = next((lay for lay in layers if str(lay.pk) == request.POST.get("layer")), None)
        try:
            if layer is None:
                raise ValidationError({"layer": "Choose what the complaint is about."})
            answers = {f.get("key"): request.POST.get(f"q_{layer.pk}_{f.get('key')}", "")
                       for f in (layer.custom_fields or [])}
            complaint = services.raise_complaint(
                request.user, layer=layer, subject=request.POST.get("subject", ""),
                body=request.POST.get("body", ""), priority="", related=related, answers=answers,
                files=request.FILES.getlist("files"))
        except (ValidationError, PermissionDenied) as exc:
            error = _msg(exc)
        else:
            url = reverse("qa_portal:complaint", args=[complaint.pk])
            if is_htmx(request):
                from django.http import HttpResponse

                response = HttpResponse(status=204)
                response["HX-Location"] = '{"path": "%s", "target": "#qa-main"}' % url
                return toast(response, "success", f"Complaint {complaint.reference} received",
                             "We'll keep you posted here and by e-mail.")
            return redirect(url)
    chosen = (request.POST.get("layer") or request.GET.get("layer")
              or next((str(lay.pk) for lay in layers if lay.code == suggested), ""))
    ctx = _shell_context(request, "complaints", layers=layers, about=about, related=related,
                         related_label=services.related_label(related), chosen=chosen, values=values,
                         error=error, read_only=_read_only(request),
                         popular=services.kb_search("payment login password ssq visit", audience=_audience(
                             request.user), limit=3))
    return render(request, "complaints/portal/raise.html", ctx, status=422 if error else 200)


@portal_login_required
@require_GET
@htmx_fragment("qa_portal:complaint_new")
def suggest(request):
    """Knowledge-base suggestions while the subject is typed (TR-17)."""
    _external(request)
    text = f"{request.GET.get('subject', '')} {request.GET.get('body', '')[:300]}"
    layer = ComplaintLayer.objects.filter(pk=request.GET.get("layer") or 0).first()
    articles = services.kb_search(text, audience=_audience(request.user), layer=layer)
    return render(request, "complaints/portal/_suggestions.html", {"articles": articles})


# ===========================================================================
# One complaint
# ===========================================================================
def _complaint_or_404(request, pk):
    _external(request)
    complaint = services.get_visible(request.user, pk)
    if complaint is None:
        raise Http404
    return complaint


def _detail_ctx(request, complaint, **extra):
    messages_qs = (complaint.messages.filter(visibility=ComplaintMessage.PUBLIC)  # TR-12: never internal notes
                   .select_related("author").prefetch_related("attachments"))
    current = complaint.open_stage()
    show_name = services.show_officer_name()
    ctx = _shell_context(
        request, "complaints", c=complaint, messages_list=list(messages_qs), stage_label=_public_stage(complaint),
        department=getattr(current, "assigned_department", None) or complaint.current_department,
        # v2.1 X10: the officer working it (never the director's queue step), only when the setting allows.
        officer=((complaint.assigned_officer if services.is_directed(complaint) else
                  getattr(current, "assigned_to", None)) if show_name else None),
        actions=services.raiser_actions(request.user, complaint) if not _read_only(request) else [],
        can_reply=complaint.is_open and services.is_raiser(request.user, complaint) and not _read_only(request),
        waiting_for_me=complaint.status == "awaiting_raiser",
        reopen_days=services.reopen_window_days(), read_only=_read_only(request),
        steps=_steps(complaint))
    ctx.update(extra)
    return ctx


def _steps(complaint):
    """A short public progress line: Received → Being worked on → Resolved → Closed."""
    order = [("received", "Received"), ("in_progress", "Being worked on"), ("resolved", "Resolved"),
             ("closed", "Closed")]
    codes = [code for code, _l in order]
    now = complaint.status if complaint.status in codes else (
        "in_progress" if complaint.is_open and complaint.status not in ("resolved",) else complaint.status)
    if complaint.status == "awaiting_raiser" or complaint.status == "referred":
        now = "in_progress"
    idx = codes.index(now) if now in codes else (3 if not complaint.is_open else 1)
    return [{"label": label, "state": "done" if i < idx else ("now" if i == idx else "todo")}
            for i, (_c, label) in enumerate(order)]


@portal_login_required
@require_GET
def complaint_detail(request, pk):
    complaint = _complaint_or_404(request, pk)
    return render(request, "complaints/portal/detail.html", _detail_ctx(request, complaint))


@portal_login_required
@require_POST
def complaint_reply(request, pk):
    complaint = _complaint_or_404(request, pk)
    try:
        if _read_only(request):
            raise PermissionDenied("Read-only impersonation.")
        services.reply(complaint, request.user, request.POST.get("body", ""), files=request.FILES.getlist("files"))
    except (ValidationError, PermissionDenied) as exc:
        response = render(request, "complaints/portal/detail.html",
                          _detail_ctx(request, complaint, error=_msg(exc), body=request.POST.get("body", "")),
                          status=422)
        return toast(response, "error", "Not sent", _msg(exc))
    complaint.refresh_from_db()
    response = render(request, "complaints/portal/detail.html", _detail_ctx(request, complaint))
    return toast(response, "success", "Sent to NBTE", "The officer handling it is notified.")


ACTION_TOASTS = {"confirm": "Thank you — the complaint is closed", "reopen": "Complaint reopened",
                 "withdraw": "Complaint withdrawn"}


@portal_login_required
@require_POST
def complaint_action(request, pk, action):
    complaint = _complaint_or_404(request, pk)
    try:
        if _read_only(request):
            raise PermissionDenied("Read-only impersonation.")
        if action not in ("confirm", "reopen", "withdraw"):
            raise PermissionDenied("Not available.")
        services.act(complaint, action, request.user, remark=request.POST.get("remark", ""))
        rating = request.POST.get("satisfaction", "")
        if action == "confirm" and rating.isdigit() and 1 <= int(rating) <= 5:
            services.record_satisfaction(complaint, request.user, int(rating))
    except (ValidationError, PermissionDenied) as exc:
        response = render(request, "complaints/portal/detail.html",
                          _detail_ctx(request, complaint, error=_msg(exc)), status=422)
        return toast(response, "error", "Not done", _msg(exc))
    complaint.refresh_from_db()
    response = render(request, "complaints/portal/detail.html", _detail_ctx(request, complaint))
    return toast(response, "success", ACTION_TOASTS.get(action, "Done"))


@portal_login_required
@require_GET
def complaint_file(request, pk, file_pk):
    """Attachments of public messages only (the portal never serves internal files)."""
    complaint = _complaint_or_404(request, pk)
    att = ComplaintAttachment.objects.filter(pk=file_pk, complaint=complaint).select_related("message").first()
    if att is None or not att.file or (att.message_id and att.message.visibility != ComplaintMessage.PUBLIC):
        raise Http404
    return attachment_response(request, att)


def attachment_response(request, att, *, channel="portal"):
    """Serve a public-message attachment already checked by the caller (the
    portal page above and the mobile API, Phase 9)."""
    import mimetypes

    from core import audit
    from core import file_preview

    name = att.original_name or att.file.name.rsplit("/", 1)[-1]
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    inline = file_preview.wants_inline(request, ctype)
    audit.log("other", att, actor=getattr(request, "impersonator", None) or request.user,
              note=file_preview.audit_note(inline, "complaint attachment") + f" ({channel})")
    response = FileResponse(att.file.open("rb"), content_type=file_preview.text_charset(ctype))
    return file_preview.finish(response, name, inline)


# ===========================================================================
# Knowledge base
# ===========================================================================
def _articles(user):
    return KnowledgeArticle.objects.filter(is_published=True, audience__in=("all", _audience(user)))


@portal_login_required
@require_GET
def help_index(request):
    _external(request)
    q = (request.GET.get("q") or "").strip()
    articles = services.kb_search(q, audience=_audience(request.user), limit=20) if q else list(
        _articles(request.user).select_related("layer"))
    ctx = _shell_context(request, "complaints", articles=articles, q=q)
    if is_htmx(request) and request.headers.get("HX-Target") == "cx-kb-rows":
        return render(request, "complaints/portal/_kb_rows.html", ctx)
    return render(request, "complaints/portal/help.html", ctx)


@portal_login_required
@require_http_methods(["GET", "POST"])
def help_article(request, slug):
    _external(request)
    article = _articles(request.user).filter(slug=slug).first()
    if article is None:
        raise Http404
    voted = False
    if request.method == "POST":
        field = "helpful" if request.POST.get("helpful") == "yes" else "not_helpful"
        type(article).objects.filter(pk=article.pk).update(**{field: F(field) + 1})
        voted = True
    ctx = _shell_context(request, "complaints", article=article, voted=voted,
                         raise_url=reverse("qa_portal:complaint_new"))
    return render(request, "complaints/portal/article.html", ctx)
