"""Bearer JWT authentication with the door rules (contract: "Doors").

* expired access token            -> 401 ``token_expired``
* any other bad token             -> 401 ``not_authenticated``
* token minted for the other door -> 403 ``wrong_door`` (+ ``correct_door``)
* user deactivated since issue    -> 403 ``account_inactive``
* device signed out (``sid`` row revoked) -> 401 ``token_revoked``

On success the user is also handed to the audit trail
(``core.audit.set_current_actor``): CurrentUserMiddleware ran before DRF
authenticated the request, so it only saw an anonymous user.
"""
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import ExpiredTokenError, TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import AccessToken

from .doors import DOORS, door_for_request
from .exceptions import AccountInactive, NotAuthenticatedError, TokenExpired, TokenRevoked, WrongDoor


class ApiJWTAuthentication(JWTAuthentication):
    www_authenticate_realm = "api"

    def authenticate(self, request):
        header = self.get_header(request)
        if header is None:
            return None
        raw_token = self.get_raw_token(header)
        if raw_token is None:
            return None
        token = self.get_validated_token(raw_token)
        door = token.get("door")
        if door not in DOORS:
            raise NotAuthenticatedError()
        if door != door_for_request(request):
            raise WrongDoor(correct_door=door)
        user = self.get_user(token)
        from .devices import check_app_session

        if not check_app_session(user, token):
            raise TokenRevoked()  # the device was signed out (me/devices/, website, logout)
        try:
            from core.audit import set_current_actor

            set_current_actor(user)
        except Exception:  # audit is best-effort
            pass
        return user, token

    def get_validated_token(self, raw_token):
        try:
            return AccessToken(raw_token)
        except ExpiredTokenError:
            raise TokenExpired() from None
        except TokenError:
            raise NotAuthenticatedError() from None

    def get_user(self, validated_token):
        user_id = validated_token.get(api_settings.USER_ID_CLAIM)
        if user_id is None:
            raise NotAuthenticatedError()
        user = get_user_model().objects.filter(**{api_settings.USER_ID_FIELD: user_id}).first()
        if user is None:
            raise NotAuthenticatedError()
        if not user.is_active:
            raise AccountInactive()
        return user
