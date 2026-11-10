"""Phase P (P.1, P.2, P.5, P.7): need-to-know rules, unit level (spec G1.i)."""
import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from qa_core.tests.conftest import *  # noqa: F401,F403

from .pp_fixtures import assign_doc, assignment_mode, pp, set_config, supervise  # noqa: F401

pytestmark = pytest.mark.django_db


def ids(user):
    from accounts.models import User
    from assignments.access import FULL, visible_institution_ids
    out = visible_institution_ids(User.objects.get(pk=user.pk))
    return out if out is FULL else set(out)


def fresh(u):
    from accounts.models import User
    return User.objects.get(pk=u.pk)


def test_p1_department_mode_is_todays_rule(pp):
    from assignments.access import FULL
    assert ids(pp.superuser) is FULL
    assert ids(pp.dpap_idle) == {pp.a.pk}  # department mode: whole handling department
    assert ids(pp.legal_officer) == set()


def test_p1_document_assignment_opens_temporary_access_in_department_mode(pp):
    from assignments import work
    from assignments.access import can_see, journey_variant
    from assignments.models import WorkAssignment
    from institutions.models import Institution, InstitutionDocument

    assert ids(fresh(pp.legal_officer)) == set()
    wa = work.ensure(pp.a, WorkAssignment.DOCUMENT_ANALYSIS, pp.legal_officer, subject=pp.doc,
                     department=pp.D["legal"], assigned_by=pp.legal_dir, payable=False)
    reviewer = fresh(pp.legal_officer)
    assert ids(reviewer) == {pp.a.pk}
    assert Institution.objects.for_user(reviewer).get(pk=pp.a.pk) == pp.a
    assert can_see(reviewer, pp.a, "summary")
    assert can_see(reviewer, pp.doc, "documents")
    assert list(InstitutionDocument.objects.for_user(reviewer)) == [pp.doc]
    assert journey_variant(reviewer, pp.a) == "mine"
    assert client_for(reviewer).get(f"/qa/institutions/{pp.a.code}/").status_code == 200
    work.complete(wa, reviewer)
    assert ids(reviewer) == set()
    assert list(Institution.objects.for_user(reviewer)) == []


def test_x1_unassigned_staff_sees_nothing(pp):
    from institutions.models import Institution
    assignment_mode()
    assert ids(pp.dpap_idle) == set()
    assert list(Institution.objects.for_user(fresh(pp.dpap_idle))) == []
    assert ids(pp.ict_other) == set()


def test_x1_assignment_opens_summary_and_subject_only(pp):
    from assignments.access import can_see
    from institutions.models import InstitutionDocument
    assignment_mode()
    wa = assign_doc(pp)
    u = fresh(pp.dpap_officer)
    assert ids(u) == {pp.a.pk}
    assert can_see(u, pp.a, "summary")
    assert can_see(u, pp.doc, "documents")
    assert list(InstitutionDocument.objects.for_user(u)) == [pp.doc]
    # Not the other departments' clearances, not the case, not the full journey
    assert not can_see(u, pp.cl["legal"])
    assert not can_see(u, pp.case)
    assert not can_see(u, pp.a, "full_journey")
    assert wa.status == "accepted"


def test_x1_grant_revoked_on_completion(pp):
    from assignments import work
    from assignments.models import AccessGrant
    assignment_mode()
    wa = assign_doc(pp)
    assert AccessGrant.objects.live(pp.dpap_officer).count() >= 1
    work.complete(wa, pp.dpap_dir)
    assert AccessGrant.objects.live(pp.dpap_officer).count() == 0
    assert ids(pp.dpap_officer) == set()  # gone within the same request


def test_x2_full_viewers(pp):
    from assignments.access import FULL, full_viewer_everywhere
    assignment_mode()
    for u in (pp.superuser, pp.es, pp.ict_dir):
        assert full_viewer_everywhere(fresh(u)), u.email
        assert ids(u) is FULL
    for u in (pp.dpap_dir, pp.supervisor, pp.legal_dir):
        assert not full_viewer_everywhere(fresh(u))


