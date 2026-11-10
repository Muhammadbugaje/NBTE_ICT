"""Rotation page, assign dialog, pool settings (X27–X29) and the review
chain panel (X30). Rules live in ``assignments.rotation`` /
``assignments.rotation_queue`` / ``assignments.chain``; these views only
gather and render.

The rotation page is a queue of REAL work: documents, SSQs and new
institutions that are waiting for an officer in the director's department
(ES / super admin: any department). Nothing here creates work that does not
exist — every assignment goes through the service that owns the job.

URLs (assignments/rotation_urls.py, mounted at /qa/department/rotation/ and
/qa/chains/):
    board            GET   ?dept=<code>                  full page / #rot-panel
    assign_dialog    GET   ?job=<category>:<pk>          HTMX partial (dialog)
    assign           POST  job, chosen, reason           assign the waiting job
    pool_settings    GET/POST ?pool=<code>                director
    member_save      POST  <member pk>
    chain_detail     GET   <pk>                           full page hosting the panel
    chain_panel      GET   <pk>                           HTMX partial
    chain_act        POST  <pk>                           submit/approve/send_back/reject
"""
from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from qa_core.ui import is_htmx, render_qa, toast

from . import chain as chains
from . import rotation
from . import rotation_load as load
from . import rotation_queue as queue
from .models_chain import ReviewChain
from .models_rotation import ROTATION_KIND_CHOICES, RotationMember, RotationPool

KIND_CHIP = {"document_analysis": ("docs", "Docs"), "ssq_review": ("ssq", "SSQ"), "visit_panel": ("visit", "Visit"),
             "physical_verification": ("visit", "Visit"), "oversight_ict": ("over", "Oversight")}
IDLE_BADGE_DAYS = 7


def _msg(exc):
    if isinstance(exc, ValidationError):
        return "; ".join(exc.messages)
    return str(exc)


def _weeks(today, before=1, after=2):
    monday = today - datetime.timedelta(days=today.weekday())
    out = []
    for i in range(-before, after + 1):
        start = monday + datetime.timedelta(weeks=i)
        end = start + datetime.timedelta(days=6)
        label = (f"{start:%-d} to {end:%-d %b}" if start.month == end.month else f"{start:%-d %b} to {end:%-d %b}")
        out.append({"start": start, "end": end, "label": label, "now": i == 0})
    return out


def _short(inst):
    if inst is None:
        return ""
    name = inst.name or inst.code
    return name if len(name) <= 22 else (inst.code or name[:20])


def _pick_department(user, depts, dept_code):
    """The department asked for, else the user's own (directed) one, else the
    first with work waiting (ES), else the first."""
    import org
    dept = next((d for d in depts if d.code and d.code == dept_code), None)
    if dept is not None:
        return dept, None
    own = org.directed_department(user) or getattr(user, "department", None)
    counts = queue.waiting_by_department(depts) if queue.is_top(user) else None
    if counts:
        busy = next((d for d in depts if counts.get(d.pk)), None)
        if own is not None and counts.get(own.pk):
            return next(d for d in depts if d.pk == own.pk), counts
        if busy is not None:
            return busy, counts
    if own is not None:
        mine = next((d for d in depts if d.pk == own.pk), None)
        if mine is not None:
            return mine, counts
    return depts[0], counts


