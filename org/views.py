"""Organisation pages (Phase B): departments (director seat, divisions,
Manage access) and Settings → Offices / Grade levels / Designations.

Every rule lives in ``org.services``; these views only collect input and
render. HTMX posts swap the tab or row they came from; every pushed URL
renders the full page on a normal request.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

import org
from accounts.models import Role, User
from staff.models import Department, Designation, Office, Staff

from .models import Division, GradeLevel

TABS = ("overview", "divisions", "access")


def _htmx(request):
    return request.headers.get("HX-Request", "").lower() == "true" and not getattr(
        request, "htmx_history_restore", False)


def _sees_all_departments(user):
    return bool(user.is_superuser or org.is_es(user) or org.can_manage_directors(user)
                or user.has_perm("accounts.view_all_users") or user.has_perm("org.manage_all_divisions"))


def _visible_departments(user):
    qs = Department.objects.select_related("director", "parent").annotate(
        n_divisions=Count("divisions", filter=Q(divisions__is_active=True), distinct=True),
        n_staff=Count("staff", filter=Q(staff__is_deleted=False), distinct=True),
    ).order_by("name")
    if _sees_all_departments(user):
        return qs
    if not user.department_id:
        return qs.none()
    return qs.filter(pk=user.department_id)


def _department_for(user, code):
    dept = _visible_departments(user).filter(code=code).first()
    if dept is None:
        raise Http404
    return dept


def _members(dept):
    return (User.objects.filter(is_active=True, user_type="staff")
            .filter(Q(department=dept) | Q(staff_profile__department=dept))
            .select_related("role", "staff_profile__division", "staff_profile__grade")
            .distinct().order_by("first_name", "last_name"))


def _context(request, dept, tab, notice="", error=""):
    user = request.user
    ctx = {
        "dept": dept, "tab": tab, "tabs": TABS, "notice": notice, "error": error,
        "director": org.director_of(dept),
        "can_manage_seat": org.can_manage_directors(user),
        "can_manage_divisions": org.can_manage_divisions(user, dept),
        "can_manage_access": bool(org.is_director(user, dept) or org.is_es(user) or org.is_super_admin(user)),
    }
    if tab == "overview":
        members = list(_members(dept))
        ctx.update(members=members, heads=org.heads_of(dept),
                   director_candidates=[m for m in members if not org.is_director(m)])
    elif tab == "divisions":
        divisions = list(Division.objects.filter(department=dept).select_related("head")
                         .annotate(n_members=Count("members", filter=Q(members__is_deleted=False)))
                         .order_by("-is_active", "name"))
        for d in divisions:
            d.open_tasks = org.division_open_task_count(d)
        ctx.update(divisions=divisions, members=list(_members(dept)),
                   staff_rows=list(Staff.objects.filter(department=dept, is_deleted=False)
                                   .select_related("division", "user").order_by("first_name", "last_name")))
    elif tab == "access":
        members = [m for m in _members(dept) if m.pk != user.pk]
        ctx.update(members=members, grants=list(org.active_grants(department=dept)),
                   grantable=org.grantable_permissions(user),
                   max_days=org.services._default_days())
    return ctx


def _render_tab(request, dept, tab, **extra):
    ctx = _context(request, dept, tab, **extra)
    if _htmx(request):
        return render(request, f"org/_tab_{tab}.html", ctx)
    return render(request, "org/department_detail.html", ctx)


# ===========================================================================
# Departments
# ===========================================================================
@login_required
def department_list(request):
    if getattr(request.user, "user_type", "staff") != "staff":
        raise PermissionDenied
    depts = list(_visible_departments(request.user))
    if len(depts) == 1 and not _sees_all_departments(request.user):
        return redirect("org:department", code=depts[0].code)
    return render(request, "org/department_list.html", {
        "departments": depts,
        "can_settings": org.can_manage_offices(request.user) or _can_grades(request.user),
    })


@login_required
def department_detail(request, code):
    dept = _department_for(request.user, code)
    tab = request.GET.get("tab") or "overview"
    if tab not in TABS:
        tab = "overview"
    if tab == "access" and not _context(request, dept, "overview")["can_manage_access"]:
        raise PermissionDenied
    return _render_tab(request, dept, tab)


def _posted_user(request, field="user", dept=None):
    raw = request.POST.get(field) or ""
    if not raw.isdigit():
        return None
    qs = User.objects.filter(pk=int(raw), is_active=True)
    if dept is not None:
        qs = qs.filter(Q(department=dept) | Q(staff_profile__department=dept))
    return qs.first()


@login_required
@require_POST
def director_assign(request, code):
    dept = _department_for(request.user, code)
    person = _posted_user(request, dept=dept)
    try:
        if person is None:
            raise org.OrgRuleError("Choose a member of this department.")
        org.assign_director(dept, person, request.user, request.POST.get("reason", ""))
    except org.OrgRuleError as exc:
        return _render_tab(request, dept, "overview", error=str(exc))
    return _render_tab(request, dept, "overview", notice=f"{person.get_full_name() or person.email} is now the director.")


@login_required
@require_POST
def director_vacate(request, code):
    dept = _department_for(request.user, code)
    try:
        org.vacate_director(dept, request.user, request.POST.get("reason", ""),
                            new_role=request.POST.get("new_role") or None)
    except org.OrgRuleError as exc:
        return _render_tab(request, dept, "overview", error=str(exc))
    return _render_tab(request, dept, "overview", notice="The director seat is now empty.")


# ---------------------------------------------------------------------------
# Divisions
# ---------------------------------------------------------------------------
def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@login_required
@require_POST
def division_create(request, code):
    dept = _department_for(request.user, code)
    try:
        head = _posted_user(request, "head", dept) if request.POST.get("head") else None
        div = org.create_division(dept, request.POST.get("name", ""), request.user,
                                  max_open_tasks=_int(request.POST.get("max_open_tasks"), 10), head=head)
    except org.OrgRuleError as exc:
        return _render_tab(request, dept, "divisions", error=str(exc))
    return _render_tab(request, dept, "divisions", notice=f"Division “{div.name}” created.")


@login_required
@require_POST
def division_update(request, code, pk):
    dept = _department_for(request.user, code)
    div = get_object_or_404(Division, pk=pk, department=dept)
    op = request.POST.get("op", "save")
    try:
        if op == "head":
            head = _posted_user(request, "head", dept) if request.POST.get("head") else None
            org.set_division_head(div, head, request.user)
            msg = f"{head.get_full_name() or head.email} heads {div.name}." if head else f"{div.name} has no head now."
        elif op == "toggle":
            org.update_division(div, request.user, is_active=not div.is_active)
            msg = f"{div.name} is {'active' if div.is_active else 'inactive'}."
        else:
            org.update_division(div, request.user, name=request.POST.get("name"),
                                max_open_tasks=_int(request.POST.get("max_open_tasks"), div.max_open_tasks))
            msg = f"{div.name} saved."
    except org.OrgRuleError as exc:
        return _render_tab(request, dept, "divisions", error=str(exc))
    return _render_tab(request, dept, "divisions", notice=msg)


@login_required
@require_POST
def division_member(request, code):
    dept = _department_for(request.user, code)
    staff = get_object_or_404(Staff, pk=_int(request.POST.get("staff"), 0), department=dept)
    div_id = _int(request.POST.get("division"))
    div = get_object_or_404(Division, pk=div_id, department=dept) if div_id else None
    try:
        org.move_to_division(staff, div, request.user)
    except org.OrgRuleError as exc:
        return _render_tab(request, dept, "divisions", error=str(exc))
    return _render_tab(request, dept, "divisions",
                       notice=f"{staff.full_name} moved to {div.name if div else 'no division'}.")


# ---------------------------------------------------------------------------
# Manage access (X31)
# ---------------------------------------------------------------------------
@login_required
@require_POST
def access_grant(request, code):
    from django.contrib.auth.models import Permission

    dept = _department_for(request.user, code)
    person = _posted_user(request, dept=dept)
    perm = Permission.objects.filter(pk=_int(request.POST.get("permission"), 0)).select_related("content_type").first()
    try:
        if person is None or perm is None:
            raise org.OrgRuleError("Choose a person and a permission.")
        org.grant_permission(request.user, person, perm, scope=request.POST.get("scope") or "department",
                             days=request.POST.get("days") or None, reason=request.POST.get("reason", ""))
    except org.OrgRuleError as exc:
        return _render_tab(request, dept, "access", error=str(exc))
    return _render_tab(request, dept, "access", notice=f"{perm.name} granted to {person.get_full_name() or person.email}.")


@login_required
@require_POST
def access_revoke(request, code, pk):
    from accounts.models import UserPermissionOverride

    dept = _department_for(request.user, code)
    grant = get_object_or_404(UserPermissionOverride, pk=pk, user__department=dept)
    try:
        org.revoke_permission(request.user, grant, request.POST.get("reason", ""))
    except org.OrgRuleError as exc:
        return _render_tab(request, dept, "access", error=str(exc))
    return _render_tab(request, dept, "access", notice=f"{grant.permission.name} removed.")


# ===========================================================================
# Settings → Departments and units (v2.2: offices merged into one list)
# ===========================================================================
def _offices_ctx(request, notice="", error="", open_pk=None):
    kind = request.GET.get("kind") or ""
    units = (Department.objects.prefetch_related("member_groups").select_related("parent", "director", "head")
             .annotate(n_staff=Count("staff", filter=Q(staff__is_deleted=False), distinct=True))
             .order_by("kind", "sort_order", "name"))
    if kind in dict(Department.KIND_CHOICES):
        units = units.filter(kind=kind)
    units = list(units)
    return {"offices": units, "units": units, "groups": list(Group.objects.order_by("name")), "notice": notice,
            "error": error, "open_pk": open_pk, "kinds": Department.KIND_CHOICES, "kind": kind,
            "parents": list(Department.objects.filter(is_active=True).order_by("name"))}


@login_required
def offices(request):
    if not org.can_manage_offices(request.user):
        raise PermissionDenied
    ctx = _offices_ctx(request)
    return render(request, "org/_offices_list.html" if _htmx(request) else "org/offices.html", ctx)


@login_required
@require_POST
def office_save(request, pk=None):
    """Create or edit a unit in the one Department list (any kind)."""
    if not org.can_manage_offices(request.user):
        raise PermissionDenied
    unit = get_object_or_404(Department, pk=pk) if pk else None
    name = (request.POST.get("name") or "").strip()
    kind = request.POST.get("kind") or Department.KIND_UNIT
    if kind not in dict(Department.KIND_CHOICES):
        kind = Department.KIND_UNIT
    parent_id = _int(request.POST.get("parent"))
    try:
        if not name:
            raise org.OrgRuleError("Give the unit a name.")
        if Department.objects.filter(name__iexact=name).exclude(pk=getattr(unit, "pk", None)).exists():
            raise org.OrgRuleError("A department or unit with that name exists.")
        parent = Department.objects.filter(pk=parent_id).first() if parent_id else None
        if unit is not None and parent is not None and parent.pk == unit.pk:
            raise org.OrgRuleError("A unit cannot sit under itself.")
        if unit is None:
            unit = Department(name=name)
        unit.name = name
        unit.kind = kind
        unit.parent = parent
        unit.description = (request.POST.get("description") or "")[:255]
        if pk:
            unit.is_active = request.POST.get("is_active") == "on"
        unit.save()
        ids = [int(x) for x in request.POST.getlist("groups") if x.isdigit()]
        org.set_office_groups(unit, list(Group.objects.filter(pk__in=ids)), request.user)
    except org.OrgRuleError as exc:
        return render(request, "org/_offices_list.html", _offices_ctx(request, error=str(exc),
                                                                      open_pk=getattr(unit, "pk", None)))
    return render(request, "org/_offices_list.html",
                  _offices_ctx(request, notice=f"{unit.kind_label} “{unit.name}” saved."))


# ===========================================================================
# Settings → Grade levels and Designations (C7, C8)
# ===========================================================================
def _can_grades(user):
    return bool(user.is_authenticated and (user.is_superuser or org.is_es(user)
                                           or user.has_perm("org.manage_grade_levels")))


def _grades_ctx(notice="", error=""):
    return {"grades": list(GradeLevel.objects.select_related("max_role")
                           .annotate(n_staff=Count("staff", filter=Q(staff__is_deleted=False)))),
            "roles": list(Role.objects.order_by("-rank", "name")), "notice": notice, "error": error}


@login_required
def grade_levels(request):
    if not _can_grades(request.user):
        raise PermissionDenied
    return render(request, "org/_grades_table.html" if _htmx(request) else "org/grade_levels.html", _grades_ctx())


@login_required
@require_POST
def grade_level_save(request, pk=None):
    if not _can_grades(request.user):
        raise PermissionDenied
    from core import audit
    number = _int(request.POST.get("number"))
    grade = get_object_or_404(GradeLevel, pk=pk) if pk else GradeLevel()
    if pk is None:
        if number is None or not 1 <= number <= 30:
            return render(request, "org/_grades_table.html", _grades_ctx(error="Give a level number from 1 to 30."))
        if GradeLevel.objects.filter(number=number).exists():
            return render(request, "org/_grades_table.html", _grades_ctx(error=f"Level {number} exists already."))
        grade.number = number
        grade.scale = request.POST.get("scale") or "CONTEDISS"
    grade.label = (request.POST.get("label") or "").strip()[:60]
    grade.min_years_in_grade = _int(request.POST.get("min_years_in_grade"))
    grade.max_role = Role.objects.filter(pk=_int(request.POST.get("max_role"), 0)).first()
    grade.notes = (request.POST.get("notes") or "").strip()[:255]
    grade.is_active = request.POST.get("is_active") == "on" if pk else True
    grade.save()
    audit.log("grade_level_saved", grade, actor=request.user)
    return render(request, "org/_grades_table.html", _grades_ctx(notice=f"{grade.display} saved."))


def _designations_ctx(request, notice="", error=""):
    qs = Designation.objects.select_related("default_role", "grade_min", "grade_max").order_by("-rank", "name")
    q = (request.GET.get("q") or request.POST.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(aliases__icontains=q))
    mapped = request.GET.get("mapped") or ""
    if mapped == "no":
        qs = qs.filter(default_role__isnull=True)
    elif mapped == "yes":
        qs = qs.filter(default_role__isnull=False)
    show_all = request.GET.get("all") == "1"
    total = qs.count()
    page = Paginator(qs, 500 if show_all else 25).get_page(request.GET.get("page"))
    return {"page": page, "q": q, "mapped": mapped, "show_all": show_all, "total": total,
            "roles": list(Role.objects.order_by("-rank", "name")),
            "grades": list(GradeLevel.objects.filter(is_active=True)), "notice": notice, "error": error}


@login_required
def designations(request):
    if not _can_grades(request.user):
        raise PermissionDenied
    ctx = _designations_ctx(request)
    return render(request, "org/_designations_table.html" if _htmx(request) else "org/designations.html", ctx)


@login_required
@require_POST
def designation_save(request, pk):
    if not _can_grades(request.user):
        raise PermissionDenied
    d = get_object_or_404(Designation, pk=pk)
    d.default_role = Role.objects.filter(pk=_int(request.POST.get("default_role"), 0)).first()
    d.grade_min = GradeLevel.objects.filter(pk=_int(request.POST.get("grade_min"), 0)).first()
    d.grade_max = GradeLevel.objects.filter(pk=_int(request.POST.get("grade_max"), 0)).first()
    if d.grade_min and d.grade_max and d.grade_min.number > d.grade_max.number:
        return render(request, "org/_designation_row.html",
                      {"d": d, "roles": list(Role.objects.order_by("-rank", "name")),
                       "grades": list(GradeLevel.objects.filter(is_active=True)),
                       "error": "The lowest grade is above the highest."})
    rank = _int(request.POST.get("rank"))
    if rank is not None:
        d.rank = rank
    d.save()
    return render(request, "org/_designation_row.html",
                  {"d": d, "roles": list(Role.objects.order_by("-rank", "name")),
                   "grades": list(GradeLevel.objects.filter(is_active=True)), "saved": True})
