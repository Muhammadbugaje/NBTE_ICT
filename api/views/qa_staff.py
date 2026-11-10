"""QA workspace for NBTE staff (staff door): ``qa/…`` endpoints.

Thin views: access = the sidebar's section checks (``QASection``), objects
are read through the website's scoped helpers (anything outside the user's
scope answers 404), and every action calls the website's own service
function. A service's ``PermissionDenied`` becomes 403 ``permission_denied``
with the service's reason; its ``ValidationError`` becomes 400
``validation_error`` (``fields.non_field_errors`` or the named field).
"""
from __future__ import annotations

import mimetypes

from django.apps import apps
from django.conf import settings
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import FileResponse, Http404, HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from .. import serializers_qa_staff as ser
from ..exceptions import ApiError
from ..pagination import StandardPagination
from ..services import qa_staff as svc
from .common import AUTHED_ERRORS, errors

OBJECT_ERRORS = {**errors(404), **AUTHED_ERRORS}
ACTION_ERRORS = {**errors(400, 404), **AUTHED_ERRORS}


class Refused(ApiError):
    """403 ``permission_denied`` carrying the website's reason."""

    status_code, code = 403, "permission_denied"


def call(fn, *args, **kwargs):
    """Run a website service; its PermissionDenied keeps its reason."""
    try:
        return fn(*args, **kwargs)
    except DjangoPermissionDenied as exc:
        raise Refused(str(exc) or None) from None


def paged(qs_or_list, request, view, shape, serializer):
    paginator = StandardPagination()
    page = paginator.paginate_queryset(qs_or_list, request, view=view)
    return paginator.get_paginated_response(serializer([shape(x) for x in page], many=True).data)


def query(serializer_cls, request):
    s = serializer_cls(data=request.query_params)
    s.is_valid(raise_exception=True)
    return s.validated_data


def body(serializer_cls, request):
    s = serializer_cls(data=request.data)
    s.is_valid(raise_exception=True)
    return s.validated_data


def paginated(name, item, **extra):
    """Schema for a paginated list (component name ``name``), plus ``extra`` fields."""
    return type(name, (serializers.Serializer,), {
        "results": item(many=True), "next": serializers.CharField(allow_null=True),
        "previous": serializers.CharField(allow_null=True), "count": serializers.IntegerField(), **extra})


class QASection(BasePermission):
    """The view's QA section must be open to the user (sidebar rules)."""

    def has_permission(self, request, view):
        section = getattr(view, "section", "")
        return not section or bool(svc.sections(request.user).get(section))


class QAView(APIView):
    section = ""
    permission_classes = [*api_settings.DEFAULT_PERMISSION_CLASSES, QASection]


# ===========================================================================
# Summary
# ===========================================================================
class SummaryView(QAView):
    """Counts for the QA home; a count is null when its section is closed to the user."""

    @extend_schema(operation_id="qa_summary", responses={200: ser.QASummarySerializer, **AUTHED_ERRORS})
    def get(self, request):
        return Response(ser.QASummarySerializer(svc.summary(request.user, request)).data)


# ===========================================================================
# Cases
# ===========================================================================
def _case_or_404(request, pk):
    from accreditation import case_services as cs

    case = (cs.visible_cases(request.user)
            .select_related("institution", "owning_department", "qa_request__qa_type", "desk_officer",
                            "supervising_audit_officer", "supervising_finance_officer")
            .filter(pk=pk).first())
    if case is None:
        raise Http404
    return case


class CaseListView(QAView):
    section = "cases"

    @extend_schema(operation_id="qa_cases_list", parameters=[ser.QACaseListQuerySerializer],
                   responses={200: paginated("PaginatedQACaseList", ser.QACaseRowSerializer), **errors(400),
                              **AUTHED_ERRORS})
    def get(self, request):
        f = query(ser.QACaseListQuerySerializer, request)
        cases = svc.filter_case_list(request.user, q=f.get("q", ""), stage=f.get("stage", ""),
                                     overdue=f["overdue"], institution=f.get("institution", ""),
                                     department=f.get("department", ""), mine=f["mine"])
        return paged(cases, request, self, svc.case_row, ser.QACaseRowSerializer)


class MyStagesView(QAView):
    section = "cases"

    @extend_schema(operation_id="qa_cases_my_stages", parameters=[ser.QAMyStagesQuerySerializer],
                   responses={200: paginated("PaginatedQAStageLinkList", ser.QAStageLinkSerializer), **errors(400),
                              **AUTHED_ERRORS})
    def get(self, request):
        from accreditation import case_services as cs

        f = query(ser.QAMyStagesQuerySerializer, request)
        links = cs.decorate_stage_links(cs.my_stage_links(request.user, kind=f["kind"]))
        return paged(links, request, self, svc.stage_link_row, ser.QAStageLinkSerializer)


class CaseDetailView(QAView):
    section = "cases"

    @extend_schema(operation_id="qa_cases_retrieve", responses={200: ser.QACaseDetailSerializer, **OBJECT_ERRORS})
    def get(self, request, pk):
        case = _case_or_404(request, pk)
        return Response(ser.QACaseDetailSerializer(svc.case_detail(case, request.user, request)).data)


