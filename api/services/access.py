"""Who may use which door (shared by token issue and the permission chain)."""


def portal_block_reason(user):
    """Message when an institution account may not use the portal
    (institution delisted or portal access switched off - same rule as
    qa_core.portal_views._access_blocked), else None."""
    if getattr(user, "user_type", "") != "institution":
        return None
    from qa_core.portal_views import _institution_for

    institution = _institution_for(user)
    if institution is not None and institution.portal_enabled and institution.status != "delisted":
        return None
    return "Portal access for your institution is switched off. Contact NBTE for help."
