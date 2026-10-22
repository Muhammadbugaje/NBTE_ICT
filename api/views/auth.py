"""auth/token/, auth/refresh/, auth/logout/, auth/password/change/,
auth/password/forgot/ (contract: "Authentication")."""
import logging

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import update_last_login
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import Throttled
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import ExpiredTokenError, TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from core import ratelimit

from .. import devices
from ..devices import register_app_session
from ..doors import door_for_request, door_for_user
from ..exceptions import (
    AccountInactive, InvalidCredentials, NotAuthenticatedError, TokenExpired, TokenRevoked, WrongDoor,
)
from ..serializers import (
    LogoutRequestSerializer, MeSerializer, PasswordChangeSerializer, PasswordForgotSerializer,
    RefreshRequestSerializer, TokenPairSerializer, TokenRequestSerializer, TokenResponseSerializer,
)
from ..services.access import portal_block_reason
from ..services.me import me_payload
from ..throttles import LoginRateThrottle
from ..tokens import refresh_for
from .common import AUTHED_ERRORS, errors

logger = logging.getLogger("api")


def _set_actor(user):
    try:
        from core.audit import set_current_actor

        set_current_actor(user)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------
class TokenView(APIView):
    """Sign in. Same checks as the website's sign-in page (and the same
    failed-attempt lockout, core.ratelimit "login": 5 per e-mail + IP per
    15 minutes), plus the door rule."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle, LoginRateThrottle]

    @extend_schema(operation_id="auth_token", request=TokenRequestSerializer,
                   responses={200: TokenResponseSerializer, **errors(400, 401, 403, 429)}, auth=[])
    def post(self, request):
        s = TokenRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        email = s.validated_data["email"].strip().lower()
        password = s.validated_data["password"]

        ident = f"{email}|{ratelimit.client_ip(request)}"
        limit, window = ratelimit.LOGIN_FAILURES
        if ratelimit.is_limited("login", ident, limit):
            raise Throttled(wait=window)

        user = authenticate(request._request, username=email, password=password)
        if user is None:
            if self._inactive_with_password(email, password):
                raise AccountInactive()
            ratelimit.hit("login", ident, window)
            raise InvalidCredentials()
        ratelimit.reset("login", ident)

        door = door_for_user(user)
        if door != door_for_request(request):
            raise WrongDoor(correct_door=door)
        reason = portal_block_reason(user)
        if reason:
            raise AccountInactive(reason)

        refresh = refresh_for(user)
        if api_settings.UPDATE_LAST_LOGIN:
            update_last_login(None, user)
        register_app_session(user, s.validated_data.get("device"), refresh[api_settings.JTI_CLAIM],
                             refresh=refresh, request=request)
        request.user = user  # me_payload reads request.user (QA sidebar rule)
        _set_actor(user)
        return Response({"access": str(refresh.access_token), "refresh": str(refresh),
                         "user": MeSerializer(me_payload(user, request)).data, "door": door})

    @staticmethod
    def _inactive_with_password(email, password):
        """Only a caller who knows the password learns the account is inactive."""
        user = get_user_model().objects.filter(email__iexact=email, is_active=False).first()
        return bool(user and user.check_password(password))


class RefreshView(APIView):
    """Rotate: a new access + refresh pair; the old refresh is blacklisted."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(operation_id="auth_refresh", request=RefreshRequestSerializer,
                   responses={200: TokenPairSerializer, **errors(400, 401, 403, 429)}, auth=[])
    def post(self, request):
        s = RefreshRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        refresh = load_refresh(s.validated_data["refresh"])
        door = refresh.get("door")
        if door != door_for_request(request):
            raise WrongDoor(correct_door=door) if door in ("staff", "portal") else NotAuthenticatedError()
        user = get_user_model().objects.filter(
            **{api_settings.USER_ID_FIELD: refresh.get(api_settings.USER_ID_CLAIM)}).first()
        if user is None:
            raise NotAuthenticatedError()
        if not user.is_active:
            raise AccountInactive()
        if door_for_user(user) != door:
            raise WrongDoor(correct_door=door_for_user(user))

        if refresh.get(devices.SID_CLAIM) and not devices.check_app_session(user, refresh):
            raise TokenRevoked()  # that device was signed out
        data = {"access": str(refresh.access_token)}
        if api_settings.ROTATE_REFRESH_TOKENS:
            old_jti = refresh[api_settings.JTI_CLAIM]
            if api_settings.BLACKLIST_AFTER_ROTATION:
                refresh.blacklist()
            refresh.set_jti()
            refresh.set_exp()
            refresh.set_iat()
            refresh.outstand()
            # The same device row follows the new refresh token (api.devices).
            devices.rotate(user, old_jti, refresh[api_settings.JTI_CLAIM],
                           session_id=refresh.get(devices.SID_CLAIM))
        data["refresh"] = str(refresh)
        return Response(data)


