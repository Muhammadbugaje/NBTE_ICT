"""Portal door: QA requests (SOP-02), the SSQ, queries, the programme board,
programmes and outcome letters.

Mirrors accreditation.qa_portal_views, accreditation.programme_portal_views
and institutions.programme_views: the same lookups (another institution's ids
answer 404), the same ``accreditation.qa_services`` calls.
"""
from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import serializers_portal as sp
from ..services import portal as base
from ..services import portal_qa as svc
from .common import AUTHED_ERRORS, errors
from .portal_common import binary, body, page, paged, query, website_rules
from .portal_institution import serve

ERR_404 = {**errors(404), **AUTHED_ERRORS}
ERR_400_404 = {**errors(400, 404), **AUTHED_ERRORS}
TAG_REQ = ["portal: qa requests"]
TAG_SSQ = ["portal: ssq"]


def _ids(raw):
    """"1,2" or ["1", "2"] -> [1, 2] (digits only, like the website)."""
    out = []
    for part in raw if isinstance(raw, (list, tuple)) else [raw]:
        for bit in str(part or "").split(","):
            if bit.strip().isdigit():
                out.append(int(bit.strip()))
    return out


def _detail(request, req, code=status.HTTP_200_OK):
    req.refresh_from_db()
    return Response(sp.QARequestDetailSerializer(svc.request_detail_payload(request.user, req)).data, status=code)


# ===========================================================================
# QA requests
# ===========================================================================
class QARequestsView(APIView):
    @extend_schema(operation_id="portal_qa_requests_list", tags=TAG_REQ, parameters=[sp.QARequestFilterSerializer],
                   responses={200: paged(sp.QARequestRowSerializer), **ERR_400_404})
    def get(self, request):
        base.institution_or_404(request.user)
        params = query(sp.QARequestFilterSerializer, request)
        return page(self, request, svc.requests_queryset(request.user, params),
                    lambda r: svc.request_row(request.user, r), sp.QARequestRowSerializer)

    @extend_schema(operation_id="portal_qa_requests_create", tags=TAG_REQ, request=sp.QARequestCreateSerializer,
                   responses={201: sp.QARequestDetailSerializer, **ERR_400_404})
    def post(self, request):
        """Create a draft request (administrators): a QA type and its programmes."""
        from accreditation import qa_services as qs
        from accreditation.models import QAType
        from institutions.models import Programme

        inst = base.full_institution(request)
        svc.manage_or_404(request.user, inst)
        data = body(sp.QARequestCreateSerializer, request)
        qa_type = QAType.objects.filter(code=data["type"], is_active=True).first()
        if qa_type is None:
            raise serializers.ValidationError({"type": ["Choose a QA type."]})
        programmes = list(Programme.objects.for_user(request.user).filter(institution=inst,
                                                                          pk__in=data["programmes"]))
        ids = {p.pk for p in programmes}
        asked = {int(k): v for k, v in (data.get("streams") or {}).items()
                 if str(k).isdigit() and int(k) in ids and v not in (None, "")}
        with website_rules():
            req = qs.create_request(inst, qa_type, programmes, user=request.user, streams=asked)
        return _detail(request, req, status.HTTP_201_CREATED)


class QARequestNewView(APIView):
    @extend_schema(operation_id="portal_qa_requests_new", tags=TAG_REQ, parameters=[sp.NewRequestQuerySerializer],
                   responses={200: sp.NewRequestSerializer, **ERR_400_404})
    def get(self, request):
        """What can be requested: the QA types (and why one is unavailable)
        and, with ?type=, each programme's eligibility and default streams."""
        from accreditation.models import QAType

        inst = base.full_institution(request)
        svc.manage_or_404(request.user, inst)
        params = query(sp.NewRequestQuerySerializer, request)
        code = params.get("type") or ""
        qa_type = QAType.objects.filter(code=code, is_active=True).first() if code else None
        return Response(sp.NewRequestSerializer(svc.new_request_payload(request.user, inst, qa_type)).data)


