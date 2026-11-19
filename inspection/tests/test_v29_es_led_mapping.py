"""v2.9: resource-person mapping is ES-led — the ES (or Audit) suggests, Audit
verifies, ONLY the ES approves; a mapping covers one programme or the whole
institution; the approved resource person confirms availability and submits
a report on the portal, which the case team, CQAC and the ES then see."""
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from qa_core.tests.phase5_fixtures import visit_dates


def _propose(p5, *, actor, row="eee", rp="rp_eng", offset=30):
    from inspection import services

    start, end = visit_dates(offset)
    target = getattr(p5, row) if row else None
    mapping, _pa = services.propose_mapping(p5.case, target, duty="resource_inspection",
                                            person=p5.rps[rp], actor=actor, start=start, end=end)
    mapping.refresh_from_db()
    return mapping


def _approve(p5, mapping):
    from inspection import services

    services.approve_mapping(mapping, actor=p5.people.audit_director, layer="audit")
    services.approve_mapping(mapping, actor=p5.people.es, layer="es")
    mapping.refresh_from_db()
    return mapping


def test_only_the_es_approves_and_audit_verifies_first(p5):
    from inspection import services

    people = p5.people
    mapping = _propose(p5, actor=people.es)
    assert mapping.suggested_by_role == "es" and mapping.audit_verified_at is None
    assert services.mapping_approval_state(mapping)[0] == "pending"
    # Nobody but the ES approves — not the handling Director, not Audit.
    for user in (people.director, people.audit_director, people.desk_officer):
        with pytest.raises(PermissionDenied):
            services.approve_mapping(mapping, actor=user, layer="es")
    # The ES cannot approve before Audit has verified.
    with pytest.raises(ValidationError):
        services.approve_mapping(mapping, actor=people.es, layer="es")
    # The handling Director cannot verify (the old "director" layer is Audit's now).
    with pytest.raises(PermissionDenied):
        services.approve_mapping(mapping, actor=people.director, layer="director")
    services.approve_mapping(mapping, actor=people.audit_director, layer="audit", remark="Checked")
    mapping.refresh_from_db()
    assert mapping.audit_verified_by_id == people.audit_director.pk
    assert services.mapping_approval_state(mapping)[0] == "audit_verified"
    # Not on the portal until the ES approves.
    assert mapping not in list(services.rp_engagements(p5.rps["rp_eng"]))
    services.approve_mapping(mapping, actor=people.es, layer="es")
    mapping.refresh_from_db()
    assert mapping.es_approved_by_id == people.es.pk and mapping.status == "active"
    assert mapping in list(services.rp_engagements(p5.rps["rp_eng"]))
    # Once approved, Audit can no longer change it.
    with pytest.raises(ValidationError):
        services.approve_mapping(mapping, actor=people.audit_director, layer="audit", approve=False,
                                 remark="Too late")


def test_audit_suggestion_counts_as_verified_and_es_rejection_releases(p5):
    from inspection import services

    mapping = _propose(p5, actor=p5.people.audit_director)
    assert mapping.suggested_by_role == "audit" and mapping.audit_verified_at is not None
    with pytest.raises(ValidationError):  # a reason is required
        services.approve_mapping(mapping, actor=p5.people.es, layer="es", approve=False)
    services.approve_mapping(mapping, actor=p5.people.es, layer="es", approve=False, remark="Use someone else")
    mapping.refresh_from_db()
    assert mapping.status == "released" and mapping.es_approved_at is None and mapping.es_remark


def test_mapping_view_rejects_a_non_es_approval(p5):
    from qa_core.tests.phase4_fixtures import staff_client

    mapping = _propose(p5, actor=p5.people.es)
    from inspection import services

    services.approve_mapping(mapping, actor=p5.people.audit_director, layer="audit")
    r = staff_client(p5.people.director).post(f"/qa/inspection/mappings/{mapping.pk}/decide/",
                                              {"layer": "es", "decision": "approve"})
    assert r.status_code in (403, 404)
    mapping.refresh_from_db()
    assert mapping.es_approved_at is None
    r = staff_client(p5.people.es).post(f"/qa/inspection/mappings/{mapping.pk}/decide/",
                                        {"layer": "es", "decision": "approve"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 204
    mapping.refresh_from_db()
    assert mapping.es_approved_at is not None


def test_programme_or_whole_institution_scope(p5):
    from inspection.models import QAMapping

    by_programme = _propose(p5, actor=p5.people.es)
    assert by_programme.scope == "programme" and by_programme.qa_request_programme_id == p5.eee.pk
    assert by_programme.target_label == str(p5.eee.programme)
    whole = _propose(p5, actor=p5.people.es, row=None, rp="rp_sci", offset=60)
    assert whole.scope == "institution" and whole.qa_request_programme_id is None
    assert whole.target_label == "Whole institution" and "Whole institution" in str(whole)
    with pytest.raises(IntegrityError), transaction.atomic():
        QAMapping.unscoped.filter(pk=whole.pk).update(scope="programme")
    with pytest.raises(IntegrityError), transaction.atomic():
        QAMapping.unscoped.filter(pk=by_programme.pk).update(qa_request_programme=None)


def test_resource_person_confirms_and_reports_and_the_case_team_sees_it(p5, portal_host):
    from django.test import Client

    from inspection import services
    from qa_core.tests.phase4_fixtures import staff_client

    rp = p5.rps["rp_eng"]
    mapping = _approve(p5, _propose(p5, actor=p5.people.es))
    rp.user.must_change_password = False
    rp.user.save(update_fields=["must_change_password"])
    c = Client(HTTP_HOST=portal_host)
    c.force_login(rp.user)
    page = c.get("/engagements/").content.decode()
    assert "Are you available" in page
    assert not services.findings_open(mapping)[0]  # confirm availability first
    r = c.post("/engagements/", {"action": "respond", "mapping": mapping.pk, "op": "accept"})
    assert r.status_code == 200
    mapping.refresh_from_db()
    assert mapping.rp_response == "accepted" and mapping.rp_response_at
    text = "Workshops are equipped as declared; the library needs more current titles."
    r = c.post("/engagements/", {"mapping": mapping.pk, "findings": text, "recommendation": "interim"})
    assert r.status_code == 200, r.content.decode()[:500]
    mapping.refresh_from_db()
    assert mapping.findings == text and mapping.rp_recommendation == "interim" and mapping.findings_submitted_at
    # The handling department sees the mapping, the reply and the report on the case.
    r = staff_client(p5.people.director).get(f"/qa/cases/{p5.case.pk}/?tab=overview")
    assert r.status_code == 200
    rows = {row["pk"]: row for row in r.context["rp_mappings"]}
    assert rows[mapping.pk]["rp_response"] == "accepted" and rows[mapping.pk]["report"] == text
    assert rows[mapping.pk]["approval_state"] == "es_approved"
    # Another person cannot answer for this one; a decline needs a reason and releases the booking.
    other = _approve(p5, _propose(p5, actor=p5.people.es, row=None, rp="rp_sci", offset=60))
    with pytest.raises(PermissionDenied):
        services.rp_respond(other, user=rp.user, accept=True)
    sci_user = p5.rps["rp_sci"].user
    if sci_user is not None:
        with pytest.raises(ValidationError):
            services.rp_respond(other, user=sci_user, accept=False)
        services.rp_respond(other, user=sci_user, accept=False, note="Travelling abroad")
        other.refresh_from_db()
        assert other.rp_response == "declined" and other.status == "released"
