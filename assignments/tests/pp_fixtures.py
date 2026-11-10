"""Phase P fixtures: one institution seen from every seat (G1.e matrix).

Institution A (handled by DPAP) has an onboarding application with
clearances of DPAP, Legal and Finance, an SSQ, a QA case owned by DPAP, a
complaint routed to Legal, timeline rows of each audience, and a document.
Institution B (VTSD) has nothing to do with most of the cast.
"""
import pytest
from django.core.files.base import ContentFile


class NS:
    pass


def set_config(key, value):
    from config.models import ConfigEntry
    entry = ConfigEntry.objects.filter(key=key, scope="global").first()
    if entry is None:
        entry = ConfigEntry(key=key, scope="global", value_type="str")
    entry.value = value
    entry.save()


def assignment_mode():
    set_config("qa.privacy.mode", "assignment")


@pytest.fixture
def pp(qa_seed, make_user, make_institution):
    from accreditation.models import (
        Application, DepartmentalClearance, QACase, QARequest, QARequestProgramme, QAType, SSQ, SSQTemplate,
    )
    from complaints.models import Complaint, ComplaintLayer
    from institutions.models import DocumentKind, InstitutionDocument, Programme
    from qa_core import timeline
    from staff.models import Department

    ns = NS()
    D = {c: Department.objects.get(code=c) for c in ("dpap", "legal", "finance", "ict", "vtsd", "es-office")}
    ns.D = D
    ns.a = make_institution("AAA", "dpap", "Alpha Polytechnic")
    ns.b = make_institution("BBB", "vtsd", "Bravo Technical College")
    mk = make_user
    ns.superuser = mk("root@t.local", superuser=True, dept="ict")
    ns.es = mk("es@t.local", role="executive-secretary", dept="es-office")
    ns.ict_dir = mk("ictdir@t.local", role="director", dept="ict")
    ns.supervisor = mk("supervisor@t.local", role="officer", dept="ict")
    ns.ict_other = mk("ictother@t.local", role="officer", dept="ict")
    ns.dpap_dir = mk("dpapdir@t.local", role="director", dept="dpap")
    ns.dpap_officer = mk("dpapoff@t.local", role="officer", dept="dpap")
    ns.dpap_idle = mk("dpapidle@t.local", role="officer", dept="dpap")
    ns.legal_dir = mk("legaldir@t.local", role="director", dept="legal")
    ns.legal_officer = mk("legaloff@t.local", role="officer", dept="legal")
    ns.vtsd_dir = mk("vtsddir@t.local", role="director", dept="vtsd")
    ns.inst_user = mk("admin@aaa.local", role="inst-admin", user_type="institution", institution=ns.a)
    for dept, user in (("dpap", ns.dpap_dir), ("legal", ns.legal_dir), ("ict", ns.ict_dir), ("vtsd", ns.vtsd_dir)):
        Department.objects.filter(code=dept).update(director=user)

    ns.app = Application.unscoped.create(reference="T/APP/1", institution=ns.a, status="clearance", round=1)
    ns.cl = {}
    for code in ("dpap", "legal", "finance"):
        ns.cl[code] = DepartmentalClearance.unscoped.create(
            application=ns.app, department=D[code], branch_code=f"{code}_review", round=1)
    qa_type = QAType.objects.filter(ssq_template__isnull=False).first() or QAType.objects.first()
    ns.req = QARequest.unscoped.create(reference="T/QAR/1", institution=ns.a, qa_type=qa_type, status="in_workflow")
    prog = Programme.unscoped.create(institution=ns.a, name="Computer Science", status="accredited")
    ns.row = QARequestProgramme.unscoped.create(qa_request=ns.req, programme=prog)
    template = qa_type.ssq_template or SSQTemplate.objects.first()
    ns.ssq = SSQ.unscoped.create(qa_request_programme=ns.row, template=template, status="submitted") \
        if template else None
    ns.case = QACase.unscoped.create(reference="T/QAC/1", qa_request=ns.req, institution=ns.a,
                                     owning_department=D["dpap"], status="generate_cost")
    layer = ComplaintLayer.objects.first()
    ns.complaint = Complaint.unscoped.create(
        reference="T/CMP/1", layer=layer, raised_by=ns.inst_user, institution=ns.a, subject="Late refund",
        body="Body", status="in_progress", current_department=D["legal"])
    kind = DocumentKind.objects.first()
    ns.doc = InstitutionDocument(institution=ns.a, kind=kind, status="submitted")
    ns.doc.file = ContentFile(b"%PDF-1.4\n%%EOF\n", name="doc.pdf")
    ns.doc.save()
    ns.ev_public = timeline.record(ns.a, "t_public", public_text="Your application was received.",
                                   visibility="public")
    ns.ev_dpap = timeline.record(ns.a, "t_dpap", internal_text="DPAP reviewed the site plan.",
                                 department=D["dpap"])
    ns.ev_legal = timeline.record(ns.a, "t_legal", internal_text="Legal reviewed the deed.", department=D["legal"])
    ns.ev_full = timeline.record(ns.a, "t_full", internal_text="Case-wide note.", audience="full")
    return ns


def supervise(pp):
    from assignments import work
    from assignments.models import WorkAssignment
    return work.ensure(pp.a, WorkAssignment.OVERSIGHT_ICT, pp.supervisor, department=pp.D["ict"],
                       assigned_by=pp.ict_dir)


def assign_doc(pp, user=None):
    from assignments import work
    from assignments.models import WorkAssignment
    return work.ensure(pp.a, WorkAssignment.DOCUMENT_ANALYSIS, user or pp.dpap_officer, subject=pp.doc,
                       department=pp.D["dpap"], assigned_by=pp.dpap_dir)
