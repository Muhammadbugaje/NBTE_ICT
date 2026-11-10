"""States and LGAs read from the database (v2.1 X49).

* ``lga_names(state)``  -> ["Birnin Gwari", ...] for a StateOfOrigin (active
  LGAs, cached per state; the cache version is bumped whenever an LGA is
  saved or deleted, so an edit in Settings shows on the register page at once).
  Until `seed_nbte --only geo` has filled the table, it falls back to the
  bundled JSON (staff/data/lgas.json) so nothing breaks on an unseeded database.
* ``validate_lga(state, name)`` -> the LGA row, or ValidationError.
* ``lga_for(state, text)`` -> the matching LGA row (case/space-insensitive) or None.
"""
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from . import nbte_reference as ref

VERSION_KEY = "geo:lga:version"
TTL = 60 * 60


def _version():
    v = cache.get(VERSION_KEY)
    if v is None:
        v = 1
        cache.set(VERSION_KEY, v, None)
    return v


def bump_lga_cache():
    try:
        cache.incr(VERSION_KEY)
    except ValueError:
        cache.set(VERSION_KEY, 2, None)


def _table_ready():
    from .models import LGA

    return LGA.objects.exists()


def lga_rows(state):
    from .models import LGA

    if state is None or not getattr(state, "pk", None):
        return []
    return list(LGA.objects.filter(state=state, is_active=True).order_by("name"))


def lga_names(state):
    if state is None:
        return []
    if not _table_ready():
        from institutions.services import lgas_from_file

        return lgas_from_file(state)
    key = f"geo:lga:{_version()}:{state.pk}"
    names = cache.get(key)
    if names is None:
        names = [l.name for l in lga_rows(state)]
        cache.set(key, names, TTL)
    return names


def lga_groups():
    """[(state name, [LGA names])] for the no-JS fallback."""
    from .models import LGA, StateOfOrigin

    if not _table_ready():
        return None
    out = []
    by_state = {}
    for name, state_id in LGA.objects.filter(is_active=True).order_by("name").values_list("name", "state_id"):
        by_state.setdefault(state_id, []).append(name)
    for st in StateOfOrigin.objects.filter(pk__in=by_state).order_by("name"):
        out.append((st.name, by_state[st.pk]))
    return out


def lga_for(state, text):
    from .models import LGA

    if state is None or not text:
        return None
    want = ref.compact_key(text)
    for lga in LGA.objects.filter(state=state):
        if ref.compact_key(lga.name) == want:
            return lga
    return None


def validate_lga(state, text):
    """The LGA must exist, be active and belong to ``state``."""
    from .models import LGA

    if not text:
        return None
    lga = lga_for(state, text)
    if lga is not None and lga.is_active:
        return lga
    other = next((l for l in LGA.objects.select_related("state").filter(is_active=True)
                  if ref.compact_key(l.name) == ref.compact_key(text)), None)
    if other is not None:
        raise ValidationError(f"'{text}' is an LGA of {other.state.name}, not {state}.")
    raise ValidationError(f"'{text}' is not an LGA of {state}. Choose one from the list.")


@receiver(post_save, sender="staff.LGA", dispatch_uid="geo_lga_saved")
@receiver(post_delete, sender="staff.LGA", dispatch_uid="geo_lga_deleted")
def _lga_changed(sender, **kwargs):
    bump_lga_cache()


# ---------------------------------------------------------------------------
# lga_ref back-fill report (X49 data step)
# ---------------------------------------------------------------------------
def backfill_lga_refs(*, apply=False):
    """Match Institution.lga / InstitutionAddress.lga text to LGA rows per state.
    Returns {"matched": n, "unmatched": [(model, pk, state, text)], "already": n}.
    With apply=True the matched rows get lga_ref set (text kept)."""
    from institutions.models import Institution, InstitutionAddress

    report = {"matched": 0, "already": 0, "unmatched": []}
    for model in (Institution, InstitutionAddress):
        manager = getattr(model, "unscoped", None) or model._base_manager
        for obj in manager.exclude(lga="").select_related("state"):
            if obj.lga_ref_id:
                report["already"] += 1
                continue
            lga = lga_for(obj.state, obj.lga) if obj.state_id else None
            if lga is None:
                report["unmatched"].append((model.__name__, obj.pk, obj.state.name if obj.state_id else "", obj.lga))
                continue
            report["matched"] += 1
            if apply:
                manager.filter(pk=obj.pk).update(lga_ref=lga)
    return report
