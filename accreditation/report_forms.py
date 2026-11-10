"""v2.4 — template-driven visit reports at SOP-03 stage 12 (system code).

The panel fills two forms built on the SSQ engine (``accreditation/ssq.py``)
from templates held by two inactive "report" QA types, so they are edited and
versioned in Settings → SSQ templates like any SSQ:

* ``programme_evaluation`` (NBTE/P/4A) — one per post-visit report
  (programme). Ratings → score → Full / Interim / Denied
  (``report_scoring.evaluate``). Submitting it writes the team
  recommendation, the findings by section and a summary onto the
  ``PostVisitReport``, so the existing endorsement chain (B14) and stage 13
  carry it.
* ``ict_verification`` (NBTE/QA/ICT/VVT/001 v3) — one per visit (all its
  programmes). Weighted 0–4 components → /400, mandatory items, capacity
  arithmetic, evidence register, computed N3 recommendation
  (``report_scoring.ict``).

Both are prefilled from the record (case, institution, SSQ submissions,
shortfall analyses, the facilities register and the panel's found-vs-declared
findings). Who may write: the same rule as the report itself (B17 — someone
who attended the visit, after it is recorded).
"""
# scoped-by-caller
from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from . import report_scoring
from . import ssq as engine
from .models import QAType, SSQTemplate, VisitReportAttachment, VisitReportForm

EVALUATION, ICT = VisitReportForm.EVALUATION, VisitReportForm.ICT
HOLDER_TYPES = {EVALUATION: "report_programme_evaluation", ICT: "report_ict_verification"}
# Computed outcome → PostVisitReport.recommendation
RECOMMENDATION = {"full": "accredit", "interim": "interim", "denied": "deny"}
# A resource inspection approves a new programme to commence (interim
# accreditation); a partial result needs a revisit.
RECOMMENDATION_BY_TYPE = {"resource_inspection": {"full": "interim", "interim": "revisit", "denied": "deny"}}
FINDING_SOURCES = {  # PostVisitReport.findings key → evaluation comment fields
    "facilities": ("classrooms", "studios", "laboratories", "workshops", "equipment", "offices", "library"),
    "staffing": ("core_staff", "service_staff", "technical_staff", "admin_staff", "headship", "staff_development"),
    "curriculum": ("goal_objectives", "curriculum", "examinations", "student_interview", "success_rate",
                   "practical_coverage", "projects", "grading", "moderation", "employer_rating"),
    "governance": ("admission", "regulations", "ent_centre", "ent_curriculum", "ent_projects", "funding"),
}


# ===========================================================================
# Templates and forms
# ===========================================================================
def template_for(kind):
    qa_type = QAType.objects.filter(code=HOLDER_TYPES[kind]).select_related("ssq_template").first()
    template = qa_type.ssq_template if qa_type is not None else None
    if template is None or template.status != SSQTemplate.PUBLISHED:
        return None
    return template


def available():
    return {kind: template_for(kind) is not None for kind in HOLDER_TYPES}


def forms_of(report):
    """(evaluation form or None, ICT form or None) without creating anything."""
    evaluation = VisitReportForm.unscoped.filter(report=report, kind=EVALUATION).first()
    ict = VisitReportForm.unscoped.filter(inspection=report.inspection, report__isnull=True, kind=ICT).first()
    return evaluation, ict


@transaction.atomic
def ensure_forms(report):
    """The programme evaluation form of a report and the ICT form of its
    visit, created (and prefilled) on first use from the current published
    templates. A form keeps the template version it was opened with."""
    evaluation, ict = forms_of(report)
    if evaluation is None:
        template = template_for(EVALUATION)
        if template is not None:
            evaluation = VisitReportForm.unscoped.create(
                inspection=report.inspection, report=report, kind=EVALUATION, template=template,
                data=prefill_evaluation(report), meta={"prefilled": True})
            _recompute(evaluation)
    if ict is None:
        template = template_for(ICT)
        if template is not None:
            ict = VisitReportForm.unscoped.create(
                inspection=report.inspection, report=None, kind=ICT, template=template,
                data=prefill_ict(report.inspection), meta={"prefilled": True})
            _recompute(ict)
    return evaluation, ict


# ===========================================================================
# Prefill from the record
# ===========================================================================
def latest_submission(row):
    from .models import Submission

    submission = (Submission.unscoped.filter(ssq__qa_request_programme=row)
                  .select_related("ssq__template")
                  .order_by("-submitted_at", "-ssq__version", "-version").first())
    return (submission.ssq, submission) if submission is not None else (None, None)


