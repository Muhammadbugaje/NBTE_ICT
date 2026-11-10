"""Portal door: institution profile and users, documents, the onboarding
application (SOP-01), the timeline and protected file downloads.

Mirrors institutions.portal_views, accreditation.portal_views and
qa_core.portal_views.portal_file: same scoping, same helpers, same rules.
"""
from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import serializers_portal as sp
from ..services import portal as svc
from .common import AUTHED_ERRORS, errors
from .portal_common import Forbidden, binary, body, form_error, page, paged, query, website_rules

ERR_404 = {**errors(404), **AUTHED_ERRORS}
ERR_400_404 = {**errors(400, 404), **AUTHED_ERRORS}


# ===========================================================================
# Institution
# ===========================================================================
class InstitutionView(APIView):
    @extend_schema(operation_id="portal_institution_retrieve", tags=["portal: institution"],
                   responses={200: sp.InstitutionProfileSerializer, **ERR_404})
    def get(self, request):
        inst = svc.full_institution(request)
        return Response(sp.InstitutionProfileSerializer(svc.institution_payload(request, inst)).data)

    @extend_schema(operation_id="portal_institution_update", tags=["portal: institution"],
                   request=sp.InstitutionUpdateSerializer,
                   responses={200: sp.InstitutionProfileSerializer, **ERR_400_404})
    def patch(self, request):
        """Only while the institution is proposed and its application is a
        draft (the website's rule); after that NBTE changes the record."""
        from institutions import portal_views as pv

        inst = svc.full_institution(request)
        if not pv._profile_editable(request, inst):
            raise Forbidden("The profile is locked after application submission. The Chief Executive must submit an "
                            "NBTE change request.")
        sent = body(sp.InstitutionUpdateSerializer, request)
        data = {k: ("" if v is None else v) for k, v in pv._profile_initial(inst).items()}
        data.update({k: ("" if v is None else v) for k, v in sent.items()})
        form = pv.PortalProfileForm(data)
        if not form.is_valid():
            form_error(form)
        with website_rules():
            pv._save_profile(inst, form.cleaned_data, request.user)
        inst = svc.full_institution(request)
        return Response(sp.InstitutionProfileSerializer(svc.institution_payload(request, inst)).data)


class InstitutionOptionsView(APIView):
    @extend_schema(operation_id="portal_institution_options", tags=["portal: institution"],
                   parameters=[sp.InstitutionOptionsQuerySerializer],
                   responses={200: sp.InstitutionOptionsSerializer, **ERR_400_404})
    def get(self, request):
        """Choices for the profile form (ownership, type, category, state, LGAs of ?state=)."""
        svc.institution_or_404(request.user)
        params = query(sp.InstitutionOptionsQuerySerializer, request)
        return Response(sp.InstitutionOptionsSerializer(svc.profile_options(params.get("state"))).data)


class InstitutionUsersView(APIView):
    @extend_schema(operation_id="portal_institution_users", tags=["portal: institution"],
                   responses={200: sp.MembersSerializer, **ERR_404})
    def get(self, request):
        inst = svc.full_institution(request)
        return Response(sp.MembersSerializer(svc.members_payload(request, inst)).data)

    @extend_schema(operation_id="portal_institution_users_invite", tags=["portal: institution"],
                   request=sp.InviteUserSerializer, responses={201: sp.MembersSerializer, **ERR_400_404})
    def post(self, request):
        """Invite a user (institution administrators / the primary contact only)."""
        from institutions import portal_views as pv
        from institutions import services

        inst = svc.full_institution(request)
        if not pv._can_manage_users(request):
            raise Forbidden("Only your institution administrator can invite users.")
        if services.institution_team_locked(inst):
            raise Forbidden("Institution users are locked after application submission. Submit a change request.")
        sent = body(sp.InviteUserSerializer, request)
        form = pv.InviteUserForm({k: ("" if v is None else v) for k, v in sent.items()})
        if not form.is_valid():
            form_error(form)
        with website_rules():
            services.invite_user(inst, form.cleaned_data, actor=request.user, request=request._request)
        return Response(sp.MembersSerializer(svc.members_payload(request, inst)).data, status=status.HTTP_201_CREATED)


# ===========================================================================
# Documents
# ===========================================================================
class DocumentsView(APIView):
    @extend_schema(operation_id="portal_documents_list", tags=["portal: documents"],
                   parameters=[sp.DocumentFilterSerializer],
                   responses={200: paged(sp.DocumentRowSerializer), **ERR_400_404})
    def get(self, request):
        """One row per required document (requirement) with its latest upload."""
        from institutions import services

        inst = svc.full_institution(request)
        params = query(sp.DocumentFilterSerializer, request)
        rows = svc.filter_document_rows(services.checklist(inst), params)
        return page(self, request, rows, lambda r: svc.document_row(request, inst, r), sp.DocumentRowSerializer)


