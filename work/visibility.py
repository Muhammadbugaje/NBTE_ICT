"""Who may see and who may be given work (Master Plan v2.1 §9.3, C.1).

One rule for tasks, tickets and requests (H2, H4, H10, I1, T-CONS-6):

* **Involved** people always see an item: its creator / requester, its
  assignee(s), its reviewer (and, for subtasks, the people on the parent).
* Inside a **department** you also see the items handled by staff of a
  *lower rank* than you (rank = ``org.rank_of``).
* The **director** of a department sees every item of that department, and
  nothing of another department unless involved (H1).
* A **division head** sees the items routed to their division (H23).
* The **ES**, superusers and holders of the model's "view all" permission
  (``tasks.view_all_tasks`` ...) see everything (H3).
* An item that belongs to a department (``owner_department`` /
  ``handling_department`` ...) is visible outside that department only to the
  people involved in it.

Assignment (H4, H10): you may give work only to active staff of *your own*
department with a *lower* rank; the ES (and superusers) may assign anyone.
Work for another department goes through a Request (``work.bridge``).

Public API::

    visible_q(user, model)            -> Q to filter model's queryset
    visible(user, model_or_qs)        -> queryset filtered by visible_q
    can_see_all(user, model)          -> bool
    assignable_users(actor, dept=None) -> User queryset, annotated ``open_load``
    with_open_load(user_qs)           -> annotate ``open_load`` in ONE query
    load_info(user)                   -> {"open", "max", "remaining", "full", "label"}
    assign_problem(actor, assignee, dept=None) -> "" or the reason it is refused
    register(model_label, VisibilitySpec(...)) -> plug another model in

Every function reads the org resolver (``org.is_es``, ``org.rank_of``,
``org.is_director``); no inline role-code checks.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import Count, Q

__all__ = [
    "VisibilitySpec", "register", "spec_for", "visible_q", "visible", "can_see_all",
    "assignable_users", "with_open_load", "load_info", "assign_problem",
    "department_id_of", "is_override", "OPEN_ROW_EXCLUDE",
]


# ---------------------------------------------------------------------------
# Per-model field map
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VisibilitySpec:
    """How ``visible_q`` reads one model.

    ``department``          FK path of the item's department.
    ``department_fallback`` used when ``department`` is empty (e.g. the creator's department).
    ``involved``            user FK paths that always see the item (single or multi valued).
    ``handlers``            user FK paths whose rank decides "lower-ranked work".
    ``division``            FK path to org.Division (or "" when the model has none).
    ``view_all_perm``       "app.codename" that grants sight of every item.
    """

    department: str
    involved: tuple
    handlers: tuple
    department_fallback: str = ""
    division: str = ""
    view_all_perm: str = ""
    extra: tuple = field(default_factory=tuple)


_REGISTRY: dict[str, VisibilitySpec] = {}


def register(model_label: str, spec: VisibilitySpec) -> None:
    """Register (or replace) the spec of ``"app_label.ModelName"``."""
    _REGISTRY[model_label.lower()] = spec


def spec_for(model) -> VisibilitySpec:
    label = model._meta.label_lower
    try:
        return _REGISTRY[label]
    except KeyError:  # pragma: no cover - programming error
        raise LookupError(f"work.visibility: no VisibilitySpec registered for {label}") from None


# Defaults. tickets / workflow may re-register a finer spec from their apps.
register("tasks.task", VisibilitySpec(
    department="owner_department",
    department_fallback="created_by__department",
    involved=("created_by", "assignee_rows__user", "reviewer",
              "parent__created_by", "parent__assignee_rows__user"),
    handlers=("created_by", "assignee_rows__user"),
    division="division",
    view_all_perm="tasks.view_all_tasks",
))
register("tickets.ticket", VisibilitySpec(
    department="handling_department",
    department_fallback="raised_by__department",
    involved=("raised_by", "assigned_to", "resolved_by"),
    handlers=("raised_by", "assigned_to"),
    view_all_perm="tickets.view_all_tickets",
))
register("workflow.request", VisibilitySpec(
    department="receiving_department",
    department_fallback="requester__department",
    involved=("requester", "assigned_to"),
    handlers=("requester", "assigned_to"),
    view_all_perm="workflow.view_all_requests",
))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _authenticated(user) -> bool:
    return bool(user is not None and getattr(user, "is_authenticated", False)
                and getattr(user, "is_active", True))


def department_id_of(user):
    """The department pk of a user (account department; the staff record keeps
    it in sync), or None."""
    if user is None:
        return None
    dept_id = getattr(user, "department_id", None)
    if dept_id:
        return dept_id
    staff = getattr(user, "staff_profile", None) if getattr(user, "pk", None) else None
    return getattr(staff, "department_id", None)


def is_override(user) -> bool:
    """ES, superuser or super admin: sees and assigns across departments."""
    import org
    return bool(_authenticated(user) and (user.is_superuser or org.is_es(user) or org.is_super_admin(user)))


def can_see_all(user, model) -> bool:
    if not _authenticated(user):
        return False
    import org
    if user.is_superuser or org.is_es(user):
        return True
    perm = spec_for(model).view_all_perm
    return bool(perm and _perm_exists(perm) and user.has_perm(perm))


def _perm_exists(perm: str) -> bool:
    # has_perm on an unknown codename is simply False; kept as a hook.
    return "." in perm


def _lower_rank_q(path: str, rank: int) -> Q:
    """Items whose ``path`` user is set, is not a superuser and ranks below ``rank``."""
    lower = Q(**{f"{path}__role__rank__lt": rank})
    if rank > 0:
        lower |= Q(**{f"{path}__role__isnull": True})
    return Q(**{f"{path}__isnull": False}) & Q(**{f"{path}__is_superuser": False}) & lower


def _department_q(spec: VisibilitySpec, dept_id) -> Q:
    q = Q(**{f"{spec.department}_id": dept_id})
    if spec.department_fallback:
        q |= Q(**{f"{spec.department}__isnull": True}) & Q(**{f"{spec.department_fallback}_id": dept_id})
    return q


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------
def visible_q(user, model) -> Q:
    """A ``Q`` selecting the rows of ``model`` that ``user`` may see.

    It is a ``pk__in`` sub-query, so it never multiplies rows (no
    ``.distinct()`` needed) and can be combined with any other filter.
    """
    if not _authenticated(user):
        return Q(pk__in=[])
    if can_see_all(user, model):
        return Q()
    spec = spec_for(model)
    import org

    q = Q()
    for path in spec.involved:
        q |= Q(**{path: user})

    dept_id = department_id_of(user)
    if dept_id:
        in_dept = _department_q(spec, dept_id)
        if org.is_director(user, dept_id):
            q |= in_dept  # H1: the director sees their whole department, nothing else
        else:
            rank = org.rank_of(user)
            lower = Q()
            for path in spec.handlers:
                lower |= _lower_rank_q(path, rank)
            if lower:
                q |= in_dept & lower

    if spec.division:
        q |= Q(**{f"{spec.division}__head": user, f"{spec.division}__is_active": True})
    for extra in spec.extra:
        q |= extra(user) if callable(extra) else extra

    manager = getattr(model, "all_objects", None) or model._default_manager
    return Q(pk__in=manager.filter(q).values("pk"))


def visible(user, model_or_qs):
    """``model_or_qs`` (a model class or queryset) narrowed to what ``user`` sees."""
    if hasattr(model_or_qs, "model") and hasattr(model_or_qs, "filter"):
        qs, model = model_or_qs, model_or_qs.model
    else:
        model = model_or_qs
        qs = model._default_manager.all()
    q = visible_q(user, model)
    return qs if not q else qs.filter(q)


# ---------------------------------------------------------------------------
# Load (T-PERF-1)
# ---------------------------------------------------------------------------
def _open_rows_filter():
    from tasks.models import Task, TaskAssignee
    return (~Q(task_assignments__state__in=[TaskAssignee.DONE, TaskAssignee.DECLINED])
            & ~Q(task_assignments__task__state__in=list(Task.CLOSED_STATES))
            & Q(task_assignments__isnull=False))


OPEN_ROW_EXCLUDE = "done/declined rows and closed tasks"


def with_open_load(user_qs):
    """Annotate ``open_load`` (open task assignments per person) in one query."""
    return user_qs.annotate(open_load=Count("task_assignments", filter=_open_rows_filter(), distinct=True))


def _default_max():
    try:
        import config as _cfg
        return int(_cfg.get("tasks.default_max_open", 5))
    except Exception:
        return 5


def load_info(user, default_max=None) -> dict:
    """Open load and capacity of one person. Uses the ``open_load`` annotation
    when present (no query), else counts."""
    used = getattr(user, "open_load", None)
    if used is None:
        from django.contrib.auth import get_user_model
        used = with_open_load(get_user_model().objects.filter(pk=user.pk)).values_list(
            "open_load", flat=True).first() or 0
    role = user.role if getattr(user, "role_id", None) else None
    cap = (role.max_open_tasks if role is not None else 0) or (default_max or _default_max())
    remaining = max(0, cap - used)
    full = remaining <= 0
    return {"open": used, "max": cap, "remaining": remaining, "full": full,
            "label": f"Full {used}/{cap}" if full else f"{used} open"}


# ---------------------------------------------------------------------------
# Assignment (H4, H10)
# ---------------------------------------------------------------------------
def assignable_users(actor, dept=None, *, annotate_load=True):
    """Active staff ``actor`` may give work to, most senior first, annotated
    with ``open_load``.

    * ES / superuser / super admin: anyone (optionally narrowed to ``dept``);
    * everyone else: staff of their own department with a lower rank. Asking
      for another department returns nobody (that needs a Request, H11).
    The ES is never offered as an assignee.
    """
    from accounts.selectors import staff_users
    import org

    qs = staff_users().select_related("role", "department")
    if not _authenticated(actor):
        return qs.none()
    dept_id = getattr(dept, "pk", dept) if dept is not None else None
    qs = qs.exclude(org.role_q(org.ES_ROLE))
    if is_override(actor):
        if dept_id:
            qs = qs.filter(department_id=dept_id)
    else:
        own = department_id_of(actor)
        if not own or (dept_id and dept_id != own):
            return qs.none()
        rank = org.rank_of(actor)
        if rank <= 0:
            return qs.none()
        qs = qs.filter(department_id=own, is_superuser=False).filter(
            Q(role__rank__lt=rank) | Q(role__isnull=True))
    if annotate_load:
        qs = with_open_load(qs)
    return qs.order_by("-role__rank", "first_name", "last_name", "email")


def assign_problem(actor, assignee, dept=None) -> str:
    """Why ``actor`` may not give work to ``assignee`` ("" when allowed)."""
    import org

    if assignee is None or not assignee.is_active:
        return "Invalid assignee."
    if getattr(assignee, "user_type", "staff") != "staff":
        return "Only NBTE staff can be assigned work."
    if org.is_es(assignee):
        return "The Executive Secretary cannot be assigned work."
    name = assignee.get_full_name() or assignee.email
    dept_id = getattr(dept, "pk", dept) if dept is not None else None
    if is_override(actor):
        if dept_id and department_id_of(assignee) != dept_id:
            return f"{name} is not in this item's department."
        return ""
    own = department_id_of(actor)
    if not own or department_id_of(assignee) != own:
        return (f"{name} is in another department. Work for another department "
                "goes through a request to that department.")
    if dept_id and dept_id != own:
        return "This item belongs to another department; only its own staff can be assigned."
    if org.rank_of(actor) <= org.rank_of(assignee):
        return f"You cannot assign work to {name}: their rank is equal to or higher than yours."
    return ""
