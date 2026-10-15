"""Memo views — list, detail, compose, publish, approve, return, reject,
resubmit, cancel, acknowledge, archive, unarchive, attachment download,
and document downloads (PDF / Word)."""
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import memos as memo_service
from . import routing
from .forms import MemoAttachmentForm, MemoForm
from .models import Memo, MemoApproval, MemoAttachment

logger = logging.getLogger(__name__)


# ===========================================================================
# Helpers
# ===========================================================================
def _require_view(user, pk):
    memo = get_object_or_404(
        Memo.objects.select_related(
            "sender", "department", "route", "target_user",
        ),
        pk=pk,
    )
    if not memo_service.can_view_memo(memo, user):
        raise Http404("Memo not found.")
    return memo


def _compose_guard(user):
    if not memo_service.can_compose_memo(user):
        raise PermissionDenied("You don't have permission to send memos.")


def _can_download_document(memo, user):
    """Published memos: anyone who can view them.
    Not-yet-published memos: sender / managers / superusers only —
    they get the watermarked preview."""
    if memo.status == Memo.PUBLISHED:
        return True
    return memo_service.can_manage_memo(memo, user)


def _sender_signature_lines(memo):
    sender = memo.sender
    name = sender.get_full_name() or sender.email
    designation = ""
    dept_name = ""
    try:
        sp = getattr(sender, "staff_profile", None)
        if sp is not None:
            designation = getattr(sp, "designation", "") or ""
            dept = getattr(sp, "department", None)
            if dept is not None:
                dept_name = dept.name
    except Exception:
        pass
    if not designation:
        role = getattr(sender, "role", None)
        if role is not None:
            designation = role.name
    if not dept_name:
        dept = getattr(sender, "department", None)
        if dept is not None:
            dept_name = dept.name
    return name, designation, dept_name


def _annotate_chain(memo):
    """Return a list of chain rows for the template.

    Each row: {step, actor, state, comment, decided_by, decided_at,
               position, is_current}
    where state ∈ {approved, returned, rejected, current, skipped, pending}
    """
    chain = routing.build_memo_chain(memo)
    if not chain:
        return []

    approvals = {
        routing.step_key(a.step): a
        for a in memo.approvals.select_related("step", "actor")
        if a.step_id
    }

    current_key = memo.current_step_key or ""
    status = memo.status

    current_position = -1
    if current_key:
        for i, s in enumerate(chain):
            if routing.step_key(s) == current_key:
                current_position = i
                break

    is_published = status == Memo.PUBLISHED
    is_awaiting = status == Memo.AWAITING
    is_returned = status == Memo.RETURNED
    is_cancelled = status == Memo.CANCELLED

    rows = []
    for i, step in enumerate(chain):
        key = routing.step_key(step)
        approval = approvals.get(key)
        actor = routing.resolve_memo_approver(step, memo)

        state = "pending"
        comment = ""
        decided_by = None
        decided_at = None

        if approval:
            decided_by = approval.actor
            decided_at = approval.decided_at
            comment = approval.comment or ""
            if approval.decision == MemoApproval.APPROVE:
                state = "approved"
            elif approval.decision == MemoApproval.RETURN:
                state = "returned"
            elif approval.decision == MemoApproval.REJECT:
                state = "rejected"
        elif is_awaiting and current_key and key == current_key:
            state = "current"
        elif is_published and not current_key:
            state = "skipped"
        elif is_awaiting and current_position >= 0 and i < current_position:
            state = "skipped"
        elif is_returned and current_position >= 0 and i < current_position:
            state = "skipped"
        elif is_cancelled:
            state = "skipped"

        rows.append({
            "step": step,
            "actor": actor,
            "state": state,
            "comment": comment,
            "decided_by": decided_by,
            "decided_at": decided_at,
            "position": i + 1,
            "is_current": state == "current",
        })

    return rows


