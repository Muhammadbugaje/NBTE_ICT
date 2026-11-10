"""tickets/ (staff door) - see api/services/tickets.py for what is reused."""
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import serializers_work as s
from ..pagination import StandardPagination
from ..services import tickets as svc
from ..services.work import Forbidden, download, form_errors, paginate, run, user_ref
from .common import AUTHED_ERRORS, errors

LIST_ERRORS = {**errors(400), **AUTHED_ERRORS}
OBJ_ERRORS = {**errors(400, 404), **AUTHED_ERRORS}


def _ticket(request, pk):
    return get_object_or_404(svc.visible(request.user), pk=pk)


def _detail(request, ticket, code=200):
    ticket = svc.visible(request.user).filter(pk=ticket.pk).first() or ticket
    return Response(s.TicketDetailSerializer(svc.detail(ticket, request.user, request)).data, status=code)


class TicketListView(APIView):
    """my (raised by me), assigned (assigned to me), all (every ticket I may
    see: tickets.access rules)."""

    pagination_class = StandardPagination
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(operation_id="tickets_list", parameters=[s.TicketListQuerySerializer],
                   responses={200: s.TicketListItemSerializer(many=True), **LIST_ERRORS})
    def get(self, request):
        q = s.TicketListQuerySerializer(data=request.query_params)
        q.is_valid(raise_exception=True)
        return paginate(self, request, svc.list_queryset(request.user, q.validated_data),
                        s.TicketListItemSerializer, svc.list_item)

    @extend_schema(operation_id="tickets_create",
                   request={"application/json": s.TicketCreateSerializer,
                            "multipart/form-data": s.TicketCreateSerializer},
                   responses={201: s.TicketDetailSerializer, **LIST_ERRORS})
    def post(self, request):
        """tickets.views.ticket_create: TicketCreateForm (priority floor,
        subject/body length, attachment rules) then services.create_ticket."""
        from tickets import services as ts
        from tickets.forms import TicketCreateForm

        body = s.TicketCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        v = body.validated_data
        category = svc.categories().filter(code=v["category"]).first()
        if category is None:
            from rest_framework import serializers

            raise serializers.ValidationError({"category": ["Choose an open ticket category."]})
        data = {"subject": v["subject"], "body": v["body"],
                "priority": v.get("priority") or category.minimum_priority,
                "current_location": v.get("current_location", ""), "asset_choice": "",
                "asset_tag": v.get("asset_tag", "")}
        files = {"attachment": v["photo"]} if v.get("photo") else {}
        form = TicketCreateForm(data, files, category=category, user=request.user)
        if not form.is_valid():
            form_errors(form, rename={"attachment": "photo"})
        asset = None
        tag = (form.cleaned_data.get("asset_tag") or "").strip()
        if tag:
            from assets.models import Asset

            asset = Asset.objects.filter(asset_tag__iexact=tag).first()
        ticket = run(ts.create_ticket, raised_by=request.user, category=category,
                     subject=form.cleaned_data["subject"], body=form.cleaned_data["body"],
                     priority=form.cleaned_data["priority"],
                     current_location=form.cleaned_data.get("current_location") or "", asset=asset,
                     attachment=form.cleaned_data.get("attachment"))
        return _detail(request, ticket, 201)


class TicketCategoriesView(APIView):
    pagination_class = StandardPagination

    @extend_schema(operation_id="tickets_categories", responses={200: s.TicketCategorySerializer(many=True),
                                                                **AUTHED_ERRORS})
    def get(self, request):
        return paginate(self, request, list(svc.categories()), s.TicketCategorySerializer, svc.category_item)


class TicketDetailView(APIView):
    @extend_schema(operation_id="tickets_retrieve", responses={200: s.TicketDetailSerializer, **OBJ_ERRORS})
    def get(self, request, pk):
        return _detail(request, _ticket(request, pk))


class TicketAssigneesView(APIView):
    """Who the ticket can be assigned to (AssignTicketForm: active users of
    the handling department). Only for users who may assign it."""

    pagination_class = StandardPagination

    @extend_schema(operation_id="tickets_assignees", parameters=[s.PeopleQuerySerializer],
                   responses={200: s.TicketPersonSerializer(many=True), **OBJ_ERRORS})
    def get(self, request, pk):
        from django.db.models import Q

        from tickets import services as ts

        ticket = _ticket(request, pk)
        if not (ticket.is_open and ts._can_assign(ticket, request.user)):
            raise Forbidden("You cannot assign this ticket.")
        qs = svc.assignable(ticket)
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q))
        return paginate(self, request, qs, s.TicketPersonSerializer,
                        lambda u: {**user_ref(u), "email": u.email or ""})