def ssq_data(row):
    _ssq, submission = latest_submission(row)
    if submission is not None:
        return submission.data_snapshot or {}
    return {}


def _dates(inspection):
    start = inspection.actual_start or inspection.scheduled_start
    end = inspection.actual_end or inspection.scheduled_end
    if start and end and start != end:
        return f"{start:%d %b %Y} – {end:%d %b %Y}"
    return f"{start:%d %b %Y}" if start else ""


def verified_equipment_pct(report):
    """Share of NBTE's list found working at the visit (the panel's found vs
    required figures, weighted like the SSQ shortfall: critical items double)."""
    from . import visit_report as vr

    rows = vr.catalogue_rows(report)
    total = got = Decimal("0")
    for row in rows:
        item, finding = row["item"], row["finding"]
        if finding is None or not item.required_quantity:
            continue
        weight = Decimal(str(item.weight or 1)) * (2 if item.is_critical else 1)
        ratio = min(Decimal(finding.found_quantity) / Decimal(item.required_quantity), Decimal("1"))
        total += weight
        got += weight * ratio
    if not total:
        return None
    return (Decimal("100") * got / total).quantize(Decimal("0.1"))


def prefill_evaluation(report):
    from . import register_link, shortfall
    from . import visit_report as vr

    row = report.qa_request_programme
    programme = row.programme
    case = report.inspection.case
    data = ssq_data(row)
    level = programme.level.code.upper() if programme.level_id else ""
    out = {"programme_title": f"{level} {programme.name}".strip(),
           "institution_title": f"{case.institution.name} ({case.institution.code})",
           "visit_dates": _dates(report.inspection), "qa_type": case.qa_request.qa_type.name}
    ssq, _sub = latest_submission(row)
    analysis = shortfall.latest_for(ssq) if ssq is not None else None
    if analysis is not None:
        out["ssq_shortfall"] = f"{analysis.score} % ({analysis.band})"
    summary = register_link.register_summary(programme)
    if summary is not None:
        out["register_items"] = (f"{summary['items']} items in {summary['facilities']} facilities "
                                 f"({summary['level']}, {summary['critical']} critical)")
    else:
        out["register_items"] = "Not in the facilities register yet"
    for key, ssq_key in (("verified_enrolment", None), ("verified_volumes", "library_volumes"),
                         ("verified_classrooms", "classrooms")):
        if ssq_key and data.get(ssq_key) not in (None, ""):
            out[key] = str(data[ssq_key])
    lecturers = len([r for r in data.get("core_staff") or [] if r.get("name")])
    if lecturers:
        out["verified_lecturers"] = str(lecturers)
    technical = len([r for r in data.get("technical_staff") or [] if r.get("name")])
    if technical:
        out["verified_technical"] = str(technical)
    enrolment = sum(int(_num(r.get("total"))) for r in data.get("enrolment") or [])
    if enrolment:
        out["verified_enrolment"] = str(enrolment)
    pct = verified_equipment_pct(report)
    if pct is not None:
        out["verified_equipment_pct"] = str(pct)
    team = []
    for person in vr.panel_people(report.inspection):
        role = "Chairman" if person.staff is not None and report.inspection.team_lead_id and \
            getattr(person.staff, "user_id", None) == report.inspection.team_lead_id else (
                "Member (academia)" if person.resource_person is not None else "NBTE coordinator")
        home = getattr(person.resource_person, "home_institution", None) if person.resource_person else None
        team.append({"name": str(person.person), "role": role, "organisation": str(home) if home else "NBTE"})
    if team:
        out["team"] = team
    return out


def _num(value):
    try:
        return Decimal(str(value).replace(",", "")) if value not in (None, "") else Decimal("0")
    except Exception:
        return Decimal("0")


