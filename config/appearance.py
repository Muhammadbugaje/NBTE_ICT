"""Appearance engine (v2.1 Phase A, A9/A10; MASTER_PLAN §5.3, §8.6).

One place for the choices, the colour validator and the resolution of the
site defaults (appearance.* settings) and a user's own preferences
(`User.preferences`). Used by the context processor, Settings -> Appearance,
the profile Appearance card, POST /me/preferences/ and the API.
"""
import re
import time

from django.core.exceptions import ValidationError

import config

PALETTES = [
    # code, name, note, light primary, accent
    ("green", "NBTE Green", "Official brand", "#1f6b3e", "#c9961a"),
    ("blue", "Royal Blue", "Calm, formal", "#1d4ed8", "#f0c75e"),
    ("gold", "Sahel Gold", "Warm, with green", "#9a6206", "#2f8f57"),
    ("teal", "Ocean Teal", "Fresh", "#0d7377", "#f6c06a"),
    ("plum", "Plum", "Rich", "#6d2a85", "#f2bd5c"),
    ("graphite", "Graphite", "Neutral", "#2b3138", "#e0b43a"),
]
PALETTE_CODES = [p[0] for p in PALETTES] + ["custom"]
SCENES = [
    ("laminar", "Laminar", "Soft ribbons drifting"),
    ("komorebi", "Komorebi", "Dappled sunlight"),
    ("quicksilver", "Quicksilver", "Liquid metal sheen"),
    ("blinds", "Glass blinds", "Still, very light"),
    ("none", "None", "Plain gradient"),
]
SCENE_CODES = [s[0] for s in SCENES]
MOTION_LEVELS = [
    ("full", "Full", "Scenes flow, cards rise in, numbers count up"),
    ("reduced", "Reduced", "Short fades only; scenes hold still"),
    ("off", "Off", "Nothing moves; best on very old phones"),
]
MOTION_CODES = [m[0] for m in MOTION_LEVELS]
DENSITIES = [("comfortable", "Comfortable"), ("compact", "Compact")]
DENSITY_CODES = [d[0] for d in DENSITIES]
THEMES = [("light", "Light"), ("dark", "Dark"), ("system", "Match device")]
THEME_CODES = [t[0] for t in THEMES]

SITE_DEFAULTS = {
    "palette": "green",
    "custom_primary": "#0e7490",
    "custom_accent": "#f59e0b",
    "theme": "system",
    "scene": "laminar",
    "motion": "full",
    "density": "comfortable",
    "allow_user_palette": True,
    "portal_follows_site": True,
    "button_gap": 8,   # X52 (config/defaults.py): px between buttons in a .btn-group
}
# Keys a user may set for themselves, with their allowed values. "system" for
# motion means "follow the site and the device" (the key is dropped).
USER_CHOICES = {
    "theme": THEME_CODES,
    "palette": [p[0] for p in PALETTES],
    "motion": MOTION_CODES + ["system"],
    "density": DENSITY_CODES,
}
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
MIN_CONTRAST = 4.5


# ---------------------------------------------------------------------------
# Colour validator (registered on ConfigEntry for appearance.custom_*)
# ---------------------------------------------------------------------------
def _luminance(hex_value):
    def channel(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    n = int(hex_value[1:], 16)
    r, g, b = (n >> 16) & 255, (n >> 8) & 255, n & 255
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(a, b="#ffffff"):
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)


def validate_hex(value):
    value = (value or "").strip()
    if not HEX_RE.match(value):
        raise ValidationError("Use a colour in the form #rrggbb (for example #0e7490).")
    return value.lower()


def validate_primary_colour(value):
    value = validate_hex(value)
    ratio = contrast_ratio(value)
    if ratio < MIN_CONTRAST:
        raise ValidationError(
            f"White text on {value} has a contrast ratio of {ratio}:1; it needs at least {MIN_CONTRAST}:1. "
            "Pick a darker colour.")
    return value


KEY_VALIDATORS = {
    "appearance.custom_primary": validate_primary_colour,
    "appearance.custom_accent": validate_hex,
    "appearance.palette": lambda v: _choice(v, PALETTE_CODES, "palette"),
    "appearance.scene": lambda v: _choice(v, SCENE_CODES, "scene"),
    "appearance.theme": lambda v: _choice(v, THEME_CODES, "theme"),
    "appearance.motion": lambda v: _choice(v, MOTION_CODES, "motion level"),
    "appearance.density": lambda v: _choice(v, DENSITY_CODES, "density"),
}
# The form also checks the button gap (X52 keeps an out-of-range stored value
# and falls back to 8px at render time, so it is not a model validator).
FORM_VALIDATORS = {**KEY_VALIDATORS, "appearance.button_gap": lambda v: _gap(v)}


def _choice(value, allowed, label):
    if value not in allowed:
        raise ValidationError(f"Unknown {label} {value!r}. Choose one of: {', '.join(allowed)}.")
    return value


def _gap(value):
    try:
        n = int(str(value).strip())
    except ValueError:
        raise ValidationError("Button gap must be a whole number of pixels.")
    if not 6 <= n <= 24:
        raise ValidationError("Button gap must be between 6 and 24 pixels.")
    return str(n)


