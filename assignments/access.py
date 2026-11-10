"""May this person see this? — the one answer (v2.1 X1–X5; spec_v21 G1.d).

Phase C answered it per object (``can_see`` = native rule OR live grant).
Phase P adds the institution branch used by the QA scoping chokepoint
(``accounts.scoping.scope_to_institution``), every object-level check in
views, downloads and the API, and the "who can see X and why" page.

Two modes (setting ``qa.privacy.mode``, dual control):

``department`` (the old rule; an install switched back through dual control)
    every member of the handling department: superuser / ``accounts.access_all_departments`` /
    ``qa_core.view_all_institutions`` see every institution; other staff see
    the institutions their department handles.

``assignment`` (X1; the default for new installs)
    * full viewers everywhere (X2): super admin, ES (``qa.privacy.full_view_roles``),
      the Director of ICT (seat of ``qa.privacy.ict_department``), holders of
      ``assignments.view_all_journeys`` (ES-granted). ``qa_core.view_all_institutions``
      is not read in this mode (P.4 narrows it to the ES, super admin and
      Director of ICT when the mode is switched: ``assignments.view_all``);
    * the ICT supervisor of an institution (open ``oversight_ict`` assignment or
      a live ``full_journey`` grant) sees that institution's whole journey;
    * a department lead (director seat, or ``assignments.view_department_qa``
      delegated by the director, X4) sees institutions where the department
      has work, and only the department's rows of department-owned records (X3);
    * everyone else sees institutions they are assigned to (working
      ``WorkAssignment``) or granted (live ``AccessGrant``), and only the
      scopes the assignment kind / grant gives (``qa.access.kind_scopes``).

Public API (signatures stable):
    FULL, privacy_mode(), full_viewer_everywhere(user), visible_institution_ids(user),
    department_part_institution_ids(dept), supervised_institution_ids(user),
    is_ict_supervisor(user, inst), is_department_lead(user), scope_institution_ids(user, scope),
    can_see(user, obj, scope="summary"), scope_queryset(qs, user, field), who_can_see(inst),
    grant_for, access_via, has_native_access, record_event.
"""
from __future__ import annotations

import contextlib
import contextvars
import json

from django.core.cache import cache
from django.db.models import Q

from . import registry
from .grants import access_version, active_grants
from .models import AccessEvent, AccessGrant

FULL_SCOPES = (AccessGrant.SCOPE_FULL_JOURNEY,)


class _Full:
    """Sentinel: every institution."""

    def __repr__(self):
        return "FULL"

    def __bool__(self):
        return True

    def __contains__(self, item):
        return True


FULL = _Full()

MODE_DEPARTMENT, MODE_ASSIGNMENT = "department", "assignment"

# Scopes the chokepoint understands. Grants can only carry AccessGrant scopes;
# assignment kinds may also give "case", "visits", "complaints" (no grant row).
SCOPES = ("summary", "documents", "ssq", "payments", "case", "visits", "complaints", "full_journey")

DEFAULT_KIND_SCOPES = {
    "oversight_ict": ["full_journey"],
    "physical_verification": ["summary", "documents", "ssq", "visits"],
    "document_analysis": ["summary", "documents:subject"],
    "ssq_review": ["summary", "ssq:subject", "documents"],
    "visit_panel": ["summary", "ssq", "documents", "visits", "case"],
    "visit_lead": ["summary", "ssq", "documents", "visits", "case"],
    "complaint_task": ["summary", "complaints:subject"],
    "clearance": ["summary", "documents"],
    "programme_clearance": ["summary", "documents"],
    "case_desk": ["summary", "case", "ssq", "documents", "visits"],
    "case_supervision_audit": ["summary", "case", "visits"],
    "case_supervision_finance": ["summary", "case", "payments"],
    "payment_review": ["summary", "payments"],
    "honoraria_schedule": ["summary", "case"],
    "delegate_act_for": ["summary", "documents", "ssq"],
    # An open workflow task (SOP stage action, CQAC review…) on a record.
    "stage_task": ["summary", "case", "ssq", "documents", "visits"],
}
# What a department lead sees of the institutions in the department's part.
DEFAULT_LEAD_SCOPES = ["summary", "documents", "ssq", "case", "visits", "complaints"]

