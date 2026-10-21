"""Demo data for the institution guide screenshots (Phase 6).

Used by ``scripts/capture_guide_screenshots.py``, which runs it against a
*throwaway copy* of the database, never the real one. It builds, through the
real services, every state the guides show:

* Northfield Polytechnic: just registered; application started, some
  documents uploaded, two programmes proposed (onboarding, step 2).
* Riverside College of Technology: application submitted; two of the four
  departments have cleared it (onboarding, step 3).
* Gidan Kwano College of Technology: cleared; the institutional fee is due
  (onboarding, step 4; payments).
* Kaduna Demo Polytechnic (the Phase 4 demo institution): a draft QA request,
  a request whose QA fee is due, and a request whose SSQs are open with one
  half-completed, plus an open query from NBTE (QA requests guide).

All names are fictional demo data.
"""
from __future__ import annotations

import datetime
import traceback

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PASSWORD = "Guide-demo-2026!"


def _pdf(name):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, PDF, content_type="application/pdf")


def _step(log, label, fn, *args, **kwargs):
    try:
        out = fn(*args, **kwargs)
        log.append(f"ok    {label}")
        return out
    except Exception as exc:  # keep going: a missing state only drops a screenshot
        log.append(f"FAIL  {label}: {exc.__class__.__name__}: {exc}")
        log.append("      " + traceback.format_exc().strip().splitlines()[-2][:200])
        return None


def _lookups():
    from institutions.models import InstitutionCategory, InstitutionOwnership, InstitutionType
    from staff.models import StateOfOrigin

    def pick(model, code):
        return model.objects.filter(code=code).first() or model.objects.first()

    return {
        "private": pick(InstitutionOwnership, "private"), "state": pick(InstitutionOwnership, "state"),
        "federal": pick(InstitutionOwnership, "federal"),
        "poly": pick(InstitutionType, "polytechnic"), "cat": pick(InstitutionCategory, "polytechnic"),
        "kaduna": StateOfOrigin.objects.filter(name__iexact="KADUNA").first() or StateOfOrigin.objects.first(),
        "niger": StateOfOrigin.objects.filter(name__iexact="NIGER").first() or StateOfOrigin.objects.first(),
    }


def _register(name, code, admin, email, *, ownership, state, town, head, log):
    """Self-registration, exactly as the portal's register page does it."""
    from accounts.models import User
    from institutions import services
    from institutions.models import Institution

    inst = Institution.unscoped.filter(name=name).first()
    if inst is not None:
        return inst, User.objects.get(email=email)
    lga = (services.lgas_for(state) or [""])[0]
    inst = services.create_institution({
        "name": name, "ownership": ownership, "institution_type": _lookups()["poly"], "category": _lookups()["cat"],
        "state": state, "lga": lga, "year_established": 2025, "cac_rc_number": code, "website": "",
        "contacts": {"official_email": {"value": f"info@{code.lower()}.demo.ng"}, "phone": {"value": "08031234567"},
                     "head_of_institution": {"name": head, "value": ""}},
        "address": {"line1": "Km 4, Old Airport Road", "town": town, "lga": lga, "state": state},
        "admin": {"first_name": admin[0], "last_name": admin[1], "email": email, "job_title": "Registrar",
                  "department": "Registry"},
    }, origin="self", activate_admin=True, admin_password=PASSWORD)
    user = User.objects.get(email=email)
    user.must_change_password = False
    user.save(update_fields=["must_change_password"])
    return inst, user


def _staff(people, key):
    return people.get(key) if people else None


