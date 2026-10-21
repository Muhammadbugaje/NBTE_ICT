"""Phase 3 — the dynamic SSQ, the curriculum catalogue, queries and the
template builder (PHASES.md Phase 3; Master Plan §5.4).

Schema validation, the immutable Submission, prefill from ACCEPTED documents
only, catalogue binding (quantities only, R-22), the academic-department
return path, versioning (Requirement 16), autosave and queries.
"""
import copy

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client

from qa_core.tests.phase2_fixtures import pdf_upload
from qa_core.tests.phase3_fixtures import accepted_doc, complete_ssq, make_request, waive_fee

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Schema validator
# ---------------------------------------------------------------------------
def test_seeded_templates_are_valid(qa_seed):
    from accreditation import ssq as engine
    from accreditation.models import SSQTemplate

    templates = list(SSQTemplate.objects.filter(status="published"))
    assert {t.qa_type.code for t in templates} == {"reaccreditation", "institutional_administration"}
    for t in templates:
        assert engine.validate_schema(t.schema) == []


@pytest.mark.parametrize("mutate, message", [
    (lambda s: s.update(sections=[]), "at least one section"),
    (lambda s: s["sections"][0].update(key="Bad Key"), "lower-case"),
    (lambda s: s["sections"][0]["fields"].append(dict(s["sections"][0]["fields"][0])), "already used"),
    (lambda s: s["sections"][0]["fields"][0].update(type="slider"), "unknown type"),
    (lambda s: s["sections"][0]["fields"].append({"key": "c1", "type": "choice", "label": "C", "options": ["only"]}),
     "two options"),
    (lambda s: s["sections"][0]["fields"].append({"key": "cat", "type": "catalogue", "label": "Kit"}),
     "catalogue field needs"),
    (lambda s: s["sections"][0]["fields"].append({"key": "p1", "type": "text", "label": "P",
                                                  "prefill_from": "institution.shoe_size"}), "unknown prefill"),
    (lambda s: s["sections"][0]["fields"].append({"key": "p2", "type": "text", "label": "P",
                                                  "prefill_from": "document.cac"}), "needs a file field"),
    (lambda s: s["sections"][0]["fields"].append({"key": "t1", "type": "table", "label": "T"}), "needs columns"),
    (lambda s: s["sections"][0]["fields"][0].update(colour="red"), "unknown keys"),
])
def test_schema_validator_rejects(qa_seed, mutate, message):
    from accreditation import ssq as engine
    from accreditation.models import QAType

    schema = copy.deepcopy(QAType.objects.get(code="reaccreditation").ssq_template.schema)
    mutate(schema)
    errors = engine.validate_schema(schema)
    assert any(message.lower() in e.lower() for e in errors), errors


def test_invalid_template_cannot_be_published(p3):
    from accreditation import builder
    from accreditation.models import QAType

    draft = builder.new_draft(QAType.objects.get(code="reaccreditation"), user=p3.p2.director)
    builder.save_draft(draft, user=p3.p2.director, schema={"sections": []})
    with pytest.raises(ValidationError):
        builder.publish(draft, user=p3.p2.director)


def test_published_template_is_never_edited_new_version_retires_old(p3, paid_request):
    from accreditation import builder, qa_services as svc
    from accreditation.models import QAType, SSQTemplate

    qa_type = QAType.objects.get(code="reaccreditation")
    v1 = qa_type.ssq_template
    with pytest.raises(ValidationError):
        builder.save_draft(v1, user=p3.p2.director, schema={"sections": []})
    v2 = builder.new_draft(qa_type, user=p3.p2.director)
    builder.add_section(v2, user=p3.p2.director, key="ict", title="ICT")
    builder.upsert_field(v2, user=p3.p2.director, section_key="ict",
                         field=builder.field_from_form({"key": "internet", "type": "yes_no", "label": "Internet?"}))
    builder.publish(v2, user=p3.p2.director)
    v1.refresh_from_db()
    qa_type.refresh_from_db()
    assert v1.status == SSQTemplate.RETIRED and not v1.is_current and qa_type.ssq_template == v2
    # SSQs already open keep version 1.
    assert all(s.template_id == v1.pk for s in svc.request_ssqs(paid_request))


