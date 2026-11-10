"""Build and drive a demo SOP-03 case (Phase 4) — used by
``manage.py seed_phase4_demo`` and by the Phase 4 tests.

Everything goes through the real services (QA request → SSQs → QA case →
the flow → the task bridge), so the tasks the demo creates are exactly the
ones a real case would create.
"""
# scoped-by-caller
from __future__ import annotations

import datetime
import secrets

from django.contrib.auth.models import Group
from django.db import transaction
from django.utils import timezone

from qa_core.departments import qa_department
from qa_core.models import DemoTag
from qa_core.seed_data_phase4 import DEMO_ACCOUNTS as P4_ACCOUNTS
from qa_core.seed_data_phase4 import DEMO_INSTITUTION, DEMO_PROGRAMMES
from qa_core.seed_data_phase5 import DEMO_ACCOUNTS as P5_ACCOUNTS
from qa_core.seed_data_phase5 import DEMO_DESIGNATIONS

# Phase 5 adds the Team Lead, Division Head, Deputy Director and a QA verifier
# (key, email, first, last, role, department, grade[, designation code]).
DEMO_ACCOUNTS = [tuple(row) + (DEMO_DESIGNATIONS.get(row[0], ""),) for row in P4_ACCOUNTS] + list(P5_ACCOUNTS)

from . import case_services as cs
from . import qa_services as svc
from .models import QACase, QARequest, QAType, RateTable

# The order the stages are driven in (each entry: stage code, who acts).
STAGE_NUMBERS = {
    "case_opened": 0, "assign_supervising_staff": 1, "generate_cost": 2, "add_personnel": 3,
    "confirm_personnel": 4, "audit_personnel_cost": 5, "confirm_audit_cost": 6, "es_approve_cost": 7,
    "add_visit_details": 8, "confirm_visit_details": 9, "es_approve_visit": 10, "request_fund_release": 11,
    "post_visit_report": 12, "post_visit_recommendation": 13, "cqac_recommendation": 14, "cqac_chair_approval": 15,
    "es_final_approval": 16, "programme_auto_update": 17, "closed": 18,
}


class People(dict):
    """key → User (keys from DEMO_ACCOUNTS)."""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(item) from None


def ensure_accounts(*, reset_passwords=False, force_change=True, tag=True, emails=None):
    """Create the demo accounts (with a staff profile and grade level).
    Returns (people, credentials) where credentials is a list of
    (key, email, password-or-None, role, department)."""
    from accounts.models import Role, User
    from institutions.models import Institution
    from staff.models import Staff

    people, creds = People(), []
    inst = Institution.unscoped.filter(code=DEMO_INSTITUTION["code"]).first()
    from staff.models import Designation

    for key, email, first, last, role_code, dept_code, grade, designation_code in DEMO_ACCOUNTS:
        if emails is not None:
            email = emails.get(key, email)
        user = User.objects.filter(email__iexact=email).first()
        password, created = None, False
        if user is None:
            created = True
            password = secrets.token_urlsafe(9)
            user = User(email=email, first_name=first, last_name=last,
                        user_type="institution" if role_code.startswith("inst-") else "staff",
                        role=Role.objects.filter(code=role_code).first(),
                        department=qa_department(dept_code) if dept_code else None,
                        institution=inst if role_code.startswith("inst-") else None,
                        must_change_password=force_change, is_active=True)
            user.set_password(password)
            user.save()
            if tag:
                DemoTag.tag(user)
        elif reset_passwords:
            password = secrets.token_urlsafe(9)
            user.set_password(password)
            user.must_change_password = force_change
            user.is_active = True
            user.save()
        if user.user_type == "staff" and grade and getattr(user, "staff_profile", None) is None:
            staff = Staff.objects.filter(email__iexact=email).first()
            if staff is None:
                staff = Staff.objects.create(employee_no=f"DEMO-{user.pk:05d}", first_name=user.first_name,
                                             last_name=user.last_name, email=email, department=user.department,
                                             grade_level=grade, user=user)
                if tag:
                    DemoTag.tag(staff)
            else:
                if staff.user_id is None:
                    staff.user = user
                if not staff.grade_level:
                    staff.grade_level = grade
                staff.save(update_fields=["user", "grade_level"])
        elif user.user_type == "staff" and grade:
            profile = user.staff_profile
            if not profile.grade_level:
                profile.grade_level = grade
                profile.save(update_fields=["grade_level"])
            if tag and created:
                DemoTag.tag(profile)  # the profile the sign-up signal made for a new demo user
        if designation_code and user.user_type == "staff":
            designation = Designation.objects.filter(code=designation_code).first()
            profile = getattr(User.objects.get(pk=user.pk), "staff_profile", None)
            if designation is not None and profile is not None and profile.designation_id is None:
                profile.designation = designation
                profile.save(update_fields=["designation"])
        # v2.1 X23: approvals that generate a document need a signature on file
        # (rank >= qa.signatures.required_min_rank). The real demo seed grants
        # one (documents/seed_sets.py demo_signatures); the test cast must match.
        if user.user_type == "staff":
            from documents.testing import give_if_required

            give_if_required(User.objects.get(pk=user.pk))
        people[key] = User.objects.get(pk=user.pk)
        creds.append((key, email, password, role_code, getattr(user.department, "name", "") or
                      (inst.name if inst and role_code.startswith("inst-") else "")))
    return people, creds