def prefill_ict(inspection):
    from .models import QARequestProgramme
    from . import shortfall

    from inspection.services import ict_assignees

    case = inspection.case
    inst = case.institution
    req = case.qa_request
    rows = list(QARequestProgramme.unscoped.filter(qa_request=req, status="active")
                .select_related("programme__level").order_by("programme__name"))
    programmes, capacity, workstations, bandwidth, lms, compliance, dates = [], 0, 0, None, "", [], []
    for row in rows:
        data = ssq_data(row)
        programme = row.programme
        streams = int(_num(data.get("running_streams")) or programme.approved_streams or 1)
        size = int(_num(data.get("stream_size")) or 30)
        cap = streams * size
        capacity += cap
        ws = int(_num(data.get("ict_workstations")))
        workstations += ws
        bw = data.get("ict_bandwidth_mbps")
        if bw not in (None, ""):
            bandwidth = max(bandwidth or Decimal("0"), _num(bw))
        lms = lms or data.get("ict_lms") or ""
        level = programme.level.code.upper() if programme.level_id else ""
        programmes.append({"programme": f"{programme.name} ({level})" if level else programme.name,
                           "qa_type": req.qa_type.name, "capacity": str(cap), "workstations": str(ws),
                           "requirement": ""})
        ssq, submission = latest_submission(row)
        if submission is not None:
            dates.append(submission.submitted_at)
        analysis = shortfall.latest_for(ssq) if ssq is not None else None
        if analysis is not None:
            compliance.append(f"{programme.name}: {analysis.score} % ({analysis.band})")
    ict = sorted((u for u in ict_assignees(inspection)
                  if getattr(u, "user_type", "") == "staff" and u.is_active),
                 key=lambda u: (u.first_name.lower(), u.last_name.lower(), u.email.lower()))
    first_ict = ict[0] if ict else None
    out = {
        "institution_name": inst.name, "institution_code": inst.code,
        "ict_officer": ((first_ict.get_full_name() or first_ict.email) if first_ict else ""),
        "ict_officer_staff_no": (getattr(getattr(first_ict, "staff_profile", None), "employee_no", "")
                                 if first_ict else ""),
        "institution_category": getattr(inst.category, "name", "") or getattr(inst.institution_type, "name", ""),
        "zone_state": ", ".join(x for x in (inst.zone, getattr(inst.state, "name", "")) if x),
        "batch_reference": case.reference, "request_type": req.qa_type.name,
        "gps_registered": getattr(inst, "gps", "") or "",
        "c_ssq_digital": "yes" if dates else "", "c_programmes": str(len(rows)),
        "c_capacity": str(capacity), "c_workstations": str(workstations), "c_lms": lms,
        "c_compliance": "; ".join(compliance), "d_programmes": programmes,
        "j_declared_units": str(workstations),
    }
    if dates:
        out["c_submission_date"] = f"{max(dates):%d %b %Y}"
    if bandwidth is not None:
        out["c_bandwidth"] = str(bandwidth)
        out["e_connectivity_declared"] = f"{bandwidth} Mbps"
    out["e_computers_declared"] = f"{workstations} workstations"
    if lms:
        out["e_lms_declared"] = lms
    start = inspection.actual_start or inspection.scheduled_start
    if start:
        out["visit_date"] = start.isoformat()
    return {k: v for k, v in out.items() if v not in (None, "")}


# ===========================================================================
# Permissions
# ===========================================================================
def can_edit(form, user):
    from inspection import services as insp

    if form.kind == EVALUATION:
        return insp.can_write(form.report, user)
    # The ICT verification belongs to the assigned ICT officer (plus the
    # Director of ICT, the ES and a super administrator) — not the whole panel.
    allowed, reason = insp.can_edit_ict(form.inspection, user)
    if not allowed:
        return allowed, reason
    if form.inspection.status != "closed":
        return False, "The Team Lead must record the visit before this report can be completed."
    return True, ""


# ===========================================================================
# Save, compute, submit
# ===========================================================================
def collect(post, template, programme=None):
    """Raw values for every non-file field of ``template`` from a POST (names
    ``f__<key>``, tables ``f__<key>__<row>__<col>``, catalogue
    ``f__<key>__<item id>`` — the SSQ workspace convention)."""
    values = {}
    for field in template.field_map().values():
        key, ftype = field["key"], field["type"]
        name = f"f__{key}"
        prefix = name + "__"
        if ftype == "file":
            continue
        present = name in post or f"{name}__present" in post or any(k.startswith(prefix) for k in post)
        if not present:
            continue
        if ftype == "multi_choice":
            values[key] = post.getlist(name) if hasattr(post, "getlist") else post.get(name, [])
        elif ftype == "table":
            rows = {}
            for k, v in post.items():
                if not k.startswith(prefix):
                    continue
                index, _, col = k[len(prefix):].partition("__")
                if index.isdigit() and col:
                    rows.setdefault(int(index), {})[col] = v
            values[key] = [rows[i] for i in sorted(rows)]
        elif ftype == "catalogue":
            values[key] = {k[len(prefix):]: v for k, v in post.items()
                           if k.startswith(prefix) and k[len(prefix):].isdigit()}
        else:
            values[key] = post.get(name, "")
    return values


