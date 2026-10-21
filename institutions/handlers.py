"""ActionHandlers for every §6 institution management action.

Whether an action needs a second officer, who initiates and confirms, the
minimum rank and the department rule are ActionPolicy rows (seeded, editable).
What each action *does* is here. Every handler re-validates at confirm time,
and the Phase 1 engine refuses to apply over a target that changed since the
action was started ("stale — please re-initiate").
"""
# scoped-by-caller
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from qa_core import flow, timeline
from qa_core.actions import ActionHandler, DiffRow, register

from . import forms
from . import services
from .models import (
    ImpersonationSession,
    Institution,
    InstitutionCategory,
    InstitutionContact,
    InstitutionImportBatch,
    InstitutionOwnership,
    InstitutionRequest,
    InstitutionType,
    Programme,
)


def _qa_request_of(container):
    """The SOP-02 QA request behind a generic request container (Phase 3), if any."""
    if not container.object_id or container.content_type_id is None:
        return None
    from accreditation.models import QARequest

    if container.content_type.model_class() is not QARequest:
        return None
    return QARequest.unscoped.filter(pk=container.object_id).first()


def _drop_from_qa_requests(programme, actor, reason):
    """A dropped programme leaves every open QA request still at an R-25
    stage; the last programme of a request cancels the request. Later
    stages keep the row (the Phase 4 case decides)."""
    from accreditation import qa_services
    from accreditation.models import QARequestProgramme

    rows = QARequestProgramme.unscoped.filter(programme=programme, status=QARequestProgramme.ACTIVE,
                                              qa_request__status__in=qa_services.OPEN_STATUSES).select_related(
        "qa_request")
    for row in rows:
        req = row.qa_request
        if not qa_services.can_change_programmes(req, "drop_programme"):
            timeline.record(req.institution, "qa_request.programme_dropped_late", subject=req, actor=actor,
                            visibility="internal", internal_text=f"{programme} was dropped while {req.reference} "
                                                                 f"is {req.status}; the QA case decides.")
            continue
        if len(req.active_programmes()) <= 1:
            qa_services.cancel(req, user=actor, reason=f"Last programme dropped: {reason}")
        else:
            qa_services.drop_programme(req, row, user=actor, reason=reason)


def _state(pk):
    from staff.models import StateOfOrigin

    return StateOfOrigin.objects.filter(pk=pk).first() if pk else None


def _name(obj):
    return getattr(obj, "name", "") or "—"


class InstitutionHandler(ActionHandler):
    """Base for actions whose target is an institution."""

    target_model = Institution
    form_class = forms.InstitutionActionForm
    icon = "edit"
    allowed_statuses = None  # None = any except delisted
    summary = ""

    def target_label(self, target):
        return f"{target.name} ({target.code})" if target is not None else ""

    def snapshot(self, target):
        return {"name": target.name, "status": target.status,
                "handling_department": getattr(target.handling_department, "name", "")}

    def check_initiator(self, target, payload, actor):
        if target is not None and not Institution.objects.for_user(actor).filter(pk=target.pk).exists():
            raise PermissionDenied("This institution is not in your scope.")

    def check_status(self, target):
        if target is None:
            raise ValidationError("Choose an institution.")
        allowed = self.allowed_statuses
        if allowed is None:
            if target.status == Institution.DELISTED:
                raise ValidationError(f"{target.name} is delisted. Relist it first.")
        elif target.status not in allowed:
            raise ValidationError(f"Not possible while the institution is {target.status_label.lower()}.")

    def validate(self, target, payload):
        self.check_status(target)

    def resolve_target(self, institution, form):
        return institution

    def available_for(self, institution):
        """(available, reason) for the action menu."""
        try:
            self.check_status(institution)
        except ValidationError as exc:
            return False, " ".join(exc.messages)
        return True, ""

    def scope_department(self, target, initiator):
        inst = target if isinstance(target, Institution) else getattr(target, "institution", None)
        if inst is not None and inst.handling_department_id:
            return inst.handling_department
        return getattr(initiator, "department", None)


