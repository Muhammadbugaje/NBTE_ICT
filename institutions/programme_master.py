"""Programme master list (v2.1 R.4, X44, spec G10.d).

* ``search_master(q)`` - type-ahead for the portal "Add programme" sheet.
* ``body_covers(body, ...)`` - a body's scope check for manual entries.
* ``add_programme(inst, ...)`` - every programme add goes through here: from the
  master list (body filled in) or typed by hand (body required, scope checked,
  flagged ``not_in_master`` and queued for NTIRD).
* ``backfill(...)`` - one master row per distinct (name, level) of existing
  programmes and body mappings; used by the data migration.
* ``relevant_masters(inst)`` / ``picker(inst, ...)`` - the catalogue rows an
  institution's type may add (Discipline.institution_types), grouped by
  programme for the portal picker.
* ``hnd_error(inst, name, level)`` - the ND/HND rule: an HND needs the ND of the
  same programme (on the onboarding list, or at an approved institution
  accredited/interim or being added).
"""
from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.db import transaction

from config import registry as config


def normalise(text):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split())


def search_master(q, *, level=None, limit=12):
    from .models import ProgrammeMaster

    key = normalise(q)
    if len(key.replace(" ", "")) < 2:
        return []
    words = key.split()
    qs = ProgrammeMaster.objects.filter(is_active=True).select_related("level", "professional_body")
    for w in words:
        qs = qs.filter(search_key__icontains=w)
    if level:
        qs = qs.filter(level=level)

    def rank(m):
        name = normalise(m.name)
        aliases = [normalise(a) for a in (m.aliases or "").splitlines() if a.strip()]
        if name.startswith(key):
            r = 0
        elif any(a.startswith(key) for a in aliases):
            r = 1
        elif all(any(t.startswith(w) for t in name.split()) for w in words):
            r = 2
        else:
            r = 3
        return (r, m.name.lower(), m.level.sort_order if m.level_id else 0)

    return sorted(qs[:200], key=rank)[:limit]


# ---------------------------------------------------------------------------
# Institution type -> relevant catalogue programmes
# ---------------------------------------------------------------------------
def allowed_discipline_ids(inst):
    """Disciplines whose programmes ``inst`` may add, or None for "all".
    A discipline mapped to no institution type is offered to everyone; an
    institution without a type sees everything."""
    from .models import Discipline

    type_id = getattr(inst, "institution_type_id", None)
    if not type_id:
        return None
    rows = list(Discipline.objects.prefetch_related("institution_types"))
    if not any(d.institution_types.all() for d in rows):
        return None  # no mapping configured at all: show everything
    return {d.pk for d in rows if not d.institution_types.all()
            or any(t.pk == type_id for t in d.institution_types.all())}


def relevant_masters(inst):
    """Active master rows relevant to ``inst``'s institution type. A row's
    discipline is its own, else its programme category's; a row with neither
    is shown to everyone."""
    from django.db.models import Q

    from .models import ProgrammeMaster

    qs = (ProgrammeMaster.objects.filter(is_active=True)
          .select_related("level", "professional_body", "programme_category", "discipline"))
    ids = allowed_discipline_ids(inst) if inst is not None else None
    if ids is None:
        return qs
    return qs.filter(Q(discipline_id__in=ids)
                     | Q(discipline__isnull=True, programme_category__discipline_id__in=ids)
                     | Q(discipline__isnull=True, programme_category__isnull=True)
                     | Q(discipline__isnull=True, programme_category__discipline__isnull=True))


# ---------------------------------------------------------------------------
# ND / HND rule
# ---------------------------------------------------------------------------
ND_ESTABLISHED = ("accredited", "interim", "approved_pending_body_letter")
ND_IN_PROGRESS = ("proposed", "resource_inspected", "approval_to_commence")


def level_code(level):
    return (getattr(level, "code", "") or "").strip().lower()


def field_key(name):
    """The programme without its level and "Technology": the ND and HND of the
    same programme share it ("HND Marine Engineering Technology" ==
    "Marine Engineering")."""
    name = (name or "").lower()
    name = re.sub(r"^\s*(h?nd)\s+", "", name)
    name = re.sub(r"\btechnology\b", "", name)
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def is_onboarding(inst):
    from .models import Institution

    return getattr(inst, "status", None) in (Institution.PROPOSED, Institution.UNDER_REVIEW)