def _programme(form):
    return form.report.qa_request_programme.programme if form.report_id else None


def _validate_photo(upload, field):
    from PIL import Image, UnidentifiedImageError
    from PIL.Image import DecompressionBombError

    import config
    from qa_core.files import validate_upload

    max_mb = config.get("qa.documents.max_upload_mb", 10)
    validate_upload(upload, extensions=("jpg", "jpeg", "png"), max_mb=max_mb, label=field["label"])
    position = upload.tell() if hasattr(upload, "tell") else 0
    try:
        with Image.open(upload) as image:
            image.verify()
            if image.format not in ("JPEG", "PNG"):
                raise ValidationError("Upload a JPEG or PNG photograph.")
    except (UnidentifiedImageError, OSError, DecompressionBombError):
        raise ValidationError("Upload a valid JPEG or PNG photograph.") from None
    finally:
        upload.seek(position)


@transaction.atomic
def save(form, values, *, user, uploads=None):
    """Clean and store answers (drafts may be incomplete). Returns
    {field_key: message} for values that could not be stored."""
    form = VisitReportForm.unscoped.select_for_update().get(pk=form.pk)
    ok, why = can_edit(form, user)
    if not ok:
        raise PermissionDenied(why)
    fmap = form.template.field_map()
    data = dict(form.data or {})
    errors = {}
    accepted_uploads = {}
    for key, files in (uploads or {}).items():
        field = fmap.get(key)
        if field is None or field["type"] != "file":
            if files:
                errors[key] = "This field does not accept uploads."
            continue
        files = [upload for upload in files if upload]
        if not files:
            continue
        if not field.get("multiple") and len(files) > 1:
            errors[key] = "Choose one file."
            continue
        maximum = int((field.get("validation") or {}).get("max_files") or 10)
        existing = VisitReportAttachment.unscoped.filter(form=form, field_key=key).count()
        if existing + len(files) > maximum:
            errors[key] = f"Upload no more than {maximum} file(s)."
            continue
        try:
            for upload in files:
                _validate_photo(upload, field)
        except ValidationError as exc:
            errors[key] = " ".join(exc.messages)
            continue
        accepted_uploads[key] = files
    for key, raw in (values or {}).items():
        field = fmap.get(key)
        if field is None or field["type"] == "file":
            continue
        try:
            data[key] = engine.clean_value(field, raw, programme=_programme(form))
        except ValidationError as exc:
            errors[key] = " ".join(exc.messages)
    form.data = data
    form.last_saved_by, form.last_saved_at = user, timezone.now()
    if form.status == "submitted":
        form.status = "draft"  # an edit after submission re-opens it (the report is still with the team)
    form.save(update_fields=["data", "last_saved_by", "last_saved_at", "status", "updated_at"])
    for key, files in accepted_uploads.items():
        for upload in files:
            attachment = VisitReportAttachment(form=form, field_key=key, uploaded_by=user)
            attachment.file = upload
            attachment.save()
    _recompute(form)
    return errors


def _context(form):
    """Programme type / streams for the evaluation suggestions."""
    if form.kind != EVALUATION:
        return {}
    data = ssq_data(form.report.qa_request_programme)
    return {"ssq": data, "programme_type": data.get("programme_type") or "technology",
            "streams": data.get("running_streams"), "stream_size": data.get("stream_size")}


def _recompute(form):
    result = report_scoring.compute(form.template.schema, form.data or {})
    if form.kind == EVALUATION:
        ctx = _context(form)
        result["suggestions"] = {k: list(v) for k, v in report_scoring.suggestions(
            ctx.get("ssq"), form.data, programme_type=ctx.get("programme_type"), streams=ctx.get("streams"),
            stream_size=ctx.get("stream_size")).items()}
        chosen = (form.data or {}).get("panel_recommendation") or "computed"
        result["final"] = result.get("outcome") if chosen == "computed" else chosen
        result["final_label"] = report_scoring.OUTCOME_LABELS.get(result["final"], "Not yet computed")
        qa_code = form.inspection.case.qa_request.qa_type.code
        mapping = RECOMMENDATION_BY_TYPE.get(qa_code, RECOMMENDATION)
        result["report_recommendation"] = mapping.get(result["final"] or "", "")
    else:
        result["final"] = result.get("outcome", "")
    form.score = result
    form.computed_outcome = (result.get("outcome") or "")[:30]
    form.save(update_fields=["score", "computed_outcome", "updated_at"])
    return result