class QARequestEstimateView(APIView):
    @extend_schema(operation_id="portal_qa_requests_estimate", tags=TAG_REQ,
                   parameters=[sp.RequestEstimateQuerySerializer],
                   responses={200: sp.RequestEstimateSerializer, **ERR_400_404})
    def get(self, request):
        """The live fee estimate for a request being prepared."""
        from accreditation.models import QAType

        inst = base.full_institution(request)
        svc.manage_or_404(request.user, inst)
        params = query(sp.RequestEstimateQuerySerializer, request)
        qa_type = QAType.objects.filter(code=params.get("type") or "", is_active=True).first()
        ids = _ids(request.query_params.getlist("programmes"))
        asked = {}
        for pair in str(params.get("streams") or "").split(","):
            pk, _sep, n = pair.partition(":")
            if pk.strip().isdigit() and n.strip():
                asked[int(pk)] = n.strip()
        return Response(sp.RequestEstimateSerializer(svc.request_estimate(request.user, inst, qa_type, ids,
                                                                          asked)).data)


class QARequestDetailView(APIView):
    @extend_schema(operation_id="portal_qa_requests_retrieve", tags=TAG_REQ,
                   responses={200: sp.QARequestDetailSerializer, **ERR_404})
    def get(self, request, pk):
        base.institution_or_404(request.user)
        return _detail(request, svc.request_or_404(request.user, pk))


class _ManageAction(APIView):
    """POST actions on a request: administrators only (else 404, like the website)."""

    def _req(self, request, pk):
        base.institution_or_404(request.user)
        req = svc.request_or_404(request.user, pk)
        svc.manage_or_404(request.user, req.institution)
        return req


class QARequestSubmitView(_ManageAction):
    @extend_schema(operation_id="portal_qa_requests_submit", tags=TAG_REQ, request=sp.DeclarationSerializer,
                   responses={200: sp.QARequestDetailSerializer, **ERR_400_404})
    def post(self, request, pk):
        from accreditation import qa_services as qs

        req = self._req(request, pk)
        data = body(sp.DeclarationSerializer, request)
        with website_rules():
            qs.submit(req, user=request.user, declaration=bool(data.get("declaration")))
        return _detail(request, req)


class QARequestCancelView(_ManageAction):
    @extend_schema(operation_id="portal_qa_requests_cancel", tags=TAG_REQ, request=sp.ReasonSerializer,
                   responses={200: sp.QARequestDetailSerializer, **ERR_400_404})
    def post(self, request, pk):
        from accreditation import qa_services as qs

        req = self._req(request, pk)
        data = body(sp.ReasonSerializer, request)
        with website_rules():
            qs.cancel(req, user=request.user, reason=data.get("reason", ""))
        return _detail(request, req)


class QARequestAddView(_ManageAction):
    @extend_schema(operation_id="portal_qa_requests_add_programme", tags=TAG_REQ,
                   request=sp.AddProgrammeSerializer, responses={200: sp.QARequestDetailSerializer, **ERR_400_404})
    def post(self, request, pk):
        from accreditation import qa_services as qs
        from institutions.models import Programme

        req = self._req(request, pk)
        data = body(sp.AddProgrammeSerializer, request)
        programme = Programme.objects.for_user(request.user).filter(pk=data["programme"],
                                                                     institution=req.institution).first()
        if programme is None:
            raise serializers.ValidationError({"programme": ["Choose a programme."]})
        with website_rules():
            qs.add_programme(req, programme, user=request.user, streams=data.get("streams") or None)
        return _detail(request, req)


class QARequestRowStreamsView(_ManageAction):
    @extend_schema(operation_id="portal_qa_requests_programme_streams", tags=TAG_REQ,
                   request=sp.StreamsSerializer, responses={200: sp.QARequestDetailSerializer, **ERR_400_404})
    def patch(self, request, pk, row):
        from accreditation import qa_services as qs

        req = self._req(request, pk)
        row = svc.row_or_404(request.user, req, row)
        data = body(sp.StreamsSerializer, request)
        with website_rules():
            qs.set_streams(req, row, data["streams"], user=request.user)
        return _detail(request, req)


class QARequestDropView(_ManageAction):
    @extend_schema(operation_id="portal_qa_requests_drop_programme", tags=TAG_REQ, request=sp.ReasonSerializer,
                   responses={200: sp.QARequestDetailSerializer, **ERR_400_404})
    def post(self, request, pk, row):
        from accreditation import qa_services as qs

        req = self._req(request, pk)
        row = svc.row_or_404(request.user, req, row)
        data = body(sp.ReasonSerializer, request)
        with website_rules():
            qs.drop_programme(req, row, user=request.user, reason=data.get("reason", ""))
        return _detail(request, req)


