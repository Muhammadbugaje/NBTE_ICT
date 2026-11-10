"""Settings -> Appearance and the personal preferences endpoint
(v2.1 Phase A, A9/A10; ported from EE Hub content/appearance_views.py).

* /settings/appearance/                 site appearance (admin), saved through
                                        qa_core.settings_service.save_setting
                                        (permission, audit log, dual control).
* /settings/appearance/preview/<page>/  the staff home or landing page, framable
                                        by this origin only, for the live preview.
* /me/preferences/                      the signed-in person's own theme,
                                        palette, motion and density (JSON, HTMX
                                        or a plain form post). Both hosts.
"""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.clickjacking import xframe_options_sameorigin

from . import appearance as ap
from .models import ConfigEntry

SITE_KEYS = ("palette", "custom_primary", "custom_accent", "theme", "scene", "motion", "density")
BOOL_KEYS = ("allow_user_palette", "portal_follows_site")
INT_KEYS = ("button_gap",)
LEGACY_KEY = "ui.legacy_css"
EDIT_PERMISSION = "config.change_configentry"


def _catalogue():
    from .settings_catalogue import SETTINGS

    return {row[0]: row for row in SETTINGS}


def _entry(key):
    """The ConfigEntry for `key`, created from the catalogue if the database
    was never seeded (create only; never overwrites)."""
    entry = ConfigEntry.objects.filter(key=key, scope="global").first()
    if entry is not None:
        return entry
    cat = _catalogue()
    if key not in cat:  # rows registered in config/defaults.py (appearance.button_gap)
        from .defaults import DEFAULTS
        cat.update({k: (k, v, t, g, d, EDIT_PERMISSION, False) for k, v, t, g, d in DEFAULTS})
    key_, value, vtype, group, desc, editable_by, dual = cat[key]
    entry = ConfigEntry(key=key, scope="global", value=value, value_type=vtype, group=group,
                        description=desc[:255], editable_by=editable_by, dual_control=dual)
    entry.save()
    return entry


def _bool_text(value):
    return "true" if str(value).strip().lower() in ("1", "true", "yes", "on") else "false"


def _form_values(post):
    """{config key: stored text} from the posted form."""
    out = {}
    for k in SITE_KEYS:
        if k in post:
            out[f"appearance.{k}"] = (post.get(k) or "").strip().lower()
    for k in BOOL_KEYS:
        out[f"appearance.{k}"] = _bool_text(post.get(k, ""))
    for k in INT_KEYS:
        if k in post:
            out[f"appearance.{k}"] = (post.get(k) or "").strip()
    out[LEGACY_KEY] = _bool_text(post.get("legacy_css", ""))
    return out


def _context(request, *, errors=None, saved=None, pending=None, values=None):
    site = ap.site_appearance()
    if values:  # re-show what was posted when it failed validation
        for key, raw in values.items():
            short = key.split(".", 1)[1]
            if short in BOOL_KEYS or key == LEGACY_KEY:
                site["legacy_css" if key == LEGACY_KEY else short] = raw == "true"
            else:
                site[short] = raw
    return {
        "site": site,
        "palettes": ap.PALETTES,
        "scenes": ap.SCENES,
        "motion_levels": ap.MOTION_LEVELS,
        "themes": ap.THEMES,
        "contrast": ap.contrast_ratio(site["custom_primary"]) if ap.HEX_RE.match(site["custom_primary"] or "") else None,
        "min_contrast": ap.MIN_CONTRAST,
        "errors": errors or {},
        "saved": saved or [],
        "pending": pending or [],
        "section": "appearance",
    }


