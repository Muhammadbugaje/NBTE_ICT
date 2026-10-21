"""python manage.py seed_startup [--with-demo] [--reset-demo] [--dry-run]

Startup seed for the NBTE Digital QA System (Master Plan §15, PHASES Phase 1).

* **Idempotent.** Every object is fetched by its natural key (code / name /
  key), created when missing and never overwritten when present — the same
  contract as `seed_config` and `seed_default_groups`. A second run reports
  no changes.
* Runs the existing seeders first: `seed_roles --sync`, `seed_config`,
  `seed_departments`, `seed_default_groups`, `seed_nbte --with-lgas`.
* `--with-demo` creates the demo institution and the five demo accounts,
  each with `must_change_password=True` and a fresh random password that is
  printed ONCE. Every demo row is tagged in `qa_core.DemoTag`.
* `--reset-demo` deletes exactly the DemoTag-listed rows, in reverse
  dependency order, inside one transaction. Nothing untagged is touched. It is
  refused when DEBUG is false unless `--i-understand-production` is passed.
* `--dry-run` runs everything inside a transaction that is rolled back, and
  prints what would have been created.
"""
import io
import json
import re
import secrets
from collections import OrderedDict
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone

from accounts.models import GroupProfile, Role, User
from config.models import ConfigEntry
from staff.models import Department, Designation, StateOfOrigin

from qa_core import seed_data
from qa_core import seed_data_phase2 as p2
from qa_core import seed_data_phase3 as p3
from qa_core import seed_data_phase4 as p4
from qa_core import seed_data_phase5 as p5
from qa_core import seed_data_phase6 as p6
from qa_core import seed_data_phase7 as p7
from qa_core.departments import qa_department
from qa_core.models import (
    ActionPolicy,
    DemoTag,
    FlowDefinition,
    FlowStage,
    FlowTransition,
    NotificationRule,
    NotificationTemplate,
    ReferenceFormat,
)
from qa_core.settings_catalogue import SETTINGS

# Reverse dependency order for --reset-demo (things that point at users and
# institutions first). Anything not listed is deleted afterwards.
RESET_ORDER = [
    # Phase 7: complaints of demo institutions and users (escalations and stages cascade)
    "complaints.escalation", "complaints.complaintattachment", "complaints.complaintmessage",
    "complaints.complaintstage", "complaints.complaint",
    # Phase 5: inspection records of demo cases and demo resource persons
    "inspection.servicerecord", "inspection.reportendorsement", "inspection.postvisitreport",
    "inspection.postvisitpersonnel", "inspection.inspectionteammember", "inspection.inspection",
    "inspection.ssqreviewassignment", "inspection.qamapping", "inspection.resourcepersonfile",
    "inspection.resourcepersondiscipline", "inspection.resourceperson", "inspection.qapersonnel",
    # Phase 4: SOP-03 records and the stage tasks of demo cases
    "tasks.task", "accreditation.fundrelease", "accreditation.costline", "accreditation.costsheet",
    "accreditation.personnelentry", "accreditation.personnellist", "accreditation.visitdetail",
    "accreditation.response", "accreditation.query", "accreditation.qacase", "accreditation.submission",
    "accreditation.evidence", "accreditation.ssq", "accreditation.qarequestprogramme", "accreditation.qarequest",
    "payments.paymentadvice", "payments.paymentverification", "payments.proofofpayment", "payments.paymentslip",
    "payments.paymentattempt", "payments.paymentcredit", "payments.refund", "payments.paymentobligation",
    "payments.bankaccount", "accreditation.departmentalclearance", "accreditation.application",
    "institutions.institutiondocument", "institutions.programme", "institutions.institutionuser",
    "institutions.institutioncontact", "institutions.institutionaddress", "institutions.institutionrequest",
    "institutions.impersonationsession", "qa_core.pendingaction", "qa_core.timelineevent", "qa_core.flowinstance",
    "auth.group", "staff.staff", "accounts.user", "institutions.institution",
]

# Every row that points at a demo institution or user is demo activity too
# (`--reset-demo` removes it with them, §15).
DEMO_DEPENDENTS = [
    ("complaints.Complaint", "institution"),  # Phase 7
    ("accreditation.Response", "query__institution"), ("accreditation.Query", "institution"),
    ("inspection.ServiceRecord", "institution"),
    ("inspection.ReportEndorsement", "report__inspection__case__institution"),
    ("inspection.PostVisitReport", "inspection__case__institution"),
    ("inspection.PostVisitPersonnel", "inspection__case__institution"),
    ("inspection.InspectionTeamMember", "inspection__case__institution"),
    ("inspection.Inspection", "case__institution"), ("inspection.QAMapping", "case__institution"),
    ("accreditation.FundRelease", "case__institution"), ("accreditation.CostLine", "sheet__case__institution"),
    ("accreditation.CostSheet", "case__institution"),
    ("accreditation.PersonnelEntry", "personnel_list__case__institution"),
    ("accreditation.PersonnelList", "case__institution"), ("accreditation.VisitDetail", "case__institution"),
    ("accreditation.QACase", "institution"),
    ("accreditation.Submission", "ssq__qa_request_programme__qa_request__institution"),
    ("accreditation.Evidence", "ssq__qa_request_programme__qa_request__institution"),
    ("accreditation.SSQ", "qa_request_programme__qa_request__institution"),
    ("accreditation.QARequestProgramme", "qa_request__institution"), ("accreditation.QARequest", "institution"),
    ("payments.PaymentAdvice", "obligation__institution"), ("payments.PaymentVerification", "obligation__institution"),
    ("payments.ProofOfPayment", "obligation__institution"), ("payments.PaymentSlip", "obligation__institution"),
    ("payments.PaymentAttempt", "obligation__institution"), ("payments.PaymentCredit", "institution"),
    ("payments.Refund", "obligation__institution"), ("payments.PaymentObligation", "institution"),
    ("accreditation.DepartmentalClearance", "application__institution"),
    ("accreditation.Application", "institution"), ("institutions.InstitutionDocument", "institution"),
    ("institutions.Programme", "institution"), ("institutions.InstitutionUser", "institution"),
    ("institutions.InstitutionContact", "institution"), ("institutions.InstitutionAddress", "institution"),
    ("institutions.InstitutionRequest", "institution"), ("institutions.ImpersonationSession", "institution"),
    ("qa_core.TimelineEvent", "institution"),
]


