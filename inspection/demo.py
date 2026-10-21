"""Drive the Phase 5 part of a demo case (stage 12–13) through the real
services — used by ``seed_phase5_demo``, ``case_demo.advance`` and tests."""
# scoped-by-caller
from __future__ import annotations

import datetime

from django.utils import timezone

from . import services

DEMO_FINDINGS = {
    "facilities": "Classrooms and laboratories match the SSQ; the electrical workshop needs two more benches.",
    "staffing": "Staff list verified against the SSQ; one HOD position is acting.",
    "curriculum": "The NBTE curriculum is in use; practical logbooks were inspected.",
}
DEMO_SUMMARY = ("The team visited every facility listed in the SSQ and met the staff. The programme meets the "
                "minimum standard, with two minor deficiencies noted for follow-up.")


def complete_visit(case, *, absent=()):
    inspection = services.live_inspection(case)
    if inspection is None:
        inspection = services.create_for_case(case)
    if inspection.status == "scheduled":
        today = timezone.localdate()
        services.complete(inspection, actor=inspection.team_lead, actual_start=today - datetime.timedelta(days=2),
                          actual_end=today - datetime.timedelta(days=1), absent=absent)
        inspection.refresh_from_db()
    return inspection


def write_and_submit(report, user=None):
    user = user or report.inspection.team_lead
    services.save_report(report, user=user, summary=DEMO_SUMMARY, findings=DEMO_FINDINGS, recommendation="accredit")
    return services.submit_report(report, user=user)


def endorse_once(report, action="endorse", content=""):
    report.refresh_from_db()
    holders, _link = services.report_holders(report)
    actor = holders[0] if holders else None
    return services.endorse(report, user=actor, action=action, content=content)


def drive_reports(case, *, until="director_endorsed"):
    """Write, submit and endorse every report up to ``until`` (a report stage)."""
    order = ["draft", "submitted", "division_endorsed", "deputy_verified", "director_endorsed"]
    stop = order.index(until)
    for report in services.reports_of(case):
        for _ in range(8):
            report.refresh_from_db()
            if report.status not in order or order.index(report.status) >= stop:
                if report.status == "returned" and stop > 0:
                    write_and_submit(report, report.submitted_by)
                    continue
                break
            if report.status == "draft":
                write_and_submit(report)
            else:
                endorse_once(report, content="Endorsed (demo).")
    return services.reports_of(case)


def recommend_all(case):
    from accreditation import case_services as cs

    holder = (cs.holders(case, "post_visit_recommendation") or [None])[0]
    for report in services.reports_of(case):
        if not report.recommended_by_id:
            services.recommend(report, user=holder, recommendation=report.recommendation or "accredit",
                               note="The Directorate recommends accreditation (demo).")


# ===========================================================================
# Building the Phase 5 demo (seed_phase5_demo and the tests)
# ===========================================================================
DEMO_DESIGNATIONS = [("director", "DIRECTOR", 90), ("deputy-director", "DEPUTY DIRECTOR", 80)]


def ensure_designations():
    from staff.models import Designation

    for code, name, rank in DEMO_DESIGNATIONS:
        if not Designation.objects.filter(code=code).exists():
            Designation.objects.create(code=code, name=name if not Designation.objects.filter(name=name).exists()
                                       else f"{name} (QA)", rank=rank)


def ensure_resource_persons(people, inst, *, tag=True):
    """The demo resource persons (one per §9 rule they illustrate). Returns
    {key: ResourcePerson}."""
    from accounts.models import Role, User
    from institutions.models import Discipline
    from qa_core.models import DemoTag
    from qa_core.seed_data_phase5 import DEMO_RESOURCE_PERSONS

    from .models import ResourcePerson, ResourcePersonDiscipline, ResourcePersonFile

    out = {}
    nominator, vetter = people.desk_officer, people.director
    for key, name, email, source, rank, disciplines, status, portal, notes in DEMO_RESOURCE_PERSONS:
        rp = ResourcePerson.objects.filter(email__iexact=email).first()
        if rp is None:
            rp = ResourcePerson.objects.create(
                full_name=name, email=email, source=source, rank=rank, qualifications=notes,
                professional_body="COREN" if "Engr" in name else "", nominated_by=nominator,
                home_institution=inst if key == "rp_coi" else None)
            for code, primary in disciplines:
                d = Discipline.objects.filter(code=code).first()
                if d is not None:
                    ResourcePersonDiscipline.objects.create(resource_person=rp, discipline=d, is_primary=primary)
            from django.core.files.base import ContentFile

            ResourcePersonFile.objects.create(resource_person=rp, kind="cv", uploaded_by=nominator,
                                              file=ContentFile(b"%PDF-1.4\n% demo CV\n", name="cv.pdf"))
            if status == "active":
                ResourcePerson.objects.filter(pk=rp.pk).update(status="active", vetted_by=vetter,
                                                               vetted_at=timezone.now(), activated_at=timezone.now())
            if tag:
                DemoTag.tag(rp)
            rp.refresh_from_db()
        if portal and rp.user_id is None:
            user = User.objects.filter(email__iexact=email).first()
            if user is None:
                first, _, last = name.partition(" ")
                user = User(email=email, first_name=first, last_name=last or "-", user_type="resource_person",
                            role=Role.objects.filter(code="resource-person").first(), is_active=True)
                user.set_unusable_password()
                user.save()
                if tag:
                    DemoTag.tag(user)
            ResourcePerson.objects.filter(pk=rp.pk).update(user=user)
            rp.refresh_from_db()
        out[key] = rp
    return out


