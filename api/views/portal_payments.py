"""Portal door: bills and payments (payments.portal_views).

Paying online uses the website's own ``start_online_payment`` (the same
provider initiation, the same callback to the portal payment page); the app
opens ``checkout_url`` in the browser and, when the provider sends the payer
back, posts the returned query parameters to ``verify/``. The app never
collects card data.
"""
from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import serializers_portal as sp
from ..services import portal as base
from ..services import portal_payments as svc
from .common import AUTHED_ERRORS, errors
from .portal_common import Forbidden, binary, body, form_error, page, paged, query, website_rules
from .portal_institution import serve

ERR_404 = {**errors(404), **AUTHED_ERRORS}
ERR_400_404 = {**errors(400, 404), **AUTHED_ERRORS}
TAG = ["portal: payments"]


def _bill(request, pk):
    base.institution_or_404(request.user)
    return svc.obligation_or_404(request.user, pk)


def _detail(request, ob, code=status.HTTP_200_OK):
    ob.refresh_from_db()
    return Response(sp.BillDetailSerializer(svc.bill_detail_payload(request, ob)).data, status=code)


class PaymentsView(APIView):
    @extend_schema(operation_id="portal_payments_list", tags=TAG, parameters=[sp.PaymentFilterSerializer],
                   responses={200: paged(sp.BillRowSerializer), **ERR_400_404})
    def get(self, request):
        """Bills, newest first. ?group=due (open) | checking | settled (paid) | closed."""
        base.institution_or_404(request.user)
        params = query(sp.PaymentFilterSerializer, request)
        return page(self, request, svc.payments_queryset(request.user, params), svc.bill_row, sp.BillRowSerializer)


class PaymentsSummaryView(APIView):
    @extend_schema(operation_id="portal_payments_summary", tags=TAG,
                   responses={200: sp.PaymentSummarySerializer, **ERR_404})
    def get(self, request):
        base.institution_or_404(request.user)
        return Response(sp.PaymentSummarySerializer(svc.summary_payload(request.user)).data)


class PaymentDetailView(APIView):
    @extend_schema(operation_id="portal_payments_retrieve", tags=TAG,
                   responses={200: sp.BillDetailSerializer, **ERR_404})
    def get(self, request, pk):
        return _detail(request, _bill(request, pk))


class PaymentSlipView(APIView):
    @extend_schema(operation_id="portal_payments_slip_download", tags=TAG, responses={200: binary(), **ERR_404})
    def get(self, request, pk):
        """The current payment slip (PDF)."""
        from payments.models import PaymentSlip

        ob = _bill(request, pk)
        slip = (PaymentSlip.objects.for_user(request.user).filter(obligation=ob, is_current=True)
                .order_by("-version").first())
        return serve(request, slip)

    @extend_schema(operation_id="portal_payments_slip_regenerate", tags=TAG, request=None,
                   responses={200: sp.BillDetailSerializer, **ERR_400_404})
    def post(self, request, pk):
        """A new slip version (expired slip / new balance): same reference."""
        from payments import services

        ob = _bill(request, pk)
        with website_rules():
            services.regenerate_slip(ob, actor=request.user)
        return _detail(request, ob)


class PaymentOnlineView(APIView):
    @extend_schema(operation_id="portal_payments_online", tags=TAG, request=sp.OnlinePaymentSerializer,
                   responses={201: sp.CheckoutSerializer, **ERR_400_404})
    def post(self, request, pk):
        """Start an online payment (Remita or Paystack) exactly as the
        website's "Pay with …" button does; open ``checkout_url``."""
        from django.urls import reverse

        from payments import services
        from qa_core.hosts import portal_absolute

        from payments import app_return

        ob = _bill(request, pk)
        data = body(sp.OnlinePaymentSerializer, request)
        to_app = data.get("return_to") == "app"
        if to_app:  # Phase 9: back to the app after the provider (payments/app_return.py)
            callback = app_return.return_url(ob, request._request)
        else:
            callback = portal_absolute(reverse("qa_portal:payment", args=[ob.pk]), request._request)
        with website_rules():
            attempt = services.start_online_payment(ob, data["provider"], user=request.user,
                                                    amount=data.get("amount") or None, callback_url=callback,
                                                    request=request._request)
        if not attempt.checkout_url:
            raise Forbidden(attempt.failure_reason or "The payment could not be started. Try again.")
        if to_app:
            app_return.mark_attempt(attempt)
        return Response(sp.CheckoutSerializer({"checkout_url": attempt.checkout_url,
                                               "reference": attempt.provider_reference,
                                               "provider": attempt.provider, "return_url": callback,
                                               "return_to": "app" if to_app else "web"}).data,
                        status=status.HTTP_201_CREATED)