def ensure_institution(people=None, *, tag=True):
    """The Phase 4 demo institution (approved, DPAP) with two programmes
    inside the reassessment window."""
    from institutions.models import Institution, Programme, ProgrammeCategory, ProgrammeLevel
    from staff.models import StateOfOrigin

    spec = DEMO_INSTITUTION
    inst = Institution.unscoped.filter(code=spec["code"]).first()
    if inst is None:
        inst = Institution.unscoped.create(code=spec["code"], name=spec["name"],
                                           handling_department=qa_department(spec["department"]),
                                           status=Institution.APPROVED,
                                           state=StateOfOrigin.objects.filter(name__iexact=spec["state"]).first(),
                                           lga=spec["lga"])
        if tag:
            DemoTag.tag(inst)
    today = timezone.localdate()
    for name, level, category in DEMO_PROGRAMMES:
        if not Programme.unscoped.filter(institution=inst, name=name).exists():
            Programme.unscoped.create(institution=inst, name=name, level=ProgrammeLevel.objects.filter(code=level).first(),
                                      category=ProgrammeCategory.objects.filter(name=category).first(),
                                      status="accredited", approved_streams=1,
                                      commencement_date=today.replace(year=today.year - 4),
                                      expiry_date=today + datetime.timedelta(days=120))
    return inst


def _complete_ssq(ssq, user):
    values = {}
    for field in ssq.template.field_map().values():
        if not field.get("required"):
            continue
        ftype = field["type"]
        if ftype in ("text", "textarea"):
            values[field["key"]] = "Demo answer"
        elif ftype in ("number", "integer"):
            values[field["key"]] = "2"
        elif ftype == "yes_no":
            values[field["key"]] = "yes"
        elif ftype == "date":
            values[field["key"]] = "2024-10-01"
        elif ftype == "choice":
            values[field["key"]] = svc.engine.option_pairs(field)[0][0]
        elif ftype == "table":
            rows = max(1, int(field.get("min_rows") or 1))
            row = {}
            for col in field.get("columns", []):
                ctype = col.get("type", "text")
                row[col["key"]] = ("Full-time" if col.get("options") else
                                   "3" if ctype in ("number", "integer") else "Demo")
                if ctype == "choice" and col.get("options"):
                    opt = col["options"][0]
                    row[col["key"]] = opt.get("value") if isinstance(opt, dict) else opt
            values[field["key"]] = [dict(row) for _ in range(rows)]
    svc.save_fields(ssq, values, user=user)


def _waive(req, people):
    from payments import services as pay

    for ob in svc.open_obligations(req):
        if ob.status in ("issued", "awaiting_payment"):
            if ob.status == "issued":
                pay.publish(ob, actor=people.finance_director)
                ob.refresh_from_db()
            pay.waive(ob, actor=people.finance_director, reason="Phase 4 demo: fee waived")
            ob.refresh_from_db()
            svc.payment_settled(ob)


def demo_pins(people):
    """The demo case's acting officers (QACase.stage_actors), so its stages go
    to the demo accounts even when the database has real directors / an ES."""
    return {"owning_department_director": people.director, "audit_director": people.audit_director,
            "finance_director": people.finance_director, "ict_director": people.ict_director,
            "es": people.es, "cqac": people.cqac_member, "cqac_chair": people.cqac_chair,
            "role:pay-officer": people.pay_officer,
            # Phase 5: the post-visit report endorsement layers (B14)
            "role:division-head": people.get("division_head"), "role:deputy-director": people.get("deputy_director")}