# ===========================================================================
# List
# ===========================================================================
@login_required
def memo_list(request):
    tab = (request.GET.get("tab") or "inbox").strip()
    if tab not in ("inbox", "awaiting", "sent", "archived"):
        tab = "inbox"

    if tab == "sent":
        qs = memo_service.visible_memos_for(request.user).filter(sender=request.user)
    elif tab == "archived":
        qs = memo_service.visible_memos_for(request.user).filter(
            memo_recipients__user=request.user,
            memo_recipients__is_archived=True,
        ).distinct()
    elif tab == "awaiting":
        awaiting_list = memo_service.awaiting_memos_for(request.user)
        ids = [m.pk for m in awaiting_list]
        qs = (
            Memo.objects
            .filter(pk__in=ids)
            .select_related("sender", "route", "department", "target_user")
            .order_by("-updated_at")
        )
    else:
        qs = memo_service.inbox_memos_for(request.user)

    q = (request.GET.get("q") or "").strip()
    prio = (request.GET.get("priority") or "").strip()

    if q:
        from django.db.models import Q
        qs = qs.filter(
            Q(reference__icontains=q)
            | Q(subject__icontains=q)
            | Q(body__icontains=q)
        )
    if prio in dict(Memo.PRIORITY_CHOICES):
        qs = qs.filter(priority=prio)

    awaiting_count = len(memo_service.awaiting_memos_for(request.user))
    counts = {
        "inbox":    memo_service.inbox_memos_for(request.user).count(),
        "awaiting": awaiting_count,
        "sent":     memo_service.visible_memos_for(request.user)
                    .filter(sender=request.user).count(),
        "archived": memo_service.visible_memos_for(request.user)
                    .filter(
                        memo_recipients__user=request.user,
                        memo_recipients__is_archived=True,
                    )
                    .distinct().count(),
    }

    return render(request, "messaging/memo_list.html", {
        "memos": qs,
        "tab": tab,
        "counts": counts,
        "q": q,
        "priority_filter": prio,
        "priority_choices": Memo.PRIORITY_CHOICES,
        "can_compose": memo_service.can_compose_memo(request.user),
    })


# ===========================================================================
# Detail
# ===========================================================================
@login_required
def memo_detail(request, pk):
    memo = _require_view(request.user, pk)

    receipt = memo.memo_recipients.filter(user=request.user).first()
    if (
        receipt is not None
        and receipt.read_at is None
        and memo.status == Memo.PUBLISHED
    ):
        memo_service.mark_memo_read(memo, request.user)
        receipt.refresh_from_db()

    show_stats = (
        memo.sender_id == request.user.pk
        or request.user.is_superuser
        or request.user.has_perm("messaging.manage_memo")
    )
    pending_recipients = []
    if (
        show_stats
        and memo.requires_acknowledgment
        and memo.status == Memo.PUBLISHED
    ):
        pending_recipients = list(
            memo.memo_recipients
            .filter(acknowledged_at__isnull=True)
            .select_related("user")[:50]
        )

    sender_name, sender_desig, sender_dept = _sender_signature_lines(memo)

    total = memo.recipient_count
    acked = memo.acknowledged_count
    ack_pct = int(round((acked / total) * 100)) if total else 0

    can_act = routing.can_act_on_memo(memo, request.user)
    current_step = routing.current_step(memo)
    current_actor = (
        routing.resolve_memo_approver(current_step, memo)
        if current_step else None
    )

    chain_rows = _annotate_chain(memo)

    return render(request, "messaging/memo_detail.html", {
        "memo": memo,
        "receipt": receipt,
        "is_sender": memo.sender_id == request.user.pk,
        "can_manage": memo_service.can_manage_memo(memo, request.user),
        "show_stats": show_stats,
        "pending_recipients": pending_recipients,
        "needs_ack": (
            receipt is not None
            and memo.requires_acknowledgment
            and receipt.acknowledged_at is None
            and memo.status == Memo.PUBLISHED
        ),
        "attachment_form": MemoAttachmentForm(),
        "sender_name": sender_name,
        "sender_desig": sender_desig,
        "sender_dept": sender_dept,
        "ack_pct": ack_pct,
        "can_act": can_act,
        "current_step": current_step,
        "current_actor": current_actor,
        "chain_rows": chain_rows,
    })


