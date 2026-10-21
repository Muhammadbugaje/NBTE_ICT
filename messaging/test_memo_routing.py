"""Memo routing — scenario tests for the approval chain.

Covers the problems reported on 16 Oct 2026: an optional Line Head step with
nobody in it must be skipped; the department director must be found (role,
department head, staff-record department, nominal-roll designation); a memo
waiting on a step nobody holds must move on when the step is fixed, made
optional or deleted; links in notifications must open the memo (or explain
that it was deleted).
"""
from datetime import date

import pytest
from django.urls import reverse

from accounts.models import Role, User
from messaging import memos as memo_service
from messaging import routing
from messaging.models import Memo, MemoApproval, MemoRoute, MemoRouteRule, MemoRouteStep
from notifications.models import Notification
from staff.models import Department, Designation, Office, Staff

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- fixtures
@pytest.fixture
def roles():
    from django.core.management import call_command
    call_command("seed_roles", "--sync", verbosity=0)
    es, _ = Role.objects.get_or_create(code="executive-secretary",
                                       defaults={"name": "Executive Secretary", "rank": 999})
    return {r.code: r for r in Role.objects.all()}


@pytest.fixture
def org(roles):
    ict = Department.objects.create(name="ICT", code="ict-t")
    fin = Department.objects.create(name="Finance", code="fin-t")

    def mk(email, role, dept, **kw):
        u = User.objects.create_user(email, "x-pass-12345", first_name=email.split("@")[0].title(),
                                     last_name="T", role=roles.get(role) if role else None,
                                     department=dept, **kw)
        u.must_change_password = False
        u.save()
        return u
    return {
        "ict": ict, "fin": fin, "roles": roles,
        "es": mk("es@x.org", "executive-secretary", None),
        "fin_director": mk("dfin@x.org", "director", fin),
        "fin_staff": mk("fstaff@x.org", "staff", fin),
        "ict_staff": mk("istaff@x.org", "officer", ict),
        "admin": mk("admin@x.org", None, None, is_superuser=True, is_staff=True),
        "mk": mk,
    }


def staff_to_es_route(*, line_optional=True, director_optional=False, director_kind="director"):
    route = MemoRoute.objects.create(name="Staff to ES", code="staff-to-es-t")
    line = MemoRouteStep.objects.create(route=route, position=1, name="Line Head",
                                        approver_kind="division_head", is_optional=line_optional)
    director = MemoRouteStep.objects.create(route=route, position=2, name="Director",
                                            approver_kind=director_kind, is_optional=director_optional)
    MemoRouteRule.objects.create(name="Any → specific person", route=route, target_kind="specific_user",
                                 priority=10)
    return route, line, director


def send(sender, target, **kw):
    memo = memo_service.create_memo(sender=sender, subject=kw.pop("subject", "Request for approval"),
                                    body="Please approve.", visibility=Memo.SPECIFIC_USER,
                                    target_user=target, **kw)
    memo_service.publish_memo(memo, sender)
    memo.refresh_from_db()
    return memo


# --------------------------------------------------------------------------- skip rules
def test_optional_line_head_with_nobody_is_skipped_and_recorded(org):
    route, line, director = staff_to_es_route()
    memo = send(org["fin_staff"], org["es"])
    assert memo.status == Memo.AWAITING
    assert routing.current_step(memo) == director
    assert routing.actor_for_memo(memo) == org["fin_director"]          # Finance director found
    skip = memo.approvals.get(decision=MemoApproval.SKIP)
    assert skip.step == line and skip.actor is None and "office head or division head" in skip.comment
    rows = routing.chain_view(memo)
    assert [r["state"] for r in rows] == ["skipped", "current"]
    assert Notification.objects.filter(recipient=org["fin_director"], url=f"/messages/memos/{memo.pk}/").exists()


def test_line_head_found_from_office_head_and_division_head(org):
    route, line, director = staff_to_es_route()
    head = org["mk"]("dh@x.org", "division-head", org["fin"])
    memo = send(org["fin_staff"], org["es"])
    assert routing.actor_for_memo(memo) == head                        # division head in department
    office = Office.objects.create(name="Budget Unit", head=org["fin_director"].staff_profile)
    s = org["fin_staff"].staff_profile
    s.office = office
    s.save()
    assert routing.find_line_head(org["fin_staff"]) == org["fin_director"]   # office head first


