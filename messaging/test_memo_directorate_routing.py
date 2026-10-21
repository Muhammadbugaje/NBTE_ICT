"""Directorate memo routing — every scenario from the 18 Oct 2026 brief.

Each test sends a real memo through the engine, lets whoever it waits on
approve it, and checks the exact path (who approved, in order) and the result.

Organisation used (built per test):

    Alpha   director, deputy director, line head (division head), 2 officers
    Bravo   director, deputy director, line head, 2 officers
    Charlie 2 officers only          (no director, no deputy, no line head)
    Delta   director + 1 officer     (no deputy, no line head)
    ES      the Executive Secretary (no department)

Policy installed with `python manage.py seed_memo_routes`.
"""
import io

import pytest
from django.core.management import call_command

from accounts.models import Role, User
from messaging import memos as memo_service
from messaging import routing
from messaging.models import Memo, MemoApproval, MemoRoute, MemoRouteRule
from staff.models import Department, Designation, Staff

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- organisation
@pytest.fixture
def org():
    call_command("seed_roles", "--sync", verbosity=0)
    Role.objects.get_or_create(code="executive-secretary", defaults={"name": "Executive Secretary", "rank": 999})
    call_command("seed_memo_routes", stdout=io.StringIO())
    roles = {r.code: r for r in Role.objects.all()}
    # Department circulars need the existing "send broadcast memos" permission
    # (seed_memo_demo gives it to directors, deputies and the ES).
    from django.contrib.auth.models import Permission
    broadcast = Permission.objects.get(codename="send_memo_broadcast")
    for code in ("director", "deputy-director", "executive-secretary"):
        roles[code].permissions.add(broadcast)
    depts = {k: Department.objects.create(name=n, code=f"t-{k}") for k, n in
             (("a", "Alpha"), ("b", "Bravo"), ("c", "Charlie"), ("d", "Delta"))}

    def mk(key, role, dept, **kw):
        user = User.objects.create_user(f"{key}@t.org", "x-pass-12345", first_name=key.replace(".", " ").title(),
                                        last_name="T", role=roles.get(role) if role else None,
                                        department=depts.get(dept) if dept else None, **kw)
        user.must_change_password = False
        user.save()
        return user

    people = {"es": mk("es", "executive-secretary", None)}
    for d in ("a", "b"):
        people[f"{d}.dir"] = mk(f"{d}.dir", "director", d)
        people[f"{d}.dep"] = mk(f"{d}.dep", "deputy-director", d)
        people[f"{d}.lh"] = mk(f"{d}.lh", "division-head", d)
        people[f"{d}.off"] = mk(f"{d}.off", "officer", d)
        people[f"{d}.off2"] = mk(f"{d}.off2", "officer", d)
    people["c.off"] = mk("c.off", "officer", "c")
    people["c.off2"] = mk("c.off2", "officer", "c")
    people["d.dir"] = mk("d.dir", "director", "d")
    people["d.off"] = mk("d.off", "officer", "d")
    people["admin"] = mk("admin", None, None, is_superuser=True, is_staff=True)
    people["_depts"] = depts
    people["_mk"] = mk
    return people


# --------------------------------------------------------------------------- helpers
def send(sender, *, to=None, people=None, dept=None, subject="Routing scenario"):
    if dept is not None:
        kind = Memo.DEPARTMENT
    elif people is not None:
        kind = Memo.INDIVIDUALS
    else:
        kind = Memo.SPECIFIC_USER
    memo = memo_service.create_memo(sender=sender, subject=subject, body="Scenario body.", visibility=kind,
                                    target_user=to, department=dept, recipients=people or None)
    memo_service.publish_memo(memo, sender)
    memo.refresh_from_db()
    return memo


def walk(memo, limit=10):
    """Approve as whoever the memo waits on, until it is delivered or nobody
    can act. Returns (approver keys in order, final memo)."""
    path = []
    for _ in range(limit):
        memo.refresh_from_db()
        if memo.status != Memo.AWAITING:
            break
        actor = routing.actor_for_memo(memo)
        if actor is None:
            break
        path.append(actor.email.split("@")[0])
        routing.approve_step(memo, actor, "ok")
    memo.refresh_from_db()
    return path, memo


