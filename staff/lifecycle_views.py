"""Staff lifecycle pages: Nominal roll (import / export / template / history),
Promotions (select, preview, apply, history, rollback) and Advanced query.

Every view checks its permission server-side and scopes staff through
accounts.scoping.scope_to_department (fail closed).
"""
from __future__ import annotations

import uuid
from datetime import date
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.scoping import scope_to_department
from core import audit
from core.storage import restricted_storage

from . import query as staff_query
from .exporters import build_template, export_nominal_roll
from .models import (
    Department, Designation, ImportSession, Office, Promotion, SavedQuery, Staff,
)
from .nominal_roll import MODES, NominalRollImporter
from .promotions import (
    PromotionError, apply_promotion, can_override_step, can_rollback, cascade_set, eligibility,
    eligible_staff, idfilter, levels, overdue_staff, plan_promotion, rollback_promotion,
    target_choices,
)

User = get_user_model()

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_UPLOAD = 15 * 1024 * 1024
UPLOAD_SESSION_KEY = "nominal_roll_upload"
UPLOAD_DIR = "staff/imports/uploads"


def scoped_staff(user):
    from accounts.audience import nbte_staff_records_q

    return scope_to_department(
        Staff.objects.select_related("department", "designation", "state", "state_of_origin", "office")
        .filter(nbte_staff_records_q()),
        user)


def _require(user, *perms):
    if not any(user.has_perm(p) for p in perms):
        raise PermissionDenied
    return True


def _xlsx_response(content, filename):
    resp = HttpResponse(content, content_type=XLSX)
    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
    resp["Cache-Control"] = "private, no-store"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


# ===========================================================================
# NOMINAL ROLL
# ===========================================================================
@login_required
def nominal_roll(request):
    user = request.user
    _require(user, "staff.import_nominal_roll", "staff.export_nominal_roll")
    can_import = user.has_perm("staff.import_nominal_roll")
    can_export = user.has_perm("staff.export_nominal_roll")
    tab = request.GET.get("tab") or ("import" if can_import else "export")
    if tab not in ("import", "export", "template", "history"):
        tab = "import"
    if not can_import and tab in ("import", "history"):
        tab = "export"
    scoped = scoped_staff(user)
    departments = (Department.objects.filter(pk__in=scoped.values("department_id"))
                   .annotate(n=Count("staff", filter=Q(staff__is_deleted=False)))
                   .order_by("name"))
    pending = request.session.get(UPLOAD_SESSION_KEY)
    return render(request, "staff/nominal_roll/index.html", {
        "tab": tab,
        "can_import": can_import,
        "can_export": can_export,
        "staff_total": scoped.count(),
        "departments": departments,
        "sessions": ImportSession.objects.select_related("uploaded_by")[:20] if can_import else [],
        "pending": pending,
        "designation_count": Designation.objects.filter(is_active=True).count(),
    })


def _stored_upload(request):
    info = request.session.get(UPLOAD_SESSION_KEY)
    if not info:
        return None, None
    storage = restricted_storage()
    if not storage.exists(info["path"]):
        request.session.pop(UPLOAD_SESSION_KEY, None)
        return None, None
    return info, storage


def _discard_upload(request):
    info = request.session.pop(UPLOAD_SESSION_KEY, None)
    if info:
        try:
            restricted_storage().delete(info["path"])
        except OSError:
            pass


