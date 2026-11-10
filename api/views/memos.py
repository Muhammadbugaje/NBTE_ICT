"""Memos (staff door). Thin views over messaging.memos / messaging.routing.
A memo the user may not view answers 404; an action the user may not take
on a memo they can view answers 403 (the services' PermissionDenied) or 400
(ValidationError, e.g. reject without a comment). See api/docs/memos.md."""
import logging

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from messaging import memos as memo_service
from messaging import routing
from messaging.models import Memo, MemoAttachment

from .. import serializers_memos as s
from ..exceptions import ApiError
from ..pagination import StandardPagination
from ..services import memos as svc
from .common import AUTHED_ERRORS, errors

logger = logging.getLogger("api")
TAGS = ["memos"]
FILE = {200: OpenApiResponse(OpenApiTypes.BINARY, description="The file bytes.")}


def _query(serializer_class, request):
    ser = serializer_class(data=request.query_params)
    ser.is_valid(raise_exception=True)
    return ser.validated_data


def _detail(memo, user):
    return s.MemoDetailSerializer(svc.memo_detail_json(memo, user)).data


class MemoListView(APIView):
    @extend_schema(operation_id="memos_list", tags=TAGS, parameters=[s.MemoListQuerySerializer],
                   responses={200: s.MemoListSerializer, **errors(400), **AUTHED_ERRORS})
    def get(self, request):
        params = _query(s.MemoListQuerySerializer, request)
        tab = params["tab"]
        qs = memo_service.memo_tab_queryset(request.user, "sent_published" if tab == "sent" else tab,
                                            q=params.get("q", ""), priority=params.get("priority", ""))
        paginator = StandardPagination()
        page = list(paginator.paginate_queryset(qs, request, view=self))
        receipts = svc.receipts_for(request.user, page)
        rows = [svc.memo_row_json(m, request.user, receipts) for m in page]
        counts = memo_service.memo_tab_counts(request.user, tabs=("inbox", "awaiting", "sent_published",
                                                                  "drafts", "archived"))
        counts["sent"] = counts.pop("sent_published")
        counts = {k: counts[k] for k in memo_service.MEMO_TABS}
        resp = paginator.get_paginated_response(s.MemoRowSerializer(rows, many=True).data)
        resp.data["counts"] = counts
        resp.data["can_compose"] = memo_service.can_compose_memo(request.user)
        return resp

    @extend_schema(operation_id="memos_create", tags=TAGS,
                   request={"application/json": s.MemoCreateSerializer,
                            "multipart/form-data": s.MemoCreateSerializer},
                   description="Validated by the website's MemoForm; action=publish starts the routing chain.",
                   responses={201: s.MemoCreateResponseSerializer, **errors(400), **AUTHED_ERRORS})
    def post(self, request):
        from messaging.forms import MemoForm

        if not memo_service.can_compose_memo(request.user):
            raise PermissionDenied
        # (kind / institution rules: messaging.memos.validate_kind_and_targets via submit_memo_form)
        ser = s.MemoCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        data = {
            "subject": d["subject"], "body": d["body"], "priority": d["priority"], "visibility": d["visibility"],
            "target_user": d.get("target_user") or "", "department": d.get("department") or "",
            "roles": d.get("roles") or [], "recipients": d.get("recipients") or [],
            "expires_at": (timezone.localtime(d["expires_at"]).strftime("%Y-%m-%dT%H:%M")
                           if d.get("expires_at") else ""),
        }
        if d.get("requires_acknowledgment"):
            data["requires_acknowledgment"] = "on"
        # v2.1 Phase E: memo type, institutions and staff filters, in the form's field names.
        from django.http import QueryDict
        qd = QueryDict(mutable=True)
        for k, v in data.items():
            qd.setlist(k, [str(x) for x in v]) if isinstance(v, list) else qd.__setitem__(k, v)
        qd["kind"] = d.get("kind") or "internal"
        qd["institution_scope"] = d.get("institution_scope") or ""
        qd.setlist("target_institutions", [str(x) for x in d.get("target_institutions") or []])
        for key, val in (d.get("institution_filter") or {}).items():
            vals = val if isinstance(val, list) else [val]
            qd.setlist(f"if_{key}", ["1" if v is True else str(v) for v in vals if v not in (None, False, "")])
        for key, val in (d.get("staff_filter") or {}).items():
            vals = val if isinstance(val, list) else [val]
            qd.setlist(f"sf_{key}", [str(v) for v in vals if v not in (None, "")])
        data = qd
        files = _MultiFiles({"attachments": d.get("attachments") or []})
        form = MemoForm(data, files, user=request.user,
                        allowed_kinds=memo_service.allowed_target_kinds_for(request.user))
        if not form.is_valid():
            raise serializers.ValidationError(
                {("non_field_errors" if k == "__all__" else k): [str(e) for e in v] for k, v in form.errors.items()})
        memo, notes = memo_service.submit_memo_form(request.user, form.cleaned_data,
                                                    publish=d["action"] == "publish",
                                                    from_message=d.get("from_message"))
        memo = memo_service.get_memo_for(request.user, memo.pk)
        return Response({"notice": " ".join(t for _, t in notes),
                         "notices": [{"level": lvl, "text": t} for lvl, t in notes],
                         "memo": _detail(memo, request.user)}, status=201)


