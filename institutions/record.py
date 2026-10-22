"""The institutional record (Phase 8): one NBTE-branded PDF with everything
about an institution, a ZIP with that PDF and all its files, and the
timeline export (PDF / CSV).

Everything is read as the requesting user (``for_user`` / the apps'
``visible_*`` helpers), so a record never shows more than that user could
open on screen; sections the user may not see say so. The views audit-log
every download.
"""
# scoped-by-caller
from __future__ import annotations

import io
import os
import re
import zipfile

from django.apps import apps
from django.db.models import Q
from django.utils import timezone

from qa_core.pdf_report import Report, text

from . import programme_info, services
from .models import Institution, InstitutionDocument, Programme


def _money(value):
    try:
        return f"NGN {value:,.2f}"
    except (TypeError, ValueError):
        return str(value or "")


def _who(user):
    if user is None:
        return "System"
    return user.get_full_name() or getattr(user, "email", "") or str(user)


def can_see_payments(user):
    return bool(user.is_superuser or user.has_perm("payments.view_obligation"))


def timeline_queryset(user, inst, *, vis="", tq=""):
    from qa_core.models import TimelineEvent

    qs = TimelineEvent.objects.for_user(user).filter(institution=inst).select_related("actor")
    if vis in ("public", "internal"):
        qs = qs.filter(visibility=vis)
    if not programme_info.can_see_internal(user, inst):
        qs = qs.filter(visibility="public")
    if tq:
        qs = qs.filter(Q(public_text__icontains=tq) | Q(internal_text__icontains=tq) | Q(event_code__icontains=tq))
    return qs.order_by("-at", "-id")


def timeline_rows(qs, limit=20000):
    for e in qs[:limit]:
        yield [timezone.localtime(e.at).strftime("%Y-%m-%d %H:%M"), e.event_code, e.public_text, e.internal_text,
               e.get_visibility_display(), _who(e.actor) if e.actor_id else "System"]


TIMELINE_HEADERS = ["When", "Event", "What happened", "NBTE note", "Visibility", "By"]


def timeline_pdf(inst, user, qs, *, filters=""):
    rep = Report("Institution timeline", subject=f"{inst.name} ({inst.code})", generated_by=user, cover=False,
                 landscape=True)
    rep.h1(f"Timeline — {inst.name} ({inst.code})")
    if filters:
        rep.para(f"Filters: {filters}", "small")
        rep.spacer(4)
    rep.table(["When", "Event", "What happened", "NBTE note", "By"],
              [[r[0], r[1], r[2], r[3], r[5]] for r in timeline_rows(qs)],
              widths=[0.12, 0.14, 0.36, 0.26, 0.12], empty="No events match.")
    return rep.build()


# ===========================================================================
# The record
# ===========================================================================
def _flow_events(subject, code):
    from qa_core import flow

    instance = flow.instance_for(subject, code)
    if instance is None:
        return []
    labels = {s.code: s.label for s in instance.flow_version.stages.all()}
    rows = []
    for ev in instance.events.select_related("actor").order_by("at", "id"):
        rows.append([ev.at, labels.get(ev.from_stage, ev.from_stage) or "—", labels.get(ev.to_stage, ev.to_stage),
                     ev.action_code.replace("_", " "), _who(ev.actor) if ev.actor_id else "System", ev.remark])
    return rows


