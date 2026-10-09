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
}


def site_info(request):
    """Returns {'site_info': {department_name, organisation_name, tagline,
    email, phone, address, footer_note}} for every template."""
    info = {key: config.get(f"site.{key}", default) for key, default in _DEFAULTS.items()}
    return {"site_info": info}