# ===========================================================================
# Compose / edit
# ===========================================================================
@login_required
def memo_create(request):
    _compose_guard(request.user)
    allowed_kinds = memo_service.allowed_target_kinds_for(request.user)

    if request.method == "POST":
        form = MemoForm(
            request.POST, request.FILES,
            user=request.user, allowed_kinds=allowed_kinds,
        )
        action = request.POST.get("action", "draft")
        if form.is_valid():
            try:
                memo = memo_service.create_memo(
                    sender=request.user,
                    subject=form.cleaned_data["subject"],
                    body=form.cleaned_data["body"],
                    visibility=form.cleaned_data["visibility"],
                    target_user=form.cleaned_data.get("target_user"),
                    department=form.cleaned_data.get("department"),
                    roles=list(form.cleaned_data.get("roles") or []),
                    recipients=list(form.cleaned_data.get("recipients") or []),
                    priority=form.cleaned_data["priority"],
                    requires_acknowledgment=form.cleaned_data.get(
                        "requires_acknowledgment", False,
                    ),
                    expires_at=form.cleaned_data.get("expires_at"),
                    is_draft=(action != "publish"),
                )
            except (ValidationError, PermissionDenied) as exc:
                msgs = getattr(exc, "messages", None) or [str(exc)]
                messages.error(request, "; ".join(msgs))
            else:
                for f in form.cleaned_data.get("attachments") or []:
                    try:
                        memo_service.add_attachment(memo, request.user, f)
                    except Exception:
                        messages.warning(request, f"Could not attach {f.name}.")

                if action == "publish":
                    try:
                        memo_service.publish_memo(memo, request.user)
                    except (ValidationError, PermissionDenied) as exc:
                        msgs = getattr(exc, "messages", None) or [str(exc)]
                        messages.warning(
                            request,
                            "Draft saved, but the chain could not start: "
                            + "; ".join(msgs),
                        )
                        return redirect("messaging:memo_detail", pk=memo.pk)
                    if memo.status == Memo.PUBLISHED:
                        messages.success(
                            request,
                            f"Memo {memo.reference} published to "
                            f"{memo.recipient_count} recipient(s).",
                        )
                    else:
                        messages.success(
                            request,
                            f"Memo {memo.reference} sent for approval.",
                        )
                else:
                    messages.success(request, "Memo saved as draft.")
                return redirect("messaging:memo_detail", pk=memo.pk)
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        form = MemoForm(user=request.user, allowed_kinds=allowed_kinds)

    return render(request, "messaging/memo_form.html", {
        "form": form,
        "memo": None,
        "mode": "create",
        "allowed_kinds": allowed_kinds,
        "can_broadcast": memo_service.can_broadcast_memo(request.user),
    })


@login_required
def memo_update(request, pk):
    memo = _require_view(request.user, pk)
    if not memo_service.can_manage_memo(memo, request.user):
        raise PermissionDenied("You cannot edit this memo.")

    allowed_kinds = memo_service.allowed_target_kinds_for(request.user)

    if request.method == "POST":
        form = MemoForm(
            request.POST, request.FILES,
            instance=memo, user=request.user, allowed_kinds=allowed_kinds,
        )
        action = request.POST.get("action", "save")
        if form.is_valid():
            try:
                memo_service.update_memo(
                    memo, request.user,
                    subject=form.cleaned_data["subject"],
                    body=form.cleaned_data["body"],
                    visibility=form.cleaned_data["visibility"],
                    priority=form.cleaned_data["priority"],
                    department=form.cleaned_data.get("department"),
                    target_user=form.cleaned_data.get("target_user"),
                    requires_acknowledgment=form.cleaned_data.get(
                        "requires_acknowledgment", False,
                    ),
                    expires_at=form.cleaned_data.get("expires_at"),
                )
                if memo.status in (Memo.DRAFT, Memo.RETURNED):
                    memo.roles.set(form.cleaned_data.get("roles") or [])
                    memo.recipients.set(form.cleaned_data.get("recipients") or [])
            except (ValidationError, PermissionDenied) as exc:
                msgs = getattr(exc, "messages", None) or [str(exc)]
                messages.error(request, "; ".join(msgs))
            else:
                for f in form.cleaned_data.get("attachments") or []:
                    try:
                        memo_service.add_attachment(memo, request.user, f)
                    except Exception:
                        messages.warning(request, f"Could not attach {f.name}.")

                if action == "publish" and memo.status == Memo.DRAFT:
                    try:
                        memo_service.publish_memo(memo, request.user)
                    except (ValidationError, PermissionDenied) as exc:
                        msgs = getattr(exc, "messages", None) or [str(exc)]
                        messages.warning(
                            request,
                            "Saved, but the chain could not start: "
                            + "; ".join(msgs),
                        )
                        return redirect("messaging:memo_detail", pk=memo.pk)
                    messages.success(
                        request,
                        f"Memo {memo.reference} sent for approval."
                        if memo.status != Memo.PUBLISHED
                        else f"Memo {memo.reference} published.",
                    )
                else:
                    messages.success(request, "Memo updated.")
                return redirect("messaging:memo_detail", pk=memo.pk)
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        form = MemoForm(instance=memo, user=request.user, allowed_kinds=allowed_kinds)
        form.fields["roles"].initial = memo.roles.all()
        form.fields["recipients"].initial = memo.recipients.all()
        form.fields["target_user"].initial = memo.target_user

    return render(request, "messaging/memo_form.html", {
        "form": form,
        "memo": memo,
        "mode": "update",
        "allowed_kinds": allowed_kinds,
        "can_broadcast": memo_service.can_broadcast_memo(request.user),
    })