# ===========================================================================
# New Institution
# ===========================================================================
@register
class AddTvetHandler(ActionHandler):
    code = "institutions.add_tvet"
    target_model = None
    form_class = forms.AddTvetForm
    icon = "plus"
    summary = "Register a new proposed institution and its primary portal account."

    def target_label(self, target):
        return ""

    def payload_label(self, payload):
        return f"New institution: {payload.get('name', '')}".strip()

    def validate(self, target, payload):
        from accounts.models import User

        name = (payload.get("name") or "").strip()
        if len(name) < 4:
            raise ValidationError("Enter the institution's full name.")
        if Institution.unscoped.filter(name__iexact=name).exists():
            raise ValidationError(f"An institution called “{name}” already exists.")
        code = (payload.get("code") or "").strip().upper()
        if code:
            services.allocate_code(name, code)
        email = (payload.get("admin_email") or "").strip().lower()
        if not email:
            raise ValidationError("Enter the administrator's e-mail.")
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError(f"{email} already has an account.")
        import config  # §9 (Phase 5 wiring): the Institutional Admin nominee's department

        required = str(config.get("qa.institution.admin_required_department", "any") or "any").strip()
        if required.lower() != "any" and (payload.get("admin_department") or "").strip().lower() != required.lower():
            raise ValidationError(f"The administrator must come from the {required} department "
                                  "(qa.institution.admin_required_department).")
        for key, model in (("ownership_id", InstitutionOwnership), ("institution_type_id", InstitutionType),
                           ("category_id", InstitutionCategory)):
            if not model.objects.filter(pk=payload.get(key), is_active=True).exists():
                raise ValidationError("Choose a valid ownership, type and category.")
        state = _state(payload.get("state_id"))
        if state is None:
            raise ValidationError("Choose a state.")
        services.validate_lga(state, payload.get("lga", ""))

    def preview(self, target, payload):
        p = payload
        own = InstitutionOwnership.objects.filter(pk=p.get("ownership_id")).first()
        itype = InstitutionType.objects.filter(pk=p.get("institution_type_id")).first()
        cat = InstitutionCategory.objects.filter(pk=p.get("category_id")).first()
        state = _state(p.get("state_id"))
        dept = services.proposed_department()
        return [
            DiffRow("Name", "", p.get("name", "")),
            DiffRow("Code", "", p.get("code") or f"{services.suggest_code(p.get('name', ''))} (allocated at confirmation)"),
            DiffRow("Ownership", "", _name(own)),
            DiffRow("Type / category", "", f"{_name(itype)} / {_name(cat)}"),
            DiffRow("State / LGA", "", f"{_name(state)} / {p.get('lga', '')}"),
            DiffRow("Address", "", ", ".join(x for x in (p.get("address_line1"), p.get("address_town")) if x)),
            DiffRow("Official e-mail / phone", "", f"{p.get('official_email', '')} / {p.get('phone', '')}"),
            DiffRow("Head of institution", "", p.get("head_name", "")),
            DiffRow("Portal administrator", "", f"{p.get('admin_first_name', '')} {p.get('admin_last_name', '')} "
                                                f"<{p.get('admin_email', '')}> (inactive until credentials are sent)"),
            DiffRow("Status", "", "Proposed"),
            DiffRow("Handled by", "", _name(dept)),
        ]

    def apply(self, target, payload, actor):
        p = payload
        data = {
            "name": p["name"], "code": p.get("code", ""),
            "ownership": InstitutionOwnership.objects.get(pk=p["ownership_id"]),
            "institution_type": InstitutionType.objects.get(pk=p["institution_type_id"]),
            "category": InstitutionCategory.objects.get(pk=p["category_id"]),
            "state": _state(p["state_id"]), "lga": p.get("lga", ""),
            "year_established": p.get("year_established"), "cac_rc_number": p.get("cac_rc_number", ""),
            "website": p.get("website", ""),
            "contacts": {"official_email": {"value": p.get("official_email", "")},
                         "phone": {"value": p.get("phone", "")},
                         "head_of_institution": {"name": p.get("head_name", ""), "value": ""}},
            "address": {"line1": p.get("address_line1", ""), "town": p.get("address_town", ""),
                        "lga": p.get("lga", ""), "state": _state(p["state_id"])},
            "admin": {"first_name": p.get("admin_first_name", ""), "last_name": p.get("admin_last_name", ""),
                      "email": p.get("admin_email", ""), "phone": p.get("admin_phone", ""),
                      "job_title": p.get("admin_job_title", ""), "department": p.get("admin_department", "")},
        }
        services.create_institution(data, actor=actor, origin="nbte", activate_admin=False)


@register
class ApproveInstitutionHandler(InstitutionHandler):
    code = "institutions.approve_institution"
    form_class = forms.InstitutionActionForm
    icon = "check"
    allowed_statuses = (Institution.PROPOSED, Institution.UNDER_REVIEW)
    summary = "Approve a proposed institution; it moves to its category's department."

    def validate(self, target, payload):
        self.check_status(target)
        if not target.category_id or not (target.category.routing_department_id
                                          or (target.institution_type_id
                                              and target.institution_type.default_department_id)):
            raise ValidationError("Set a category that maps to a department before approving.")

    def preview(self, target, payload):
        new_dept = target.derive_handling_department(status=Institution.APPROVED)
        return [DiffRow("Status", target.status_label, "Approved"),
                DiffRow("Handling department", _name(target.handling_department), _name(new_dept))]

    def apply(self, target, payload, actor):
        new_dept = target.derive_handling_department(status=Institution.APPROVED)
        services.lifecycle_move(target, "approve", actor, remark=payload.get("reason", ""), confirmed=True)
        old = target.handling_department
        target.handling_department = new_dept
        target.approved_at = timezone.now()
        target.save(update_fields=["handling_department", "approved_at", "updated_at"])
        timeline.record(target, "institution.approved", actor=actor, visibility="public",
                        public_text=f"{target.name} was approved by NBTE.",
                        internal_text=f"Handling department: {_name(old)} → {_name(new_dept)}.")
        services.notify_institution(target, "institution.approved", {"department": _name(new_dept)})


