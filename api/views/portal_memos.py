"""Portal door: the institution's Memos inbox (v2.1 Phase E, M4).

Mirrors messaging.portal_views through the same services
(messaging.memos.institution_memos_for / get_institution_receipt /
open_institution_memo / acknowledge_institution_memo): another
institution's memo answers 404, opening marks it read, the PDF is the
signed letter addressed to the institution."""
from django.http import HttpResponse
from django.urls import reverse
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from messaging import memos as svc

from ..services import portal as base
from .common import AUTHED_ERRORS, errors
from .portal_common import binary, page, paged

TAGS = ["portal: memos"]
ERR_404 = {**errors(404), **AUTHED_ERRORS}


class PortalMemoRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    reference = serializers.CharField()
    subject = serializers.CharField()
    kind = serializers.CharField()
    priority = serializers.CharField()
    signed_as = serializers.CharField(help_text="from | approved (the Executive Secretary)")
    published_at = serializers.DateTimeField()
    requires_acknowledgment = serializers.BooleanField()
    read_at = serializers.DateTimeField(allow_null=True)
    acknowledged_at = serializers.DateTimeField(allow_null=True)


class PortalMemoDetailSerializer(PortalMemoRowSerializer):
    body = serializers.CharField()
    letter_reference = serializers.CharField()
    signatory = serializers.CharField()
    pdf_path = serializers.CharField()


class PortalMemoQuerySerializer(serializers.Serializer):
    show = serializers.ChoiceField(choices=["", "unread", "to_ack"], required=False, default="")


def _row(r):
    m = r.memo
    return {"id": m.pk, "reference": m.reference, "subject": m.subject, "kind": m.kind, "priority": m.priority,
            "signed_as": m.signed_as, "published_at": m.published_at, "requires_acknowledgment":
            m.requires_acknowledgment, "read_at": r.read_at, "acknowledged_at": r.acknowledged_at}


def _detail(r):
    from messaging.memo_documents import external_reference

    m = r.memo
    es = m.es_signatory
    return {**_row(r), "body": m.body, "letter_reference": external_reference(m),
            "signatory": (es.get_full_name() or es.email) if es else "",
            "pdf_path": reverse("api:portal_memo_pdf", args=[m.pk])}


class PortalMemosView(APIView):
    @extend_schema(operation_id="portal_memos_list", tags=TAGS, parameters=[PortalMemoQuerySerializer],
                   responses={200: paged(PortalMemoRowSerializer), **ERR_404})
    def get(self, request):
        inst = base.institution_or_404(request.user)
        rows = svc.institution_memos_for(inst)
        show = request.query_params.get("show") or ""
        if show == "unread":
            rows = rows.filter(read_at__isnull=True)
        elif show == "to_ack":
            rows = rows.filter(memo__requires_acknowledgment=True, acknowledged_at__isnull=True)
        return page(self, request, rows, _row, PortalMemoRowSerializer)


class PortalMemoDetailView(APIView):
    @extend_schema(operation_id="portal_memos_detail", tags=TAGS, description="Opening marks it read.",
                   responses={200: PortalMemoDetailSerializer, **ERR_404})
    def get(self, request, pk):
        inst = base.institution_or_404(request.user)
        r = svc.get_institution_receipt(inst, pk)
        svc.open_institution_memo(r, request.user, impersonating=getattr(request, "is_impersonating", False))
        return Response(PortalMemoDetailSerializer(_detail(r)).data)


class PortalMemoAcknowledgeView(APIView):
    @extend_schema(operation_id="portal_memos_acknowledge", tags=TAGS, request=None,
                   responses={200: PortalMemoDetailSerializer, **ERR_404})
    def post(self, request, pk):
        inst = base.institution_or_404(request.user)
        r = svc.get_institution_receipt(inst, pk)
        svc.acknowledge_institution_memo(r, request.user)
        return Response(PortalMemoDetailSerializer(_detail(r)).data)


class PortalMemoPdfView(APIView):
    @extend_schema(operation_id="portal_memos_pdf", tags=TAGS, responses={200: binary(), **ERR_404})
    def get(self, request, pk):
        from messaging.memo_documents import render_memo_pdf

        inst = base.institution_or_404(request.user)
        r = svc.get_institution_receipt(inst, pk)
        resp = HttpResponse(render_memo_pdf(r.memo, institution=inst), content_type="application/pdf")
        kind = "inline" if request.query_params.get("inline") else "attachment"
        resp["Content-Disposition"] = f'{kind}; filename="{r.memo.reference}.pdf"'
        resp["Cache-Control"] = "private, no-store"
        return resp