# ===========================================================================
# Workflow actions
# ===========================================================================
@login_required
@require_POST
def memo_publish(request, pk):
    memo = _require_view(request.user, pk)
    if not memo_service.can_manage_memo(memo, request.user):
        raise PermissionDenied("You cannot publish this memo.")
    try:
        memo_service.publish_memo(memo, request.user)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        if memo.status == Memo.PUBLISHED:
            messages.success(
                request,
                f"Memo {memo.reference} published to {memo.recipient_count} recipient(s).",
            )
        else:
            messages.success(request, f"Memo {memo.reference} sent for approval.")
    return redirect("messaging:memo_detail", pk=memo.pk)


@login_required
@require_POST
def memo_approve(request, pk):
    memo = _require_view(request.user, pk)
    comment = (request.POST.get("comment") or "").strip()
    try:
        routing.approve_step(memo, request.user, comment=comment)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        if memo.status == Memo.PUBLISHED:
            messages.success(
                request,
                f"Approved. Memo {memo.reference} published to "
                f"{memo.recipient_count} recipient(s).",
            )
        elif memo.status == Memo.AWAITING:
            messages.success(request, "Approved. Passed to the next step.")
        else:
            messages.success(request, "Approved.")
    return redirect("messaging:memo_detail", pk=memo.pk)


@login_required
@require_POST
def memo_return(request, pk):
    memo = _require_view(request.user, pk)
    comment = (request.POST.get("comment") or "").strip()
    try:
        routing.return_to_sender(memo, request.user, comment=comment)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Memo returned to the sender.")
    return redirect("messaging:memo_detail", pk=memo.pk)


@login_required
@require_POST
def memo_reject(request, pk):
    memo = _require_view(request.user, pk)
    comment = (request.POST.get("comment") or "").strip()
    try:
        routing.reject_memo(memo, request.user, comment=comment)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Memo rejected.")
    return redirect("messaging:memo_detail", pk=memo.pk)


@login_required
@require_POST
def memo_resubmit(request, pk):
    memo = _require_view(request.user, pk)
    if memo.sender_id != request.user.pk and not request.user.is_superuser:
        raise PermissionDenied("Only the sender can resubmit.")

    subject = (request.POST.get("subject") or "").strip() or None
    body = (request.POST.get("body") or "").strip() or None
    comment = (request.POST.get("comment") or "").strip()

    try:
        routing.resubmit_memo(
            memo, request.user,
            subject=subject, body=body, comment=comment,
        )
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        if memo.status == Memo.PUBLISHED:
            messages.success(
                request,
                f"Memo {memo.reference} resubmitted and published.",
            )
        else:
            messages.success(request, "Memo resubmitted for approval.")
    return redirect("messaging:memo_detail", pk=memo.pk)


@login_required
@require_POST
def memo_cancel(request, pk):
    memo = _require_view(request.user, pk)
    reason = (request.POST.get("reason") or "").strip()
    try:
        memo_service.cancel_memo(memo, request.user, reason=reason)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, f"Memo {memo.reference} cancelled.")
    return redirect("messaging:memo_list")


# ===========================================================================
# Acknowledge / archive / delete
# ===========================================================================
@login_required
@require_POST
def memo_acknowledge(request, pk):
    memo = _require_view(request.user, pk)
    try:
        memo_service.acknowledge_memo(memo, request.user)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Acknowledged.")
    return redirect("messaging:memo_detail", pk=memo.pk)


@login_required
@require_POST
def memo_archive(request, pk):
    memo = _require_view(request.user, pk)
    try:
        memo_service.archive_memo(memo, request.user)
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Memo {memo.reference} archived.")
    return redirect("messaging:memo_list")


@login_required
@require_POST
def memo_unarchive(request, pk):
    memo = _require_view(request.user, pk)
    try:
        memo_service.unarchive_memo(memo, request.user)
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Memo {memo.reference} restored to your inbox.")
    return redirect("messaging:memo_list")


@login_required
@require_POST
def memo_delete(request, pk):
    memo = _require_view(request.user, pk)
    try:
        memo_service.delete_memo(memo, request.user)
    except PermissionDenied as exc:
        messages.error(request, str(exc))
        return redirect("messaging:memo_detail", pk=memo.pk)
    messages.success(request, f"Memo {memo.reference} deleted.")
    return redirect("messaging:memo_list")