def _staff_rows(user, dept, weeks, today):
    """Who has what in the department, week by week (read-only)."""
    from .models import WorkAssignment
    lo = weeks[0]["start"]
    staff = load.department_staff_cached(dept, queue.department_staff)
    memberships = {}
    for m in RotationMember.objects.filter(pool__department=dept, pool__is_active=True, is_active=True,
                                           user__in=staff).select_related("pool"):
        memberships.setdefault(m.user_id, []).append(m)
    work = list(WorkAssignment.objects.filter(department=dept, assignee__in=staff,
                                              kind__in=[k for k, _ in ROTATION_KIND_CHOICES])
                .select_related("institution"))
    rows = []
    epoch = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    for u in staff:
        cells = [[] for _ in weeks]
        mine = [w for w in work if w.assignee_id == u.pk]
        for w in mine:
            when = timezone.localtime(w.assigned_at).date()
            try:
                when = datetime.date.fromisoformat((w.meta or {}).get("from", "")) or when
            except (TypeError, ValueError):
                pass
            show = [i for i, wk in enumerate(weeks) if wk["start"] <= when <= wk["end"]]
            if w.is_open and when < lo:
                show = [i for i, wk in enumerate(weeks) if wk["now"]]
            cls, label = KIND_CHIP.get(w.kind, ("docs", w.kind))
            for i in show:
                cells[i].append({"cls": cls, "kind": label, "text": _short(w.institution), "open": w.is_open})
        ms = memberships.get(u.pk, [])
        paused = [m for m in ms if m.is_paused]
        if paused:
            m = paused[0]
            for i, wk in enumerate(weeks):
                if wk["end"] >= today and wk["start"] <= m.paused_until:
                    cells[i].insert(0, {"cls": "leave", "kind": "Leave", "text": m.pause_reason or "Away",
                                        "open": False})
        stamps = [m.last_assigned_at for m in ms if m.last_assigned_at] + [w.assigned_at for w in mine]
        last = max(stamps, key=lambda x: x or epoch) if stamps else None
        idle = (timezone.now() - last).days if last else None
        name = u.get_full_name() or u.email
        rows.append({"user": u, "name": name, "role": getattr(u.role, "name", ""),
                     "open": load.open_load_all(u),
                     "cells": [{"chips": c, "now": weeks[i]["now"]} for i, c in enumerate(cells)],
                     "idle": idle if (idle is not None and idle >= IDLE_BADGE_DAYS) and not paused else None,
                     "never": last is None and not paused, "paused": paused[0] if paused else None,
                     "in_pool": bool(ms), "is_me": u.pk == user.pk,
                     "initials": "".join(p[0] for p in name.split()[:2]).upper()})
    return rows


def board_context(user, dept_code=None, *, done=None):
    """The rotation page of one department: the work waiting for an officer,
    grouped by category, with the next person in rotation for the next job."""
    depts = queue.departments_for(user)
    if not depts:
        raise PermissionDenied("Rotation is for the director of a department and the Executive Secretary.")
    dept, counts = _pick_department(user, depts, dept_code)
    # One batch for the whole page: the per-person numbers (open load, recent,
    # availability, conflicts, staff profiles) come from grouped queries. The
    # staff list is asked once and shared by the candidates, the suggestions
    # and the staff board; the numbers preload once staff and pool members
    # are known.
    with load.batch((), queue.ORDER) as b:
        staff = load.department_staff_cached(dept, queue.department_staff)
        member_users = [m.user for m in RotationMember.objects
                        .filter(Q(pool__department=dept) | Q(pool__kind=queue.OVERSIGHT, pool__department__isnull=True))
                        .filter(is_active=True, user__is_active=True)
                        .select_related("user")]
        b.register([*staff, *member_users])
        b.preload([*staff, *member_users], queue.ORDER)
        groups = queue.waiting([dept])
        for g in groups:
            rows, suggested = queue.candidates(g.head)
            g.head_suggested = suggested
            g.head_rows = [r for r in rows if r["eligible"]][:3]
        today = timezone.localdate()
        weeks = _weeks(today)
        staff_rows = _staff_rows(user, dept, weeks, today)
    pools = list(RotationPool.objects.active().filter(department=dept).order_by("kind"))
    if dept == queue.ict_department():
        pools += [p for p in RotationPool.objects.active().filter(kind="oversight_ict", department__isnull=True)]
    manage_pool = next((p for p in pools if rotation.can_manage_pool(user, p)), None)
    waiting_total = sum(g.count for g in groups)
    by_cat = {g.meta["code"]: g.count for g in groups}
    kpis = {"waiting": waiting_total, "staff": len(staff_rows),
            "open": sum(r["open"] for r in staff_rows),
            "idle": sum(1 for r in staff_rows if r["idle"] is not None or r["never"])}
    dept_options = depts if len(depts) > 1 else []
    if dept_options and counts is None:
        counts = queue.waiting_by_department(depts)
    return {"dept": dept, "depts": dept_options, "dept_counts": counts or {}, "groups": groups,
            "categories": [{**queue.CATEGORIES[c], "count": by_cat.get(c, 0)} for c in queue.ORDER],
            "waiting_total": waiting_total, "weeks": weeks, "rows": staff_rows, "kpis": kpis,
            "manage_pool": manage_pool, "pools": pools, "is_top": queue.is_top(user), "done": done,
            "can_override": rotation.can_override_constraints(user),
            "ict_pk": getattr(queue.ict_department(), "pk", None)}