# The scope a row of each institution-bearing model needs (models with a
# DEPARTMENT_PATH are decided by department / assignment instead). Unknown
# models need "full_journey" (fail closed).
MODEL_SCOPES = {
    "institutions.institution": "summary",
    "institutions.institutioncontact": "summary",
    "institutions.institutionaddress": "summary",
    "institutions.programme": "summary",
    "institutions.institutionuser": "summary",
    "institutions.institutiondocument": "documents",
    "institutions.institutionrequest": "documents",
    "institutions.impersonationsession": "full_journey",
    "accreditation.application": "documents",
    "accreditation.qarequest": "ssq",
    "accreditation.qarequestprogramme": "ssq",
    "accreditation.submission": "ssq",
    "accreditation.evidence": "ssq",
    "accreditation.query": "ssq",
    "accreditation.response": "ssq",
    "accreditation.programmeclearance": "ssq",
    "inspection.ssqreviewassignment": "ssq",
    "inspection.qamapping": "visits",
    "inspection.inspection": "visits",
    "inspection.inspectionteammember": "visits",
    "inspection.postvisitpersonnel": "visits",
    "inspection.inspectionreport": "visits",
    "inspection.reportsection": "visits",
    "inspection.servicerecord": "full_journey",
    "payments.paymentobligation": "payments",
    "payments.paymentslip": "payments",
    "payments.paymentattempt": "payments",
    "payments.proofofpayment": "payments",
    "payments.paymentverification": "payments",
    "payments.paymentadvice": "payments",
    "payments.refund": "payments",
    "payments.paymentcredit": "payments",
    "assignments.workassignment": "summary",
}
# Department-owned models (DEPARTMENT_PATH) that an institution-level scope also
# opens: an SSQ reviewer assigned through the rotation board (work on the
# institution, grant scope "ssq") must reach the SSQs of that institution.
DEPARTMENT_MODEL_SCOPES = {
    "accreditation.ssq": "ssq",
}


def _cfg(key, default):
    try:
        import config
        value = config.get(key, None)
    except Exception:
        return default
    if value is None or value == "":
        return default
    if isinstance(default, (list, dict)) and isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value


_MODE_OVERRIDE = contextvars.ContextVar("asg_privacy_mode_override", default=None)


@contextlib.contextmanager
def mode_override(mode):
    """Answer as if ``qa.privacy.mode`` were ``mode`` (impact reports, dry runs)."""
    token = _MODE_OVERRIDE.set(mode)
    try:
        yield
    finally:
        _MODE_OVERRIDE.reset(token)


def privacy_mode():
    mode = _MODE_OVERRIDE.get() or str(_cfg("qa.privacy.mode", MODE_ASSIGNMENT)).strip().lower()
    return MODE_DEPARTMENT if mode == MODE_DEPARTMENT else MODE_ASSIGNMENT


def assignment_mode():
    return privacy_mode() == MODE_ASSIGNMENT


def kind_scopes(kind):
    table = _cfg("qa.access.kind_scopes", DEFAULT_KIND_SCOPES)
    if not isinstance(table, dict):
        table = DEFAULT_KIND_SCOPES
    return list(table.get(kind, DEFAULT_KIND_SCOPES.get(kind, ["summary"])))


def parse_scope(spec):
    """"documents:subject" -> ("documents", "subject")."""
    scope, _, limit = str(spec).partition(":")
    return scope.strip(), limit.strip()


def model_scope(model):
    if getattr(model, "DEPARTMENT_PATH", None) and model._meta.label_lower != "assignments.workassignment":
        return None
    return MODEL_SCOPES.get(model._meta.label_lower, "full_journey")


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
def _active(user):
    return bool(user is not None and getattr(user, "is_authenticated", False) and getattr(user, "is_active", False))


def _is_staff(user):
    return _active(user) and getattr(user, "user_type", "staff") == "staff"


def ict_department_code():
    return str(_cfg("qa.privacy.ict_department", "ict")).strip() or "ict"


def full_viewer_everywhere(user):
    """ES, super admin, Director of ICT (X2) — not the ICT supervisor (per institution)."""
    if not _is_staff(user):
        return False
    if user.is_superuser:
        return True
    import org
    roles = _cfg("qa.privacy.full_view_roles", ["executive-secretary"])
    if org.has_role(user, *[str(r) for r in (roles or [])]) or org.is_super_admin(user):
        return True
    if user.has_perm("assignments.view_all_journeys"):
        return True
    return org.is_director(user, ict_department_code())


def is_department_lead(user):
    """X4: the director seat of the user's department, or a person the director
    delegated ``assignments.view_department_qa`` to."""
    if not _is_staff(user) or not user.department_id:
        return False
    import org
    return org.is_director(user, user.department_id) or user.has_perm("assignments.view_department_qa")


def lead_scopes(user):
    scopes = list(_cfg("qa.access.lead_scopes", DEFAULT_LEAD_SCOPES))
    try:
        from payments.services import finance_department_code
        import org
        if org.department(finance_department_code()) and org.department(finance_department_code()).pk == user.department_id:
            scopes.append("payments")
    except Exception:
        pass
    return scopes