def gather(inst, user):
    """Everything about ``inst`` that ``user`` may see, as plain data."""
    from accreditation import case_services as cs
    from accreditation.models import SSQ, Application, DepartmentalClearance, Query, QARequest, QARequestProgramme
    from accreditation.models import Response as QueryResponse
    from accreditation.models import Submission
    from inspection import services as insp
    from inspection.models import InspectionTeamMember, PostVisitPersonnel

    data = {"inst": inst}
    data["contacts"] = list(services.contact_history(inst))
    data["addresses"] = list(services.addresses_of(inst, current=False))
    data["members"] = list(services.members_of(inst))
    data["status_history"] = _flow_events(inst, services.LIFECYCLE) if hasattr(services, "LIFECYCLE") else []
    programmes = list(Programme.objects.for_user(user).filter(institution=inst)
                      .select_related("level", "category").order_by("name", "level__sort_order"))
    bodies = programme_info.bodies_map(programmes)
    for p in programmes:
        p.bodies = bodies.get(p.pk, [])
        p.approval_type_name = programme_info.approval_type_name(p)
    data["programmes"] = programmes
    data["documents"] = list(InstitutionDocument.objects.for_user(user).filter(institution=inst)
                             .select_related("kind").order_by("kind__name", "-version"))
    data["applications"] = list(Application.objects.for_user(user).filter(institution=inst).order_by("created_at"))
    data["clearances"] = list(DepartmentalClearance.objects.for_user(user).filter(application__institution=inst)
                              .select_related("application", "department", "decided_by")
                              .order_by("application_id", "round", "opened_at"))
    data["requests"] = list(QARequest.objects.for_user(user).filter(institution=inst).select_related("qa_type")
                            .order_by("created_at"))
    data["request_rows"] = list(QARequestProgramme.objects.for_user(user).filter(qa_request__institution=inst)
                                .select_related("qa_request", "programme", "programme__level"))
    data["ssqs"] = list(SSQ.objects.for_user(user).filter(qa_request_programme__qa_request__institution=inst)
                        .select_related("qa_request_programme__qa_request", "qa_request_programme__programme")
                        .order_by("created_at"))
    data["submissions"] = list(Submission.objects.for_user(user)
                               .filter(ssq__qa_request_programme__qa_request__institution=inst)
                               .select_related("ssq__qa_request_programme__programme").order_by("submitted_at")) \
        if any(f.name == "submitted_at" for f in Submission._meta.get_fields()) else \
        list(Submission.objects.for_user(user).filter(ssq__qa_request_programme__qa_request__institution=inst))
    cases = list(cs.visible_cases(user).filter(institution=inst).order_by("created_at"))
    for case in cases:
        case.events = _flow_events(case, "qa_case")
    data["cases"] = cases
    data["reports"] = list(insp.visible_reports(user).filter(inspection__case__institution=inst)
                           .select_related("inspection", "qa_request_programme__programme"))
    inspections = list(insp.visible_inspections(user).filter(case__institution=inst).order_by("scheduled_start"))
    for i in inspections:
        i.team_rows = list(InspectionTeamMember.unscoped.filter(inspection=i).select_related(
            "staff", "qa_mapping__resource_person", "qa_mapping__qa_personnel__staff"))
        i.attendance_rows = list(PostVisitPersonnel.unscoped.filter(inspection=i))
    data["inspections"] = inspections
    data["decisions"] = []
    for name in ("Decision", "OutcomeLetter"):
        try:
            model = apps.get_model("accreditation", name)
        except LookupError:
            continue
        mgr = model.objects
        qs = mgr.for_user(user) if hasattr(mgr, "for_user") else mgr.all()
        fields = {f.name for f in model._meta.get_fields()}
        if "institution" in fields:
            qs = qs.filter(institution=inst)
        elif "case" in fields:
            qs = qs.filter(case__in=[c.pk for c in cases])
        else:
            continue
        data["decisions"] += [(model._meta.verbose_name.capitalize(), obj) for obj in qs]
    data["can_see_payments"] = can_see_payments(user)
    if data["can_see_payments"]:
        from payments.models import PaymentAdvice, PaymentObligation, ProofOfPayment, Refund

        data["obligations"] = list(PaymentObligation.objects.for_user(user).filter(institution=inst)
                                   .order_by("created_at"))
        data["advices"] = list(PaymentAdvice.objects.for_user(user).filter(obligation__institution=inst)
                               .select_related("obligation"))
        data["proofs"] = list(ProofOfPayment.objects.for_user(user).filter(obligation__institution=inst)
                              .select_related("obligation"))
        data["refunds"] = list(Refund.objects.for_user(user).filter(obligation__institution=inst)
                               .select_related("obligation"))
    data["queries"] = list(Query.objects.for_user(user).filter(institution=inst).order_by("created_at"))
    data["responses"] = list(QueryResponse.objects.for_user(user).filter(query__institution=inst)
                             .select_related("query", "responded_by").order_by("at"))
    try:
        from complaints import services as complaint_services

        data["complaints"] = list(complaint_services.visible_to(user).filter(institution=inst).order_by("created_at"))
    except Exception:
        data["complaints"] = []
    data["timeline"] = timeline_queryset(user, inst)
    return data