def test_x2_ict_supervisor_per_institution(pp):
    from accreditation.models import DepartmentalClearance
    from assignments.access import can_see, journey_variant
    assignment_mode()
    supervise(pp)
    u = fresh(pp.supervisor)
    assert ids(u) == {pp.a.pk}
    assert can_see(u, pp.a, "full_journey")
    assert not can_see(u, pp.b)
    assert set(DepartmentalClearance.objects.for_user(u)) == set(pp.cl.values())
    assert journey_variant(u, pp.a) == "full"


@pytest.mark.parametrize("model_attr", ["cl", "case", "complaint", "timeline"])
def test_x3_director_sees_only_own_department_rows(pp, model_attr):
    from accreditation.models import DepartmentalClearance, QACase
    from complaints.models import Complaint
    from qa_core.models import TimelineEvent
    assignment_mode()
    dpap, legal = fresh(pp.dpap_dir), fresh(pp.legal_dir)
    if model_attr == "cl":
        assert list(DepartmentalClearance.objects.for_user(dpap)) == [pp.cl["dpap"]]
        assert list(DepartmentalClearance.objects.for_user(legal)) == [pp.cl["legal"]]
    elif model_attr == "case":
        assert list(QACase.objects.for_user(dpap)) == [pp.case]
        assert list(QACase.objects.for_user(legal)) == []
    elif model_attr == "complaint":
        assert list(Complaint.objects.for_user(dpap)) == []
        assert list(Complaint.objects.for_user(legal)) == [pp.complaint]
    else:
        seen = set(TimelineEvent.objects.for_user(dpap).values_list("event_code", flat=True))
        assert seen == {"t_public", "t_dpap"}
        seen = set(TimelineEvent.objects.for_user(legal).values_list("event_code", flat=True))
        assert seen == {"t_public", "t_legal"}


def test_x3_other_director_sees_nothing(pp):
    assignment_mode()
    assert ids(pp.vtsd_dir) == {pp.b.pk}  # VTSD handles B only
    assert pp.a.pk not in ids(pp.vtsd_dir)


def test_x4_rank_delegation(pp):
    """A deputy sees the department's part only when the director delegates
    assignments.view_department_qa."""
    from django.contrib.auth.models import Permission
    from accreditation.models import DepartmentalClearance
    assignment_mode()
    assert list(DepartmentalClearance.objects.for_user(fresh(pp.dpap_idle))) == []
    perm = Permission.objects.get(codename="view_department_qa", content_type__app_label="assignments")
    pp.dpap_idle.user_permissions.add(perm)
    u = fresh(pp.dpap_idle)
    assert list(DepartmentalClearance.objects.for_user(u)) == [pp.cl["dpap"]]


def test_x5_sweep_expires_and_extends(pp):
    import datetime
    from django.utils import timezone
    from assignments import grants
    from assignments.models import AccessGrant
    assignment_mode()
    wa = assign_doc(pp)
    g = AccessGrant.objects.live(pp.dpap_officer).first()
    AccessGrant.objects.filter(pk=g.pk).update(expires_at=timezone.now() + datetime.timedelta(minutes=1))
    out = grants.sweep(report=False)
    assert out["extended"] >= 1  # work still open: extended
    wa.status = "completed"
    type(wa).objects.filter(pk=wa.pk).update(status="completed")
    out = grants.sweep(report=False)
    assert out["closed"] >= 1
    assert AccessGrant.objects.live(pp.dpap_officer).count() == 0


def test_x5_grants_audited(pp):
    from core.models import AuditLogEntry
    from assignments import work
    assignment_mode()
    wa = assign_doc(pp)
    work.complete(wa, pp.dpap_dir)
    actions = set(AuditLogEntry.objects.filter(model_label="assignments.AccessGrant").values_list("action", flat=True))
    assert {"grant_created", "grant_revoked"} <= actions