def _refuse_monitor(user):
    from accreditation import case_services as cs

    if cs.is_monitor(user):
        raise Refused("The ICT monitor has read-only access to QA cases (R-13).")


class CaseActView(QAView):
    """Approve (complete / advance) or return the case at one of its current
    stages — accreditation.case_services.act, exactly as the website's
    stage-action buttons."""

    section = "cases"

    @extend_schema(operation_id="qa_cases_act", request=ser.QACaseActSerializer,
                   responses={200: ser.QACaseActResultSerializer, **ACTION_ERRORS})
    def post(self, request, pk):
        from accreditation import case_services as cs

        case = _case_or_404(request, pk)
        data = body(ser.QACaseActSerializer, request)
        _refuse_monitor(request.user)
        call(cs.act, case, user=request.user, stage_code=data["stage"], action=data["action"],
             remark=data.get("remark", ""))
        case = _case_or_404(request, pk)
        now = ", ".join(s.label for s in cs.stage_objects(case)) or "closed"
        title = "Returned" if data["action"] == "return" else "Done"
        return Response(ser.QACaseActResultSerializer({
            "message": f"{title}. The case is now at: {now}.",
            "case": svc.case_detail(case, request.user, request)}).data)


class CaseAttachView(QAView):
    """Attach a document to the open task of a stage the user holds (the
    website's task attachment: tasks.services.add_attachment)."""

    section = "cases"

    @extend_schema(operation_id="qa_cases_attach", request={"multipart/form-data": ser.QACaseAttachSerializer},
                   responses={201: ser.QACaseDocumentSerializer, **ACTION_ERRORS})
    def post(self, request, pk):
        from accreditation import case_services as cs
        from tasks import services as task_services
        from tasks.forms import AttachmentForm

        case = _case_or_404(request, pk)
        data = body(ser.QACaseAttachSerializer, request)
        _refuse_monitor(request.user)
        ok, why = cs.can_act(case, request.user, data["stage"])
        link = cs.open_link(case, data["stage"])
        if not ok or link is None:
            raise Refused(why or "This stage has no open task.")
        form = AttachmentForm({"description": data.get("description", "")}, {"file": data["file"]})
        if not form.is_valid():
            raise serializers.ValidationError({k: [str(e) for e in v] for k, v in form.errors.items()})
        att = call(task_services.add_attachment, link.task, request.user, file=form.cleaned_data["file"],
                   description=form.cleaned_data.get("description") or "")
        row = {**svc.file_dict(att, request), "source": "stage_task", "stage_code": link.stage_code,
               "description": att.description, "uploaded_by": svc.person(request.user),
               "uploaded_at": svc.dt(att.created_at)}
        return Response(ser.QACaseDocumentSerializer(row).data, status=status.HTTP_201_CREATED)


# ===========================================================================
# Files (every QA download goes through the row's can_download)
# ===========================================================================
class FileDownloadView(QAView):
    """Streams a restricted QA file. Unknown kind, missing row or a row the
    user may not download → 404 (never confirms that the file exists)."""

    def perform_content_negotiation(self, request, force=False):
        return super().perform_content_negotiation(request, force=True)

    @extend_schema(operation_id="qa_files_download",
                   parameters=[OpenApiParameter("kind", str, OpenApiParameter.PATH, enum=list(svc.FILE_MODELS))],
                   responses={(200, "application/octet-stream"): OpenApiResponse(OpenApiTypes.BINARY),
                              **OBJECT_ERRORS})
    def get(self, request, kind, pk):
        from core import audit, file_preview

        if kind not in svc.FILE_MODELS:
            raise Http404
        model = apps.get_model(kind)
        manager = getattr(model, "unscoped", None) or model._default_manager
        obj = manager.filter(pk=pk).first()
        if obj is None or not obj.file or not obj.can_download(request.user):
            raise Http404
        filename = getattr(obj, "original_name", "") or obj.file.name.rsplit("/", 1)[-1]
        ctype = file_preview.text_charset(mimetypes.guess_type(filename)[0] or "application/octet-stream")
        audit.log("other", obj, actor=request.user, note="downloaded (mobile app)")
        if settings.USE_X_ACCEL_REDIRECT:
            from urllib.parse import quote

            response = HttpResponse(content_type=ctype)
            response["X-Accel-Redirect"] = settings.PROTECTED_MEDIA_URL + quote(obj.file.name)
        else:
            response = FileResponse(obj.file.open("rb"), content_type=ctype)
        return file_preview.finish(response, filename, False)


# ===========================================================================
# Confirmations (qa_core PendingAction)
# ===========================================================================
def _pending_or_404(request, pk):
    from qa_core import actions

    pa = actions.visible_to(request.user).filter(pk=pk).first()
    if pa is None:
        raise Http404
    return pa


