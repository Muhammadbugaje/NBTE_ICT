"""v2.4 — the SSQ curriculum catalogue drawn from the facilities register.

The facilities register (``facilities.FacilityProgramme`` / ``FacilityItem``,
imported from the NBTE curricula or the facilities workbook) is the source of
truth for what an ND/HND programme must have. SSQ ``catalogue`` fields and the
shortfall score work on ``CatalogueItem`` rows (institutions enter quantities
against stable item ids, R-22), so a programme's register list is
*materialised* into catalogue rows the first time an SSQ, a review screen or a
visit report needs it, and refreshed whenever the register changed since.

* level-aware: an ND programme gets the ND quantities, an HND programme the
  HND ones (rows are keyed by the programme's own master-list row);
* an item with a quantity only for the other level is not required at this
  level and is left out; an item with no number at all counts as 1;
* register facilities (laboratories, workshops, studios …) become
  ``facility`` rows with a required quantity of 1, register items become
  ``equipment`` rows (critical flag and remarks carried over);
* nothing is ever deleted: rows whose register line disappeared are retired.

Works whether or not the register has been imported: with no register list
for the programme nothing happens and the catalogue falls back to rows
maintained by hand (Settings → Curriculum catalogue).
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Max, Q

log = logging.getLogger(__name__)

SOURCE_NOTE = "facilities register"


def level_code(programme):
    level = getattr(programme, "level", None)
    code = (getattr(level, "code", "") or "").lower()
    return "hnd" if code.startswith("hnd") or code == "p-hnd" else "nd"


def register_for(programme):
    """The active register programme for an institution programme: linked to
    its master-list row, else matched by name (master name, then own name)."""
    try:
        from facilities.models import FacilityProgramme
    except Exception:  # pragma: no cover - facilities app missing
        return None
    if programme is None:
        return None
    qs = FacilityProgramme.objects.filter(is_active=True)
    master = getattr(programme, "master", None) if getattr(programme, "master_id", None) else None
    if master is not None:
        found = qs.filter(programme_master=master).first()
        if found is not None:
            return found
        found = qs.filter(name__iexact=master.name).first()
        if found is not None:
            return found
    return qs.filter(name__iexact=programme.name).first()


def _register_stamp(prog):
    from facilities.models import FacilityItem

    newest = FacilityItem.objects.filter(programme=prog).aggregate(m=Max("updated_at"))["m"]
    return max([d for d in (newest, prog.updated_at) if d is not None], default=None)


def _required(item, level):
    own = item.hnd_qty_num if level == "hnd" else item.nd_qty_num
    other = item.nd_qty_num if level == "hnd" else item.hnd_qty_num
    if own is not None:
        return own
    own_text = (item.hnd_qty if level == "hnd" else item.nd_qty or "").strip()
    if other is not None and own_text in ("", "-", "–", "—", "nil", "Nil", "N/A", "n/a"):
        return None  # only required at the other level
    return 1


def needs_refresh(programme, prog=None):
    from .models import CatalogueItem

    prog = prog or register_for(programme)
    if prog is None:
        return False
    base = ({"programme_master_id": programme.master_id} if getattr(programme, "master_id", None)
            else {"programme_master__isnull": True})
    marked = CatalogueItem.objects.filter(**base).filter(programme_name__iexact=prog.name[:200])
    if not marked.exists():
        return True
    stamp = _register_stamp(prog)
    newest = marked.aggregate(m=Max("updated_at"))["m"]
    return bool(stamp and newest and stamp > newest)


@transaction.atomic
def materialise(programme, *, force=False):
    """Bring the programme's catalogue rows in line with its register list.
    Returns {"new", "updated", "retired"} or None when there is no register
    list for the programme (or nothing to do)."""
    from .models import CatalogueItem

    prog = register_for(programme)
    if prog is None:
        return None
    if not force and not needs_refresh(programme, prog):
        return None
    level = level_code(programme)
    master_id = getattr(programme, "master_id", None)
    category_id = getattr(programme, "category_id", None)
    base = {"programme_master_id": master_id} if master_id else {"programme_master__isnull": True}
    name_for_rows = prog.name[:200]
    existing = {(r.kind, r.name.casefold()): r for r in CatalogueItem.objects.filter(**base).filter(
        Q(programme_name__iexact=name_for_rows) | Q(programme_name__iexact=programme.name))}
    seen, made, changed = set(), 0, 0

    def put(kind, name, defaults):
        nonlocal made, changed
        ident = (kind, name.casefold())
        seen.add(ident)
        row = existing.get(ident)
        if row is None:
            CatalogueItem.objects.create(programme_master_id=master_id, kind=kind, name=name,
                                         programme_name=name_for_rows, category_id=category_id, **defaults)
            made += 1
            return
        dirty = [f for f, v in defaults.items() if getattr(row, f) != v]
        if row.programme_name != name_for_rows:
            row.programme_name = name_for_rows
            dirty.append("programme_name")
        if dirty:
            for f, v in defaults.items():
                setattr(row, f, v)
            row.save()
            changed += 1
        else:
            row.save(update_fields=["updated_at"])  # marks the row as in step with the register

    sections = list(prog.sections.all().order_by("sort_order", "code"))
    for sec in sections:
        put(CatalogueItem.FACILITY, sec.name[:200],
            {"required_quantity": 1, "is_active": True, "sort_order": sec.sort_order, "unit": "no.",
             "min_spec": f"{sec.code}. {sec.name}"[:255] if sec.code else sec.name[:255], "is_critical": True})
    for item in prog.items.filter(is_active=True).select_related("section").order_by("section__sort_order",
                                                                                         "sort_order"):
        qty = _required(item, level)
        if qty is None:
            continue
        name = item.name[:200]
        if (CatalogueItem.EQUIPMENT, name.casefold()) in seen:
            continue  # the same item listed under two facilities counts once
        spec = " · ".join(x for x in (item.section.name, item.group, item.remarks) if x)
        put(CatalogueItem.EQUIPMENT, name,
            {"required_quantity": qty, "is_active": True, "is_critical": item.is_critical,
             "sort_order": (item.section.sort_order or 0) * 1000 + item.sort_order, "unit": "no.",
             "min_spec": spec[:500]})
    retired = 0
    for ident, row in existing.items():
        if ident not in seen and row.is_active and row.programme_name.casefold() == name_for_rows.casefold():
            row.is_active = False
            row.save(update_fields=["is_active", "updated_at"])
            retired += 1
    if made or changed or retired:
        log.info("catalogue for %s (%s) from the register: %s new, %s updated, %s retired",
                 programme.name, level.upper(), made, changed, retired)
    return {"new": made, "updated": changed, "retired": retired}


def ensure(programme):
    """Never breaks a page: a failure is logged and the catalogue stays as it is."""
    if programme is None:
        return None
    try:
        with transaction.atomic():
            return materialise(programme)
    except Exception:  # pragma: no cover - defensive
        log.exception("could not materialise the register for %s", getattr(programme, "pk", programme))
        return None


def register_summary(programme):
    """What the register says for this programme (for review/report screens)."""
    prog = register_for(programme)
    if prog is None:
        return None
    level = level_code(programme)
    items = list(prog.items.filter(is_active=True))
    required = [i for i in items if _required(i, level) is not None]
    return {"programme": prog, "level": level.upper(), "facilities": prog.sections.count(),
            "items": len(required), "critical": sum(1 for i in required if i.is_critical)}
