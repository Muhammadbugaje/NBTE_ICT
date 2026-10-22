"""Default permission chain (settings.REST_FRAMEWORK):

    IsAuthenticated -> CorrectDoor -> PasswordChangeCompleted

A view that must stay reachable while the password is temporary sets
``allow_while_password_change_required = True`` (me/, auth/password/change/).
"""
from django.conf import settings
from rest_framework.permissions import BasePermission

from .doors import door_for_request, door_for_user
from .exceptions import AccountInactive, PasswordChangeRequired, WrongDoor


class CorrectDoor(BasePermission):
    """The signed-in user's *current* user_type must belong to this door (a
    token can outlive a change of user_type)."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return True  # IsAuthenticated already decided
        expected = door_for_user(user)
        if expected != door_for_request(request):
            raise WrongDoor(correct_door=expected)
        from .services.access import portal_block_reason

        reason = portal_block_reason(user)
        if reason:
            raise AccountInactive(reason)
        return True


class PasswordChangeCompleted(BasePermission):
    """Mirror of ForcePasswordChangeMiddleware for the API."""

    def has_permission(self, request, view):
        user = request.user
        if (user and user.is_authenticated and getattr(user, "must_change_password", False)
                and not getattr(view, "allow_while_password_change_required", False)):
            raise PasswordChangeRequired()
        return True


class DocsAccess(BasePermission):
    """Schema and Swagger UI: anyone while DEBUG, superusers otherwise."""

    def has_permission(self, request, view):
        if settings.DEBUG:
            return True
        user = request.user
        return bool(user and user.is_authenticated and user.is_superuser)