class _MultiFiles(dict):
    """dict with getlist() so MemoForm's multiple-file widget reads it."""

    def getlist(self, key, default=None):
        return list(self.get(key) or (default or []))


class ComposeOptionsView(APIView):
    @extend_schema(operation_id="memos_compose_options", tags=TAGS,
                   responses={200: s.ComposeOptionsSerializer, **AUTHED_ERRORS})
    def get(self, request):
        return Response(s.ComposeOptionsSerializer(svc.compose_options(request.user)).data)


class PeopleView(APIView):
    @extend_schema(operation_id="memos_people", tags=TAGS, parameters=[s.PeopleQuerySerializer],
                   description="People a memo can be addressed to (the memo form's picker).",
                   responses={200: s.MemoPersonSerializer(many=True), **errors(400), **AUTHED_ERRORS})
    def get(self, request):
        from django.db.models import Q

        from messaging.forms import MemoForm

        params = _query(s.PeopleQuerySerializer, request)
        qs = MemoForm(user=request.user).fields["recipients"].queryset.select_related("department")
        for word in (params.get("q") or "").split()[:4]:
            qs = qs.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word)
                           | Q(email__icontains=word) | Q(department__name__icontains=word))
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        rows = [{**svc.person(u), "email": u.email, "department": u.department.name if u.department_id else None}
                for u in page]
        return paginator.get_paginated_response(s.MemoPersonSerializer(rows, many=True).data)


class MemoDetailView(APIView):
    @extend_schema(operation_id="memos_detail", tags=TAGS,
                   description="Opening marks your copy read (as on the website).",
                   responses={200: s.MemoDetailSerializer, **errors(404), **AUTHED_ERRORS})
    def get(self, request, pk):
        memo = memo_service.get_memo_for(request.user, pk)
        memo = memo_service.open_memo(memo, request.user)
        return Response(_detail(memo, request.user))


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def _approve(memo, user, d):
    routing.approve_step(memo, user, comment=d.get("comment", "").strip())
    if memo.status == Memo.PUBLISHED:
        return f"Approved. Memo {memo.reference} published to {memo.recipient_count} recipient(s)."
    if memo.status == Memo.AWAITING:
        return "Approved. Passed to the next step."
    return "Approved."


def _return(memo, user, d):
    routing.return_to_sender(memo, user, comment=d.get("comment", "").strip())
    return "Memo returned to the sender."


def _reject(memo, user, d):
    routing.reject_memo(memo, user, comment=d.get("comment", "").strip())
    return "Memo rejected."


def _resubmit(memo, user, d):
    if memo.sender_id != user.pk and not user.is_superuser:
        raise PermissionDenied("Only the sender can resubmit.")
    routing.resubmit_memo(memo, user, subject=(d.get("subject") or "").strip() or None,
                          body=(d.get("body") or "").strip() or None, comment=d.get("comment", "").strip())
    if memo.status == Memo.PUBLISHED:
        return f"Memo {memo.reference} resubmitted and published."
    return "Memo resubmitted for approval."


def _publish(memo, user, d):
    if not memo_service.can_manage_memo(memo, user):
        raise PermissionDenied("You cannot publish this memo.")
    memo_service.publish_memo(memo, user)
    if memo.status == Memo.PUBLISHED:
        return f"Memo {memo.reference} published to {memo.recipient_count} recipient(s)."
    return f"Memo {memo.reference} sent for approval."


def _acknowledge(memo, user, d):
    memo_service.acknowledge_memo(memo, user)
    return "Acknowledged."


def _archive(memo, user, d):
    memo_service.archive_memo(memo, user)
    return f"Memo {memo.reference} archived."


def _unarchive(memo, user, d):
    memo_service.unarchive_memo(memo, user)
    return f"Memo {memo.reference} restored to your inbox."


