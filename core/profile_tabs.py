"""My profile tabs registry (v2.1 Phase B).

Several apps add a section to the person's OWN profile (My profile): the
documents app adds "Signature", honoraria adds "Bank details". Each app
registers its tab in ``AppConfig.ready()``; the shared partial
``partials/profile_tabs.html`` (``{% profile_tabs "key" %}``) renders the
strip, so no app edits another app's template.

    from core.profile_tabs import register
    register("signature", "Signature", "documents:my_signature", order=20,
             visible=lambda user: user.user_type == "staff",
             badge=lambda user: "Required" if ... else "")
"""
from dataclasses import dataclass
from typing import Callable, Optional

from django.urls import NoReverseMatch, reverse


@dataclass
class ProfileTab:
    key: str
    label: str
    url_name: str
    order: int = 100
    visible: Optional[Callable] = None
    badge: Optional[Callable] = None


_TABS = {}


def register(key, label, url_name, *, order=100, visible=None, badge=None):
    _TABS[key] = ProfileTab(key, label, url_name, order, visible, badge)


def tabs_for(user, active=""):
    out = []
    for tab in sorted(_TABS.values(), key=lambda t: (t.order, t.key)):
        try:
            if tab.visible and not tab.visible(user):
                continue
            url = reverse(tab.url_name)
        except NoReverseMatch:
            continue
        badge = ""
        if tab.badge:
            try:
                badge = tab.badge(user) or ""
            except Exception:
                badge = ""
        out.append({"key": tab.key, "label": tab.label, "url": url, "active": tab.key == active, "badge": badge})
    return out


register("profile", "Profile", "accounts:profile", order=0)
