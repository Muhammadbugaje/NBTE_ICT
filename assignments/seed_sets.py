"""seed_nbte data sets for v2.1 Phase P rotation and review chains.

* ``qa.rotation_pools``  one pool per department and kind (core/seed_data/qa/rotation_pools.json),
                         members filled from the department's staff (never removed).
* ``qa.review_chains``   the default review chain policies (core/seed_data/qa/review_chains.json).
* ``demo.rotation``      (--with-demo) a month of decisions and open work for the DPAP board.

Idempotent: a second run reports no changes; edited rows are kept.
"""
import datetime

from core.seeding import load_json, seed_set, upsert


@seed_set("qa.rotation_pools", group="qa", depends=("settings", "roles", "org.departments"),
          files=("qa/rotation_pools.json",), order=560, phase="P")
def seed_rotation_pools(ctx):
    from staff.models import Department

    from . import rotation
    from .models_rotation import RotationPool

    data = load_json(ctx, "qa/rotation_pools.json")
    for row in data["rows"]:
        dept = Department.objects.filter(code=row["department"]).first()
        if dept is None:
            ctx.warn(f"No department '{row['department']}': pool {row['kind']} skipped")
            continue
        code = rotation.pool_code(row["kind"], dept)
        pool = upsert(RotationPool, {"code": code},
                      {"kind": row["kind"], "department": dept, "name": row["name"],
                       "eligible_roles": row.get("eligible_roles", []), "rules": row.get("rules", {})}, ctx=ctx)
        added = rotation.sync_members(pool)
        if added:
            ctx.res.filled += 1
            ctx.note(f"{code}: {added} member(s)")


@seed_set("qa.review_chains", group="qa", depends=("settings",), files=("qa/review_chains.json",), order=565,
          phase="P")
def seed_review_chains(ctx):
    from .models_chain import ReviewChainPolicy

    data = load_json(ctx, "qa/review_chains.json")
    for row in data["rows"]:
        upsert(ReviewChainPolicy, {"code": row["code"]},
               {"name": row["name"], "description": row.get("description", ""), "steps": row["steps"],
                "return_targets": row.get("return_targets", {})}, ctx=ctx)


@seed_set("demo.rotation", group="demo", depends=("qa.rotation_pools", "qa.review_chains"), order=960, phase="P",
          demo_only=True)
def seed_demo_rotation(ctx):
    """DPAP board demo: decisions and open work over the last four weeks on
    the demo institutions, one paused member (annual leave), one conflict
    declaration. Only touches demo-tagged institutions and demo accounts."""
    from django.contrib.contenttypes.models import ContentType
    from django.utils import timezone

    from institutions.models import Institution
    from qa_core.models import DemoTag

    from .models import WorkAssignment
    from .models_rotation import ConflictDeclaration, RotationDecision, RotationMember, RotationPool

    ct = ContentType.objects.get_for_model(Institution)
    inst_ids = [int(i) for i in DemoTag.objects.filter(content_type=ct).values_list("object_id", flat=True)]
    insts = list(Institution.unscoped.filter(pk__in=inst_ids).order_by("pk"))
    pools = {p.kind: p for p in RotationPool.objects.filter(department__code="dpap")}
    if not insts or not pools:
        ctx.warn("No demo institutions or DPAP pools: demo rotation skipped")
        return
    if RotationDecision.objects.filter(pool__in=pools.values()).exists():
        ctx.res.unchanged += 1
        return
    members = {}
    for kind, pool in pools.items():
        members[kind] = [m for m in pool.members.select_related("user").order_by("user_id")
                         if (m.user.email or "").endswith("@demo.nbte.local")]
    now = timezone.now()
    plan = [  # (kind, member index, days ago, institution index, open?)
        ("document_analysis", 0, 24, 0, False), ("ssq_review", 0, 23, 1, False), ("ssq_review", 0, 3, 1, True),
        ("document_analysis", 0, 2, 0, True), ("ssq_review", 0, 1, 2, True),
        ("document_analysis", 1, 22, 2, False), ("document_analysis", 1, 9, 3, True),
        ("visit_panel", 2, 25, 3, False), ("visit_panel", 2, 4, 3, True), ("document_analysis", 2, 8, 1, True),
        ("ssq_review", 3, 26, 0, False),
        ("ssq_review", 4, 21, 2, False), ("document_analysis", 4, 5, 0, True), ("ssq_review", 4, 2, 3, True),
        ("document_analysis", 6, 20, 3, False), ("document_analysis", 6, 6, 2, True), ("ssq_review", 6, 1, 0, True),
    ]
    n = 0
    for kind, mi, days, ii, is_open in plan:
        ms = members.get(kind) or members.get("document_analysis") or []
        pool = pools.get(kind) or pools.get("document_analysis")
        if not ms or pool is None:
            continue
        m = ms[mi % len(ms)]
        if m.pool_id != pool.pk:
            m = RotationMember.objects.filter(pool=pool, user=m.user).first() or m
        inst = insts[ii % len(insts)]
        when = now - datetime.timedelta(days=days)
        wa = WorkAssignment.objects.create(
            institution=inst, kind=kind, department=pool.department, assignee=m.user,
            status=WorkAssignment.IN_PROGRESS if is_open else WorkAssignment.COMPLETED, assigned_at=when,
            completed_at=None if is_open else when + datetime.timedelta(days=5),
            meta={"demo": True})
        DemoTag.tag(wa)
        d = RotationDecision.objects.create(
            pool=pool, kind=kind, department=pool.department, institution=inst, subject_label=inst.name,
            candidates=[{"user_id": m.user_id, "name": m.user.get_full_name(), "eligible": True}],
            suggested_user=m.user, chosen_user=m.user, decided_at=when, created_at=when,
            work_assignment=wa, parameters={"demo": True})
        DemoTag.tag(d)
        RotationMember.objects.filter(pool__department=pool.department, user=m.user).update(
            last_assigned_at=when)
        n += 1
    # one member on annual leave (paused), one conflict declaration
    leave = members.get("ssq_review") or []
    if len(leave) > 3:
        RotationMember.objects.filter(pool__department__code="dpap", user=leave[3].user).update(
            paused_until=timezone.localdate() + datetime.timedelta(days=11), pause_reason="Annual leave")
    if len(leave) > 1:
        ConflictDeclaration.objects.get_or_create(user=leave[1].user, institution=insts[0],
                                                  defaults={"reason": "Former lecturer there (demo)"})
    ctx.res.created += n
    ctx.note(f"{n} demo assignment(s) on the DPAP board")