def _form_value(value):
    if value is None:
        return ""
    return str(value)


def _action_view(name, body_serializer, form_factory, call, doc):
    class _View(APIView):
        __doc__ = doc

        @extend_schema(operation_id=f"tickets_{name}", request=body_serializer,
                       responses={200: s.TicketDetailSerializer, **OBJ_ERRORS})
        def post(self, request, pk):
            ticket = _ticket(request, pk)
            ser = body_serializer(data=request.data)
            ser.is_valid(raise_exception=True)
            form = form_factory(ticket)({k: _form_value(v) for k, v in ser.validated_data.items()})
            if not form.is_valid():
                form_errors(form)
            run(call, ticket, request.user, form.cleaned_data)
            ticket.refresh_from_db()
            return _detail(request, ticket)

    _View.__name__ = f"Ticket{name.title()}View"
    return _View


def _build_actions():
    from tickets import forms as f
    from tickets import services as ts

    def fixed(form_class):
        return lambda ticket: form_class

    def assign_form(ticket):
        return lambda data: f.AssignTicketForm(data, department=ticket.handling_department)

    return {
        "assign": _action_view("assign", s.TicketAssignSerializer, assign_form,
                               lambda t, u, d: ts.assign_ticket(t, u, assignee=d["assignee"],
                                                                reason=d.get("reason") or ""),
                               "Assign / reassign (raiser for the first assignment, or a head of the "
                               "handling department)."),
        "start": _action_view("start", s.NoteSerializer, fixed(f.StartWorkForm),
                              lambda t, u, d: ts.start_work(t, u, note=d.get("note") or ""),
                              "Assignee starts work."),
        "awaiting": _action_view("awaiting", s.ReasonSerializer, fixed(f.AwaitingForm),
                                 lambda t, u, d: ts.mark_awaiting_response(t, u, reason=d.get("reason") or ""),
                                 "Assignee pauses: awaiting a response."),
        "resolve": _action_view("resolve", s.TicketResolveSerializer, fixed(f.ResolveForm),
                                lambda t, u, d: ts.resolve_ticket(
                                    t, u, resolution_notes=d["resolution_notes"],
                                    collection_location=d.get("collection_location") or "",
                                    collection_timeframe=d.get("collection_timeframe") or ""),
                                "Resolve (goes to director sign-off when the category requires it)."),
        "signoff": _action_view("signoff", s.TicketSignoffSerializer, fixed(f.DirectorSignoffForm),
                                lambda t, u, d: ts.director_sign_off(t, u, notes=d.get("notes") or ""),
                                "Director sign-off of a resolution."),
        "close": _action_view("close", s.TicketCloseSerializer, fixed(f.CloseForm),
                              lambda t, u, d: ts.close_ticket(t, u, rating=d.get("rating") or None,
                                                              comment=d.get("comment") or ""),
                              "Raiser closes (optional 1-5 rating)."),
        "reopen": _action_view("reopen", s.RequiredReasonSerializer, fixed(f.ReopenForm),
                               lambda t, u, d: ts.reopen_ticket(t, u, d.get("reason") or ""),
                               "Raiser reopens a resolved/closed ticket (reason required)."),
        "cancel": _action_view("cancel", s.ReasonSerializer, fixed(f.CancelForm),
                               lambda t, u, d: ts.cancel_ticket(t, u, d.get("reason") or ""),
                               "Raiser cancels."),
    }


ACTIONS = _build_actions()


class TicketCommentsView(APIView):
    @extend_schema(operation_id="tickets_comment", request=s.CommentRequestSerializer,
                   responses={201: s.TicketDetailSerializer, **OBJ_ERRORS})
    def post(self, request, pk):
        """Public comment, or an internal note (``is_internal``: staff on the
        ticket only, never shown to the raiser)."""
        from tickets import services as ts
        from tickets.forms import CommentForm, InternalNoteForm

        ticket = _ticket(request, pk)
        body = s.CommentRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        internal = body.validated_data["is_internal"]
        form = (InternalNoteForm if internal else CommentForm)({"body": body.validated_data["body"]})
        if not form.is_valid():
            form_errors(form)
        fn = ts.add_internal_comment if internal else ts.add_public_comment
        run(fn, ticket, request.user, form.cleaned_data["body"])
        return _detail(request, ticket, status.HTTP_201_CREATED)