# ===========================================================================
# TVET Support
# ===========================================================================
@register
class DropProgrammesHandler(InstitutionHandler):
    code = "institutions.drop_programmes"
    form_class = forms.DropProgrammesForm
    icon = "minus"
    summary = "Mark programmes dropped (their open tasks are cancelled)."

    def _programmes(self, target, payload):
        ids = payload.get("programme_ids") or []
        return list(Programme.unscoped.filter(institution=target, pk__in=ids).select_related("level"))

    def validate(self, target, payload):
        self.check_status(target)
        rows = self._programmes(target, payload)
        if not rows or len(rows) != len(set(payload.get("programme_ids") or [])):
            raise ValidationError("Choose programmes of this institution.")
        dropped = [str(p) for p in rows if p.status == "dropped"]
        if dropped:
            raise ValidationError(f"Already dropped: {', '.join(dropped)}.")

    def preview(self, target, payload):
        return [DiffRow(str(p), p.get_status_display(), "Dropped") for p in self._programmes(target, payload)]

    def apply(self, target, payload, actor):
        now = timezone.now()
        names = []
        for p in self._programmes(target, payload):
            p.status, p.dropped_at, p.dropped_reason = "dropped", now, payload.get("reason", "")
            p.save(update_fields=["status", "dropped_at", "dropped_reason", "updated_at"])
            names.append(str(p))
            _drop_from_qa_requests(p, actor, payload.get("reason", "") or "Programme dropped by NBTE")
        # QA task links arrive with the Phase 4 task bridge; nothing to cancel yet.
        timeline.record(target, "programmes.dropped", actor=actor, visibility="public",
                        public_text=f"Programmes dropped: {', '.join(names)}.")


@register
class UpdateStateLgaHandler(InstitutionHandler):
    code = "institutions.update_state_lga"
    form_class = forms.UpdateStateLgaForm
    icon = "map"
    summary = "Change the state and LGA; the zone is recomputed."

    def snapshot(self, target):
        return {**super().snapshot(target), "state": target.state_name, "lga": target.lga, "zone": target.zone}

    def validate(self, target, payload):
        self.check_status(target)
        state = _state(payload.get("state_id"))
        if state is None:
            raise ValidationError("Choose a state.")
        services.validate_lga(state, payload.get("lga", ""))
        if state.pk == target.state_id and payload.get("lga") == target.lga:
            raise ValidationError("Nothing would change.")

    def preview(self, target, payload):
        state = _state(payload.get("state_id"))
        return [DiffRow("State", target.state_name, _name(state)), DiffRow("LGA", target.lga, payload.get("lga", "")),
                DiffRow("Zone", target.zone or "—", (state.zone if state else "") or "—")]

    def apply(self, target, payload, actor):
        state = _state(payload["state_id"])
        old_zone = target.zone
        target.state, target.lga = state, payload["lga"]
        target.recompute_zone()
        target.save(update_fields=["state", "lga", "zone", "updated_at"])
        note = f" Zone {old_zone or '—'} → {target.zone or '—'} (open complaints are re-routed from Phase 7)." \
            if old_zone != target.zone else ""
        timeline.record(target, "institution.location_changed", actor=actor, visibility="public",
                        public_text=f"Location updated to {target.lga}, {target.state_name}.", internal_text=note)


@register
class WipeRequestsHandler(InstitutionHandler):
    code = "institutions.wipe_requests"
    form_class = forms.WipeRequestsForm
    icon = "trash"
    summary = "Cancel every open, pre-submission request of the chosen types (never deletes)."

    def _split(self, target, payload):
        qs = InstitutionRequest.unscoped.filter(institution=target, request_type__code__in=payload.get("type_codes")
                                                or []).exclude(status__in=("cancelled", "closed"))
        return ([r for r in qs if r.is_pre_submission], [r for r in qs if not r.is_pre_submission])

    def validate(self, target, payload):
        self.check_status(target)
        if not payload.get("type_codes"):
            raise ValidationError("Choose at least one request type.")

    def preview(self, target, payload):
        wipe, keep = self._split(target, payload)
        rows = [DiffRow(str(r), r.get_status_display(), "Cancelled") for r in wipe]
        rows += [DiffRow(f"{r} (submitted — excluded)", r.get_status_display(), r.get_status_display()) for r in keep]
        return rows or [DiffRow("Requests", "None open", "Nothing to cancel")]

    def apply(self, target, payload, actor):
        wipe, keep = self._split(target, payload)
        now = timezone.now()
        for r in wipe:
            qa_req = _qa_request_of(r)
            if qa_req is not None:
                from accreditation import qa_services

                if qa_services.is_cancellable(qa_req):
                    qa_services.cancel(qa_req, user=actor, reason=payload.get("reason", "") or "Wiped")
                continue
            r.status, r.cancelled_at, r.cancel_reason = "cancelled", now, payload.get("reason", "Wiped")
            r.save(update_fields=["status", "cancelled_at", "cancel_reason", "updated_at"])
        timeline.record(target, "requests.wiped", actor=actor, visibility="internal",
                        internal_text=f"{len(wipe)} request(s) cancelled; {len(keep)} submitted request(s) kept.")