def ssq_accessible_ids(user):
    """SSQs visible only to their department director, authorised oversight,
    the assigned reviewer, or NBTE-wide executive administrators."""
    if not _is_staff(user):
        return set()
    import org

    if user.is_superuser or org.is_super_admin(user) or org.is_es(user):
        return FULL

    from accreditation.models import SSQ
    from django.contrib.contenttypes.models import ContentType
    from .models import WorkAssignment

    ids = set(SSQ.unscoped.filter(
        qa_request_programme__qa_request__institution__handling_department__director_id=user.pk
    ).values_list("pk", flat=True))
    supervised = supervised_institution_ids(user)
    if supervised:
        ids.update(SSQ.unscoped.filter(
            qa_request_programme__qa_request__institution_id__in=supervised
        ).values_list("pk", flat=True))

    ssq_type = ContentType.objects.get_for_model(SSQ, for_concrete_model=False)
    assigned = WorkAssignment.objects.filter(
        assignee=user, kind=WorkAssignment.SSQ_REVIEW, status__in=WorkAssignment.WORKING,
        subject_ct=ssq_type,
    ).values_list("subject_id", flat=True)
    for pk in assigned:
        try:
            ids.add(SSQ._meta.pk.to_python(pk))
        except (TypeError, ValueError):
            continue
    return ids


def ssq_queryset_scope(queryset, user):
    """Apply the strict SSQ allow-list before general institution scoping."""
    model = queryset.model
    label = model._meta.label_lower
    path = getattr(model, "INSTITUTION_PATH", "")
    is_ssq = label == "accreditation.ssq"
    is_ssq_child = path.startswith("ssq__")
    is_ssq_query = label == "accreditation.query"
    if not (is_ssq or is_ssq_child or is_ssq_query) or getattr(user, "user_type", "") != "staff":
        return None

    ids = ssq_accessible_ids(user)
    if ids is FULL:
        return queryset
    if is_ssq:
        return queryset.filter(pk__in=ids)
    if is_ssq_child:
        return queryset.filter(ssq_id__in=ids)
    from django.apps import apps
    from django.contrib.contenttypes.models import ContentType

    protected_types, allowed_subjects = [], Q(pk__in=[])
    for related_model in apps.get_models():
        related_path = getattr(related_model, "INSTITUTION_PATH", "")
        if related_model._meta.label_lower != "accreditation.ssq" and not related_path.startswith("ssq__"):
            continue
        content_type = ContentType.objects.get_for_model(related_model, for_concrete_model=False)
        protected_types.append(content_type.pk)
        if related_model._meta.label_lower == "accreditation.ssq":
            subject_ids = [str(pk) for pk in ids]
        else:
            subject_ids = [str(pk) for pk in related_model.unscoped.filter(ssq_id__in=ids)
                           .values_list("pk", flat=True)]
        if subject_ids:
            allowed_subjects |= Q(content_type=content_type, object_id__in=subject_ids)
    return queryset.filter(~Q(content_type_id__in=protected_types) | allowed_subjects)


def can_access_any_ssq(user):
    ids = ssq_accessible_ids(user)
    return ids is FULL or bool(ids)


def ssq_for_object(obj):
    """Return the SSQ governing an SSQ row or one of its generic subjects."""
    from accreditation.models import SSQ

    model = type(obj)
    if model is SSQ:
        return obj
    if getattr(model, "INSTITUTION_PATH", "").startswith("ssq__"):
        return getattr(obj, "ssq", None)
    if model._meta.label_lower == "accreditation.query" and getattr(obj, "content_type_id", None):
        from django.contrib.contenttypes.models import ContentType

        subject = obj.subject
        if isinstance(subject, SSQ):
            return subject
        if (subject is not None
                and getattr(type(subject), "INSTITUTION_PATH", "").startswith("ssq__")):
            return getattr(subject, "ssq", None)
    return None


# ---------------------------------------------------------------------------
# Caching: per request (on the user object) + versioned shared cache
# ---------------------------------------------------------------------------
GLOBAL_VERSION_KEY = "assignments:access_global_version"
CACHE_SECONDS = 300


def global_version():
    return cache.get(GLOBAL_VERSION_KEY, 0)


def bump_global_version():
    """Any change that can move a department's part (assignments, clearances,
    complaints, cases, the privacy mode) invalidates every cached answer."""
    try:
        cache.incr(GLOBAL_VERSION_KEY)
    except ValueError:
        cache.set(GLOBAL_VERSION_KEY, 1, None)


def _cache_key(user, what):
    return (f"assignments:vis:{what}:{user.pk}:{access_version(user)}:{global_version()}:"
            f"{user.department_id}:{privacy_mode()}")


def _cached(user, what, compute):
    key = _cache_key(user, what)
    memo = getattr(user, "_asg_access_memo", None)
    if memo is None:
        memo = {}
        try:
            user._asg_access_memo = memo
        except Exception:
            pass
    if key in memo:
        return memo[key]
    value = cache.get(key)
    if value is None:
        value = compute()
        cache.set(key, "FULL" if value is FULL else sorted(value), CACHE_SECONDS)
    elif value == "FULL":
        value = FULL
    else:
        value = set(value)
    memo[key] = value
    return value


