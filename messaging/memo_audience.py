"""Who a memo reaches beyond the classic staff targets (v2.1 Phase E).

Institutions (M4, M5) - external and general memos:
    INSTITUTION_FILTER_KEYS
    parse_institution_filter(data)      -> dict (clean; from a QueryDict or dict)
    institutions_matching(flt)          -> Institution queryset (filter builder)
    memo_institutions(memo)             -> Institution queryset (all / selected / filter)
    describe_institution_filter(flt)    -> "Polytechnics with more than 5 programmes"
    institution_filter_options()        -> lookups for the builder

Staff (M6) - visibility "staff_filter":
    parse_staff_filter(data)            -> dict
    staff_matching(flt, exclude=None)   -> User queryset
    describe_staff_filter(flt)          -> plain-language summary
    staff_filter_options()              -> lookups for the builder

Delisted institutions never receive memos. Programme counts leave out
dropped and denied programmes ("current programmes").
"""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

# ---------------------------------------------------------------------------
# Institutions
# ---------------------------------------------------------------------------
INSTITUTION_FILTER_KEYS = ("types", "categories", "ownerships", "zones", "states", "statuses",
                           "new_only", "min_programmes", "accreditation", "q")
LIST_KEYS = ("types", "categories", "ownerships", "zones", "states", "statuses")
NOT_COUNTED_PROGRAMME_STATUSES = ("dropped", "denied")
ACCREDITATION_CHOICES = [("", "Any"), ("accredited", "Has an accredited programme"),
                         ("none", "No accredited programme yet")]
ZONE_CHOICES = [("NC", "North Central"), ("NE", "North East"), ("NW", "North West"),
                ("SE", "South East"), ("SS", "South South"), ("SW", "South West")]
STATUS_CHOICES = [("approved", "Approved"), ("proposed", "Proposed"), ("under_review", "Under review"),
                  ("suspended", "Suspended")]


def _cfg_int(key, default):
    try:
        import config
        return int(config.get(key, default) or default)
    except Exception:
        return default


def new_institution_days():
    return _cfg_int("memo.institution_new_days", 365)


def _getlist(data, key):
    if hasattr(data, "getlist"):
        vals = data.getlist(key) or data.getlist(f"if_{key}")
    else:
        vals = data.get(key) or data.get(f"if_{key}") or []
    if isinstance(vals, str):
        vals = [vals]
    return [str(v).strip() for v in vals if str(v).strip()]


def _get(data, key):
    v = data.get(key, None)
    if v is None:
        v = data.get(f"if_{key}", None)
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    return v


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "on", "yes") if v is not None else False


def parse_institution_filter(data):
    """A clean filter dict from form data (keys may carry an ``if_`` prefix)."""
    data = data or {}
    out = {}
    for key in LIST_KEYS:
        vals = _getlist(data, key)
        if key == "states":
            vals = [int(v) for v in vals if v.isdigit()]
        if vals:
            out[key] = sorted(set(vals))
    if _truthy(_get(data, "new_only")):
        out["new_only"] = True
    raw = _get(data, "min_programmes")
    try:
        n = int(str(raw).strip()) if raw not in (None, "") else None
    except ValueError:
        n = None
    if n is not None and n >= 0:
        out["min_programmes"] = n
    acc = str(_get(data, "accreditation") or "").strip()
    if acc in dict(ACCREDITATION_CHOICES) and acc:
        out["accreditation"] = acc
    q = str(_get(data, "q") or "").strip()[:80]
    if q:
        out["q"] = q
    return out


def _base_institutions():
    from institutions.models import Institution
    # System code: the memo audience is decided by the ES, never by the viewer's scope.
    return Institution.unscoped.exclude(status=Institution.DELISTED).filter(delisted_at__isnull=True)