def test_officer_without_publish_permission_cannot_publish(p3):
    from accreditation import builder
    from accreditation.models import QAType

    draft = builder.new_draft(QAType.objects.get(code="reaccreditation"), user=p3.p2.director)
    with pytest.raises(PermissionDenied):
        builder.publish(draft, user=p3.p2.officer)


# ---------------------------------------------------------------------------
# Immutable submission
# ---------------------------------------------------------------------------
def test_submit_creates_an_immutable_submission(p3, paid_request):
    from accreditation import qa_services as svc
    from accreditation.models import Submission

    ssq = complete_ssq(svc.request_ssqs(paid_request)[0], p3.admin)
    svc.submit_ssq(ssq, user=p3.admin, declaration=True)
    snap = Submission.unscoped.get(ssq=ssq)
    assert snap.version == 1 and snap.template_version == ssq.template.version
    assert snap.data_snapshot["department_name"] == "Computing" and snap.verify()
    snap.data_snapshot["department_name"] = "Tampered"
    with pytest.raises(PermissionError):
        snap.save()
    with pytest.raises(PermissionError):
        snap.delete()
    assert Submission.unscoped.get(pk=snap.pk).data_snapshot["department_name"] == "Computing"
    # The SSQ itself is locked after submission.
    ssq.refresh_from_db()
    assert ssq.status == "submitted" and svc.editable_sections(ssq, p3.admin) == []
    with pytest.raises(PermissionDenied):
        svc.save_fields(ssq, {"department_name": "Changed"}, user=p3.admin)


def test_snapshot_freezes_catalogue_names_and_quantities(p3, paid_request):
    from accreditation import builder, qa_services as svc
    from accreditation.models import CatalogueItem, Submission

    ssq = svc.request_ssqs(paid_request)[0]   # Computer Science
    item = CatalogueItem.objects.get(programme_name="Computer Science", name="Desktop computer")
    complete_ssq(ssq, p3.admin)
    svc.save_fields(ssq, {"equipment": {str(item.pk): "35"}}, user=p3.admin)
    svc.submit_ssq(ssq, user=p3.admin, declaration=True)
    builder.revise_item(item, user=p3.p2.director, required_quantity="60", name="Desktop computer (i5)")
    snap = Submission.unscoped.get(ssq=ssq)
    rows = next(f for s in snap.rendered_snapshot for f in s["fields"] if f["key"] == "equipment")["rows"]
    row = next(r for r in rows if r["item_id"] == item.pk)
    assert row["name"] == "Desktop computer" and row["required"] == "40.00" and row["quantity"] == "35"
    assert row["short"] is True


def test_only_submit_roles_can_submit(p3, paid_request):
    from accreditation import qa_services as svc

    ssq = complete_ssq(svc.request_ssqs(paid_request)[0], p3.admin)
    with pytest.raises(PermissionDenied):
        svc.submit_ssq(ssq, user=p3.dept, declaration=True)
    svc.submit_ssq(ssq, user=p3.ceo, declaration=True)


def test_incomplete_ssq_cannot_be_submitted(p3, paid_request):
    from accreditation import qa_services as svc

    with pytest.raises(ValidationError, match="required"):
        svc.submit_ssq(svc.request_ssqs(paid_request)[0], user=p3.admin, declaration=True)