def record_pdf(inst, user, data=None):
    data = data or gather(inst, user)
    lifecycle_label = inst.status_label
    rep = Report("Institutional record", subject=f"{inst.name} ({inst.code})", generated_by=user, toc=True,
                 facts=[("Institution", inst.name), ("Code", inst.code), ("Status", lifecycle_label),
                        ("Handled by", getattr(inst.handling_department, "name", "—")),
                        ("Programmes", len(data["programmes"]))],
                 note="This record is generated from the NBTE Quality Assurance system and shows what the "
                      "person who generated it may see. It is restricted to NBTE use.")
    # 1. Identity
    rep.h1("Identity")
    addr = inst.address("permanent_site") or inst.address()
    rep.kv([("Name", inst.name), ("Code", inst.code), ("Status", lifecycle_label),
            ("Ownership", getattr(inst.ownership, "name", "")), ("Type", getattr(inst.institution_type, "name", "")),
            ("Category", getattr(inst.category, "name", "")),
            ("Handling department", getattr(inst.handling_department, "name", "")),
            ("State / LGA / zone", f"{inst.state_name} / {inst.lga} / {inst.zone}"),
            ("Address", addr.one_line if addr else ""), ("Year established", inst.year_established),
            ("CAC RC number", inst.cac_rc_number), ("Website", inst.website),
            ("Origin", inst.get_origin_display()), ("Approved", inst.approved_at),
            ("Portal access", "On" if inst.portal_enabled else "Off"), ("Desk officer", _who(inst.desk_officer)
                                                                         if inst.desk_officer_id else "")])
    if inst.delisted_at:
        rep.kv([("Delisted", inst.delisted_at), ("Reason", inst.delist_reason)])
    rep.h2("Former names")
    rep.table(["Name", "Until"], [[n.get("name"), n.get("until")] for n in (inst.former_names or [])],
              widths=[0.7, 0.3], empty="No former names.")
    rep.h2("Status history")
    rep.table(["When", "From", "To", "Action", "By", "Remark"], data["status_history"],
              widths=[0.15, 0.14, 0.14, 0.14, 0.15, 0.28], empty="No status changes recorded.")
    # 2. Contacts, addresses, users
    rep.h1("Contacts, addresses and portal users")
    rep.h2("Contacts (with history)")
    rep.table(["Kind", "Name", "Value", "Current", "Since", "Until"],
              [[c.get_kind_display(), c.name, c.value, "Yes" if c.is_current else "No", c.created_at, c.ended_at]
               for c in data["contacts"]], widths=[0.16, 0.19, 0.25, 0.1, 0.15, 0.15])
    rep.h2("Addresses (with history)")
    rep.table(["Kind", "Address", "Current", "From", "Until"],
              [[a.get_kind_display(), a.one_line, "Yes" if a.is_current else "No", a.started_at, a.ended_at]
               for a in data["addresses"]], widths=[0.15, 0.43, 0.1, 0.16, 0.16])
    rep.h2("Portal users")
    rep.table(["Name", "E-mail", "Role", "Primary", "Active", "Credentials sent"],
              [[_who(m.user), m.user.email, getattr(m.institution_role, "name", ""),
                "Yes" if m.is_primary_contact else "", "Yes" if m.user.is_active else "No",
                m.credentials_last_sent_at] for m in data["members"]],
              widths=[0.2, 0.24, 0.15, 0.09, 0.08, 0.24])
    # 3. Programmes
    rep.h1("Programmes")
    rep.table(["Programme", "Level", "Status", "Approval type", "Commenced", "Expires", "Streams",
               "Professional body"],
              [[p.name, p.level.code.upper() if p.level_id else "", p.get_status_display(), p.approval_type_name,
                p.commencement_date, p.expiry_date, p.approved_streams,
                ", ".join(b.acronym or b.name for b in p.bodies)] for p in data["programmes"]],
              widths=[0.23, 0.07, 0.11, 0.11, 0.11, 0.11, 0.08, 0.18], empty="No programmes.")
    rep.h2("Statutory documents")
    rep.table(["Document", "Version", "Status", "Uploaded", "Reviewed", "Remark"],
              [[d.kind.name, d.version, d.get_status_display(), d.created_at, d.reviewed_at, d.review_remark]
               for d in data["documents"]], widths=[0.26, 0.08, 0.16, 0.15, 0.15, 0.2])
    # 4. Applications and clearances
    rep.h1("Applications and clearances")
    rep.table(["Reference", "Status", "Submitted", "Returned", "Return remark"],
              [[a.reference, a.status.replace("_", " "), a.submitted_at, a.returned_at, a.return_remark]
               for a in data["applications"]], widths=[0.2, 0.18, 0.16, 0.16, 0.3], empty="No applications.")
    rep.table(["Application", "Department", "Round", "Status", "Decided", "By", "Remark"],
              [[c.application.reference, c.department.name, c.round, c.get_status_display(), c.decided_at,
                _who(c.decided_by) if c.decided_by_id else "", c.remark] for c in data["clearances"]],
              widths=[0.16, 0.16, 0.07, 0.1, 0.14, 0.14, 0.23], empty="No clearances.")
    # 5. QA requests and SSQs
    rep.h1("QA requests and SSQ submissions")
    rep.table(["Reference", "Type", "Status", "Submitted", "Joint QA", "Programmes"],
              [[r.reference, r.qa_type.name if r.qa_type_id else "", r.status.replace("_", " "), r.submitted_at,
                "Yes" if r.is_joint_qa else "",
                "; ".join(f"{x.programme} ({x.get_status_display().lower()})" for x in data["request_rows"]
                          if x.qa_request_id == r.pk)] for r in data["requests"]],
              widths=[0.16, 0.16, 0.14, 0.13, 0.07, 0.34], empty="No QA requests.")
    rep.h2("SSQs")
    rep.table(["Request", "Programme", "Version", "Status", "Submitted", "Reviewed", "Review remark"],
              [[s.qa_request_programme.qa_request.reference, str(s.qa_request_programme.programme), s.version,
                s.status.replace("_", " "), s.submitted_at, s.reviewed_at, s.review_remark] for s in data["ssqs"]],
              widths=[0.14, 0.2, 0.07, 0.13, 0.13, 0.13, 0.2], empty="No SSQs.")
    rep.para(f"{len(data['submissions'])} SSQ submission snapshot(s) are kept on the system.", "small")
    # 6. Cases
    rep.h1("QA cases")
    if not data["cases"]:
        rep.para("No QA cases you can see.", "small")
    for case in data["cases"]:
        rep.h2(f"{case.reference} — {case.status.replace('_', ' ')}")
        rep.kv([("Started", case.started_at), ("Completed", case.completed_at),
                ("Desk officer", _who(case.desk_officer) if case.desk_officer_id else ""),
                ("Programmes", case.programme_count)])
        rep.table(["When", "From", "To", "Action", "By", "Remark"], case.events,
                  widths=[0.15, 0.17, 0.17, 0.12, 0.15, 0.24], empty="No stage events.")
    reports = [r for r in data["reports"]]
    if reports:
        rep.h2("Post-visit reports and recommendations")
        rep.table(["Visit", "Programme", "Status", "Recommendation", "Note"],
                  [[r.inspection.reference, str(r.qa_request_programme.programme), (r.status or "").replace("_", " "),
                    r.get_recommendation_display(), r.recommendation_note] for r in reports],
                  widths=[0.16, 0.22, 0.16, 0.16, 0.3])
    if data["decisions"]:
        rep.h2("Decisions and letters")
        rep.table(["Kind", "Record", "Date"], [[k, str(o), getattr(o, "created_at", "")] for k, o in data["decisions"]],
                  widths=[0.2, 0.6, 0.2])
    # 7. Inspections
    rep.h1("Inspections and teams")
    if not data["inspections"]:
        rep.para("No inspections you can see.", "small")
    for i in data["inspections"]:
        rep.h2(f"{i.reference} — {i.get_kind_display()}")
        rep.kv([("Scheduled", f"{text(i.scheduled_start)} – {text(i.scheduled_end)}"),
                ("Visited", f"{text(i.actual_start)} – {text(i.actual_end)}" if i.actual_start else ""),
                ("Status", (i.status or "").replace("_", " ")), ("Team lead", _who(i.team_lead) if i.team_lead_id
                                                                   else "")])
        rep.table(["Planned team", "Role"], [[str(m), m.role_on_team] for m in i.team_rows], widths=[0.6, 0.4],
                  empty="No planned team.")
        if i.attendance_rows:
            rep.table(["Who went", "Duty", "Attended"],
                      [[str(getattr(a, "person", "") or a), a.get_duty_display() if hasattr(a, "get_duty_display")
                        else a.duty, "Yes" if a.attended else "No"] for a in i.attendance_rows],
                      widths=[0.5, 0.3, 0.2])
    # 8. Payments
    rep.h1("Bills, payments and advices")
    if not data["can_see_payments"]:
        rep.para("Payments are not shown: you do not hold payments.view_obligation.", "small")
    else:
        rep.table(["Reference", "Description", "Amount", "Paid", "Balance", "Status", "Issued"],
                  [[o.reference, o.description, _money(o.amount), _money(o.amount_paid), _money(o.balance),
                    o.status.replace("_", " "), o.created_at] for o in data["obligations"]],
                  widths=[0.15, 0.25, 0.12, 0.12, 0.12, 0.12, 0.12], empty="No bills.")
        total = sum((o.amount for o in data["obligations"]), 0)
        paid = sum((o.amount_paid for o in data["obligations"]), 0)
        rep.para(f"Total billed {_money(total)} · paid {_money(paid)}.", "small")
        rep.h2("Payment advices")
        rep.table(["Reference", "Bill", "Amount", "Generated"],
                  [[a.reference, a.obligation.reference, _money(a.amount), a.generated_at] for a in data["advices"]],
                  widths=[0.3, 0.3, 0.2, 0.2], empty="No payment advices.")
        rep.h2("Proofs of payment")
        rep.table(["Bill", "Kind", "Payer", "Paid on", "Amount", "Bank / teller"],
                  [[p.obligation.reference, p.get_kind_display(), p.payer_name, p.paid_on, _money(p.amount_claimed),
                    f"{p.bank_name} {p.teller_number}".strip()] for p in data["proofs"]],
                  widths=[0.18, 0.12, 0.2, 0.14, 0.16, 0.2], empty="No proofs uploaded.")
        if data["refunds"]:
            rep.h2("Refunds")
            rep.table(["Bill", "Amount", "Status", "Reason", "Paid"],
                      [[r.obligation.reference, _money(r.amount), r.status.replace("_", " "), r.reason, r.paid_at]
                       for r in data["refunds"]], widths=[0.18, 0.15, 0.15, 0.37, 0.15])
    # 9. Queries and complaints
    rep.h1("Queries and complaints")
    rep.table(["Reference", "Title", "Status", "Raised", "Due", "Closed"],
              [[q.reference, q.title, q.get_status_display(), q.created_at, q.due_at, q.closed_at]
               for q in data["queries"]], widths=[0.15, 0.33, 0.1, 0.14, 0.14, 0.14], empty="No queries.")
    if data["responses"]:
        rep.h2("Responses to queries")
        rep.table(["Query", "When", "By", "Response"],
                  [[r.query.reference, r.at, _who(r.responded_by), r.body] for r in data["responses"]],
                  widths=[0.15, 0.15, 0.17, 0.53])
    rep.h2("Complaints")
    rep.table(["Reference", "Subject", "Status", "Raised", "Resolved", "Closed"],
              [[c.reference, c.subject, (c.status or "").replace("_", " "), c.created_at, c.resolved_at, c.closed_at]
               for c in data["complaints"]], widths=[0.15, 0.33, 0.12, 0.14, 0.13, 0.13],
              empty="No complaints you can see.")
    # 10. Timeline
    rep.h1("Timeline")
    rep.table(["When", "Event", "What happened", "NBTE note", "By"],
              [[r[0], r[1], r[2], r[3], r[5]] for r in timeline_rows(data["timeline"])],
              widths=[0.13, 0.15, 0.33, 0.27, 0.12], empty="No events.")
    return rep.build()


