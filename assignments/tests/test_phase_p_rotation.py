"""Phase P: rotation engine (X27–X29) and review chains (X30)."""
import datetime
from collections import Counter
from types import SimpleNamespace

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from assignments import chain, rotation
from assignments.models import WorkAssignment
from assignments.models_chain import ReviewChain, ReviewChainPolicy
from assignments.models_rotation import ConflictDeclaration, RotationDecision, RotationMember, RotationPool
from tasks.conftest import mk


@pytest.fixture
def inst(db, depts):
    from institutions.models import Institution
    return Institution.unscoped.create(code="ROT", name="Rotation Demo Poly", status="approved",
                                       handling_department=depts["ict"])


@pytest.fixture
def inst2(db, depts):
    from institutions.models import Institution
    return Institution.unscoped.create(code="ROT2", name="Second Demo Poly", status="approved",
                                       handling_department=depts["ict"])


@pytest.fixture
def analysts(people):
    return [people["officer"], people["officer2"], people["staff"]]


def _pool(depts, members, kind="document_analysis", **rules):
    pool = RotationPool.objects.create(code=rotation.pool_code(kind, depts["ict"]), kind=kind,
                                       department=depts["ict"], name=f"{kind} (t)", rules=rules)
    for u in members:
        RotationMember.objects.create(pool=pool, user=u)
    return pool


def _pick(pool, director, inst, **kw):
    s = rotation.suggest(pool, subject=inst, institution=inst, **kw)
    rotation.record_decision(s.decision, by=director, chosen=s.suggested.user)
    return s.suggested.user


# ---------------------------------------------------------------------------
# X27
# ---------------------------------------------------------------------------
def test_x27_rotation_spread_and_no_streak(people, depts, analysts, inst):
    pool = _pool(depts, analysts)
    picks = [_pick(pool, people["director"], inst) for _ in range(10)]
    counts = sorted(Counter(u.pk for u in picks).values(), reverse=True)
    assert counts == [4, 3, 3]
    assert all(a != b for a, b in zip(picks, picks[1:])), "same person twice in a row"


def test_x27_weights(people, depts, analysts, inst):
    pool = _pool(depts, analysts)
    RotationMember.objects.filter(pool=pool, user=analysts[0]).update(weight=2)
    picks = Counter(_pick(pool, people["director"], inst).pk for _ in range(30))
    heavy = picks[analysts[0].pk]
    light = [picks[analysts[1].pk], picks[analysts[2].pk]]
    assert abs(heavy - 2 * (sum(light) / 2)) <= 2, picks
    assert abs(heavy - 15) <= 1 and all(abs(x - 7.5) <= 1 for x in light), picks


def test_x27_load_and_recency_order(people, depts, analysts, inst):
    pool = _pool(depts, analysts)
    # officer holds two open analyses: the others come first
    for i in range(2):
        WorkAssignment.objects.create(institution=inst, kind="document_analysis", department=depts["ict"],
                                      assignee=analysts[0], status="in_progress", subject_id=f"d{i}")
    s = rotation.suggest(pool, subject=inst, record=False)
    assert s.eligible[-1].user == analysts[0]
    assert s.eligible[-1].open == 2 and "2 open of 5" in s.eligible[-1].summary


def test_x27_decision_logged_and_override_needs_reason(people, depts, analysts, inst):
    pool = _pool(depts, analysts)
    s = rotation.suggest(pool, subject=inst)
    d = s.decision
    assert d.suggested_user == s.suggested.user
    assert {c["user_id"] for c in d.candidates} == {u.pk for u in analysts}
    assert d.parameters["cap"] == 5
    other = s.eligible[1].user
    with pytest.raises(ValidationError):
        rotation.record_decision(d, by=people["director"], chosen=other, override_reason="")
    with pytest.raises(PermissionDenied):
        rotation.record_decision(d, by=people["officer"], chosen=other, override_reason="I want it")
    d = rotation.record_decision(d, by=people["director"], chosen=other, override_reason="Knows this institution")
    assert d.overridden and d.chosen_user == other and d.decided_by == people["director"]
    with pytest.raises(ValidationError):
        rotation.record_decision(d, by=people["director"], chosen=other, override_reason="again please")
    with pytest.raises(ValueError):
        d.override_reason = "edited"
        d.save()
    from core.models import AuditLogEntry
    assert AuditLogEntry.objects.filter(action="rotation_overridden", object_pk=str(d.pk)).exists()
    assert RotationMember.objects.get(pool=pool, user=other).last_assigned_at is not None


