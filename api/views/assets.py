"""assets/ (staff door). See api/services/assets_staff.py."""
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import serializers_assets_staff as s
from ..pagination import StandardPagination
from ..services import assets_staff as svc
from ..services.work import Forbidden, download, form_errors, paginate
from .common import AUTHED_ERRORS, errors

OBJ_ERRORS = {**errors(400, 404), **AUTHED_ERRORS}


def _asset(request, tag):
    return get_object_or_404(svc.visible_assets(request.user), asset_tag__iexact=tag)


class MyAssetsView(APIView):
    pagination_class = StandardPagination

    @extend_schema(operation_id="assets_mine", parameters=[s.MyAssetsQuerySerializer],
                   responses={200: s.MyAssetSerializer(many=True), **errors(400), **AUTHED_ERRORS})
    def get(self, request):
        """Assets in my custody now (status=current) or handed back."""
        q = s.MyAssetsQuerySerializer(data=request.query_params)
        q.is_valid(raise_exception=True)
        qs = svc.my_assignments(request.user, q.validated_data["status"], (q.validated_data.get("q") or "").strip())
        return paginate(self, request, qs, s.MyAssetSerializer, svc.assignment_item)


class AssetByCodeView(APIView):
    @extend_schema(operation_id="assets_by_code", responses={200: s.AssetDetailSerializer, **OBJ_ERRORS})
    def get(self, request, code):
        """Scanned QR content (the asset URL) or a typed tag. 404 when there
        is no such asset among those I may see."""
        asset = svc.by_code(request.user, code)
        if asset is None:
            raise Http404
        return Response(s.AssetDetailSerializer(svc.asset_detail(asset, request.user, request)).data)


class AssetDetailView(APIView):
    @extend_schema(operation_id="assets_retrieve", responses={200: s.AssetDetailSerializer, **OBJ_ERRORS})
    def get(self, request, tag):
        asset = _asset(request, tag)
        return Response(s.AssetDetailSerializer(svc.asset_detail(asset, request.user, request)).data)


class AssetIssueView(APIView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(operation_id="assets_report_issue",
                   request={"application/json": s.AssetIssueRequestSerializer,
                            "multipart/form-data": s.AssetIssueRequestSerializer},
                   responses={201: s.AssetDetailSerializer, **OBJ_ERRORS})
    def post(self, request, tag):
        """Report an issue (anyone who can see the asset; an in-service asset
        becomes "damaged"). ``photo`` is stored as an asset photo, which the
        website allows to users with edit rights only."""
        from assets import services
        from assets.access import can_manage_asset
        from assets.forms import AssetAttachmentForm, AssetIssueForm

        asset = _asset(request, tag)
        body = s.AssetIssueRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        photo = body.validated_data.get("photo")
        if photo is not None and not can_manage_asset(request.user, asset):
            raise Forbidden("You cannot add photos to this asset. Raise a ticket for it with the photo instead.")
        form = AssetIssueForm({"summary": body.validated_data["summary"],
                               "description": body.validated_data.get("description") or ""})
        if not form.is_valid():
            form_errors(form)
        photo_form = None
        if photo is not None:
            photo_form = AssetAttachmentForm({"kind": "photo", "description": form.cleaned_data["summary"][:255]},
                                             {"file": photo})
            if not photo_form.is_valid():
                form_errors(photo_form, rename={"file": "photo"})
        services.report_issue(asset=asset, reported_by=request.user, summary=form.cleaned_data["summary"],
                              description=form.cleaned_data.get("description") or "")
        if photo_form is not None:
            from assets.models import AssetAttachment

            AssetAttachment(asset=asset, uploaded_by=request.user, file=photo_form.cleaned_data["file"],
                            kind="photo", description=photo_form.cleaned_data.get("description") or "").save()
        asset.refresh_from_db()
        return Response(s.AssetDetailSerializer(svc.asset_detail(asset, request.user, request)).data, status=201)


class AssetAttachmentView(APIView):
    @extend_schema(operation_id="assets_attachment_download",
                   responses={(200, "*/*"): OpenApiResponse(OpenApiTypes.BINARY, description="The file."),
                              **OBJ_ERRORS})
    def get(self, request, tag, attachment_id):
        from assets.models import AssetAttachment

        asset = _asset(request, tag)
        return download(request, get_object_or_404(AssetAttachment, pk=attachment_id, asset=asset))