# ===========================================================================
# ZIP: the PDF plus every file, in folders
# ===========================================================================
def _safe(name):
    name = re.sub(r"[^\w.\- ()]+", "_", str(name or "")).strip(" ._")
    return name[:120] or "file"


def files_for(inst, user):
    """[(folder, filename, field_file)] — every restricted file of the
    institution the user may download."""
    from qa_core.files import QAFileMixin
    from qa_core.scoping import InstitutionScopedModelMixin

    folders = {
        "institutions.institutiondocument": "Documents",
        "payments.paymentslip": "Payments/Slips",
        "payments.paymentadvice": "Payments/Advices",
        "payments.proofofpayment": "Payments/Proofs of payment",
        "accreditation.evidence": "SSQ evidence",
        "accreditation.response": "Query responses",
    }
    out, seen = [], set()
    for model in apps.get_models():
        if not (issubclass(model, QAFileMixin) and issubclass(model, InstitutionScopedModelMixin)):
            continue
        path = getattr(model, "INSTITUTION_PATH", None)
        if path is None:
            continue
        label = model._meta.label_lower
        folder = folders.get(label) or ("Letters" if "letter" in label else model._meta.verbose_name_plural.capitalize())
        qs = model.objects.for_user(user).filter(**({path: inst} if path else {"pk": inst.pk}))
        for obj in qs:
            if not obj.file or not obj.file.name:
                continue
            try:
                if not obj.can_download(user):
                    continue
            except Exception:
                continue
            prefix = ""
            for attr in ("kind", "obligation", "ssq", "query"):
                rel = getattr(obj, attr, None)
                if rel is not None and not isinstance(rel, str):
                    prefix = _safe(getattr(rel, "reference", None) or getattr(rel, "name", None) or rel) + " - "
                    break
            version = f" v{obj.version}" if getattr(obj, "version", None) else ""
            base = _safe(obj.display_name)
            stem, ext = os.path.splitext(base)
            name = f"{prefix}{stem}{version}{ext}"
            key = (folder, name)
            n = 2
            while key in seen:
                key = (folder, f"{prefix}{stem}{version} ({n}){ext}")
                n += 1
            seen.add(key)
            out.append((key[0], key[1], obj.file))
    # Complaint attachments (not institution-scoped rows: through the complaint)
    try:
        from complaints import services as complaint_services
        from complaints.models import ComplaintAttachment

        visible = complaint_services.visible_to(user).filter(institution=inst).values("pk")
        for att in ComplaintAttachment.objects.filter(**{_complaint_fk(ComplaintAttachment): visible}):
            if att.file and att.file.name and att.can_download(user):
                out.append(("Complaints", _safe(f"{att.pk} - {att.display_name}"), att.file))
    except Exception:
        pass
    return out


def _complaint_fk(model):
    for f in model._meta.get_fields():
        if getattr(f, "related_model", None) is not None and f.related_model.__name__ == "Complaint" \
                and not f.auto_created:
            return f"{f.name}__in"
    for f in model._meta.get_fields():  # through a message
        if getattr(f, "related_model", None) is not None and f.related_model.__name__ == "ComplaintMessage":
            return f"{f.name}__complaint__in"
    return "pk__in"


def record_zip(inst, user):
    pdf = record_pdf(inst, user)
    buf = io.BytesIO()
    missing = []
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{inst.code} - institutional record.pdf", pdf)
        for folder, name, field_file in files_for(inst, user):
            try:
                field_file.open("rb")
                try:
                    zf.writestr(f"{folder}/{name}", field_file.read())
                finally:
                    field_file.close()
            except Exception:
                missing.append(f"{folder}/{name}")
        if missing:
            zf.writestr("MISSING FILES.txt", "These files are recorded but could not be read from storage:\n"
                        + "\n".join(missing))
    return buf.getvalue()