class ConfirmationListView(QAView):
    section = "confirmations"

    @extend_schema(operation_id="qa_confirmations_list", parameters=[ser.QAConfirmationQuerySerializer],
                   responses={200: paginated("PaginatedQAConfirmationList", ser.QAConfirmationRowSerializer,
                                                   counts=ser.QAConfirmationCountsSerializer()),
                              **errors(400), **AUTHED_ERRORS})
    def get(self, request):
        f = query(ser.QAConfirmationQuerySerializer, request)
        qs = svc.confirmation_list(request.user, tab=f["tab"], action=f.get("action", ""), q=f.get("q", ""))
        response = paged(qs, request, self, lambda pa: svc.confirmation_row(pa, request.user),
                         ser.QAConfirmationRowSerializer)
        response.data["counts"] = ser.QAConfirmationCountsSerializer(svc.confirmation_counts(request.user)).data
        return response


class ConfirmationDetailView(QAView):
    section = "confirmations"

    @extend_schema(operation_id="qa_confirmations_retrieve",
                   responses={200: ser.QAConfirmationDetailSerializer, **OBJECT_ERRORS})
    def get(self, request, pk):
        pa = _pending_or_404(request, pk)
        return Response(ser.QAConfirmationDetailSerializer(svc.confirmation_detail(pa, request.user)).data)


class _ConfirmationDecision(QAView):
    section = "confirmations"

    def _respond(self, request, pa):
        pa.refresh_from_db()
        titles = {"applied": f"{pa.reference} confirmed and applied", "failed": f"{pa.reference} could not be applied",
                  "rejected": f"{pa.reference} rejected", "withdrawn": f"{pa.reference} withdrawn"}
        message = titles.get(pa.status, f"{pa.reference}: {pa.get_status_display()}")
        if pa.failure_detail:
            message = f"{message}: {pa.failure_detail}"
        return Response(ser.QAConfirmationResultSerializer({
            "message": message, "confirmation": svc.confirmation_detail(pa, request.user)}).data)


class ConfirmationApproveView(_ConfirmationDecision):
    """Confirm (and apply) — qa_core.actions.confirm. The server enforces the
    two-step rules (not the initiator, permission, rank, department, expiry)."""

    @extend_schema(operation_id="qa_confirmations_approve", request=ser.QAApproveSerializer,
                   responses={200: ser.QAConfirmationResultSerializer, **ACTION_ERRORS})
    def post(self, request, pk):
        from qa_core import actions

        pa = _pending_or_404(request, pk)
        data = body(ser.QAApproveSerializer, request)
        if data.get("options"):
            actions.store_confirm_options(pa, request.user, data["options"])
        pa = call(actions.confirm, pa, request.user, remark=data.get("note", ""))
        return self._respond(request, pa)


class ConfirmationRejectView(_ConfirmationDecision):
    @extend_schema(operation_id="qa_confirmations_reject", request=ser.QARejectSerializer,
                   responses={200: ser.QAConfirmationResultSerializer, **ACTION_ERRORS})
    def post(self, request, pk):
        from qa_core import actions

        pa = _pending_or_404(request, pk)
        data = body(ser.QARejectSerializer, request)
        pa = call(actions.reject, pa, request.user, remark=data["note"])
        return self._respond(request, pa)


class ConfirmationWithdrawView(_ConfirmationDecision):
    """The initiator withdraws their own pending change."""

    @extend_schema(operation_id="qa_confirmations_withdraw", request=None,
                   responses={200: ser.QAConfirmationResultSerializer, **ACTION_ERRORS})
    def post(self, request, pk):
        from qa_core import actions

        pa = _pending_or_404(request, pk)
        pa = call(actions.withdraw, pa, request.user)
        return self._respond(request, pa)


# ===========================================================================
# Finance (payment verification queue)
# ===========================================================================
def _item_or_404(request, pk):
    from payments.models import PaymentVerification

    item = (PaymentVerification.objects.for_user(request.user).filter(pk=pk)
            .select_related("obligation", "obligation__institution", "obligation__fee_schedule", "attempt", "proof",
                            "statement_line", "verified_by", "second_verifier", "final_approver").first())
    if item is None:
        raise Http404
    return item


class FinanceListView(QAView):
    section = "finance"

    @extend_schema(operation_id="qa_finance_list", parameters=[ser.QAFinanceQuerySerializer],
                   responses={200: paginated("PaginatedQAFinanceList", ser.QAFinanceRowSerializer,
                                              summary=ser.QAFinanceHeroSerializer()), **errors(400),
                              **AUTHED_ERRORS})
    def get(self, request):
        from payments import services as pay

        f = query(ser.QAFinanceQuerySerializer, request)
        items = pay.queue_items(request.user, source=f["source"], age=f["age"], q=f.get("q", ""), stage=f["stage"])
        response = paged(items, request, self, svc.finance_row, ser.QAFinanceRowSerializer)
        response.data["summary"] = ser.QAFinanceHeroSerializer(svc.finance_hero(request.user)).data
        return response