def clear_request_cache(user):
    if hasattr(user, "_asg_access_memo"):
        user._asg_access_memo = {}


# ---------------------------------------------------------------------------
# Institution sets
# ---------------------------------------------------------------------------
def _engaged_institution_ids(user):
    from accounts.scoping import _engaged_institution_ids as engaged
    return set(engaged(user))


def supervised_institution_ids(user):
    """Institutions whose whole journey this person supervises (X2): an open
    ``oversight_ict`` assignment or a live ``full_journey`` grant."""
    if not _is_staff(user):
        return set()

    def compute():
        from .models import WorkAssignment
        ids = set(WorkAssignment.objects.filter(
            assignee=user, kind__in=WorkAssignment.FULL_JOURNEY_KINDS, status__in=WorkAssignment.WORKING)
            .values_list("institution_id", flat=True))
        ids |= set(AccessGrant.objects.live(user).filter(scope=AccessGrant.SCOPE_FULL_JOURNEY)
                   .exclude(institution=None).values_list("institution_id", flat=True))
        return ids
    return _cached(user, "supervised", compute)


def is_ict_supervisor(user, inst):
    pk = getattr(inst, "pk", inst)
    return pk is not None and pk in supervised_institution_ids(user)


def department_part_institution_ids(dept):
    """Institutions department ``dept`` is involved with (X3/X4): it handles the
    institution's category or one of its open QA cases, or it holds an open
    review step there (document review, clearance, programme review, SSQ /
    visit / other work, complaint). Closed steps do not keep it visible."""
    dept_id = getattr(dept, "pk", dept)
    if not dept_id:
        return set()
    from accreditation.models import DepartmentalClearance, OnboardingProgrammeReview, QACase
    from complaints.models import Complaint
    from institutions.models import DocumentReview, Institution

    from .models import WorkAssignment
    ids = set(WorkAssignment.objects.filter(department_id=dept_id, status__in=WorkAssignment.OPEN)
              .values_list("institution_id", flat=True))
    ids |= set(DocumentReview.unscoped.filter(department_id=dept_id, status__in=DocumentReview.OPEN
                                              + (DocumentReview.RETURNED,))
               .values_list("document__institution_id", flat=True))
    ids |= set(DepartmentalClearance.unscoped.filter(department_id=dept_id)
               .exclude(status__in=(DepartmentalClearance.CLEARED, DepartmentalClearance.CLOSED))
               .values_list("application__institution_id", flat=True))
    ids |= set(OnboardingProgrammeReview.unscoped.filter(department_id=dept_id)
               .exclude(status__in=("cleared", "superseded"))
               .values_list("application__institution_id", flat=True))
    ids |= set(Complaint.unscoped.filter(current_department_id=dept_id).exclude(institution=None)
               .exclude(status__in=Complaint.OPEN_EXCLUDED).values_list("institution_id", flat=True))
    ids |= set(QACase.unscoped.filter(owning_department_id=dept_id, completed_at__isnull=True)
               .values_list("institution_id", flat=True))
    # The handling department owns its institutions (proposed onboarding,
    # approved monitoring) — the "handling" part of the journey.
    ids |= set(Institution.unscoped.filter(handling_department_id=dept_id).values_list("pk", flat=True))
    try:
        from payments.models import PaymentObligation
        from payments.services import finance_department_code
        import org
        fin = org.department(finance_department_code())
        if fin is not None and fin.pk == dept_id:
            ids |= set(PaymentObligation.unscoped.values_list("institution_id", flat=True))
    except Exception:
        pass
    ids.discard(None)
    return ids


def _finance_department():
    try:
        from payments.services import finance_department_code
        import org
        return org.department(finance_department_code())
    except Exception:
        return None


def is_payment_desk(user):
    """Finance staff who verify payments (first pass / second check): the desk
    is their standing assignment, for bills only (summary + payments scope)."""
    if not _is_staff(user) or not user.department_id:
        return False
    fin = _finance_department()
    if fin is None or fin.pk != user.department_id:
        return False
    return bool(user.has_perm("payments.verify_payment") or user.has_perm("payments.second_verify_payment"))


def billed_institution_ids():
    from payments.models import PaymentObligation
    ids = set(PaymentObligation.unscoped.values_list("institution_id", flat=True))
    ids.discard(None)
    return ids