@login_required
@require_POST
def nominal_roll_upload(request):
    _require(request.user, "staff.import_nominal_roll")
    upload = request.FILES.get("file")
    if upload is None:
        messages.error(request, "Choose a nominal roll file (.xlsx) to upload.")
        return redirect(reverse("staff:nominal_roll") + "?tab=import")
    name = upload.name or "nominal-roll.xlsx"
    if not name.lower().endswith((".xlsx", ".xlsm")):
        messages.error(request, "Only Excel workbooks (.xlsx) can be read. In Excel, use "
                                "File → Save As → Excel Workbook (.xlsx) and upload that.")
        return redirect(reverse("staff:nominal_roll") + "?tab=import")
    if upload.size > MAX_UPLOAD:
        messages.error(request, "That file is larger than 15 MB. Remove pictures or extra sheets and try again.")
        return redirect(reverse("staff:nominal_roll") + "?tab=import")
    try:
        import openpyxl
        upload.seek(0)
        openpyxl.load_workbook(upload, read_only=True).close()
        upload.seek(0)
    except Exception:
        messages.error(request, "That file could not be opened as an Excel workbook.")
        return redirect(reverse("staff:nominal_roll") + "?tab=import")
    _discard_upload(request)
    path = restricted_storage().save(f"{UPLOAD_DIR}/{uuid.uuid4().hex}.xlsx", upload)
    request.session[UPLOAD_SESSION_KEY] = {
        "path": path, "name": name[:200], "size": upload.size,
        "uploaded_at": timezone.now().isoformat(),
    }
    return redirect("staff:nominal_roll_preview")


def _import_options(request, importer):
    data = request.POST if request.method == "POST" else request.GET
    available = [n for n, _l, _c in importer.sheet_choices()]
    if data.get("sheets_submitted"):
        sheets = [s for s in data.getlist("sheet") if s in available]
    else:
        sheets = available
    mode = data.get("mode") if data.get("mode") in MODES else "update"
    return sheets, mode


def _account_options(request):
    """({"scheme", "password"} or None, error message or "") from the preview form."""
    from .account_provisioning import SCHEMES
    data = request.POST if request.method == "POST" else request.GET
    if data.get("create_accounts") != "on":
        return None, ""
    if not request.user.has_perm("accounts.add_user"):
        raise PermissionDenied
    scheme = data.get("password_scheme") if data.get("password_scheme") in SCHEMES else "surname_fileno"
    password = data.get("starter_password") or ""
    opts = {"scheme": scheme, "password": password, "email": data.get("email_details") == "on",
            "login_url": request.build_absolute_uri(reverse("accounts:login"))}
    if scheme == "shared" and len(password) < 8:
        return dict(opts, password=""), "The starter password must be at least 8 characters."
    return opts, ""


@login_required
def nominal_roll_preview(request):
    _require(request.user, "staff.import_nominal_roll")
    info, storage = _stored_upload(request)
    if info is None:
        messages.info(request, "Upload a nominal roll file first.")
        return redirect(reverse("staff:nominal_roll") + "?tab=import")
    if request.method == "POST" and request.POST.get("discard"):
        _discard_upload(request)
        messages.info(request, "Upload discarded.")
        return redirect(reverse("staff:nominal_roll") + "?tab=import")
    with storage.open(info["path"], "rb") as fh:
        importer = NominalRollImporter(fh, filename=info["name"], actor=request.user)
        sheets, mode = _import_options(request, importer)
        accounts, account_error = _account_options(request)
        importer.only_sheets = set(sheets)
        importer.mode = mode
        importer.accounts = accounts if accounts and not account_error else None
        result = importer.run(dry_run=True) if sheets else None
        choices = importer.sheet_choices()
        empty = [s.label for s in importer.scan() if s.is_empty]
    return render(request, "staff/nominal_roll/preview.html", {
        "upload": info,
        "result": result,
        "choices": choices,
        "selected": set(sheets),
        "empty_sheets": empty,
        "mode": mode,
        "accounts": accounts,
        "account_error": account_error,
        "can_create_accounts": request.user.has_perm("accounts.add_user"),
        "password_schemes": _password_schemes(),
        "modes": [("update", "Update existing", "Staff already in the system get any new or changed "
                   "values from the file. Empty cells never erase saved data."),
                  ("skip", "Skip existing", "Only add people who are not in the system yet."),
                  ("fail", "Fail on any duplicate", "Stop without saving anything if any row matches "
                   "someone already in the system.")],
    })