def institutions_matching(flt):
    """The filter builder's result (M5). Every filter narrows (AND); values
    inside one filter widen (OR)."""
    from institutions.models import Programme

    flt = flt or {}
    qs = _base_institutions()
    if flt.get("types"):
        qs = qs.filter(institution_type__code__in=flt["types"])
    if flt.get("categories"):
        qs = qs.filter(category__code__in=flt["categories"])
    if flt.get("ownerships"):
        qs = qs.filter(ownership__code__in=flt["ownerships"])
    if flt.get("zones"):
        qs = qs.filter(zone__in=flt["zones"])
    if flt.get("states"):
        qs = qs.filter(state_id__in=flt["states"])
    if flt.get("statuses"):
        qs = qs.filter(status__in=flt["statuses"])
    if flt.get("new_only"):
        since = timezone.now() - timedelta(days=new_institution_days())
        qs = qs.filter(Q(approved_at__gte=since) | Q(approved_at__isnull=True, created_at__gte=since))
    if flt.get("min_programmes") is not None:
        qs = qs.annotate(_progs=Count("programmes", filter=~Q(
            programmes__status__in=NOT_COUNTED_PROGRAMME_STATUSES), distinct=True)).filter(
            _progs__gt=flt["min_programmes"])
    acc = flt.get("accreditation")
    if acc:
        accredited = Programme.unscoped.filter(institution=OuterRef("pk"), status="accredited")
        qs = qs.filter(Exists(accredited)) if acc == "accredited" else qs.exclude(Exists(accredited))
    if flt.get("q"):
        qs = qs.filter(Q(name__icontains=flt["q"]) | Q(code__iexact=flt["q"]))
    return qs.order_by("name")


def target_institution_ids(memo):
    """Ids picked for scope = selected (read through the M2M table: the
    Institution default manager is scoped to a viewer)."""
    if not getattr(memo, "pk", None):
        return []
    through = memo.__class__.target_institutions.through
    return list(through.objects.filter(memo_id=memo.pk).values_list("institution_id", flat=True))


def set_target_institutions(memo, institutions):
    through = memo.__class__.target_institutions.through
    ids = {getattr(i, "pk", i) for i in (institutions or [])}
    through.objects.filter(memo_id=memo.pk).exclude(institution_id__in=ids).delete()
    have = set(through.objects.filter(memo_id=memo.pk).values_list("institution_id", flat=True))
    through.objects.bulk_create([through(memo_id=memo.pk, institution_id=i) for i in ids - have])


def memo_institutions(memo):
    """The institutions an external / general memo goes to."""
    if memo.kind not in ("external", "general"):
        return _base_institutions().none()
    scope = memo.institution_scope
    if scope == "all":
        return _base_institutions().order_by("name")
    if scope == "selected":
        if not memo.pk:
            return _base_institutions().none()
        ids = target_institution_ids(memo)
        return _base_institutions().filter(pk__in=ids).order_by("name")
    if scope == "filter":
        return institutions_matching(memo.institution_filter or {})
    return _base_institutions().none()


def _names(model, codes, field="code"):
    rows = model.objects.filter(**{f"{field}__in": codes}).values_list("name", flat=True)
    return list(rows)


def _join(words, last="or"):
    words = [w for w in words if w]
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + f" {last} " + words[-1]


def _plural(name):
    low = name.lower()
    if low.endswith("y") and not low.endswith("ey"):
        return name[:-1] + "ies"
    if low.endswith("s"):
        return name
    return name + "s"


def describe_institution_filter(flt):
    """Plain-language summary, e.g. 'Polytechnics with more than 5 programmes'."""
    from institutions.models import InstitutionCategory, InstitutionOwnership, InstitutionType
    from staff.models import StateOfOrigin

    flt = flt or {}
    noun = "Institutions"
    if flt.get("types"):
        noun = _join([_plural(n) for n in _names(InstitutionType, flt["types"])])
    elif flt.get("categories"):
        noun = _join([_plural(n) for n in _names(InstitutionCategory, flt["categories"])])
    parts = []
    if flt.get("ownerships"):
        noun = _join(_names(InstitutionOwnership, flt["ownerships"])) + " " + noun[:1].lower() + noun[1:]
    if flt.get("new_only"):
        noun = "New " + noun[:1].lower() + noun[1:]
    if flt.get("min_programmes") is not None:
        parts.append(f"with more than {flt['min_programmes']} programme{'s' if flt['min_programmes'] != 1 else ''}")
    if flt.get("accreditation") == "accredited":
        parts.append("with an accredited programme")
    elif flt.get("accreditation") == "none":
        parts.append("with no accredited programme yet")
    if flt.get("zones"):
        zones = dict(ZONE_CHOICES)
        parts.append("in " + _join([zones.get(z, z) for z in flt["zones"]]))
    if flt.get("states"):
        parts.append("in " + _join(list(StateOfOrigin.objects.filter(pk__in=flt["states"])
                                        .values_list("name", flat=True))) + " State")
    if flt.get("statuses"):
        st = dict(STATUS_CHOICES)
        parts.append("(" + _join([st.get(s, s).lower() for s in flt["statuses"]]) + ")")
    if flt.get("q"):
        parts.append(f"matching “{flt['q']}”")
    return " ".join([noun] + parts) if (parts or noun != "Institutions") else "All institutions"


