"""Programme master list (v2.1 R.4, X44, spec G10.d).

* ``search_master(q)`` - type-ahead for the portal "Add programme" sheet.
* ``body_covers(body, ...)`` - a body's scope check for manual entries.
* ``add_programme(inst, ...)`` - every programme add goes through here: from the
  master list (body filled in) or typed by hand (body required, scope checked,
  flagged ``not_in_master`` and queued for NTIRD).
* ``backfill(...)`` - one master row per distinct (name, level) of existing
  programmes and body mappings; used by the data migration.
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


def _notify_nbte_of_programme(programme):
    """Tell NBTE (the master-list department and the handling department's
    director) that an institution added a programme from the master list; the
    professional body is told on commit by the caller."""
    from django.contrib.auth import get_user_model

    from . import services as inst_services

    code = str(config.get("qa.programmes.master_list_department", "ntird") or "ntird")
    users = list(get_user_model().objects.filter(is_active=True, user_type="staff",
                                                 department__code__iexact=code))
    dept = getattr(programme.institution, "handling_department", None)
    if dept is not None and dept.director_id:
        director = get_user_model().objects.filter(pk=dept.director_id, is_active=True, user_type="staff").first()
        if director is not None:
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