class FinanceDetailView(QAView):
    section = "finance"

    @extend_schema(operation_id="qa_finance_retrieve", responses={200: ser.QAFinanceDetailSerializer, **OBJECT_ERRORS})
    def get(self, request, pk):
        item = _item_or_404(request, pk)
        return Response(ser.QAFinanceDetailSerializer(svc.finance_detail(item, request.user, request)).data)


class _FinanceAction(QAView):
    section = "finance"

    def _respond(self, request, pk, message):
        item = _item_or_404(request, pk)
        return Response(ser.QAFinanceResultSerializer({
            "message": message, "item": svc.finance_detail(item, request.user, request)}).data)


class FinanceFirstPassView(_FinanceAction):
    """Finance officer's first pass — payments.services.first_pass."""

    @extend_schema(operation_id="qa_finance_first_pass", request=ser.QAFirstPassSerializer,
                   responses={200: ser.QAFinanceResultSerializer, **ACTION_ERRORS})
    def post(self, request, pk):
        from payments import services as pay

        item = _item_or_404(request, pk)
        data = body(ser.QAFirstPassSerializer, request)
        call(pay.first_pass, item, user=request.user, decision=data["decision"], amount=data.get("amount"),
             reason=data.get("reason", ""))
        return self._respond(request, pk, "First pass recorded. Only the Director of Finance can now confirm or "
                                          "reject this payment.")


class FinanceSecondCheckView(_FinanceAction):
    """Second officer's check above the dual-verification threshold — payments.services.second_pass."""

    @extend_schema(operation_id="qa_finance_second_check", request=ser.QASecondCheckSerializer,
                   responses={200: ser.QAFinanceResultSerializer, **ACTION_ERRORS})
    def post(self, request, pk):
        from payments import services as pay

        item = _item_or_404(request, pk)
        data = body(ser.QASecondCheckSerializer, request)
        call(pay.second_pass, item, user=request.user, decision=data["decision"], reason=data.get("reason", ""))
        return self._respond(request, pk, "Second check recorded.")


class FinanceDecideView(_FinanceAction):
    """The Director of Finance confirms or rejects — payments.services.final_decision
    (the only path to a confirmed payment)."""

    @extend_schema(operation_id="qa_finance_decide", request=ser.QAFinalDecisionSerializer,
                   responses={200: ser.QAFinanceResultSerializer, **ACTION_ERRORS})
    def post(self, request, pk):
        from payments import services as pay

        item = _item_or_404(request, pk)
        data = body(ser.QAFinalDecisionSerializer, request)
        call(pay.final_decision, item, user=request.user, decision=data["decision"], reason=data.get("reason", ""))
        ref = item.obligation.reference
        message = (f"{ref} confirmed. The Payment Advice was generated and the institution notified."
                   if data["decision"] == "confirmed" else f"{ref} rejected. The institution sees your reason.")
        return self._respond(request, pk, message)


# ===========================================================================
# Institutions (read-only)
# ===========================================================================
def _institutions(request):
    from institutions.models import Institution

    return Institution.objects.for_user(request.user).select_related(
        "ownership", "institution_type", "category", "state", "handling_department")


class InstitutionListView(QAView):
    section = "institutions"

    @extend_schema(operation_id="qa_institutions_list", parameters=[ser.QAInstitutionQuerySerializer],
                   responses={200: paginated("PaginatedQAInstitutionList", ser.QAInstitutionRowSerializer),
                              **errors(400), **AUTHED_ERRORS})
    def get(self, request):
        from institutions import services as isvc

        f = query(ser.QAInstitutionQuerySerializer, request)
        qs = isvc.filter_institutions(_institutions(request), q=f.get("q", ""), status=f.get("status", ""),
                                      ownership=f.get("ownership", ""), type_code=f.get("type", ""),
                                      dept=f.get("dept", ""), zone=f.get("zone", ""), sort=f["sort"])
        return paged(qs, request, self, svc.institution_row, ser.QAInstitutionRowSerializer)


class InstitutionDetailView(QAView):
    section = "institutions"

    @extend_schema(operation_id="qa_institutions_retrieve",
                   responses={200: ser.QAInstitutionDetailSerializer, **OBJECT_ERRORS})
    def get(self, request, code):
        inst = _institutions(request).filter(code=code.upper()).first()
        if inst is None:
            raise Http404
        return Response(ser.QAInstitutionDetailSerializer(svc.institution_detail(inst, request.user, request)).data)


# ===========================================================================
# Applications (departmental clearance, SOP-01)
# ===========================================================================
def _clearance_or_404(request, pk):
    from accreditation import services as asvc
    from accreditation.views import visible_applications

    clearance = asvc.load_clearance(pk)
    if clearance is None or clearance.application_id not in visible_applications(request.user):
        raise Http404
    return clearance


