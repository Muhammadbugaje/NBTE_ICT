"""requests/ (staff door) - workflow requests. See api/services/requests.py."""
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import serializers_work as s
from ..pagination import StandardPagination
from ..services import requests as svc
from ..services.work import download, form_errors, paginate, run
from .common import AUTHED_ERRORS, errors

LIST_ERRORS = {**errors(400), **AUTHED_ERRORS}
OBJ_ERRORS = {**errors(400, 404), **AUTHED_ERRORS}


def _req(request, pk):
    req = svc.get_visible(request.user, pk)
    if req is None:
        raise Http404
    return req


def _detail(request, req, code=200):
    fresh = svc.get_visible(request.user, req.pk)
    if fresh is None:  # e.g. a step moved on and the user no longer sees it
        fresh = req
    return Response(s.RequestDetailSerializer(svc.detail(fresh, request.user, request)).data, status=code)


class RequestTypesView(APIView):
    """Request types a staff member can start (every active type, as on the
    website's "New request" picker)."""

    pagination_class = StandardPagination

    @extend_schema(operation_id="requests_types", responses={200: s.RequestTypeSerializer(many=True),
                                                            **AUTHED_ERRORS})
    def get(self, request):
        return paginate(self, request, list(svc.active_types()), s.RequestTypeSerializer,
                        lambda t: svc.type_item(t, request.user, with_options=False))


class RequestTypeDetailView(APIView):
    @extend_schema(operation_id="requests_type_retrieve", responses={200: s.RequestTypeSerializer, **OBJ_ERRORS})
    def get(self, request, code):
        """One type with its form fields and the picker options for this user
        (asset_picker: the assets you may see; user_picker: your department)."""
        t = get_object_or_404(svc.active_types(), code=code)
        return Response(s.RequestTypeSerializer(svc.type_item(t, request.user, with_options=True)).data)


class RequestListView(APIView):
    """mine (raised by me), awaiting (waiting for my action), assigned
    (assigned to me)."""

    pagination_class = StandardPagination

    @extend_schema(operation_id="requests_list", parameters=[s.RequestListQuerySerializer],
                   responses={200: s.RequestListItemSerializer(many=True), **LIST_ERRORS})
    def get(self, request):
        q = s.RequestListQuerySerializer(data=request.query_params)
        q.is_valid(raise_exception=True)
        return paginate(self, request, svc.list_items(request.user, q.validated_data),
                        s.RequestListItemSerializer, svc.list_item)

    @extend_schema(operation_id="requests_create", request=s.RequestCreateSerializer,
                   responses={201: s.RequestDetailSerializer, **LIST_ERRORS})
    def post(self, request):
        """Raise a request: the type's dynamic form (common fields + schema
        fields) then workflow.services.raise_request_checked (annual leave is
        validated and synced exactly as on the website). Attach files after
        with requests/<id>/attachments/."""
        body = s.RequestCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        t = svc.active_types().filter(code=body.validated_data["request_type"]).first()
        if t is None:
            raise serializers.ValidationError({"request_type": ["This request type is not available."]})
        req = svc.create(request.user, t, body.validated_data)
        return _detail(request, req, 201)


class RequestDetailView(APIView):
    @extend_schema(operation_id="requests_retrieve", responses={200: s.RequestDetailSerializer, **OBJ_ERRORS})
    def get(self, request, pk):
        return _detail(request, _req(request, pk))


def _action_view(name, body_serializer, form_class, call, doc):
    class _View(APIView):
        __doc__ = doc

        @extend_schema(operation_id=f"requests_{name.replace('-', '_')}", request=body_serializer,
                       responses={200: s.RequestDetailSerializer, **OBJ_ERRORS})
        def post(self, request, pk):
            req = _req(request, pk)
            ser = body_serializer(data=request.data)
            ser.is_valid(raise_exception=True)
            form = form_class({k: ("" if v is None else str(v)) for k, v in ser.validated_data.items()})
            if not form.is_valid():
                form_errors(form)
            run(call, req, request.user, form.cleaned_data)
            return _detail(request, req)

    _View.__name__ = "Request" + "".join(p.title() for p in name.split("-")) + "View"
    return _View


def _build_actions():
    from workflow import forms as f
    from workflow import services as ws

    def comment(d):
        return d.get("comment") or ""

    return {
        "approve": _action_view("approve", s.CommentOnlySerializer, f.ApproveForm,
                                lambda r, u, d: ws.approve_step(r, u, comment=comment(d)),
                                "Approve the current step (you must be its acting user)."),
        "return": _action_view("return", s.RequiredCommentSerializer, f.ReturnForm,
                               lambda r, u, d: ws.return_step(r, u, comment=comment(d)),
                               "Return to the previous step (comment required)."),
        "return-to-requester": _action_view(
            "return-to-requester", s.RequiredCommentSerializer, f.ReturnForm,
            lambda r, u, d: ws.return_to_requester(r, u, comment=comment(d)),
            "Return to the requester for revision (comment required)."),
        "reject": _action_view("reject", s.RequiredCommentSerializer, f.RejectForm,
                               lambda r, u, d: ws.reject_step(r, u, comment=comment(d)),
                               "Reject the request (comment required)."),
        "fulfilled": _action_view("fulfilled", s.CommentOnlySerializer, f.FulfilledForm,
                                  lambda r, u, d: ws.mark_fulfilled(r, u, comment=comment(d)),
                                  "Assigned staff: mark the work done."),
        "close": _action_view("close", s.CommentOnlySerializer, f.CloseForm,
                              lambda r, u, d: ws.confirm_handover(r, u, comment=comment(d)),
                              "Requester: confirm handover (closes the request)."),
        "cancel": _action_view("cancel", s.CommentOnlySerializer, f.CancelForm,
                               lambda r, u, d: ws.cancel_request(r, u, comment=comment(d)),
                               "Requester: cancel the request."),
    }