def recompute(form):
    return _recompute(form)


def problems(form):
    """{field_key: message} that block submission."""
    file_counts = {}
    for field in form.template.field_map().values():
        if field["type"] == "file":
            file_counts[field["key"]] = VisitReportAttachment.unscoped.filter(
                form=form, field_key=field["key"]).count()
    out = engine.required_problems(form.template, form.data or {}, file_counts)
    data, score = form.data or {}, form.score or {}
    if form.kind == EVALUATION:
        chosen = data.get("panel_recommendation")
        if chosen and chosen != "computed" and chosen != score.get("outcome") \
                and not (data.get("override_reason") or "").strip():
            out["override_reason"] = (f"The ratings compute '{score.get('outcome_label', '')}'. Say why the panel "
                                      "recommends otherwise.")
        if score.get("final") in ("interim", "denied") and not (data.get("conditions") or "").strip():
            out["conditions"] = "State what the institution must do to reach the minimum standard."
    else:
        for row in score.get("missing_evidence") or []:
            key = next((c["field"] for c in score.get("components", []) if c["label"] == row["label"]), "")
            target = f"{key}_evidence" if key else "k_register"
            out[target] = ("Cite at least one evidence reference from Section K." if not row["refs"] else
                           f"Not in the evidence register (Section K): {', '.join(row['unknown'])}.")
        chosen = data.get("n3_recommendation")
        if (chosen == "obstructed" or (chosen and chosen != "computed" and chosen != score.get("computed"))) \
                and not (data.get("n3_reason") or "").strip():
            out["n3_reason"] = "Give the reason."
        if data.get("o_signed") == "no" and not (data.get("o_refusal") or "").strip():
            out["o_refusal"] = "Record who declined to sign and the reason given."
    return out


@transaction.atomic
def submit(form, *, user):
    """Freeze the form; for the programme evaluation, write the outcome onto
    the post-visit report (team recommendation, findings, summary)."""
    form = VisitReportForm.unscoped.select_for_update().get(pk=form.pk)
    ok, why = can_edit(form, user)
    if not ok:
        raise PermissionDenied(why)
    _recompute(form)
    issues = problems(form)
    if issues:
        raise ValidationError(issues)
    form.rendered_snapshot = engine.render_snapshot(form.template, form.data or {}, programme=_programme(form))
    attachments = {}
    for item in VisitReportAttachment.unscoped.filter(form=form):
        attachments.setdefault(item.field_key, []).append(item.display_name)
    for section in form.rendered_snapshot:
        for field in section.get("fields", []):
            if field.get("type") == "file":
                field["value"] = ", ".join(attachments.get(field["key"], []))
    form.status, form.submitted_by, form.submitted_at = "submitted", user, timezone.now()
    form.save(update_fields=["rendered_snapshot", "status", "submitted_by", "submitted_at", "updated_at"])
    if form.kind == EVALUATION:
        apply_to_report(form, user=user)
    return form


def summary_text(form):
    score = form.score or {}
    data = form.data or {}
    lines = [f"Programme evaluation (NBTE/P/4A): {score.get('score') or '—'} % — "
             f"{score.get('final_label') or score.get('outcome_label')}."]
    groups = ", ".join(f"{g['label']} {g['score']} %" for g in score.get("groups", []) if g.get("score"))
    if groups:
        lines.append(f"Sections: {groups}.")
    if score.get("reasons"):
        lines.append("Basis: " + " ".join(score["reasons"]))
    if data.get("strengths"):
        lines.append(f"Strengths: {data['strengths']}")
    weak = [f"{w.get('weakness')} → {w.get('action') or 'action to be stated'}" for w in data.get("weaknesses") or []
            if w.get("weakness")]
    if weak:
        lines.append("Weaknesses: " + "; ".join(weak) + ".")
    if data.get("conditions"):
        lines.append(f"Conditions: {data['conditions']}")
    return "\n".join(lines)


def apply_to_report(form, *, user):
    from inspection import services as insp

    report = form.report
    data = form.data or {}
    findings = dict(report.findings or {})
    for key, sources in FINDING_SOURCES.items():
        parts = []
        for src in sources:
            rating = data.get(src)
            comment = (data.get(f"{src}_comment") or "").strip()
            if not rating and not comment:
                continue
            field = form.template.field_map().get(src) or {}
            label = field.get("label", src)
            parts.append(f"{label}: {report_scoring._label(field, rating) if rating else '—'}"
                         + (f" — {comment}" if comment else ""))
        if parts and not (findings.get(key) or "").strip():
            findings[key] = "\n".join(parts)
    recommendation = (form.score or {}).get("report_recommendation") or None
    summary = report.summary if len((report.summary or "").strip()) >= 20 else summary_text(form)
    return insp.save_report(report, user=user, summary=summary, findings=findings, recommendation=recommendation)