class QARequestReplaceView(_ManageAction):
    @extend_schema(operation_id="portal_qa_requests_replace_programme", tags=TAG_REQ,
                   request=sp.ReplaceProgrammeSerializer, responses={200: sp.QARequestDetailSerializer, **ERR_400_404})
    def post(self, request, pk, row):
        from accreditation import qa_services as qs
        from institutions.models import Programme

        req = self._req(request, pk)
        row = svc.row_or_404(request.user, req, row)
        data = body(sp.ReplaceProgrammeSerializer, request)
        new = Programme.objects.for_user(request.user).filter(pk=data["programme"],
                                                               institution=req.institution).first()
        if new is None:
            raise serializers.ValidationError({"programme": ["Choose the replacement programme."]})
        with website_rules():
            qs.replace_programme(req, row, new, user=request.user, reason=data.get("reason", ""))
        return _detail(request, req)


# ===========================================================================
# SSQ
# ===========================================================================
def _ssq(request, pk, ssq_pk):
    base.institution_or_404(request.user)
    req = svc.request_or_404(request.user, pk)
    return req, svc.ssq_or_404(request.user, req, ssq_pk)


def _ssq_response(request, req, ssq):
    ssq.refresh_from_db()
    return Response(sp.SSQSerializer(svc.ssq_payload(request.user, req, ssq)).data)


class SSQView(APIView):
    @extend_schema(operation_id="portal_ssq_retrieve", tags=TAG_SSQ, responses={200: sp.SSQSerializer, **ERR_404})
    def get(self, request, pk, ssq_pk):
        """The whole questionnaire: sections, fields, answers, evidence,
        progress and what the user may do (for offline drafting)."""
        req, ssq = _ssq(request, pk, ssq_pk)
        return _ssq_response(request, req, ssq)

    @extend_schema(operation_id="portal_ssq_save", tags=TAG_SSQ, request=sp.SSQAnswersSerializer,
                   responses={200: sp.SSQSaveResultSerializer, **ERR_400_404})
    def patch(self, request, pk, ssq_pk):
        """Partial save (the website's autosave): only the keys sent are
        cleaned and stored; good values are saved even when others fail
        (they come back in ``errors``). 403 if a key belongs to a section the
        user cannot edit now (nothing is saved then)."""
        from accreditation import qa_services as qs
        from accreditation import ssq as engine

        req, ssq = _ssq(request, pk, ssq_pk)
        data = body(sp.SSQAnswersSerializer, request)
        answers = data["answers"]
        with website_rules():
            errs = qs.save_fields(ssq, answers, user=request.user)
        ssq.refresh_from_db()
        fields = ssq.template.field_map()
        saved = [k for k in answers if k in fields and fields[k]["type"] != "file" and k not in errs]
        counts = qs.evidence_counts(ssq)
        progress = {p["section"]["key"]: {"filled": p["filled"], "total": p["total"], "complete": p["complete"]}
                    for p in engine.section_progress(ssq.template, ssq.data or {}, counts)}
        problems = qs.submission_problems(ssq) if ssq.is_current else {}
        return Response(sp.SSQSaveResultSerializer({
            "saved": saved, "errors": {k: str(v) for k, v in errs.items()}, "last_saved_at": ssq.last_saved_at,
            "progress": progress, "problems": [{"key": k, "message": str(v)} for k, v in problems.items()]}).data)


class SSQEvidenceUploadView(APIView):
    @extend_schema(operation_id="portal_ssq_evidence_upload", tags=TAG_SSQ,
                   request={"multipart/form-data": sp.FileUploadSerializer},
                   responses={201: sp.SSQSerializer, **ERR_400_404})
    def post(self, request, pk, ssq_pk, field):
        from accreditation import qa_services as qs

        req, ssq = _ssq(request, pk, ssq_pk)
        data = body(sp.FileUploadSerializer, request)
        with website_rules():
            qs.attach_upload(ssq, field, data["file"], user=request.user)
        response = _ssq_response(request, req, ssq)
        response.status_code = status.HTTP_201_CREATED
        return response


class SSQEvidenceReuseView(APIView):
    @extend_schema(operation_id="portal_ssq_evidence_reuse", tags=TAG_SSQ, request=sp.ReuseSerializer,
                   responses={201: sp.SSQSerializer, **ERR_400_404})
    def post(self, request, pk, ssq_pk, field):
        """Attach one of the institution's accepted documents instead of a new file."""
        from accreditation import qa_services as qs
        from institutions.models import InstitutionDocument

        req, ssq = _ssq(request, pk, ssq_pk)
        data = body(sp.ReuseSerializer, request)
        doc = InstitutionDocument.objects.for_user(request.user).filter(pk=data["document"]).first()
        if doc is None:
            raise serializers.ValidationError({"document": ["Choose one of your accepted documents."]})
        with website_rules():
            qs.reuse_document(ssq, field, doc, user=request.user)
        response = _ssq_response(request, req, ssq)
        response.status_code = status.HTTP_201_CREATED
        return response


