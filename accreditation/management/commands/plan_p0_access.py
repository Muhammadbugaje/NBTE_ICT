"""Dry run for the Phase 0 access changes (O10 and X12, decision 32).

Reports — without changing anything — what migration
``accreditation.0009_p0_access_changes`` and the new service rules will change:

* who loses departmental clearance (officer role holders, by department);
* which roles keep ``decide_departmental_clearance`` and who counts as a
  department director;
* the ``qa.ssq.submit_roles`` row before → after;
* institution portal accounts by role, and institutions with no active Chief
  Executive account (only the CE submits from Phase 0 on).

Privacy: counts only. Names and e-mails are printed for demo accounts
(``@demo.nbte.local`` etc.) and nothing else.

    python manage.py plan_p0_access [--output docs/master_plan_v2_1/reports/p0_access_dry_run.md]
"""
# scoped-by-caller
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

DEMO_DOMAINS = ("@demo.nbte.local", "@demo-poly.local", "@demo-health.local", "@p4demo.local",
                "@demo.nbte.test")
DEFAULT_OUT = "docs/master_plan_v2_1/reports/p0_access_dry_run.md"


def _demo(email):
    return any((email or "").lower().endswith(d) for d in DEMO_DOMAINS)


class Command(BaseCommand):
    help = "Dry run (report only) for the Phase 0 access changes: O10 clearance and X12 CE-only submission."

    def add_arguments(self, parser):
        parser.add_argument("--output", default=DEFAULT_OUT)

    def handle(self, *args, **opts):
        from django.contrib.auth.models import Permission

        import config
        from accounts.models import Role, User, UserPermissionOverride
        from institutions.models import Institution
        from staff.models import Department

        lines = [f"# Phase 0 access changes — dry run ({timezone.localtime():%Y-%m-%d %H:%M})", "",
                 "Report only; nothing was changed. Applied by migration "
                 "`accreditation.0009_p0_access_changes` (reversible).", ""]
        perm = Permission.objects.filter(content_type__app_label="accreditation",
                                         codename="decide_departmental_clearance").first()

        # --- O10 ------------------------------------------------------------------
        lines += ["## 1. O10 / T-SEC-7 — departmental clearance", ""]
        if perm is None:
            lines += ["The permission does not exist yet (fresh database): nothing to change.", ""]
        else:
            holders = list(Role.objects.filter(permissions=perm).order_by("-rank", "code"))
            lines += ["Roles holding `accreditation.decide_departmental_clearance` now:", "",
                      "| Role | Active users | After migration |", "|---|---|---|"]
            for role in holders:
                n = User.objects.filter(role=role, is_active=True).count()
                after = "**removed**" if role.code == "officer" else "kept (service still requires director/delegate)"
                lines.append(f"| `{role.code}` | {n} | {after} |")
            es = Role.objects.filter(code="executive-secretary").first()
            if es is not None and not es.permissions.filter(pk=perm.pk).exists():
                lines.append(f"| `executive-secretary` | {User.objects.filter(role=es, is_active=True).count()} "
                             "| **added** (ES may decide any clearance) |")
            lines.append("")
            officers = User.objects.filter(role__code="officer", is_active=True, user_type="staff")
            by_dept = Counter(getattr(u.department, "name", "No department") for u in officers.select_related(
                "department"))
            lines += [f"Officers losing the right to clear: **{officers.count()}** in {len(by_dept)} departments.",
                      "", "| Department | Officers |", "|---|---|"]
            lines += [f"| {name} | {n} |" for name, n in sorted(by_dept.items())]
            demo = sorted(u.email for u in officers if _demo(u.email))
            if demo:
                lines += ["", "Demo officers among them (the demo seed makes the four clearance officers "
                          "ClearanceDelegates so the walkthrough still works): " + ", ".join(f"`{e}`" for e in demo)]
            grants = UserPermissionOverride.objects.filter(permission=perm, effect="grant")
            lines += ["", f"Individual grant overrides of the permission: {grants.count()} (left as they are; "
                          "the service still requires director, delegate or ES)."]
            lines += ["", "Who counts as director (until Phase B): `Department.head` when set, otherwise the "
                          "holders of the `director` role in the department.", "",
                      "| Department | Head set | `director` role holders |", "|---|---|---|"]
            for dept in Department.objects.order_by("name"):
                n = User.objects.filter(department=dept, role__code="director", is_active=True).count()
                lines.append(f"| {dept.name} | {'yes' if dept.head_id else 'no'} | {n} |")
            lines.append("")

        # --- X12 ------------------------------------------------------------------
        lines += ["## 2. X12 — only the Chief Executive submits (decision 32 / D-3: default yes)", ""]
        raw = config.get("qa.ssq.submit_roles", None)
        try:
            current = raw if isinstance(raw, list) else json.loads(raw) if raw else None
        except (TypeError, ValueError):
            current = raw
        after = [r for r in current if r != "inst-admin"] if isinstance(current, list) else ["inst-chief-executive"]
        lines += [f"`qa.ssq.submit_roles`: `{json.dumps(current)}` → `{json.dumps(after or ['inst-chief-executive'])}`",
                  ""]
        roles = Counter(User.objects.filter(user_type="institution", is_active=True)
                        .values_list("role__code", flat=True))
        lines += ["Active institution portal accounts by role:", "", "| Role | Accounts |", "|---|---|"]
        lines += [f"| `{code or '(none)'}` | {n} |" for code, n in sorted(roles.items(), key=lambda x: str(x[0]))]
        insts = Institution.unscoped.filter(portal_enabled=True)
        with_ce = set(User.objects.filter(user_type="institution", is_active=True,
                                          role__code="inst-chief-executive").values_list("institution_id",
                                                                                         flat=True))
        without = [i for i in insts if i.pk not in with_ce]
        lines += ["", f"Portal-enabled institutions: {insts.count()}; without an active Chief Executive account: "
                      f"**{len(without)}**. Their administrators can still upload, pay and manage users, and must "
                      "invite the Chief Executive (Institution → Users → role *Chief Executive*) before they can "
                      "submit applications, programmes, QA requests or SSQs."]
        demo_codes = sorted(i.code for i in without if i.code in ("DMP", "DCH", "KDP"))
        if demo_codes:
            lines += ["", "Demo institutions among them: " + ", ".join(demo_codes)
                      + " (`seed_p0_demo` adds a demo Chief Executive to DMP)."]
        lines.append("")

        text = "\n".join(lines) + "\n"
        out = Path(opts["output"])
        if not out.is_absolute():
            out = Path(settings.BASE_DIR) / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        self.stdout.write(text)
        self.stdout.write(self.style.SUCCESS(f"Dry-run report written to {out}"))