ACTIONS = _build_actions()


class RequestAssignView(APIView):
    @extend_schema(operation_id="requests_assign", request=s.RequestAssignSerializer,
                   responses={200: s.RequestDetailSerializer, **OBJ_ERRORS})
    def post(self, request, pk):
        """Assignment step: pick a staff member (receiving department's
        active users, as the website's AssignForm)."""
        from workflow import services as ws
        from workflow.forms import AssignForm

        req = _req(request, pk)
        body = s.RequestAssignSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        form = AssignForm({"assignee": str(body.validated_data["assignee"]),
                           "comment": body.validated_data["comment"]},
                          handling_department=req.receiving_department)
        if not form.is_valid():
            form_errors(form)
        run(ws.assign_staff, req, request.user, staff_user=form.cleaned_data["assignee"],
            comment=form.cleaned_data.get("comment") or "")
        return _detail(request, req)


class RequestResubmitView(APIView):
    @extend_schema(operation_id="requests_resubmit", request=s.RequestResubmitSerializer,
                   responses={200: s.RequestDetailSerializer, **OBJ_ERRORS})
    def post(self, request, pk):
        """Requester revises a returned request (same dynamic form)."""
        from workflow.models import Request

        from ..services.work import Forbidden

        req = _req(request, pk)
        if req.requester_id != request.user.pk:
            raise Forbidden("Only the requester can resubmit.")
        if req.status != Request.RETURNED:
            raise serializers.ValidationError(
                {"non_field_errors": ["This request is not currently returned for revision."]})
        body = s.RequestResubmitSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        svc.resubmit(req, request.user, body.validated_data)
        return _detail(request, req)


class RequestCommentsView(APIView):
    @extend_schema(operation_id="requests_comment", request=s.CommentRequestSerializer,
                   responses={201: s.RequestDetailSerializer, **OBJ_ERRORS})
    def post(self, request, pk):
        """Comment; ``is_internal`` notes are hidden from the requester (a
        requester's note is always public, as on the website)."""
        from workflow import services as ws
        from workflow.forms import CommentForm

        req = _req(request, pk)
        if req.is_closed:
            raise serializers.ValidationError(
                {"non_field_errors": ["This request is closed. No further comments can be added."]})
        body = s.CommentRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        form = CommentForm({"body": body.validated_data["body"].strip(),
                            "is_internal": "on" if body.validated_data["is_internal"] else ""})
        if not form.is_valid():
            form_errors(form)
        run(ws.add_comment, req, request.user, body=form.cleaned_data["body"],
            internal=form.cleaned_data.get("is_internal", False))
        return _detail(request, req, status.HTTP_201_CREATED)


class RequestAttachmentsView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(operation_id="requests_attach", request={"multipart/form-data": s.AttachmentUploadSerializer},
                   responses={201: s.RequestDetailSerializer, **OBJ_ERRORS})
    def post(self, request, pk):
        from workflow import services as ws
        from workflow.forms import AttachmentForm

        req = _req(request, pk)
        if req.is_closed:
            raise serializers.ValidationError(
                {"non_field_errors": ["This request is closed. No further files can be attached."]})
        form = AttachmentForm(request.POST, request.FILES)
        if not form.is_valid():
            form_errors(form)
        run(ws.add_attachment, req, request.user, file=form.cleaned_data["file"],
            description=form.cleaned_data.get("description") or "")
        return _detail(request, req, status.HTTP_201_CREATED)


class RequestAttachmentView(APIView):
    @extend_schema(operation_id="requests_attachment_download",
                   responses={(200, "*/*"): OpenApiResponse(OpenApiTypes.BINARY, description="The file."),
                              **OBJ_ERRORS})
    def get(self, request, pk, attachment_id):
        from workflow.models import RequestAttachment

        req = _req(request, pk)
        return download(request, get_object_or_404(RequestAttachment, pk=attachment_id, request=req))


class RequestRecordPdfView(APIView):
    @extend_schema(operation_id="requests_record_pdf",
                   responses={(200, "application/pdf"): OpenApiResponse(OpenApiTypes.BINARY), **OBJ_ERRORS})
    def get(self, request, pk):
        """The generated record PDF of a closed request (workflow.views.record_pdf)."""
        from django.http import FileResponse

        req = _req(request, pk)
        if not req.record_pdf:
            raise Http404
        return FileResponse(req.record_pdf.open("rb"), content_type="application/pdf", as_attachment=False,
                            filename=req.record_original_name or f"{req.reference}.pdf")