def recipients_of(memo):
    from messaging.models import MemoRecipient
    return sorted(e.split("@")[0] for e in MemoRecipient.objects.filter(memo=memo)
                  .values_list("user__email", flat=True))


def skips(memo):
    return {a.step_name: a.comment for a in memo.approvals.filter(decision=MemoApproval.SKIP)}


# =========================================================================== directors
def test_director_to_staff_in_own_department_is_delivered_directly(org):
    memo = send(org["a.dir"], to=org["a.off"])
    assert memo.status == Memo.PUBLISHED and memo.route.code == "dr-direct"
    assert recipients_of(memo) == ["a.off"]


def test_director_to_whole_own_department_is_delivered_directly(org):
    memo = send(org["a.dir"], dept=org["_depts"]["a"])
    assert memo.status == Memo.PUBLISHED and memo.route.code == "dr-direct"
    assert recipients_of(memo) == ["a.dep", "a.lh", "a.off", "a.off2"]


def test_director_to_a_list_in_own_department_is_delivered_directly(org):
    memo = send(org["a.dir"], people=[org["a.off"], org["a.lh"]])
    assert memo.status == Memo.PUBLISHED and recipients_of(memo) == ["a.lh", "a.off"]


def test_director_to_another_director_is_delivered_directly(org):
    memo = send(org["a.dir"], to=org["b.dir"])
    assert memo.status == Memo.PUBLISHED and memo.route.code == "dr-direct"
    assert recipients_of(memo) == ["b.dir"]


def test_director_to_staff_in_another_directorate_goes_through_that_director(org):
    memo = send(org["a.dir"], to=org["b.off"])
    assert memo.status == Memo.AWAITING and routing.actor_for_memo(memo) == org["b.dir"]
    assert recipients_of(memo) == []                       # nothing delivered before approval
    path, memo = walk(memo)
    assert path == ["b.dir"] and memo.status == Memo.PUBLISHED
    assert recipients_of(memo) == ["b.off"]


def test_director_circular_to_another_directorate_goes_through_that_director(org):
    path, memo = walk(send(org["a.dir"], dept=org["_depts"]["b"]))
    assert path == ["b.dir"] and memo.status == Memo.PUBLISHED
    assert recipients_of(memo) == ["b.dep", "b.dir", "b.lh", "b.off", "b.off2"]


def test_director_to_mixed_list_goes_through_the_other_director(org):
    path, memo = walk(send(org["a.dir"], people=[org["a.off"], org["b.off"]]))
    assert path == ["b.dir"] and recipients_of(memo) == ["a.off", "b.off"]


def test_director_never_stuck_behind_a_co_director(org):
    """The reported bug: another director-rank person in the same department
    (department head on record, second Director account) used to hold the
    "sender's director" step, so the director's memo waited forever."""
    co = org["_mk"]("a.codir", "director", "a")
    org["_depts"]["a"].head = co
    org["_depts"]["a"].save()
    for memo in (send(org["a.dir"], to=org["a.off"]), send(org["a.dir"], dept=org["_depts"]["a"]),
                 send(org["a.dir"], to=org["b.dir"]), send(org["a.dir"], to=org["es"])):
        memo.refresh_from_db()
        assert memo.status == Memo.PUBLISHED, memo.route.code if memo.route_id else "-"
    # Even on a route that explicitly asks for the sender's director (and keeps same rank):
    path, memo = walk(send(org["a.dir"], to=org["b.off"]))
    assert path == ["b.dir"]


