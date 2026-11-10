"""v2.2 — facilities and equipment register: import, export, analysis,
assignment and the link to the SSQ curriculum catalogue."""
from __future__ import annotations

import io
import zipfile
from collections import Counter, defaultdict

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from . import docx_parser
from .diffspecs import FacilityItemDiff, _letter
from .models import FacilityItem, FacilityProgramme, FacilitySection

MANAGERS_GROUP = "Facilities & Equipment – Managers"


# ---------------------------------------------------------------------------
# Who
# ---------------------------------------------------------------------------
def can_view(user):
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "user_type", "staff") == "staff")


def can_manage(user):
    if not getattr(user, "is_authenticated", False):
        return False
    import org

    return bool(user.is_superuser or org.is_es(user) or org.is_super_admin(user)
                or user.has_perm("facilities.manage_facilities"))


def can_assign_handlers(user):
    """ES, super admin, or the director of the Curriculum department."""
    if not getattr(user, "is_authenticated", False):
        return False
    import org
    from qa_core.departments import qa_department

    if user.is_superuser or org.is_es(user) or org.is_super_admin(user):
        return True
    for code in ("curriculum-office", "curr-dept", "curriculum"):
        dept = qa_department(code)
        if dept is not None and org.is_director(user, dept):
            return True
    return False


def managers_group():
    from django.contrib.auth.models import Group, Permission

    group, _ = Group.objects.get_or_create(name=MANAGERS_GROUP)
    perm = Permission.objects.filter(codename="manage_facilities", content_type__app_label="facilities").first()
    if perm is not None:
        group.permissions.add(perm)
    return group


def handlers():
    from accounts.models import User

    return list(User.objects.filter(is_active=True, groups__name=MANAGERS_GROUP).distinct()
                .order_by("first_name", "last_name"))


def set_handler(user, target, *, add, actor):
    if not can_assign_handlers(actor):
        raise PermissionDenied("Only the ES, a super admin or the Curriculum director can assign handlers.")
    group = managers_group()
    (group.user_set.add if add else group.user_set.remove)(target)
    from core import audit

    audit.log("other", target, actor=actor,
              note=f"{'Added to' if add else 'Removed from'} {MANAGERS_GROUP}")


# ---------------------------------------------------------------------------
# Word files → rows
# ---------------------------------------------------------------------------
def parse_upload(upload):
    """A .docx or a .zip of .docx files → list of parsed programmes."""
    name = (getattr(upload, "name", "") or "").lower()
    data = upload.read()
    parsed = []
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = [m for m in zf.namelist() if m.lower().endswith(".docx") and "__macosx" not in m.lower()
                       and not m.rsplit("/", 1)[-1].startswith(("~$", "."))]
            if len(members) > 400:
                raise ValidationError("The zip holds more than 400 Word files.")
            for m in members:
                with zf.open(m) as fh:
                    parsed.append(docx_parser.parse(io.BytesIO(fh.read()), m))
    elif name.endswith(".docx"):
        parsed.append(docx_parser.parse(io.BytesIO(data), name))
    else:
        raise ValidationError("Upload a Word file (.docx) or a .zip of Word files.")
    return [p for p in parsed if p["programme"]]


def rows_to_workbook(rows):
    """Rows (dicts keyed by spec fields) → an .xlsx in the import template layout."""
    from openpyxl import Workbook

    spec = FacilityItemDiff()
    wb = Workbook()
    ws = wb.active
    ws.title = "Items"
    ws.append(spec.header())
    for r in rows:
        ws.append([r.get(c.field, "") if c.field not in ("is_critical", "is_active")
                   else ("Yes" if r.get(c.field, c.field == "is_active") else "No") for c in spec.columns])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def parsed_rows(parsed_list):
    rows = []
    for p in parsed_list:
        for r in docx_parser.rows_from(p):
            rows.append({"programme": r["programme"], "levels": r["levels"], "facility": r["facility"],
                         "group": r["group"], "name": r["name"], "nd_qty": r["nd_qty"], "hnd_qty": r["hnd_qty"],
                         "remarks": r["remarks"], "is_active": True, "is_critical": False,
                         "sort_order": r["sort_order"]})
    return rows


@transaction.atomic
def load_parsed(parsed_list, *, actor=None, update_existing=False):
    _BATCH["depth"] += 1
    try:
        out = _load_parsed(parsed_list, actor=actor, update_existing=update_existing)
        # v2.3: every curriculum in the register is selectable as a programme.
        out["masters_added"], out["masters_linked"] = import_master_list(actor=actor)
        return out
    finally:
        _BATCH["depth"] -= 1
        bump_stats()