@seed_set("demo.review_chains", group="demo", depends=("qa.review_chains", "demo.rotation"), order=961, phase="P",
          demo_only=True)
def seed_demo_review_chains(ctx):
    """Two demo chains: a DPAP document analysis waiting for the director, and
    an honoraria schedule above the ES limit (three steps)."""
    from django.contrib.contenttypes.models import ContentType

    from accounts.models import User
    from institutions.models import Institution
    from qa_core.models import DemoTag
    from staff.models import Department

    from . import chain
    from .models_chain import ReviewChain

    if ReviewChain.objects.filter(subject_label__startswith="Demo:").exists():
        ctx.res.unchanged += 1
        return
    ct = ContentType.objects.get_for_model(Institution)
    inst_ids = [int(i) for i in DemoTag.objects.filter(content_type=ct).values_list("object_id", flat=True)]
    inst = Institution.unscoped.filter(pk__in=inst_ids).order_by("pk").first()
    officer = User.objects.filter(email__iexact="qa.officer@demo.nbte.local").first()
    dpap = Department.objects.filter(code="dpap").first()
    fin_officer = User.objects.filter(email__iexact="qa.finance_officer@demo.nbte.local").first()
    fin = Department.objects.filter(code="finance").first()
    if not (inst and officer and dpap):
        ctx.warn("Demo officer / DPAP / demo institution missing: demo chains skipped")
        return
    c = chain.open_chain("document_analysis", inst, department=dpap, institution=inst, assignee=officer)
    ReviewChain.objects.filter(pk=c.pk).update(subject_label=f"Demo: {inst.name} · Certificate of incorporation")
    c.refresh_from_db()
    chain.submit(c, actor=officer, note="Certificate matches the CAC record. Recommend accept.")
    DemoTag.tag(c)
    ctx.res.created += 1
    if fin_officer and fin:
        h = chain.open_chain("honoraria_schedule", None, department=fin, institution=inst, assignee=fin_officer,
                             context={"amount": 6450000})
        ReviewChain.objects.filter(pk=h.pk).update(subject_label="Demo: Honoraria schedule · October visits")
        h.refresh_from_db()
        import org
        dof = org.director_of(fin)
        chain.submit(h, actor=fin_officer, note="14 resource persons, October visits.")
        if dof is not None and dof != fin_officer:
            chain.approve(h, actor=dof, remark="Rates checked against the schedule.")
        DemoTag.tag(h)
        ctx.res.created += 1
