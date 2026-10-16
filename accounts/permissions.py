"""Actor-vs-target checks for user-management actions (review finding H3)."""


def rank_of(user):
    """Effective seniority: superusers 1000, otherwise the role's rank (0 if none)."""
    if user is None:
        return 0
    if getattr(user, "is_superuser", False):
        return 1000
    role = getattr(user, "role", None) if getattr(user, "role_id", None) else None
    return (role.rank or 0) if role is not None else 0


def can_act_on_user(actor, target):
    """May `actor` reset the password of, or (de)activate, `target`?

    * superusers may act on anyone
    * nobody else may act on a superuser
    * nobody else may act on someone who outranks them
    """
    if actor is None or target is None or not actor.is_authenticated:
        return False
    if actor.is_superuser:
        return True
    if target.is_superuser:
        return False
    return rank_of(target) <= rank_of(actor)