def _load_parsed(parsed_list, *, actor=None, update_existing=False):
    """Direct load (first import / seeding). Existing items are left alone
    unless ``update_existing``; nothing is ever deleted. Returns counts."""
    made = updated = programmes = 0
    for p in parsed_list:
        prog = FacilityProgramme.objects.filter(name__iexact=p["programme"]).first()
        if prog is None:
            prog = FacilityProgramme.objects.create(name=p["programme"], levels=", ".join(p["levels"]),
                                                    source=p.get("source", "")[:500],
                                                    notes="\n".join(p.get("notes") or []))
            programmes += 1
        _link_master(prog)
        for s_order, sec in enumerate(p["sections"], 1):
            section, _ = FacilitySection.objects.get_or_create(
                programme=prog, name=sec["name"][:255], defaults={"code": sec["code"] or _letter(s_order),
                                                                  "sort_order": s_order})
            for i_order, it in enumerate(sec["items"], 1):
                obj = FacilityItem.objects.filter(section=section, group=(it.get("group") or "")[:255],
                                                  name__iexact=it["name"][:500]).first()
                if obj is None:
                    FacilityItem.objects.create(programme=prog, section=section, group=(it.get("group") or "")[:255],
                                                name=it["name"][:500], nd_qty=it["nd"][:60], hnd_qty=it["hnd"][:60],
                                                remarks=(it.get("remarks") or "")[:500],
                                                sort_order=i_order, updated_by=actor)
                    made += 1
                elif update_existing and (obj.nd_qty != it["nd"] or obj.hnd_qty != it["hnd"]):
                    obj.nd_qty, obj.hnd_qty, obj.updated_by = it["nd"][:60], it["hnd"][:60], actor
                    obj.save()
                    updated += 1
    return {"programmes": programmes, "items": made, "updated": updated}


def _link_master(prog):
    if prog.programme_master_id:
        return
    try:
        from institutions.models import ProgrammeMaster
    except Exception:
        return
    master = ProgrammeMaster.objects.filter(name__iexact=prog.name).first()
    if master is not None:
        prog.programme_master = master
        prog.save(update_fields=["programme_master", "updated_at"])


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
STATS_VERSION_KEY = "facilities:stats:version"


_BATCH = {"depth": 0}


def bump_stats(*_args, **_kwargs):
    """Invalidate the cached register statistics (any item/section/programme change)."""
    from django.core.cache import cache

    if _BATCH["depth"]:
        return          # inside a bulk load: bumped once at the end

    try:
        cache.set(STATS_VERSION_KEY, (cache.get(STATS_VERSION_KEY) or 0) + 1, None)
    except Exception:
        pass


def _stats():
    """The heavy aggregates over ~15,000 lines, cached for 10 minutes and
    refreshed as soon as anything in the register changes (v2.2)."""
    from django.core.cache import cache
    from django.db.models.functions import Lower

    key = f"facilities:stats:{cache.get(STATS_VERSION_KEY) or 0}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    items = FacilityItem.objects.filter(is_active=True)
    stats = {
        "item_counts": dict(items.order_by().values_list("programme_id").annotate(c=Count("pk"))),
        "section_counts": dict(FacilitySection.objects.order_by().values_list("programme_id")
                               .annotate(c=Count("pk"))),
        "unquantified": items.filter(nd_qty_num__isnull=True, hnd_qty_num__isnull=True).count(),
        "common": [{"name": r["n"].capitalize(), "programmes": r["c"]} for r in
                   items.annotate(n=Lower("name")).values("n").annotate(c=Count("programme", distinct=True))
                   .order_by("-c", "n")[:15]],
        "item_count": items.count(),
        "section_count": FacilitySection.objects.filter(programme__is_active=True).count(),
        "critical": items.filter(is_critical=True).count(),
    }
    cache.set(key, stats, 600)
    return stats


def overview():
    st = _stats()
    progs = list(FacilityProgramme.objects.filter(is_active=True)
                 .select_related("assigned_to", "programme_master").order_by("name"))
    for p in progs:
        p.n_items = st["item_counts"].get(p.pk, 0)
        p.n_sections = st["section_counts"].get(p.pk, 0)
    level = Counter(p.levels or "—" for p in progs)
    biggest = sorted(progs, key=lambda p: -p.n_items)[:10]
    top = max([p.n_items for p in biggest] + [1])
    for p in biggest:
        p.pct = round(100 * p.n_items / top)
    return {
        "programmes": progs, "programme_count": len(progs), "item_count": st["item_count"],
        "section_count": st["section_count"],
        "levels": sorted(level.items()), "unquantified": st["unquantified"],
        "empty": [p for p in progs if p.n_items == 0], "unassigned": [p for p in progs if not p.assigned_to_id],
        "unlinked": [p for p in progs if not p.programme_master_id], "common": st["common"], "biggest": biggest,
        "critical": st["critical"],
    }


def search(q, limit=100):
    q = " ".join((q or "").split())
    if len(q) < 2:
        return []
    return list(FacilityItem.objects.filter(is_active=True, name__icontains=q)
                .select_related("programme", "section").order_by("programme__name")[:limit])