class ClearanceListView(QAView):
    section = "applications"

    @extend_schema(operation_id="qa_applications_clearance_list", parameters=[ser.QAClearanceQuerySerializer],
                   responses={200: paginated("PaginatedQAClearanceList", ser.QAClearanceRowSerializer), **errors(400),
                              **AUTHED_ERRORS})
    def get(self, request):
        import datetime

        from django.utils import timezone

        from accreditation import services as asvc

        f = query(ser.QAClearanceQuerySerializer, request)
        rows = asvc.clearance_rows(request.user, status=f["status"], dept_code=f.get("dept", ""))
        q = (f.get("q") or "").strip().lower()
        if q:
            rows = [c for c in rows if q in c.application.reference.lower()
                    or q in c.application.institution.name.lower()]
        ages = {"1d": 1, "3d": 3, "7d": 7}
        if f["age"] in ages:
            cutoff = timezone.now() - datetime.timedelta(days=ages[f["age"]])
            rows = [c for c in rows if c.opened_at <= cutoff]
        return paged(rows, request, self, lambda c: svc.clearance_row(c, request.user), ser.QAClearanceRowSerializer)


class ClearanceDetailView(QAView):
    section = "applications"

    @extend_schema(operation_id="qa_applications_clearance_retrieve",
                   responses={200: ser.QAClearanceDetailSerializer, **OBJECT_ERRORS})
    def get(self, request, pk):
        clearance = _clearance_or_404(request, pk)
        return Response(ser.QAClearanceDetailSerializer(svc.clearance_detail(clearance, request.user, request)).data)


class ClearanceDecideView(QAView):
    """Clear or return the application for my department — accreditation.services.decide."""

    section = "applications"

    @extend_schema(operation_id="qa_applications_clearance_decide", request=ser.QAClearanceDecideSerializer,
                   responses={200: ser.QAClearanceResultSerializer, **ACTION_ERRORS})
    def post(self, request, pk):
        from accreditation import services as asvc

        clearance = _clearance_or_404(request, pk)
        data = body(ser.QAClearanceDecideSerializer, request)
        call(asvc.decide, clearance, user=request.user, decision=data["decision"], remark=data.get("remark", ""),
             accept_documents=data["accept_documents"])
        clearance = _clearance_or_404(request, pk)
        app = clearance.application
        if data["decision"] == "clear":
            message = (f"{clearance.department.name}: cleared. "
                       + ("All four departments have cleared it — the institutional fee was issued."
                          if app.status not in ("clearance", "submitted") else "Waiting for the other departments."))
        else:
            message = "Returned to the institution. The other departments' reviews were closed for this round."
        return Response(ser.QAClearanceResultSerializer({
            "message": message, "clearance": svc.clearance_detail(clearance, request.user, request)}).data)


# ---------------------------------------------------------------------------
# Phase 0 (Q2): onboarding programme clearance — accreditation.onboarding_programmes
# ---------------------------------------------------------------------------
def _programme_item(item):
    app = item["app"]
    return {"application": {"id": app.pk, "reference": app.reference, "status": app.status,
                            "institution": app.institution.name, "institution_code": app.institution.code},
            "department": item["department"].name if item["department"] is not None else "",
            "round": app.programme_round, "waiting_days": item["waiting_days"], "can_clear": item["can_clear"],
            "reason": item["why"],
            "programmes": [{"id": r.pk, "programme": r.programme.name,
                            "level": r.programme.level.name if r.programme.level_id else "",
                            "streams": r.programme.requested_streams, "status": r.status, "remark": r.remark}
                           for r in item["reviews"]]}


class ProgrammeClearanceListView(QAView):
    """Onboarding programme lists for my department (NTIRD by default)."""

    section = "applications"

    @extend_schema(operation_id="qa_onboarding_programmes_list",
                   parameters=[OpenApiParameter("status", str, description="pending (default) | all")],
                   responses={200: ser.QAProgrammeClearanceSerializer(many=True)})
    def get(self, request):
        from accreditation import onboarding_programmes as op

        status_ = request.query_params.get("status", "pending")
        items = op.queue(request.user, status="all" if status_ == "all" else "pending")
        return paged(items, request, self, _programme_item, ser.QAProgrammeClearanceSerializer)


class ProgrammeClearanceDecideView(QAView):
    """Clear or return one programme (remark required to return)."""

    section = "applications"

    @extend_schema(operation_id="qa_onboarding_programme_decide", request=ser.QAClearanceDecideSerializer,
                   responses={200: ser.QAProgrammeClearanceSerializer, **ACTION_ERRORS})
    def post(self, request, pk):
        from accreditation import onboarding_programmes as op

        review = op.load_review(pk)
        if review is None:
            raise Http404
        item = next((i for i in op.queue(request.user, status="all") if i["app"].pk == review.application_id), None)
        if item is None:
            raise Http404
        data = body(ser.QAClearanceDecideSerializer, request)
        call(op.decide, review, user=request.user, decision=data["decision"], remark=data.get("remark", ""))
        item = next(i for i in op.queue(request.user, status="all") if i["app"].pk == review.application_id)
        return Response(ser.QAProgrammeClearanceSerializer(_programme_item(item)).data)


