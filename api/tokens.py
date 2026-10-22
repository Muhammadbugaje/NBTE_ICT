"""JWT issue with the custom ``door`` and ``user_type`` claims.

``SIMPLE_JWT["TOKEN_OBTAIN_SERIALIZER"]`` points here so simplejwt itself
would mint the same claims; the API's own token view calls
:func:`refresh_for`. Access tokens made from a refresh token copy its claims,
so a rotated refresh/access pair keeps ``door``.
"""
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .doors import door_for_user


class DoorTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["door"] = door_for_user(user)
        token["user_type"] = getattr(user, "user_type", "staff")
        return token


def refresh_for(user):
    """A new RefreshToken (recorded as outstanding) for ``user``."""
    return DoorTokenObtainPairSerializer.get_token(user)