@login_required
@require_POST
def nominal_roll_confirm(request):
    _require(request.user, "staff.import_nominal_roll")
    info, storage = _stored_upload(request)
    if info is None:
        messages.info(request, "Upload a nominal roll file first.")
        return redirect(reverse("staff:nominal_roll") + "?tab=import")
    with storage.open(info["path"], "rb") as fh:
        importer = NominalRollImporter(fh, filename=info["name"], actor=request.user)
        sheets, mode = _import_options(request, importer)
        if not sheets:
            messages.error(request, "Tick at least one sheet to import.")
            return redirect("staff:nominal_roll_preview")
        accounts, account_error = _account_options(request)
        if account_error:
            messages.error(request, account_error)
            return redirect("staff:nominal_roll_preview")
        importer.only_sheets = set(sheets)
        importer.mode = mode
        importer.accounts = accounts
        result = importer.run(dry_run=False)
    if result.aborted:
        messages.error(request, f"Import stopped — nothing was saved. {result.aborted}")
        return redirect("staff:nominal_roll_preview")
    _discard_upload(request)
    messages.success(
        request,
        f"Staff imported: {result.new} new, {result.updated} updated, "
        f"{result.unchanged} unchanged, {result.skipped} skipped.")
    if result.accounts_created:
        extra = (" Download the sign-in details from History — it can be downloaded once."
                 if result.credentials else "")
        emailed = (f" {result.accounts_emailed} welcome email(s) queued." if result.accounts_emailed else "")
        messages.success(request, f"{result.accounts_created} sign-in account(s) created. Staff sign in "
                                  f"with their email (or file number if they have none yet) and must change "
                                  f"the password.{emailed}{extra}")
    if result.date_warnings or result.duplicate_warnings:
        messages.warning(request, f"{len(result.date_warnings)} date warning(s) and "
                                  f"{len(result.duplicate_warnings)} duplicate FILE NO warning(s) — "
                                  "see the report in History.")
    return redirect(reverse("staff:nominal_roll") + "?tab=history")


@login_required
@require_GET
def nominal_roll_export(request):
    user = request.user
    _require(user, "staff.export_nominal_roll")
    per_department = request.GET.get("layout") == "departments"
    qs = scoped_staff(user)
    dept_id = request.GET.get("department")
    label = "all departments"
    if dept_id and dept_id.isdigit():
        qs = qs.filter(department_id=int(dept_id))
        dept = Department.objects.filter(pk=int(dept_id)).first()
        label = dept.name if dept else label
    content, count = export_nominal_roll(qs, per_department=per_department)
    stamp = timezone.localdate().strftime("%Y%m%d")
    suffix = "_by_department" if per_department else ""
    audit.log("export_nominal_roll", user, actor=user,
              changes={"rows": count, "layout": "per-department" if per_department else "full",
                       "scope": label}, note="Nominal roll export")
    return _xlsx_response(content, f"NBTE_Nominal_Roll{suffix}_{stamp}.xlsx")


@login_required
@require_GET
def nominal_roll_template(request):
    _require(request.user, "staff.import_nominal_roll", "staff.export_nominal_roll")
    return _xlsx_response(build_template(), "NBTE_Nominal_Roll_Template.xlsx")


@login_required
@require_GET
def nominal_roll_report(request, pk):
    _require(request.user, "staff.import_nominal_roll")
    session = get_object_or_404(ImportSession, pk=pk)
    if not session.report_file:
        raise Http404
    from core import file_preview

    inline = file_preview.wants_inline(request, "text/plain")
    audit.log("other", session, actor=request.user, note=file_preview.audit_note(inline, "import report"))
    filename = f"import-report-{session.created_at:%Y%m%d-%H%M}.txt"
    if getattr(settings, "USE_X_ACCEL_REDIRECT", False):
        resp = HttpResponse(content_type="text/plain; charset=utf-8")
        resp["X-Accel-Redirect"] = settings.PROTECTED_MEDIA_URL + quote(session.report_file.name)
    else:
        resp = FileResponse(session.report_file.open("rb"), content_type="text/plain; charset=utf-8")
    return file_preview.finish(resp, filename, inline)


def _password_schemes():
    from .account_provisioning import SCHEMES
    help_text = {
        "surname_fileno": "Easy to hand out. Anyone who knows a person's surname and file number can "
                          "guess it, so ask staff to sign in and change it quickly.",
        "shared": "Everyone gets the same starter password you type below (at least 8 characters).",
        "random": "Safest. You download a sheet with each person's password once, right after the import.",
    }
    return [(k, v, help_text[k]) for k, v in SCHEMES.items()]