def test_x27_blocked_person_needs_es_override(people, depts, analysts, inst):
    pool = _pool(depts, analysts)
    ConflictDeclaration.objects.create(user=analysts[1], institution=inst, reason="Former staff")
    d = rotation.suggest(pool, subject=inst).decision
    with pytest.raises(ValidationError):
        rotation.record_decision(d, by=people["director"], chosen=analysts[1], override_reason="Only expert")
    d = rotation.record_decision(d, by=people["es"], chosen=analysts[1], override_reason="Only expert available")
    assert d.constraint_overridden and d.overridden


# ---------------------------------------------------------------------------
# X28: every constraint blocks with a reason
# ---------------------------------------------------------------------------
def _avail(leave=(), holiday=(), duty=(), free=True):
    def fn(user, a, b):
        return {"leave": list(leave), "holiday": list(holiday), "duty": list(duty), "unavailable": [],
                "free_working_days": [a] if free else []}
    return fn


CASES = ["cap", "leave", "holiday", "visit_buffer", "coi", "reviewer_not_panel", "panel_not_reviewer",
         "discipline", "cooldown", "paused"]


@pytest.mark.parametrize("case", CASES)
def test_x28_constraints_each(case, people, depts, analysts, inst, inst2, monkeypatch):
    import attendance
    target = analysts[0]
    today = timezone.localdate()
    window = None
    kind = "document_analysis"
    need = None
    rules = {}
    if case in ("visit_buffer", "reviewer_not_panel", "cooldown"):
        kind = "visit_panel"
    if case == "panel_not_reviewer":
        kind = "ssq_review"
    pool = _pool(depts, analysts, kind=kind, **rules)
    if case == "cap":
        pool.rules = {"cap": 1}
        pool.save()
        WorkAssignment.objects.create(institution=inst2, kind=kind, department=depts["ict"], assignee=target,
                                      status="in_progress")
        expect = "At cap"
    elif case == "leave":
        monkeypatch.setattr(attendance, "availability", lambda u, a, b: _avail(
            leave=[today] if u == target else ())(u, a, b))
        expect = "On leave"
    elif case == "holiday":
        window = (today, today)
        monkeypatch.setattr(attendance, "availability", lambda u, a, b: _avail(
            holiday=[today] if u == target else (), free=u != target)(u, a, b))
        expect = "holiday"
    elif case == "visit_buffer":
        window = (today + datetime.timedelta(days=10), today + datetime.timedelta(days=11))
        WorkAssignment.objects.create(institution=inst2, kind="visit_panel", department=depts["ict"],
                                      assignee=target, status="accepted",
                                      meta={"from": (today + datetime.timedelta(days=12)).isoformat(),
                                            "to": (today + datetime.timedelta(days=13)).isoformat()})
        pool.rules = {"cap": 5}
        pool.save()
        expect = "On a visit"
    elif case == "coi":
        ConflictDeclaration.objects.create(user=target, institution=inst, reason="Brother is the Rector")
        expect = "Conflict of interest"
    elif case == "reviewer_not_panel":
        WorkAssignment.objects.create(institution=inst, kind="ssq_review", department=depts["ict"],
                                      assignee=target, status="completed")
        expect = "Reviewed this SSQ"
    elif case == "panel_not_reviewer":
        WorkAssignment.objects.create(institution=inst, kind="visit_panel", department=depts["ict"],
                                      assignee=target, status="completed")
        expect = "visit panel"
    elif case == "discipline":
        from institutions.models import Discipline
        eng = Discipline.objects.create(name="Engineering (t)", code="eng-t")
        sci = Discipline.objects.create(name="Science (t)", code="sci-t")
        RotationMember.objects.get(pool=pool, user=target).disciplines.add(sci)
        need = SimpleNamespace(discipline_id=eng.pk, start=today, end=today)
        expect = "discipline"
    elif case == "cooldown":
        WorkAssignment.objects.create(institution=inst, kind="visit_panel", department=depts["ict"],
                                      assignee=target, status="completed",
                                      assigned_at=timezone.now() - datetime.timedelta(days=200))
        expect = "Served this institution"
    elif case == "paused":
        RotationMember.objects.filter(pool=pool, user=target).update(
            paused_until=today + datetime.timedelta(days=5), pause_reason="Annual leave")
        expect = "Paused"
    s = rotation.suggest(pool, subject=inst, institution=inst, window=window, need=need, record=True)
    assert target not in [c.user for c in s.eligible]
    blocked = {c.user.pk: c for c in s.blocked}
    assert target.pk in blocked
    assert any(expect.lower() in r.lower() for r in blocked[target.pk].reasons), blocked[target.pk].reasons
    row = next(c for c in s.decision.candidates if c["user_id"] == target.pk)
    assert row["eligible"] is False and row["reasons"]
    # a rule switched off stops blocking
    code = blocked[target.pk].blocks[0]
    pool.rules = {**(pool.rules or {}), "rules": {code: False}}
    pool.save()
    s2 = rotation.suggest(pool, subject=inst, institution=inst, window=window, need=need, record=False)
    assert target in [c.user for c in s2.eligible] or blocked[target.pk].blocks[1:]