# ---------------------------------------------------------------------------
# Prefill and evidence: accepted documents only
# ---------------------------------------------------------------------------
def _admin_ssq(p3, django_capture_on_commit_callbacks):
    from accreditation import qa_services as svc
    from institutions.models import Programme, ProgrammeLevel

    admin_prog = Programme.unscoped.create(institution=p3.inst, name="Institutional Administration",
                                           level=ProgrammeLevel.objects.get(code="nd"), status="accredited",
                                           is_institutional_admin=True, expiry_date=p3.cs.expiry_date)
    req = make_request(p3, [admin_prog], type_code="institutional_administration")
    svc.submit(req, user=p3.admin, declaration=True)
    with django_capture_on_commit_callbacks(execute=True):
        waive_fee(p3, req)
    return svc.request_ssqs(req)[0]


def test_prefill_uses_only_accepted_documents(p3, django_capture_on_commit_callbacks):
    from accreditation.models import Evidence

    organogram = accepted_doc(p3.inst, "organogram")                       # accepted → prefilled
    accepted_doc(p3.inst, "physical-master-plan", status="submitted")      # awaiting review → never
    accepted_doc(p3.inst, "cac", status="returned")                        # returned → never
    old = accepted_doc(p3.inst, "c-of-o")                                   # accepted v1 …
    old.status = "superseded"
    old.save(update_fields=["status"])                                      # … then superseded → never
    ssq = _admin_ssq(p3, django_capture_on_commit_callbacks)
    reused = {e.field_key: e.reused_from for e in Evidence.unscoped.filter(ssq=ssq)}
    fields_by_kind = {f.get("document_kind"): f["key"] for f in ssq.template.field_map().values()
                      if f["type"] == "file"}
    assert reused == {fields_by_kind["organogram"]: organogram}
    assert ssq.data["institution_name"] == "Alpha Polytechnic"
    assert "institution_name" in ssq.meta["prefilled"]


def test_reuse_refuses_documents_that_are_not_accepted(p3, django_capture_on_commit_callbacks):
    from accreditation import qa_services as svc

    ssq = _admin_ssq(p3, django_capture_on_commit_callbacks)
    draft = accepted_doc(p3.inst, "cac", status="submitted")
    field_key = next(f["key"] for f in ssq.template.field_map().values() if f.get("document_kind") == "cac")
    with pytest.raises(ValidationError, match="accepted"):
        svc.reuse_document(ssq, field_key, draft, user=p3.admin)
    other = accepted_doc(p3.other, "cac")
    with pytest.raises(PermissionDenied):
        svc.reuse_document(ssq, field_key, other, user=p3.admin)
    assert [d.status for d in svc.reusable_documents(ssq, ssq.template.field_map()[field_key])] == []


def test_upload_evidence(p3, paid_request):
    from accreditation import qa_services as svc

    ssq = svc.request_ssqs(paid_request)[0]
    ev = svc.attach_upload(ssq, "corrective_evidence", pdf_upload("plan.pdf"), user=p3.admin)
    assert ev.sha256 and ev.original_name == "plan.pdf" and svc.evidence_counts(ssq) == {"corrective_evidence": 1}
    with pytest.raises(ValidationError):
        svc.attach_upload(ssq, "department_name", pdf_upload("x.pdf"), user=p3.admin)


# ---------------------------------------------------------------------------
# Catalogue binding (R-22): quantities only
# ---------------------------------------------------------------------------
def test_catalogue_fields_take_quantities_only(p3, paid_request):
    from accreditation import qa_services as svc
    from accreditation.models import CatalogueItem

    ssq = svc.request_ssqs(paid_request)[0]   # Computer Science
    item = CatalogueItem.objects.get(programme_name="Computer Science", name="Desktop computer")
    eee_item = CatalogueItem.objects.get(name="Digital oscilloscope")    # another programme's item
    errors = svc.save_fields(ssq, {"equipment": {str(item.pk): "38", str(eee_item.pk): "5", "name": "Laptop",
                                                 "999999": "4"}}, user=p3.admin)
    ssq.refresh_from_db()
    assert errors == {} and ssq.data["equipment"] == {str(item.pk): "38"}
    errors = svc.save_fields(ssq, {"equipment": {str(item.pk): "-1"}}, user=p3.admin)
    assert "equipment" in errors
    ssq.refresh_from_db()
    assert ssq.data["equipment"] == {str(item.pk): "38"}  # the bad value never replaced the good one