@pytest.mark.parametrize("how", ["role", "department_head", "staff_department", "designation"])
def test_director_detected_every_way(org, how):
    route, line, director = staff_to_es_route()
    dept = org["ict"]
    if how == "role":
        expected = org["mk"]("dict@x.org", "director", dept)
    elif how == "department_head":
        expected = org["mk"]("head@x.org", "staff", None)
        dept.head = expected
        dept.save()
    elif how == "staff_department":
        expected = org["mk"]("dict2@x.org", "director", None)      # account has no department …
        s = expected.staff_profile
        s.department = dept                                         # … but the staff record does
        s.save()
        expected.department = None
        expected.save(update_fields=["department"])
    else:
        expected = org["mk"]("dict3@x.org", "staff", dept)          # role "Staff", post DIRECTOR
        s = expected.staff_profile
        s.designation = Designation.objects.create(name="DIRECTOR", rank=90)
        s.save()
    assert routing.find_director(dept) == expected
    memo = send(org["ict_staff"], org["es"])
    assert routing.actor_for_memo(memo) == expected


def test_sender_without_account_department_uses_staff_record(org):
    route, line, director = staff_to_es_route()
    u = org["mk"]("nodept@x.org", "staff", None)
    s = u.staff_profile
    s.department = org["fin"]
    s.save()
    u.department = None
    u.save(update_fields=["department"])
    memo = send(u, org["es"])
    assert routing.actor_for_memo(memo) == org["fin_director"]


def test_self_and_junior_and_same_rank(org):
    route, line, director = staff_to_es_route(director_optional=True)
    # Director sends: the director step would be self-approval → skipped → delivered.
    memo = send(org["fin_director"], org["es"])
    assert memo.status == Memo.PUBLISHED
    assert "no self-approval" in memo.approvals.get(step=director).comment
    # Same rank skips by default; "keep" makes the peer approve.
    peer = org["mk"]("dfin2@x.org", "director", org["ict"])
    m2 = send(org["fin_director"], peer)
    assert m2.status == Memo.PUBLISHED
    route.same_rank_behaviour = MemoRoute.SAME_RANK_KEEP
    route.save()
    other = org["mk"]("dict@x.org", "director", org["fin"])
    org["fin"].head = other
    org["fin"].save()
    # 18 Oct 2026 (directorate routing): a director never waits for "the
    # sender's director", even when another director-rank person holds that
    # post and the route keeps same-rank steps. This used to park the memo on
    # the co-director — the "director's memo gets stuck" report.
    m3 = send(org["fin_director"], org["es"])
    assert m3.status == Memo.PUBLISHED
    assert "already at director level" in m3.approvals.get(step=director).comment
    # "Keep" still applies below director level: a deputy's memo goes to the
    # department's director (senior) as before.
    deputy = org["mk"]("dep2@x.org", "deputy-director", org["fin"])
    m4 = send(deputy, org["es"])
    assert routing.actor_for_memo(m4) == other


# --------------------------------------------------------------------------- parked memos
def test_required_step_with_nobody_parks_then_moves_when_fixed(org):
    route, line, director = staff_to_es_route()
    memo = send(org["ict_staff"], org["es"])                       # ICT has no director yet
    assert memo.status == Memo.AWAITING and routing.is_parked(memo)
    assert "No director in ICT" in routing.describe_missing(director, memo)
    assert Notification.objects.filter(recipient=org["admin"], title__contains="No one matches").exists()
    # The parked memo shows in the admin's awaiting list, not in everyone's.
    assert memo in memo_service.awaiting_memos_for(org["admin"])
    assert memo not in memo_service.awaiting_memos_for(org["fin_director"])
    # Give ICT a director → re-check → the memo moves to them.
    d = org["mk"]("dict@x.org", "director", org["ict"])
    assert routing.reconcile_memo(memo) is False                  # already actionable now: nothing to move
    assert routing.actor_for_memo(memo) == d


def test_parked_step_made_optional_then_skipped(org):
    route, line, director = staff_to_es_route()
    memo = send(org["ict_staff"], org["es"])
    assert routing.is_parked(memo)
    director.is_optional = True
    director.save()
    assert routing.reconcile_memo(memo) is True
    memo.refresh_from_db()
    assert memo.status == Memo.PUBLISHED


