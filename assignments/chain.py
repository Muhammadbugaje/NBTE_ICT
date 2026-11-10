"""Review chains (v2.1 X30, spec_v21 G6.d): two- or three-step verification
for any work — staff → director → [ES / second confirmer where configured].

    chain = chain.open_chain("document_analysis", doc, department=dpap, institution=inst,
                             created_by=director, assignee=officer)
    chain.submit(c, actor=officer, note="Recommend accept")
    chain.approve(c, actor=director)            # done, or on to the next step
    chain.send_back(c, actor=director, to_step="work", remark="Check page 4")
    chain.reject(c, actor=director, remark="…")

Step definition (``ReviewChainPolicy.steps``)::

    {"key": "work", "actor": "assignee", "label": "Officer analyses"}
    {"key": "director", "actor": "director_of_department"}
    {"key": "second", "actor": "approver_set", "when": "amount > 5000000",
     "set": {"mode": "roles", "roles": ["executive-secretary"]}}

Actor kinds: ``assignee``, ``director_of_department``, ``division_head``,
``finance_director``, ``es``, ``approver_set`` (mode ``es_only`` | ``users`` |
``roles`` | ``group`` | ``permission``). ``when`` is a ``qa_core.formula``
expression over ``chain.context``; a step whose condition is false is
skipped. **Distinct actors**: nobody may act on two steps of the same pass
(a send-back starts a new pass from the target step). ES may act on any
step with a reason (audited).
"""
# scoped-by-caller
from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

import config

from .models_chain import ReviewChain, ReviewChainPolicy, ReviewStep

ACTOR_KINDS = [("assignee", "The assigned officer"), ("director_of_department", "Director of the department"),
               ("division_head", "Head of the officer's division"), ("finance_director", "Director of Finance"),
               ("es", "Executive Secretary"), ("approver_set", "A configured set of approvers")]


class ChainError(ValidationError):
    pass


# ===========================================================================
# Policies
# ===========================================================================
def policy(code):
    p = ReviewChainPolicy.objects.filter(code=code, is_active=True).first()
    if p is None:
        raise ChainError(f"No active review chain policy '{code}'.")
    return p


def effective_steps(pol):
    """The policy's steps with configured extras (``qa.chain.<code>.third_step``:
    ``none`` | ``es`` | ``roles:<code,code>``) appended."""
    steps = [dict(s) for s in pol.steps or []]
    extra = str(config.get(f"qa.chain.{pol.code}.third_step", "none") or "none").strip()
    if extra and extra != "none" and not any(s.get("key") == "second" for s in steps):
        if extra == "es":
            steps.append({"key": "second", "actor": "es", "label": "Executive Secretary confirms"})
        elif extra.startswith("roles:"):
            roles = [r.strip() for r in extra[6:].split(",") if r.strip()]
            steps.append({"key": "second", "actor": "approver_set", "label": "Second confirmer",
                          "set": {"mode": "roles", "roles": roles}})
    return steps


def _step(chain, key):
    return next((s for s in effective_steps(chain.policy) if s.get("key") == key), None)


def _applies(step, chain):
    expr = (step or {}).get("when")
    if not expr:
        return True
    from qa_core.formula import FormulaError, evaluate_value
    try:
        return bool(evaluate_value(expr, dict(chain.context or {})))
    except FormulaError:
        # a condition that cannot be evaluated (missing value) keeps the step: safer
        return True


def applicable_steps(chain):
    return [s for s in effective_steps(chain.policy) if _applies(s, chain)]


def _next_key(chain, after_key):
    keys = [s["key"] for s in applicable_steps(chain)]
    if after_key not in keys:
        return keys[0] if keys else None
    i = keys.index(after_key)
    return keys[i + 1] if i + 1 < len(keys) else None


# ===========================================================================
# Who acts on a step
# ===========================================================================
def _dept(chain):
    return chain.department


def eligible_actors(chain, step_key=None):
    """Users who may act on ``step_key`` (default: the current step)."""
    import org
    from accounts.models import User

    step = _step(chain, step_key or chain.current_step) or {}
    kind = step.get("actor")
    if kind == "assignee":
        return [chain.assignee] if chain.assignee else []
    if kind == "director_of_department":
        d = org.director_of(_dept(chain)) if _dept(chain) else None
        return [d] if d else []
    if kind == "division_head":
        div = org.division_of(chain.assignee) if chain.assignee else None
        head = getattr(div, "head", None)
        return [head] if head and head.is_active else []
    if kind == "finance_director":
        code = config.get("qa.payment.finance_department", "finance") or "finance"
        d = org.director_of(code)
        return [d] if d else []
    if kind == "es":
        return list(org.users_with_role(org.ES_ROLE, staff_only=False))
    if kind == "approver_set":
        sel = step.get("set") or {}
        mode = sel.get("mode", "es_only")
        if mode == "es_only":
            return list(org.users_with_role(org.ES_ROLE, staff_only=False))
        if mode == "users":
            return list(User.objects.filter(pk__in=sel.get("users") or [], is_active=True))
        if mode == "roles":
            return list(org.users_with_role(*(sel.get("roles") or []), staff_only=False))
        if mode == "group":
            return list(User.objects.filter(groups__name=sel.get("group", ""), is_active=True))
        if mode == "permission":
            app, _, codename = str(sel.get("permission", "")).partition(".")
            return [u for u in User.objects.filter(is_active=True, user_type="staff")
                    if u.has_perm(f"{app}.{codename}")]
    return []