def institution_filter_options():
    from institutions.models import InstitutionCategory, InstitutionOwnership, InstitutionType
    from staff.models import StateOfOrigin

    return {
        "types": list(InstitutionType.objects.filter(is_active=True).order_by("sort_order", "name")
                      .values_list("code", "name")),
        "categories": list(InstitutionCategory.objects.filter(is_active=True).order_by("sort_order", "name")
                           .values_list("code", "name")),
        "ownerships": list(InstitutionOwnership.objects.filter(is_active=True).order_by("sort_order", "name")
                           .values_list("code", "name")),
        "zones": ZONE_CHOICES,
        "states": list(StateOfOrigin.objects.order_by("name").values_list("pk", "name")),
        "statuses": STATUS_CHOICES,
        "accreditation": ACCREDITATION_CHOICES,
        "new_days": new_institution_days(),
    }


# ---------------------------------------------------------------------------
# Staff (M6)
# ---------------------------------------------------------------------------
STAFF_LIST_KEYS = ("departments", "designations", "roles")


def parse_staff_filter(data):
    data = data or {}
    out = {}
    for key in STAFF_LIST_KEYS:
        if hasattr(data, "getlist"):
            vals = data.getlist(f"sf_{key}") or data.getlist(key)
        else:
            vals = data.get(f"sf_{key}") or data.get(key) or []
        if isinstance(vals, str):
            vals = [vals]
        vals = [str(v).strip() for v in vals if str(v).strip()]
        if key in ("departments", "designations"):
            vals = [int(v) for v in vals if v.isdigit()]
        if vals:
            out[key] = sorted(set(vals))
    for key in ("grade_min", "grade_max"):
        raw = data.get(f"sf_{key}", data.get(key))
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        try:
            n = int(str(raw).strip()) if raw not in (None, "") else None
        except ValueError:
            n = None
        if n is not None:
            out[key] = n
    return out


def staff_matching(flt, *, exclude=None):
    """Active staff matching every given filter (M6). An empty filter
    matches nobody, so 'staff matching filters' never means everyone."""
    from accounts.selectors import staff_users

    flt = flt or {}
    qs = staff_users()
    if exclude is not None:
        qs = qs.exclude(pk=getattr(exclude, "pk", exclude))
    if not any(flt.get(k) not in (None, [], "") for k in STAFF_LIST_KEYS + ("grade_min", "grade_max")):
        return qs.none()
    if flt.get("departments"):
        qs = qs.filter(Q(department_id__in=flt["departments"])
                       | Q(staff_profile__department_id__in=flt["departments"]))
    if flt.get("designations"):
        qs = qs.filter(staff_profile__designation_id__in=flt["designations"])
    if flt.get("roles"):
        qs = qs.filter(role__code__in=flt["roles"])
    if flt.get("grade_min") is not None:
        qs = qs.filter(staff_profile__grade__number__gte=flt["grade_min"])
    if flt.get("grade_max") is not None:
        qs = qs.filter(staff_profile__grade__number__lte=flt["grade_max"])
    return qs.distinct()


def describe_staff_filter(flt):
    from accounts.models import Role
    from staff.models import Department, Designation

    flt = flt or {}
    parts = []
    if flt.get("departments"):
        parts.append(_join(list(Department.objects.filter(pk__in=flt["departments"])
                                .values_list("name", flat=True))))
    if flt.get("roles"):
        parts.append(_join(list(Role.objects.filter(code__in=flt["roles"]).values_list("name", flat=True))))
    if flt.get("designations"):
        parts.append(_join(list(Designation.objects.filter(pk__in=flt["designations"])
                                .values_list("name", flat=True))))
    lo, hi = flt.get("grade_min"), flt.get("grade_max")
    if lo is not None and hi is not None:
        parts.append(f"grade levels {lo}–{hi}")
    elif lo is not None:
        parts.append(f"grade level {lo} and above")
    elif hi is not None:
        parts.append(f"grade level {hi} and below")
    return "Staff: " + "; ".join(parts) if parts else "Staff matching filters"


def staff_filter_options():
    from accounts.models import Role
    from org.models import GradeLevel
    from staff.models import Department, Designation

    return {
        "departments": list(Department.objects.filter(is_active=True).order_by("sort_order", "name")
                            .values_list("pk", "name")),
        "designations": list(Designation.objects.filter(is_active=True).order_by("-rank", "name")
                             .values_list("pk", "name")),
        "roles": list(Role.objects.exclude(code__startswith="inst-").order_by("-rank", "name")
                      .values_list("code", "name")),
        "grades": list(GradeLevel.objects.filter(is_active=True).order_by("number").values_list("number", flat=True)),
    }