def load_refresh(raw):
    """RefreshToken or 401 token_expired / token_revoked / not_authenticated."""
    try:
        return RefreshToken(raw)
    except ExpiredTokenError:
        raise TokenExpired() from None
    except TokenError:
        pass
    try:
        unverified = RefreshToken(raw, verify=False)
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

        if BlacklistedToken.objects.filter(token__jti=unverified.get(api_settings.JTI_CLAIM)).exists():
            raise TokenRevoked()
    except TokenError:
        pass
    raise NotAuthenticatedError()


class LogoutView(APIView):
    """Revoke one device session: its refresh token is blacklisted. Works
    with an expired access token (no Authorization needed)."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(operation_id="auth_logout", request=LogoutRequestSerializer,
                   responses={204: OpenApiResponse(description="Signed out."), **errors(400, 401, 429)}, auth=[])
    def post(self, request):
        s = LogoutRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            refresh = load_refresh(s.validated_data["refresh"])
        except (TokenRevoked, TokenExpired):
            return Response(status=status.HTTP_204_NO_CONTENT)  # already signed out
        refresh.blacklist()
        devices.logged_out(refresh[api_settings.JTI_CLAIM])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
_FIELD_MAP = {"current_password": "old_password", "confirm_password": "new_password",
              "__all__": "new_password", "new_password": "new_password"}


def _form_errors(form):
    fields = {}
    for name, errs in form.errors.items():
        fields.setdefault(_FIELD_MAP.get(name, name), []).extend(str(e) for e in errs)
    return fields


class PasswordChangeView(APIView):
    """Same forms (and so the same password rules) as the website: on the
    staff door accounts.forms.ForcePasswordChangeForm /
    VoluntaryPasswordChangeForm, on the portal qa_core.portal_forms
    .PortalPasswordChangeForm. A temporary password needs no old_password.
    Saving clears must_change_password."""

    allow_while_password_change_required = True

    @extend_schema(operation_id="auth_password_change", request=PasswordChangeSerializer,
                   responses={204: OpenApiResponse(description="Password changed."), **errors(400),
                              **AUTHED_ERRORS})
    def post(self, request):
        s = PasswordChangeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = request.user
        forced = bool(user.must_change_password)
        new = s.validated_data["new_password"]
        data = {"current_password": s.validated_data.get("old_password", ""), "new_password": new,
                "confirm_password": new}
        if door_for_request(request) == "portal":
            from qa_core.portal_forms import PortalPasswordChangeForm

            form = PortalPasswordChangeForm(user, data, forced=forced)
        else:
            from accounts.forms import ForcePasswordChangeForm, VoluntaryPasswordChangeForm

            form = (ForcePasswordChangeForm if forced else VoluntaryPasswordChangeForm)(user, data)
        if not form.is_valid():
            raise serializers.ValidationError(_form_errors(form))
        form.save()
        # TODO(Phase 9 follow-up): offer "sign out other devices" once sessions are stored.
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordForgotView(APIView):
    """Always 204 (no account enumeration). Staff door: the website's
    password-reset e-mail (accounts.forms.PortalPasswordResetForm, its own
    3-per-hour limits). Portal door: the portal's one-time set-password link
    (qa_core.portal_views.send_set_password_link, same limits as the
    portal's "Forgot password" page)."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(operation_id="auth_password_forgot", request=PasswordForgotSerializer,
                   responses={204: OpenApiResponse(description="If the account exists, an e-mail is sent."),
                              **errors(400, 429)}, auth=[])
    def post(self, request):
        s = PasswordForgotSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        email = s.validated_data["email"].strip().lower()
        try:
            if door_for_request(request) == "portal":
                self._portal(request._request, email)
            else:
                self._staff(request._request, email)
        except Exception:
            logger.exception("password forgot failed")
        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def _staff(django_request, email):
        from accounts.forms import PortalPasswordResetForm

        form = PortalPasswordResetForm({"email": email})
        if form.is_valid():
            form.save(request=django_request, use_https=django_request.is_secure())

    @staticmethod
    def _portal(django_request, email):
        # Body of qa_core.portal_views.password_forgot, minus the HTML.
        from qa_core.portal_forms import PortalPasswordResetForm
        from qa_core.portal_views import send_set_password_link

        form = PortalPasswordResetForm({"email": email})
        if not form.is_valid():
            return
        ip = ratelimit.client_ip(django_request)
        limit, window = ratelimit.PASSWORD_RESETS
        if (ratelimit.is_limited("portal-reset-ip", ip, limit)
                or ratelimit.is_limited("portal-reset-email", email, limit)):
            return
        ratelimit.hit("portal-reset-ip", ip, window)
        ratelimit.hit("portal-reset-email", email, window)
        for user in form.get_users(email):
            send_set_password_link(django_request, user)
