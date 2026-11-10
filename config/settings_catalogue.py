"""v2.1 Phase A (A9, A10, V7) appearance and UI settings.

Seeded by `seed_nbte` (set `settings` reads every `<app>/settings_catalogue.py`
SETTINGS list); edited on Settings -> Appearance, which saves through
`qa_core.settings_service.save_setting` (permission, audit log, optional dual
control). Columns: key, value, type, group, description, editable_by, dual.

Choices and validators live in `config.appearance` so the settings page, the
`/me/preferences/` endpoint and the context processor share one source.
"""

G_APPEARANCE = "Appearance"
EDIT = "config.change_configentry"

SETTINGS = [
    ("appearance.palette", "green", "str", G_APPEARANCE,
     "Site colour palette: green, blue, gold, teal, plum, graphite or custom.", EDIT, False),
    ("appearance.custom_primary", "#0e7490", "str", G_APPEARANCE,
     "Custom palette primary colour (#rrggbb; contrast with white at least 4.5:1).", EDIT, False),
    ("appearance.custom_accent", "#f59e0b", "str", G_APPEARANCE,
     "Custom palette accent colour (#rrggbb).", EDIT, False),
    ("appearance.theme", "system", "str", G_APPEARANCE,
     "Light or dark for people who have not chosen: light, dark or system (match the device).", EDIT, False),
    ("appearance.scene", "laminar", "str", G_APPEARANCE,
     "Hero scene behind the homepage headline and dashboard greeting: laminar, komorebi, quicksilver, blinds, none.",
     EDIT, False),
    ("appearance.motion", "full", "str", G_APPEARANCE,
     "Site motion level: full, reduced or off. Devices set to reduce motion always get the still version.",
     EDIT, False),
    ("appearance.density", "comfortable", "str", G_APPEARANCE,
     "Layout density: comfortable or compact.", EDIT, False),
    ("appearance.allow_user_palette", "true", "bool", G_APPEARANCE,
     "Let staff pick their own palette, light/dark, motion and density (saved to their profile).", EDIT, False),
    ("appearance.portal_follows_site", "true", "bool", G_APPEARANCE,
     "Institutions on the QA portal see the site palette. Off keeps institutions on NBTE Green.", EDIT, False),
    ("ui.legacy_css", "false", "bool", G_APPEARANCE,
     "Fallback: load the pre-v2.1 stylesheets one by one instead of the layered bundles "
     "(for one release, in case a page looks wrong after the CSS consolidation).", EDIT, False),
]
