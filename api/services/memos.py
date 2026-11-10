"""Memos for the mobile app: shapes messaging.memos / messaging.routing data.

Visibility (messaging.memos.can_view_memo), the tab lists
(memo_tab_queryset), the page state and the buttons a user gets
(memo_detail_state) all come from the website's own services."""
import mimetypes

from django.urls import reverse
from django.utils import timezone

from messaging import chat_services as cs
from messaging import memos as memo_service
from messaging.models import Memo

from .chats import _iso, person


def audience_label(memo):
    from messaging.memo_documents import _memo_recipients_label

    label = _memo_recipients_label(memo)
    if label == "—" and memo.visibility == Memo.INDIVIDUALS:
        names = [u.get_full_name() or u.email for u in memo.recipients.all()[:6]]
        more = memo.recipients.count() - len(names)
        label = ", ".join(names) + (f" +{more} more" if more > 0 else "") if names else label
    return label


def _receipt_json(receipt):
    if receipt is None:
        return None
    return {"read_at": _iso(receipt.read_at), "acknowledged_at": _iso(receipt.acknowledged_at),
            "archived": receipt.is_archived}


def memo_row_json(memo, user, receipts=None):
    receipt = (receipts or {}).get(memo.pk) if receipts is not None else \
        memo.memo_recipients.filter(user=user).first()
    step = memo.current_step if memo.status == Memo.AWAITING else None
    return {
        "id": memo.pk,
        "reference": memo.reference,
        "subject": memo.subject,
        "summary": memo.card_summary,
        "priority": memo.priority,
        "status": memo.status,
        "status_label": memo.get_status_display(),
        "sender": person(memo.sender),
        "audience": audience_label(memo),
        "created_at": _iso(memo.created_at),
        "published_at": _iso(memo.published_at),
        "updated_at": _iso(memo.updated_at),
        "requires_acknowledgment": memo.requires_acknowledgment,
        "unread": bool(receipt and receipt.read_at is None and memo.status == Memo.PUBLISHED),
        "acknowledged": bool(receipt and receipt.acknowledged_at),
        "current_step": step.name if step else None,
        "attachment_count": memo.attachments.count(),
        "kind": memo.kind,
    }


def receipts_for(user, memos):
    from messaging.models import MemoRecipient

    ids = [m.pk for m in memos]
    return {r.memo_id: r for r in MemoRecipient.objects.filter(user=user, memo_id__in=ids)}


def _step_row(row):
    def who(u):
        return None if u is None else person(u)

    return {
        "position": row["position"],
        "name": row["step"].name,
        "state": row["state"],
        "is_current": row["is_current"],
        "actor": who(row["actor"]),
        "reason": row["reason"] or "",
        "decided_by": who(row["decided_by"]),
        "decided_at": _iso(row["decided_at"]),
        "comment": row["comment"] or "",
    }


def attachment_json(att):
    name = att.original_name or att.file.name.rsplit("/", 1)[-1]
    try:
        size = att.file.size if att.file else 0
    except (OSError, ValueError):
        size = 0
    return {
        "id": att.pk,
        "name": name,
        "description": att.description,
        "size_bytes": size,
        "content_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
        "download_path": reverse("api:memos_attachment", args=[att.pk]),
        "expires_at": _iso(att.expires_at),
        "expired": att.expires_at <= timezone.now(),
    }


def memo_detail_json(memo, user):
    state = memo_service.memo_detail_state(memo, user)
    step = state["current_step"]
    stats = None
    if state["show_stats"] and memo.status == Memo.PUBLISHED:
        stats = {
            "recipients": memo.recipient_count,
            "read": memo.read_count,
            "acknowledged": memo.acknowledged_count,
            "pending_acknowledgment": [cs.user_name(r.user) for r in state["pending_recipients"]],
        }
    can_doc = state["can_download_document"]
    return {
        **memo_row_json(memo, user, {memo.pk: state["receipt"]} if state["receipt"] else {}),
        "body": memo.body,
        "visibility": memo.visibility,
        "visibility_label": memo.get_visibility_display(),
        "target_user": person(memo.target_user) if memo.target_user_id else None,
        "department": ({"id": memo.department_id, "name": memo.department.name}
                       if memo.department_id else None),
        "expires_at": _iso(memo.expires_at),
        "attachments": [attachment_json(a) for a in memo.attachments.all()],
        "route": memo.route.name if memo.route_id else None,
        "route_steps": [_step_row(r) for r in state["chain_rows"]],
        "current_step_detail": None if step is None or memo.status != Memo.AWAITING else {
            "name": step.name, "instructions": step.instructions,
            "actor": person(state["current_actor"]) if state["current_actor"] else None},
        "acting_for_someone": bool(state["acting_for"]),
        "parked_reason": state["parked_reason"],
        "returned": None if memo.status != Memo.RETURNED else {
            "by": person(memo.returned_by) if memo.returned_by_id else None,
            "at": _iso(memo.returned_at), "comment": memo.returned_comment},
        "rejected": None if memo.status != Memo.REJECTED else {
            "by": person(memo.rejected_by) if memo.rejected_by_id else None,
            "at": _iso(memo.rejected_at), "comment": memo.rejection_comment},
        "my_receipt": _receipt_json(state["receipt"]),
        "is_sender": state["is_sender"],
        "actions": state["actions"],
        "stats": stats,
        "institutions": None if not memo.is_outbound else {
            "scope": memo.institution_scope,
            "label": _institutions_label(memo),
            "signed_as": memo.signed_as,
            "delivered": memo.institution_recipients.count(),
            "read": memo.institution_recipients.filter(read_at__isnull=False).count(),
            "acknowledged": memo.institution_recipients.filter(acknowledged_at__isnull=False).count(),
        },
        "documents": None if not can_doc else {
            "pdf_path": reverse("api:memos_pdf", args=[memo.pk]),
            "docx_path": reverse("api:memos_docx", args=[memo.pk]),
            "watermarked": memo.status != Memo.PUBLISHED,
        },
    }


def _institutions_label(memo):
    from messaging.memo_documents import institutions_label
    return institutions_label(memo)


def compose_options(user):
    from django.contrib.auth.models import Group

    from staff.models import Department

    kinds = memo_service.allowed_target_kinds_for(user)
    labels = dict(Memo.VISIBILITY_CHOICES)
    return {
        "visibility": [{"value": k, "label": labels[k]} for k, _ in Memo.VISIBILITY_CHOICES
                       if k in kinds and k != Memo.NO_STAFF],
        "priorities": [{"value": v, "label": label} for v, label in Memo.PRIORITY_CHOICES],
        "departments": [{"id": d.pk, "name": d.name} for d in
                        Department.objects.filter(is_active=True).order_by("sort_order", "name")]
        if Memo.DEPARTMENT in kinds else [],
        "roles": [{"id": g.pk, "name": g.name} for g in Group.objects.order_by("name")]
        if Memo.ROLE in kinds else [],
        "can_broadcast": memo_service.can_broadcast_memo(user),
        "attachments_total_bytes": 5 * 1024 * 1024,
        "kinds": [{"value": v, "label": label} for v, label in Memo.KIND_CHOICES
                  if v in memo_service.allowed_memo_kinds(user)],
        "can_send_external": memo_service.can_send_external_memo(user),
        "institution_filter_options": _filter_options(user),
    }


def _filter_options(user):
    if not memo_service.can_initiate_external_memo(user):
        return {}
    from messaging.memo_audience import institution_filter_options
    o = institution_filter_options()
    return {k: [list(x) for x in v] if isinstance(v, list) else v for k, v in o.items()}