def build():
    """Create the guide states. Returns (logins, log): logins maps a name to an
    e-mail address the capture script signs in as."""
    from django.contrib.auth import get_user_model
    from django.db import transaction
    from django.utils import timezone

    from accreditation import case_demo
    from accreditation import qa_services as qa
    from accreditation import services as app_services
    from accreditation.models import QAType
    from config.models import ConfigEntry
    from institutions import services as inst_services
    from institutions.models import Programme, ProgrammeCategory, ProgrammeLevel
    from payments import services as pay
    from payments.models import BankAccount

    log = []
    User = get_user_model()
    L = _lookups()
    people, _creds = case_demo.ensure_accounts(force_change=False)
    for u in people.values():
        if u is not None and u.must_change_password:
            u.must_change_password = False
            u.save(update_fields=["must_change_password"])
    if not BankAccount.objects.filter(is_active=True, is_placeholder=False).exists():
        BankAccount.objects.create(bank_name="Central Bank of Nigeria (TSA)", account_name="National Board for Technical Education",
                                   account_number="0020154789", is_active=True, is_default=True)
    for key, value in (("qa.fee.qa_request.base", "50000"), ("qa.fee.qa_request.per_programme", "150000"),
                       ("qa.fee.qa_request_processing.confirmed", "true")):
        ConfigEntry.objects.update_or_create(key=key, scope="global", defaults={"value": value})
    admin_staff = User.objects.filter(is_superuser=True).first() or people.director

    # --- Onboarding: just registered -------------------------------------------------
    north, north_user = _register("Northfield Polytechnic, Zaria", "NFP", ("Aisha", "Lawal"),
                                  "registrar@northfield.demo.ng", ownership=L["private"], state=L["kaduna"],
                                  town="Zaria", head="Dr. Ibrahim Sani", log=log)
    app = _step(log, "northfield: start application", app_services.start_application, north, user=north_user)
    rows = inst_services.checklist(north)
    for row in rows[: max(1, len(rows) // 2)]:
        _step(log, f"northfield: upload {row['kind'].code}", inst_services.upload_document, north, row["kind"],
              _pdf(f"{row['kind'].code}.pdf"), user=north_user, application=app)
    nd = ProgrammeLevel.objects.filter(code="nd").first()
    cat = ProgrammeCategory.objects.first()
    for pname in ("Computer Science", "Science Laboratory Technology"):
        if not Programme.unscoped.filter(institution=north, name=pname).exists():
            _step(log, f"northfield: propose {pname}", inst_services.add_proposed_programme, north, pname, nd, cat,
                  application=app, user=north_user)

    # --- Onboarding: submitted, partly cleared ------------------------------------------
    river, river_user = _register("Riverside College of Technology, Minna", "RCT", ("Chinedu", "Okafor"),
                                  "registrar@riverside.demo.ng", ownership=L["private"], state=L["niger"],
                                  town="Minna", head="Engr. Fatima Musa", log=log)
    rapp = _step(log, "riverside: start application", app_services.start_application, river, user=river_user)
    if rapp is not None and rapp.status == "draft":
        for row in inst_services.checklist(river):
            _step(log, f"riverside: upload {row['kind'].code}", inst_services.upload_document, river, row["kind"],
                  _pdf(f"{row['kind'].code}.pdf"), user=river_user, application=rapp)
        _step(log, "riverside: submit", app_services.submit, rapp, user=river_user, declaration=True)
        rapp.refresh_from_db()
        done = 0
        for clearance in app_services.clearances_for_round(rapp):
            if clearance.status == "pending" and done < 2:
                if _step(log, f"riverside: clear {clearance.branch_code}", app_services.decide, clearance,
                         user=admin_staff, decision="clear", remark="Documents in order.") is not None:
                    done += 1

    # --- Onboarding: cleared, institutional fee due ---------------------------------------
    gidan, gidan_user = _register("Gidan Kwano College of Technology", "GKC", ("Hauwa", "Bello"),
                                  "registrar@gidankwano.demo.ng", ownership=L["private"], state=L["niger"],
                                  town="Minna", head="Prof. Sadiq Umar", log=log)
    gapp = _step(log, "gidan: start application", app_services.start_application, gidan, user=gidan_user)
    if gapp is not None and gapp.status == "draft":
        for row in inst_services.checklist(gidan):
            _step(log, f"gidan: upload {row['kind'].code}", inst_services.upload_document, gidan, row["kind"],
                  _pdf(f"{row['kind'].code}.pdf"), user=gidan_user, application=gapp)
        _step(log, "gidan: submit", app_services.submit, gapp, user=gidan_user, declaration=True)
        gapp.refresh_from_db()
        for clearance in app_services.clearances_for_round(gapp):
            if clearance.status == "pending":
                _step(log, f"gidan: clear {clearance.branch_code}", app_services.decide, clearance, user=admin_staff,
                      decision="clear", remark="Documents in order.")
    from payments.models import PaymentObligation

    for ob in PaymentObligation.unscoped.filter(institution=gidan, status="issued"):
        _step(log, "gidan: publish fee", pay.publish, ob, actor=admin_staff)

    # --- QA requests on the Phase 4 demo institution --------------------------------------
    kdp = case_demo.ensure_institution(people)
    kdp_admin = people.institution_admin
    # An academic department account, so the SSQ can be shared with it.
    from accounts.models import Role

    if not User.objects.filter(email="hod.statistics@p4demo.local").exists():
        dept_user = User(email="hod.statistics@p4demo.local", first_name="Yusuf", last_name="Abdullahi",
                         user_type="institution", institution=kdp, is_active=True, must_change_password=False,
                         role=Role.objects.filter(code="inst-academic-dept").first())
        dept_user.set_password(PASSWORD)
        dept_user.save()
    today = timezone.localdate()
    hnd = ProgrammeLevel.objects.filter(code="hnd").first()
    eng = ProgrammeCategory.objects.first()
    progs = {}
    for pname, level, status in (("Accountancy", nd, "accredited"), ("Computer Science", hnd, "accredited"),
                                 ("Mechanical Engineering Technology", nd, "accredited"),
                                 ("Statistics", nd, "approval_to_commence")):
        progs[pname] = (Programme.unscoped.filter(institution=kdp, name=pname, level=level).first()
                        or Programme.unscoped.create(institution=kdp, name=pname, level=level, category=eng,
                                                     status=status, approved_streams=1,
                                                     commencement_date=today.replace(year=today.year - 3),
                                                     expiry_date=today + datetime.timedelta(days=400)))
    types = {code: QAType.objects.get(code=code) for code in ("verification", "validation", "accreditation")}
    # This runs on a throwaway copy: give the types without a published SSQ the
    # reaccreditation questionnaire, so their requests can be shown.
    from accreditation.models import SSQTemplate

    source = QAType.objects.get(code="reaccreditation").ssq_template
    for qa_type in types.values():
        if qa_type.ssq_template_id is None and source is not None:
            tpl = SSQTemplate.objects.create(qa_type=qa_type, version=1, name=f"{qa_type.name} SSQ",
                                             schema=source.schema, status=SSQTemplate.PUBLISHED, is_current=True,
                                             published_at=timezone.now())
            QAType.objects.filter(pk=qa_type.pk).update(ssq_template=tpl)
            qa_type.refresh_from_db()
    # A draft (started, not submitted).
    _step(log, "kdp: draft request", qa.create_request, kdp, types["verification"], [progs["Accountancy"]],
          user=kdp_admin)
    # Submitted, fee due.
    due = _step(log, "kdp: request with fee due", qa.create_request, kdp, types["validation"],
                [progs["Computer Science"], progs["Mechanical Engineering Technology"]], user=kdp_admin)
    if due is not None:
        _step(log, "kdp: submit (fee due)", qa.submit, due, user=kdp_admin, declaration=True)
        for ob in qa.open_obligations(due):
            if ob.status == "issued":
                _step(log, "kdp: publish QA fee", pay.publish, ob, actor=people.finance_director)
    # Fee settled, SSQ half done, a query raised.
    open_req = _step(log, "kdp: request with SSQs open", qa.create_request, kdp, types["accreditation"],
                     [progs["Statistics"]], user=kdp_admin)
    if open_req is not None:
        _step(log, "kdp: submit (SSQs)", qa.submit, open_req, user=kdp_admin, declaration=True)
        open_req.refresh_from_db()
        _step(log, "kdp: waive fee", case_demo._waive, open_req, people)
        open_req.refresh_from_db()
        ssqs = qa.request_ssqs(open_req)
        log.append(f"      SSQs open: {len(ssqs)}; request status {open_req.status}")
        if ssqs:
            ssq = ssqs[0]
            # Realistic answers for the first sections (the rest stay empty, so
            # the guide can show what "still to do" looks like).
            known = {
                "programme_name": "ND Statistics", "programme_level": "National Diploma",
                "department_name": "Department of Mathematics and Statistics",
                "head_of_department": "Mr. Yusuf Abdullahi", "approved_streams": 1, "running_streams": 1,
                "graduates_last_3": 214,
                "siwes": "Students spend four months with the Kaduna State Bureau of Statistics and partner banks; "
                         "the SIWES coordinator visits each placement twice.",
                "enrolment": [{"session": "2023/2024", "year_one": "86", "year_two": "79"},
                              {"session": "2024/2025", "year_one": "92", "year_two": "84"},
                              {"session": "2025/2026", "year_one": "95", "year_two": "88"}],
            }
            fields = ssq.template.field_map()
            values = {k: v for k, v in known.items() if k in fields}
            _step(log, "kdp: fill part of the SSQ", qa.save_fields, ssq, values, user=kdp_admin)
        _step(log, "kdp: raise query", qa.raise_query, open_req, user=admin_staff,
              title="Confirm the number of full-time lecturers",
              body="Your SSQ lists 12 lecturers for ND Statistics. Please upload the staff nominal roll "
                   "for the department and confirm how many are full-time.", due_days=14)
    logins = {"new": north_user.email, "cleared": gidan_user.email, "partial": river_user.email,
              "qa": kdp_admin.email}
    return logins, log