def task_subjects(user):
    """{(model, pk)} of records with an open QA workflow task for ``user``
    (qa_core.QATaskLink): stage actors see what their step needs."""
    if not _is_staff(user):
        return set()
    memo = getattr(user, "_asg_task_memo", None)
    key = (global_version(), access_version(user))
    if memo is not None and memo[0] == key:
        return memo[1]
    out = set()
    try:
        from qa_core.models import QATaskLink
        for ct, oid in (QATaskLink.objects.filter(closed_at__isnull=True, task__assignees=user)
                        .values_list("content_type", "object_id").distinct()):
            from django.contrib.contenttypes.models import ContentType
            model = ContentType.objects.get_for_id(ct).model_class()
            if model is not None:
                out.add((model, oid))
    except Exception:  # pragma: no cover
        out = set()
    try:
        user._asg_task_memo = (key, out)
    except Exception:
        pass
    return out


def task_institution_ids(user):
    ids = set()
    for model, oid in task_subjects(user):
        manager = getattr(model, "unscoped", None) or model._default_manager
        obj = manager.filter(pk=oid).first()
        inst = institution_of(obj) if obj is not None and hasattr(model, "INSTITUTION_PATH") else None
        if inst is not None:
            ids.add(inst.pk)
    return ids


def _working_assignments(user):
    from .models import WorkAssignment
    return WorkAssignment.objects.filter(assignee=user, status__in=WorkAssignment.WORKING)


def assigned_institution_ids(user):
    """Institution summaries opened by the user's active work or live grant."""
    ids = set(_working_assignments(user).values_list("institution_id", flat=True))
    ids |= set(AccessGrant.objects.live(user).exclude(institution=None).values_list("institution_id", flat=True))
    from institutions.models import DocumentReview

    ids |= set(DocumentReview.unscoped.filter(analyst=user, assignment__isnull=True,
                                               status__in=DocumentReview.OPEN)
               .values_list("document__institution_id", flat=True))
    ids.discard(None)
    return ids


def visible_institution_ids(user):
    """Institution ids ``user`` may see at all (summary level), or ``FULL``.
    Request-cached and versioned (assignment / grant changes bump it)."""
    if not _active(user):
        return set()
    user_type = getattr(user, "user_type", "staff")
    if user_type == "institution":
        return {user.institution_id} if getattr(user, "institution_id", None) else set()
    if user_type == "resource_person":
        return _engaged_institution_ids(user)
    if user_type != "staff":
        return set()

    def compute():
        from institutions.models import Institution
        if privacy_mode() == MODE_DEPARTMENT:
            if (user.is_superuser or user.has_perm("accounts.access_all_departments")
                    or user.has_perm("qa_core.view_all_institutions")):
                return FULL
            ids = set(Institution.unscoped.filter(handling_department_id=user.department_id)
                      .values_list("pk", flat=True)) if user.department_id else set()
            ids |= assigned_institution_ids(user)
            return ids
        if full_viewer_everywhere(user) or list_viewer(user):
            return FULL  # list viewers: summary only (scope_institution_ids / can_see narrow the rest)
        ids = set(_working_assignments(user).values_list("institution_id", flat=True))
        ids |= set(AccessGrant.objects.live(user).exclude(institution=None).values_list("institution_id", flat=True))
        ids |= task_institution_ids(user)
        if is_department_lead(user):
            ids |= department_part_institution_ids(user.department_id)
        if is_payment_desk(user):
            ids |= billed_institution_ids()
        ids.discard(None)
        return ids
    return _cached(user, "visible", compute)


def list_viewer(user):
    """Part 1b: ``institutions.view_all_institutions`` — a read-only list of every
    institution (summary level), nothing more."""
    return bool(_is_staff(user) and not full_viewer_everywhere(user)
                and user.has_perm("institutions.view_all_institutions"))


def scope_institution_ids(user, scope):
    """Institutions where ``user`` holds ``scope`` at institution level (not
    limited to one record). ``FULL`` for full viewers."""
    vis = visible_institution_ids(user)
    if vis is FULL and (scope == "summary" or privacy_mode() == MODE_DEPARTMENT or not list_viewer(user)):
        return FULL
    if getattr(user, "user_type", "staff") != "staff" or privacy_mode() == MODE_DEPARTMENT:
        return set(vis)
    if scope == "summary":
        return set(vis)
    # (a list viewer reaches here with vis FULL: their other scopes come from work, as for anyone)

    def compute():
        ids = set(supervised_institution_ids(user))
        for kind, inst in _working_assignments(user).values_list("kind", "institution_id"):
            for spec in kind_scopes(kind):
                s, limit = parse_scope(spec)
                if not limit and s in (scope, "full_journey"):
                    ids.add(inst)
        if any(parse_scope(s)[0] == scope and not parse_scope(s)[1] for s in kind_scopes("stage_task")):
            ids |= task_institution_ids(user)
        ids |= set(AccessGrant.objects.live(user).filter(scope__in=(scope, "full_journey"))
                   .filter(object_ct__app_label="institutions", object_ct__model="institution")
                   .exclude(institution=None).values_list("institution_id", flat=True))
        if is_department_lead(user) and scope in lead_scopes(user):
            ids |= department_part_institution_ids(user.department_id)
        if scope == "payments" and is_payment_desk(user):
            ids |= billed_institution_ids()
        ids.discard(None)
        return ids
    return _cached(user, f"scope:{scope}", compute)