def _chief_executive(inst, *, tag=True):
    """The institution's Chief Executive account (a demo one is created when
    missing, e.g. ce.kdp@p4demo.local)."""
    from accounts.models import Role, User
    from institutions.models import InstitutionUser
    from institutions.services import chief_executives

    found = chief_executives(inst)
    if found:
        return found[0]
    user = User(email=f"ce.{inst.code.lower()}@p4demo.local", first_name="Chief", last_name=f"Executive ({inst.code})",
                user_type="institution", institution=inst, role=Role.objects.get(code="inst-chief-executive"),
                is_active=True, must_change_password=False)
    user.set_password(secrets.token_urlsafe(12))
    user.save()
    InstitutionUser.unscoped.get_or_create(user=user, defaults={"institution": inst, "institution_role": user.role,
                                                                 "job_title": "Chief Executive"})
    if tag:
        DemoTag.tag(user)
    return user


@transaction.atomic
def create_case(people, inst=None):
    """QA request → fee waived → SSQs submitted → QA case (started)."""
    from config.models import ConfigEntry

    inst = inst or ensure_institution(people)
    # The demo needs a bill it can waive: use the demo QA fee when the real one is unset.
    for key, value in (("qa.fee.qa_request.base", "50000"), ("qa.fee.qa_request.per_programme", "150000"),
                       ("qa.fee.qa_request_processing.confirmed", "true")):
        entry = ConfigEntry.objects.filter(key=key, scope="global").first()
        if entry is not None and entry.value in ("0", "false", "", "0.00"):
            entry.value = value
            entry.save()
    from payments.models import BankAccount

    if not BankAccount.objects.filter(is_active=True, is_placeholder=False).exists():
        DemoTag.tag(BankAccount.objects.create(bank_name="Demo Bank (Phase 4)", account_name="NBTE",
                                               account_number="0123456789", is_active=True, is_default=True))
    admin = people.institution_admin
    qa_type = QAType.objects.get(code="reaccreditation")
    from institutions.models import Programme

    programmes = list(Programme.unscoped.filter(institution=inst, status="accredited"))
    req = svc.create_request(inst, qa_type, programmes, user=admin)
    ce = _chief_executive(inst, tag=True)  # X12: only the Chief Executive submits
    svc.submit(req, user=ce, declaration=True)
    req.refresh_from_db()
    _waive(req, people)
    req.refresh_from_db()
    with cs.pinned_actors(demo_pins(people)):
        for ssq in svc.request_ssqs(req):
            _complete_ssq(ssq, admin)
            svc.submit_ssq(ssq, user=ce, declaration=True)
    req.refresh_from_db()
    case = QACase.unscoped.get(qa_request=req)
    DemoTag.tag(req)
    if not case.stage_actors:  # started outside the pins (should not happen): pin now and re-route
        QACase.unscoped.filter(pk=case.pk).update(stage_actors={k: u.pk for k, u in demo_pins(people).items() if u is not None})
    return cs.start_case(case)


def demo_case():
    from institutions.models import Institution

    inst = Institution.unscoped.filter(code=DEMO_INSTITUTION["code"]).first()
    if inst is None:
        return None
    return QACase.unscoped.filter(institution=inst).order_by("-created_at").first()


def stage_no(case):
    stages = cs.current_stages(case)
    if not stages:
        return -1
    if any(s in ("assign_audit_officer", "assign_finance_officer", "assign_ict_officer", "supervisors_assigned")
           for s in stages):
        return 1
    return STAGE_NUMBERS.get(stages[0], -1)