def test_x28_near_cap_warning(people, depts, analysts, inst):
    pool = _pool(depts, analysts)
    pool.rules = {"cap": 2}
    pool.save()
    WorkAssignment.objects.create(institution=inst, kind="document_analysis", department=depts["ict"],
                                  assignee=analysts[0], status="in_progress")
    s = rotation.suggest(pool, subject=inst, record=False)
    c = next(c for c in s.eligible if c.user == analysts[0])
    assert c.warnings and "Near cap" in c.warnings[0]


def test_x28_inspection_rules_delegate():
    """The Phase 5/8 finder sorts through rotation.fair_sort with its old key."""
    today = datetime.date(2026, 10, 1)

    def cand(name, kind, ext, n, last, score):
        return SimpleNamespace(name=name, kind=kind, obj=SimpleNamespace(is_staff_person=not ext),
                               assignments_this_year=n, last_served=last, score=score)
    people = [cand("Ext A", "resource_person", True, 0, None, 0), cand("Staff B", "qa_personnel", False, 2, None, 0),
              cand("Staff C", "qa_personnel", False, 1, today - datetime.timedelta(days=5), 1),
              cand("Staff D", "qa_personnel", False, 1, today - datetime.timedelta(days=50), 3),
              cand("Staff E", "qa_personnel", False, 1, today - datetime.timedelta(days=50), 2)]

    def old_key(c):
        days = (today - c.last_served).days if c.last_served else 100000
        external = c.kind == "resource_person" and not c.obj.is_staff_person
        return (True and external, c.assignments_this_year, -days, c.score, c.name)

    expected = [c.name for c in sorted(people, key=old_key)]
    assert [c.name for c in rotation.fair_sort(list(people), staff_first=True, today=today)] == expected
    assert expected == ["Staff E", "Staff D", "Staff C", "Staff B", "Ext A"]
    import inspect

    from inspection import rules
    assert "fair_sort" in inspect.getsource(rules.find)


def test_p8_suggest_ict_supervisor(people, depts, inst):
    pool = RotationPool.objects.create(code="oversight_ict", kind="oversight_ict", department=depts["ict"],
                                       name="ICT supervision")
    assert rotation.sync_members(pool) >= 3
    assert not pool.members.filter(user=people["director"]).exists(), "the director does not take turns"
    s = rotation.suggest_ict_supervisor(inst)
    assert s.suggested is not None and s.decision.kind == "oversight_ict"
    assert rotation.can_assign(people["director"], pool) and not rotation.can_assign(people["officer"], pool)