@login_required
@permission_required(EDIT_PERMISSION, raise_exception=True)
def appearance_settings(request):
    from qa_core import settings_service

    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])
    template = "config/_appearance_form.html" if request.headers.get("HX-Request") else "config/appearance.html"
    if request.method == "GET":
        return render(request, "config/appearance.html", _context(request))

    values = _form_values(request.POST)
    # Validate everything first (no half-saved palette).
    errors = {}
    for key, raw in values.items():
        func = ap.FORM_VALIDATORS.get(key)
        if func is None:
            continue
        if key in ("appearance.custom_primary", "appearance.custom_accent") and values.get("appearance.palette") != "custom":
            # Only the custom pair needs to be valid when the custom palette is chosen;
            # otherwise a malformed value is still refused, but contrast is not checked.
            if raw and not ap.HEX_RE.match(raw):
                errors[key.split(".", 1)[1]] = "Use a colour in the form #rrggbb (for example #0e7490)."
            continue
        try:
            func(raw)
        except ValidationError as exc:
            errors[key.split(".", 1)[1]] = " ".join(exc.messages)
    if errors:
        return render(request, template, _context(request, errors=errors, values=values))

    saved, pending, failed = [], [], {}
    for key, raw in values.items():
        try:
            entry = _entry(key)
        except (ValidationError, KeyError) as exc:
            failed[key] = str(exc)
            continue
        if entry.value.strip().lower() == raw:
            continue
        if not entry.editable_by:  # rows from config/defaults.py: this page's permission
            entry.editable_by = EDIT_PERMISSION
        try:
            outcome, _obj = settings_service.save_setting(
                request.user, entry, raw, reason="Settings → Appearance",
                impersonating=getattr(request, "is_impersonating", False))
        except PermissionDenied as exc:
            failed[key.split(".", 1)[1]] = str(exc)
        except ValidationError as exc:
            failed[key.split(".", 1)[1]] = " ".join(exc.messages)
        else:
            (saved if outcome == "saved" else pending).append(key)
    if not request.headers.get("HX-Request"):
        if failed:
            messages.error(request, "Not saved: " + "; ".join(f"{k}: {v}" for k, v in failed.items()))
        elif saved or pending:
            messages.success(request, "Appearance saved." + (" Some changes wait for a second officer." if pending else ""))
        else:
            messages.info(request, "Nothing changed.")
        return redirect("config:appearance")
    return render(request, template, _context(request, errors=failed, saved=saved, pending=pending))


PREVIEW_PAGES = {"home", "landing"}


@login_required
@permission_required(EDIT_PERMISSION, raise_exception=True)
@xframe_options_sameorigin
def appearance_preview(request, page):
    """The real page, framable by this origin, for the live preview. The
    preview's palette/scene/motion come from ?palette=&scene=… (theme-init.js)
    and from the settings page script, which edits the frame directly."""
    if page not in PREVIEW_PAGES:
        raise Http404
    from core import views as core_views

    if page == "home":
        response = core_views.home(request)
    else:
        response = core_views.landing(request)
    return response


# ---------------------------------------------------------------------------
# Personal preferences
# ---------------------------------------------------------------------------
def _payload(request):
    ctype = (request.content_type or "").split(";")[0].strip()
    if ctype == "application/json":
        try:
            data = json.loads(request.body or b"{}")
        except ValueError:
            raise ValidationError("The request body is not valid JSON.")
        return data
    return {k: request.POST.get(k) for k in ap.USER_CHOICES if k in request.POST}


def _wants_json(request):
    return ("application/json" in request.headers.get("Accept", "")
            or (request.content_type or "").startswith("application/json"))


def my_preferences(request):
    """GET: the person's saved choices and what the page uses. POST: change
    one or more of theme, palette, motion, density ("" = site default)."""
    user = request.user
    if not user.is_authenticated:
        return JsonResponse({"error": "Sign in to save preferences."}, status=401)
    if request.method == "GET":
        r = ap.resolve(request)
        return JsonResponse({"preferences": user.preferences or {}, "effective": r["prefs"],
                             "allow_user_palette": r["site"]["allow_user_palette"]})
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])
    if getattr(request, "is_impersonating", False):
        return _pref_error(request, "Preferences cannot be changed while acting as someone else.", 403)
    try:
        data = _payload(request)
        site = ap.site_appearance()
        if not site["allow_user_palette"]:
            blocked = [k for k in data if k not in ("theme", "stamp")]
            if blocked:
                return _pref_error(request, "The site does not let staff choose their own palette, motion or density.",
                                   403)
        prefs = ap.save_user_preferences(user, data)
    except ValidationError as exc:
        return _pref_error(request, " ".join(exc.messages), 400)
    if request.headers.get("HX-Request"):
        return render(request, "accounts/_appearance_card.html", _card_context(request, saved=True))
    if _wants_json(request):
        return JsonResponse({"ok": True, "preferences": prefs})
    messages.success(request, "Appearance saved. It follows you to every device.")
    nxt = request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        nxt = "/"
    return redirect(nxt)


def _pref_error(request, message, status):
    if request.headers.get("HX-Request"):
        return render(request, "accounts/_appearance_card.html", _card_context(request, error=message))
    if _wants_json(request):
        return JsonResponse({"error": message}, status=status)
    messages.error(request, message)
    from core.urlsafe import safe_next

    return redirect(safe_next(request, request.POST.get("next"), "/"))


def _card_context(request, *, saved=False, error=""):
    r = ap.resolve(request)
    own = dict(request.user.preferences or {})
    return {
        "ap_site": r["site"],
        "ap_own": own,
        "ap_effective": r["prefs"],
        "ap_palettes": ap.PALETTES,
        "ap_themes": ap.THEMES,
        "ap_motion_levels": ap.MOTION_LEVELS,
        "ap_saved": saved,
        "ap_error": error,
    }


def appearance_card_context(request):
    """For the profile page (accounts.views.profile_view)."""
    return _card_context(request)