def _is_es(user):
    import org
    return org.is_es(user) or org.is_super_admin(user)


def pass_actors(chain):
    """step_key -> actor_id for the current pass (submit/approve since the
    last send-back that reached that step)."""
    order = [s["key"] for s in effective_steps(chain.policy)]
    acted = {}
    for st in chain.steps.order_by("at", "pk"):
        if st.action in (ReviewStep.SUBMIT, ReviewStep.APPROVE):
            acted[st.step_key] = st.actor_id
        elif st.action in (ReviewStep.RETURN, ReviewStep.REOPEN):
            target = st.to_step
            if target in order:
                cut = order.index(target)
                acted = {k: v for k, v in acted.items() if k in order and order.index(k) < cut}
            else:
                acted = {}
    return acted


def can_act(user, chain, step_key=None):
    """(ok, reason) for ``user`` acting on the chain's current step."""
    if not chain.is_open:
        return False, "This review is closed."
    key = step_key or chain.current_step
    earlier = {k: v for k, v in pass_actors(chain).items() if k != key}
    if user.pk in earlier.values():
        return False, "You already acted on an earlier step; a different person must confirm this one."
    if user in eligible_actors(chain, key):
        return True, ""
    if _is_es(user):
        return True, "es"
    return False, "You are not the person for this step."


def _require(user, chain, *, reason=""):
    ok, why = can_act(user, chain)
    if not ok:
        raise PermissionDenied(why)
    if why == "es" and len((reason or "").strip()) < 5 and chain.current_step != "work":
        raise ChainError("Give a reason for acting on this step as ES.")
    return why == "es"


# ===========================================================================
# Life cycle
# ===========================================================================
@transaction.atomic
def open_chain(policy_code, subject=None, *, department=None, institution=None, created_by=None, assignee=None,
               context=None):
    pol = policy(policy_code)
    ct = sid = None
    label = ""
    if subject is not None and getattr(subject, "pk", None) is not None:
        ct = ContentType.objects.get_for_model(subject, for_concrete_model=False)
        sid = str(subject.pk)
        label = str(getattr(subject, "reference", None) or subject)[:200]
        if institution is None:
            institution = getattr(subject, "institution", None)
    chain = ReviewChain(policy=pol, subject_ct=ct, subject_id=sid or "", subject_label=label,
                        institution=institution if getattr(institution, "pk", None) else None,
                        department=department, assignee=assignee, context=dict(context or {}),
                        created_by=created_by)
    steps = applicable_steps(chain)
    if len(steps) < 2:
        raise ChainError("A review chain needs at least two steps.")
    chain.current_step = steps[0]["key"]
    chain.save()
    from core import audit
    audit.log("chain_opened", chain, actor=created_by, changes={"policy": pol.code})
    _notify(chain)
    return chain


def for_subject(subject):
    ct = ContentType.objects.get_for_model(subject, for_concrete_model=False)
    return ReviewChain.objects.filter(subject_ct=ct, subject_id=str(subject.pk)).select_related("policy").first()


def _record(chain, actor, action, to_step, remark="", snapshot=None, step_key=None):
    return ReviewStep.objects.create(chain=chain, step_key=step_key or chain.current_step, actor=actor,
                                     action=action, to_step=to_step or "", remark=remark or "",
                                     snapshot=snapshot or {})


def _advance(chain, actor, action, remark, snapshot):
    nxt = _next_key(chain, chain.current_step)
    _record(chain, actor, action, nxt or "done", remark, snapshot)
    if nxt is None:
        chain.state = ReviewChain.APPROVED
        chain.locked_at = timezone.now()
        chain.current_step = "done"
        chain.save(update_fields=["state", "locked_at", "current_step", "updated_at"])
        from core import audit
        audit.log("chain_approved", chain, actor=actor)
        _notify(chain, approved=True)
    else:
        chain.current_step = nxt
        chain.save(update_fields=["current_step", "updated_at"])
        _notify(chain)
    return chain


