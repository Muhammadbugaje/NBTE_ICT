"""Resolve the QA department codes used in seed data and flows.

`seed_startup` reuses an existing department instead of creating a duplicate
(Master Plan §15): on a system seeded by `seed_nbte`, Academic Planning is
``aspd``, Physical Planning ``ppd``, Health ``nhspd`` and Audit
``int-aud-dept``. Code and flows therefore never look a QA department up by
its QA code directly; they call :func:`qa_department`.
"""
from functools import lru_cache


@lru_cache(maxsize=1)
def _aliases():
    from .seed_data import DEPARTMENTS

    return {code: (name, tuple(aliases)) for code, name, aliases in DEPARTMENTS}


def candidate_codes(code):
    name, aliases = _aliases().get(code, ("", ()))
    return [code, *aliases]


def qa_department(code):
    """The Department for a QA code ('legal', 'academic-planning' …) or None.
    v2.2: memoised per request/thread for 30 s (called many times per page).
    v2.4: the per-request map below reads every department in ONE query, so
    distinct codes on one page no longer cost one query each."""
    if not code:
        return None
    import time

    hit = _MEMO.get(code)
    if hit is not None and time.monotonic() - hit[1] < 30:
        return hit[0]
    dept = _qa_department(code)
    _MEMO[code] = (dept, time.monotonic())
    return dept


_MEMO = {}

import threading as _threading  # noqa: E402

_DEPTS = _threading.local()


def dept_request_started(*_args, **_kwargs):
    """v2.4: the whole department table as maps, one query, for one request."""
    _DEPTS.map = None
    _DEPTS.in_request = True


def dept_request_finished(*_args, **_kwargs):
    _DEPTS.map = None
    _DEPTS.in_request = False


def _dept_map():
    """{"by_code": {code: Department}, "name": {name.lower(): Department}} for
    one web request on one thread (cleared on the same events as ``_MEMO``)."""
    if not getattr(_DEPTS, "in_request", False):
        return None
    m = getattr(_DEPTS, "map", None)
    if m is not None:
        return m
    from staff.models import Department

    by_code, names = {}, {}
    for dept in Department.objects.all().order_by("pk"):
        if dept.code and dept.code not in by_code:
            by_code[dept.code] = dept
        if dept.name and dept.name.lower() not in names:
            names[dept.name.lower()] = dept
    m = _DEPTS.map = (by_code, names)
    return m


def clear_cache(*_args, **_kwargs):
    _MEMO.clear()
    _DEPTS.map = None
    from org.services import clear_seat_cache

    clear_seat_cache()


def _qa_department(code):
    from staff.models import Department

    if not code:
        return None
    m = _dept_map()
    if m is not None:
        by_code, names = m
        for candidate in candidate_codes(code):
            dept = by_code.get(candidate)
            if dept is not None:
                return dept
        name, _aliases_ = _aliases().get(code, ("", ()))
        return names.get(name.lower()) if name else None
    for candidate in candidate_codes(code):
        dept = Department.objects.filter(code=candidate).first()
        if dept is not None:
            return dept
    name, _aliases_ = _aliases().get(code, ("", ()))
    if name:
        return Department.objects.filter(name__iexact=name).first()
    return None


def qa_code_of(department):
    """Reverse lookup: the QA code for a Department row ('' if none)."""
    if department is None:
        return ""
    for code, (name, aliases) in _aliases().items():
        if department.code == code or department.code in aliases or department.name.lower() == name.lower():
            return code
    return department.code or ""