def test_director_by_designation_or_department_head_counts_as_director(org):
    # Role "officer", but the department's appointed head → treated as director.
    head = org["_mk"]("c.head", "officer", "c")
    org["_depts"]["c"].head = head
    org["_depts"]["c"].save()
    assert send(head, to=org["c.off"]).status == Memo.PUBLISHED
    assert send(head, to=org["b.dir"]).status == Memo.PUBLISHED
    path, memo = walk(send(head, to=org["b.off"]))
    assert path == ["b.dir"]
    # Role "staff", nominal-roll designation DIRECTOR → treated as director.
    des = Designation.objects.create(name="DIRECTOR (TEST)", code="dir-test", rank=95)
    by_post = org["_mk"]("d.post", "staff", "d")
    Staff.objects.filter(user=by_post).update(designation=des) or Staff.objects.create(
        user=by_post, first_name="D", last_name="Post", department=org["_depts"]["d"], designation=des)
    by_post.refresh_from_db()
    assert "director" in routing.role_codes_of(by_post)
    assert send(by_post, to=org["a.dir"]).status == Memo.PUBLISHED


# =========================================================================== staff → own director
def test_officer_to_own_director_goes_line_head_then_deputy(org):
    path, memo = walk(send(org["a.off"], to=org["a.dir"]))
    assert path == ["a.lh", "a.dep"] and memo.status == Memo.PUBLISHED
    assert recipients_of(memo) == ["a.dir"]
    assert "is the recipient" in skips(memo)["Director"]


def test_line_head_to_own_director_skips_line_head_step(org):
    path, memo = walk(send(org["a.lh"], to=org["a.dir"]))
    assert path == ["a.dep"] and recipients_of(memo) == ["a.dir"]
    assert "line head level" in skips(memo)["Line Head"] or "no self-approval" in skips(memo)["Line Head"]


def test_deputy_to_own_director_goes_straight_to_the_director(org):
    path, memo = walk(send(org["a.dep"], to=org["a.dir"]))
    assert path == [] and memo.status == Memo.PUBLISHED and recipients_of(memo) == ["a.dir"]


def test_officer_without_line_head_or_deputy_goes_straight_to_director(org):
    path, memo = walk(send(org["d.off"], to=org["d.dir"]))
    assert path == [] and memo.status == Memo.PUBLISHED
    reasons = skips(memo)
    assert "Optional step" in reasons["Line Head"] and "Optional step" in reasons["Deputy Director"]


def test_officer_to_own_deputy_goes_through_line_head(org):
    path, memo = walk(send(org["a.off"], to=org["a.dep"]))
    assert path == ["a.lh"] and recipients_of(memo) == ["a.dep"]


# =========================================================================== staff → another directorate
def test_officer_to_another_director_goes_up_own_line_first(org):
    memo = send(org["a.off"], to=org["b.dir"])
    assert memo.route.code == "dr-up-and-across"
    path, memo = walk(memo)
    assert path == ["a.lh", "a.dep", "a.dir"] and memo.status == Memo.PUBLISHED
    assert recipients_of(memo) == ["b.dir"]
    assert "is the recipient" in skips(memo)["Receiving Director"]


def test_deputy_to_another_director_goes_through_own_director(org):
    path, memo = walk(send(org["a.dep"], to=org["b.dir"]))
    assert path == ["a.dir"] and recipients_of(memo) == ["b.dir"]


def test_officer_to_staff_in_another_directorate_follows_both_lines(org):
    path, memo = walk(send(org["a.off"], to=org["b.off"]))
    assert path == ["a.lh", "a.dep", "a.dir", "b.dir"] and memo.status == Memo.PUBLISHED
    assert recipients_of(memo) == ["b.off"]


def test_officer_circular_to_another_directorate(org):
    from django.contrib.auth.models import Permission
    from django.core.exceptions import PermissionDenied
    with pytest.raises(PermissionDenied):                  # officers cannot broadcast by default
        send(org["a.off"], dept=org["_depts"]["b"])
    officer = org["a.off"]
    officer.user_permissions.add(Permission.objects.get(codename="send_memo_broadcast"))
    officer = User.objects.get(pk=officer.pk)              # fresh permission cache
    path, memo = walk(send(officer, dept=org["_depts"]["b"]))
    assert path == ["a.lh", "a.dep", "a.dir", "b.dir"]
    assert recipients_of(memo) == ["b.dep", "b.dir", "b.lh", "b.off", "b.off2"]