@register
class RevertQAHistoryHandler(InstitutionHandler):
    code = "institutions.revert_qa_history"
    form_class = forms.RevertHistoryForm
    icon = "rewind"
    summary = "Move the onboarding application back to an earlier stage (compensating event)."

    @property
    def target_model(self):
        from accreditation.models import Application

        return Application

    def resolve_target(self, institution, form):
        return form.application

    def target_label(self, target):
        return f"{target.reference} — {target.institution.name}" if target is not None else ""

    def snapshot(self, target):
        return {"status": target.status}

    def check_initiator(self, target, payload, actor):
        inst = getattr(target, "institution", None)
        if inst is not None and not Institution.objects.for_user(actor).filter(pk=inst.pk).exists():
            raise PermissionDenied("This institution is not in your scope.")

    def validate(self, target, payload):
        if target is None:
            raise ValidationError("There is no application to revert.")
        instance = flow.instance_for(target, "onboarding")
        if instance is None:
            raise ValidationError("The application has no workflow.")
        stage = payload.get("stage")
        visited = {e.to_stage for e in flow.history(instance)}
        if stage not in visited or stage in instance.current_stages:
            raise ValidationError("Choose a stage the application has already passed.")
        if target.status in ("ssq_submitted", "withdrawn"):
            raise ValidationError("This application is closed.")

    def preview(self, target, payload):
        instance = flow.instance_for(target, "onboarding")
        st = flow.stage(instance, payload.get("stage")) if instance else None
        return [DiffRow("Stage", flow.public_label(instance) or target.status, st.label if st else payload.get("stage")),
                DiffRow("History", "kept", "kept, with a compensating event")]

    def apply(self, target, payload, actor):
        from accreditation.models import DepartmentalClearance

        instance = flow.instance_for(target, "onboarding")
        flow.force_stage(instance, payload["stage"], actor, remark=payload.get("reason", ""))
        if payload["stage"] == "draft":
            DepartmentalClearance.unscoped.filter(application=target, round=target.round, status="pending") \
                .update(status="closed", updated_at=timezone.now())
            target.declaration_accepted = False
            target.save(update_fields=["declaration_accepted", "updated_at"])
        timeline.record(target.institution, "application.reverted", subject=target, actor=actor, visibility="internal",
                        internal_text=f"Reverted to {payload['stage']}: {payload.get('reason', '')}")

    def scope_department(self, target, initiator):
        inst = getattr(target, "institution", None)
        return inst.handling_department if inst is not None else getattr(initiator, "department", None)

    def available_for(self, institution):
        ok, why = super().available_for(institution)
        return ok, why


@register
class CancelInstitutionRequestHandler(InstitutionHandler):
    code = "institutions.cancel_institution_request"
    form_class = forms.CancelRequestForm
    icon = "x"
    summary = "Cancel one request of an enabled request type."

    def validate(self, target, payload):
        self.check_status(target)
        req = InstitutionRequest.unscoped.filter(pk=payload.get("request_id"), institution=target).first()
        if req is None or req.status in ("cancelled", "closed"):
            raise ValidationError("Choose an open request of this institution.")
        qa_req = _qa_request_of(req)
        if qa_req is not None:
            from accreditation import qa_services

            if not qa_services.is_cancellable(qa_req):
                raise ValidationError(f"{qa_req.reference} can no longer be cancelled: only while it is draft, "
                                      "submitted, fee due or payment pending confirmation (R-25).")

    def preview(self, target, payload):
        req = InstitutionRequest.unscoped.filter(pk=payload.get("request_id")).first()
        return [DiffRow(str(req), req.get_status_display(), "Cancelled")] if req else []

    def apply(self, target, payload, actor):
        req = InstitutionRequest.unscoped.get(pk=payload["request_id"])
        qa_req = _qa_request_of(req)
        if qa_req is not None:
            from accreditation import qa_services

            qa_services.cancel(qa_req, user=actor, reason=payload.get("reason", "") or "Cancelled by NBTE")
            return
        req.status, req.cancelled_at, req.cancel_reason = "cancelled", timezone.now(), payload.get("reason", "")
        req.save(update_fields=["status", "cancelled_at", "cancel_reason", "updated_at"])
        timeline.record(target, "request.cancelled", subject=req, actor=actor, visibility="public",
                        public_text=f"{req.request_type.name} request {req.reference or req.pk} was cancelled.")


@register
class UpdateContactHandler(InstitutionHandler):
    code = "institutions.update_contact"
    form_class = forms.UpdateContactForm
    icon = "phone"
    summary = "Replace a contact (history kept)."

    def snapshot(self, target):
        return {**super().snapshot(target),
                "contacts": {c.kind: f"{c.name} {c.value}".strip() for c in services.contacts_of(target)}}

    def validate(self, target, payload):
        self.check_status(target)
        if payload.get("kind") not in dict(InstitutionContact.KIND_CHOICES):
            raise ValidationError("Choose a contact type.")
        current = target.contact(payload["kind"])
        if current and current.value == payload.get("value") and current.name == payload.get("name", ""):
            raise ValidationError("Nothing would change.")

    def preview(self, target, payload):
        current = target.contact(payload.get("kind"))
        label = dict(InstitutionContact.KIND_CHOICES).get(payload.get("kind"), payload.get("kind"))
        before = f"{current.name} {current.value}".strip() if current else ""
        after = f"{payload.get('name', '')} {payload.get('value', '')}".strip()
        return [DiffRow(label, before, after)]

    def apply(self, target, payload, actor):
        services.set_contact(target, payload["kind"], payload.get("value", ""), payload.get("name", ""), actor=actor)
        label = dict(InstitutionContact.KIND_CHOICES).get(payload["kind"])
        timeline.record(target, "contact.updated", actor=actor, visibility="public",
                        public_text=f"{label} updated by NBTE.")


