"""Portal door: complaints and the knowledge base — JSON shapes of what
``complaints.portal_views`` shows. Complaints are read through
``complaints.services.visible_to`` / ``get_visible``; internal notes never
leave this module (public messages only, TR-12)."""
from django.http import Http404
from django.urls import reverse

EXTERNAL = ("institution", "resource_person")


def external_or_404(user):
    """complaints.portal_views._external."""
    if getattr(user, "user_type", "staff") not in EXTERNAL:
        raise Http404


def complaint_or_404(user, pk):
    from complaints import services

    external_or_404(user)
    complaint = services.get_visible(user, pk)
    if complaint is None:
        raise Http404
    return complaint


def complaints_queryset(user, params):
    from django.db.models import Q

    from complaints import services

    qs = services.visible_to(user).select_related("layer", "current_department")
    status = params.get("status", "open")
    if status == "open":
        qs = qs.filter(closed_at__isnull=True)
    elif status == "closed":
        qs = qs.filter(closed_at__isnull=False)
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(subject__icontains=q))
    return qs.order_by("-created_at", "-id")


def complaint_row(c):
    from complaints.portal_views import _public_stage

    return {"id": c.pk, "reference": c.reference, "subject": c.subject,
            "layer": {"id": c.layer_id, "name": c.layer.name} if c.layer_id else None, "status": c.status,
            "stage_label": str(_public_stage(c)), "is_open": bool(c.is_open), "created_at": c.created_at,
            "closed_at": c.closed_at, "related_label": c.related_label or ""}


def complaint_detail_payload(request, c):
    """complaints.portal_views._detail_ctx, as JSON."""
    from complaints import services
    from complaints.models import ComplaintMessage
    from complaints.portal_views import _steps

    from .me import initials
    from .portal import user_name

    user = request.user
    readonly = bool(getattr(request, "is_impersonating", False))
    msgs = []
    for m in (c.messages.filter(visibility=ComplaintMessage.PUBLIC)  # never internal notes
              .select_related("author").prefetch_related("attachments").order_by("created_at", "id")):
        msgs.append({"id": m.pk, "author": user_name(m.author) or "NBTE",
                     "initials": initials(m.author) if m.author_id else "N",
                     "mine": m.author_id == user.pk, "is_system": bool(m.is_system), "body": m.body,
                     "at": m.created_at,
                     "attachments": [{"id": f.pk, "name": f.display_name, "size_bytes": f.size_bytes or 0,
                                      "size_label": f.size_label or "",
                                      "download_url": reverse("api:portal_complaint_file", args=[c.pk, f.pk])}
                                     for f in m.attachments.all()]})
    current = c.open_stage()
    department = getattr(current, "assigned_department", None) or c.current_department
    officer = current.assigned_to if current is not None and services.show_officer_name() else None
    actions = services.raiser_actions(user, c) if not readonly else []
    out = complaint_row(c)
    out.update({
        "body": c.body, "resolution": c.resolution or "", "resolved_at": c.resolved_at,
        "department": department.name if department is not None else "NBTE",
        "officer": user_name(officer) if officer is not None else "",
        "steps": _steps(c), "messages": msgs,
        "actions": [{"code": t.action_code, "label": str(t.label)} for t in actions],
        "can_reply": bool(c.is_open and services.is_raiser(user, c) and not readonly),
        "waiting_for_me": c.status == "awaiting_raiser",
        "reopen_days": services.reopen_window_days(),
    })
    return out


def layer_dict(layer):
    return {"id": layer.pk, "code": layer.code, "name": layer.name, "description": layer.description or "",
            "parent": layer.parent.name if layer.parent_id else "",
            "custom_fields": [{"key": str(f.get("key") or ""), "label": str(f.get("label") or f.get("key") or ""),
                               "type": str(f.get("type") or "text"), "required": bool(f.get("required")),
                               "options": [str(o) for o in (f.get("options") or [])]}
                              for f in (layer.custom_fields or []) if f.get("key")]}


def article_dict(a, *, body=False):
    out = {"slug": a.slug, "title": a.title, "summary": a.summary or "",
           "layer": a.layer.name if a.layer_id else ""}
    if body:
        out["body"] = a.body
    return out


def resolve_about(user, about):
    """complaints.portal_views._about: "kind:pk" -> (about, kind, record)."""
    from complaints import services

    kind, _sep, pk = (about or "").partition(":")
    related = services.resolve_related(user, kind, pk)
    return ((about if related is not None else ""), kind if related is not None else "", related)


def new_complaint_payload(user, about=""):
    from complaints import services

    layers = services.layers_for(user)
    about, kind, related = resolve_about(user, about)
    suggested = services.SUGGESTED_LAYERS.get(kind, "")
    suggested_layer = next((lay.pk for lay in layers if lay.code == suggested), None)
    audience = getattr(user, "user_type", "institution")
    return {"layers": [layer_dict(lay) for lay in layers], "suggested_layer": suggested_layer,
            "about": about or "", "related_label": services.related_label(related) if related is not None else "",
            "popular": [article_dict(a) for a in services.kb_search("payment login password ssq visit",
                                                                    audience=audience, limit=3)]}