class SSQEvidenceRemoveView(APIView):
    @extend_schema(operation_id="portal_ssq_evidence_remove", tags=TAG_SSQ,
                   responses={200: sp.SSQSerializer, **ERR_404})
    def delete(self, request, pk, ssq_pk, ev):
        from accreditation import qa_services as qs
        from accreditation.models import Evidence

        req, ssq = _ssq(request, pk, ssq_pk)
        evidence = Evidence.objects.for_user(request.user).filter(pk=ev, ssq=ssq).exclude(status="removed").first()
        if evidence is None:
            raise Http404
        with website_rules():
            qs.remove_evidence(evidence, user=request.user)
        return _ssq_response(request, req, ssq)


def _ssq_action(request, pk, ssq_pk, action):
    """accreditation.qa_portal_views._ws_action: run it; a new version (revise)
    answers with that version."""
    from accreditation.models import SSQ

    req, ssq = _ssq(request, pk, ssq_pk)
    with website_rules():
        result = action(ssq)
    target = result if isinstance(result, SSQ) else ssq
    return _ssq_response(request, req, target)


class SSQSubmitView(APIView):
    @extend_schema(operation_id="portal_ssq_submit", tags=TAG_SSQ, request=sp.DeclarationSerializer,
                   responses={200: sp.SSQSerializer, **ERR_400_404})
    def post(self, request, pk, ssq_pk):
        """Submit to NBTE (chief executive / administrator; declaration required)."""
        from accreditation import qa_services as qs

        data = body(sp.DeclarationSerializer, request)
        return _ssq_action(request, pk, ssq_pk, lambda s: qs.submit_ssq(
            s, user=request.user, declaration=bool(data.get("declaration"))))


class SSQReviseView(APIView):
    @extend_schema(operation_id="portal_ssq_revise", tags=TAG_SSQ, request=None,
                   responses={200: sp.SSQSerializer, **ERR_400_404})
    def post(self, request, pk, ssq_pk):
        """Start a new version of a returned SSQ (answers and files carried over); answers with the new version."""
        from accreditation import qa_services as qs

        return _ssq_action(request, pk, ssq_pk, lambda s: qs.revise_ssq(s, user=request.user))


class SSQRecallView(APIView):
    @extend_schema(operation_id="portal_ssq_recall", tags=TAG_SSQ, request=None,
                   responses={200: sp.SSQSerializer, **ERR_400_404})
    def post(self, request, pk, ssq_pk):
        """Take the SSQ back from the academic department."""
        from accreditation import qa_services as qs

        return _ssq_action(request, pk, ssq_pk, lambda s: qs.recall_from_department(s, user=request.user))


class SSQDepartmentView(APIView):
    @extend_schema(operation_id="portal_ssq_send_to_department", tags=TAG_SSQ,
                   request=sp.DepartmentSendSerializer, responses={200: sp.SSQSerializer, **ERR_400_404})
    def post(self, request, pk, ssq_pk):
        """Send chosen sections to an academic-department account."""
        from accounts.models import User
        from accreditation import qa_services as qs

        data = body(sp.DepartmentSendSerializer, request)
        req = svc.request_or_404(request.user, pk) if request.user.user_type == "institution" else None
        dept_user = (User.objects.filter(pk=data["department_user"], institution=req.institution).first()
                     if req is not None else None)
        return _ssq_action(request, pk, ssq_pk, lambda s: qs.return_to_department(
            s, user=request.user, department_user=dept_user, sections=data["sections"],
            remark=data.get("remark", "")))


class SSQDepartmentDoneView(APIView):
    @extend_schema(operation_id="portal_ssq_department_done", tags=TAG_SSQ, request=None,
                   responses={200: sp.SSQSerializer, **ERR_400_404})
    def post(self, request, pk, ssq_pk):
        """The academic department hands its sections back to the administrator."""
        from accreditation import qa_services as qs

        return _ssq_action(request, pk, ssq_pk, lambda s: qs.department_submit(s, user=request.user))