class TicketAttachmentsView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(operation_id="tickets_attach", request={"multipart/form-data": s.AttachmentUploadSerializer},
                   responses={201: s.TicketDetailSerializer, **OBJ_ERRORS})
    def post(self, request, pk):
        from tickets import services as ts
        from tickets.forms import AttachmentForm

        ticket = _ticket(request, pk)
        form = AttachmentForm(request.POST, request.FILES)
        if not form.is_valid():
            form_errors(form)
        run(ts.add_attachment, ticket, request.user, form.cleaned_data["file"],
            description=form.cleaned_data.get("description") or "")
        return _detail(request, ticket, status.HTTP_201_CREATED)


class TicketAttachmentView(APIView):
    @extend_schema(operation_id="tickets_attachment_download",
                   responses={(200, "*/*"): OpenApiResponse(OpenApiTypes.BINARY, description="The file."),
                              **OBJ_ERRORS})
    def get(self, request, pk, attachment_id):
        from tickets.models import TicketAttachment

        ticket = _ticket(request, pk)
        return download(request, get_object_or_404(TicketAttachment, pk=attachment_id, ticket=ticket))


# ---------------------------------------------------------------------------
# v2.1 Phase C: routing (I2), division decision (H23), funds (I8), rating (I4), report (I13)
# ---------------------------------------------------------------------------
class TicketRouteView(APIView):
    @extend_schema(operation_id="tickets_route", request=s.TicketRouteSerializer,
                   responses={200: s.TicketDetailSerializer, **OBJ_ERRORS})
    def post(self, request, pk):
        """Director (or a lead) of the handling department routes the ticket to a division."""
        from org.models import Division
        from tickets.routing import route_to_division

        ticket = _ticket(request, pk)
        body = s.TicketRouteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        division = Division.objects.filter(pk=body.validated_data["division"]).first()
        run(route_to_division, ticket, request.user, division, body.validated_data.get("note") or "")
        ticket.refresh_from_db()
        return _detail(request, ticket)


class TicketDivisionDecisionView(APIView):
    @extend_schema(operation_id="tickets_division_decide", request=s.ReasonSerializer,
                   responses={200: s.TicketDetailSerializer, **OBJ_ERRORS})
    def post(self, request, pk, decision):
        """The division head accepts (``accept``) or declines (``decline``, reason required)."""
        from tickets.routing import accept_division, decline_division

        ticket = _ticket(request, pk)
        if decision == "accept":
            run(accept_division, ticket, request.user)
        else:
            body = s.ReasonSerializer(data=request.data)
            body.is_valid(raise_exception=True)
            run(decline_division, ticket, request.user, body.validated_data.get("reason") or "")
        ticket.refresh_from_db()
        return _detail(request, ticket)


class TicketRequestFundsView(APIView):
    @extend_schema(operation_id="tickets_request_funds", request=s.TicketFundsRequestSerializer,
                   responses={200: s.TicketDetailSerializer, **OBJ_ERRORS})
    def post(self, request, pk):
        """Raise a Funds request (Finance + ES approve) carrying the ticket's trail."""
        from tickets.bridge import request_funds

        ticket = _ticket(request, pk)
        body = s.TicketFundsRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        run(request_funds, ticket, request.user, body.validated_data["amount"], body.validated_data["purpose"])
        ticket.refresh_from_db()
        return _detail(request, ticket)


class TicketRateView(APIView):
    @extend_schema(operation_id="tickets_rate", request=s.TicketRateSerializer,
                   responses={200: s.TicketDetailSerializer, **OBJ_ERRORS})
    def post(self, request, pk):
        """The opener rates a resolved ticket (1-5)."""
        from tickets.rating import rate_ticket

        ticket = _ticket(request, pk)
        body = s.TicketRateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        run(rate_ticket, ticket, request.user, body.validated_data["rating"], body.validated_data.get("comment") or "")
        ticket.refresh_from_db()
        return _detail(request, ticket)


class TicketReportPdfView(APIView):
    @extend_schema(operation_id="tickets_report_pdf",
                   responses={(200, "application/pdf"): OpenApiResponse(OpenApiTypes.BINARY), **OBJ_ERRORS})
    def get(self, request, pk):
        """The ticket record PDF (internal notes left out)."""
        from django.http import HttpResponse

        from tickets.pdf import render_ticket_pdf

        ticket = _ticket(request, pk)
        resp = HttpResponse(render_ticket_pdf(ticket), content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{ticket.reference}.pdf"'
        return resp
