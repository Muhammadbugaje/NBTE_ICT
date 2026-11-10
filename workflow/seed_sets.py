"""Portable reference seed data for internal request workflows."""
from __future__ import annotations

import copy

from core.seeding import seed_set, upsert


@seed_set("workflow.request_catalog", group="workflow", depends=("roles", "org.departments"), order=490)
def seed_request_catalog(ctx):
    """Seed request categories, approval chains, steps, and request forms.

    The request-type definitions reuse the maintained form schemas from the
    management command. User-specific approvers are deliberately never seeded.
    """
    from accounts.models import Role
    from staff.models import Department
    from workflow.management.commands.seed_request_types import CATEGORIES, CHAINS, REQUEST_TYPES
    from workflow.models import ApprovalChain, ApprovalStep, RequestCategory, RequestType

    categories = list(CATEGORIES) + [("funds", "Funds", "", 90)]
    category_objects = {}
    for code, name, icon, sort_order in categories:
        category_objects[code] = upsert(
            RequestCategory,
            {"code": code},
            {"name": name, "icon": icon, "sort_order": sort_order, "is_active": True},
            ctx=ctx,
        )

    chains = copy.deepcopy(CHAINS)
    ticket_funds_steps = [{
        "position": 1,
        "name": "Finance releases the funds",
        "kind": "approval",
        "approver_kind": "receiving_director",
        "role_code": "director",
        "scope": "receiving",
        "is_optional": False,
        "is_hierarchical": False,
        "instructions": "Release the approved amount to the ticket's handler, then approve this step.",
    }]
    chains.append((
        "ticket-funds-release",
        "Ticket funds: Finance releases",
        "",
        ticket_funds_steps,
    ))

    chain_objects = {}
    for code, name, description, steps in chains:
        chain = upsert(
            ApprovalChain,
            {"code": code},
            {"name": name, "description": description, "is_active": True},
            ctx=ctx,
        )
        chain_objects[code] = chain

        for step in steps:
            role = Role.objects.filter(code=step.get("role_code")).first() if step.get("role_code") else None
            # The current database routes ICT handover confirmation to the
            # requester's director, not the generic staff role in the older
            # command definition.
            if code == "ict-fulfillment" and step["name"] == "Handover confirmation":
                role = Role.objects.filter(code="director").first()
            upsert(
                ApprovalStep,
                {"chain": chain, "position": step["position"]},
                {
                    "name": step["name"],
                    "kind": step.get("kind", "approval"),
                    "approver_kind": step["approver_kind"],
                    "approver_role": role,
                    "approver_user": None,
                    "scope": step.get("scope", "requester"),
                    "is_optional": step.get("is_optional", False),
                    "is_hierarchical": step.get("is_hierarchical", True),
                    "instructions": step.get("instructions", ""),
                },
                ctx=ctx,
            )

    request_types = copy.deepcopy(REQUEST_TYPES)
    # These are the active flags in the current database. Leave/workflow
    # routing relies on both the requester-side chain and the mandatory gates.
    for row in request_types:
        if row[0] == "annual-leave":
            row[5]["is_cross_department"] = True
            row[5]["requires_es_approval"] = True
    request_types.append((
        "ticket-funds",
        "Funds for a ticket",
        "funds",
        "finance",
        "ticket-funds-release",
        {
            "requires_es_approval": True,
            "requires_finance_approval": True,
            "is_cross_department": False,
            "form_schema": {},
            "sort_order": 900,
        },
    ))

    for code, name, category_code, department_code, chain_code, flags in request_types:
        flags = dict(flags)
        form_schema = copy.deepcopy(flags.pop("form_schema", {}))
        flags.setdefault("is_cross_department", False)
        flags.setdefault("requires_es_approval", False)
        flags.setdefault("requires_finance_approval", False)
        flags.setdefault("requires_audit_approval", False)
        flags.setdefault("requires_hr_approval", False)
        flags.setdefault("requires_records_filing", False)
        flags.setdefault("sort_order", 0)
        flags.setdefault("is_active", True)

        department = Department.objects.filter(code=department_code).first()
        if department is None:
            ctx.warn(f"request type {code}: department '{department_code}' is missing")
            ctx.res.skipped += 1
            continue
        category = category_objects[category_code]
        chain = chain_objects[chain_code]
        requester_chain = chain_objects.get("internal-4-step") if flags["is_cross_department"] else None
        upsert(
            RequestType,
            {"code": code},
            {
                "name": name,
                "category": category,
                "handling_department": department,
                "approval_chain": chain,
                "requester_chain": requester_chain,
                "form_schema": form_schema,
                "description": "",
                **flags,
            },
            ctx=ctx,
        )