# ---------------------------------------------------------------------------
# Link to the SSQ curriculum catalogue (shortfall scoring)
# ---------------------------------------------------------------------------
@transaction.atomic
def sync_to_catalogue(prog, *, actor, level="ND"):
    """Publish this programme's active items as SSQ catalogue requirements
    (accreditation.CatalogueItem, kind equipment / facility). Quantities use
    ``level`` (ND or HND; falls back to the other when blank). Existing rows
    are updated; rows for items no longer in the list are retired, never
    deleted."""
    if not can_manage(actor):
        raise PermissionDenied
    from accreditation.models import CatalogueItem

    if prog.programme_master_id is None:
        raise ValidationError("Link this programme to the national programme master list first.")
    seen, made, changed = set(), 0, 0
    for sec in prog.sections.all():
        CatalogueItem.objects.update_or_create(
            programme_master=prog.programme_master, kind=CatalogueItem.FACILITY, name=sec.name[:200],
            defaults={"required_quantity": 1, "is_active": True, "sort_order": sec.sort_order,
                      "programme_name": prog.name[:200]})
        seen.add((CatalogueItem.FACILITY, sec.name[:200].casefold()))
    for it in prog.items.filter(is_active=True).select_related("section"):
        qty = (it.hnd_qty_num if level == "HND" else it.nd_qty_num) or it.nd_qty_num or it.hnd_qty_num or 1
        obj, created = CatalogueItem.objects.update_or_create(
            programme_master=prog.programme_master, kind=CatalogueItem.EQUIPMENT, name=it.name[:200],
            defaults={"required_quantity": qty, "is_critical": it.is_critical, "is_active": True,
                      "sort_order": it.section.sort_order * 1000 + it.sort_order, "programme_name": prog.name[:200],
                      "min_spec": (it.remarks or "")[:255]})
        made += created
        changed += not created
        seen.add((CatalogueItem.EQUIPMENT, it.name[:200].casefold()))
    retired = 0
    for row in CatalogueItem.objects.filter(programme_master=prog.programme_master, is_active=True):
        if (row.kind, row.name.casefold()) not in seen:
            row.is_active = False
            row.save(update_fields=["is_active"])
            retired += 1
    prog.last_synced_to_catalogue = timezone.now()
    prog.save(update_fields=["last_synced_to_catalogue", "updated_at"])
    from core import audit

    audit.log("other", prog, actor=actor, note=f"Synced to SSQ catalogue: {made} new, {changed} updated, "
                                                 f"{retired} retired ({level})")
    return {"new": made, "updated": changed, "retired": retired}


# ---------------------------------------------------------------------------
# v2.3: the national programme master list from the curriculum register
# ---------------------------------------------------------------------------
@transaction.atomic
def sync_master_list_after_migrate(sender=None, using="default", **kwargs):
    """post_migrate: an existing database with the register but no master
    rows for it gets them on its next ``migrate`` (quick no-op otherwise)."""
    if using != "default":
        return
    try:
        if FacilityProgramme.objects.filter(is_active=True, programme_master__isnull=True).exists():
            import_master_list()
    except Exception:  # tables not ready yet (first migrate) — the seed does it later
        import logging

        logging.getLogger(__name__).debug("programme master sync skipped", exc_info=True)


def import_master_list(*, actor=None):
    """Make sure every ND/HND curriculum in the register is on the programme
    master list (one row per programme and level), and link each register
    programme to its master row (ND first). Idempotent; never deletes or renames
    an existing master row. Returns (created, linked)."""
    from institutions.models import ProgrammeLevel, ProgrammeMaster

    levels = {lv.code: lv for lv in ProgrammeLevel.objects.filter(code__in=("nd", "hnd"))}
    created = linked = 0
    for prog in FacilityProgramme.objects.filter(is_active=True).order_by("name"):
        codes = [c.strip().lower() for c in (prog.levels or "").replace("&", ",").split(",") if c.strip()]
        codes = [c for c in ("nd", "hnd") if c in codes] or ["nd"]
        first = None
        for code in codes:
            level = levels.get(code)
            if level is None:
                continue
            master = (ProgrammeMaster.objects.filter(name__iexact=prog.name, level=level).first())
            if master is None:
                master = ProgrammeMaster(name=prog.name, level=level, curriculum_ref="NBTE curriculum register",
                                         notes="Added from the NBTE ND/HND curriculum register (facilities and "
                                               "equipment index).")
                master.save()
                created += 1
            first = first or master
        if first is not None and prog.programme_master_id is None:
            prog.programme_master = first
            prog.save(update_fields=["programme_master", "updated_at"])
            linked += 1
    if created or linked:
        try:
            from core import audit

            audit.log("other", FacilityProgramme.objects.first(), actor=actor,
                      note=f"Programme master list: {created} added, {linked} linked from the curriculum register")
        except Exception:
            pass
    return created, linked