@login_required
@require_GET
def nominal_roll_credentials(request, pk):
    """One-time download of the random passwords made by an import."""
    _require(request.user, "staff.import_nominal_roll")
    if not request.user.has_perm("accounts.add_user"):
        raise PermissionDenied
    session = get_object_or_404(ImportSession, pk=pk)
    if not session.credentials_file:
        messages.info(request, "Those sign-in details were already downloaded and have been deleted.")
        return redirect(reverse("staff:nominal_roll") + "?tab=history")
    with session.credentials_file.open("rb") as fh:
        content = fh.read()
    session.credentials_file.delete(save=True)
    audit.log("other", session, actor=request.user, note="sign-in details downloaded (then deleted)")
    return _xlsx_response(content, f"NBTE_sign_in_details_{session.created_at:%Y%m%d-%H%M}.xlsx")


# ===========================================================================
# PROMOTIONS (B.9: D2-D12)
# ===========================================================================
PROMO_TABS = (("eligible", "Eligible"), ("overdue", "Overdue"), ("all", "All"), ("history", "History"))
ID_FILTER_KEY = "promotion_id_filter"


def _years_in_post(d):
    if not d:
        return None
    today = timezone.localdate()
    return today.year - d.year - ((today.month, today.day) < (d.month, d.day))


def _selected_staff(request, data):
    ids = [int(i) for i in data.getlist("ids") if str(i).isdigit()]
    if not ids:
        return []
    return list(scoped_staff(request.user).filter(pk__in=ids))


def _id_filter(request):
    info = request.session.get(ID_FILTER_KEY)
    return info if isinstance(info, dict) and info.get("ids") else None


def _promotion_list(request):
    """The filtered, tabbed staff list (shared list contract: search + three
    facets + ID list + pagination with Show all)."""
    from qa_core import ui

    user = request.user
    tab = request.GET.get("tab") or "eligible"
    if tab not in ("eligible", "overdue", "all"):
        tab = "eligible"
    base = scoped_staff(user)
    f_dept = request.GET.get("department", "")
    f_office = request.GET.get("office", "")
    f_desig = request.GET.get("designation", "")
    q = (request.GET.get("q") or "").strip()
    if f_dept.isdigit():
        base = base.filter(department_id=int(f_dept))
    if f_office.isdigit():   # v2.2: offices are units in the Department list
        base = base.filter(department_id=int(f_office))
    if f_desig.isdigit():
        base = base.filter(designation_id=int(f_desig))
    if q:
        base = base.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q)
                           | Q(employee_no__icontains=q))
    id_info = _id_filter(request)
    unmatched = []
    if id_info:
        base, unmatched = idfilter.match(base, id_info["ids"])
    counts = {"eligible": eligible_staff(base).count(), "overdue": overdue_staff(base).count(),
              "all": base.count()}
    qs = {"eligible": eligible_staff, "overdue": overdue_staff}.get(tab, lambda x: x)(base)
    qs = qs.order_by("department__name", "-designation__rank", "last_name", "first_name")
    page = ui.paginate(request, qs)
    today = timezone.localdate()
    for s in page["rows"]:
        s.years_in_post = _years_in_post(s.date_present_appointment)
        s.elig = eligibility(s, today)
    return {
        "tab": tab, "tabs": PROMO_TABS, "counts": counts, "page": page,
        "filters_active": bool(f_dept or f_office or f_desig or q),
        "f_dept": f_dept, "f_office": f_office, "f_desig": f_desig, "q": q,
        "id_filter": id_info, "id_unmatched": unmatched,
        "min_years": levels.min_years_for(), "grace_days": levels.overdue_grace_days(),
    }