def test_p9_seed_sets_idempotent(db, depts, roles):
    import io

    from core import seeding
    from staff.models import Department
    Department.objects.create(name="DPAP (t)", code="dpap")
    seeding.run(only=["qa.rotation_pools", "qa.review_chains"], no_deps=True, stdout=io.StringIO())
    assert RotationPool.objects.filter(code="document_analysis:dpap").exists()
    assert RotationPool.objects.filter(code="oversight_ict").exists()
    assert ReviewChainPolicy.objects.filter(code="honoraria_schedule").exists()
    again = seeding.run(only=["qa.rotation_pools", "qa.review_chains"], no_deps=True, stdout=io.StringIO())
    assert sum(r.changes for _n, r in again) == 0


# ---------------------------------------------------------------------------
# X30 review chains
# ---------------------------------------------------------------------------
@pytest.fixture
def policies(db):
    ReviewChainPolicy.objects.create(code="document_analysis", name="Document analysis", steps=[
        {"key": "work", "actor": "assignee"}, {"key": "director", "actor": "director_of_department"}],
        return_targets={"director": ["work"]})
    ReviewChainPolicy.objects.create(code="honoraria_schedule", name="Honoraria", steps=[
        {"key": "work", "actor": "assignee"}, {"key": "director", "actor": "director_of_department"},
        {"key": "second", "actor": "approver_set", "when": "amount > 5000000", "set": {"mode": "es_only"}}])


def test_x30_chain_two_and_three_steps(people, depts, policies, inst):
    two = chain.open_chain("document_analysis", inst, department=depts["ict"], created_by=people["director"],
                           assignee=people["officer"])
    chain.submit(two, actor=people["officer"], note="Recommend accept")
    two.refresh_from_db()
    assert two.current_step == "director"
    chain.approve(two, actor=people["director"], remark="OK")
    two.refresh_from_db()
    assert two.state == ReviewChain.APPROVED and two.locked_at and two.steps.count() == 2

    small = chain.open_chain("honoraria_schedule", inst, department=depts["ict"], assignee=people["officer"],
                             context={"amount": 1200000})
    chain.submit(small, actor=people["officer"])
    chain.approve(small, actor=people["director"])
    small.refresh_from_db()
    assert small.state == ReviewChain.APPROVED, "below the limit the ES step is skipped"

    big = chain.open_chain("honoraria_schedule", inst, department=depts["ict"], assignee=people["officer"],
                           context={"amount": 6500000})
    chain.submit(big, actor=people["officer"])
    chain.approve(big, actor=people["director"])
    big.refresh_from_db()
    assert big.state == ReviewChain.OPEN and big.current_step == "second"
    with pytest.raises(PermissionDenied):
        chain.approve(big, actor=people["deputy"])
    chain.approve(big, actor=people["es"], remark="Approved above limit")
    big.refresh_from_db()
    assert big.state == ReviewChain.APPROVED
    assert len({s.actor_id for s in big.steps.all()}) == 3


def test_x30_distinct_actors(people, depts, policies, inst):
    c = chain.open_chain("honoraria_schedule", inst, department=depts["ict"], assignee=people["director"],
                         context={"amount": 9000000})
    chain.submit(c, actor=people["director"])
    c.refresh_from_db()
    with pytest.raises(PermissionDenied):
        chain.approve(c, actor=people["director"])           # same person can't confirm their own work
    ok, why = chain.can_act(people["director"], c)
    assert not ok and "different person" in why


def test_x30_send_back_and_resubmit(people, depts, policies, inst):
    c = chain.open_chain("document_analysis", inst, department=depts["ict"], assignee=people["officer"])
    chain.submit(c, actor=people["officer"])
    with pytest.raises(ValidationError):
        chain.send_back(c, actor=people["director"], to_step="work", remark="")
    chain.send_back(c, actor=people["director"], to_step="work", remark="Check page 4")
    c.refresh_from_db()
    assert c.current_step == "work"
    chain.submit(c, actor=people["officer"], note="Fixed")
    chain.approve(c, actor=people["director"])
    c.refresh_from_db()
    assert c.state == ReviewChain.APPROVED
    p = chain.panel(c, people["director"])
    assert [r["state"] for r in p["rows"]] == ["done", "done"]