@register
class UpdateAddressHandler(InstitutionHandler):
    code = "institutions.update_address"
    form_class = forms.UpdateAddressForm
    icon = "home"
    summary = "Close the current address and open a new one."

    def validate(self, target, payload):
        self.check_status(target)
        state = _state(payload.get("state_id"))
        if state is None or not payload.get("line1"):
            raise ValidationError("Enter the address and state.")
        services.validate_lga(state, payload.get("lga", ""))

    def preview(self, target, payload):
        current = target.address(payload.get("kind"))
        state = _state(payload.get("state_id"))
        after = ", ".join(x for x in (payload.get("line1"), payload.get("town"), payload.get("lga"), _name(state)) if x)
        from .models import InstitutionAddress

        label = dict(InstitutionAddress.KIND_CHOICES).get(payload.get("kind"), "Address")
        return [DiffRow(label, current.one_line if current else "", after)]

    def apply(self, target, payload, actor):
        services.set_address(target, payload["kind"], line1=payload["line1"], town=payload.get("town", ""),
                             lga=payload.get("lga", ""), state=_state(payload["state_id"]), gps=payload.get("gps", ""))
        timeline.record(target, "address.updated", actor=actor, visibility="public",
                        public_text="Address updated by NBTE.")


@register
class UpdateCategoryHandler(InstitutionHandler):
    code = "institutions.update_category"
    form_class = forms.UpdateCategoryForm
    icon = "layers"
    summary = "Change the category — may move the institution to another department."

    def snapshot(self, target):
        return {**super().snapshot(target), "category": _name(target.category)}

    def _new(self, target, payload):
        category = InstitutionCategory.objects.filter(pk=payload.get("category_id")).select_related(
            "routing_department", "institution_type").first()
        return category

    def new_department(self, target, category):
        probe = Institution(status=target.status, category=category,
                            institution_type=category.institution_type or target.institution_type,
                            handling_department=target.handling_department)
        if target.status in (Institution.PROPOSED, Institution.UNDER_REVIEW):
            return target.handling_department
        return probe.derive_handling_department() or target.handling_department

    def validate(self, target, payload):
        self.check_status(target)
        category = self._new(target, payload)
        if category is None:
            raise ValidationError("Choose a category.")
        if category.pk == target.category_id:
            raise ValidationError("That is already the institution's category.")

    def preview(self, target, payload):
        category = self._new(target, payload)
        new_dept = self.new_department(target, category)
        rows = [DiffRow("Category", _name(target.category), _name(category))]
        if category.institution_type_id and category.institution_type_id != target.institution_type_id:
            rows.append(DiffRow("Type", _name(target.institution_type), _name(category.institution_type)))
        transfer = _name(new_dept)
        if new_dept != target.handling_department:
            transfer += "  ⟵ DEPARTMENT TRANSFER"
        elif target.status in (Institution.PROPOSED, Institution.UNDER_REVIEW):
            transfer += " (stays until approval)"
        rows.append(DiffRow("Handling department", _name(target.handling_department), transfer))
        return rows

    def apply(self, target, payload, actor):
        category = self._new(target, payload)
        old_dept = target.handling_department
        new_dept = self.new_department(target, category)
        target.category = category
        if category.institution_type_id:
            target.institution_type = category.institution_type
        target.handling_department = new_dept
        target.save(update_fields=["category", "institution_type", "handling_department", "updated_at"])
        internal = f"Category → {category.name}."
        if new_dept != old_dept:
            internal += f" Department transfer: {_name(old_dept)} → {_name(new_dept)}."
        timeline.record(target, "institution.category_changed", actor=actor, visibility="public",
                        public_text=f"Category updated to {category.name}.", internal_text=internal)


@register
class UpdateNameHandler(InstitutionHandler):
    code = "institutions.update_name"
    form_class = forms.UpdateNameForm
    icon = "type"
    summary = "Rename the institution (the old name is kept)."

    def validate(self, target, payload):
        self.check_status(target)
        name = (payload.get("name") or "").strip()
        if len(name) < 4:
            raise ValidationError("Enter the full new name.")
        if name == target.name:
            raise ValidationError("That is already the name.")
        if Institution.unscoped.filter(name__iexact=name).exclude(pk=target.pk).exists():
            raise ValidationError("Another institution already has that name.")

    def preview(self, target, payload):
        return [DiffRow("Name", target.name, payload.get("name", "")),
                DiffRow("Former names", ", ".join(n.get("name", "") for n in target.former_names or []) or "—",
                        ", ".join([n.get("name", "") for n in target.former_names or []] + [target.name]))]

    def apply(self, target, payload, actor):
        old = target.name
        target.former_names = list(target.former_names or []) + [{"name": old, "until": timezone.localdate().isoformat()}]
        target.name = payload["name"].strip()
        target.save(update_fields=["name", "former_names", "updated_at"])
        timeline.record(target, "institution.renamed", actor=actor, visibility="public",
                        public_text=f"Name changed from “{old}” to “{target.name}”. Letters already issued are "
                                    "unchanged.")