def ensure_qa_personnel(people):
    """The demo directors registered for QA duty (verification officers)."""
    from institutions.models import Discipline

    from .models import QAPersonnel

    out = {}
    for key in ("team_lead", "qa_officer2"):
        user = people.get(key)
        staff = getattr(user, "staff_profile", None) if user else None
        if staff is None:
            continue
        qp = QAPersonnel.objects.filter(staff=staff).first()
        if qp is None:
            qp = QAPersonnel.objects.create(staff=staff, is_verification_officer=True,
                                            is_team_lead_eligible=key == "team_lead", registered_by=people.director)
            qp.disciplines.set(Discipline.objects.filter(code__in=("engineering", "science-environmental")))
        out[key] = qp
    return out


def backfill_capped(rp, *, visits=6, tag=True):
    """History for the 'annual cap reached' demo person (§9 backfill)."""
    from institutions.models import Institution
    from qa_core.models import DemoTag
    from qa_core.departments import qa_department

    from .models import ServiceRecord

    if ServiceRecord.unscoped.filter(resource_person=rp, source="backfill").exists():
        return
    other = Institution.unscoped.filter(code="ZDC").first()
    if other is None:
        other = Institution.unscoped.create(code="ZDC", name="Zaria Demo College (Phase 5 history)",
                                            handling_department=qa_department("dpap"), status="approved")
        if tag:
            DemoTag.tag(other)
    today = timezone.localdate()
    for n in range(visits):
        day = today.replace(month=1, day=10) + datetime.timedelta(days=20 * n)
        if day > today:
            day = today - datetime.timedelta(days=n + 1)
        ServiceRecord.unscoped.create(person_type="resource_person", person_id=rp.pk, person_name=rp.full_name,
                                      resource_person=rp, institution=other, duty="resource_inspection",
                                      served_from=day, served_to=day, visit_count=1, source="backfill",
                                      reason="Demo history (earlier visits this year)")


def map_demo_people(case, people, rps):
    """Map the two demo assessors to the case's programmes through the
    Availability Finder rules (book → Edit QA Mapping → a second officer
    confirms). Needs the visit dates (stage 8). Idempotent."""
    from accreditation.models import QARequestProgramme
    from qa_core import actions

    from .models import QAMapping

    rows = {r.programme.category.discipline.code if r.programme.category_id and r.programme.category.discipline_id
            else "": r for r in QARequestProgramme.unscoped.filter(qa_request=case.qa_request, status="active")
            .select_related("programme__category__discipline")}
    out = []
    for key, discipline in (("rp_eng", "engineering"), ("rp_sci", "science-environmental")):
        rp, row = rps.get(key), rows.get(discipline)
        if rp is None or row is None:
            continue
        mapping = QAMapping.unscoped.filter(case=case, resource_person=rp, status__in=QAMapping.LIVE).first()
        if mapping is None:
            mapping, pa = services.propose_mapping(case, row, duty="resource_inspection", person=rp,
                                                   actor=people.desk_officer,
                                                   reason=f"Demo: {rp.full_name} for {row.programme}")
            if pa is not None and pa.status == "pending":
                actions.confirm(pa, people.director, remark="Eligible in the finder (demo).")
            mapping.refresh_from_db()
        out.append(mapping)
    return out


DEMO_RP_FINDINGS = ("Workshops visited with the team: the electrical machines laboratory has the equipment listed "
                    "in the SSQ; two oscilloscopes were out of order. Logbooks for ND II were complete.")


def submit_demo_findings(case, rps):
    """The engineering assessor writes findings on the portal (feeds the report)."""
    from .models import QAMapping

    rp = rps.get("rp_eng")
    if rp is None or rp.user_id is None:
        return
    for mapping in QAMapping.unscoped.filter(case=case, resource_person=rp).exclude(status="overridden"):
        if not mapping.findings and services.findings_open(mapping)[0]:
            services.submit_findings(mapping, user=rp.user, text=DEMO_RP_FINDINGS)