def nd_statuses(inst):
    """Statuses of an ND that let the institution add the HND of the same
    programme: onboarding = anything on its list; approved = an ND it runs
    (accredited / interim) or one it is adding now."""
    if is_onboarding(inst):
        return None  # any status except dropped
    return ND_ESTABLISHED + ND_IN_PROGRESS


def nd_keys(inst, statuses=None):
    """{field_key: ND programme} at ``inst`` (not dropped)."""
    from .models import Programme

    qs = Programme.unscoped.filter(institution=inst, level__code__iexact="nd").exclude(status="dropped")
    if statuses is not None:
        qs = qs.filter(status__in=statuses)
    return {field_key(p.name): p for p in qs.only("pk", "name", "status")}


def hnd_error(inst, name, level):
    """"" or why ``inst`` may not add the HND ``name`` now."""
    if level_code(level) != "hnd":
        return ""
    if field_key(name) in nd_keys(inst, nd_statuses(inst)):
        return ""
    if is_onboarding(inst):
        return (f"Add ND {name} first. You can offer an ND alone, or the ND and HND of the same programme, "
                "but never an HND without its ND.")
    return (f"HND {name} needs the ND of the same programme at your institution (accredited, interim or being "
            "proposed now). Propose the ND first.")


def orphan_hnds(programmes):
    """HND programmes in ``programmes`` whose ND (same programme) is not there."""
    progs = [p for p in programmes if p.status != "dropped"]
    nds = {field_key(p.name) for p in progs if level_code(p.level) == "nd"}
    return [p for p in progs if level_code(p.level) == "hnd" and field_key(p.name) not in nds]


def dependent_hnds(programme):
    """Proposed HND programmes that lean on the ND ``programme``."""
    from .models import Programme

    if level_code(programme.level) != "nd":
        return []
    key = field_key(programme.name)
    return [p for p in Programme.unscoped.filter(institution_id=programme.institution_id, level__code__iexact="hnd")
            .exclude(status="dropped").exclude(pk=programme.pk) if field_key(p.name) == key]


# ---------------------------------------------------------------------------
# The portal picker: every relevant programme, grouped by name, one choice per level
# ---------------------------------------------------------------------------
def picker(inst, *, picked=None):
    """{"groups": [...], "count": n} for the programme picker.

    Each group is one programme name with its levels (ND, HND …). A level is
    ``added`` when the institution already has it, ``blocked`` (with a hint)
    when it is an HND whose ND the institution does not have yet."""
    from .models import Programme

    have = {(normalise(n), lv) for n, lv in Programme.unscoped.filter(institution=inst)
            .values_list("name", "level_id")}
    nds = set(nd_keys(inst, nd_statuses(inst)))
    picked = picked if isinstance(picked, (list, tuple, set)) else ([picked] if picked else [])
    picked_pks = {int(getattr(x, "pk", x)) for x in picked if str(getattr(x, "pk", x)).isdigit()}
    groups, by_key = [], {}
    rows = sorted(relevant_masters(inst), key=lambda m: (m.name.lower(), m.level.sort_order if m.level_id else 0))
    for m in rows:
        key = field_key(m.name)
        g = by_key.get(m.name.lower())
        if g is None:
            disc = m.discipline or getattr(m.programme_category, "discipline", None)
            g = {"name": m.name, "options": [], "category": getattr(m.programme_category, "name", ""),
                 "discipline": getattr(disc, "name", ""),
                 "search": " ".join(filter(None, [normalise(m.name), normalise(m.aliases.replace("\n", " ")),
                                                  normalise(getattr(m.programme_category, "name", "")),
                                                  normalise(getattr(disc, "name", ""))]))}
            by_key[m.name.lower()] = g
            groups.append(g)
        code = level_code(m.level)
        added = (normalise(m.name), m.level_id) in have
        blocked = code == "hnd" and key not in nds and not added
        hint = ""
        if added:
            hint = "Already on your list"
        elif blocked:
            hint = (f"Add ND {m.name} first" if is_onboarding(inst)
                    else "Needs the ND of this programme at your institution")
        g["options"].append({"master": m, "code": code, "added": added, "blocked": blocked, "hint": hint,
                             "body": m.professional_body, "checked": m.pk in picked_pks and not added})
        g["search"] = g["search"] + " " + code
    return {"groups": groups, "count": len(rows)}


