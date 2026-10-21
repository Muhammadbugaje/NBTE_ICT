"""§6 New Institution / TVET Support actions through the Phase 1 engine:
initiate → pending (nothing changes) → confirm by a second officer → applied;
plus reject, stale and expiry for every action, and the special cases
(Update Category department transfer, Update Ownership document re-check)."""
import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from qa_core import actions
from qa_core.models import PendingAction

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def people(p2, make_user):
    from accounts.models import User
    from qa_core.departments import qa_department

    def mk(email, role, dept):
        u = make_user(email, role=role)
        u.department = qa_department(dept)
        u.save(update_fields=["department"])
        return User.objects.get(pk=u.pk)

    p2.director2 = mk("p2.director2@t.local", "director", "dpap")
    p2.es = mk("p2.es@t.local", "executive-secretary", "dpap")
    return p2


@pytest.fixture
def approved(people, lookups):
    """An approved private polytechnic handled by DPAP, with a programme,
    an open draft request and a portal user."""
    from institutions import services
    from institutions.models import (
        Institution, InstitutionRequest, InstitutionRequestType, Programme, ProgrammeLevel,
    )

    inst = services.create_institution({
        "name": "Kaduna Polytechnic of Trades", "ownership": lookups.private, "institution_type": lookups.poly,
        "category": lookups.poly_cat, "state": lookups.kaduna, "lga": services.lgas_for(lookups.kaduna)[0],
        "contacts": {"official_email": {"value": "a@kpt.edu.ng"}, "phone": {"value": "0801"},
                     "head_of_institution": {"name": "Head", "value": ""}},
        "address": {"line1": "1 Road", "town": "Kaduna"},
        "admin": {"first_name": "A", "last_name": "B", "email": "admin@kpt.edu.ng"},
    }, actor=people.officer)
    pa = actions.initiate("institutions.approve_institution", actor=people.director, target=inst,
                          payload={"reason": "approved"}, reason="approved")
    assert actions.confirm(pa, people.es).status == PendingAction.APPLIED
    inst.refresh_from_db()
    assert inst.status == "approved" and inst.handling_department.code == "dpap"
    level = ProgrammeLevel.objects.exclude(code="admin").first()
    Programme.unscoped.create(institution=inst, name="ND Welding", level=level, status="accredited")
    InstitutionRequest.unscoped.create(institution=inst, request_type=InstitutionRequestType.objects.get(code="qa"),
                                       status="draft", title="Draft QA request")
    return Institution.unscoped.get(pk=inst.pk)


def _other_state(lookups):
    from staff.models import StateOfOrigin

    return StateOfOrigin.objects.get(name="KANO")


# action → (initiator attr, confirmer attr, payload builder(inst, lookups), check(inst_before, inst_after))
def _payload_state(inst, lookups):
    from institutions import services

    st = _other_state(lookups)
    return {"state_id": st.pk, "lga": services.lgas_for(st)[0], "reason": "moved"}


def _payload_drop(inst, lookups):
    from institutions.models import Programme

    return {"programme_ids": list(Programme.unscoped.filter(institution=inst).values_list("pk", flat=True)),
            "reason": "no longer offered"}


def _payload_wipe(inst, lookups):
    return {"type_codes": ["qa"], "reason": "stale drafts"}


def _payload_cancel(inst, lookups):
    from institutions.models import InstitutionRequest

    return {"request_id": InstitutionRequest.unscoped.filter(institution=inst).first().pk, "reason": "duplicate"}


def _payload_contact(inst, lookups):
    return {"kind": "phone", "value": "08099999999", "name": "", "reason": "new line"}


def _payload_address(inst, lookups):
    from institutions import services

    return {"kind": "permanent_site", "line1": "2 New Road", "town": "Kaduna", "state_id": lookups.kaduna.pk,
            "lga": services.lgas_for(lookups.kaduna)[1], "reason": "moved site"}


def _payload_category(inst, lookups):
    from institutions.models import InstitutionCategory

    return {"category_id": InstitutionCategory.objects.get(code="college-nursing").pk, "reason": "re-categorised"}


def _payload_name(inst, lookups):
    return {"name": "Kaduna Federal Polytechnic of Trades", "reason": "renamed by law"}


def _payload_ownership(inst, lookups):
    return {"ownership_id": lookups.state_own.pk, "reason": "taken over by the state"}


def _reason(inst, lookups):
    return {"reason": "per directive"}


def _check_state(before, after):
    assert after.state.name == "KANO"