@register
class DelistHandler(InstitutionHandler):
    code = "institutions.delist"
    form_class = forms.InstitutionActionForm
    icon = "ban"
    summary = "Delist: portal sign-in disabled, open requests frozen, public notice."

    def validate(self, target, payload):
        if target is None or target.status == Institution.DELISTED:
            raise ValidationError("This institution is already delisted.")

    def preview(self, target, payload):
        return [DiffRow("Status", target.status_label, "Delisted"),
                DiffRow("Portal sign-in", "On" if target.portal_enabled else "Off", "Off"),
                DiffRow("Open applications", "Running", "Frozen (on hold)")]

    def apply(self, target, payload, actor):
        from accreditation.models import Application

        services.lifecycle_move(target, "delist", actor, remark=payload.get("reason", ""), confirmed=True)
        target.delisted_at = timezone.now()
        target.delist_reason = payload.get("reason", "")
        target.portal_enabled = False
        target.save(update_fields=["delisted_at", "delist_reason", "portal_enabled", "updated_at"])
        for app in Application.unscoped.filter(institution=target).exclude(status__in=("ssq_submitted", "withdrawn")):
            instance = flow.instance_for(app, "onboarding")
            if instance is not None and not instance.paused:
                instance.paused, instance.paused_at = True, timezone.now()
                instance.save(update_fields=["paused", "paused_at", "updated_at"])
        timeline.record(target, "institution.delisted", actor=actor, visibility="public",
                        public_text=f"{target.name} has been delisted by NBTE.",
                        internal_text=payload.get("reason", ""))


@register
class RelistHandler(InstitutionHandler):
    code = "institutions.relist"
    form_class = forms.InstitutionActionForm
    icon = "rotate"
    allowed_statuses = (Institution.DELISTED,)
    summary = "Relist a delisted institution and re-enable portal sign-in."

    def preview(self, target, payload):
        return [DiffRow("Status", target.status_label, "Approved"),
                DiffRow("Portal sign-in", "Off", "On")]

    def apply(self, target, payload, actor):
        from accreditation.models import Application

        services.lifecycle_move(target, "relist", actor, remark=payload.get("reason", ""), confirmed=True)
        target.portal_enabled = True
        target.delisted_at = None
        target.save(update_fields=["portal_enabled", "delisted_at", "updated_at"])
        for app in Application.unscoped.filter(institution=target).exclude(status__in=("ssq_submitted", "withdrawn")):
            instance = flow.instance_for(app, "onboarding")
            if instance is not None and instance.paused and not services.active_hold(app):
                instance.paused, instance.paused_at = False, None
                instance.save(update_fields=["paused", "paused_at", "updated_at"])
        timeline.record(target, "institution.relisted", actor=actor, visibility="public",
                        public_text=f"{target.name} has been relisted by NBTE.")


@register
class UpdateOwnershipHandler(InstitutionHandler):
    code = "institutions.update_ownership"
    form_class = forms.UpdateOwnershipForm
    icon = "key"
    summary = "Change ownership — re-checks which statutory documents are required."

    def snapshot(self, target):
        return {**super().snapshot(target), "ownership": _name(target.ownership)}

    def _new(self, payload):
        return InstitutionOwnership.objects.filter(pk=payload.get("ownership_id"), is_active=True).first()

    def validate(self, target, payload):
        self.check_status(target)
        own = self._new(payload)
        if own is None:
            raise ValidationError("Choose an ownership.")
        if own.pk == target.ownership_id:
            raise ValidationError("That is already the ownership.")

    def preview(self, target, payload):
        import config

        own = self._new(payload)
        kinds = services.newly_required_kinds(target, own)
        rows = [DiffRow("Ownership", _name(target.ownership), _name(own)),
                DiffRow("Newly required documents", "—", ", ".join(k.name for k in kinds) or "None")]
        hold = str(config.get("qa.ownership_change.hold_open_cases", True)).lower() in ("true", "1")
        if kinds and hold:
            rows.append(DiffRow("Open application", "Running", "On hold until the documents are uploaded"))
        return rows

    def apply(self, target, payload, actor):
        own = self._new(payload)
        kinds = services.newly_required_kinds(target, own)
        old = target.ownership
        target.ownership = own
        target.save(update_fields=["ownership", "updated_at"])
        text = f"Ownership changed from {_name(old)} to {own.name}."
        if kinds:
            text += " Please upload: " + ", ".join(k.name for k in kinds) + "."
        timeline.record(target, "institution.ownership_changed", actor=actor, visibility="public", public_text=text)
        if kinds and services.cfg_bool("qa.ownership_change.hold_open_cases", True):
            services.hold_open_application(target, actor, "Ownership changed: waiting for "
                                           + ", ".join(k.name for k in kinds) + ".")


@register
class SuspendPortalAccessHandler(InstitutionHandler):
    code = "institutions.suspend_portal_access"
    icon = "lock"
    summary = "Switch portal sign-in off without delisting."

    def validate(self, target, payload):
        self.check_status(target)
        if not target.portal_enabled:
            raise ValidationError("Portal access is already off.")

    def available_for(self, institution):
        ok, why = super().available_for(institution)
        if ok and not institution.portal_enabled:
            return False, "Portal access is already off."
        return ok, why

    def preview(self, target, payload):
        return [DiffRow("Portal sign-in", "On", "Off")]

    def apply(self, target, payload, actor):
        target.portal_enabled = False
        target.save(update_fields=["portal_enabled", "updated_at"])
        timeline.record(target, "portal.suspended", actor=actor, visibility="public",
                        public_text="Portal access was suspended by NBTE.", internal_text=payload.get("reason", ""))