def test_parked_step_deleted_then_memo_moves_on(org):
    route, line, director = staff_to_es_route()
    es_step = MemoRouteStep.objects.create(route=route, position=3, name="ES", approver_kind="es")
    memo = send(org["ict_staff"], org["es"])
    assert routing.current_step(memo) == director
    director.delete()                                             # used to be blocked / leave it stuck
    memo.refresh_from_db()
    assert routing.current_step(memo) is None
    assert routing.reconcile_memo(memo) is True
    memo.refresh_from_db()
    assert routing.current_step(memo) == es_step and routing.actor_for_memo(memo) == org["es"]


def test_step_with_recorded_decisions_can_be_deleted(org):
    route, line, director = staff_to_es_route()
    memo = send(org["fin_staff"], org["es"])
    routing.approve_step(memo, org["fin_director"], "ok")
    director.delete()                                             # approvals keep the step's name
    a = MemoApproval.objects.get(memo=memo, decision=MemoApproval.APPROVE)
    assert a.step is None and a.step_name == "Director" and "Director" in str(a)


def test_detail_page_rechecks_and_admin_can_act(client, org):
    route, line, director = staff_to_es_route()
    memo = send(org["ict_staff"], org["es"])
    client.force_login(org["admin"])
    page = client.get(reverse("messaging:memo_detail", args=[memo.pk]))
    html = page.content.decode()
    assert "Nobody can act on this step yet" in html and "Re-check routing" in html
    assert "No director in ICT" in html
    resp = client.post(reverse("messaging:memo_approve", args=[memo.pk]), {"comment": "covering"})
    assert resp.status_code == 302
    memo.refresh_from_db()
    assert memo.status == Memo.PUBLISHED
    assert "administrator" in memo.approvals.get(decision=MemoApproval.APPROVE).comment


def test_recheck_button(client, org):
    route, line, director = staff_to_es_route()
    memo = send(org["ict_staff"], org["es"])
    director.is_optional = True
    director.save()
    client.force_login(org["admin"])
    resp = client.post(reverse("messaging:memo_recheck", args=[memo.pk]), follow=True)
    memo.refresh_from_db()
    assert memo.status == Memo.PUBLISHED and resp.status_code == 200
    client.force_login(org["ict_staff"])
    assert client.post(reverse("messaging:memo_recheck", args=[memo.pk])).status_code in (403, 404)


# --------------------------------------------------------------------------- return / resubmit / reject
def test_return_resubmit_walks_fresh_and_page_shows_current_pass(org):
    route, line, director = staff_to_es_route()
    memo = send(org["fin_staff"], org["es"])
    routing.return_to_sender(memo, org["fin_director"], "Fix the figures")
    memo.refresh_from_db()
    assert memo.status == Memo.RETURNED
    routing.resubmit_memo(memo, org["fin_staff"], body="Figures fixed.")
    memo.refresh_from_db()
    states = [r["state"] for r in routing.chain_view(memo)]
    assert states == ["skipped", "current"]
    routing.approve_step(memo, org["fin_director"])
    memo.refresh_from_db()
    assert memo.status == Memo.PUBLISHED
    assert memo.memo_recipients.filter(user=org["es"]).exists()


def test_reject_needs_comment(org):
    from django.core.exceptions import ValidationError
    route, line, director = staff_to_es_route()
    memo = send(org["fin_staff"], org["es"])
    with pytest.raises(ValidationError):
        routing.reject_memo(memo, org["fin_director"], "")
    routing.reject_memo(memo, org["fin_director"], "Not now")
    memo.refresh_from_db()
    assert memo.status == Memo.REJECTED


def test_only_the_acting_user_can_approve(org):
    from django.core.exceptions import PermissionDenied
    route, line, director = staff_to_es_route()
    memo = send(org["fin_staff"], org["es"])
    with pytest.raises(PermissionDenied):
        routing.approve_step(memo, org["ict_staff"])


# --------------------------------------------------------------------------- rules
def test_rules_pick_route_by_target_role_including_designation(org):
    to_es = MemoRoute.objects.create(name="To ES", code="to-es")
    MemoRouteStep.objects.create(route=to_es, position=1, name="Director", approver_kind="director",
                                 is_optional=True)
    to_dir = MemoRoute.objects.create(name="To director", code="to-dir")
    MemoRouteStep.objects.create(route=to_dir, position=1, name="Line head", approver_kind="division_head",
                                 is_optional=True)
    MemoRouteRule.objects.create(name="→ ES", route=to_es, target_kind="specific_user",
                                 target_role_codes=["executive-secretary"], priority=100)
    MemoRouteRule.objects.create(name="→ director", route=to_dir, target_kind="specific_user",
                                 target_role_codes=["director"], priority=90)
    m1 = memo_service.create_memo(sender=org["fin_staff"], subject="To ES", body="x x x",
                                  visibility=Memo.SPECIFIC_USER, target_user=org["es"])
    assert routing.route_for_memo(m1) == to_es
    # A director by post (designation) though their role is "Staff".
    d = org["mk"]("post@x.org", "staff", org["ict"])
    s = d.staff_profile
    s.designation = Designation.objects.create(name="DIRECTOR", rank=90)
    s.save()
    m2 = memo_service.create_memo(sender=org["ict_staff"], subject="To dir", body="x x x",
                                  visibility=Memo.SPECIFIC_USER, target_user=d)
    assert routing.route_for_memo(m2) == to_dir