def _perm(label):
    app, codename = label.split(".", 1)
    return Permission.objects.filter(content_type__app_label=app, codename=codename).first()


class Stats:
    def __init__(self):
        self.created = OrderedDict()
        self.totals = OrderedDict()
        self.notes = []
        self.deferred_perms = set()
        self.grants_added = 0
        self.credentials = []

    def add(self, label, created=0, total=None):
        self.created[label] = self.created.get(label, 0) + created
        if total is not None:
            self.totals[label] = total

    @property
    def changes(self):
        return sum(self.created.values()) + self.grants_added


class Command(BaseCommand):
    help = "Seed the NBTE QA startup data (idempotent). See Master Plan §15."

    def add_arguments(self, parser):
        parser.add_argument("--with-demo", action="store_true",
                            help="Also create the demo institution and demo accounts (tagged).")
        parser.add_argument("--reset-demo", action="store_true",
                            help="Delete exactly the rows tagged as demo, then stop.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Run inside a transaction that is rolled back; report only.")
        parser.add_argument("--i-understand-production", action="store_true",
                            help="Allow --reset-demo when DEBUG is false.")
        parser.add_argument("--skip-base-seeders", action="store_true",
                            help="Do not run the existing seeders first (they are idempotent; "
                                 "this only saves time in tests).")

    # ------------------------------------------------------------------
    def handle(self, *args, **opts):
        if opts["reset_demo"]:
            if not settings.DEBUG and not opts["i_understand_production"]:
                raise CommandError("--reset-demo is refused when DEBUG is false. "
                                   "Add --i-understand-production if you really mean it.")
            self._reset_demo(dry_run=opts["dry_run"])
            if not opts["with_demo"]:
                return

        stats = Stats()
        with transaction.atomic():
            if not opts["skip_base_seeders"]:
                self._base_seeders(stats)
            self._roles(stats)
            self._groups(stats)
            self._departments(stats)
            self._settings(stats)
            self._reference_formats(stats)
            self._templates(stats)
            self._policies(stats)
            self._flows(stats)
            self._patch_flows(stats)
            self._lookups(stats)
            self._documents(stats)
            self._fees(stats)
            self._phase3(stats)
            self._phase4(stats)
            self._phase7(stats)
            if opts["with_demo"]:
                self._demo(stats)
                self._demo_phase2(stats)
                self._demo_phase3(stats)
                self._demo_phase6(stats)
                self._demo_phase7(stats)
            if opts["dry_run"]:
                transaction.set_rollback(True)
        self._print(stats, dry_run=opts["dry_run"], with_demo=opts["with_demo"])

    # ------------------------------------------------------------------
    # Existing seeders (§15: "It calls the existing seeders first")
    # ------------------------------------------------------------------
    def _base_seeders(self, stats):
        for name, args in (("seed_roles", ["--sync"]), ("seed_config", []), ("seed_departments", []),
                           ("seed_default_groups", []), ("seed_nbte", ["--with-lgas", "--quiet"])):
            buffer = io.StringIO()
            call_command(name, *args, stdout=buffer, stderr=io.StringIO())
            output = buffer.getvalue()
            lines = output.splitlines()
            if name == "seed_nbte":
                created = 0 if "no changes" in output else 1
            elif name == "seed_default_groups":
                created = sum(1 for line in lines if re.search(r"\bcreated\b", line))
            else:
                created = sum(1 for line in lines if line.startswith("created "))
            stats.notes.append(f"{name} {' '.join(args)}".strip()
                               + (" — up to date" if not created else " — applied"))

    # ------------------------------------------------------------------
    def _roles(self, stats):
        created = 0
        for code, name, rank, max_open, _user_type, description in seed_data.NEW_ROLES:
            _role, was_created = Role.objects.get_or_create(
                code=code,
                defaults={"name": name, "rank": rank, "max_open_tasks": max_open,
                          "description": description, "is_system": True},
            )
            created += was_created
        merged = {code: list(labels) for code, labels in seed_data.ROLE_PERMISSIONS.items()}
        for code, labels in (list(p2.ROLE_PERMISSIONS.items()) + list(p3.ROLE_PERMISSIONS.items())
                             + list(p4.ROLE_PERMISSIONS.items()) + list(p5.ROLE_PERMISSIONS.items())
                             + list(p6.ROLE_PERMISSIONS.items()) + list(p7.ROLE_PERMISSIONS.items())):
            merged.setdefault(code, [])
            merged[code] += [label for label in labels if label not in merged[code]]
        for code, labels in merged.items():
            role = Role.objects.filter(code=code).first()
            if role is None:
                continue
            have = set(role.permissions.values_list("pk", flat=True))
            for label in labels:
                perm = _perm(label)
                if perm is None:
                    stats.deferred_perms.add(label)
                    continue
                if perm.pk not in have:
                    role.permissions.add(perm)
                    stats.grants_added += 1
        stats.add("Roles", created, Role.objects.count())

    def _groups(self, stats):
        created = 0
        for name, description, labels, role_codes in seed_data.GROUPS:
            perms = []
            for label in labels:
                perm = _perm(label)
                if perm is None:
                    stats.deferred_perms.add(label)
                else:
                    perms.append(perm)
            group, was_created = Group.objects.get_or_create(name=name)
            if not was_created:
                # Never overwrite a group that already exists (§12) — but a
                # permission that was "deferred" when the group was created
                # (its app had no models yet) is added once it exists. Nothing
                # is ever removed.
                have = set(group.permissions.values_list("pk", flat=True))
                for perm in perms:
                    if perm.pk not in have and perm.content_type.app_label in ("institutions", "payments",
                                                                               "accreditation", "inspection",
                                                                               "complaints"):
                        group.permissions.add(perm)
                        stats.grants_added += 1
                continue
            created += 1
            GroupProfile.objects.get_or_create(group=group, defaults={"description": description})
            group.permissions.set(perms)
            for code in role_codes:
                role = Role.objects.filter(code=code).first()
                if role is not None:
                    role.groups.add(group)
        for name, labels in (list(p3.GROUP_PERMISSIONS.items()) + list(p4.GROUP_PERMISSIONS.items())
                             + list(p6.GROUP_PERMISSIONS.items()) + list(p7.GROUP_PERMISSIONS.items())):
            group = Group.objects.filter(name=name).first()
            if group is None:
                continue
            have = set(group.permissions.values_list("pk", flat=True))
            for label in labels:
                perm = _perm(label)
                if perm is None:
                    stats.deferred_perms.add(label)
                elif perm.pk not in have:
                    group.permissions.add(perm)
                    stats.grants_added += 1
        stats.add("Groups", created, Group.objects.count())

    def _departments(self, stats):
        created = 0
        for code, name, alias_codes in seed_data.DEPARTMENTS:
            existing = (Department.objects.filter(code=code).first()
                        or Department.objects.filter(code__in=alias_codes).first()
                        or Department.objects.filter(name__iexact=name).first())
            if existing is None:
                Department.objects.create(code=code, name=name, sort_order=500)
                created += 1
        stats.add("Departments", created, Department.objects.count())

    def _settings(self, stats):
        created = 0
        for key, value, vtype, group, desc, editable_by, dual in SETTINGS:
            value = value() if callable(value) else value
            _entry, was_created = ConfigEntry.objects.get_or_create(
                key=key, scope="global",
                defaults={"value": value, "value_type": vtype, "group": group, "description": desc,
                          "editable_by": editable_by, "dual_control": dual},
            )
            created += was_created
        stats.add("Settings", created, ConfigEntry.objects.filter(key__startswith="qa.").count())

    def _reference_formats(self, stats):
        created = 0
        for code, name, pattern, reset, check, allowed, scope in seed_data.REFERENCE_FORMATS + p3.REFERENCE_FORMATS:
            _fmt, was_created = ReferenceFormat.objects.get_or_create(
                code=code,
                defaults={"name": name, "pattern": pattern, "reset_period": reset, "check_digit": check,
                          "allowed_chars": allowed, "sequence_scope": scope},
            )
            created += was_created
        # Phase 3 data fix: the QA request series repeated the onboarding
        # pattern; switch it while it has never issued a number.
        fmt = ReferenceFormat.objects.filter(code="qa_request", pattern=p3.QA_REQUEST_PATTERN_OLD).first()
        if fmt is not None and not fmt.counters.exists():
            fmt.pattern = p3.QA_REQUEST_PATTERN_NEW
            fmt.save()
            stats.notes.append("Reference format qa_request: NBTE/QA/… → NBTE/QAR/… (it repeated the onboarding "
                               "pattern; no number had been issued).")
        stats.add("Reference formats", created, ReferenceFormat.objects.count())

    def _templates(self, stats):
        created = 0
        for code, channel, subject, body, description in (seed_data.TEMPLATES + p2.TEMPLATES + p3.TEMPLATES
                                                          + p4.TEMPLATES + p5.TEMPLATES + p7.TEMPLATES):
            _t, was_created = NotificationTemplate.objects.get_or_create(
                code=code, channel=channel,
                defaults={"subject": subject, "body": body, "description": description},
            )
            created += was_created
        rules_created = 0
        for event, recipient_kind, template_code, channel in (seed_data.RULES + p2.RULES + p3.RULES + p4.RULES
                                                              + p5.RULES + p7.RULES):
            template = NotificationTemplate.objects.get(code=template_code, channel=channel)
            if not NotificationRule.objects.filter(event_code=event, recipient_kind=recipient_kind,
                                                   template=template).exists():
                NotificationRule.objects.create(event_code=event, recipient_kind=recipient_kind,
                                                template=template)
                rules_created += 1
        stats.add("Templates", created, NotificationTemplate.objects.count())
        stats.add("Notification rules", rules_created, NotificationRule.objects.count())

    def _policies(self, stats):
        created = 0
        for row in seed_data.ACTION_POLICIES + p2.ACTION_POLICIES:
            data = dict(row)
            code = data.pop("action_code")
            _p, was_created = ActionPolicy.objects.get_or_create(action_code=code, defaults=data)
            created += was_created
        # Phase 3: "Add Programme to QA" goes live (it was seeded disabled in Phase 2).
        for patch, phase in ([(dict(p3.ADD_PROGRAMME_POLICY), "Phase 3"), (dict(p4.SEND_BACK_POLICY), "Phase 4")]
                             + [(dict(p), "Phase 5") for p in p5.POLICY_PATCHES]):
            code = patch.pop("action_code")
            policy = ActionPolicy.objects.filter(action_code=code).first()
            if policy is not None and not policy.is_enabled and (policy.description or "").startswith("Needs Phase"):
                policy.is_enabled = True
                policy.description = patch["description"]
                policy.save()
                stats.notes.append(f"Action policy {code} enabled ({phase}).")
        stats.add("Action policies", created, ActionPolicy.objects.count())

    def _flows(self, stats):
        created = stages_created = transitions_created = 0
        self._task_categories(stats)
        from tasks.models import TaskCategory

        for spec in seed_data.FLOWS + p2.FLOWS + p3.FLOWS + p4.FLOWS + p5.FLOWS + p7.FLOWS:
            if FlowDefinition.objects.filter(code=spec["code"]).exists():
                continue  # published flows are edited by versioning, never re-seeded
            flow = FlowDefinition.objects.create(
                code=spec["code"], name=spec["name"], description=spec["description"],
                subject_model=spec["subject_model"], version=1, is_active=True,
                published_at=timezone.now(),
            )
            created += 1
            by_code = {}
            for order, (code, label, public, kind, actor_kind, extra) in enumerate(spec["stages"]):
                extra = dict(extra)
                scope = extra.get("actor_scope", "")
                if scope and scope not in ("owning", "institution_home"):
                    dept = qa_department(scope)
                    extra["actor_scope"] = dept.code if dept is not None else scope
                if "actor_role" in extra:  # Phase 4: role code → Role
                    extra["actor_role"] = Role.objects.filter(code=extra["actor_role"]).first()
                if "task_category" in extra:
                    extra["task_category"] = TaskCategory.objects.filter(code=extra["task_category"]).first()
                by_code[code] = FlowStage.objects.create(
                    flow=flow, code=code, label=label, public_label=public, kind=kind,
                    actor_kind=actor_kind, sort_order=(order + 1) * 10, **extra)
                stages_created += 1
            for order, (src, dst, action, label, extra) in enumerate(spec["transitions"]):
                FlowTransition.objects.create(
                    flow=flow, from_stage=by_code[src], to_stage=by_code[dst], action_code=action,
                    label=label, sort_order=(order + 1) * 10, **extra)
                transitions_created += 1
        stats.add("Flows", created, FlowDefinition.objects.filter(published_at__isnull=False).count())
        stats.add("Stages", stages_created, FlowStage.objects.count())
        stats.add("Transitions", transitions_created, FlowTransition.objects.count())

    # ------------------------------------------------------------------
    # Demo
    # ------------------------------------------------------------------
    def _demo(self, stats):
        from institutions.models import Institution

        spec = seed_data.DEMO_INSTITUTION
        dept = Department.objects.filter(code=spec["department"]).first()
        if dept is None:
            raise CommandError(f"Department '{spec['department']}' is missing; run without --skip-base-seeders.")
        institution = Institution.unscoped.filter(code=spec["code"]).first()
        inst_created = 0
        if institution is None:
            institution = Institution.unscoped.create(code=spec["code"], name=spec["name"],
                                                      handling_department=dept, status=spec["status"])
            inst_created = 1
            DemoTag.tag(institution)
        users_created = 0
        for email, first, last, user_type, dept_code, role_code, extra_groups in seed_data.DEMO_USERS:
            user = User.objects.filter(email__iexact=email).first()
            if user is not None:
                stats.credentials.append((email, None, role_code))
                continue
            password = secrets.token_urlsafe(12)
            user = User(
                email=email, first_name=first, last_name=last, user_type=user_type,
                role=Role.objects.filter(code=role_code).first(),
                department=Department.objects.filter(code=dept_code).first() if dept_code else None,
                institution=institution if user_type == "institution" else None,
                must_change_password=True, is_active=True, is_staff=False,
            )
            user.set_password(password)
            user.save()
            for group_name in extra_groups:
                group = Group.objects.filter(name=group_name).first()
                if group is not None:
                    user.groups.add(group)
            DemoTag.tag(user)
            users_created += 1
            stats.credentials.append((email, password, role_code))
        stats.add("Demo institutions", inst_created, 1)
        stats.add("Demo users", users_created, len(seed_data.DEMO_USERS))

    # ------------------------------------------------------------------
    # Phase 2 blocks
    # ------------------------------------------------------------------
    def _patch_flows(self, stats):
        """Additive data fix for the Phase 1 payment flow: a part payment can
        also be confirmed from `finance_confirmation_overdue`. Adds the missing
        transition row only; nothing existing is changed."""
        added = 0
        for flow in FlowDefinition.objects.filter(code="payment"):
            src = flow.stages.filter(code="finance_confirmation_overdue").first()
            dst = flow.stages.filter(code="partially_paid").first()
            if src is None or dst is None:
                continue
            if not FlowTransition.objects.filter(from_stage=src, action_code="final_confirm_partial").exists():
                FlowTransition.objects.create(flow=flow, from_stage=src, to_stage=dst,
                                              action_code="final_confirm_partial",
                                              label="Confirm part payment (Director of Finance)",
                                              sort_order=999, **seed_data.FINAL)
                added += 1
        # Phase 5: qa_case stage 12 is driven by the post-visit report sub-flow.
        for flow in FlowDefinition.objects.filter(code="qa_case"):
            stage = flow.stages.filter(code=p5.CASE_STAGE12_PATCH["stage"]).first()
            dst = flow.stages.filter(code=p5.CASE_STAGE12_TRANSITION[1]).first()
            if stage is None or dst is None:
                continue
            if stage.creates_task:
                stage.creates_task = False
                stage.save(update_fields=["creates_task"])
                stats.notes.append("qa_case stage 12 now runs on the post-visit report sub-flow (Phase 5).")
            src_code, _dst, action, label, extra = p5.CASE_STAGE12_TRANSITION
            if not FlowTransition.objects.filter(from_stage=stage, action_code=action).exists():
                FlowTransition.objects.create(flow=flow, from_stage=stage, to_stage=dst, action_code=action,
                                              label=label, sort_order=995, **extra)
                added += 1
        stats.add("Transitions", added)

    def _lookups(self, stats):
        from institutions.models import (
            Discipline, InstitutionCategory, InstitutionOwnership, InstitutionRequestType, InstitutionType,
            ProgrammeCategory, ProgrammeLevel,
        )

        created = 0
        for order, (code, name, is_public) in enumerate(p2.OWNERSHIPS):
            _o, c = InstitutionOwnership.objects.get_or_create(
                code=code, defaults={"name": name, "is_public": is_public, "sort_order": (order + 1) * 10})
            created += c
        types = {}
        for order, (code, name, dept_code) in enumerate(p2.INSTITUTION_TYPES):
            types[code], c = InstitutionType.objects.get_or_create(
                code=code, defaults={"name": name, "default_department": qa_department(dept_code),
                                     "sort_order": (order + 1) * 10})
            created += c
        for order, (code, name, type_code, dept_code) in enumerate(p2.INSTITUTION_CATEGORIES):
            _c, c = InstitutionCategory.objects.get_or_create(
                code=code, defaults={"name": name, "institution_type": types.get(type_code),
                                     "routing_department": qa_department(dept_code),
                                     "sort_order": (order + 1) * 10})
            created += c
        disciplines = {}
        for order, (code, name) in enumerate(p2.DISCIPLINES):
            disciplines[code], c = Discipline.objects.get_or_create(
                code=code, defaults={"name": name, "sort_order": (order + 1) * 10})
            created += c
        for order, (code, name) in enumerate(p2.PROGRAMME_LEVELS):
            _l, c = ProgrammeLevel.objects.get_or_create(code=code, defaults={"name": name,
                                                                               "sort_order": (order + 1) * 10})
            created += c
        for name, discipline, per_stream, tech in p2.PROGRAMME_CATEGORIES:
            _p, c = ProgrammeCategory.objects.get_or_create(
                name=name, defaults={"discipline": disciplines.get(discipline), "students_per_stream": per_stream,
                                     "is_technology_based": tech})
            created += c
        for code, name, enabled in p2.REQUEST_TYPES:
            _r, c = InstitutionRequestType.objects.get_or_create(code=code, defaults={"name": name,
                                                                                      "is_enabled": enabled})
            created += c
        stats.add("Lookups", created, InstitutionOwnership.objects.count() + InstitutionType.objects.count()
                  + InstitutionCategory.objects.count() + Discipline.objects.count()
                  + ProgrammeLevel.objects.count() + ProgrammeCategory.objects.count())

    def _documents(self, stats):
        from institutions.models import DocumentKind, DocumentRequirement, InstitutionOwnership, InstitutionType

        kinds = {}
        created = 0
        for order, (code, name, desc, exts, max_mb) in enumerate(p2.DOCUMENT_KINDS):
            kinds[code], c = DocumentKind.objects.get_or_create(
                code=code, defaults={"name": name, "description": desc, "accepted_extensions": exts,
                                     "max_mb": max_mb, "sort_order": (order + 1) * 10})
            created += c
        rules = 0
        for process, own, type_code, kind_code, mandatory, dept_code in p2.DOCUMENT_REQUIREMENTS:
            ownership = InstitutionOwnership.objects.filter(code=own).first() if own else None
            itype = InstitutionType.objects.filter(code=type_code).first() if type_code else None
            kind = kinds.get(kind_code) or DocumentKind.objects.filter(code=kind_code).first()
            if kind is None or (own and ownership is None) or (type_code and itype is None):
                continue
            _r, c = DocumentRequirement.objects.get_or_create(
                process=process, ownership=ownership, institution_type=itype, document_kind=kind,
                defaults={"is_mandatory": mandatory, "reviewing_department": qa_department(dept_code)})
            rules += c
        stats.add("Document kinds", created, DocumentKind.objects.count())
        stats.add("Document rules", rules, DocumentRequirement.objects.count())

    def _fees(self, stats):
        from payments.models import BankAccount, FeeSchedule

        bank_created = 0
        if not BankAccount.objects.exists():
            BankAccount.objects.create(**p2.PLACEHOLDER_BANK)
            bank_created = 1
        placeholder = BankAccount.objects.filter(is_placeholder=True).first()
        created = 0
        for code, name, description, formula, confirmed in p2.FEE_SCHEDULES:
            _f, c = FeeSchedule.objects.get_or_create(
                code=code, defaults={"name": name, "description": description, "formula": formula,
                                     "confirmed_setting": confirmed, "revenue_account": placeholder})
            created += c
        stats.add("Fee schedules", created, FeeSchedule.objects.count())
        stats.add("Bank accounts", bank_created, BankAccount.objects.count())

    # ------------------------------------------------------------------
    # Phase 3 blocks
    # ------------------------------------------------------------------
    def _phase3(self, stats):
        """QA types, the two SSQ templates (published as version 1) and the
        starter curriculum catalogue. Existing rows are never changed."""
        from accreditation import builder
        from accreditation.models import CatalogueItem, QAType, SSQTemplate
        from institutions.models import ProgrammeCategory
        from payments.models import FeeSchedule

        fee = FeeSchedule.objects.filter(code="qa_request_processing").first()
        created = 0
        for order, (code, name, desc, statuses, window, new, admin_only) in enumerate(p3.QA_TYPES):
            _t, was_created = QAType.objects.get_or_create(code=code, defaults={
                "name": name, "description": desc, "eligible_programme_statuses": statuses,
                "uses_reassessment_window": window, "includes_new_programmes": new,
                "admin_programme_only": admin_only, "fee_schedule": fee, "sort_order": (order + 1) * 10})
            created += was_created
        stats.add("QA types", created, QAType.objects.count())
        created = 0
        for type_code, name, schema in p3.SSQ_TEMPLATES:
            qa_type = QAType.objects.filter(code=type_code).first()
            if qa_type is None or SSQTemplate.objects.filter(qa_type=qa_type).exists():
                continue
            template = SSQTemplate.objects.create(qa_type=qa_type, version=1, name=name, schema=schema,
                                                  notes="Seeded starting point — DPAP to refine (new version).")
            builder.publish(template, user=None, system=True)
            created += 1
        stats.add("SSQ templates", created, SSQTemplate.objects.count())
        created = 0
        for order, (programme, category, kind, name, quantity, unit) in enumerate(p3.CATALOGUE):
            cat = ProgrammeCategory.objects.filter(name=category).first()
            if CatalogueItem.objects.filter(programme_name=programme, name=name).exists():
                continue
            CatalogueItem.objects.create(category=cat, programme_name=programme, kind=kind, name=name,
                                         required_quantity=quantity, unit=unit, sort_order=(order + 1) * 10)
            created += 1
        stats.add("Catalogue items", created, CatalogueItem.objects.count())

    # ------------------------------------------------------------------
    # Phase 4 blocks
    # ------------------------------------------------------------------
    def _task_categories(self, stats):
        from tasks.models import TaskCategory

        created = 0
        for order, (code, name, description) in enumerate(p4.TASK_CATEGORIES + p7.TASK_CATEGORIES):
            _c, was_created = TaskCategory.objects.get_or_create(code=code, defaults={
                "name": name, "description": description, "requires_review": False, "default_due_days": 3,
                "default_priority": "high", "icon": "shield", "colour": "#0b6b3a", "sort_order": 900 + order})
            created += was_created
        stats.add("Task categories", created, TaskCategory.objects.filter(code__startswith="qa-").count())

    def _phase4(self, stats):
        """Rate table (placeholder amounts) and the QA system user."""
        from accreditation.models import RateTable
        from qa_core import task_bridge

        created = 0
        for order, (code, role, kind, amount, unit) in enumerate(p4.RATES):
            _r, was_created = RateTable.objects.get_or_create(code=code, department=None, defaults={
                "role": role, "allowance_type": kind, "amount": amount, "unit": unit, "sort_order": (order + 1) * 10})
            created += was_created
        stats.add("Rate table", created, RateTable.objects.count())
        task_bridge.system_user()

    # ------------------------------------------------------------------
    # Phase 7 blocks: complaint layers, routing, escalation, knowledge base
    # ------------------------------------------------------------------
    def _phase7(self, stats):
        from accounts.models import Role as _Role
        from complaints.models import ComplaintLayer, EscalationRule, KnowledgeArticle, RoutingRule

        created = 0
        layers = {}
        for order, (code, name, description, applies, priority, fields) in enumerate(p7.LAYERS):
            layers[code], was_created = ComplaintLayer.objects.get_or_create(code=code, defaults={
                "name": name, "description": description, "applies_to_user_types": applies,
                "default_priority": priority, "custom_fields": fields, "sort_order": (order + 1) * 10})
            created += was_created
        stats.add("Complaint layers", created, ComplaintLayer.objects.count())
        routed = 0
        if not RoutingRule.objects.exists():  # seeded once; afterwards the table belongs to the admins
            for layer_code, stage, condition, kind, dept_code, role_code, order, note in p7.ROUTING:
                RoutingRule.objects.create(
                    layer=layers.get(layer_code) or ComplaintLayer.objects.filter(code=layer_code).first(),
                    stage_code=stage, condition=condition, target_kind=kind,
                    target_department=qa_department(dept_code) if dept_code else None,
                    target_role=_Role.objects.filter(code=role_code).first() if role_code else None,
                    order=order, note=note)
                routed += 1
        stats.add("Routing rules", routed, RoutingRule.objects.count())
        escalated = 0
        if not EscalationRule.objects.exists():
            for level, minutes, kind, only_if in p7.ESCALATION:
                EscalationRule.objects.create(level=level, after_breach_minutes=minutes, target_kind=kind,
                                              only_if=only_if)
                escalated += 1
        stats.add("Escalation rules", escalated, EscalationRule.objects.count())
        articles = 0
        for title, slug, summary, keywords, layer_code, audience, body in p7.KB_ARTICLES:
            _a, was_created = KnowledgeArticle.objects.get_or_create(slug=slug, defaults={
                "title": title, "summary": summary, "keywords": keywords, "audience": audience, "body": body,
                "layer": layers.get(layer_code)})
            articles += was_created
        stats.add("Knowledge articles", articles, KnowledgeArticle.objects.count())

    def _demo_phase7(self, stats):
        """Three open demo complaints (on track, near breach, breached and escalated)."""
        from complaints.demo import build_demo

        result = build_demo()
        stats.add("Demo complaints", result.get("created", 0), result.get("total", 0))

    def _demo_phase6(self, stats):
        """Phase 6: two example professional bodies and their mappings (demo
        only: the e-mail addresses are placeholders)."""
        from institutions.models import ProfessionalBody, ProgrammeBodyMapping, ProgrammeLevel

        created = 0
        for spec in p6.DEMO_BODIES:
            spec = dict(spec)
            programmes = spec.pop("programmes")
            body, was_created = ProfessionalBody.objects.get_or_create(name=spec["name"], defaults=spec)
            created += was_created
            for name, level_code in programmes:
                level = ProgrammeLevel.objects.filter(code=level_code).first() if level_code else None
                if level_code and level is None:
                    continue
                if not ProgrammeBodyMapping.objects.filter(body=body, programme_name__iexact=name, level=level).exists():
                    ProgrammeBodyMapping.objects.create(body=body, programme_name=name, level=level)
        stats.add("Professional bodies (demo)", created, ProfessionalBody.objects.count())

    def _demo_phase3(self, stats):
        from qa_core.demo import build_phase3_demo

        result = build_phase3_demo(stats, self._make_demo_user)
        stats.add("Demo programmes", result.get("programmes", 0))
        stats.add("Demo documents", result.get("documents", 0))
        stats.add("Demo users", result.get("users", 0))

    def _demo_phase2(self, stats):
        from qa_core.demo import build_phase2_demo

        users_before = len(stats.credentials)
        result = build_phase2_demo(stats, self._make_demo_user)
        stats.add("Demo institutions", result.get("institutions", 0), 2)
        stats.add("Demo programmes", result.get("programmes", 0), result.get("programme_total", 0))
        stats.add("Demo obligations", result.get("obligations", 0), result.get("obligation_total", 0))
        stats.add("Demo users", result.get("users", 0), len(seed_data.DEMO_USERS) + len(p2.DEMO_USERS))
        return users_before

    def _make_demo_user(self, stats, email, first, last, user_type, dept_code, role_code, extra_groups,
                        institution=None):
        user = User.objects.filter(email__iexact=email).first()
        if user is not None:
            stats.credentials.append((email, None, role_code))
            return user, False
        password = secrets.token_urlsafe(12)
        user = User(
            email=email, first_name=first, last_name=last, user_type=user_type,
            role=Role.objects.filter(code=role_code).first(),
            department=qa_department(dept_code) if dept_code else None,
            institution=institution if user_type == "institution" else None,
            must_change_password=True, is_active=True, is_staff=False,
        )
        user.set_password(password)
        user.save()
        for group_name in extra_groups:
            group = Group.objects.filter(name=group_name).first()
            if group is not None:
                user.groups.add(group)
        DemoTag.tag(user)
        stats.credentials.append((email, password, role_code))
        return user, True

    def _collect_demo_dependents(self, by_label):
        """Add every row hanging off a demo institution or demo user to the
        reset set (walkthrough activity: applications, bills, documents …)."""
        from django.apps import apps

        inst_ids = by_label.get("institutions.institution", [])
        user_ids = by_label.get("accounts.user", [])
        if not inst_ids and not user_ids:
            return
        subjects = []
        if inst_ids:
            for label, path in DEMO_DEPENDENTS:
                model = apps.get_model(label)
                pks = [str(pk) for pk in model.unscoped.filter(**{f"{path}_id__in": inst_ids})
                       .values_list("pk", flat=True)]
                key = label.lower()
                if pks:
                    existing = set(by_label.get(key, []))
                    by_label.setdefault(key, []).extend(pk for pk in pks if pk not in existing)
                    subjects.append((model, pks))
            subjects.append((apps.get_model("institutions.Institution"), list(inst_ids)))
            members = [str(pk) for pk in User.objects.filter(institution_id__in=inst_ids).values_list("pk", flat=True)]
            existing = set(by_label.get("accounts.user", []))
            by_label.setdefault("accounts.user", []).extend(pk for pk in members if pk not in existing)
        from qa_core.models import FlowInstance, PendingAction, QATaskLink

        # Phase 4: the stage tasks of demo QA cases go with them.
        for app_label, model_name in (("accreditation", "qacase"), ("inspection", "postvisitreport"),
                                      ("inspection", "inspection")):
            subject_pks = by_label.get(f"{app_label}.{model_name}", [])
            if not subject_pks:
                continue
            ct_subject = ContentType.objects.get(app_label=app_label, model=model_name)
            task_pks = [str(pk) for pk in QATaskLink.objects.filter(content_type=ct_subject,
                                                                    object_id__in=subject_pks)
                        .values_list("task_id", flat=True)]
            existing = set(by_label.get("tasks.task", []))
            by_label.setdefault("tasks.task", []).extend(pk for pk in task_pks if pk not in existing)
        flow_pks = []
        for model, pks in subjects:
            ct = ContentType.objects.get_for_model(model, for_concrete_model=True)
            flow_pks += [str(pk) for pk in FlowInstance.objects.filter(content_type=ct, object_id__in=pks)
                         .values_list("pk", flat=True)]
        if flow_pks:
            existing = set(by_label.get("qa_core.flowinstance", []))
            by_label.setdefault("qa_core.flowinstance", []).extend(pk for pk in flow_pks if pk not in existing)
        all_users = by_label.get("accounts.user", [])
        if all_users:
            pa = [str(pk) for pk in PendingAction.objects.filter(initiated_by_id__in=all_users)
                  .values_list("pk", flat=True)]
            existing = set(by_label.get("qa_core.pendingaction", []))
            by_label.setdefault("qa_core.pendingaction", []).extend(pk for pk in pa if pk not in existing)

    def _reset_demo(self, dry_run=False):
        counts = OrderedDict()
        try:
            with transaction.atomic():
                tags = list(DemoTag.objects.select_related("content_type"))
                by_label = OrderedDict()
                for tag in tags:
                    label = f"{tag.content_type.app_label}.{tag.content_type.model}"
                    by_label.setdefault(label, []).append(tag.object_id)
                self._collect_demo_dependents(by_label)
                ordered = [l for l in RESET_ORDER if l in by_label] + \
                          [l for l in by_label if l not in RESET_ORDER]
                for label in ordered:
                    app_label, model_name = label.split(".")
                    ct = ContentType.objects.get(app_label=app_label, model=model_name)
                    model = ct.model_class()
                    if model is None:
                        continue
                    manager = getattr(model, "unscoped", None) or model._base_manager
                    removed = 0
                    if label in ("accreditation.submission", "inspection.servicerecord"):
                        # Snapshots refuse obj.delete(); a demo reset removes them in bulk.
                        removed, _ = manager.filter(pk__in=by_label[label]).delete()
                        counts[label] = removed
                        continue
                    for obj in manager.filter(pk__in=by_label[label]):
                        if hasattr(obj, "hard_delete"):
                            obj.hard_delete()
                        else:
                            obj.delete()
                        removed += 1
                    counts[label] = removed
                DemoTag.objects.filter(pk__in=[t.pk for t in tags]).delete()
                if dry_run:
                    transaction.set_rollback(True)
        except ProtectedError as exc:
            blockers = sorted({f"{o._meta.label}#{o.pk}" for o in exc.protected_objects})[:10]
            raise CommandError("Demo rows are still referenced by untagged data, so nothing was deleted: "
                               + ", ".join(blockers)) from None
        total = sum(counts.values())
        self.stdout.write(self.style.SUCCESS(
            f"NBTE QA — demo reset {'(dry run, rolled back) ' if dry_run else ''}complete: "
            f"{total} tagged row(s) removed."))
        for label, n in counts.items():
            self.stdout.write(f"  {label:<32}{n:>5}")
        if not counts:
            self.stdout.write("  No demo rows were tagged.")

    # ------------------------------------------------------------------
    # Summary (§15)
    # ------------------------------------------------------------------
    def _print(self, stats, *, dry_run, with_demo):
        w = self.stdout.write

        def fmt(label):
            total = stats.totals.get(label, "?")
            return f"{label}: {total} (+{stats.created.get(label, 0)})"

        lgas = 0
        lga_file = Path(settings.BASE_DIR) / "staff" / "data" / "lgas.json"
        try:
            states = json.loads(lga_file.read_text())["states"]
            lgas = sum(len(v) for v in (states.values() if isinstance(states, dict) else states))
        except Exception:
            pass
        qa_perms = Permission.objects.filter(content_type__app_label="qa_core").exclude(
            codename__regex=r"^(add|change|delete|view)_(flowdefinition|flowstage|flowtransition|flowinstance|"
                            r"flowevent|actionpolicy|pendingaction|referenceformat|referencecounter|qataskl"
                            r"ink|timelineevent|suspension|notificationtemplate|notificationrule|demotag)$")

        title = "NBTE QA — seed_startup complete" + (" (DRY RUN — rolled back)" if dry_run else "")
        w(self.style.SUCCESS(title))
        w(f"  {fmt('Roles')}  {fmt('Groups')}  QA permissions: {qa_perms.count()} "
          f"(role grants +{stats.grants_added}; {len(stats.deferred_perms)} deferred to later phases)")
        w(f"  {fmt('Departments')}  States: {StateOfOrigin.objects.count()}  LGAs: {lgas}  "
          f"Designations: {Designation.objects.count()}")
        w(f"  {fmt('Flows')} published  {fmt('Stages')}  {fmt('Transitions')}")
        w(f"  {fmt('Settings')}  {fmt('Reference formats')}  {fmt('Templates')}  "
          f"{fmt('Notification rules')}  {fmt('Action policies')}")
        for note in stats.notes:
            w(f"  · {note}")
        locked = ConfigEntry.objects.filter(key="qa.payment.provider_confirmation_is_evidence_only").first()
        if locked is not None:
            w("  Locked: qa.payment.provider_confirmation_is_evidence_only = true (Fixed by policy)")
        confirmed = ConfigEntry.objects.filter(key="qa.fee.qa_request_processing.confirmed").first()
        if confirmed is not None and not confirmed.parsed():
            w(self.style.WARNING("  !! qa.fee.qa_request_processing is UNCONFIRMED — SOP-02 bills are blocked."))
        w(f"  {fmt('QA types')}  {fmt('SSQ templates')} (published v1 per type)  {fmt('Catalogue items')}")
        w(f"  {fmt('Rate table')} (placeholder amounts until Finance confirms)  {fmt('Task categories')}")
        w(f"  {fmt('Complaint layers')}  {fmt('Routing rules')}  {fmt('Escalation rules')}  "
          f"{fmt('Knowledge articles')}")
        w(f"  {fmt('Lookups')}  {fmt('Document kinds')}  {fmt('Document rules')}  {fmt('Fee schedules')}  "
          f"{fmt('Bank accounts')}")
        try:
            from payments import providers
            from payments.models import BankAccount

            methods = []
            for row in providers.status_report():
                state = "ENABLED" if row["setting"] else "off"
                if row["setting"] and row["missing"] and not row["simulated"]:
                    state += ", MISSING ENV: " + ", ".join(row["missing"])
                elif row["simulated"]:
                    state += ", SIMULATED (dev)"
                methods.append(f"{row['code']}[{state}]")
            w("  Payment methods: " + " ".join(methods))
            if not BankAccount.objects.filter(is_active=True, is_placeholder=False).exists():
                w(self.style.WARNING("  !! Bank account is a placeholder — slips cannot be issued until Finance sets one "
                                     "(Settings → QA → Fees and bank accounts; needs a second officer)."))
        except Exception:  # never fail the summary
            pass
        w("  (Backups are reported from Phase 8.)")
        if with_demo:
            demo_users = DemoTag.objects.filter(content_type=ContentType.objects.get_for_model(User)).count()
            w(f"  Demo: {stats.totals.get('Demo institutions', 0)} institutions, "
              f"{stats.totals.get('Demo programmes', 0)} programmes, "
              f"{stats.totals.get('Demo obligations', 0)} obligations, {demo_users} users "
              f"(+{stats.created.get('Demo users', 0)} new; tagged; remove with --reset-demo)")
            if "Demo complaints" in stats.totals:
                w(f"  Demo complaints: {stats.totals['Demo complaints']} open (on track, near breach, escalated)")
            new_creds = [c for c in stats.credentials if c[1]]
            if new_creds:
                w(self.style.WARNING("  Demo credentials — shown ONCE. Change them at first sign-in "
                                     "(forced) or remove them with --reset-demo:"))
                for email, password, role in new_creds:
                    w(f"    {email:<36} {password:<20} {role}")
            existing = [c for c in stats.credentials if not c[1]]
            if existing:
                w("  Existing demo accounts kept (passwords are never shown again): "
                  + ", ".join(c[0] for c in existing))
        w(f"  Changes this run: {stats.changes}" + (" — already up to date." if not stats.changes else "."))
        w("Next steps:")
        w("  1. Point the QA portal subdomain (QA_PORTAL_HOST, e.g. qa.nbte.gov.ng) at this server.")
        w("  2. Assign roles: Finance officers → qa-finance-officer; Director of Finance → qa-finance-director;")
        w("     QA settings admins → qa-settings-admin; ICT monitor → ict-qa-monitor.")
        w("  3. Review Settings → QA (fee formulas need a second officer to change).")