@login_required
def promotions(request):
    from qa_core import ui

    user = request.user
    _require(user, "staff.promote_staff")
    if request.GET.get("tab") == "history":
        return redirect("staff:promotion_history")
    ctx = _promotion_list(request)
    scope = scoped_staff(user)
    ctx.update({
        "departments": Department.objects.filter(pk__in=scope.values("department_id")).order_by("name"),
        "offices": Department.objects.none(),   # v2.2: merged into departments
        "designations": Designation.objects.filter(pk__in=scope.values("designation_id")).order_by("-rank", "name"),
        "targets": Designation.objects.filter(is_active=True).order_by("-rank", "name")
                   if can_override_step(user) else [],
        "can_override": can_override_step(user),
        "today": timezone.localdate(),
    })
    return ui.render_qa(request, "staff/promotions/index.html", ctx,
                        partials={"promo-list": "staff/promotions/_list.html"})


@login_required
@require_POST
def promotion_id_filter(request):
    """Upload an .xlsx of employee IDs to filter the list (D6); ?clear=1 removes it."""
    _require(request.user, "staff.promote_staff")
    back = reverse("staff:promotions") + "?tab=" + (request.POST.get("tab") or "all")
    if request.POST.get("clear"):
        request.session.pop(ID_FILTER_KEY, None)
        messages.info(request, "Employee ID filter removed.")
        return redirect(back)
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Choose an Excel file (.xlsx) with the employee IDs.")
        return redirect(back)
    if f.size > MAX_UPLOAD or not f.name.lower().endswith((".xlsx", ".xlsm")):
        messages.error(request, "Upload an .xlsx file under 15 MB.")
        return redirect(back)
    try:
        ids = idfilter.read_ids(f)
    except idfilter.IdFilterError as exc:
        messages.error(request, str(exc))
        return redirect(back)
    matched, unmatched = idfilter.match(scoped_staff(request.user), ids)
    request.session[ID_FILTER_KEY] = {"ids": ids, "name": f.name[:120]}
    msg = f"{matched.count()} of {len(ids)} employee IDs matched."
    if unmatched:
        msg += " Not found: " + ", ".join(unmatched[:10]) + ("…" if len(unmatched) > 10 else "")
        messages.warning(request, msg)
    else:
        messages.success(request, msg)
    return redirect(back)


def _plan_from_post(request):
    data = request.POST
    roots = _selected_staff(request, data)
    if not roots:
        raise PromotionError("Select at least one staff member.")
    target = None
    if data.get("mode") == "target":
        target_id = data.get("target")
        if not (target_id and target_id.isdigit()):
            raise PromotionError("Choose the designation to promote to.")
        target = get_object_or_404(Designation, pk=int(target_id), is_active=True)
    cascade = data.get("cascade") == "on"
    scope_kind = "grade" if data.get("cascade_scope") == "grade" else "department"
    plan = plan_promotion(roots, target, cascade=cascade, cascade_scope=scope_kind,
                          scope=scoped_staff(request.user), actor=request.user,
                          override_reason=data.get("override_reason", ""))
    try:
        effective = date.fromisoformat(data.get("effective_date") or "")
    except ValueError:
        effective = timezone.localdate()
    return plan, effective, (data.get("reason") or "").strip()


@login_required
@require_POST
def promotion_preview(request):
    _require(request.user, "staff.promote_staff")
    try:
        plan, effective, reason = _plan_from_post(request)
        error = ""
    except PromotionError as exc:
        plan, effective, reason, error = None, None, "", str(exc)
    ctx = {"plan": plan, "effective": effective, "reason": reason, "error": error,
           "post": request.POST}
    if request.headers.get("X-Requested-With") == "fetch" or request.headers.get("HX-Request"):
        return render(request, "staff/promotions/preview_modal.html", ctx)
    return render(request, "staff/promotions/preview_page.html", ctx)