def _cancel(memo, user, d):
    memo_service.cancel_memo(memo, user, reason=(d.get("reason") or "").strip())
    return f"Memo {memo.reference} cancelled."


ACTIONS = {"approve": _approve, "return": _return, "reject": _reject, "resubmit": _resubmit,
           "publish": _publish, "acknowledge": _acknowledge, "archive": _archive, "unarchive": _unarchive,
           "cancel": _cancel}


class _ActionView(APIView):
    action_name = None

    def post(self, request, pk):
        memo = memo_service.get_memo_for(request.user, pk)
        ser = s.MemoActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        notice = ACTIONS[self.action_name](memo, request.user, ser.validated_data)
        try:
            fresh = memo_service.get_memo_for(request.user, pk)
            data = _detail(fresh, request.user)
        except Http404:
            data = None
        return Response({"notice": notice, "memo": data})


def _action_view(name, description):
    def post(self, request, pk):  # a fresh function per view, so each gets its own schema
        return _ActionView.post(self, request, pk)

    post = extend_schema(
        operation_id=f"memos_{name}", tags=TAGS, request=s.MemoActionSerializer, description=description,
        responses={200: s.MemoActionResponseSerializer, **errors(400, 404), **AUTHED_ERRORS})(post)
    return type(f"Memo{name.title()}View", (_ActionView,), {"action_name": name, "post": post})


MemoApproveView = _action_view("approve", "Current approver (or an administrator). Optional comment.")
MemoReturnView = _action_view("return", "Current approver: send back to the sender. Optional comment.")
MemoRejectView = _action_view("reject", "Current approver: final rejection. `comment` is required.")
MemoResubmitView = _action_view("resubmit", "Sender of a returned memo; optional subject/body/comment.")
MemoPublishView = _action_view("publish", "Sender/manager of a draft: start the routing chain.")
MemoAcknowledgeView = _action_view("acknowledge", "Recipient of a published memo that requires it.")
MemoArchiveView = _action_view("archive", "Recipient: move to the archived tab.")
MemoUnarchiveView = _action_view("unarchive", "Recipient: back to the inbox.")
MemoCancelView = _action_view("cancel", "Sender/manager of a draft, awaiting or returned memo. Optional reason.")


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
class MemoAttachmentDownloadView(APIView):
    @extend_schema(operation_id="memos_attachment_download", tags=TAGS,
                   responses={**FILE, **errors(404), **AUTHED_ERRORS})
    def get(self, request, pk):
        from messaging.memos_views import memo_attachment_response

        attachment = MemoAttachment.objects.select_related("memo").filter(pk=pk).first()
        if (attachment is None or not memo_service.can_view_memo(attachment.memo, request.user)
                or attachment.is_deleted or attachment.expires_at <= timezone.now() or not attachment.file):
            raise Http404
        return memo_attachment_response(request._request, attachment)


class _DocumentView(APIView):
    kind = "pdf"

    def get(self, request, pk):
        from messaging import memo_documents

        memo = memo_service.get_memo_for(request.user, pk)
        if not memo_service.can_download_document(memo, request.user):
            raise PermissionDenied("This memo isn't published yet. Downloads unlock once the approval chain "
                                   "completes.")
        try:
            if self.kind == "pdf":
                data = memo_documents.render_memo_pdf(memo, request=request._request)
                resp = HttpResponse(data, content_type="application/pdf")
            else:
                data = memo_documents.render_memo_docx(memo, request=request._request)
                resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument."
                                                       "wordprocessingml.document")
        except Exception:
            logger.exception("Memo %s generation failed for %s", self.kind, memo.reference)
            raise DocumentFailed()
        resp["Content-Disposition"] = f'attachment; filename="{memo.reference}.{self.kind}"'
        resp["Cache-Control"] = "private, no-store"
        return resp


class DocumentFailed(ApiError):
    status_code, code = 503, "document_unavailable"

    def __init__(self):
        super().__init__("Could not generate the document. Try again later.")


class MemoPdfView(_DocumentView):
    kind = "pdf"

    @extend_schema(operation_id="memos_pdf", tags=TAGS,
                   responses={**FILE, **errors(404), **AUTHED_ERRORS,
                              503: OpenApiResponse(description="document_unavailable")})
    def get(self, request, pk):
        return super().get(request, pk)


class MemoDocxView(_DocumentView):
    kind = "docx"

    @extend_schema(operation_id="memos_docx", tags=TAGS,
                   responses={**FILE, **errors(404), **AUTHED_ERRORS,
                              503: OpenApiResponse(description="document_unavailable")})
    def get(self, request, pk):
        return super().get(request, pk)