class DocumentsSummaryView(APIView):
    @extend_schema(operation_id="portal_documents_summary", tags=["portal: documents"],
                   responses={200: sp.DocumentSummarySerializer, **ERR_404})
    def get(self, request):
        from institutions import services

        inst = svc.full_institution(request)
        return Response(sp.DocumentSummarySerializer(svc.summary_payload(services.checklist(inst))).data)


class DocumentHistoryView(APIView):
    @extend_schema(operation_id="portal_documents_history", tags=["portal: documents"],
                   responses={200: paged(sp.DocumentVersionSerializer), **ERR_404})
    def get(self, request):
        """Every uploaded version (the documents page's history table)."""
        from institutions.models import InstitutionDocument

        inst = svc.full_institution(request)
        qs = (InstitutionDocument.objects.for_user(request.user).filter(institution=inst)
              .select_related("kind", "uploaded_by").order_by("kind__name", "-version"))

        def row(d):
            out = svc.document_dict(d)
            return {"id": d.pk, "kind": d.kind.name, "version": d.version, "status": d.status,
                    "uploaded_at": d.created_at, "uploaded_by": svc.user_name(d.uploaded_by),
                    "review_remark": d.review_remark or "", "file": out["file"]}

        return page(self, request, qs, row, sp.DocumentVersionSerializer)


class DocumentUploadView(APIView):
    @extend_schema(operation_id="portal_documents_upload", tags=["portal: documents"],
                   request={"multipart/form-data": sp.FileUploadSerializer},
                   responses={201: sp.DocumentRowSerializer, **ERR_400_404})
    def post(self, request, kind_id):
        """Upload a (new version of a) required document — the website's
        validators (extensions and size of the document kind)."""
        from institutions import portal_views as pv
        from institutions import services
        from institutions.models import DocumentKind

        inst = svc.full_institution(request)
        kind = DocumentKind.objects.filter(pk=kind_id, is_active=True).first()
        rows = {r["kind"].pk: r for r in services.checklist(inst)}
        if kind is None or kind.pk not in rows:
            raise Http404
        if not pv._uploads_allowed(request, inst, rows[kind.pk]):
            raise Forbidden("Documents are locked while NBTE reviews your application.")
        data = body(sp.FileUploadSerializer, request)
        app = pv._open_application(request, inst)
        with website_rules():
            services.upload_document(inst, kind, data["file"], user=request.user, application=app)
        fresh = {r["kind"].pk: r for r in services.checklist(inst)}[kind.pk]
        return Response(sp.DocumentRowSerializer(svc.document_row(request, inst, fresh)).data,
                        status=status.HTTP_201_CREATED)


class DocumentDownloadView(APIView):
    @extend_schema(operation_id="portal_documents_download", tags=["portal: documents"],
                   responses={200: binary(), **ERR_404})
    def get(self, request, pk):
        from institutions.models import InstitutionDocument

        svc.institution_or_404(request.user)
        doc = InstitutionDocument.objects.for_user(request.user).filter(pk=pk).first()
        return serve(request, doc)


# ===========================================================================
# Protected files (any portal file the website lets this user download)
# ===========================================================================
def serve(request, obj):
    from qa_core.portal_views import portal_file_response

    if obj is None or not obj.file or not obj.can_download(request.user):
        raise Http404
    return portal_file_response(request, obj, channel="mobile app")


class PortalFileView(APIView):
    @extend_schema(operation_id="portal_file_download", tags=["portal: files"], responses={200: binary(), **ERR_404})
    def get(self, request, label, pk):
        """The portal's protected download (qa_core.portal_views.portal_file):
        ``label`` is one of institutions.institutiondocument,
        accreditation.evidence, accreditation.response, payments.paymentslip,
        payments.paymentadvice, payments.proofofpayment,
        accreditation.outcomelettercopy. Use the ``download_url`` the other
        endpoints give."""
        from django.apps import apps

        from qa_core.portal_views import PORTAL_FILE_MODELS

        if label not in PORTAL_FILE_MODELS:
            raise Http404
        model = apps.get_model(label)
        return serve(request, model.objects.for_user(request.user).filter(pk=pk).first())


# ===========================================================================
# Application (SOP-01)
# ===========================================================================
def _application(request):
    inst = svc.full_institution(request)
    return Response(sp.ApplicationSerializer(svc.application_payload(request, inst)).data)


class ApplicationView(APIView):
    @extend_schema(operation_id="portal_application_retrieve", tags=["portal: application"],
                   responses={200: sp.ApplicationSerializer, **ERR_404})
    def get(self, request):
        """Stage board, what is missing, clearances, fees, programmes and whether it can be submitted."""
        return _application(request)


class ApplicationStartView(APIView):
    @extend_schema(operation_id="portal_application_start", tags=["portal: application"], request=None,
                   responses={200: sp.ApplicationSerializer, **ERR_400_404})
    def post(self, request):
        from accreditation import services

        inst = svc.full_institution(request)
        with website_rules():
            services.start_application(inst, user=request.user)
        return _application(request)