# ===========================================================================
# QA requests and SSQ review (SOP-02)
# ===========================================================================
REQUEST_GROUPS = {"draft": ("draft",), "clearance": ("submitted", "programme_clearance"),
                  "fee": ("fee_due", "payment_pending_confirmation", "fee_confirmed"), "ssq": ("ssq_in_progress",),
                  "case": ("ssq_submitted", "in_workflow"), "closed": ("decided", "cancelled")}


def _request_or_404(request, pk):
    from accreditation.models import QARequest

    req = (QARequest.objects.for_user(request.user)
           .select_related("institution__handling_department", "qa_type", "cycle").filter(pk=pk).first())
    if req is None:
        raise Http404
    return req


def _ssq_or_404(request, pk):
    from accreditation.models import SSQ

    ssq = (SSQ.objects.for_user(request.user)
           .select_related("qa_request_programme__qa_request__institution", "qa_request_programme__programme__level",
                           "template", "qa_request_programme__qa_request__qa_type", "reviewed_by").filter(pk=pk).first())
    if ssq is None:
        raise Http404
    return ssq


class RequestListView(QAView):
    section = "requests"

    @extend_schema(operation_id="qa_requests_list", parameters=[ser.QARequestQuerySerializer],
                   responses={200: paginated("PaginatedQARequestList", ser.QARequestRowSerializer), **errors(400),
                              **AUTHED_ERRORS})
    def get(self, request):
        from django.db.models import Q

        from accreditation.models import QARequest

        f = query(ser.QARequestQuerySerializer, request)
        qs = QARequest.objects.for_user(request.user).select_related("institution", "qa_type").order_by("-created_at")
        if f["stage"] in REQUEST_GROUPS:
            qs = qs.filter(status__in=REQUEST_GROUPS[f["stage"]])
        if f.get("type"):
            qs = qs.filter(qa_type__code=f["type"])
        q = (f.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(reference__icontains=q) | Q(institution__name__icontains=q)
                           | Q(institution__code__iexact=q))
        return paged(qs, request, self, lambda r: svc.request_row(r, request.user), ser.QARequestRowSerializer)


class RequestDetailView(QAView):
    section = "requests"

    @extend_schema(operation_id="qa_requests_retrieve", responses={200: ser.QARequestDetailSerializer, **OBJECT_ERRORS})
    def get(self, request, pk):
        req = _request_or_404(request, pk)
        return Response(ser.QARequestDetailSerializer(svc.request_detail(req, request.user)).data)


class RequestClearView(QAView):
    """Programme clearance: clear (the QA fee is issued) or return to the
    institution — accreditation.qa_services.clear_programmes / return_programmes."""

    section = "requests"

    @extend_schema(operation_id="qa_requests_clearance", request=ser.QARequestClearSerializer,
                   responses={200: ser.QARequestResultSerializer, **ACTION_ERRORS})
    def post(self, request, pk):
        from accreditation import qa_services as qsvc

        req = _request_or_404(request, pk)
        data = body(ser.QARequestClearSerializer, request)
        remark = data.get("remark", "").strip()
        if data["decision"] == "return":
            call(qsvc.return_programmes, req, user=request.user, remark=remark)
            message = "Returned to the institution. It is back in draft."
        else:
            call(qsvc.clear_programmes, req, user=request.user, remark=remark)
            message = "Programmes cleared. The QA fee was issued (or is waiting for its amount)."
        req = _request_or_404(request, pk)
        return Response(ser.QARequestResultSerializer({"message": message,
                                                     "request": svc.request_detail(req, request.user)}).data)


class SSQListView(QAView):
    section = "requests"

    @extend_schema(operation_id="qa_ssq_list", parameters=[ser.QASSQQuerySerializer],
                   responses={200: paginated("PaginatedQASSQList", ser.QASSQRowSerializer), **errors(400),
                              **AUTHED_ERRORS})
    def get(self, request):
        from django.db.models import Q

        from accreditation.models import SSQ

        f = query(ser.QASSQQuerySerializer, request)
        qs = (SSQ.objects.for_user(request.user)
              .select_related("qa_request_programme__qa_request__institution",
                              "qa_request_programme__qa_request__qa_type", "qa_request_programme__programme__level")
              .order_by("submitted_at", "pk"))
        if f["status"] == "draft":
            qs = qs.filter(is_current=True, status__in=SSQ.EDITABLE_STATUSES)
        elif f["status"] != "all":
            qs = qs.filter(status=f["status"])
        if f.get("type"):
            qs = qs.filter(qa_request_programme__qa_request__qa_type__code=f["type"])
        if f.get("level"):
            qs = qs.filter(qa_request_programme__programme__level__code=f["level"])
        q = (f.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(qa_request_programme__qa_request__reference__icontains=q)
                           | Q(qa_request_programme__qa_request__institution__name__icontains=q)
                           | Q(qa_request_programme__programme__name__icontains=q))
        return paged(qs, request, self, svc.ssq_row, ser.QASSQRowSerializer)


class SSQDetailView(QAView):
    section = "requests"

    @extend_schema(operation_id="qa_ssq_retrieve", responses={200: ser.QASSQDetailSerializer, **OBJECT_ERRORS})
    def get(self, request, pk):
        ssq = _ssq_or_404(request, pk)
        return Response(ser.QASSQDetailSerializer(svc.ssq_detail(ssq, request.user, request)).data)