def body_covers(body, *, discipline=None, category=None, level=None):
    """A body with no discipline/category scope covers everything."""
    if body is None:
        return False
    if category is not None and discipline is None:
        discipline = category.discipline
    has_disc = body.scope_disciplines.exists()
    has_cat = body.scope_categories.exists()
    if has_disc or has_cat:
        ok = bool((discipline is not None and has_disc and body.scope_disciplines.filter(pk=discipline.pk).exists())
                  or (category is not None and has_cat and body.scope_categories.filter(pk=category.pk).exists()))
        if not ok:
            return False
    if level is not None and body.scope_levels.exists():
        return body.scope_levels.filter(pk=level.pk).exists()
    return True


def scope_label(body):
    bits = [d.name for d in body.scope_disciplines.all()] + [c.name for c in body.scope_categories.all()]
    return ", ".join(bits) if bits else "any programme"


def covering_bodies(*, discipline=None, category=None, level=None):
    from .models import ProfessionalBody

    return [b for b in ProfessionalBody.objects.filter(is_active=True)
            if (b.scope_disciplines.exists() or b.scope_categories.exists())
            and body_covers(b, discipline=discipline, category=category, level=level)]


def scope_error(body, typed_name, *, discipline=None, category=None, level=None):
    label = body.acronym or body.name
    others = [b.acronym or b.name for b in covering_bodies(discipline=discipline, category=category, level=level)]
    msg = f"{label} does not cover {typed_name}. Choose the professional body that regulates this programme."
    if others:
        msg += " Bodies that cover it: " + ", ".join(others[:4]) + "."
    return msg


def require_body():
    return bool(config.get("qa.programmes.require_body", True))


def allow_manual():
    return bool(config.get("qa.programmes.allow_manual_entry", True))


@transaction.atomic
def add_programme(inst, *, user=None, master=None, typed_name="", level=None, body=None, category=None,
                  discipline=None, application=None, streams=1):
    """Add a proposed programme (X44). Raises ValidationError with a message
    the add sheet shows."""
    from . import services
    from .models import Programme

    if master is not None:
        name, level, category = master.name, master.level, master.programme_category or category
        fields = {"master": master, "professional_body": master.professional_body, "body_source": "master"}
    else:
        name = " ".join(str(typed_name or "").split())
        if not allow_manual():
            raise ValidationError("Choose the programme from NBTE's list.")
        if not name or level is None:
            raise ValidationError("Type the programme name and choose its level.")
        if body is None and require_body():
            raise ValidationError("Choose the professional body that regulates this programme.")
        if body is not None and not body_covers(body, discipline=discipline, category=category, level=level):
            raise ValidationError(scope_error(body, name, discipline=discipline, category=category, level=level))
        fields = {"professional_body": body, "body_source": "manual" if body else "", "not_in_master": True}
    if Programme.unscoped.filter(institution=inst, name__iexact=name, level=level).exists():
        raise ValidationError("This programme is already on your list.")
    why = hnd_error(inst, name, level)  # ND/HND rule: never an HND without its ND
    if why:
        raise ValidationError(why)
    programme = services.add_proposed_programme(inst, name, level, category, application=application, user=user,
                                                streams=streams)
    pb = fields.get("professional_body")
    fields["awaiting_body_letter"] = bool(pb and pb.requires_letter_before_commencement)
    for k, v in fields.items():
        setattr(programme, k, v)
    programme.save(update_fields=list(fields) + ["updated_at"])
    if programme.not_in_master:
        _notify_master_department(programme)
    else:
        _notify_nbte_of_programme(programme)
    if pb is not None:
        from . import pb_portal

        transaction.on_commit(lambda: pb_portal.notify_body(programme, "programme_proposed"))
    return programme


@transaction.atomic
def remove_programme(programme, *, user=None, locked=lambda p: False):
    """Take a proposed programme off the onboarding list. Removing an ND also
    removes the proposed HND of the same programme (an HND never stays without
    its ND); returns the programmes removed. An HND NBTE already cleared
    (``locked``) blocks the ND's removal."""
    from . import services

    hnds = dependent_hnds(programme)
    stuck = [p for p in hnds if p.status != "proposed" or locked(p)]
    if stuck:
        raise ValidationError(f"HND {stuck[0].name} depends on this ND and can no longer be removed, so the ND "
                              "stays on your list.")
    removed = [programme] + hnds
    for p in hnds:
        services.remove_proposed_programme(p, user=user)
    services.remove_proposed_programme(programme, user=user)
    return removed


