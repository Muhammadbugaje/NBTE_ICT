"""me/ (GET, PATCH) and the account endpoints: notification preferences,
signed-in devices, push token, account deletion request and the digital
staff ID (see api/docs/me.md)."""
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from ..serializers import MeSerializer, MeUpdateSerializer
from ..serializers_me import (
    DeletionRequestCreateSerializer, DeletionRequestRowSerializer, DeletionStatusSerializer, DeviceRowSerializer,
    PreferencesSerializer, PreferencesUpdateSerializer, PushTokenSerializer, StaffIdSerializer,
)
from ..services import account
from ..services.me import me_payload
from .common import AUTHED_ERRORS, errors


class MeView(APIView):
    allow_while_password_change_required = True

    @extend_schema(operation_id="me_retrieve", responses={200: MeSerializer, **AUTHED_ERRORS})
    def get(self, request):
        return Response(MeSerializer(me_payload(request.user, request)).data)

    @extend_schema(operation_id="me_update", request={"application/json": MeUpdateSerializer,
                                                      "multipart/form-data": MeUpdateSerializer},
                   responses={200: MeSerializer, **errors(400), **AUTHED_ERRORS})
    def patch(self, request):
        s = MeUpdateSerializer(data=request.data, context={"request": request})
        s.is_valid(raise_exception=True)
        user = request.user
        if user.user_type == "staff":
            self._save_staff(user, s.validated_data)
        else:
            self._save_portal(user, s.validated_data)
        user.refresh_from_db()
        return Response(MeSerializer(me_payload(user, request)).data)

    @staticmethod
    def _raise(form):
        raise serializers.ValidationError({("non_field_errors" if k == "__all__" else k): [str(e) for e in v]
                                           for k, v in form.errors.items()})

    def _save_staff(self, user, changes):
        """Through the website's accounts.forms.ProfileForm: current values
        plus the changed ones (PATCH semantics). E-mail is not changeable
        from the app."""
        from accounts.forms import ProfileForm

        current = ProfileForm(user)
        data = {name: field.initial for name, field in current.fields.items() if name != "photo"}
        data = {k: ("" if v is None else v) for k, v in data.items()}
        data.update({k: v for k, v in changes.items() if k != "photo"})
        data["email"] = user.email
        files = {"photo": changes["photo"]} if changes.get("photo") else {}
        form = ProfileForm(user, data, files)
        if not form.is_valid():
            self._raise(form)
        form.save()
        try:  # the website's "profile complete" notification, if just crossed
            from accounts.views import _maybe_notify_profile_complete

            _maybe_notify_profile_complete(None, user)
        except Exception:
            pass

    def _save_portal(self, user, changes):
        """Through the portal's qa_core.portal_forms.AccountProfileForm."""
        from qa_core.portal_forms import AccountProfileForm

        data = {"first_name": changes.get("first_name", user.first_name),
                "last_name": changes.get("last_name", user.last_name)}
        form = AccountProfileForm(data, instance=user)
        if not form.is_valid():
            self._raise(form)
        form.save()


# ---------------------------------------------------------------------------
# Preferences, devices, push token, deletion request, staff ID
# ---------------------------------------------------------------------------
class PreferencesView(APIView):
    """Notification settings: the website's notification-settings form
    (portal accounts get only the portal's kinds)."""

    @extend_schema(operation_id="me_preferences_retrieve", responses={200: PreferencesSerializer, **AUTHED_ERRORS})
    def get(self, request):
        return Response(PreferencesSerializer(account.preferences_payload(request.user)).data)

    @extend_schema(operation_id="me_preferences_update", request=PreferencesUpdateSerializer,
                   responses={200: PreferencesSerializer, **errors(400), **AUTHED_ERRORS})
    def patch(self, request):
        s = PreferencesUpdateSerializer(data=request.data, context={"request": request})
        s.is_valid(raise_exception=True)
        account.save_preferences(request.user, s.validated_data)
        return Response(PreferencesSerializer(account.preferences_payload(request.user)).data)


class DevicesView(APIView):
    """Signed-in devices: app installs and website browsers (both doors)."""

    @extend_schema(operation_id="me_devices_list", responses={200: DeviceRowSerializer(many=True), **AUTHED_ERRORS})
    def get(self, request):
        return Response(DeviceRowSerializer(account.device_rows(request.user, request), many=True).data)


class DeviceDetailView(APIView):
    @extend_schema(operation_id="me_devices_destroy", request=None,
                   responses={204: OpenApiResponse(description="Signed out."), **errors(404), **AUTHED_ERRORS})
    def delete(self, request, pk):
        if not account.revoke_device(request.user, pk):
            raise NotFound()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DevicesRevokeOthersView(APIView):
    @extend_schema(operation_id="me_devices_revoke_others", request=None,
                   responses={204: OpenApiResponse(description="Every other device is signed out."),
                              **AUTHED_ERRORS})
    def post(self, request):
        account.revoke_others(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PushTokenView(APIView):
    @extend_schema(operation_id="me_push_token", request=PushTokenSerializer,
                   responses={204: OpenApiResponse(description="Stored on this device."), **errors(400),
                              **AUTHED_ERRORS})
    def post(self, request):
        s = PushTokenSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if not account.store_push_token(request, s.validated_data.get("push_token"),
                                        s.validated_data.get("device_id") or ""):
            raise serializers.ValidationError(
                {"device_id": ["This device is not signed in. Sign in again to turn on notifications."]})
        return Response(status=status.HTTP_204_NO_CONTENT)


class DeletionRequestView(APIView):
    """Account deletion request (accounts.deletion): file, see, withdraw."""

    @extend_schema(operation_id="me_deletion_request_retrieve",
                   responses={200: DeletionStatusSerializer, **AUTHED_ERRORS})
    def get(self, request):
        return Response(DeletionStatusSerializer(account.deletion_status(request.user)).data)

    @extend_schema(operation_id="me_deletion_request", request=DeletionRequestCreateSerializer,
                   responses={201: DeletionRequestRowSerializer, 200: DeletionRequestRowSerializer,
                              **errors(400), **AUTHED_ERRORS})
    def post(self, request):
        from accounts import deletion

        s = DeletionRequestCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        existing = deletion.open_request(request.user)
        req = deletion.request_deletion(request.user, s.validated_data.get("reason", ""))
        code = status.HTTP_200_OK if existing is not None else status.HTTP_201_CREATED
        return Response(DeletionRequestRowSerializer(account.deletion_row(req)).data, status=code)

    @extend_schema(operation_id="me_deletion_request_withdraw", request=None,
                   responses={204: OpenApiResponse(description="Withdrawn."), **errors(404), **AUTHED_ERRORS})
    def delete(self, request):
        from accounts import deletion

        if not deletion.withdraw(request.user):
            raise NotFound("There is no pending request to withdraw.")
        return Response(status=status.HTTP_204_NO_CONTENT)


class StaffIdView(APIView):
    """The digital staff ID card with a rotating QR code (staff door only)."""

    @extend_schema(operation_id="me_staff_id", responses={200: StaffIdSerializer, **AUTHED_ERRORS})
    def get(self, request):
        from accounts import staff_id

        if not staff_id.is_eligible(request.user):
            raise PermissionDenied()
        response = Response(StaffIdSerializer(account.staff_id_payload(request.user, request)).data)
        response["Cache-Control"] = "no-store"
        return response