def advance(case, people, target):
    """Drive ``case`` forward with the demo officers until its stage number
    is ``target`` (0 = case opened … 11 = fund release … 14–16 decisions …
    18 = closed: stage 17 has updated the programmes and issued the letters)."""
    for _ in range(40):
        case.refresh_from_db()
        if stage_no(case) >= target:
            return case
        stages = cs.current_stages(case)
        code = stages[0]
        if code == "case_opened":
            cs.set_desk_officer(case, user=people.director, officer=people.desk_officer)
            cs.act(case, user=people.director, stage_code=code, action="approve")
        elif code in ("assign_audit_officer", "assign_finance_officer", "assign_ict_officer"):
            for branch in list(stages):
                if branch == "assign_audit_officer":
                    cs.set_supervisor(case, user=people.audit_director, kind="audit", officers=[people.audit_officer])
                    cs.act(case, user=people.audit_director, stage_code=branch, action="approve")
                elif branch == "assign_finance_officer":
                    cs.set_supervisor(case, user=people.finance_director, kind="finance",
                                      officers=[people.finance_officer])
                    cs.act(case, user=people.finance_director, stage_code=branch, action="approve")
                elif branch == "assign_ict_officer":
                    officers = [people.ict_officer] + ([people.ict_officer2] if cs.ict_required(case) > 1 else [])
                    cs.set_supervisor(case, user=people.ict_director, kind="ict", officers=officers)
                    cs.act(case, user=people.ict_director, stage_code=branch, action="approve")
        elif code == "generate_cost":
            rates = {r.code: r for r in RateTable.objects.filter(is_active=True, department__isnull=True)}
            for rate_code, days, role in (("dta-director", "3", "Team lead"), ("dta-officer", "3", "Member"),
                                          ("transport-road", "1", "Road transport")):
                if rate_code in rates:
                    cs.add_cost_line(case, user=people.desk_officer, rate=rates[rate_code], days=days,
                                     role_on_exercise=role)
            cs.act(case, user=people.desk_officer, stage_code=code, action="approve")
        elif code == "add_personnel":
            lead = people.get("team_lead") or people.director
            cs.add_personnel(case, user=people.desk_officer, staff=lead.staff_profile, duty="team_lead")
            cs.add_personnel(case, user=people.desk_officer, staff=people.desk_officer.staff_profile, duty="member")
            cs.act(case, user=people.desk_officer, stage_code=code, action="approve")
        elif code == "add_visit_details":
            start = timezone.localdate() + datetime.timedelta(days=21)
            lead = people.get("team_lead") or people.director
            cs.save_visit(case, user=people.desk_officer, planned_start=start,
                          planned_end=start + datetime.timedelta(days=2), team_lead=lead.staff_profile,
                          notes="Road travel from Abuja; hotel booked by the institution.")
            cs.act(case, user=people.desk_officer, stage_code=code, action="approve")
        elif code == "post_visit_report":  # Phase 5: the visit, then the report sub-flow
            from inspection import demo as inspection_demo

            inspection_demo.complete_visit(case)
            inspection_demo.drive_reports(case)
        elif code == "post_visit_recommendation":
            from inspection import demo as inspection_demo

            run_demo_capacity(case, people.desk_officer)
            inspection_demo.recommend_all(case)
            holder = (cs.holders(case, code) or [None])[0]
            cs.act(case, user=holder, stage_code=code, action="approve", remark="Demo")
        elif code == "request_fund_release":
            cs.record_release(case, user=people.pay_officer, reference="PV/QA/2026/0001")
            cs.act(case, user=people.pay_officer, stage_code=code, action="approve")
        elif code in ("cqac_recommendation", "cqac_chair_approval", "es_final_approval"):  # Phase 8
            holder = (cs.holders(case, code) or [None])[0]
            if holder is None:
                raise RuntimeError(f"Nobody holds {code}")
            record_demo_decisions(case, code, holder)
            cs.act(case, user=holder, stage_code=code, action="approve", remark="Demo")
        else:
            holder = (cs.holders(case, code) or [None])[0]
            if holder is None:
                raise RuntimeError(f"Nobody holds {code}")
            cs.act(case, user=holder, stage_code=code, action="approve", remark="Demo")
    return case


def record_demo_decisions(case, stage_code, user, *, commencement=None):
    """Phase 8: the demo decides every programme as the stage suggests
    (stage 14 from the Directorate's recommendation, then each stage adopts
    the previous one), starting today unless ``commencement`` is given."""
    from . import decisions as dec

    stage = dec.STAGE_OF_FLOW[stage_code]
    saved = dec.decisions_at(case, stage)
    for row in dec.rows(case):
        if row.pk in saved:
            continue
        start = dec.suggestion(case, row, stage)
        outcome = start["outcome"] if start["outcome"] != "return" else "approve"
        approval = start["approval_type"]
        if outcome == "approve" and approval is None:
            from .models import ApprovalType

            approval = ApprovalType.objects.filter(code="full").first()
        dec.record(case, row, stage, user=user, outcome=outcome, approval_type=approval,
                   approved_streams=start["approved_streams"] or 1,
                   commencement_date=commencement or start["commencement_date"],
                   weaknesses=start["weaknesses"] or ([] if outcome == "approve" else ["Demo weakness"]),
                   note="Demo decision")


def run_demo_capacity(case, user):
    """Prepare capacity as the desk officer and confirm it as Stage 13's Director."""
    from django.core.exceptions import ValidationError

    from . import capacity
    from . import decisions as dec

    rows = dec.rows(case)
    for row in rows:
        current = capacity.current(row)
        for kind, inputs in capacity.prefill(row).items():
            if current[kind] is not None:
                continue
            try:
                capacity.run(row, kind, inputs, user=user)
            except ValidationError:
                continue
    reviewer = next(iter(cs.holders(case, "post_visit_recommendation")), None)
    if reviewer is None:
        raise RuntimeError("Nobody holds post_visit_recommendation to confirm demo capacity.")
    for row in rows:
        if not capacity.missing_assessments([row]):
            capacity.confirm_review(case, row, user=reviewer)
