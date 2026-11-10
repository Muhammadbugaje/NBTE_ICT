"""Who a user account or a role is for: NBTE staff vs the QA portal.

One user table holds NBTE staff, institution users, resource persons and
professional-body accounts (``User.user_type``). The internal system and the
QA portal are kept strictly apart, and this module is the single place that
says which side a user, a role or a list belongs to:

* **segments** — the User management sections and the "Act as user" lists
  (``nbte`` / ``institution`` / ``external``), each a server-side filter;
* **role audience** — Role has no audience column, so it is read from the
  role code convention the seeds already follow: ``inst-*`` roles are
  institution portal roles, ``pb-*`` professional-body roles,
  ``resource-person`` the resource-person role; every other role is an NBTE
  internal role. New portal roles must follow the same prefixes.
* **checks** — ``role_problem`` refuses a role whose audience does not match
  the user's type (used by org.assign_role, the user-management forms and the
  Data Hub import), so an NBTE internal role can never land on an
  institution account and vice versa.
"""
from __future__ import annotations

from django.db.models import Q

STAFF = "staff"
INSTITUTION = "institution"
RESOURCE_PERSON = "resource_person"
PROFESSIONAL_BODY = "professional_body"

# Role audiences (the user_type a role is meant for).
AUDIENCE_LABELS = {
    STAFF: "NBTE staff",
    INSTITUTION: "Institution users",
    RESOURCE_PERSON: "Resource persons",
    PROFESSIONAL_BODY: "Professional bodies",
}

ROLE_KIND = {STAFF: "an NBTE staff", INSTITUTION: "an institution portal",
             RESOURCE_PERSON: "a resource-person", PROFESSIONAL_BODY: "a professional-body"}
ACCOUNT_KIND = {STAFF: "an NBTE staff account", INSTITUTION: "an institution user",
                RESOURCE_PERSON: "a resource person", PROFESSIONAL_BODY: "a professional-body account"}

# User management / Act as segments.
NBTE, EXTERNAL = "nbte", "external"
SEGMENTS = {
    NBTE: {"label": "NBTE staff", "types": (STAFF,), "icon": "users"},
    INSTITUTION: {"label": "Institution users", "types": (INSTITUTION,), "icon": "building"},
    EXTERNAL: {"label": "Resource persons & professional bodies",
               "types": (RESOURCE_PERSON, PROFESSIONAL_BODY), "icon": "user"},
}


def segment_of_type(user_type):
    for key, seg in SEGMENTS.items():
        if user_type in seg["types"]:
            return key
    return NBTE


def segment_of(user):
    return segment_of_type(getattr(user, "user_type", STAFF))


def clean_segment(value, default=NBTE):
    return value if value in SEGMENTS else default


def segment_q(segment, prefix=""):
    """Q selecting the users of one segment (``prefix`` e.g. "user__")."""
    return Q(**{f"{prefix}user_type__in": SEGMENTS[clean_segment(segment)]["types"]})


def nbte_staff_records_q():
    """Staff directory rows that are NBTE staff: no account yet, or a staff
    account. (Older databases auto-created directory rows for portal accounts.)"""
    return Q(user__isnull=True) | Q(user__user_type=STAFF)


# ---------------------------------------------------------------------------
# NBTE staff only (every person picker that hands out NBTE work)
# ---------------------------------------------------------------------------
SYSTEM_ACCOUNT_EMAILS = ("qa-system@nbte.local",)  # qa_core.seed_data_phase4.SYSTEM_USER


def _portal_role_q(prefix=""):
    return (Q(**{f"{prefix}role__code__istartswith": "inst-"}) | Q(**{f"{prefix}role__code__istartswith": "pb-"})
            | Q(**{f"{prefix}role__code__iexact": "resource-person"}) | Q(**{f"{prefix}role__code__istartswith": "rp-"}))


def nbte_staff_users_q(prefix=""):
    """Q for active NBTE staff accounts only: ``user_type`` staff, not holding a
    portal role (institution / professional body / resource person), not a
    system account. ``prefix`` e.g. "user__" to filter Staff rows."""
    system = Q()
    for email in SYSTEM_ACCOUNT_EMAILS:
        system |= Q(**{f"{prefix}email__iexact": email})
    return (Q(**{f"{prefix}is_active": True, f"{prefix}user_type": STAFF})
            & ~Q(**{f"{prefix}pk__in": _portal_role_users()}) & ~system)


def _portal_role_users():
    from .models import User

    return User.objects.filter(_portal_role_q()).values("pk")


def is_nbte_staff(user):
    """True for an active NBTE staff account (see :func:`nbte_staff_users_q`)."""
    if user is None or not getattr(user, "is_active", False):
        return False
    if (getattr(user, "user_type", "") or "") != STAFF:
        return False
    if (getattr(user, "email", "") or "").lower() in SYSTEM_ACCOUNT_EMAILS:
        return False
    role = getattr(user, "role", None)
    return role is None or role_audience(role) == STAFF


def not_nbte_staff_reason(user, label="person"):
    """'' for an NBTE staff account, else a clear refusal for the save paths."""
    if is_nbte_staff(user):
        return ""
    name = ((user.get_full_name() or "").strip() or user.email) if user is not None else "That person"
    if user is not None and not getattr(user, "is_active", False):
        return f"{name} is not an active account — only NBTE staff can be chosen as the {label}."
    kind = ACCOUNT_KIND.get(getattr(user, "user_type", "") or STAFF, "not NBTE staff")
    if user is not None and getattr(user, "user_type", "") == STAFF:
        kind = "a system or portal account"
    return f"{name} is {kind} — only NBTE staff can be chosen as the {label}."


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
def role_audience(role):
    code = (getattr(role, "code", role) or "").lower()
    if code.startswith("inst-"):
        return INSTITUTION
    if code.startswith("pb-"):
        return PROFESSIONAL_BODY
    if code == "resource-person" or code.startswith("rp-"):
        return RESOURCE_PERSON
    return STAFF


def role_audience_label(role):
    return AUDIENCE_LABELS[role_audience(role)]


def role_matches(role, user_type):
    return role is None or role_audience(role) == (user_type or STAFF)


def role_problem(role, user_type):
    """'' when ``role`` may be held by a ``user_type`` account, else why not."""
    if role_matches(role, user_type):
        return ""
    role_kind = ROLE_KIND[role_audience(role)]
    who = ACCOUNT_KIND.get(user_type or STAFF, user_type)
    return f"“{role.name}” is {role_kind} role; it cannot be given to {who}."


def roles_for(user_type, qs=None):
    """Roles whose audience is ``user_type`` (a queryset)."""
    from .models import Role

    qs = Role.objects.all() if qs is None else qs
    inst, pb = Q(code__istartswith="inst-"), Q(code__istartswith="pb-")
    rp = Q(code__iexact="resource-person") | Q(code__istartswith="rp-")
    if user_type == INSTITUTION:
        return qs.filter(inst)
    if user_type == PROFESSIONAL_BODY:
        return qs.filter(pb)
    if user_type == RESOURCE_PERSON:
        return qs.filter(rp)
    return qs.exclude(inst | pb | rp)


def roles_for_segment(segment, qs=None):
    from .models import Role

    qs = Role.objects.all() if qs is None else qs
    ids = set()
    for t in SEGMENTS[clean_segment(segment)]["types"]:
        ids |= set(roles_for(t, qs).values_list("pk", flat=True))
    return qs.filter(pk__in=ids)