def test_x7_locked_step_refuses_save_and_reopen_audited(pp):
    from core.models import AuditLogEntry
    from assignments import locks, work
    from assignments.locks import StepLocked
    wa = assign_doc(pp)
    work.complete(wa, pp.dpap_dir)
    wa.refresh_from_db()
    assert wa.locked_at is not None
    wa.due_at = wa.completed_at
    with pytest.raises(StepLocked):
        wa.save()
    wa.refresh_from_db()
    with pytest.raises(PermissionDenied):
        locks.reopen_completed_step(wa, fresh(pp.dpap_officer), "try")
    with pytest.raises(PermissionDenied):
        locks.reopen_completed_step(wa, fresh(pp.legal_dir), "not my department")
    with pytest.raises(ValidationError):
        locks.reopen_completed_step(wa, fresh(pp.es), "  ")
    from django.contrib.auth.models import Permission
    fresh(pp.dpap_dir).user_permissions.add(Permission.objects.get(codename="reopen_completed_step"))
    locks.reopen_completed_step(wa, fresh(pp.dpap_dir), "Analysis missed page 4", status="in_progress")
    wa.refresh_from_db()
    assert wa.locked_at is None and wa.status == "in_progress"
    assert AuditLogEntry.objects.filter(model_label="assignments.WorkAssignment", action="reopen").exists()
    from qa_core.models import TimelineEvent
    assert TimelineEvent.unscoped.filter(event_code="step_reopened", institution=pp.a).exists()


def test_p1_backfill_creates_rows_and_is_idempotent(pp):
    from assignments import backfill
    from assignments.models import WorkAssignment
    pp.case.desk_officer = pp.dpap_officer
    pp.case.save()
    pp.case.tracking_ict_officers.add(pp.supervisor)
    # New assignments are mirrored at once by assignments.sync; the back-fill
    # finds them and creates nothing twice.
    first = backfill.run()
    for key in ("QACase.desk_officer", "QACase.tracking_ict_officers"):
        assert first[key]["created"] + first[key]["existing"] == 1, key
    assert WorkAssignment.objects.filter(kind="oversight_ict", assignee=pp.supervisor, institution=pp.a,
                                         status__in=WorkAssignment.WORKING).exists()
    second = backfill.run()
    assert sum(r["created"] for r in second.values()) == 0
    assert "Total" in backfill.render_report(second, dry_run=True)


def test_p1_work_assignment_never_deleted(pp):
    wa = assign_doc(pp)
    with pytest.raises(ValueError):
        wa.delete()


def test_p2_cache_invalidates_on_assignment(pp):
    from assignments.access import visible_institution_ids
    assignment_mode()
    u = fresh(pp.dpap_officer)
    assert visible_institution_ids(u) == set()
    assign_doc(pp)
    assert visible_institution_ids(u) == {pp.a.pk}  # same user object, same "request"


def test_p11_privacy_mode_is_dual_control(pp):
    from config.models import ConfigEntry
    entry = ConfigEntry.objects.get(key="qa.privacy.mode")
    assert entry.dual_control and entry.value == "department"


def test_p5_weekly_digest(pp):
    from assignments import digest
    from notifications.models import Notification
    assign_doc(pp)
    data = digest.send()
    assert data["granted"] >= 1 and data["assigned"] >= 1
    assert Notification.objects.filter(recipient=pp.ict_dir, title="Weekly access digest").exists()


def test_p1_rotation_dialog_signature(pp):
    """assignments.views_rotation._create_work calls work.assign(**kwargs, subject=...)."""
    from assignments import work
    wa = work.assign(institution=pp.a, kind="ssq_review", department=pp.D["dpap"], assignee=pp.dpap_officer,
                     assigned_by=pp.dpap_dir, subject=None)
    assert wa.status == "offered" and wa.subject == pp.a


def test_p1_visit_meta_dates(pp):
    from assignments import work
    wa = work.ensure(pp.a, "visit_panel", pp.dpap_officer, subject=pp.case,
                     meta={"from": "2026-11-09", "to": "2026-11-11"})
    wa = work.ensure(pp.a, "visit_panel", pp.dpap_officer, subject=pp.case,
                     meta={"from": "2026-11-10", "to": "2026-11-12"})
    wa.refresh_from_db()
    assert wa.meta["from"] == "2026-11-10" and wa.meta["to"] == "2026-11-12"
