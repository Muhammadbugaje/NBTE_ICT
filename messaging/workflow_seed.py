"""Portable memo routes and routing rules from the current baseline policy."""
from __future__ import annotations

from core.seeding import seed_set, upsert


ROUTES = [
    ("all-staff-circular", "Institution-wide circular",
     "All staff / all directors / roles: the sender's director, then the ES.",
     [("Director Approval", "director", True, True, ""),
      ("ES Approval", "es", False, False, "Final approval before it goes to everyone.")]),
    ("department-circular", "Department circular",
     "A memo to a whole department needs the sender's director.",
     [("Director Approval", "director", False, True, "Approve before it reaches the whole department.")]),
    ("director-to-es", "Director to ES",
     "Directors and deputies write to the ES directly.", []),
    ("es-to-staff", "Executive Secretary to Staff",
     "Memos from the Executive Secretary are delivered directly.", []),
    ("general-memo", "General memo",
     "Any other memo to one person or a list: the sender's line head sees it first, if there is one.",
     [("Line Head", "division_head", True, True, "")]),
    ("staff-to-deputy", "Staff to Deputy Director",
     "Through the sender's line head, if they have one.",
     [("Line Head", "division_head", True, True, "Check the request before it goes to the deputy.")]),
    ("staff-to-director", "Staff to Director",
     "Staff memos to a director go through the sender's deputy and director.",
     [("Deputy Director Approval", "deputy", True, True, ""),
      ("Director Approval", "director", False, True, "")]),
    ("staff-to-es", "Staff to ES",
     "Staff memos to the Executive Secretary go through the sender's line head, director, and ES.",
     [("Line Head", "division_head", True, True, ""),
      ("Director Approval", "director", False, True, ""),
      ("ES Approval", "es", False, False, "")]),
]

# name, route, target kind, sender roles, target roles, department relation, priority
RULES = [
    ("ES writes to a list", "es-to-staff", "individuals", ["executive-secretary"], [], "any", 300),
    ("ES writes to anyone", "es-to-staff", "specific_user", ["executive-secretary"], [], "any", 300),
    ("Director/Deputy to ES", "director-to-es", "specific_user",
     ["director", "deputy-director"], ["executive-secretary"], "any", 200),
    ("Staff memo to ES", "staff-to-es", "specific_user", [], ["executive-secretary"], "any", 100),
    ("Staff memo to Director", "staff-to-director", "specific_user", [], ["director"], "any", 90),
    ("Staff memo to Deputy Director", "staff-to-deputy", "specific_user", [],
     ["deputy-director"], "any", 80),
    ("All-directors circular", "all-staff-circular", "all_directors", [], [], "any", 50),
    ("All-staff circular", "all-staff-circular", "all_staff", [], [], "any", 50),
    ("Circular to roles", "all-staff-circular", "role", [], [], "any", 50),
    ("Department circular", "department-circular", "department", [], [], "any", 50),
    ("Any other memo to one person", "general-memo", "specific_user", [], [], "any", 10),
]

MEMO_PERMISSIONS = {
    "send_memo_broadcast": ("executive-secretary", "director", "deputy-director", "super-admin"),
    "approve_memo": (
        "executive-secretary", "director", "deputy-director", "division-head",
        "office-head-ts", "office-head-dev", "office-head-admin", "super-admin",
    ),
    "manage_memo": ("executive-secretary", "super-admin"),
    "manage_memoroute": ("super-admin",),
}


@seed_set(
    "messaging.memo_routing",
    group="workflow",
    depends=("roles", "messaging.external_route"),
    order=480,
    phase="E",
)
def seed_memo_routing(ctx):
    """Seed reusable routes/rules only; never create demo accounts or memos."""
    from accounts.models import Role
    from django.contrib.auth.models import Permission
    from messaging.models import MemoRoute, MemoRouteRule, MemoRouteStep

    route_objects = {}
    for code, name, description, steps in ROUTES:
        route = upsert(
            MemoRoute,
            {"code": code},
            {
                "name": name,
                "description": description,
                "same_rank_behaviour": MemoRoute.SAME_RANK_SKIP,
                "is_active": True,
            },
            ctx=ctx,
        )
        route_objects[code] = route
        for position, (step_name, approver_kind, optional, hierarchical, instructions) in enumerate(steps, 1):
            upsert(
                MemoRouteStep,
                {"route": route, "position": position},
                {
                    "name": step_name,
                    "approver_kind": approver_kind,
                    "approver_role": None,
                    "approver_user": None,
                    "scope": "requester",
                    "is_optional": optional,
                    "is_hierarchical": hierarchical,
                    "instructions": instructions,
                },
                ctx=ctx,
            )

    for name, route_code, target_kind, senders, targets, relation, priority in RULES:
        upsert(
            MemoRouteRule,
            {"name": name},
            {
                "route": route_objects[route_code],
                "target_kind": target_kind,
                "sender_role_codes": senders,
                "target_role_codes": targets,
                "department_relation": relation,
                "priority": priority,
                "is_active": True,
            },
            ctx=ctx,
        )

    for codename, role_codes in MEMO_PERMISSIONS.items():
        permission = Permission.objects.filter(
            content_type__app_label="messaging",
            codename=codename,
        ).first()
        if permission is None:
            ctx.warn(f"memo permission messaging.{codename} is missing")
            continue
        for role in Role.objects.filter(code__in=role_codes):
            if not role.permissions.filter(pk=permission.pk).exists():
                role.permissions.add(permission)
