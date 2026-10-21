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
    """The Department for a QA code ('legal', 'academic-planning' …) or None."""
    from staff.models import Department

    if not code:
        return None
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