def _render_board(request, dept_code=None, *, done=None, status=200):
    ctx = board_context(request.user, dept_code, done=done)
    return render_qa(request, "assignments/rotation_board.html", ctx,
                     {"rot-panel": "assignments/_rotation_panel.html"}, status=status)


@login_required
@require_GET
def board(request):
    return _render_board(request, request.GET.get("dept"))


# ---------------------------------------------------------------------------
# Assign a waiting job
# ---------------------------------------------------------------------------
def _require_queue(user):
    if not queue.can_open(user):
        raise PermissionDenied("Rotation is for the director of a department and the Executive Secretary.")


def _dialog_ctx(job, user, *, error="", chosen=None, reason=""):
    rows, suggested = queue.candidates(job)
    return {"job": job, "rows": rows, "suggested": suggested, "error": error,
            "eligible": [r for r in rows if r["eligible"]], "blocked": [r for r in rows if not r["eligible"]],
            "chosen": chosen if chosen is not None else (suggested["user_id"] if suggested else None),
            "reason": reason, "can_override": rotation.can_override_constraints(user)}


@login_required
@require_GET
def assign_dialog(request):
    _require_queue(request.user)
    job = queue.get_job(request.user, request.GET.get("job"))
    if job is None:
        raise Http404("This work is not waiting for assignment in your department.")
    return render(request, "assignments/_assign_dialog.html", _dialog_ctx(job, request.user))


@login_required
@require_POST
def assign(request):
    """Assign one waiting job. The job is looked up again in the user's own
    scope: a forged, already assigned or other department's job is refused."""
    _require_queue(request.user)
    key = request.POST.get("job", "")
    dept_code = request.POST.get("dept") or None
    reason = request.POST.get("reason", "")
    try:
        chosen = int(request.POST.get("chosen") or 0)
    except ValueError:
        chosen = 0
    job = queue.get_job(request.user, key)
    error = ""
    if job is None:
        error = "This work is no longer waiting for assignment in your department — someone may have assigned it."
    else:
        try:
            officer = queue.assign(job, by=request.user, chosen=chosen, reason=reason)
        except (ValidationError, PermissionDenied) as exc:
            error = _msg(exc)
    if error:
        if not is_htmx(request):
            messages.error(request, error)
            return redirect(f"{reverse('rotation:board')}?dept={dept_code or ''}")
        if job is None:
            resp = render(request, "assignments/_assign_gone.html", {"error": error}, status=422)
        else:
            resp = render(request, "assignments/_assign_dialog.html",
                          _dialog_ctx(job, request.user, error=error, chosen=chosen, reason=reason), status=422)
        resp["HX-Retarget"] = "#rot-dialog"
        resp["HX-Reswap"] = "innerHTML"
        return toast(resp, "error", "Not assigned", error)
    name = officer.get_full_name() or officer.email
    done = {"name": name, "title": job.title, "detail": job.detail, "category": job.meta["label"],
            "url": job.url}
    body = f"{name} now has {job.meta['label'].lower()}: {job.title}. They have been notified."
    if not is_htmx(request):
        messages.success(request, body)
        return redirect(f"{reverse('rotation:board')}?dept={job.department.code or ''}")
    ctx = board_context(request.user, job.department.code, done=done)
    resp = render(request, "assignments/_rotation_panel.html", {**ctx, "clear_dialog": True})
    resp["HX-Retarget"] = "#rot-panel"
    resp["HX-Reswap"] = "innerHTML"
    return toast(resp, "success", "Assigned", body)



