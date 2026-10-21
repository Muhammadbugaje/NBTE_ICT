"""The three §6 two-officer actions that need Phase 5 records (now live):
Edit QA Personnel, Edit QA Mapping and Override Post-Visit Personnel.

Policies (who initiates / confirms, minimum rank, department rule) are
ActionPolicy data; what each action does is here. Every handler re-validates
at confirmation; Edit QA Mapping re-checks every §9 rule under the lock.
"""
# scoped-by-caller
from __future__ import annotations

from django.core.exceptions import ValidationError

from qa_core.actions import ActionHandler, DiffRow, register

from . import rules, services
from .models import Inspection, PostVisitPersonnel, QAMapping, QAPersonnel


@register
class EditQAPersonnelHandler(ActionHandler):
    """Register NBTE staff for QA duty, or change an entry. Payload:
    staff_id, discipline_ids, is_verification_officer, is_team_lead_eligible,
    active. Target: the QAPersonnel row (None when registering)."""

    code = "inspection.edit_qa_personnel"
    target_model = QAPersonnel

    def _staff(self, target, payload):
        from staff.models import Staff

        if target is not None:
            return target.staff
        return Staff.objects.filter(pk=payload.get("staff_id")).select_related("user__role", "designation").first()

    def payload_label(self, payload):
        staff = self._staff(None, payload)
        return f"QA personnel: {staff}" if staff else "QA personnel"

    def target_label(self, target):
        return f"QA personnel: {target}" if target is not None else ""

    def snapshot(self, target):
        if target is None:
            return {}
        return {"active": target.active, "disciplines": sorted(target.disciplines.values_list("pk", flat=True)),
                "verification": target.is_verification_officer, "team_lead": target.is_team_lead_eligible}

    def validate(self, target, payload):
        staff = self._staff(target, payload)
        if staff is None or staff.user_id is None:
            raise ValidationError("Choose a member of staff with a user account.")
        if target is None and QAPersonnel.objects.filter(staff=staff).exists():
            raise ValidationError(f"{staff} is already registered; edit the existing entry.")
        if not payload.get("discipline_ids"):
            raise ValidationError("Choose at least one discipline.")
        if payload.get("active", True):
            rules.raise_if(rules.staff_problems(staff))

    def preview(self, target, payload):
        from institutions.models import Discipline

        names = ", ".join(Discipline.objects.filter(pk__in=payload.get("discipline_ids") or [])
                          .values_list("name", flat=True))
        before = self.snapshot(target)
        old = ", ".join(Discipline.objects.filter(pk__in=before.get("disciplines", [])).values_list("name", flat=True))
        return [DiffRow("Person", str(self._staff(target, payload)), str(self._staff(target, payload))),
                DiffRow("Disciplines", old or "—", names or "—"),
                DiffRow("Verification officer", "Yes" if before.get("verification") else "—",
                        "Yes" if payload.get("is_verification_officer") else "No"),
                DiffRow("Team-lead eligible", "Yes" if before.get("team_lead") else "—",
                        "Yes" if payload.get("is_team_lead_eligible") else "No"),
                DiffRow("Active", "Yes" if before.get("active") else "—", "Yes" if payload.get("active", True) else "No")]

    def apply(self, target, payload, actor):
        from institutions.models import Discipline

        services.register_qa_personnel(
            self._staff(target, payload), actor=actor,
            disciplines=list(Discipline.objects.filter(pk__in=payload.get("discipline_ids") or [])),
            is_verification_officer=payload.get("is_verification_officer", True),
            is_team_lead_eligible=payload.get("is_team_lead_eligible", False), active=payload.get("active", True))

    def scope_department(self, target, initiator):
        staff = self._staff(target, {}) if target is not None else None
        return getattr(staff, "department", None) or getattr(initiator, "department", None)