@login_required
@require_GET
def promotion_cascade_count(request):
    _require(request.user, "staff.promote_staff")
    roots = _selected_staff(request, request.GET)
    scope = scoped_staff(request.user)
    scope_kind = "grade" if request.GET.get("cascade_scope") == "grade" else "department"
    root_ids = {r.pk for r in roots}
    others = set()
    for r in roots:
        others.update(s.pk for s in cascade_set(r, scope, scope_kind) if s.pk not in root_ids)
    tiers = sorted({r.designation.name for r in roots if r.designation_id})
    grades = sorted({r.grade_level for r in roots if r.grade_level})
    lowest = min([r.designation.rank for r in roots if r.designation_id] or [0])
    allowed = list(target_choices(roots, request.user).values_list("pk", flat=True)) if roots else []
    nxt = {}
    for r in roots:
        step = levels.next_level(r)
        nxt[str(r.pk)] = step.designation.pk if step else None
    return JsonResponse({"count": len(others), "tiers": tiers, "grades": grades, "scope": scope_kind,
                         "lowest_rank": lowest, "allowed_targets": allowed, "next": nxt})


@login_required
@require_POST
def promotion_apply(request):
    _require(request.user, "staff.promote_staff")
    back = request.POST.get("next") or reverse("staff:promotions")
    from core.urlsafe import safe_next

    back = safe_next(request, back, reverse("staff:promotions"))
    try:
        plan, effective, reason = _plan_from_post(request)
        result = apply_promotion(plan, effective_date=effective, reason=reason,
                                 approver=request.user, notify=request.POST.get("notify") == "on")
    except PromotionError as exc:
        messages.error(request, str(exc))
        return redirect(back)
    n = len(result.promotions)
    cascaded = sum(1 for p in result.promotions if p.cascade_source_id)
    msg = f"{n} promotion{'s' if n != 1 else ''} saved"
    if cascaded:
        msg += f" ({cascaded} by cascade)"
    if plan.skipped:
        msg += f"; {len(plan.skipped)} not promoted"
    if result.notified:
        msg += f"; {result.notified} notification{'s' if result.notified != 1 else ''} sent"
    messages.success(request, msg + ".")
    for w in result.warnings:
        if w.startswith("Grade level unchanged"):
            messages.warning(request, w)
    return redirect(back)


@login_required
def promotion_history(request):
    user = request.user
    _require(user, "staff.promote_staff")
    scope_ids = scoped_staff(user).values("pk")
    qs = (Promotion.objects.filter(staff_id__in=scope_ids)
          .select_related("staff", "staff__department", "from_designation", "to_designation",
                          "approved_by", "cascade_source", "cascade_source__staff", "reverses")
          .order_by("-effective_date", "-created_at"))
    f_from, f_to = request.GET.get("from", ""), request.GET.get("to", "")
    f_dept, f_approver = request.GET.get("department", ""), request.GET.get("approver", "")
    try:
        if f_from:
            qs = qs.filter(effective_date__gte=date.fromisoformat(f_from))
        if f_to:
            qs = qs.filter(effective_date__lte=date.fromisoformat(f_to))
    except ValueError:
        messages.error(request, "Dates must be in YYYY-MM-DD format.")
    if f_dept.isdigit():
        qs = qs.filter(staff__department_id=int(f_dept))
    if f_approver.isdigit():
        qs = qs.filter(approved_by_id=int(f_approver))
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    reversed_ids = set(Promotion.objects.filter(reverses__in=[p.pk for p in page.object_list])
                       .values_list("reverses_id", flat=True))
    may_rollback = user.is_superuser or user.has_perm("staff.rollback_promotion")
    for p in page.object_list:
        p.was_reversed = p.pk in reversed_ids
        p.rollback_ok, p.rollback_why = ((False, "Already rolled back.") if p.was_reversed
                                         else can_rollback(user, p))
    approvers = User.objects.filter(
        pk__in=Promotion.objects.exclude(approved_by=None).values("approved_by")).order_by("first_name")
    return render(request, "staff/promotions/history.html", {
        "page": page,
        "tab": "history", "tabs": PROMO_TABS,
        "may_rollback": may_rollback,
        "filters_active": bool(f_from or f_to or f_dept or f_approver),
        "f_from": f_from, "f_to": f_to, "f_dept": f_dept, "f_approver": f_approver,
        "departments": Department.objects.filter(
            pk__in=scoped_staff(user).values("department_id")).order_by("name"),
        "approvers": approvers,
    })


