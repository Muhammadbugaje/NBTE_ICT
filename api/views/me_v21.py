"""v2.1 Phase B (pb-platform): the owner's signature status and bank details.

* GET  me/signature/                - status only; the image is never sent (S-13).
* GET  me/bank-details/             - masked summary (owner only).
* PUT  me/bank-details/             - change; needs "password" every time (X33).
* POST me/bank-details/confirm/     - "my details are correct".

Only the token's own user is ever read: there is no endpoint for anyone
else's bank details, and impersonation never reaches the API (JWT only).
"""
from django.core.exceptions import ValidationError
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .common import AUTHED_ERRORS, errors

SignatureStatus = inline_serializer("SignatureStatus", {
    "has_signature": serializers.BooleanField(), "required": serializers.BooleanField(),
    "accepted_at": serializers.DateTimeField(allow_null=True), "source": serializers.CharField(),
    "manage_url": serializers.CharField()})

BankSummary = inline_serializer("BankDetails", {
    "bank": serializers.CharField(), "bank_code": serializers.CharField(), "masked": serializers.CharField(),
    "last4": serializers.CharField(), "account_name": serializers.CharField(),
    "name_match": serializers.CharField(), "confirmed_at": serializers.DateTimeField(allow_null=True),
    "needs_confirmation": serializers.BooleanField()})


class BankChangeSerializer(serializers.Serializer):
    bank_code = serializers.CharField(max_length=40)
    account_number = serializers.CharField(max_length=13)
    account_name = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=256, write_only=True)


def _staff_owner(request):
    if getattr(request.user, "user_type", "staff") != "staff":
        raise PermissionDenied("Bank details and signatures are kept for NBTE staff.")
    if getattr(getattr(request, "_request", None), "is_impersonating", False):
        raise PermissionDenied("Hidden while acting as someone else.")
    return request.user


class MySignatureView(APIView):
    @extend_schema(operation_id="me_signature", responses={200: SignatureStatus, **AUTHED_ERRORS})
    def get(self, request):
        from django.urls import reverse

        from documents import signature

        user = _staff_owner(request)
        active = signature.active_for(user)
        return Response({"has_signature": active is not None, "required": signature.signature_required(user),
                         "accepted_at": active.accepted_at if active else None,
                         "source": ("drawn" if active and active.source_name == signature.DRAWN else
                                    "photo" if active else ""),
                         "manage_url": reverse("documents:my_signature")})


class MyBankDetailsView(APIView):
    @extend_schema(operation_id="me_bank_details", responses={200: BankSummary, 204: None, **AUTHED_ERRORS})
    def get(self, request):
        from honoraria import services

        user = _staff_owner(request)
        data = services.summary(services.active_account(user))
        response = Response(data if data is not None else {"bank": None}, status=200)
        response["Cache-Control"] = "no-store"
        return response

    @extend_schema(operation_id="me_bank_details_update", request=BankChangeSerializer,
                   responses={200: BankSummary, **errors(400), **AUTHED_ERRORS})
    def put(self, request):
        from core import reauth
        from honoraria import services
        from honoraria.models import Bank

        _staff_owner(request)
        s = BankChangeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        bank = Bank.objects.filter(code=s.validated_data["bank_code"], is_active=True).first()
        try:
            account = services.save_for_owner(request, bank=bank, account_number=s.validated_data["account_number"],
                                              account_name=s.validated_data["account_name"],
                                              password=s.validated_data["password"], via="api")
        except reauth.ReauthRequired as exc:
            raise PermissionDenied(exc.message)
        except ValidationError as exc:
            raise DRFValidationError(exc.message_dict if hasattr(exc, "error_dict") else {"non_field_errors": exc.messages})
        response = Response(services.summary(account))
        response["Cache-Control"] = "no-store"
        return response


class MyBankDetailsConfirmView(APIView):
    @extend_schema(operation_id="me_bank_details_confirm", request=None,
                   responses={200: BankSummary, **errors(400), **AUTHED_ERRORS})
    def post(self, request):
        from django.utils import timezone

        from honoraria import services
        from honoraria.models import BankDetailsLog

        user = _staff_owner(request)
        account = services.active_account(user)
        if account is None:
            raise DRFValidationError({"non_field_errors": ["Add your bank details first."]})
        account.confirmed_at = timezone.now()
        account.save(update_fields=["confirmed_at", "updated_at"])
        services.log(owner=user, account=account, action=BankDetailsLog.CONFIRMED, before=services.describe(account),
                     after=services.describe(account), note="Confirmed in the app")
        return Response(services.summary(account))
