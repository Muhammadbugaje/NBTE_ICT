"""Portal door: resource persons (engagements and findings, service history,
profile & CV) and the institution's read-only inspections list
(inspection.portal_views)."""
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import serializers_portal as sp
from ..services import portal as base
from ..services import portal_rp as svc
from .common import AUTHED_ERRORS, errors
from .portal_common import body, page, paged, query, website_rules

from rest_framework import serializers as drf


class _ResponseFields(drf.Serializer):
    """v2.9: the ES-led engagement fields (scope, the reply, the report's recommendation)."""
    scope = drf.CharField()
    scope_label = drf.CharField()
    rp_response = drf.CharField(allow_blank=True)
    rp_response_label = drf.CharField()
    can_respond = drf.BooleanField()
    rp_recommendation = drf.CharField(allow_blank=True)


class EngagementRow(_ResponseFields, sp.EngagementRowSerializer):
    pass


class EngagementDetail(_ResponseFields, sp.EngagementDetailSerializer):
    pass


class RespondSerializer(drf.Serializer):
    accept = drf.BooleanField()
    note = drf.CharField(required=False, allow_blank=True, max_length=255)


class FindingsWithRecommendation(sp.FindingsSerializer):
    recommendation = drf.CharField(required=False, allow_blank=True)


ERR_404 = {**errors(404), **AUTHED_ERRORS}
ERR_400_404 = {**errors(400, 404), **AUTHED_ERRORS}
TAG = ["portal: resource persons"]


class EngagementsView(APIView):
    @extend_schema(operation_id="portal_engagements_list", tags=TAG, parameters=[sp.EngagementFilterSerializer],
                   responses={200: paged(EngagementRow), **ERR_400_404})
    def get(self, request):
        """Visits and SSQ reviews the resource person is mapped to."""
        rp = base.rp_or_404(request.user)
        params = query(sp.EngagementFilterSerializer, request)
        return page(self, request, svc.engagements_queryset(rp, params), svc.engagement_row, EngagementRow)


class EngagementDetailView(APIView):
    @extend_schema(operation_id="portal_engagements_retrieve", tags=TAG,
                   responses={200: EngagementDetail, **ERR_404})
    def get(self, request, pk):
        rp = base.rp_or_404(request.user)
        return Response(EngagementDetail(svc.engagement_detail_payload(
            svc.engagement_or_404(rp, pk))).data)


class EngagementFindingsView(APIView):
    @extend_schema(operation_id="portal_engagements_findings", tags=TAG, request=FindingsWithRecommendation,
                   responses={200: EngagementDetail, **ERR_400_404})
    def post(self, request, pk):
        """Write or update "My findings" (visit duties, until the team submits the report)."""
        from inspection import services

        rp = base.rp_or_404(request.user)
        m = svc.engagement_or_404(rp, pk)
        data = body(FindingsWithRecommendation, request)
        with website_rules():
            services.submit_findings(m, user=request.user, text=data.get("findings", ""),
                                     recommendation=data.get("recommendation"))
        return Response(EngagementDetail(svc.engagement_detail_payload(
            svc.engagement_or_404(rp, pk))).data)


class EngagementRespondView(APIView):
    @extend_schema(operation_id="portal_engagements_respond", tags=TAG, request=RespondSerializer,
                   responses={200: EngagementDetail, **ERR_400_404})
    def post(self, request, pk):
        """v2.9: confirm availability (accept) or decline an ES-approved engagement."""
        from inspection import services

        rp = base.rp_or_404(request.user)
        m = svc.engagement_or_404(rp, pk)
        data = body(RespondSerializer, request)
        with website_rules():
            services.rp_respond(m, user=request.user, accept=data["accept"], note=data.get("note", ""))
        return Response(EngagementDetail(svc.engagement_detail_payload(
            svc.engagement_or_404(rp, pk))).data)


class ServiceHistoryView(APIView):
    @extend_schema(operation_id="portal_service_history_list", tags=TAG, parameters=[sp.HistoryFilterSerializer],
                   responses={200: paged(sp.ServiceRowSerializer), **ERR_400_404})
    def get(self, request):
        rp = base.rp_or_404(request.user)
        params = query(sp.HistoryFilterSerializer, request)
        return page(self, request, svc.history_queryset(rp, params), svc.history_row, sp.ServiceRowSerializer)


class ServiceHistorySummaryView(APIView):
    @extend_schema(operation_id="portal_service_history_summary", tags=TAG, parameters=[sp.HistoryFilterSerializer],
                   responses={200: sp.ServiceSummarySerializer, **ERR_400_404})
    def get(self, request):
        """Totals for the same filters, the years served and the duty choices."""
        rp = base.rp_or_404(request.user)
        params = query(sp.HistoryFilterSerializer, request)
        return Response(sp.ServiceSummarySerializer(svc.history_summary(rp, params)).data)


class RPProfileView(APIView):
    @extend_schema(operation_id="portal_rp_profile", tags=TAG, responses={200: sp.RPProfileSerializer, **ERR_404})
    def get(self, request):
        """The resource-person record (read-only: NBTE changes it) and the uploaded CV/documents."""
        rp = base.rp_or_404(request.user)
        return Response(sp.RPProfileSerializer(svc.profile_payload(rp)).data)


class RPProfileFilesView(APIView):
    @extend_schema(operation_id="portal_rp_profile_upload", tags=TAG,
                   request={"multipart/form-data": sp.RPFileUploadSerializer},
                   responses={201: sp.RPProfileSerializer, **ERR_400_404})
    def post(self, request):
        """Upload an updated CV or promotion letter (PDF, Word or image)."""
        from inspection import services

        rp = base.rp_or_404(request.user)
        data = body(sp.RPFileUploadSerializer, request)
        with website_rules():
            services.add_file(rp, actor=request.user, upload=data.get("file"), kind=data.get("kind", "cv"))
        rp.refresh_from_db()
        return Response(sp.RPProfileSerializer(svc.profile_payload(rp)).data, status=status.HTTP_201_CREATED)


class InspectionsView(APIView):
    @extend_schema(operation_id="portal_inspections_list", tags=["portal: institution"],
                   parameters=[sp.InspectionFilterSerializer],
                   responses={200: paged(sp.InspectionRowSerializer), **ERR_400_404})
    def get(self, request):
        """The institution's scheduled and recorded inspections (read-only)."""
        base.institution_or_404(request.user)
        params = query(sp.InspectionFilterSerializer, request)
        return page(self, request, svc.inspections_queryset(request.user, params), svc.inspection_row,
                    sp.InspectionRowSerializer)