@login_required
@require_POST
def promotion_rollback(request, pk):
    _require(request.user, "staff.promote_staff")
    promo = get_object_or_404(
        Promotion.objects.filter(staff_id__in=scoped_staff(request.user).values("pk")), pk=pk)
    ok, why = can_rollback(request.user, promo)
    if not ok and why.startswith("Only the Executive Secretary"):
        raise PermissionDenied(why)
    try:
        reversal = rollback_promotion(promo, reason=request.POST.get("reason", ""), approver=request.user)
    except PromotionError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Promotion rolled back: {reversal.staff.full_name} is "
                                  f"{reversal.to_designation.name} again.")
    nxt = request.POST.get("next") or ""
    from core.urlsafe import safe_next

    return redirect(safe_next(request, nxt, reverse("staff:promotion_history")))


# ===========================================================================
# ADVANCED QUERY
# ===========================================================================
def _query_from_request(request):
    data = request.GET
    saved = None
    if data.get("saved", "").isdigit():
        saved = _visible_queries(request.user).filter(pk=int(data["saved"])).first()
    if saved is not None and not data.getlist("field"):
        return saved.conditions, saved.logic, saved
    return staff_query.conditions_from_querydict(data), (data.get("logic") or "AND").upper(), saved


def _visible_queries(user):
    return SavedQuery.objects.filter(Q(owner=user) | Q(is_shared=True)).select_related("owner")


@login_required
def query_builder(request):
    user = request.user
    _require(user, "staff.view_staff")
    conditions, logic, saved = _query_from_request(request)
    base = scoped_staff(user).order_by("last_name", "first_name")
    error, page, ran = "", None, bool(conditions) or request.GET.get("run") == "1"
    try:
        conditions = staff_query.clean_conditions(conditions)
        if ran:
            qs = staff_query.build_queryset(conditions, logic, base)
            page = Paginator(qs, 50).get_page(request.GET.get("page"))
    except staff_query.QueryError as exc:
        error = str(exc)
        page = None
    return render(request, "staff/query/builder.html", {
        "conditions": conditions,
        "conditions_json": conditions,
        "logic": logic if logic in ("AND", "OR") else "AND",
        "schema": staff_query.builder_schema(user),
        "operators": list(staff_query.OPERATORS.items()),
        "error": error,
        "page": page,
        "ran": ran,
        "saved": saved,
        "described": [staff_query.describe(c) for c in conditions],
        "can_export": user.has_perm("staff.export_nominal_roll"),
        "can_share": user.has_perm("staff.manage_shared_queries"),
        "querystring": request.GET.urlencode(),
        "saved_queries": _visible_queries(user)[:8],
    })


@login_required
@require_GET
def query_export(request):
    user = request.user
    _require(user, "staff.view_staff")
    _require(user, "staff.export_nominal_roll")
    conditions, logic, _saved = _query_from_request(request)
    try:
        qs = staff_query.build_queryset(conditions, logic, scoped_staff(user))
    except staff_query.QueryError as exc:
        messages.error(request, str(exc))
        return redirect("staff:query")
    content, count = export_nominal_roll(qs)
    audit.log("export_nominal_roll", user, actor=user,
              changes={"rows": count, "layout": "query",
                       "conditions": [staff_query.describe(c) for c in staff_query.clean_conditions(conditions)],
                       "logic": logic}, note="Advanced query export")
    return _xlsx_response(content, f"NBTE_Staff_Query_{timezone.localdate():%Y%m%d}.xlsx")


@login_required
@require_POST
def query_save(request):
    user = request.user
    _require(user, "staff.view_staff")
    name = (request.POST.get("name") or "").strip()[:120]
    conditions = staff_query.conditions_from_querydict(request.POST)
    logic = (request.POST.get("logic") or "AND").upper()
    back = reverse("staff:query")
    try:
        conditions = staff_query.clean_conditions(conditions)
        staff_query.build_queryset(conditions, logic, Staff.objects.none())   # validate
    except staff_query.QueryError as exc:
        messages.error(request, f"Not saved: {exc}")
        return redirect(back)
    if not name:
        messages.error(request, "Give the query a name.")
        return redirect(back)
    shared = request.POST.get("is_shared") == "on"
    if shared and not user.has_perm("staff.manage_shared_queries"):
        raise PermissionDenied
    obj, created = SavedQuery.objects.get_or_create(owner=user, name=name)
    obj.description = (request.POST.get("description") or "").strip()
    obj.conditions, obj.logic, obj.is_shared = conditions, logic if logic in ("AND", "OR") else "AND", shared
    obj.save()
    messages.success(request, f"Query “{obj.name}” {'saved' if created else 'updated'}.")
    return redirect(f"{back}?saved={obj.pk}")