# ===========================================================================
# Rendering helpers (screens and the PDF)
# ===========================================================================
def field_rows(form, errors=None):
    """Sections with field dicts ready for the form template."""
    data = form.data or {}
    errors = errors or {}
    suggestions = (form.score or {}).get("suggestions") or {}
    sections = []
    for section in form.template.sections():
        fields = []
        for field in section.get("fields", []):
            item = dict(field)
            value = data.get(field["key"])
            item["value"] = value if value is not None else ([] if field["type"] == "multi_choice" else "")
            item["choices"] = engine.option_pairs(field)
            item["error"] = errors.get(field["key"], "")
            item["suggestion"] = suggestions.get(field["key"])
            if field["type"] == "table":
                rows = list(value or [])
                item["rows"] = rows + [{} for _ in range(max(2, int(field.get("min_rows") or 0) - len(rows)))]
                rules = field.get("validation") or {}
                item["render_max_rows"] = int(field.get("max_rows") or rules.get("max_rows") or 200) + 2
                item["columns"] = [dict(c, choices=[(o.get("value"), o.get("label")) if isinstance(o, dict)
                                                    else (o, o) for o in c.get("options") or []])
                                   for c in field.get("columns", [])]
            elif field["type"] == "file":
                item["attachments"] = VisitReportAttachment.unscoped.filter(
                    form=form, field_key=field["key"])
            fields.append(item)
        sections.append({"key": section["key"], "title": section["title"],
                         "description": section.get("description", ""), "fields": fields})
    return sections


def pdf_bytes(form):
    from qa_core.pdf_report import Report

    schema = form.template.schema or {}
    case = form.inspection.case
    title = form.template.name
    subject = (f"{case.institution.name} · {form.report.qa_request_programme.programme}" if form.report_id
               else f"{case.institution.name} · {form.inspection.reference}")
    rep = Report(title, subject=subject, cover=False,
                 facts=[("Form", schema.get("form", "")), ("Case", case.reference),
                        ("Visit", form.inspection.reference), ("Template", f"v{form.template.version}"),
                        ("Status", form.get_status_display())])
    rep.h1(title)
    rep.para(schema.get("intro", ""), "small")
    score = form.score or {}
    rep.h2("Score and recommendation")
    if form.kind == EVALUATION:
        rep.kv([("Score", f"{score.get('score') or '—'} %"), ("Computed", score.get("outcome_label", "")),
                ("Panel's recommendation", score.get("final_label", "")),
                ("Report recommendation", score.get("report_recommendation", ""))])
        rep.table(["Section", "Weight", "Score %"], [[g["label"], g["weight"], g["score"]]
                                                     for g in score.get("groups", [])], widths=[0.6, 0.2, 0.2])
        for reason in score.get("reasons", []):
            rep.para(f"• {reason}", "small")
    else:
        rep.kv([("Points", f"{score.get('points')} / {score.get('max_points')}"),
                ("Percentage", f"{score.get('score') or '—'} %"), ("Band", score.get("band", "")),
                ("Mandatory items not met", str(len(score.get("mandatory_not_met") or []))),
                ("Critical / major / minor", " / ".join(str((score.get("deficiencies") or {}).get(k, 0))
                                                        for k in ("Critical", "Major", "Minor"))),
                ("Students per working workstation", score.get("students_per_workstation") or "—"),
                ("Recommendation (N3)", score.get("outcome_label", ""))])
    snapshot = form.rendered_snapshot or engine.render_snapshot(form.template, form.data or {})
    for section in snapshot:
        rep.h2(section["title"])
        pairs = []
        for field in section["fields"]:
            if field["type"] == "table":
                if pairs:
                    rep.kv(pairs, cols=1)
                    pairs = []
                cols = field.get("columns") or []
                rep.para(field["label"], "small")
                rep.table([c["label"] for c in cols], [[r.get(c["key"], "") for c in cols]
                                                       for r in field.get("rows") or []], empty="None recorded.")
            elif field["type"] != "catalogue":
                pairs.append((field["label"], field.get("value") or "—"))
        if pairs:
            rep.kv(pairs, cols=1)
    return rep.build()
