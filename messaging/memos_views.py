"""Memo views — list, detail, compose, publish, acknowledge, archive,
unarchive, attachment download, and document downloads (PDF / Word)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import memos as memo_service
from .forms import MemoAttachmentForm, MemoForm
from .models import Memo, MemoAttachment


# ===========================================================================
# Helpers
# ===========================================================================
def _require_view(user, pk):
    memo = get_object_or_404(
        Memo.objects.select_related("sender", "department"),
        pk=pk,
    )
    if not memo_service.can_view_memo(memo, user):
        raise Http404("Memo not found.")
    return memo


def _compose_guard(user):
    if not memo_service.can_compose_memo(user):
        raise PermissionDenied("You don't have permission to send memos.")


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


# ===========================================================================
# List
# ===========================================================================
@login_required
def memo_list(request):
    tab = (request.GET.get("tab") or "inbox").strip()
    if tab not in ("inbox", "sent", "archived"):
        tab = "inbox"

    if tab == "sent":
        qs = memo_service.visible_memos_for(request.user).filter(sender=request.user)
    elif tab == "archived":
        qs = memo_service.visible_memos_for(request.user).filter(
            memo_recipients__user=request.user,
            memo_recipients__is_archived=True,
        ).distinct()
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

    counts = {
        "inbox": memo_service.inbox_memos_for(request.user).count(),
        "sent": (
            memo_service.visible_memos_for(request.user)
            .filter(sender=request.user)
            .count()
        ),
        "archived": (
            memo_service.visible_memos_for(request.user)
            .filter(
                memo_recipients__user=request.user,
                memo_recipients__is_archived=True,
            )
            .distinct()
            .count()
        ),
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
    if receipt is not None and receipt.read_at is None:
        memo_service.mark_memo_read(memo, request.user)
        receipt.refresh_from_db()

    show_stats = (
        memo.sender_id == request.user.pk
        or request.user.is_superuser
        or request.user.has_perm("messaging.manage_memo")
    )
    pending_recipients = []
    if show_stats and memo.requires_acknowledgment:
        pending_recipients = list(
            memo.memo_recipients
            .filter(acknowledged_at__isnull=True)
            .select_related("user")[:50]
        )

    sender_name, sender_desig, sender_dept = _sender_signature_lines(memo)

    total = memo.recipient_count
    acked = memo.acknowledged_count
    ack_pct = int(round((acked / total) * 100)) if total else 0

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
        ),
        "attachment_form": MemoAttachmentForm(),
        "sender_name": sender_name,
        "sender_desig": sender_desig,
        "sender_dept": sender_dept,
        "ack_pct": ack_pct,
    })


# ===========================================================================
# Compose / edit
# ===========================================================================
@login_required
def memo_create(request):
    _compose_guard(request.user)

    if request.method == "POST":
        form = MemoForm(request.POST, request.FILES, user=request.user)
        action = request.POST.get("action", "draft")
        if form.is_valid():
            try:
                memo = memo_service.create_memo(
                    sender=request.user,
                    subject=form.cleaned_data["subject"],
                    body=form.cleaned_data["body"],
                    visibility=form.cleaned_data["visibility"],
                    priority=form.cleaned_data["priority"],
                    department=form.cleaned_data.get("department"),
                    roles=list(form.cleaned_data.get("roles") or []),
                    recipients=list(form.cleaned_data.get("recipients") or []),
                    requires_acknowledgment=form.cleaned_data.get(
                        "requires_acknowledgment", False,
                    ),
                    expires_at=form.cleaned_data.get("expires_at"),
                    is_draft=(action != "publish"),
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                for f in form.cleaned_data.get("attachments") or []:
                    try:
                        memo_service.add_attachment(memo, request.user, f)
                    except Exception:
                        messages.warning(request, f"Could not attach {f.name}.")

                if action == "publish":
                    try:
                        memo_service.publish_memo(memo, request.user)
                    except ValidationError as exc:
                        messages.warning(
                            request,
                            "Draft saved, but publication failed: "
                            + "; ".join(exc.messages),
                        )
                        return redirect("messaging:memo_detail", pk=memo.pk)
                    messages.success(
                        request,
                        f"Memo {memo.reference} published to "
                        f"{memo.recipient_count} recipient(s).",
                    )
                else:
                    messages.success(request, "Memo saved as draft.")
                return redirect("messaging:memo_detail", pk=memo.pk)
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        form = MemoForm(user=request.user)

    return render(request, "messaging/memo_form.html", {
        "form": form,
        "memo": None,
        "mode": "create",
    })


@login_required
def memo_update(request, pk):
    memo = _require_view(request.user, pk)
    if not memo_service.can_manage_memo(memo, request.user):
        raise PermissionDenied("You cannot edit this memo.")

    if request.method == "POST":
        form = MemoForm(request.POST, request.FILES, instance=memo, user=request.user)
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
                    requires_acknowledgment=form.cleaned_data.get(
                        "requires_acknowledgment", False,
                    ),
                    expires_at=form.cleaned_data.get("expires_at"),
                )
                if not memo.is_published:
                    memo.roles.set(form.cleaned_data.get("roles") or [])
                    memo.recipients.set(form.cleaned_data.get("recipients") or [])
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                for f in form.cleaned_data.get("attachments") or []:
                    try:
                        memo_service.add_attachment(memo, request.user, f)
                    except Exception:
                        messages.warning(request, f"Could not attach {f.name}.")

                if action == "publish" and memo.is_draft:
                    try:
                        memo_service.publish_memo(memo, request.user)
                    except ValidationError as exc:
                        messages.warning(
                            request,
                            "Saved, but publication failed: "
                            + "; ".join(exc.messages),
                        )
                        return redirect("messaging:memo_detail", pk=memo.pk)
                    messages.success(
                        request,
                        f"Memo {memo.reference} published to "
                        f"{memo.recipient_count} recipient(s).",
                    )
                else:
                    messages.success(request, "Memo updated.")
                return redirect("messaging:memo_detail", pk=memo.pk)
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        form = MemoForm(instance=memo, user=request.user)
        form.fields["roles"].initial = memo.roles.all()
        form.fields["recipients"].initial = memo.recipients.all()

    return render(request, "messaging/memo_form.html", {
        "form": form,
        "memo": memo,
        "mode": "update",
    })


# ===========================================================================
# Actions
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
        messages.success(
            request,
            f"Memo {memo.reference} published to {memo.recipient_count} recipient(s).",
        )
    return redirect("messaging:memo_detail", pk=memo.pk)


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
    """Restore an archived memo to the user's inbox."""
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
    """Stream a memo attachment — permission-checked."""
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
    """Stream the memo as a PDF with the NBTE letterhead."""
    memo = _require_view(request.user, pk)

    from .memo_documents import render_memo_pdf

    try:
        pdf_bytes = render_memo_pdf(memo, request=request)
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect("messaging:memo_detail", pk=memo.pk)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "Memo PDF generation failed for %s", memo.reference,
        )
        messages.error(request, "Could not generate the PDF. Try again later.")
        return redirect("messaging:memo_detail", pk=memo.pk)

    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{memo.reference}.pdf"'
    resp["Cache-Control"] = "private, no-store"
    return resp


@login_required
def memo_download_docx(request, pk):
    """Stream the memo as a Word .docx with the NBTE letterhead."""
    memo = _require_view(request.user, pk)

    from .memo_documents import render_memo_docx

    try:
        docx_bytes = render_memo_docx(memo, request=request)
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect("messaging:memo_detail", pk=memo.pk)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "Memo DOCX generation failed for %s", memo.reference,
        )
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