def _check_drop(before, after):
    from institutions.models import Programme

    assert set(Programme.unscoped.filter(institution=after).values_list("status", flat=True)) == {"dropped"}


def _check_wipe(before, after):
    from institutions.models import InstitutionRequest

    assert set(InstitutionRequest.unscoped.filter(institution=after).values_list("status", flat=True)) == {"cancelled"}


def _check_contact(before, after):
    assert after.contact("phone").value == "08099999999"


def _check_address(before, after):
    assert after.address("permanent_site").line1 == "2 New Road"


def _check_category(before, after):
    assert after.category.code == "college-nursing"


def _check_name(before, after):
    assert after.name == "Kaduna Federal Polytechnic of Trades"


def _check_ownership(before, after):
    assert after.ownership.code == "state"


def _check_delist(before, after):
    assert after.status == "delisted" and not after.portal_enabled


def _check_suspend(before, after):
    assert after.portal_enabled is False and after.status == "approved"


ACTIONS = {
    "institutions.update_state_lga": ("officer", "director", _payload_state, _check_state),
    "institutions.drop_programmes": ("officer", "director", _payload_drop, _check_drop),
    "institutions.wipe_requests": ("officer", "director", _payload_wipe, _check_wipe),
    "institutions.cancel_institution_request": ("officer", "director", _payload_cancel, _check_wipe),
    "institutions.update_contact": ("officer", "director", _payload_contact, _check_contact),
    "institutions.update_address": ("officer", "director", _payload_address, _check_address),
    "institutions.update_category": ("officer", "director", _payload_category, _check_category),
    "institutions.update_name": ("officer", "director", _payload_name, _check_name),
    "institutions.update_ownership": ("officer", "director", _payload_ownership, _check_ownership),
    "institutions.delist": ("director", "es", _reason, _check_delist),
    "institutions.suspend_portal_access": ("director", "director2", _reason, _check_suspend),
}


def _start(people, approved, lookups, code):
    initiator, confirmer, payload_fn, check = ACTIONS[code]
    pa = actions.initiate(code, actor=getattr(people, initiator), target=approved,
                          payload=payload_fn(approved, lookups), reason="test reason")
    return pa, getattr(people, confirmer), check


def _fresh(inst):
    from institutions.models import Institution

    return Institution.unscoped.get(pk=inst.pk)


# ---------------------------------------------------------------------------
# The generic contract, for every §6 action
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code", sorted(ACTIONS))
def test_initiate_pending_confirm_applied(people, approved, lookups, code):
    before = _fresh(approved)
    pa, confirmer, check = _start(people, approved, lookups, code)
    assert pa.status == PendingAction.PENDING
    # Nothing has changed yet.
    still = _fresh(approved)
    assert (still.name, still.status, still.ownership_id, still.category_id, still.state_id, still.portal_enabled) == \
           (before.name, before.status, before.ownership_id, before.category_id, before.state_id, before.portal_enabled)
    # The initiator can never confirm their own request.
    with pytest.raises(PermissionDenied):
        actions.confirm(pa, pa.initiated_by)
    pa = actions.confirm(pa, confirmer, remark="ok")
    assert pa.status == PendingAction.APPLIED, pa.failure_detail
    check(before, _fresh(approved))


@pytest.mark.parametrize("code", sorted(ACTIONS))
def test_reject_changes_nothing(people, approved, lookups, code):
    before = _fresh(approved)
    pa, confirmer, _check = _start(people, approved, lookups, code)
    with pytest.raises(ValidationError):
        actions.reject(pa, confirmer, remark="")  # a reason is required
    pa = actions.reject(pa, confirmer, remark="Not supported by the file")
    assert pa.status == PendingAction.REJECTED
    after = _fresh(approved)
    assert (after.name, after.status, after.ownership_id, after.category_id, after.portal_enabled) == \
           (before.name, before.status, before.ownership_id, before.category_id, before.portal_enabled)


@pytest.mark.parametrize("code", sorted(ACTIONS))
def test_stale_fails_safely(people, approved, lookups, code):
    pa, confirmer, _check = _start(people, approved, lookups, code)
    inst = _fresh(approved)
    inst.website = "https://changed.example"
    inst.save()  # someone else changed the record meanwhile
    pa = actions.confirm(pa, confirmer)
    assert pa.status == PendingAction.FAILED
    assert "Stale" in pa.failure_detail
    assert _fresh(approved).name == "Kaduna Polytechnic of Trades"


