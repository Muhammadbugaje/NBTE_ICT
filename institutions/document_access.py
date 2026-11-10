"""v2.2 — who may see an institution's statutory documents.

The rule (owner's instruction): a document is seen only by the department
that reviews it — its director, the officer the director assigned, and staff
the director delegated department QA viewing to. Other departments do not
see it and cannot assign anyone to it. Exceptions:

* the ES, super admins / superusers and the Director of ICT see everything
  (they may also assign: ES / super admin);
* the ICT officer assigned to oversee the institution sees every document's
  *progress* read-only — never acting on it.
"""
from __future__ import annotations

from django.db.models import Q


def _org():
    import org

    return org


def is_full_viewer(user):
    if not getattr(user, "is_authenticated", False) or getattr(user, "user_type", "staff") != "staff":
        return False
    org = _org()
    from assignments import access

    # v2.2 (owner): only these four see every department's documents. The old
    # "review_documents_override" permission (held by every director role)
    # no longer opens other departments' documents.
    return bool(user.is_superuser or org.is_super_admin(user) or org.is_es(user)
                or org.is_director(user, access.ict_department_code()))


def is_watcher(user, inst):
    """The ICT officer overseeing this institution: progress only."""
    from assignments import access

    return bool(getattr(user, "is_authenticated", False) and access.is_ict_supervisor(user, inst))


def my_departments(user):
    """Departments whose documents ``user`` may see: the one they direct, and
    their own when the director delegated department QA viewing."""
    org = _org()
    ids = set()
    directed = org.directed_department(user)
    if directed is not None:
        ids.add(directed.pk)
    if user.department_id and user.has_perm("assignments.view_department_qa"):
        ids.add(user.department_id)
    return ids


def analysed_document_ids(user, inst=None):
    """Documents this person is working on: an open review as analyst, or an
    open work assignment whose subject is the document (assigned by the
    handling department)."""
    from django.contrib.contenttypes.models import ContentType

    from assignments.models import WorkAssignment

    from .models import DocumentReview, InstitutionDocument

    qs = DocumentReview.unscoped.filter(analyst=user, status__in=DocumentReview.OPEN + (DocumentReview.RETURNED,))
    if inst is not None:
        qs = qs.filter(document__institution=inst)
    ids = set(qs.values_list("document_id", flat=True))
    wa = WorkAssignment.objects.filter(assignee=user, status__in=WorkAssignment.OPEN,
                                       subject_ct=ContentType.objects.get_for_model(InstitutionDocument))
    if inst is not None:
        wa = wa.filter(institution=inst)
    ids |= {int(x) for x in wa.values_list("subject_id", flat=True) if str(x).isdigit()}
    return ids


def reviewing_department_id(doc):
    """The department that reviews this document (from the requirement, or
    its review record)."""
    from .models import DocumentReview

    dept = (DocumentReview.unscoped.filter(document=doc).order_by("-pk").values_list("department_id", flat=True)
            .first())
    if dept:
        return dept
    from . import services

    for req in services.requirements_for(doc.institution):
        if req.document_kind_id == doc.kind_id:
            return req.reviewing_department_id
    return None


def can_view(user, doc):
    """Row-level decision for one InstitutionDocument (staff only)."""
    if is_full_viewer(user) or is_watcher(user, doc.institution):
        return True
    if doc.pk in analysed_document_ids(user, doc.institution):
        return True
    dept = reviewing_department_id(doc)
    return bool(dept and dept in my_departments(user))


def filter_rows(user, inst, rows):
    """Checklist rows → (visible_rows, hidden_count). Rows of other
    departments are replaced by a locked stub (name and department only)."""
    if is_full_viewer(user) or is_watcher(user, inst):
        return rows, 0
    mine = my_departments(user)
    analysed = analysed_document_ids(user, inst)
    out, hidden = [], 0
    for row in rows:
        dept = row.get("department")
        doc = row.get("document")
        if (dept is not None and dept.pk in mine) or (doc is not None and doc.pk in analysed):
            out.append(row)
        else:
            hidden += 1
            out.append({**row, "document": None, "locked": True, "state": "locked"})
    return out, hidden


def filter_documents(user, inst, qs):
    """A queryset of InstitutionDocument limited to what ``user`` may open."""
    if is_full_viewer(user) or is_watcher(user, inst):
        return qs
    mine = my_departments(user)
    kinds = set()
    from . import services

    for req in services.requirements_for(inst):
        if req.reviewing_department_id in mine:
            kinds.add(req.document_kind_id)
    return qs.filter(Q(kind_id__in=kinds) | Q(pk__in=analysed_document_ids(user, inst)))