# ---------------------------------------------------------------------------
# Row level: assigned / granted records
# ---------------------------------------------------------------------------
def _assigned_subject_ids(user, model):
    """Primary keys (str) of ``model`` rows the user works on or is granted."""
    from django.contrib.contenttypes.models import ContentType
    ct = ContentType.objects.get_for_model(model, for_concrete_model=False)
    ids = set(_working_assignments(user).filter(subject_ct=ct).values_list("subject_id", flat=True))
    ids |= set(AccessGrant.objects.live(user).filter(object_ct=ct).values_list("object_id", flat=True))
    ids |= {str(oid) for m, oid in task_subjects(user) if m._meta.label_lower == model._meta.label_lower}
    if model._meta.label_lower in ("institutions.documentreview", "institutions.institutiondocument"):
        from institutions.models import DocumentReview

        reviews = DocumentReview.unscoped.filter(analyst=user, assignment__isnull=True,
                                                  status__in=DocumentReview.OPEN)
        field = "pk" if model._meta.label_lower == "institutions.documentreview" else "document_id"
        ids |= set(reviews.values_list(field, flat=True))
    return ids


def _pk_values(model, ids):
    field = model._meta.pk
    out = []
    for value in ids:
        try:
            out.append(field.to_python(value))
        except Exception:
            continue
    return out


def assigned_rows_q(user, model):
    """Q matching rows of ``model`` the user is assigned / granted, directly or
    as children of an assigned record (one hop: Evidence of an assigned SSQ)."""
    q = Q(pk__in=_pk_values(model, _assigned_subject_ids(user, model)))
    if model._meta.label_lower == "institutions.documentreview":
        return q
    for field in model._meta.get_fields():
        if not getattr(field, "many_to_one", False) or not getattr(field, "related_model", None):
            continue
        rel = field.related_model
        if rel is model or not hasattr(rel, "INSTITUTION_PATH") or rel._meta.label_lower == "institutions.institution":
            continue
        ids = _assigned_subject_ids(user, rel)
        if ids:
            q |= Q(**{f"{field.name}_id__in": _pk_values(rel, ids)})
    return q


def _path_value(obj, path):
    for part in path.split("__"):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def department_of(obj):
    path = getattr(type(obj), "DEPARTMENT_PATH", None)
    if not path:
        return None
    value = _path_value(obj, path)
    return getattr(value, "pk", value)


def institution_of(obj):
    from institutions.models import Institution
    if isinstance(obj, Institution):
        return obj
    from qa_core.files import institution_of as _inst_of
    return _inst_of(obj)


def scope_queryset(queryset, user, field):
    """The assignment-mode staff branch of the chokepoint (spec G1.d)."""
    model = queryset.model
    ids = visible_institution_ids(user)
    if ids is FULL and not list_viewer(user):
        return queryset
    if ids is FULL:  # list viewer: every institution's summary rows, the rest by their work
        if model_scope(model) == "summary" and not getattr(model, "DEPARTMENT_PATH", None):
            return queryset
        from institutions.models import Institution
        ids = set(Institution.unscoped.values_list("pk", flat=True))
    lookup = f"{field}_id__in" if field else "pk__in"
    sup = supervised_institution_ids(user)
    qs = queryset.filter(**{lookup: list(ids)})
    sup_q = Q(**{lookup: list(sup)}) if sup else Q(pk__in=[])
    label = model._meta.label_lower
    if label == "qa_core.timelineevent":
        q = Q(audience="public")
        if user.department_id:
            dept_q = Q(audience="department", department_id=user.department_id)
            if not is_department_lead(user):
                dept_q &= Q(actor=user)
            q |= dept_q
        return qs.filter(sup_q | q)
    dept_path = getattr(model, "DEPARTMENT_PATH", None)
    if dept_path and label != "assignments.workassignment":
        own = Q(pk__in=[])
        if is_department_lead(user):
            own = Q(**{f"{dept_path}_id": user.department_id})
        scoped = Q(pk__in=[])
        if label in DEPARTMENT_MODEL_SCOPES:
            allowed = scope_institution_ids(user, DEPARTMENT_MODEL_SCOPES[label])
            if allowed is FULL:
                return qs
            scoped = Q(**{lookup: list(allowed)})
        return qs.filter(sup_q | own | scoped | assigned_rows_q(user, model))
    if label == "assignments.workassignment":
        own = Q(assignee=user)
        if is_department_lead(user):
            own |= Q(department_id=user.department_id)
        return qs.filter(sup_q | own)
    scope = model_scope(model)
    if scope == "summary":
        return qs
    allowed = scope_institution_ids(user, scope)
    if allowed is FULL:
        return qs
    return qs.filter(sup_q | Q(**{lookup: list(allowed)}) | assigned_rows_q(user, model))


