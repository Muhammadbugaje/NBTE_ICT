"""Template context: makes public site info (contact, names) available to
every template without each view having to fetch it.

Reads from the config registry (keys prefixed `site.`) with sensible defaults,
so templates never crash before seed_config has been run.
"""
import config

_DEFAULTS = {
    "department_name": "ICT Directorate",
    "organisation_name": "National Board for Technical Education",
    "tagline": "",
    "email": "",
    "phone": "",
    "address": "",
    "footer_note": "Authorized access only. All actions logged and audited.",
    # Phase 8 UI: names shown by the templates (Settings → Site).
    "name": "",
    "qa_name": "Quality Assurance",
    "portal_name": "NBTE Quality Assurance Portal",
    "portal_short_name": "Quality Assurance Portal",
}


def site_info(request):
    """Returns {'site_info': {department_name, organisation_name, tagline,
    email, phone, address, footer_note}} for every template."""
    info = {key: config.get(f"site.{key}", default) for key, default in _DEFAULTS.items()}
    for key, default in _DEFAULTS.items():  # a blank value falls back to the default
        if not str(info.get(key) or "").strip() and default:
            info[key] = default
    # v2.1 X52: spacing between buttons in a .btn-group (Settings → appearance.button_gap).
    try:
        gap = int(config.get("appearance.button_gap", 8))
    except (TypeError, ValueError):
        gap = 8
    info["button_gap"] = gap if 6 <= gap <= 24 else 8
    return {"site_info": info}


def appearance(request):
    """v2.1 A9/A10: site appearance (appearance.* settings) and the resolved
    preferences that theme-init.js applies before the first paint.

    * `appearance`        site defaults (palette, scene, motion, density, …)
    * `nbte_prefs`        printed with json_script as #nbte-prefs
    * `appearance_custom` validated custom pair, or None
    * `appearance_own`    the signed-in user's own saved choices
    """
    from .appearance import resolve

    try:
        r = resolve(request)
    except Exception:  # never break a page over appearance
        from .appearance import SITE_DEFAULTS

        return {"appearance": dict(SITE_DEFAULTS, legacy_css=False), "nbte_prefs": {"palette": "green"},
                "appearance_custom": None, "appearance_own": {}}
    return {"appearance": r["site"], "nbte_prefs": r["prefs"], "appearance_custom": r["custom"],
            "appearance_own": r["own"]}