class PaymentVerifyView(APIView):
    @extend_schema(operation_id="portal_payments_verify", tags=TAG, request=sp.VerifyRequestSerializer,
                   responses={200: sp.VerifyResultSerializer, **ERR_400_404})
    def post(self, request, pk):
        """The payer came back from the provider: check that attempt now
        (payments.services.verify_on_return), as the portal page does."""
        from payments import services

        ob = _bill(request, pk)
        data = body(sp.VerifyRequestSerializer, request)
        attempt = services.verify_on_return(ob, data["params"])
        ob.refresh_from_db()
        return Response(sp.VerifyResultSerializer({
            "matched": attempt is not None, "attempt": svc.attempt_dict(attempt),
            "message": svc.verify_message(attempt), "bill": svc.bill_detail_payload(request, ob)}).data)


class PaymentProofView(APIView):
    @extend_schema(operation_id="portal_payments_proof", tags=TAG,
                   request={"multipart/form-data": sp.ProofUploadSerializer},
                   responses={201: sp.BillDetailSerializer, **ERR_400_404})
    def post(self, request, pk):
        """"I have paid": teller / transfer receipt (the website's ProofForm)."""
        from payments import services
        from payments.portal_views import ProofForm

        ob = _bill(request, pk)
        body(sp.ProofUploadSerializer, request)
        form = ProofForm(request.data, request.FILES)
        if not form.is_valid():
            form_error(form)
        d = form.cleaned_data
        with website_rules():
            services.submit_proof(ob, user=request.user, upload=d["file"], payer_name=d.get("payer_name", ""),
                                  paid_on=d["paid_on"], amount_claimed=d["amount_claimed"],
                                  bank_name=d["bank_name"], teller_number=d["teller_number"])
        return _detail(request, ob, status.HTTP_201_CREATED)


class PaymentReceiptView(APIView):
    @extend_schema(operation_id="portal_payments_receipt_download", tags=TAG, responses={200: binary(), **ERR_404})
    def get(self, request, pk):
        """The latest Payment Advice (NBTE's receipt, PDF) once Finance confirmed the payment."""
        from payments.models import PaymentAdvice

        ob = _bill(request, pk)
        advice = PaymentAdvice.objects.for_user(request.user).filter(obligation=ob).order_by("-generated_at").first()
        return serve(request, advice)

    @extend_schema(operation_id="portal_payments_receipt_upload", tags=TAG,
                   request={"multipart/form-data": sp.FileUploadSerializer},
                   responses={201: sp.BillDetailSerializer, **ERR_400_404})
    def post(self, request, pk):
        """Upload the provider's receipt for an online payment the provider reported."""
        from payments import services
        from payments.models import PaymentAttempt

        ob = _bill(request, pk)
        data = body(sp.FileUploadSerializer, request)
        attempt = (PaymentAttempt.objects.for_user(request.user)
                   .filter(obligation=ob, status="provider_reported_paid").order_by("-created_at").first())
        if attempt is None:
            raise Http404
        with website_rules():
            services.submit_proof(ob, user=request.user, upload=data["file"], kind="provider_receipt",
                                  attempt=attempt, amount_claimed=attempt.amount)
        return _detail(request, ob, status.HTTP_201_CREATED)