@login_required
def query_saved(request):
    _require(request.user, "staff.view_staff")
    queries = list(_visible_queries(request.user).order_by("-is_shared", "name"))
    for sq in queries:
        sq.described = [staff_query.describe(c) for c in sq.conditions]
        sq.can_delete = sq.owner_id == request.user.pk or (
            sq.is_shared and request.user.has_perm("staff.manage_shared_queries"))
    return render(request, "staff/query/saved.html", {"queries": queries})


@login_required
@require_POST
def query_delete(request, pk):
    _require(request.user, "staff.view_staff")
    sq = get_object_or_404(_visible_queries(request.user), pk=pk)
    if not (sq.owner_id == request.user.pk
            or (sq.is_shared and request.user.has_perm("staff.manage_shared_queries"))):
        raise PermissionDenied
    name = sq.name
    sq.delete()
    messages.success(request, f"Query “{name}” deleted.")
    return redirect("staff:query_saved")


# ===========================================================================
# TRANSFERS (staff file)
# ===========================================================================
@login_required
@require_POST
def staff_transfer(request, pk):
    """Move a staff member to another department and/or office (e.g. a zonal
    office). Staff.save() writes the StaffTransfer row and the audit entry."""
    from .models import StaffTransfer
    user = request.user
    _require(user, "staff.transfer_staff")
    staff = get_object_or_404(scoped_staff(user), pk=pk)
    back = reverse("staff:detail", args=[staff.pk])
    dept_id, office_id = request.POST.get("department", ""), request.POST.get("office", "")
    reason = (request.POST.get("reason") or "").strip()
    try:
        effective = date.fromisoformat(request.POST.get("effective_date") or "")
    except ValueError:
        effective = timezone.localdate()
    dept = Department.objects.filter(pk=int(dept_id), is_active=True).first() if dept_id.isdigit() else None
    if dept is None and office_id.isdigit():   # v2.2: an "office" is a unit in the Department list
        dept = Department.objects.filter(pk=int(office_id), is_active=True).first()
    office = None
    if dept is None:
        messages.error(request, "Choose the department or unit to transfer to.")
        return redirect(back)
    if not reason:
        messages.error(request, "Give a reason for the transfer (e.g. the posting letter reference).")
        return redirect(back)
    new_dept = dept or staff.department
    new_office = None
    if new_dept == staff.department and new_office == staff.office:
        messages.info(request, f"{staff.full_name} is already there — nothing changed.")
        return redirect(back)
    old_place = " · ".join(p.name for p in (staff.department, staff.office) if p) or "—"
    staff = Staff.objects.get(pk=staff.pk)          # fresh copy with the change-tracking snapshot
    staff.department, staff.office = new_dept, new_office
    staff._transfer_source = StaffTransfer.SOURCE_TRANSFER
    staff._transfer_reason = reason
    staff._transfer_date = effective
    staff._transfer_by = user
    staff.save()
    new_place = " · ".join(p.name for p in (new_dept, new_office) if p)
    audit.log("transfer", staff, actor=user, changes={"from": old_place, "to": new_place,
                                                       "effective": effective.isoformat()}, note=reason[:255])
    if request.POST.get("notify") == "on" and staff.user_id:
        from notifications.services import notify
        notify(staff.user, title="You have been transferred",
               body=f"From {old_place} to {new_place}, with effect from {effective.day} {effective:%B %Y}.",
               url=back, actor=user)
    messages.success(request, f"{staff.full_name} transferred to {new_place}.")
    return redirect(back)