class ApplicationSubmitView(APIView):
    @extend_schema(operation_id="portal_application_submit", tags=["portal: application"],
                   request=sp.DeclarationSerializer, responses={200: sp.ApplicationSerializer, **ERR_400_404})
    def post(self, request):
        from accreditation import portal_views as apv
        from accreditation import services

        inst = svc.full_institution(request)
        app = apv._app(request, inst)
        if app is None:
            raise Http404
        data = body(sp.DeclarationSerializer, request)
        with website_rules():
            services.submit(app, user=request.user, declaration=bool(data.get("declaration")))
        return _application(request)


class ApplicationProgrammesSubmitView(APIView):
    @extend_schema(operation_id="portal_application_programmes_submit", tags=["portal: application"],
                   request=None, responses={200: sp.ApplicationSerializer, **ERR_400_404})
    def post(self, request):
        """Q1: the Chief Executive submits the programme list to NBTE for clearance (X12: CE only)."""
        from accreditation import onboarding_programmes as op
        from accreditation import portal_views as apv

        inst = svc.full_institution(request)
        app = apv._app(request, inst)
        if app is None:
            raise Http404
        with website_rules():
            op.submit_programmes(app, user=request.user,
                                 impersonating=bool(getattr(request, "is_impersonating", False)))
        return _application(request)


def _selecting(request, inst):
    from accreditation import portal_views as apv

    app = apv._app(request, inst)
    if app is None or app.status != "programme_selection" or getattr(request, "is_impersonating", False):
        raise Forbidden("Programme selection is not open for your application.")
    return app


class ApplicationProgrammesView(APIView):
    @extend_schema(operation_id="portal_application_programmes_add", tags=["portal: application"],
                   request=sp.ProgrammeAddSerializer, responses={201: sp.ApplicationSerializer, **ERR_400_404})
    def post(self, request):
        """Add a proposed programme while the application is at programme selection."""
        from accreditation import portal_views as apv
        from institutions import services as inst_services
        from institutions.models import Programme

        inst = svc.full_institution(request)
        app = _selecting(request, inst)
        sent = body(sp.ProgrammeAddSerializer, request)
        form = apv.ProgrammeForm({k: ("" if v is None else v) for k, v in sent.items()})
        if not form.is_valid():
            form_error(form)
        d = form.cleaned_data
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework import serializers

        from institutions.programme_master import add_programme

        try:
            with website_rules():
                add_programme(inst, user=request.user, master=d.get("master"), typed_name=d.get("name") or "",
                              level=d.get("level"), category=d.get("category"), body=d.get("body"),
                              application=app, streams=d["streams"])
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"name": exc.messages})
        response = _application(request)
        response.status_code = status.HTTP_201_CREATED
        return response


class ApplicationProgrammeView(APIView):
    def _programme(self, request, pk):
        from institutions.models import Programme

        inst = svc.full_institution(request)
        programme = Programme.objects.for_user(request.user).filter(pk=pk, institution=inst).first()
        if programme is None:
            raise Http404
        _selecting(request, inst)
        if programme.status != "proposed":
            raise Forbidden("Only a proposed programme can be changed here.")
        from accreditation import onboarding_programmes as op
        from accreditation import portal_views as apv

        if op.is_locked(apv._app(request, inst), programme):
            raise Forbidden("NBTE has cleared this programme; it can no longer be changed.")
        return programme

    @extend_schema(operation_id="portal_application_programme_streams", tags=["portal: application"],
                   request=sp.StreamsSerializer, responses={200: sp.ApplicationSerializer, **ERR_400_404})
    def patch(self, request, pk):
        from django.core.exceptions import ValidationError
        from rest_framework import serializers

        from accreditation.qa_services import clean_streams
        from institutions import services as inst_services

        programme = self._programme(request, pk)
        data = body(sp.StreamsSerializer, request)
        try:
            n = clean_streams(data["streams"])
        except ValidationError as exc:
            raise serializers.ValidationError({"streams": exc.messages}) from None
        inst_services.set_programme_streams(programme, n, user=request.user)
        return _application(request)

    @extend_schema(operation_id="portal_application_programme_remove", tags=["portal: application"],
                   responses={200: sp.ApplicationSerializer, **ERR_404})
    def delete(self, request, pk):
        from institutions import services as inst_services

        programme = self._programme(request, pk)
        inst_services.remove_proposed_programme(programme, user=request.user)
        return _application(request)


# ===========================================================================
# Timeline
# ===========================================================================
class TimelineView(APIView):
    @extend_schema(operation_id="portal_timeline", tags=["portal: institution"],
                   parameters=[sp.TimelineFilterSerializer],
                   responses={200: paged(sp.TimelineRowSerializer), **ERR_400_404})
    def get(self, request):
        """The institution's public timeline, newest first."""
        svc.institution_or_404(request.user)
        params = query(sp.TimelineFilterSerializer, request)
        return page(self, request, svc.timeline_queryset(request.user, params), svc.timeline_row,
                    sp.TimelineRowSerializer)