@register
class RestorePortalAccessHandler(InstitutionHandler):
    code = "institutions.restore_portal_access"
    icon = "unlock"
    summary = "Switch portal sign-in back on."

    def validate(self, target, payload):
        self.check_status(target)
        if target.portal_enabled:
            raise ValidationError("Portal access is already on.")

    def available_for(self, institution):
        ok, why = super().available_for(institution)
        if ok and institution.portal_enabled:
            return False, "Portal access is already on."
        return ok, why

    def preview(self, target, payload):
        return [DiffRow("Portal sign-in", "Off", "On")]

    def apply(self, target, payload, actor):
        target.portal_enabled = True
        target.save(update_fields=["portal_enabled", "updated_at"])
        timeline.record(target, "portal.restored", actor=actor, visibility="public",
                        public_text="Portal access was restored by NBTE.")


@register
class SendLoginCredentialsHandler(InstitutionHandler):
    """Single step (no second officer), audited through the same engine."""

    code = "institutions.send_login_credentials"
    form_class = forms.SendCredentialsForm
    icon = "mail"
    summary = "E-mail a one-time set-password link to the primary contact."

    def validate(self, target, payload):
        self.check_status(target)
        if target.primary_user() is None:
            raise ValidationError("This institution has no portal account yet.")
        if not target.portal_enabled:
            raise ValidationError("Portal access is switched off for this institution.")

    def preview(self, target, payload):
        user = target.primary_user()
        return [DiffRow("Recipient", "", user.email if user else "—"),
                DiffRow("Account", "Inactive" if user and not user.is_active else "Active", "Active")]

    def apply(self, target, payload, actor):
        services.send_credentials(target, actor=actor)


@register
class ImpersonateHandler(InstitutionHandler):
    """Single step with safeguards: reason required, time-boxed, read-only,
    banner, every page view audited."""

    code = "institutions.impersonate"
    form_class = forms.ImpersonateForm
    icon = "eye"
    summary = "View the portal as this institution (read-only, time-boxed)."

    def validate(self, target, payload):
        if target is None:
            raise ValidationError("Choose an institution.")
        if services.impersonation_target(target) is None:
            raise ValidationError("This institution has no active portal account to view as. Send its login "
                                  "credentials first.")

    def available_for(self, institution):
        if services.impersonation_target(institution) is None:
            return False, "No active portal account yet."
        return True, ""

    def preview(self, target, payload):
        return [DiffRow("View as", "", str(services.impersonation_target(target) or "—"))]

    def apply(self, target, payload, actor):
        services.start_impersonation(target, staff=actor, reason=payload.get("reason", ""))


@register
class BulkImportHandler(ActionHandler):
    code = "institutions.bulk_import"
    target_model = InstitutionImportBatch
    summary = "Create or update institutions / programmes from a validated spreadsheet."

    def target_label(self, target):
        if target is None:
            return ""
        s = target.summary or {}
        return (f"{target.get_kind_display()} import #{target.pk}: {s.get('create', 0)} new, "
                f"{s.get('update', 0)} updated")

    def snapshot(self, target):
        return {"status": target.status, "rows": len(target.rows or [])}

    def validate(self, target, payload):
        from .importexport import revalidate

        if target is None or target.status not in ("validated", "pending"):
            raise ValidationError("This import is not waiting to be applied.")
        revalidate(target)

    def preview(self, target, payload):
        rows = []
        for row in (target.rows or [])[:60]:
            label = row.get("_label") or row.get("name") or row.get("code")
            rows.append(DiffRow(f"Row {row.get('_row')}: {label}", row.get("_before", "") or "—",
                                row.get("_after", "") or ("new" if row.get("_action") == "create" else "")))
        if len(target.rows or []) > 60:
            rows.append(DiffRow("…", "", f"{len(target.rows) - 60} more row(s)"))
        return rows

    def apply(self, target, payload, actor):
        from .importexport import apply_batch

        apply_batch(target, actor=actor)

    def on_closed(self, pa, target, actor):
        if target is not None and target.status == "pending":
            # failed (stale / no longer valid) → back to validated: the officer
            # can re-send it, and it is re-checked when sent.
            target.status = {"rejected": "rejected", "failed": "validated"}.get(pa.status, "withdrawn")
            target.save(update_fields=["status", "updated_at"])

    def scope_department(self, target, initiator):
        return getattr(initiator, "department", None)


# ---------------------------------------------------------------------------
# Shown disabled until the phase that provides their records (§6 note)
# ---------------------------------------------------------------------------
class _LaterHandler(InstitutionHandler):
    later_phase = "Phase 3–5"

    def available_for(self, institution):
        return False, f"Available from {self.later_phase}: it needs QA request / personnel / mapping records."

    def validate(self, target, payload):
        raise ValidationError(f"This action becomes available in {self.later_phase}.")

    def apply(self, target, payload, actor):  # pragma: no cover
        raise ValidationError("Not available yet.")