# ---------------------------------------------------------------------------
# Object level
# ---------------------------------------------------------------------------
def grant_for(user, obj, scope):
    """The live grant through which ``user`` holds ``scope`` on ``obj`` (or None)."""
    if obj is None or not _active(user):
        return None
    return active_grants(user, obj=obj, scope=(scope,) + FULL_SCOPES).order_by("-expires_at").first()


def has_native_access(user, obj, scope="summary"):
    fn = registry.native_resolver(obj)
    if fn is None:
        return False
    try:
        return bool(fn(user, obj, scope))
    except Exception:
        return False


def _is_assigned_row(user, obj):
    model = type(obj)
    if str(obj.pk) in _assigned_subject_ids(user, model):
        return True
    for field in model._meta.get_fields():
        if not getattr(field, "many_to_one", False) or not getattr(field, "related_model", None):
            continue
        rel = field.related_model
        if rel is model or not hasattr(rel, "INSTITUTION_PATH") or rel._meta.label_lower == "institutions.institution":
            continue
        value = getattr(obj, f"{field.name}_id", None)
        if value is not None and str(value) in _assigned_subject_ids(user, rel):
            return True
    return False


def can_see(user, obj, scope="summary"):
    """Object-level decision used by views, downloads, API and templates.

    Institution-bearing records follow the chokepoint rules; other records
    (assets…) are their native rule OR a live grant (Phase C)."""
    if obj is None or not _active(user):
        return False
    if getattr(user, "user_type", "") == "staff":
        model = type(obj)
        ssq = ssq_for_object(obj)
        if ssq is not None:
            ids = ssq_accessible_ids(user)
            return ids is FULL or ssq.pk in ids
        if model._meta.label_lower == "accreditation.query" and getattr(obj, "content_type_id", None):
            from django.apps import apps
            from django.contrib.contenttypes.models import ContentType

            protected_types = [
                ContentType.objects.get_for_model(m, for_concrete_model=False).pk
                for m in apps.get_models()
                if m._meta.label_lower == "accreditation.ssq"
                or getattr(m, "INSTITUTION_PATH", "").startswith("ssq__")
            ]
            if obj.content_type_id in protected_types:
                return False
    if not hasattr(type(obj), "INSTITUTION_PATH"):
        if has_native_access(user, obj, scope):
            return True
        return grant_for(user, obj, scope) is not None
    inst = institution_of(obj)
    if inst is None:
        return bool(_is_staff(user) and (full_viewer_everywhere(user) or has_native_access(user, obj, scope)))
    ids = visible_institution_ids(user)
    if ids is FULL and not list_viewer(user):
        return True
    if ids is not FULL and inst.pk not in ids:
        return False
    if getattr(user, "user_type", "staff") != "staff":
        return True
    if privacy_mode() == MODE_DEPARTMENT:
        from institutions.models import Institution

        if inst.handling_department_id == user.department_id:
            return True
        if isinstance(obj, Institution):
            return True
        return _is_assigned_row(user, obj) or grant_for(user, obj, scope) is not None
    if is_ict_supervisor(user, inst):
        return True
    if scope == "full_journey":
        return False
    model = type(obj)
    label = model._meta.label_lower
    if label == "qa_core.timelineevent":
        if obj.audience == "public":
            return True
        return obj.audience == "department" and obj.department_id == user.department_id and (
            is_department_lead(user) or obj.actor_id == user.pk)
    dept_path = getattr(model, "DEPARTMENT_PATH", None)
    if dept_path and label != "assignments.workassignment":
        if is_department_lead(user) and department_of(obj) == user.department_id:
            return True
        if label in DEPARTMENT_MODEL_SCOPES:
            allowed = scope_institution_ids(user, DEPARTMENT_MODEL_SCOPES[label])
            if allowed is FULL or inst.pk in allowed:
                return True
        return _is_assigned_row(user, obj)
    needed = scope if scope != "summary" else (model_scope(model) or "summary")
    if needed == "summary":
        return True
    allowed = scope_institution_ids(user, needed)
    if allowed is FULL or inst.pk in allowed:
        return True
    return _is_assigned_row(user, obj) or grant_for(user, obj, needed) is not None


def journey_variant(user, inst):
    """Which staff institution page to show (P.6 / G1.g #1):
    ``full`` (whole journey), ``department`` (your department's part),
    ``mine`` (my work on this institution) or ``None`` (not visible)."""
    if not can_see(user, inst, "summary"):
        return None
    if privacy_mode() == MODE_DEPARTMENT:
        return "full" if (visible_institution_ids(user) is FULL
                          or inst.handling_department_id == getattr(user, "department_id", None)) else "mine"
    if full_viewer_everywhere(user) or is_ict_supervisor(user, inst):
        return "full"
    if is_department_lead(user) and inst.pk in department_part_institution_ids(user.department_id):
        return "department"
    return "mine"