def test_draft_shows_expected_chain(org):
    staff_to_es_route()
    memo = memo_service.create_memo(sender=org["fin_staff"], subject="Draft", body="x x x",
                                    visibility=Memo.SPECIFIC_USER, target_user=org["es"])
    states = [(r["step"].name, r["state"]) for r in routing.chain_view(memo)]
    assert states == [("Line Head", "will_skip"), ("Director", "pending")]


def test_awaiting_list_urgent_first(org):
    staff_to_es_route()
    normal = send(org["fin_staff"], org["es"], subject="Normal one")
    urgent = send(org["fin_staff"], org["es"], subject="Urgent one", priority=Memo.URGENT)
    assert memo_service.awaiting_memos_for(org["fin_director"])[:2] == [urgent, normal]


# --------------------------------------------------------------------------- links
def test_old_notification_links_and_deleted_memos(client, org):
    staff_to_es_route()
    memo = send(org["fin_staff"], org["es"])
    client.force_login(org["fin_director"])
    old = client.get(f"/messaging/memos/{memo.pk}/")
    assert old.status_code == 302 and old["Location"] == f"/messages/memos/{memo.pk}/"
    assert client.get(old["Location"]).status_code == 200
    pk = memo.pk
    memo_service.delete_memo(memo, org["admin"])
    gone = client.get(reverse("messaging:memo_detail", args=[pk]), follow=True)
    assert gone.status_code == 200
    assert gone.redirect_chain[-1][0] == reverse("messaging:memo_list")
    assert "no longer available" in gone.content.decode()


def test_memo_someone_cannot_see_is_still_404(client, org):
    staff_to_es_route()
    memo = send(org["fin_staff"], org["es"])
    client.force_login(org["ict_staff"])
    assert client.get(reverse("messaging:memo_detail", args=[memo.pk])).status_code == 404


# --------------------------------------------------------------------------- seed_memo_demo
def test_seed_memo_demo_repairs_shipped_routes_and_sets_priorities(org):
    from django.core.management import call_command
    route = MemoRoute.objects.create(name="Staff to ES", code="staff-to-es")
    MemoRouteStep.objects.create(route=route, position=1, name="Line Head", approver_kind="deputy",
                                 is_optional=True)
    MemoRouteStep.objects.create(route=route, position=2, name="Director Approval",
                                 approver_kind="director", is_optional=True)
    MemoRouteStep.objects.create(route=route, position=3, name="ES Approval", approver_kind="es")
    MemoRouteRule.objects.create(name="Staff memo to ES", route=route, target_kind="specific_user",
                                 priority=100)
    call_command("seed_memo_demo", "--routes-only", verbosity=0)
    steps = list(route.steps.order_by("position"))
    assert steps[0].approver_kind == "division_head" and steps[0].is_optional
    assert steps[1].approver_kind == "director" and not steps[1].is_optional
    assert MemoRouteRule.objects.get(name="Staff memo to ES").target_role_codes == ["executive-secretary"]
    # Memo to the ES uses Staff to ES; memo to a colleague uses the general fallback.
    to_es = send(org["fin_staff"], org["es"])
    assert to_es.route == route and routing.actor_for_memo(to_es) == org["fin_director"]
    to_peer = send(org["fin_staff"], org["ict_staff"])
    assert to_peer.route.code == "general-memo" and to_peer.status == Memo.PUBLISHED
    # Director → ES goes straight out.
    assert send(org["fin_director"], org["es"]).status == Memo.PUBLISHED
    assert org["roles"]["director"].permissions.filter(codename="send_memo_broadcast").exists()
    # Running it twice changes nothing.
    n = MemoRouteRule.objects.count()
    call_command("seed_memo_demo", "--routes-only", verbosity=0)
    assert MemoRouteRule.objects.count() == n
