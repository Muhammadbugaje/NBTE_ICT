"""v2.1 Phase B site-wide prompts: signature (X23) and bank details (X33)."""
from django import template

register = template.Library()

SKIP_VIEWS = {"documents:my_signature", "honoraria:my_bank", "core:verify_code"}


def _banners_for(user):
    """The two lookups (signature on file? bank details on file?) per user.
    Cached under the core.fastcache BANNERS namespace — those models plus the
    user are watched, so edits bump the cache instantly; TTL is the net."""
    from core.fastcache import BANNERS, cached

    def build():
        banners = []
        try:
            from config import registry as config
            from documents import signature

            if signature.active_for(user) is None:
                if signature.signature_required(user):
                    banners.append({"key": "sig-required", "tone": "warn",
                                    "url_name": "documents:my_signature",
                                    "title": "Add your signature",
                                    "text": "Directors and above must have a signature on file. Approvals that "
                                            "produce a document or memo wait until you add it.",
                                    "action": "Add my signature"})
                elif config.get("qa.signatures.remind_others", True):
                    banners.append({"key": "sig-remind", "tone": "info", "url_name": "documents:my_signature",
                                    "title": "Add your signature",
                                    "text": "Documents and memos your approvals generate will carry it.",
                                    "action": "Add signature", "dismissible": True})
        except Exception:  # noqa: BLE001
            pass
        try:
            from honoraria import services as bank

            b = bank.banner_for(user)
            if b is not None and b["kind"] == "add":
                banners.append({"key": "bank-add", "tone": "info", "url_name": "honoraria:my_bank",
                                "title": "Add your bank details",
                                "text": "NBTE pays SSQ reviews, visits and other payments into this account. "
                                        "Only you can change it; Finance views it only to pay you (logged).",
                                "action": "Add bank details", "dismissible": True})
            elif b is not None:
                banners.append({"key": "bank-confirm", "tone": "warn", "url_name": "honoraria:my_bank",
                                "title": "Are your bank details still correct?",
                                "text": f"Payments go to {b['summary']['bank']} {b['summary']['masked']} "
                                        f"({b['summary']['account_name']}).",
                                "action": "Change them", "confirm": True})
        except Exception:  # noqa: BLE001
            pass
        return banners

    # The key already carries the user id, so rows cannot leak between users.
    return cached(BANNERS, f"u{user.pk}", build, ttl=60)


@register.inclusion_tag("partials/v21_platform_banners.html", takes_context=True)
def platform_banners(context):
    request = context.get("request")
    user = getattr(request, "user", None)
    out = {"banners": [], "request": request}
    if (user is None or not user.is_authenticated or getattr(request, "is_impersonating", False)
            or getattr(user, "user_type", "staff") != "staff"):
        return out
    match = getattr(request, "resolver_match", None)
    if match is not None and match.view_name in SKIP_VIEWS:
        return out
    try:
        out["banners"] = _banners_for(user)
    except Exception:  # noqa: BLE001 — a cache outage must never break a page
        out["banners"] = []
    return out