def test_small_department_officer_to_other_directorate(org):
    path, memo = walk(send(org["d.off"], to=org["b.off"]))
    assert path == ["d.dir", "b.dir"] and recipients_of(memo) == ["b.off"]


def test_list_across_several_directorates_goes_through_own_director(org):
    path, memo = walk(send(org["a.off"], people=[org["b.off"], org["d.off"]]))
    assert path == ["a.lh", "a.dep", "a.dir"] and memo.status == Memo.PUBLISHED
    assert recipients_of(memo) == ["b.off", "d.off"]
    assert "several other departments" in skips(memo)["Receiving Director"]


# =========================================================================== staff → own department
def test_officer_to_colleague_goes_through_line_head_only(org):
    memo = send(org["a.off"], to=org["a.off2"])
    assert memo.route.code == "dr-within-department"
    path, memo = walk(memo)
    assert path == ["a.lh"] and recipients_of(memo) == ["a.off2"]


def test_line_head_to_colleague_is_delivered_directly(org):
    path, memo = walk(send(org["a.lh"], to=org["a.off"]))
    assert path == [] and memo.status == Memo.PUBLISHED


def test_officer_without_line_head_to_colleague_is_delivered_directly(org):
    path, memo = walk(send(org["c.off"], to=org["c.off2"]))
    assert path == [] and memo.status == Memo.PUBLISHED


# =========================================================================== ES
def test_es_memos_are_delivered_directly(org):
    for memo in (send(org["es"], to=org["b.off"]), send(org["es"], dept=org["_depts"]["a"]),
                 send(org["es"], people=[org["a.off"], org["b.off"]])):
        memo.refresh_from_db()
        assert memo.status == Memo.PUBLISHED


def test_officer_to_es_goes_up_own_line(org):
    path, memo = walk(send(org["a.off"], to=org["es"]))
    assert path == ["a.lh", "a.dep", "a.dir"] and recipients_of(memo) == ["es"]


# =========================================================================== nobody there
def test_missing_receiving_director_waits_alerts_and_moves_on_when_set(org):
    memo = send(org["d.dir"], to=org["c.off"])             # Charlie has no director
    assert memo.status == Memo.AWAITING and routing.is_parked(memo)
    assert "No director in Charlie" in routing.describe_missing(routing.current_step(memo), memo)
    from notifications.models import Notification
    assert Notification.objects.filter(recipient=org["admin"], title__contains="No one matches").exists()
    new_director = org["_mk"]("c.dir", "director", "c")    # appointing one re-checks waiting memos
    routing.reconcile_parked()
    memo.refresh_from_db()
    assert routing.actor_for_memo(memo) == new_director
    path, memo = walk(memo)
    assert path == ["c.dir"] and recipients_of(memo) == ["c.off"]


def test_missing_own_director_waits_then_moves_on(org):
    memo = send(org["c.off"], to=org["a.off"])             # Charlie officer, no director at home
    assert routing.is_parked(memo)
    org["_depts"]["c"].head = org["c.off2"]
    org["_depts"]["c"].save()
    routing.reconcile_parked()
    path, memo = walk(memo)
    assert path == ["c.off2", "a.dir"] and recipients_of(memo) == ["a.off"]


# =========================================================================== return / reject / preview
def test_receiving_director_can_return_and_the_resubmission_walks_the_whole_way_again(org):
    memo = send(org["a.off"], to=org["b.off"])
    for who in ("a.lh", "a.dep", "a.dir"):
        routing.approve_step(memo, org[who], "ok")
    memo.refresh_from_db()
    assert routing.actor_for_memo(memo) == org["b.dir"]
    routing.return_to_sender(memo, org["b.dir"], "Add the budget line")
    memo.refresh_from_db()
    assert memo.status == Memo.RETURNED
    routing.resubmit_memo(memo, org["a.off"], body="Revised with the budget line.")
    path, memo = walk(memo)
    assert path == ["a.lh", "a.dep", "a.dir", "b.dir"] and memo.status == Memo.PUBLISHED