# ===========================================================================
# Attachments
# ===========================================================================
@login_required
@require_POST
def memo_add_attachment(request, pk):
    memo = _require_view(request.user, pk)
    if not memo_service.can_manage_memo(memo, request.user):
        raise PermissionDenied("You cannot attach files to this memo.")
    form = MemoAttachmentForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Please choose a file.")
        return redirect("messaging:memo_detail", pk=memo.pk)
    try:
        memo_service.add_attachment(
            memo, request.user,
            file=form.cleaned_data["file"],
            description=form.cleaned_data.get("description") or "",
        )
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Attachment added.")
    return redirect("messaging:memo_detail", pk=memo.pk)


@login_required
@require_POST
def memo_remove_attachment(request, pk, attachment_pk):
    memo = _require_view(request.user, pk)
    attachment = get_object_or_404(MemoAttachment, pk=attachment_pk, memo=memo)
    try:
        memo_service.remove_attachment(memo, request.user, attachment)
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Attachment removed.")
    return redirect("messaging:memo_detail", pk=memo.pk)


@login_required
def memo_attachment_download(request, pk):
    import mimetypes
    from urllib.parse import quote
    from django.conf import settings
    from django.http import FileResponse

    attachment = get_object_or_404(
        MemoAttachment.objects.select_related("memo"), pk=pk,
    )
    memo = attachment.memo

    if not memo_service.can_view_memo(memo, request.user):
        raise PermissionDenied("You don't have access to this memo.")

    if (
        attachment.is_deleted
        or attachment.expires_at <= timezone.now()
        or not attachment.file
    ):
        raise Http404("This attachment is no longer available.")

    original_name = (
        getattr(attachment, "original_name", "")
        or attachment.file.name.rsplit("/", 1)[-1]
    )
    ctype = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    disposition = f"attachment; filename*=UTF-8''{quote(original_name)}"

    if getattr(settings, "USE_X_ACCEL_REDIRECT", False):
        resp = HttpResponse(content_type=ctype)
        prefix = getattr(settings, "PROTECTED_MEDIA_URL", "/protected/")
        resp["X-Accel-Redirect"] = prefix + quote(attachment.file.name)
    else:
        resp = FileResponse(attachment.file.open("rb"), content_type=ctype)

    resp["Content-Disposition"] = disposition
    resp["Cache-Control"] = "private, no-store"
    return resp


# ===========================================================================
# Document downloads — PDF & Word
# ===========================================================================
@login_required
def memo_download_pdf(request, pk):
    memo = _require_view(request.user, pk)

    # Gate: only sender / managers / superuser can preview before publish.
    # Everyone who can view the memo can download once it's published.
    if not _can_download_document(memo, request.user):
        messages.error(
            request,
            "This memo isn't published yet. PDF downloads unlock once "
            "the approval chain completes.",
        )
        return redirect("messaging:memo_detail", pk=memo.pk)

    from .memo_documents import render_memo_pdf
    try:
        pdf_bytes = render_memo_pdf(memo, request=request)
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect("messaging:memo_detail", pk=memo.pk)
    except Exception:
        logger.exception("Memo PDF generation failed for %s", memo.reference)
        messages.error(request, "Could not generate the PDF. Try again later.")
        return redirect("messaging:memo_detail", pk=memo.pk)

    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{memo.reference}.pdf"'
    resp["Cache-Control"] = "private, no-store"
    return resp


@login_required
def memo_download_docx(request, pk):
    memo = _require_view(request.user, pk)

    # Same gate as the PDF download.
    if not _can_download_document(memo, request.user):
        messages.error(
            request,
            "This memo isn't published yet. Word downloads unlock once "
            "the approval chain completes.",
        )
        return redirect("messaging:memo_detail", pk=memo.pk)

    from .memo_documents import render_memo_docx
    try:
        docx_bytes = render_memo_docx(memo, request=request)
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect("messaging:memo_detail", pk=memo.pk)
    except Exception:
        logger.exception("Memo DOCX generation failed for %s", memo.reference)
        messages.error(request, "Could not generate the Word document.")
        return redirect("messaging:memo_detail", pk=memo.pk)

    resp = HttpResponse(
        docx_bytes,
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )
    resp["Content-Disposition"] = f'attachment; filename="{memo.reference}.docx"'
    resp["Cache-Control"] = "private, no-store"
    return resp