# ---------------------------------------------------------------------------
# Pool settings (director)
# ---------------------------------------------------------------------------
def _parse_bool(v):
    return str(v).lower() in ("1", "true", "on", "yes")


@login_required
@require_http_methods(["GET", "POST"])
def pool_settings(request):
    _require_queue(request.user)  # director of a department, ES / super admin only
    allowed = {d.pk for d in queue.departments_for(request.user)}
    ict = queue.ict_department()
    manageable = [p for p in RotationPool.objects.select_related("department")
                  if (p.department_id in allowed or (p.department_id is None and ict is not None
                                                     and ict.pk in allowed))
                  and rotation.can_manage_pool(request.user, p)]
    if not manageable:
        raise PermissionDenied("Only the director edits the rotation pools.")
    pool = next((p for p in manageable if p.code == request.GET.get("pool", request.POST.get("pool"))), None) \
        or next((p for p in manageable if p.department_id == request.user.department_id), manageable[0])
    error, saved = "", False
    if request.method == "POST":
        action = request.POST.get("action", "rules")
        if action == "sync":
            n = rotation.sync_members(pool)
            saved = f"{n} person(s) added" if n else "Everyone is already in the pool"
        else:
            rules = dict(pool.rules or {})
            cap = (request.POST.get("cap") or "").strip()
            if cap:
                try:
                    rules["cap"] = max(0, int(cap))
                except ValueError:
                    error = "The cap must be a whole number."
            else:
                rules.pop("cap", None)
            w = {}
            for k in ("load", "fairness", "recency"):
                raw = (request.POST.get(f"w_{k}") or "").strip()
                if raw:
                    try:
                        w[k] = round(float(raw) / (100.0 if float(raw) > 1 else 1.0), 3)
                    except ValueError:
                        error = "Weights are numbers (percent)."
            if w:
                rules["weights"] = w
            toggles = {}
            for code, _label in rotation.constraint_labels():
                val = request.POST.get(f"rule_{code}", "inherit")
                if val in ("on", "off"):
                    toggles[code] = val == "on"
            rules["rules"] = toggles
            if not toggles:
                rules.pop("rules", None)
            if not error:
                pool.rules = rules
                pool.auto_assign = _parse_bool(request.POST.get("auto_assign"))
                pool.save(update_fields=["rules", "auto_assign", "updated_at"])
                from core import audit
                audit.log("rotation_pool_rules", pool, actor=request.user, changes={"rules": rules})
                saved = "Rules saved"
    cfg = rotation.settings_for(pool)
    rule_rows = []
    for code, label in rotation.constraint_labels():
        over = (pool.rules or {}).get("rules", {}).get(code)
        rule_rows.append({"code": code, "label": label, "state": "inherit" if over is None else ("on" if over else "off"),
                          "effective": cfg.rule_on(code)})
    ctx = {"pool": pool, "pools": manageable, "cfg": cfg, "rule_rows": rule_rows, "error": error, "saved": saved,
           "members": pool.members.select_related("user", "user__role").order_by("user__first_name", "user_id"),
           "weights_pct": {k: int(round(v * 100)) for k, v in cfg.weights.items()},
           "own_cap": (pool.rules or {}).get("cap", "")}
    resp = render_qa(request, "assignments/pool_settings.html", ctx,
                     {"rot-settings": "assignments/_pool_settings_panel.html"})
    if saved and request.method == "POST":
        toast(resp, "success", "Saved", saved if isinstance(saved, str) else "")
    elif error:
        toast(resp, "error", "Not saved", error)
    return resp