@register
class EditQAMappingHandler(ActionHandler):
    """Confirm a proposed mapping (booked by the availability finder). Target:
    the institution; payload: mapping_id. Confirmation re-checks every §9
    rule under the lock; rejection or expiry releases the booking."""

    code = "inspection.edit_qa_mapping"

    @property
    def target_model(self):
        from institutions.models import Institution

        return Institution

    def _mapping(self, payload):
        return (QAMapping.unscoped.filter(pk=payload.get("mapping_id"))
                .select_related("case__institution", "qa_request_programme__programme", "resource_person",
                                "qa_personnel__staff").first())

    def target_label(self, target):
        return f"{target.name} ({target.code})" if target is not None else ""

    def snapshot(self, target):
        return {"name": target.name} if target is not None else {}

    def validate(self, target, payload):
        mapping = self._mapping(payload)
        if mapping is None or (target is not None and mapping.case.institution_id != target.pk):
            raise ValidationError("The mapping is not for this institution.")
        if mapping.status != "proposed":
            raise ValidationError("This mapping is no longer waiting for confirmation.")

    def preview(self, target, payload):
        m = self._mapping(payload)
        if m is None:
            return []
        return [DiffRow("Person", "—", str(m.person)), DiffRow("Programme", "—", str(m.qa_request_programme.programme)),
                DiffRow("Duty", "—", m.get_duty_display()),
                DiffRow("Dates", "—", f"{m.from_date:%d %b %Y} – {m.to_date:%d %b %Y}"),
                DiffRow("Case", "—", m.case.reference)]

    def apply(self, target, payload, actor):
        mapping = rules.activate(self._mapping(payload), actor=actor)
        services.on_mapping_active(mapping)

    def on_closed(self, pa, target, actor):
        mapping = self._mapping(pa.payload or {})
        if mapping is not None and mapping.status == "proposed":
            rules.release(mapping)

    def scope_department(self, target, initiator):
        if target is not None and target.handling_department_id:
            return target.handling_department
        return getattr(initiator, "department", None)


@register
class OverridePostVisitPersonnelHandler(ActionHandler):
    """Record who actually attended. Target: the Inspection; payload:
    changes [{pvp_id | staff_id, attended, reason}]. Started by the Team
    Lead; confirmed by a second officer. Changes the service history
    (compensating rows), never the plan."""

    code = "inspection.override_post_visit_personnel"
    target_model = Inspection

    def target_label(self, target):
        return f"{target.reference} · {target.case.institution.name}" if target is not None else ""

    def snapshot(self, target):
        return {str(p.pk): p.attended for p in PostVisitPersonnel.unscoped.filter(inspection=target)}

    def check_initiator(self, target, payload, actor):
        from django.core.exceptions import PermissionDenied

        if not isinstance(target, Inspection):
            raise ValidationError("Choose the inspection whose attendance is being corrected.")
        if not (actor.is_superuser or (target.team_lead_id and target.team_lead_id == actor.pk)):
            raise PermissionDenied("Only the Team Lead of the visit starts an attendance correction.")

    def validate(self, target, payload):
        if target is None or target.status not in ("completed", "closed"):
            raise ValidationError("Attendance can be corrected only after the visit is recorded.")
        changes = payload.get("changes") or []
        if not changes:
            raise ValidationError("Change at least one person's attendance.")
        for change in changes:
            if len((change.get("reason") or "").strip()) < 3:
                raise ValidationError("Give a reason for each change.")
            if change.get("pvp_id") and not PostVisitPersonnel.unscoped.filter(
                    pk=change["pvp_id"], inspection=target).exists():
                raise ValidationError("That person is not on this visit.")
        remaining = {p.pk: p.attended for p in PostVisitPersonnel.unscoped.filter(inspection=target)}
        for change in changes:
            if change.get("pvp_id"):
                remaining[int(change["pvp_id"])] = bool(change.get("attended"))
        if not any(remaining.values()) and not any(c.get("staff_id") for c in changes):
            raise ValidationError("At least one person must have attended.")

    def preview(self, target, payload):
        from staff.models import Staff

        rows = []
        pvps = {p.pk: p for p in PostVisitPersonnel.unscoped.filter(inspection=target)}
        for change in payload.get("changes") or []:
            if change.get("pvp_id"):
                p = pvps.get(int(change["pvp_id"]))
                if p is not None:
                    rows.append(DiffRow(str(p.person), "Attended" if p.attended else "Did not attend",
                                        ("Attended" if change.get("attended") else "Did not attend")
                                        + f" — {change.get('reason', '')}"))
            elif change.get("staff_id"):
                staff = Staff.objects.filter(pk=change["staff_id"]).first()
                rows.append(DiffRow(str(staff), "Not on the plan", f"Attended — {change.get('reason', '')}"))
        return rows

    def apply(self, target, payload, actor):
        from qa_core.models import PendingAction

        pa = (PendingAction.objects.filter(policy__action_code=self.code, object_id=str(target.pk))
              .order_by("-pk").first())
        services.apply_attendance(target, payload.get("changes") or [], actor=actor, pending_action=pa)