def test_receiving_director_can_reject(org):
    memo = send(org["a.dir"], to=org["b.off"])
    routing.reject_memo(memo, org["b.dir"], "Not for my staff")
    memo.refresh_from_db()
    assert memo.status == Memo.REJECTED and recipients_of(memo) == []


def test_only_the_waiting_person_can_act(org):
    memo = send(org["a.off"], to=org["b.off"])
    from django.core.exceptions import PermissionDenied
    for wrong in ("b.dir", "a.dir", "a.dep", "b.off"):
        assert not routing.can_act_on_memo(memo, org[wrong])
        with pytest.raises(PermissionDenied):
            routing.approve_step(memo, org[wrong])
    assert routing.can_act_on_memo(memo, org["a.lh"])


def test_draft_preview_shows_the_expected_chain(org):
    memo = memo_service.create_memo(sender=org["a.off"], subject="Draft scenario", body="Body",
                                    visibility=Memo.SPECIFIC_USER, target_user=org["b.off"])
    states = [(row["step"].name, row["state"]) for row in routing.chain_view(memo)]
    assert states == [("Line Head", "pending"), ("Deputy Director", "pending"),
                      ("Director", "pending"), ("Receiving Director", "pending")]
    memo2 = memo_service.create_memo(sender=org["a.off"], subject="Draft scenario", body="Body",
                                     visibility=Memo.SPECIFIC_USER, target_user=org["a.dir"])
    states = {row["step"].name: row["state"] for row in routing.chain_view(memo2)}
    assert states["Director"] == "will_skip" and states["Receiving Director"] == "will_skip"


def test_memo_page_shows_the_receiving_director_step(client, org):
    from django.urls import reverse
    memo = send(org["a.dir"], to=org["b.off"])
    client.force_login(org["b.dir"])
    html = client.get(reverse("messaging:memo_detail", args=[memo.pk])).content.decode()
    assert "Receiving Director" in html


# =========================================================================== seed command
def test_seed_memo_routes_is_idempotent_and_removable(org):
    out = io.StringIO()
    call_command("seed_memo_routes", stdout=out)
    assert "0 row(s) changed" in out.getvalue()
    n_rules = MemoRouteRule.objects.filter(name__startswith="Directorate:").count()
    assert n_rules == 16 and MemoRoute.objects.filter(code__startswith="dr-").count() == 4
    call_command("seed_memo_routes", "--remove", stdout=io.StringIO())
    assert not MemoRouteRule.objects.filter(name__startswith="Directorate:").exists()
    assert not MemoRoute.objects.filter(code__startswith="dr-").exists()


def test_policy_works_alongside_seed_memo_demo_routes(org):
    """Installed on top of the routes from seed_memo_demo, the directorate
    rules still decide every person-to-person case, and the ES rules still
    deliver directly."""
    call_command("seed_memo_demo", "--routes-only", stdout=io.StringIO())
    assert walk(send(org["a.off"], to=org["b.off"]))[0] == ["a.lh", "a.dep", "a.dir", "b.dir"]
    assert walk(send(org["a.dir"], to=org["b.off"]))[0] == ["b.dir"]
    assert send(org["a.dir"], to=org["a.off"]).status == Memo.PUBLISHED
    assert walk(send(org["a.off"], to=org["a.dir"]))[0] == ["a.lh", "a.dep"]


def test_department_relation_on_rules():
    from messaging.models import MemoRouteRule
    assert {c for c, _ in MemoRouteRule.RELATION_CHOICES} == {"any", "same", "other"}


def test_scenario_playground_walks_every_case_and_cleans_up():
    call_command("seed_roles", "--sync", verbosity=0)
    out = io.StringIO()
    call_command("seed_memo_scenarios", "--walk", stdout=out)
    text = out.getvalue()
    assert "every case followed its expected path" in text, text
    assert text.count("✔") == 14 and "✘" not in text
    call_command("seed_memo_scenarios", "--remove", stdout=io.StringIO())
    assert not User.objects.filter(email__endswith="@memo-scenarios.test").exists()
    assert not Memo.objects.filter(subject__startswith="[Scenario] ").exists()