@pytest.mark.parametrize("code", sorted(ACTIONS))
def test_expiry(people, approved, lookups, code):
    pa, confirmer, _check = _start(people, approved, lookups, code)
    PendingAction.objects.filter(pk=pa.pk).update(expires_at=timezone.now() - datetime.timedelta(minutes=1))
    actions.expire_overdue()
    pa.refresh_from_db()
    assert pa.status == PendingAction.EXPIRED
    with pytest.raises((ValidationError, PermissionDenied)):
        actions.confirm(pa, confirmer)


# ---------------------------------------------------------------------------
# Add TVET, approve, relist, restore, credentials, impersonation
# ---------------------------------------------------------------------------
def test_add_tvet_creates_proposed_institution_with_inactive_admin(people, add_tvet_payload):
    from institutions.models import Institution

    pa = actions.initiate("institutions.add_tvet", actor=people.officer, payload=add_tvet_payload(), reason="new")
    assert pa.status == PendingAction.PENDING
    assert not Institution.unscoped.filter(name="Zaria Institute of Technology").exists()
    pa = actions.confirm(pa, people.director)
    assert pa.status == PendingAction.APPLIED, pa.failure_detail
    inst = Institution.unscoped.get(name="Zaria Institute of Technology")
    assert inst.status == "proposed" and inst.handling_department.code == "ntird"
    assert inst.code == "ZIT"
    user = inst.primary_user()
    assert user.email == "registrar@zit.edu.ng" and not user.is_active and not user.has_usable_password()


def test_add_tvet_duplicate_email_rejected_at_initiate(people, add_tvet_payload, proposed_inst):
    with pytest.raises(ValidationError):
        actions.initiate("institutions.add_tvet", actor=people.officer, reason="dup",
                         payload=add_tvet_payload(name="Another Institute"))


def test_approve_moves_department(people, proposed_inst):
    pa = actions.initiate("institutions.approve_institution", actor=people.director, target=proposed_inst,
                          payload={"reason": "approved by board"}, reason="board")
    pa = actions.confirm(pa, people.es)
    assert pa.status == PendingAction.APPLIED, pa.failure_detail
    inst = _fresh(proposed_inst)
    assert inst.status == "approved" and inst.handling_department.code == "dpap"


def test_relist_after_delist(people, approved):
    pa = actions.confirm(actions.initiate("institutions.delist", actor=people.director, target=approved,
                                          payload={"reason": "x"}, reason="x"), people.es)
    assert pa.status == PendingAction.APPLIED
    pa = actions.initiate("institutions.relist", actor=people.director, target=_fresh(approved),
                          payload={"reason": "fixed"}, reason="fixed")
    pa = actions.confirm(pa, people.es)
    assert pa.status == PendingAction.APPLIED, pa.failure_detail
    inst = _fresh(approved)
    assert inst.status == "approved" and inst.portal_enabled


def test_delisted_institution_cannot_sign_in(people, approved, client, portal_host):
    from accounts.models import User

    user = User.objects.get(email="admin@kpt.edu.ng")
    user.is_active = True
    user.set_password("Test-pass-2026!")
    user.save()
    actions.confirm(actions.initiate("institutions.delist", actor=people.director, target=approved,
                                     payload={"reason": "x"}, reason="x"), people.es)
    response = client.post("/login/", {"username": user.email, "password": "Test-pass-2026!"}, HTTP_HOST=portal_host)
    assert response.status_code == 200  # stays on the form with an error
    assert "_auth_user_id" not in client.session


def test_restore_portal_access(people, approved):
    actions.confirm(actions.initiate("institutions.suspend_portal_access", actor=people.director, target=approved,
                                     payload={"reason": "x"}, reason="x"), people.director2)
    assert _fresh(approved).portal_enabled is False
    pa = actions.initiate("institutions.restore_portal_access", actor=people.director, target=_fresh(approved),
                          payload={"reason": "ok"}, reason="ok")
    pa = actions.confirm(pa, people.director2)
    assert pa.status == PendingAction.APPLIED, pa.failure_detail
    assert _fresh(approved).portal_enabled is True


