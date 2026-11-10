"""SOP-03 people rules (accreditation/visit_people.py): who the cost-sheet,
personnel and team-lead pickers recommend, mark busy / on leave, or lock —
and the server refusing a locked person."""
import datetime

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from qa_core.tests.phase4_fixtures import advance_to, second_case, set_config, staff_client

pytestmark = pytest.mark.django_db


def _rate(code):
    from accreditation.models import RateTable

    return RateTable.objects.get(code=code, department__isnull=True, state__isnull=True)


def _row(rows, user):
    return next(r for r in rows if r["pk"] == user.staff_profile.pk)


def _rank(case, rate=None, purpose="cost"):
    from accreditation import case_services as cs
    from accreditation import visit_people as vp

    return vp.rank(case, cs.staff_choices(case), rate=rate, purpose=purpose)


def test_recommended_available_and_locked_for_a_member_line(p4):
    case = advance_to(p4, 2)
    rows = _rank(case, _rate("dta-officer"))
    desk = _row(rows, p4.people.desk_officer)  # DPAP (handling department), GL 10, light load
    assert desk["status"] == "recommended"
    assert ("good", "Handling department") in desk["reasons"]
    fin = _row(rows, p4.people.finance_officer)  # another department: fine, not preferred
    assert fin["status"] == "available"
    director = _row(rows, p4.people.director)  # GL 17 is outside the member band GL 7–16
    assert director["status"] == "locked" and "GL 17" in director["blocked"]
    # Recommended first, Not allowed last.
    order = [r["status"] for r in rows]
    assert order.index("recommended") < order.index("available") < order.index("locked")


def test_busy_over_the_open_task_cap_and_on_an_overlapping_visit(p4):
    from accreditation.models import PersonnelEntry, PersonnelList, VisitDetail

    case = advance_to(p4, 2)
    set_config("qa.people.busy_open_tasks", "1")  # the desk officer holds the stage-2 task
    desk = _row(_rank(case, _rate("dta-officer")), p4.people.desk_officer)
    assert desk["status"] == "busy" and any("open tasks" in t for _k, t in desk["reasons"])
    set_config("qa.people.busy_open_tasks", "0")

    other = second_case(p4)
    start = timezone.localdate() + datetime.timedelta(days=3)
    VisitDetail.unscoped.update_or_create(case=other, defaults={"planned_start": start,
                                                               "planned_end": start + datetime.timedelta(days=2)})
    plist, _ = PersonnelList.unscoped.get_or_create(case=other)
    PersonnelEntry.unscoped.create(personnel_list=plist, staff=p4.people.ict_officer.staff_profile, duty="member")
    ict = _row(_rank(case, _rate("dta-officer")), p4.people.ict_officer)
    assert ict["status"] == "busy"
    assert any("visit to" in t for _k, t in ict["reasons"])


def test_on_leave_is_shown_as_away(p4, monkeypatch):
    from accreditation import visit_people as vp

    case = advance_to(p4, 2)
    sid = p4.people.finance_officer.staff_profile.pk
    today = timezone.localdate()
    monkeypatch.setattr(vp.Facts, "_leave", lambda self, ids: {sid: (today, today + datetime.timedelta(days=5))})
    fin = _row(_rank(case, _rate("dta-officer")), p4.people.finance_officer)
    assert fin["status"] == "away" and any(t.startswith("On leave") for _k, t in fin["reasons"])


def test_team_lead_line_locks_other_roles_and_a_second_lead(p4):
    from accreditation import case_services as cs

    case = advance_to(p4, 2)
    lead = _rate("dta-director")
    rows = _rank(case, lead)
    assert _row(rows, p4.people.desk_officer)["status"] == "locked"  # an officer cannot lead
    assert _row(rows, p4.people.director)["status"] in ("recommended", "available")
    cs.add_cost_line(case, user=p4.people.desk_officer, rate=lead, days="2", staff=p4.people.director.staff_profile)
    rows = _rank(case, lead)
    other = _row(rows, p4.people.audit_director)  # also a director, but the sheet has its lead
    assert other["status"] == "locked" and "already costed as team lead" in other["blocked"]
    assert "Already has this line" in _row(rows, p4.people.director)["blocked"]


