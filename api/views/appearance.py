"""v2.1 Phase A (A10): GET/PATCH me/appearance/ — the person's own theme,
palette, motion and density (both doors). Same rules as the website's
POST /me/preferences/ (config.appearance): values from fixed lists, "" goes
back to the site default, and only "theme" may change when the site does not
let staff choose (appearance.allow_user_palette = false)."""
from django.core.exceptions import ValidationError
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from config import appearance as ap

from .common import AUTHED_ERRORS, errors

AppearanceOut = inline_serializer("Appearance", {
    "preferences": serializers.DictField(help_text="Saved choices: theme, palette, motion, density, stamp."),
    "effective": serializers.DictField(help_text="What the website applies: site defaults with the choices on top."),
    "allow_user_palette": serializers.BooleanField(),
    "choices": serializers.DictField(),
})


class AppearanceUpdateSerializer(serializers.Serializer):
    theme = serializers.CharField(required=False, allow_blank=True)
    palette = serializers.CharField(required=False, allow_blank=True)
    motion = serializers.CharField(required=False, allow_blank=True)
    density = serializers.CharField(required=False, allow_blank=True)


def _payload(request):
    r = ap.resolve(request)  # the DRF request: its .user is the token user
    eff = {k: v for k, v in r["prefs"].items() if k != "site"}
    return {"preferences": request.user.preferences or {}, "effective": eff,
            "allow_user_palette": r["site"]["allow_user_palette"],
            "choices": {k: list(v) for k, v in ap.USER_CHOICES.items()}}


class MyAppearanceView(APIView):
    @extend_schema(operation_id="me_appearance_retrieve", responses={200: AppearanceOut, **AUTHED_ERRORS})
    def get(self, request):
        return Response(_payload(request))

    @extend_schema(operation_id="me_appearance_update", request=AppearanceUpdateSerializer,
                   responses={200: AppearanceOut, **errors(400, 403), **AUTHED_ERRORS})
    def patch(self, request):
        s = AppearanceUpdateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = dict(s.validated_data)
        if not ap.site_appearance()["allow_user_palette"] and any(k != "theme" for k in data):
            raise PermissionDenied("The site does not let staff choose their own palette, motion or density.")
        try:
            ap.save_user_preferences(request.user, data)
        except ValidationError as exc:
            raise DRFValidationError({"detail": exc.messages})
        return Response(_payload(request))