class SSQDecideView(QAView):
    """Accept or return a submitted SSQ — accreditation.qa_services.accept_ssq / return_ssq."""

    section = "requests"

    @extend_schema(operation_id="qa_ssq_decide", request=ser.QASSQDecideSerializer,
                   responses={200: ser.QASSQResultSerializer, **ACTION_ERRORS})
    def post(self, request, pk):
        from accreditation import qa_services as qsvc

        ssq = _ssq_or_404(request, pk)
        data = body(ser.QASSQDecideSerializer, request)
        remark = data.get("remark", "").strip()
        if data["decision"] == "accept":
            call(qsvc.accept_ssq, ssq, user=request.user, remark=remark)
            message = "SSQ accepted."
        else:
            call(qsvc.return_ssq, ssq, user=request.user, remark=remark)
            message = "SSQ returned. The institution revises it as a new version."
        ssq = _ssq_or_404(request, pk)
        return Response(ser.QASSQResultSerializer({"message": message,
                                                 "ssq": svc.ssq_detail(ssq, request.user, request)}).data)


class SSQAssignView(QAView):
    """Assign a submitted SSQ to one officer — accreditation.ssq_assign.assign (same rules as the website)."""

    section = "requests"

    @extend_schema(operation_id="qa_ssq_assign", request=ser.QASSQAssignSerializer,
                   responses={200: ser.QASSQResultSerializer, **ACTION_ERRORS})
    def post(self, request, pk):
        from accounts.models import User
        from accreditation import ssq_assign

        ssq = _ssq_or_404(request, pk)
        data = body(ser.QASSQAssignSerializer, request)
        officer = User.objects.filter(pk=data.get("officer") or 0, is_active=True).first()
        wa = call(ssq_assign.assign, ssq, by=request.user, officer=officer,
                  accept_suggestion=data.get("accept_suggestion", False), reason=data.get("reason", ""))
        ssq = _ssq_or_404(request, pk)
        name = wa.assignee.get_full_name() or wa.assignee.email
        return Response(ser.QASSQResultSerializer({"message": f"Assigned to {name}.",
                                                 "ssq": svc.ssq_detail(ssq, request.user, request)}).data)


# ===========================================================================
# Complaints
# ===========================================================================
def _complaint_or_404(request, pk):
    from complaints import services as csvc

    complaint = csvc.get_visible(request.user, pk)
    if complaint is None:
        raise Http404
    return complaint


class ComplaintListView(QAView):
    section = "complaints"

    @extend_schema(operation_id="qa_complaints_list", parameters=[ser.QAComplaintQuerySerializer],
                   responses={200: paginated("PaginatedQAComplaintList", ser.QAComplaintRowSerializer,
                                                counts=ser.QAComplaintCountsSerializer(), tab=serializers.CharField()), **errors(400),
                              **AUTHED_ERRORS})
    def get(self, request):
        from complaints import services as csvc

        f = query(ser.QAComplaintQuerySerializer, request)
        counts = csvc.queue_counts(request.user)
        tab = f["tab"] or csvc.default_tab(request.user, counts)
        rows = csvc.queue_rows(request.user, tab, status=f["status"], layer=f.get("layer", ""),
                               priority=f.get("priority", ""), q=f.get("q", ""), sla_filter=f["sla"])
        response = paged(rows, request, self, svc.complaint_row, ser.QAComplaintRowSerializer)
        response.data["tab"] = tab
        response.data["counts"] = ser.QAComplaintCountsSerializer(counts).data
        return response


class ComplaintDetailView(QAView):
    section = "complaints"

    @extend_schema(operation_id="qa_complaints_retrieve", responses={200: ser.QAComplaintDetailSerializer,
                                                                     **OBJECT_ERRORS})
    def get(self, request, pk):
        complaint = _complaint_or_404(request, pk)
        return Response(ser.QAComplaintDetailSerializer(svc.complaint_detail(complaint, request.user, request)).data)


class _ComplaintAction(QAView):
    section = "complaints"

    def _respond(self, request, pk, message, code=200):
        complaint = _complaint_or_404(request, pk)
        return Response(ser.QAComplaintResultSerializer({
            "message": message, "complaint": svc.complaint_detail(complaint, request.user, request)}).data,
            status=code)


class ComplaintMessageView(_ComplaintAction):
    """Reply to the raiser (public) or add an internal note — complaints.services.add_message."""

    @extend_schema(operation_id="qa_complaints_message",
                   request={"application/json": ser.QAComplaintReplySerializer,
                            "multipart/form-data": ser.QAComplaintReplySerializer},
                   responses={201: ser.QAComplaintResultSerializer, **ACTION_ERRORS})
    def post(self, request, pk):
        from complaints import services as csvc

        complaint = _complaint_or_404(request, pk)
        data = body(ser.QAComplaintReplySerializer, request)
        files = request.FILES.getlist("files") if hasattr(request, "FILES") else []
        call(csvc.add_message, complaint, request.user, data["body"], visibility=data["visibility"], files=files)
        label = "Reply sent to the raiser." if data["visibility"] == "public" else "Internal note added."
        return self._respond(request, pk, label, status.HTTP_201_CREATED)