@register
class AddProgrammeToQAHandler(InstitutionHandler):
    """Phase 3: live. Adds a programme to an open QA request while the SOP-02
    flow still allows it (draft, submitted, fee due); an unpaid bill is
    re-issued for the new programme count."""

    code = "institutions.add_programme_to_qa"
    form_class = forms.AddProgrammeToQAForm
    icon = "plus"
    allowed_statuses = (Institution.APPROVED,)
    summary = "Add one of the institution's programmes to an open QA request."

    def _objects(self, target, payload):
        from accreditation.models import QARequest

        req = QARequest.unscoped.filter(pk=payload.get("qa_request_id"), institution=target).first()
        programme = Programme.unscoped.filter(pk=payload.get("programme_id"), institution=target).first()
        return req, programme

    def snapshot(self, target):
        from accreditation.models import QARequestProgramme

        base = super().snapshot(target)
        base["qa_rows"] = sorted(QARequestProgramme.unscoped.filter(qa_request__institution=target)
                                 .values_list("pk", flat=True))
        return base

    def available_for(self, institution):
        from accreditation.models import QARequest

        ok, why = super().available_for(institution)
        if not ok:
            return ok, why
        if not QARequest.unscoped.filter(institution=institution,
                                         status__in=("draft", "submitted", "fee_due")).exists():
            return False, "No QA request is open at draft, submitted or fee due."
        return True, ""

    def validate(self, target, payload):
        from accreditation import qa_services
        from accreditation.models import QARequestProgramme

        self.check_status(target)
        req, programme = self._objects(target, payload)
        if req is None or programme is None:
            raise ValidationError("Choose a QA request and a programme of this institution.")
        if not qa_services.can_change_programmes(req, "add_programme"):
            raise ValidationError(f"{req.reference} no longer accepts new programmes (it is "
                                  f"{qa_services.public_status(req).lower()}).")
        if QARequestProgramme.unscoped.filter(qa_request=req, programme=programme,
                                              status=QARequestProgramme.ACTIVE).exists():
            raise ValidationError(f"{programme} is already on {req.reference}.")
        ok, why = qa_services.programme_eligibility(programme, req.qa_type)
        if not ok:
            raise ValidationError(f"{programme}: {why}")

    def preview(self, target, payload):
        req, programme = self._objects(target, payload)
        if req is None or programme is None:
            return []
        current = ", ".join(str(r.programme) for r in req.active_programmes()) or "—"
        return [DiffRow(f"Programmes on {req.reference}", current, f"{current} + {programme}")]

    def apply(self, target, payload, actor):
        from accreditation import qa_services

        req, programme = self._objects(target, payload)
        qa_services.add_programme(req, programme, user=actor, remark=payload.get("reason", ""))


# Phase 5: Edit QA Personnel, Edit QA Mapping and Override Post-Visit
# Personnel are live in inspection/handlers.py.


@register
class SendBackToAuditHandler(InstitutionHandler):
    """Phase 4: live. Moves a QA case at stage 7 or later back to stage 5
    (Audit the personnel cost) with the remark; the open stage task closes
    and the supervising Audit officer gets a new one."""

    code = "accreditation.send_back_to_audit"
    form_class = forms.SendBackToAuditForm
    icon = "rewind"
    allowed_statuses = (Institution.APPROVED,)
    summary = "Return a QA case (stage 7 or later) to Audit for the cost to be reviewed again."

    def _case(self, target, payload):
        from accreditation.models import QACase

        return QACase.unscoped.filter(pk=payload.get("case_id"), institution=target).first()

    def snapshot(self, target):
        from accreditation import case_services
        from accreditation.models import QACase

        base = super().snapshot(target)
        base["case_stages"] = {str(c.pk): case_services.current_stages(c)
                               for c in QACase.unscoped.filter(institution=target)}
        return base

    def available_for(self, institution):
        from accreditation import case_services
        from accreditation.models import QACase

        ok, why = super().available_for(institution)
        if not ok:
            return ok, why
        if not any(case_services.can_send_back_to_audit(c) for c in QACase.unscoped.filter(institution=institution)):
            return False, "No QA case of this institution is at stage 7 or later."
        return True, ""

    def validate(self, target, payload):
        from accreditation import case_services

        self.check_status(target)
        case = self._case(target, payload)
        if case is None:
            raise ValidationError("Choose a QA case of this institution.")
        if not case_services.can_send_back_to_audit(case):
            raise ValidationError(f"{case.reference} is not at stage 7 or later any more.")

    def preview(self, target, payload):
        from accreditation import case_services

        case = self._case(target, payload)
        if case is None:
            return []
        now = ", ".join(s.label for s in case_services.stage_objects(case))
        return [DiffRow(f"Stage of {case.reference}", now, case_services.stage_label("audit_personnel_cost"))]

    def apply(self, target, payload, actor):
        from accreditation import case_services

        case = self._case(target, payload)
        case_services.send_back_to_audit(case, actor=actor,
                                         remark=payload.get("reason", "") or "Sent back to Audit")


def latest_impersonation(staff, institution):
    return (ImpersonationSession.unscoped.filter(staff=staff, institution=institution, ended_at__isnull=True)
            .order_by("-started_at").first())