# ===========================================================================
# Queries
# ===========================================================================
class QueriesView(APIView):
    @extend_schema(operation_id="portal_queries_list", tags=["portal: queries"],
                   parameters=[sp.QueryFilterSerializer],
                   responses={200: paged(sp.QueryRowSerializer), **ERR_400_404})
    def get(self, request):
        base.institution_or_404(request.user)
        params = query(sp.QueryFilterSerializer, request)
        return page(self, request, svc.queries_queryset(request.user, params), svc.query_row,
                    sp.QueryRowSerializer)


def _query(request, pk):
    from accreditation.models import Query

    base.institution_or_404(request.user)
    q = Query.objects.for_user(request.user).select_related("qa_request", "raised_by").filter(pk=pk).first()
    if q is None:
        raise Http404
    return q


class QueryDetailView(APIView):
    @extend_schema(operation_id="portal_queries_retrieve", tags=["portal: queries"],
                   responses={200: sp.QueryDetailSerializer, **ERR_404})
    def get(self, request, pk):
        q = _query(request, pk)
        return Response(sp.QueryDetailSerializer(svc.query_detail_payload(request.user, q)).data)


class QueryReplyView(APIView):
    @extend_schema(operation_id="portal_queries_reply", tags=["portal: queries"],
                   request={"multipart/form-data": sp.QueryReplySerializer,
                            "application/json": sp.QueryReplySerializer},
                   responses={201: sp.QueryDetailSerializer, **ERR_400_404})
    def post(self, request, pk):
        """Respond to NBTE (text, optional attachment)."""
        from accreditation import qa_services as qs

        q = _query(request, pk)
        data = body(sp.QueryReplySerializer, request)
        with website_rules():
            qs.respond(q, user=request.user, body=data.get("body", ""), upload=data.get("file"))
        q.refresh_from_db()
        return Response(sp.QueryDetailSerializer(svc.query_detail_payload(request.user, q)).data,
                        status=status.HTTP_201_CREATED)


# ===========================================================================
# Programmes and letters
# ===========================================================================
class ProgrammeBoardView(APIView):
    @extend_schema(operation_id="portal_programmes_board", tags=["portal: programmes"],
                   responses={200: sp.BoardSerializer, **ERR_404})
    def get(self, request):
        """Each programme with its expiry countdown and whether re-accreditation can be requested."""
        inst = base.full_institution(request)
        return Response(sp.BoardSerializer(svc.board_payload(request.user, inst)).data)


class ProgrammesView(APIView):
    @extend_schema(operation_id="portal_programmes_list", tags=["portal: programmes"],
                   parameters=[sp.ProgrammeFilterSerializer],
                   responses={200: paged(sp.ProgrammeRowSerializer), **ERR_400_404})
    def get(self, request):
        from ..pagination import StandardPagination

        inst = base.full_institution(request)
        params = query(sp.ProgrammeFilterSerializer, request)
        paginator = StandardPagination()
        chunk = paginator.paginate_queryset(svc.programmes_queryset(request.user, inst, params), request, view=self)
        return paginator.get_paginated_response(sp.ProgrammeRowSerializer(svc.programme_rows(list(chunk)),
                                                                          many=True).data)


class ProgrammeDetailView(APIView):
    @extend_schema(operation_id="portal_programmes_retrieve", tags=["portal: programmes"],
                   responses={200: sp.ProgrammeDetailSerializer, **ERR_404})
    def get(self, request, pk):
        inst = base.full_institution(request)
        return Response(sp.ProgrammeDetailSerializer(svc.programme_detail_payload(request.user, inst, pk)).data)


class LettersView(APIView):
    @extend_schema(operation_id="portal_letters_list", tags=["portal: programmes"],
                   responses={200: paged(sp.LetterSerializer), **ERR_404})
    def get(self, request):
        """Outcome letters (original and watermarked duplicate of each)."""
        base.institution_or_404(request.user)
        return page(self, request, svc.letters_queryset(request.user), svc.letter_dict, sp.LetterSerializer)


class LetterDownloadView(APIView):
    @extend_schema(operation_id="portal_letters_download", tags=["portal: programmes"],
                   responses={200: binary(), **ERR_404})
    def get(self, request, pk):
        """One copy of a letter (``copies[].id``)."""
        from accreditation.models import OutcomeLetterCopy

        base.institution_or_404(request.user)
        return serve(request, OutcomeLetterCopy.objects.for_user(request.user).filter(pk=pk).first())