class ComplaintActView(_ComplaintAction):
    """Take one of the current stage's handler actions (acknowledge, refer,
    request information, resolve, close … as the flow allows) — complaints.services.act."""

    @extend_schema(operation_id="qa_complaints_act", request=ser.QAComplaintActSerializer,
                   responses={200: ser.QAComplaintResultSerializer, **ACTION_ERRORS})
    def post(self, request, pk):
        from accounts.models import User
        from complaints import services as csvc
        from staff.models import Department

        complaint = _complaint_or_404(request, pk)
        data = body(ser.QAComplaintActSerializer, request)
        ok, why = csvc.can_act(request.user, complaint)
        if not ok:
            raise Refused(why)
        available = {t.action_code: t for t in csvc.handler_actions(request.user, complaint)}
        transition = available.get(data["action"])
        if transition is None:
            raise serializers.ValidationError({"action": ["This action is not available on the complaint now."]})
        if transition.requires_remark and len(data.get("remark", "").strip()) < 3:
            raise serializers.ValidationError({"remark": ["This action needs a remark."]})
        dept = Department.objects.filter(pk=data.get("refer_department") or 0).first()
        person = User.objects.filter(pk=data.get("refer_user") or 0, is_active=True, user_type="staff").first()
        officer = None
        if data["action"] == "assign":  # v2.1 X10: the service re-checks the department rule
            officer = User.objects.filter(pk=data.get("officer") or 0).first()
            if officer is None:
                raise serializers.ValidationError({"officer": ["Choose the officer to assign."]})
        call(csvc.act, complaint, data["action"], request.user, remark=data.get("remark", ""),
             refer_department=dept, refer_user=person, officer=officer)
        complaint = _complaint_or_404(request, pk)
        return self._respond(request, pk, f"Done. {complaint.reference} is now: "
                                          f"{(complaint.status or '').replace('_', ' ')}.")


class ComplaintEscalateView(_ComplaintAction):
    """Escalate to the next configured level or to a named person — complaints.services.escalate_manually."""

    @extend_schema(operation_id="qa_complaints_escalate", request=ser.QAComplaintEscalateSerializer,
                   responses={200: ser.QAComplaintResultSerializer, **ACTION_ERRORS})
    def post(self, request, pk):
        from accounts.models import User
        from complaints import services as csvc

        complaint = _complaint_or_404(request, pk)
        data = body(ser.QAComplaintEscalateSerializer, request)
        to_user = None
        if data.get("to_user"):
            to_user = User.objects.filter(pk=data["to_user"], is_active=True, user_type="staff").first()
            if to_user is None:
                raise serializers.ValidationError({"to_user": ["Choose an active NBTE staff member."]})
        rows = call(csvc.escalate_manually, complaint, request.user, reason=data["reason"], to_user=to_user)
        names = ", ".join(r.escalated_to.get_full_name() or r.escalated_to.email for r in rows if r.escalated_to)
        return self._respond(request, pk, f"Escalated. {names} is now watching {complaint.reference}.")


# ===========================================================================
# Inspections (read-only)
# ===========================================================================
class InspectionListView(QAView):
    section = "inspections"

    @extend_schema(operation_id="qa_inspections_list", parameters=[ser.QAInspectionQuerySerializer],
                   responses={200: paginated("PaginatedQAInspectionList", ser.QAInspectionRowSerializer),
                              **errors(400), **AUTHED_ERRORS})
    def get(self, request):
        from django.db.models import Q

        from inspection import services as insp

        f = query(ser.QAInspectionQuerySerializer, request)
        qs = svc.my_inspections(request.user) if f["mine"] else insp.visible_inspections(request.user)
        qs = svc.upcoming_filter(qs, f["when"])
        if f.get("status"):
            qs = qs.filter(status=f["status"])
        q = (f.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(reference__icontains=q) | Q(case__institution__name__icontains=q)
                           | Q(case__institution__code__iexact=q) | Q(case__reference__icontains=q))
        qs = qs.select_related("case__institution", "team_lead").order_by("-scheduled_start", "-pk")
        return paged(qs, request, self, lambda i: svc.inspection_row(i, request.user), ser.QAInspectionRowSerializer)


class InspectionDetailView(QAView):
    section = "inspections"

    @extend_schema(operation_id="qa_inspections_retrieve",
                   responses={200: ser.QAInspectionDetailSerializer, **OBJECT_ERRORS})
    def get(self, request, pk):
        from inspection import services as insp

        inspection = (insp.visible_inspections(request.user).filter(pk=pk)
                      .select_related("case__institution", "team_lead").first())
        if inspection is None:
            raise Http404
        return Response(ser.QAInspectionDetailSerializer(svc.inspection_detail(inspection, request.user)).data)