def test_send_credentials_is_single_step_and_never_sends_a_password(people, proposed_inst):
    from qa_core.models import OutboundEmail

    proposed_inst.primary_user().__class__.objects.filter(pk=proposed_inst.primary_user().pk).update(is_active=False)
    pa = actions.initiate("institutions.send_login_credentials", actor=people.ntird_officer, target=proposed_inst,
                          payload={}, reason="")
    assert pa.status == PendingAction.APPLIED, pa.failure_detail
    assert proposed_inst.primary_user().is_active
    mail = next((m for m in OutboundEmail.objects.order_by("-id") if "registrar@zit.edu.ng" in m.recipients), None)
    assert mail is not None and "/password/set/" in (mail.body_text + mail.body_html)
    assert "password:" not in mail.body_text.lower()


def test_later_phase_actions_are_disabled(people, approved):
    for code in ("institutions.add_programme_to_qa", "accreditation.send_back_to_audit"):
        with pytest.raises((ValidationError, PermissionDenied)):
            actions.initiate(code, actor=people.director, target=approved, payload={}, reason="x")


def test_phase5_actions_are_live_but_refuse_an_invalid_request(people, approved):
    """Edit QA Personnel / Mapping and Override Post-Visit Personnel went live in
    Phase 5 (inspection/handlers.py); an empty payload is still refused."""
    for code in ("inspection.edit_qa_personnel", "inspection.edit_qa_mapping",
                 "inspection.override_post_visit_personnel"):
        with pytest.raises((ValidationError, PermissionDenied)):
            actions.initiate(code, actor=people.director, target=approved, payload={}, reason="x")


def test_officer_outside_scope_cannot_initiate(people, approved):
    with pytest.raises(PermissionDenied):
        actions.initiate("institutions.update_name", actor=people.ntird_officer, target=approved,
                         payload={"name": "X Poly", "reason": "x"}, reason="x")


# ---------------------------------------------------------------------------
# Update Category: department transfer; Update Ownership: document re-check
# ---------------------------------------------------------------------------
def test_update_category_transfers_department(people, approved):
    from institutions.models import InstitutionCategory

    nursing = InstitutionCategory.objects.get(code="college-nursing")
    pa = actions.initiate("institutions.update_category", actor=people.officer, target=approved,
                          payload={"category_id": nursing.pk, "reason": "x"}, reason="x")
    handler = actions.get_handler(pa.policy_id)
    preview = {row.label: row.after for row in handler.preview(approved, pa.payload)}
    assert "DEPARTMENT TRANSFER" in preview["Handling department"]
    pa = actions.confirm(pa, people.director)
    assert pa.status == PendingAction.APPLIED, pa.failure_detail
    inst = _fresh(approved)
    assert inst.handling_department.code == nursing.routing_department.code != "dpap"
    # The DPAP officer no longer sees it; the new department does.
    from institutions.models import Institution

    assert not Institution.objects.for_user(people.officer).filter(pk=inst.pk).exists()


def test_update_category_of_proposed_institution_stays_in_ntird(people, proposed_inst):
    from institutions.models import InstitutionCategory

    nursing = InstitutionCategory.objects.get(code="college-nursing")
    pa = actions.initiate("institutions.update_category", actor=people.ntird_officer, target=proposed_inst,
                          payload={"category_id": nursing.pk, "reason": "x"}, reason="x")
    pa = actions.confirm(pa, people.ntird_director)
    assert pa.status == PendingAction.APPLIED, pa.failure_detail
    assert _fresh(proposed_inst).handling_department.code == "ntird"


def test_update_ownership_re_evaluates_documents_and_holds_application(people, submitted_app, proposed_inst):
    from accreditation import services as app_services
    from institutions import services

    before = {r["kind"].code for r in services.checklist(proposed_inst) if r["mandatory"]}
    assert {"cac", "bank-guarantee"} <= before
    pa = actions.initiate("institutions.update_ownership", actor=people.ntird_officer, target=proposed_inst,
                          payload={"ownership_id": proposed_inst.ownership.__class__.objects.get(code="federal").pk,
                                   "reason": "federal takeover"}, reason="x")
    preview = {r.label: r.after for r in actions.get_handler(pa.policy_id).preview(proposed_inst, pa.payload)}
    assert "Enabling" in preview["Newly required documents"]
    pa = actions.confirm(pa, people.ntird_director)
    assert pa.status == PendingAction.APPLIED, pa.failure_detail
    inst = _fresh(proposed_inst)
    rows = {r["kind"].code: r for r in services.checklist(inst) if r["mandatory"]}
    assert "enabling-law" in rows and rows["enabling-law"]["state"] == "missing"
    assert "cac" not in rows
    submitted_app.refresh_from_db()
    assert app_services.current_application(inst) is not None
    assert services.active_hold(submitted_app) is not None