def test_locked_person_is_refused_by_the_service_and_the_view(p4):
    from accreditation import case_services as cs
    from accreditation.models import CostLine

    case = advance_to(p4, 2)
    lead = _rate("dta-director")
    with pytest.raises(ValidationError, match="cannot be costed"):
        cs.add_cost_line(case, user=p4.people.desk_officer, rate=lead, days="2",
                         staff=p4.people.desk_officer.staff_profile)
    before = CostLine.unscoped.count()
    client = staff_client(p4.people.desk_officer)
    r = client.post(reverse("qa_case:cost_add", args=[case.pk]),
                    {"rate": lead.pk, "days": "2", "staff": p4.people.desk_officer.staff_profile.pk, "tab": "cost"},
                    HTTP_HX_REQUEST="true")
    assert r.status_code == 422 and "cannot be costed" in r["HX-Trigger"]
    assert CostLine.unscoped.count() == before
    # No person, or an allowed one, still works.
    r = client.post(reverse("qa_case:cost_add", args=[case.pk]),
                    {"rate": lead.pk, "days": "2", "staff": p4.people.director.staff_profile.pk, "tab": "cost"},
                    HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and CostLine.unscoped.count() == before + 1


def test_busy_person_is_added_with_a_warning(p4):
    from accreditation.models import CostLine

    case = advance_to(p4, 2)
    set_config("qa.people.busy_open_tasks", "1")
    client = staff_client(p4.people.desk_officer)
    r = client.post(reverse("qa_case:cost_add", args=[case.pk]),
                    {"rate": _rate("dta-officer").pk, "days": "2", "staff": p4.people.desk_officer.staff_profile.pk,
                     "tab": "cost"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and '"warning"' in r["HX-Trigger"]
    assert CostLine.unscoped.filter(staff=p4.people.desk_officer.staff_profile).exists()


def test_ssq_reviewer_is_locked_r02(p4):
    from accreditation.models import SSQ

    case = advance_to(p4, 2)
    SSQ.unscoped.filter(qa_request_programme__qa_request=case.qa_request).update(reviewed_by=p4.people.ict_officer)
    ict = _row(_rank(case, _rate("dta-officer")), p4.people.ict_officer)
    assert ict["status"] == "locked" and "R-02" in ict["blocked"]
    # …but the audit supervision line (not a visiting line) does not apply R-02.
    audit = _row(_rank(case, _rate("audit-supervision")), p4.people.audit_officer)
    assert audit["status"] in ("recommended", "available")
    assert _row(_rank(case, _rate("audit-supervision")), p4.people.desk_officer)["status"] == "locked"


def test_external_only_line_locks_nbte_staff(p4):
    case = advance_to(p4, 2)
    rows = _rank(case, _rate("honorarium-rp"))
    assert _row(rows, p4.people.desk_officer)["status"] == "locked"


def test_personnel_batch_workload_matches_the_per_person_rule(p4):
    from accreditation import case_services as cs
    from accreditation import visit_people as vp
    from inspection import workload

    case = advance_to(p4, 3)
    set_config("qa.resource_person.max_visits_per_year", "0")  # everyone is at the cap
    staff = cs.staff_choices(case)
    batch = vp.batch_workload(case, staff)
    for st in staff:
        single = workload.problems_for(st.user, duty="physical_verification", institution_id=case.institution_id)
        assert len(single) == len(batch.get(st.pk, [])), st
    rows = vp.rank(case, staff, purpose="personnel")
    assert all(r["status"] == "locked" for r in rows if r["pk"] in batch)


def test_cost_people_endpoint_reranks_for_the_rate(p4):
    case = advance_to(p4, 2)
    client = staff_client(p4.people.desk_officer)
    r = client.get(reverse("qa_case:cost_people", args=[case.pk]), {"rate": _rate("dta-director").pk},
                   HTTP_HX_REQUEST="true")
    assert r.status_code == 200
    body = r.content.decode()
    assert 'id="cl-person-wrap"' in body and "one per sheet" in body and 'data-status="locked"' in body
    # Someone who cannot edit the sheet is refused.
    r = staff_client(p4.people.audit_officer).get(reverse("qa_case:cost_people", args=[case.pk]))
    assert r.status_code in (403, 404)


# ---------------------------------------------------------------------------
# NBTE staff only — and the external participants of the visit team
# ---------------------------------------------------------------------------
def _portal_staff_row(email, user_type, role=None):
    """A portal account that also has a staff directory row (older databases
    auto-created them) — it must never be offered or accepted."""
    from accounts.models import Role, User
    from staff.models import Staff

    from accreditation.models import QACase  # noqa: F401
    institution = None
    if user_type == "institution":
        from institutions.models import Institution

        institution = Institution.unscoped.first()
    user = User.objects.create_user(email=email, password="x-Long-Password-1", first_name="Portal",
                                    last_name=user_type.title(), user_type=user_type, institution=institution,
                                    role=Role.objects.filter(code=role).first() if role else None)
    staff = Staff.objects.filter(user=user).first() or Staff.objects.create(
        employee_no=f"P-{user.pk}", first_name="Portal", last_name=user_type.title(), email=email, user=user)
    return user, staff


def test_non_staff_never_appear_in_any_sop03_picker(p4):
    from accreditation import case_services as cs
    from accreditation import case_ui
    from accreditation import case_views

    case = advance_to(p4, 2)
    inst_user, inst_staff = _portal_staff_row("portal.inst@example.test", "institution", role="inst-admin")
    rp_user, rp_staff = _portal_staff_row("portal.rp@example.test", "resource_person", role="resource-person")
    bad_users, bad_staff = {inst_user.pk, rp_user.pk}, {inst_staff.pk, rp_staff.pk}
    assert not bad_staff & {s.pk for s in cs.staff_choices(case)}
    for kind in ("desk", "audit", "finance", "ict"):
        assert not bad_users & {u.pk for u in cs.eligible_officers(case, kind)}
    assert not bad_users & {u.pk for u in case_views._reassign_choices()}
    assert not bad_users & {r["pk"] for r in case_ui.people([inst_user, rp_user, p4.people.desk_officer])}
    assert not bad_staff & {r["pk"] for r in _rank(case, _rate("dta-officer"))}
    assert "qa-system@nbte.local" not in {u.email for u in case_views._reassign_choices()}
    body = staff_client(p4.people.desk_officer).get(
        reverse("qa_case:detail", args=[case.pk]) + "?tab=cost").content.decode()
    assert f'value="{inst_staff.pk}"' not in body and "portal.inst@example.test" not in body


def test_posting_a_non_staff_person_is_refused(p4):
    from accreditation.models import CostLine, PersonnelEntry

    case = advance_to(p4, 2)
    _u, inst_staff = _portal_staff_row("portal.inst2@example.test", "institution", role="inst-admin")
    client = staff_client(p4.people.desk_officer)
    r = client.post(reverse("qa_case:cost_add", args=[case.pk]),
                    {"rate": _rate("transport-road").pk, "days": "1", "staff": inst_staff.pk, "tab": "cost"},
                    HTTP_HX_REQUEST="true")
    assert r.status_code == 422 and "only NBTE staff" in r["HX-Trigger"]
    assert not CostLine.unscoped.filter(staff=inst_staff).exists()
    case = advance_to(p4, 3, case)
    r = client.post(reverse("qa_case:personnel_add", args=[case.pk]),
                    {"staff": inst_staff.pk, "duty": "member", "tab": "personnel"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 422 and "only NBTE staff" in r["HX-Trigger"]
    assert not PersonnelEntry.unscoped.filter(staff=inst_staff).exists()


def test_desk_officer_must_be_nbte_staff(p4):
    from accreditation import case_services as cs

    inst_user, _s = _portal_staff_row("portal.inst3@example.test", "institution", role="inst-admin")
    with pytest.raises(ValidationError, match="only NBTE staff"):
        cs.set_desk_officer(p4.case, user=p4.people.director, officer=inst_user)


def _rp(email, discipline, status="active", primary=True, home=None):
    from inspection.models import ResourcePerson, ResourcePersonDiscipline

    rp = ResourcePerson.objects.create(full_name=email.split("@")[0].title(), email=email, status=status,
                                       home_institution=home)
    ResourcePersonDiscipline.objects.create(resource_person=rp, discipline=discipline, is_primary=primary)
    return rp


def test_visit_team_externals_match_the_case_discipline(p4):
    from institutions.models import Discipline

    from accreditation import visit_people as vp
    from accreditation.models import VisitExternalMember

    case = advance_to(p4, 3)
    prog = vp.case_programmes(case)[0]
    eng, _ = Discipline.objects.get_or_create(code="s3b-eng", defaults={"name": "Engineering (test)"})
    other, _ = Discipline.objects.get_or_create(code="s3b-art", defaults={"name": "Arts (test)"})
    prog.category.discipline = eng
    prog.category.save()
    match = _rp("match.rp@example.test", eng)
    miss = _rp("miss.rp@example.test", other)
    local = _rp("local.rp@example.test", eng, home=case.institution)
    rows = {r["pk"]: r for r in vp.external_candidates(case)}
    assert f"rp:{match.pk}" in rows and rows[f"rp:{match.pk}"]["status"] == "recommended"
    assert rows[f"rp:{match.pk}"]["rank"] == "External resource person"
    assert f"rp:{miss.pk}" not in rows  # other disciplines are not offered
    assert rows[f"rp:{local.pk}"]["status"] == "locked"  # works at the institution
    client = staff_client(p4.people.desk_officer)
    url = reverse("qa_case:personnel_external_add", args=[case.pk])
    r = client.post(url, {"external": f"rp:{match.pk}", "tab": "personnel"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 200
    m = VisitExternalMember.unscoped.get(case=case, resource_person=match)
    assert m.discipline == eng and m.label == "External resource person · Engineering (test)"
    for value, why in ((f"rp:{miss.pk}", "does not cover"), (f"rp:{local.pk}", "conflict of interest"),
                       (f"rp:{match.pk}", "already on the visit team"), ("pb:999999", "professional body")):
        r = client.post(url, {"external": value, "tab": "personnel"}, HTTP_HX_REQUEST="true")
        assert r.status_code == 422 and why in r["HX-Trigger"], value
    # The staff picker never lists the externals, and the tab shows them apart.
    body = client.get(reverse("qa_case:detail", args=[case.pk]) + "?tab=personnel").content.decode()
    assert "External participants" in body and "External resource person" in body