def validate_entry(entry):
    """ConfigEntry key validator (config.models.register_key_validator)."""
    func = KEY_VALIDATORS.get(entry.key)
    if func is None:
        return
    try:
        func((entry.value or "").strip())
    except ValidationError as exc:
        raise ValidationError({"value": exc.messages})


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
def site_appearance():
    """The site defaults from appearance.* settings (safe on a fresh DB)."""
    out = {}
    for key, default in SITE_DEFAULTS.items():
        try:
            value = config.get(f"appearance.{key}", default)
        except Exception:  # registry not ready
            value = default
        out[key] = value
    # Never let an invalid stored value reach the page (the <style> tag).
    if out["palette"] not in PALETTE_CODES:
        out["palette"] = "green"
    for key in ("custom_primary", "custom_accent"):
        if not isinstance(out[key], str) or not HEX_RE.match(out[key].strip()):
            out[key] = SITE_DEFAULTS[key]
        out[key] = out[key].strip().lower()
    for key, allowed in (("scene", SCENE_CODES), ("motion", MOTION_CODES), ("density", DENSITY_CODES),
                         ("theme", THEME_CODES)):
        if out[key] not in allowed:
            out[key] = SITE_DEFAULTS[key]
    try:
        gap = int(out["button_gap"])
    except (TypeError, ValueError):
        gap = 8
    out["button_gap"] = gap if 6 <= gap <= 24 else 8
    try:
        out["legacy_css"] = bool(config.get("ui.legacy_css", False))
    except Exception:
        out["legacy_css"] = False
    return out


def clean_preferences(data, *, partial=True, current=None):
    """Validate a user's preference payload. Returns the new dict to store.
    An empty string, None or "site" removes a key (back to the site default);
    motion "system" also removes it. Raises ValidationError with a message."""
    if not isinstance(data, dict):
        raise ValidationError("Send an object such as {\"palette\": \"blue\"}.")
    prefs = dict(current or {}) if partial else {}
    unknown = [k for k in data if k not in USER_CHOICES and k != "stamp"]
    if unknown:
        raise ValidationError(f"Unknown preference(s): {', '.join(sorted(unknown))}.")
    for key, value in data.items():
        if key == "stamp":
            continue
        if value in (None, "", "site") or (key == "motion" and value == "system"):
            prefs.pop(key, None)
            continue
        if value not in USER_CHOICES[key]:
            raise ValidationError(f"{key}: {value!r} is not one of {', '.join(USER_CHOICES[key])}.")
        prefs[key] = value
    prefs = {k: v for k, v in prefs.items() if k in USER_CHOICES}
    prefs["stamp"] = int(time.time() * 1000)
    return prefs


def save_user_preferences(user, data, *, partial=True):
    prefs = clean_preferences(data, partial=partial, current=user.preferences or {})
    user.preferences = prefs
    user.save(update_fields=["preferences"])
    return prefs


def portal_request(request):
    try:
        from qa_core.hosts import is_portal_request
        return bool(is_portal_request(request))
    except Exception:
        return False


def resolve(request):
    """(site, prefs) for templates. `prefs` is what theme-init.js reads as
    window.NBTE_PREFS: site defaults with the user's own choices on top (when
    the site allows them), plus `stamp` and `lock`."""
    site = site_appearance()
    user = getattr(request, "user", None) if request is not None else None
    palette = site["palette"]
    # Institutions on the portal keep NBTE Green unless the site says otherwise.
    if (request is not None and not site["portal_follows_site"] and user is not None
            and getattr(user, "is_authenticated", False) and getattr(user, "user_type", "staff") != "staff"):
        palette = "green"
    elif (request is not None and not site["portal_follows_site"] and portal_request(request)
          and not (user is not None and getattr(user, "is_authenticated", False)
                   and getattr(user, "user_type", "") == "staff")):
        palette = "green"
    prefs = {"theme": site["theme"], "palette": palette, "scene": site["scene"], "motion": site["motion"], "density": site["density"],
             "site": {"palette": palette, "motion": site["motion"], "density": site["density"]}}
    own = {}
    if user is not None and getattr(user, "is_authenticated", False):
        stored = getattr(user, "preferences", None) or {}
        if isinstance(stored, dict):
            own = {k: v for k, v in stored.items() if k in USER_CHOICES and v in USER_CHOICES[k]}
            if not site["allow_user_palette"]:
                own = {k: v for k, v in own.items() if k == "theme"}
            prefs.update(own)
            if isinstance(stored.get("stamp"), int):
                prefs["stamp"] = stored["stamp"]
    # Site motion is a ceiling: a user can ask for less motion, never more.
    order = {"full": 0, "reduced": 1, "off": 2}
    if order.get(prefs.get("motion"), 0) < order[site["motion"]]:
        prefs["motion"] = site["motion"]
    if not site["allow_user_palette"]:
        prefs["lock"] = True
    custom = None
    if prefs["palette"] == "custom":
        custom = {"primary": site["custom_primary"], "accent": site["custom_accent"]}
    return {"site": site, "prefs": prefs, "own": own, "custom": custom}