@login_required
@require_POST
def member_save(request, pk):
    _require_queue(request.user)
    m = get_object_or_404(RotationMember.objects.select_related("pool", "user"), pk=pk)
    if not rotation.can_manage_pool(request.user, m.pool):
        raise PermissionDenied
    error = ""
    try:
        m.weight = Decimal(request.POST.get("weight") or "1").quantize(Decimal("0.01"))
        if m.weight <= 0 or m.weight > 5:
            raise InvalidOperation
    except InvalidOperation:
        error = "Weight is a number between 0.1 and 5."
    cap = (request.POST.get("max_concurrent") or "").strip()
    m.max_concurrent = int(cap) if cap.isdigit() else None
    m.is_active = _parse_bool(request.POST.get("is_active"))
    until = (request.POST.get("paused_until") or "").strip()
    try:
        m.paused_until = datetime.date.fromisoformat(until) if until else None
    except ValueError:
        error = "Pause date is not a date."
    m.pause_reason = (request.POST.get("pause_reason") or "")[:120]
    if not error:
        m.save()
        from core import audit
        audit.log("rotation_member_saved", m, actor=request.user,
                  changes={"weight": str(m.weight), "max_concurrent": m.max_concurrent, "active": m.is_active,
                           "paused_until": str(m.paused_until or "")})
    resp = render(request, "assignments/_member_row.html", {"m": m, "error": error, "saved": not error},
                  status=422 if error else 200)
    return toast(resp, "error" if error else "success", "Not saved" if error else "Saved",
                 error or (m.user.get_full_name() or m.user.email))


# ---------------------------------------------------------------------------
# Review chain panel
# ---------------------------------------------------------------------------
def can_view_chain(user, c):
    import org
    if rotation._is_top(user):
        return True
    if user.pk in {c.assignee_id, c.created_by_id} or c.steps.filter(actor=user).exists():
        return True
    if user in chains.eligible_actors(c):
        return True
    return bool(c.department_id and org.is_director(user, c.department_id))


def _chain_or_404(request, pk):
    c = get_object_or_404(ReviewChain.objects.select_related("policy", "assignee", "department"), pk=pk)
    if not can_view_chain(request.user, c):
        raise Http404
    return c


@login_required
@require_GET
def chain_detail(request, pk):
    c = _chain_or_404(request, pk)
    return render_qa(request, "assignments/chain_detail.html", {"p": chains.panel(c, request.user)},
                     {"chain-panel": "assignments/_chain_panel.html"})


@login_required
@require_GET
def chain_panel(request, pk):
    c = _chain_or_404(request, pk)
    return render(request, "assignments/_chain_panel.html", {"p": chains.panel(c, request.user)})


@login_required
@require_POST
def chain_act(request, pk):
    c = _chain_or_404(request, pk)
    action = request.POST.get("action")
    remark = request.POST.get("remark", "")
    error = ""
    try:
        if action == "submit":
            chains.submit(c, actor=request.user, note=remark)
        elif action == "approve":
            chains.approve(c, actor=request.user, remark=remark)
        elif action == "send_back":
            chains.send_back(c, actor=request.user, to_step=request.POST.get("to_step") or None, remark=remark)
        elif action == "reject":
            chains.reject(c, actor=request.user, remark=remark)
        else:
            error = "Unknown action."
    except (ValidationError, PermissionDenied) as exc:
        error = _msg(exc)
    c.refresh_from_db()
    p = chains.panel(c, request.user)
    p["error"] = error
    resp = render(request, "assignments/_chain_panel.html", {"p": p}, status=422 if error else 200)
    return toast(resp, "error" if error else "success", "Not done" if error else "Done", error)