def _notify_nbte_of_programme(programme):
    """Tell NBTE (the master-list department and the handling department's
    director) that an institution added a programme from the master list; the
    professional body is told on commit by the caller."""
    from django.contrib.auth import get_user_model

    from . import services as inst_services

    code = str(config.get("qa.programmes.master_list_department", "ntird") or "ntird")
    users = []
    if is_onboarding(programme.institution):
        # Onboarding: the master-list department (NTIRD) follows the list. An
        # approved institution's new programme belongs to its handling
        # department only (NTIRD does not handle approved institutions).
        users = list(get_user_model().objects.filter(is_active=True, user_type="staff",
                                                     department__code__iexact=code))
    dept = getattr(programme.institution, "handling_department", None)
    if dept is not None and dept.director_id:
        director = get_user_model().objects.filter(pk=dept.director_id, is_active=True, user_type="staff").first()
        if director is not None and director not in users:
            users.append(director)
    if not users:
        return
    ctx = {"institution": programme.institution.name, "reference": getattr(programme.institution, "code", ""),
           "programme": str(programme)}
    inst_services.notify_role("programmes.added_master", ctx, users, url="/qa/programmes/")


def _notify_master_department(programme):
    from django.contrib.auth import get_user_model

    from notifications.services import notify_many

    code = str(config.get("qa.programmes.master_list_department", "ntird") or "ntird")
    users = get_user_model().objects.filter(is_active=True, user_type="staff", department__code__iexact=code)
    notify_many(list(users), title="Programme not in master list: map it",
                body=f"{programme.institution.name} added {programme} by hand. Map it to the master list.",
                url="/qa/professional-bodies/master/unmapped/")


def map_programme(programme, master, *, user=None):
    """NTIRD maps a hand-typed programme to a master row."""
    programme.master = master
    programme.not_in_master = False
    if master.professional_body_id and not programme.professional_body_id:
        programme.professional_body = master.professional_body
        programme.body_source = "nbte"
    programme.save(update_fields=["master", "not_in_master", "professional_body", "body_source", "updated_at"])
    return programme


def backfill(ProgrammeMaster, Programme, ProgrammeBodyMapping, ProgrammeLevel, *, slugify=None):
    """One master row per distinct (normalised name, level). Body from the
    mappings when exactly one body matches; returns the ambiguous keys."""
    from django.utils.text import slugify as _slugify

    slugify = slugify or _slugify
    from .models import master_search_key, programme_key

    mappings = list(ProgrammeBodyMapping.objects.filter(is_active=True))
    levels = list(ProgrammeLevel.objects.all())
    seen, ambiguous, codes = {}, [], set(ProgrammeMaster.objects.values_list("code", flat=True))
    for m in ProgrammeMaster.objects.all():
        seen[(programme_key(m.name), m.level_id)] = m

    def bodies_for(key, level_id):
        return {mp.body_id for mp in mappings if mp.programme_key == key
                and (mp.level_id is None or mp.level_id == level_id)}

    def make(name, level_id, category_id=None):
        key = (programme_key(name), level_id)
        if key in seen:
            return seen[key]
        level = next((lv for lv in levels if lv.pk == level_id), None)
        base = slugify(f"{level.code if level else ''} {name}")[:70] or "programme"
        code, n = base, 2
        while code in codes:
            code, n = f"{base}-{n}", n + 1
        codes.add(code)
        bodies = bodies_for(key[0], level_id)
        if len(bodies) > 1:
            ambiguous.append(f"{name} ({level.code if level else '-'})")
        m = ProgrammeMaster.objects.create(
            code=code, name=" ".join(name.split()), level_id=level_id, programme_category_id=category_id,
            professional_body_id=next(iter(bodies)) if len(bodies) == 1 else None,
            search_key=master_search_key(name), notes="Back-filled from existing programmes.")
        seen[key] = m
        return m

    for p in Programme._base_manager.exclude(level__isnull=True).order_by("pk"):
        if getattr(p, "is_institutional_admin", False):
            continue
        m = make(p.name, p.level_id, p.category_id)
        p.master_id = m.pk
        if m.professional_body_id and not p.professional_body_id:
            p.professional_body_id = m.professional_body_id
            p.body_source = "master"
        p.save(update_fields=["master", "professional_body", "body_source"])
    for mp in mappings:
        for level in ([lv for lv in levels if lv.pk == mp.level_id] if mp.level_id else
                      [lv for lv in levels if lv.code in ("nd", "hnd")]):
            make(mp.programme_name, level.pk)
    return ambiguous