def access_via(user, obj, scope="summary"):
    """("native" | "grant" | None, grant) — for banners ("You can see this
    because you are assigned ticket TKT-…")."""
    if obj is None or not getattr(user, "is_authenticated", False):
        return None, None
    if has_native_access(user, obj, scope):
        return "native", None
    g = grant_for(user, obj, scope)
    return ("grant", g) if g is not None else (None, None)


# ---------------------------------------------------------------------------
# Who can see institution X and why (G1.g #5, P.5)
# ---------------------------------------------------------------------------
def who_can_see(inst, *, mode=None):
    """[{"user", "reason", "level", "detail"}] — every active account that
    can see ``inst`` under ``mode`` (default: the current mode), with why."""
    from accounts.models import User

    from .models import WorkAssignment
    mode = mode or privacy_mode()
    rows, seen = [], {}

    def add(user, reason, level, detail=""):
        entry = seen.get(user.pk)
        if entry is None:
            entry = {"user": user, "reasons": [], "level": level}
            seen[user.pk] = entry
            rows.append(entry)
        entry["reasons"].append({"reason": reason, "detail": detail})
        order = ["full", "department", "own", "summary", "institution"]
        if order.index(level) < order.index(entry["level"]):
            entry["level"] = level

    staff = list(User.objects.filter(is_active=True, user_type="staff").select_related("role", "department"))
    for u in staff:
        if mode == MODE_DEPARTMENT:
            if u.is_superuser or u.has_perm("accounts.access_all_departments") \
                    or u.has_perm("qa_core.view_all_institutions"):
                add(u, "Sees every institution (all-departments or view-all permission)", "full")
            elif u.department_id and u.department_id == inst.handling_department_id:
                add(u, "Member of the handling department", "department")
            continue
        if full_viewer_everywhere(u):
            add(u, "Full viewer (super admin, ES or Director of ICT)", "full")
    if mode == MODE_ASSIGNMENT:
        for wa in (WorkAssignment.objects.filter(institution=inst, status__in=WorkAssignment.WORKING)
                   .select_related("assignee", "department")):
            if wa.assignee.is_active:
                level = "full" if wa.kind in WorkAssignment.FULL_JOURNEY_KINDS else "own"
                add(wa.assignee, f"Assigned: {wa.get_kind_display()}", level, wa.subject_label or "")
        for g in (AccessGrant.objects.live().filter(institution=inst).select_related("grantee")):
            if g.grantee.is_active:
                add(g.grantee, f"Temporary access: {g.get_scope_display()}",
                    "full" if g.scope == AccessGrant.SCOPE_FULL_JOURNEY else "own", g.reason)
        for u in staff:
            if u.pk not in seen or seen[u.pk]["level"] != "full":
                if inst.pk in task_institution_ids(u):
                    add(u, "Has an open workflow step on this institution", "own")
        from staff.models import Department
        for dept in Department.objects.filter(pk__in=_departments_with_part(inst)):
            for u in staff:
                if u.department_id == dept.pk and is_department_lead(u) and not full_viewer_everywhere(u):
                    add(u, f"Leads a department with work here ({dept.name})", "department")
    for u in User.objects.filter(is_active=True, user_type="institution", institution_id=inst.pk):
        add(u, "Institution portal account", "institution")
    for u in User.objects.filter(is_active=True, user_type="resource_person"):
        if inst.pk in _engaged_institution_ids(u):
            add(u, "Resource person with an active engagement", "own")
    return rows


def _departments_with_part(inst):
    from staff.models import Department
    return [d for d in Department.objects.values_list("pk", flat=True)
            if inst.pk in department_part_institution_ids(d)]


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
def _client_ip(request):
    if request is None:
        return None
    try:
        from core.ratelimit import client_ip  # type: ignore
        return client_ip(request)
    except Exception:
        return request.META.get("REMOTE_ADDR") or None


def record_event(user, obj, action, *, via_grant=None, request=None, institution=None):
    """Append one AccessEvent. Never raises into the caller."""
    from django.contrib.contenttypes.models import ContentType
    try:
        if institution is None and hasattr(type(obj), "INSTITUTION_PATH"):
            institution = institution_of(obj)
        return AccessEvent.objects.create(
            user=user if getattr(user, "pk", None) else None,
            institution=institution,
            object_ct=ContentType.objects.get_for_model(obj, for_concrete_model=False),
            object_id=str(obj.pk), action=action, via_grant=via_grant, ip=_client_ip(request))
    except Exception:  # pragma: no cover
        import logging
        logging.getLogger(__name__).exception("Could not record access event")
        return None