@transaction.atomic
def submit(chain, *, actor, note="", snapshot=None):
    """The first step's actor hands the work on."""
    chain = ReviewChain.objects.select_for_update().get(pk=chain.pk)
    first = applicable_steps(chain)[0]["key"]
    if chain.current_step != first:
        raise ChainError("This work was already submitted.")
    _require(actor, chain, reason=note)
    return _advance(chain, actor, ReviewStep.SUBMIT, note, snapshot)


@transaction.atomic
def approve(chain, *, actor, remark="", snapshot=None):
    chain = ReviewChain.objects.select_for_update().get(pk=chain.pk)
    first = applicable_steps(chain)[0]["key"]
    if chain.current_step == first:
        return submit(chain, actor=actor, note=remark, snapshot=snapshot)
    _require(actor, chain, reason=remark)
    return _advance(chain, actor, ReviewStep.APPROVE, remark, snapshot)


def return_targets(chain):
    """Steps the current step may send back to."""
    keys = [s["key"] for s in applicable_steps(chain)]
    if chain.current_step not in keys:
        return []
    earlier = keys[:keys.index(chain.current_step)]
    allowed = (chain.policy.return_targets or {}).get(chain.current_step)
    return [k for k in earlier if allowed is None or k in allowed]


@transaction.atomic
def send_back(chain, *, actor, to_step=None, remark):
    chain = ReviewChain.objects.select_for_update().get(pk=chain.pk)
    if not (remark or "").strip():
        raise ChainError("Say what needs to change.")
    targets = return_targets(chain)
    to_step = to_step or (targets[0] if targets else None)
    if to_step not in targets:
        raise ChainError("This step cannot send the work back there.")
    _require(actor, chain, reason=remark)
    _record(chain, actor, ReviewStep.RETURN, to_step, remark)
    chain.current_step = to_step
    chain.save(update_fields=["current_step", "updated_at"])
    _notify(chain, returned=True, remark=remark)
    return chain


@transaction.atomic
def reject(chain, *, actor, remark):
    chain = ReviewChain.objects.select_for_update().get(pk=chain.pk)
    if not (remark or "").strip():
        raise ChainError("Give the reason for rejecting.")
    _require(actor, chain, reason=remark)
    _record(chain, actor, ReviewStep.REJECT, "done", remark)
    chain.state = ReviewChain.REJECTED
    chain.locked_at = timezone.now()
    chain.save(update_fields=["state", "locked_at", "updated_at"])
    from core import audit
    audit.log("chain_rejected", chain, actor=actor, note=remark[:255])
    return chain


# ===========================================================================
# Panel data
# ===========================================================================
def panel(chain, user=None):
    """Rows for the chain panel partial: every applicable step with its
    actor names, latest action, and the current step's allowed actions."""
    steps = applicable_steps(chain)
    history = list(chain.steps.select_related("actor").order_by("at", "pk"))
    latest = {}
    for h in history:
        latest[h.step_key] = h
    keys = [s["key"] for s in steps]
    cur = keys.index(chain.current_step) if chain.current_step in keys else len(keys)
    rows = []
    for i, s in enumerate(steps):
        actors = eligible_actors(chain, s["key"])
        state = "done" if (i < cur or chain.state == ReviewChain.APPROVED) else (
            "current" if i == cur and chain.is_open else "waiting")
        rows.append({"key": s["key"], "label": s.get("label") or s["key"].replace("_", " ").title(),
                     "actor_kind": dict(ACTOR_KINDS).get(s.get("actor"), s.get("actor")),
                     "who": (("Any of " + str(len(actors)) + " approvers" if len(actors) > 1 else "One approver")
                             if s.get("actor") in ("es", "approver_set") and actors else
                             ", ".join((a.get_full_name() or a.email) for a in actors[:3]) or "Nobody yet"),
                     "when": s.get("when", ""), "state": state, "last": latest.get(s["key"])})
    can, why = (can_act(user, chain) if user is not None else (False, ""))
    return {"chain": chain, "rows": rows, "history": history, "can_act": can, "why": why,
            "is_first": bool(steps) and chain.current_step == steps[0]["key"],
            "targets": return_targets(chain) if can else []}


def _notify(chain, *, approved=False, returned=False, remark=""):
    try:
        from notifications.services import notify_many
    except ImportError:  # pragma: no cover
        return
    label = chain.subject_label or chain.policy.name
    if approved:
        people = {s.actor for s in chain.steps.select_related("actor")}
        notify_many(list(people), title=f"Approved: {label}", body=f"{chain.policy.name} is complete.")
        return
    actors = eligible_actors(chain)
    if returned:
        notify_many(actors, title=f"Sent back: {label}", body=remark[:200])
    elif chain.is_open and actors:
        notify_many(actors, title=f"Waiting for you: {label}", body=f"{chain.policy.name} — your step.")