def test_catalogue_names_cannot_be_posted_through_the_portal(p3, paid_request, portal_host):
    from accreditation import qa_services as svc
    from accreditation.models import CatalogueItem

    ssq = svc.request_ssqs(paid_request)[0]
    item = CatalogueItem.objects.get(programme_name="Computer Science", name="Desktop computer")
    c = Client(HTTP_HOST=portal_host)
    c.force_login(p3.admin)
    r = c.post(f"/qa-requests/{paid_request.pk}/ssq/{ssq.pk}/save/",
               {"section": "facilities", f"f__equipment__{item.pk}": "12", "f__equipment__name": "Gaming PC",
                f"f__equipment__{item.pk}__name": "Gaming PC"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 200
    ssq.refresh_from_db()
    assert ssq.data["equipment"] == {str(item.pk): "12"}
    item.refresh_from_db()
    assert item.name == "Desktop computer"
    page = c.get(f"/qa-requests/{paid_request.pk}/ssq/{ssq.pk}/?section=facilities").content.decode()
    assert f'name="f__equipment__{item.pk}"' in page and "Desktop computer" in page
    assert 'name="f__equipment__name' not in page


def test_catalogue_revision_writes_a_new_version(p3):
    from accreditation import builder
    from accreditation.models import CatalogueItem

    item = CatalogueItem.objects.get(programme_name="Computer Science", name="Laser printer")
    new = builder.revise_item(item, user=p3.p2.director, required_quantity="3")
    item.refresh_from_db()
    assert not item.is_active and new.version == 2 and new.required_quantity == 3
    with pytest.raises(PermissionDenied):
        builder.revise_item(new, user=p3.p2.officer, required_quantity="4")


# ---------------------------------------------------------------------------
# Department return path
# ---------------------------------------------------------------------------
def test_department_return_path(p3, paid_request):
    from accreditation import qa_services as svc

    ssq = svc.request_ssqs(paid_request)[0]
    assert svc.editable_sections(ssq, p3.dept) == []      # nothing until the institution sends it
    svc.return_to_department(ssq, user=p3.admin, department_user=p3.dept, sections=["staff", "students"],
                             remark="Please complete staff and students")
    ssq.refresh_from_db()
    assert ssq.status == "returned_to_department"
    assert set(svc.editable_sections(ssq, p3.dept)) == {"staff", "students"}
    assert svc.editable_sections(ssq, p3.admin) == []     # locked for the admin meanwhile
    with pytest.raises(PermissionDenied):
        svc.save_fields(ssq, {"department_name": "x"}, user=p3.dept)   # not an assigned section
    svc.save_fields(ssq, {"graduates_last_3": "90"}, user=p3.dept)
    with pytest.raises(ValidationError):
        svc.department_submit(ssq, user=p3.dept)          # required answers in its sections missing
    errors = svc.save_fields(ssq, {"enrolment": [{"session": "2025/26", "year_one": "30"}], "siwes": "Done",
                                   "academic_staff": [{"name": "A", "status": "Full-time"},
                                                      {"name": "B", "status": "Part-time"}]}, user=p3.dept)
    assert errors == {}
    svc.department_submit(ssq, user=p3.dept)
    ssq.refresh_from_db()
    assert ssq.status == "submitted_by_department" and svc.editable_sections(ssq, p3.dept) == []
    complete_ssq(ssq, p3.admin)
    svc.submit_ssq(ssq, user=p3.admin, declaration=True)
    ssq.refresh_from_db()
    assert ssq.status == "submitted"


def test_admin_can_recall_from_department(p3, paid_request):
    from accreditation import qa_services as svc

    ssq = svc.request_ssqs(paid_request)[0]
    svc.return_to_department(ssq, user=p3.admin, department_user=p3.dept, sections=["staff"], remark="Staff")
    svc.recall_from_department(ssq, user=p3.admin)
    ssq.refresh_from_db()
    assert ssq.status == "draft" and "staff" in svc.editable_sections(ssq, p3.admin)


# ---------------------------------------------------------------------------
# Review and versioning (Requirement 16)
# ---------------------------------------------------------------------------
def test_return_then_revise_supersedes_never_overwrites(p3, paid_request, django_capture_on_commit_callbacks):
    from accreditation import qa_services as svc
    from accreditation.models import SSQ, Evidence, Submission

    v1 = complete_ssq(svc.request_ssqs(paid_request)[0], p3.admin)
    svc.attach_upload(v1, "corrective_evidence", pdf_upload("plan.pdf"), user=p3.admin)
    svc.submit_ssq(v1, user=p3.admin, declaration=True)
    with pytest.raises(PermissionDenied):
        svc.return_ssq(v1, user=p3.other_admin, remark="x")
    with django_capture_on_commit_callbacks(execute=True):
        svc.return_ssq(v1, user=p3.p2.officer, remark="Give the staff qualifications in full")
    v1.refresh_from_db()
    assert v1.status == "returned"
    v2 = svc.revise_ssq(v1, user=p3.admin)
    v1.refresh_from_db()
    assert v2.version == 2 and v2.supersedes == v1 and v2.is_current and not v1.is_current
    assert v2.data == v1.data and Evidence.unscoped.filter(ssq=v2).count() == 1
    svc.save_fields(v2, {"department_name": "Department of Computing"}, user=p3.admin)
    v1.refresh_from_db()
    assert v1.data["department_name"] == "Computing"                 # old version untouched
    assert Submission.unscoped.get(ssq=v1).data_snapshot["department_name"] == "Computing"
    with pytest.raises(ValidationError):
        svc.revise_ssq(v1, user=p3.admin)                            # only the current returned version
    assert SSQ.unscoped.filter(qa_request_programme=v1.qa_request_programme).count() == 2
    svc.submit_ssq(v2, user=p3.admin, declaration=True)
    svc.accept_ssq(v2, user=p3.p2.officer)
    v2.refresh_from_db()
    assert v2.status == "accepted"


def test_reviewer_needs_permission_and_scope(p3, paid_request):
    from accounts.models import User
    from staff.models import Department

    from accreditation import qa_services as svc

    ssq = complete_ssq(svc.request_ssqs(paid_request)[0], p3.admin)
    svc.submit_ssq(ssq, user=p3.admin, declaration=True)
    outsider = User.objects.get(pk=p3.p2.officer2.pk)
    outsider.department = Department.objects.get(code="vtsd")
    outsider.save(update_fields=["department"])
    outsider = User.objects.get(pk=outsider.pk)
    ok, why = svc.can_review(ssq, outsider)
    assert not ok and "another department" in why
    with pytest.raises(PermissionDenied):
        svc.accept_ssq(ssq, user=p3.p2.fin_officer)


# ---------------------------------------------------------------------------
# Autosave through the portal (a reload shows the saved answers)
# ---------------------------------------------------------------------------
def test_autosave_persists_across_reload(p3, paid_request, portal_host):
    from accreditation import qa_services as svc

    ssq = svc.request_ssqs(paid_request)[0]
    c = Client(HTTP_HOST=portal_host)
    c.force_login(p3.admin)
    url = f"/qa-requests/{paid_request.pk}/ssq/{ssq.pk}/"
    r = c.post(url + "save/", {"section": "programme", "f__department_name": "Computing Dept",
                               "f__running_streams": "two"}, HTTP_HX_REQUEST="true",
               HTTP_HX_TARGET="ssq-save-status")
    assert r.status_code == 200 and b"is-partial" in r.content       # one answer saved, one rejected
    assert b'id="ssq-err-running_streams"' in r.content and b"hx-swap-oob" in r.content
    page = c.get(url + "?section=programme").content.decode()          # the "refresh"
    assert 'value="Computing Dept"' in page
    assert "Prefilled from your record" in page                        # untouched prefilled answers keep their badge
    r = c.get(url + "?section=staff", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="ssq-section")
    assert r.status_code == 200 and b"<html" not in r.content          # a section swap, not a page


def test_department_user_cannot_autosave_other_sections(p3, paid_request, portal_host):
    from accreditation import qa_services as svc

    ssq = svc.request_ssqs(paid_request)[0]
    svc.return_to_department(ssq, user=p3.admin, department_user=p3.dept, sections=["staff"], remark="Staff")
    c = Client(HTTP_HOST=portal_host)
    c.force_login(p3.dept)
    r = c.post(f"/qa-requests/{paid_request.pk}/ssq/{ssq.pk}/save/",
               {"section": "programme", "f__department_name": "Hijack"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 403
    ssq.refresh_from_db()
    assert ssq.data.get("department_name") != "Hijack"


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def test_query_raise_respond_close(p3, paid_request, portal_host, django_capture_on_commit_callbacks):
    from accreditation import qa_services as svc
    from accreditation.models import Query, Response
    from notifications.models import Notification

    ssq = svc.request_ssqs(paid_request)[0]
    with pytest.raises(PermissionDenied):
        svc.raise_query(ssq, user=p3.p2.fin_officer, title="Hi", body="No permission")
    with django_capture_on_commit_callbacks(execute=True):
        query = svc.raise_query(ssq, user=p3.p2.officer, title="Staff list", body="Send the staff payroll.")
    assert query.reference.startswith("NBTE-QRY-") and query.qa_request == paid_request
    assert Notification.objects.filter(recipient=p3.admin).exists()
    c = Client(HTTP_HOST=portal_host)
    c.force_login(p3.other_admin)
    assert c.post(f"/queries/{query.pk}/", {"body": "not mine"}).status_code == 404
    c.force_login(p3.admin)
    with django_capture_on_commit_callbacks(execute=True):
        r = c.post(f"/queries/{query.pk}/", {"body": "Payroll attached.", "file": pdf_upload("payroll.pdf")},
                   HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-main")
    assert r.status_code == 200
    query.refresh_from_db()
    assert query.status == "answered" and Response.unscoped.get(query=query).original_name == "payroll.pdf"
    assert Notification.objects.filter(recipient=p3.p2.officer).exists()
    svc.close_query(query, user=p3.p2.officer, remark="Thanks")
    query.refresh_from_db()
    assert query.status == Query.CLOSED
    with pytest.raises(ValidationError):
        svc.respond(query, user=p3.admin, body="late")


def test_internal_query_form_and_review_screens(p3, paid_request):
    from accreditation import qa_services as svc

    ssq = complete_ssq(svc.request_ssqs(paid_request)[0], p3.admin)
    svc.submit_ssq(ssq, user=p3.admin, declaration=True)
    c = Client(HTTP_HOST="localhost")
    c.force_login(p3.p2.officer)
    r = c.get("/qa/requests/ssq/?status=submitted")
    assert r.status_code == 200 and b"Computer Science" in r.content
    r = c.get(f"/qa/requests/ssq/{ssq.pk}/")
    assert r.status_code == 200 and b"Immutable submission snapshot" in r.content and b"Verified" in r.content
    r = c.get(f"/qa/requests/query/ssq/{ssq.pk}/", HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and b"Raise a query" in r.content
    r = c.post(f"/qa/requests/query/ssq/{ssq.pk}/", {"title": "Labs", "body": "Photos of the labs please.",
                                                      "due_days": "7"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and b"sent" in r.content
    r = c.post(f"/qa/requests/ssq/{ssq.pk}/decide/", {"decision": "return", "remark": ""}, HTTP_HX_REQUEST="true")
    assert r.status_code == 422   # a return needs a remark
