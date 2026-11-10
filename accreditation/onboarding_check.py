"""Bare-minimum check of one programme self-study (SOP-01).

The counter on ``/qa/applications/self-study/<pk>/`` answers one question
before anybody acts on the page — *does this self-study reach the bare
minimum?* It reads what the institution submitted, then pulls what NBTE
already holds about the institution and the programme and scores the two
together:

* **failing** items block nothing by themselves, but a ``clear``
  recommendation needs an explicit override and a written reason before it
  can go to the director (``onboarding_linear.send_to_director``); the
  director sees the override on the clearance card;
* **warnings** count half towards the score;
* ``meets`` = no failing item **and** ``score >= MEETS_AT``.

Everything here is a read: ``check(study)`` never writes a record, and a
data source that is missing in this installation (no document rules, no
management roles, a template without that field) leaves its check out
rather than scoring it — the score only ever describes checks that ran.

It works for the very first self-study of a programme: with nothing to
compare against, the submission is measured against NBTE's minimums and
the institution's own record, and the card says so (``compare.first``).
"""
from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

PASS, WARN, FAIL = "pass", "warn", "fail"
STATUS_LABELS = {PASS: "Met", WARN: "Warning", FAIL: "Failing"}
MEETS_AT = 70           # weighted score that still counts as "meets"
GOOD_AT = 85            # …and scores here are shown in the good band
THIN_ANSWER = 120       # characters a long answer needs to count as substantive
DEFAULT_STAFF_PER_STUDENT = 30
OVERRIDE_MIN_LENGTH = 10


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _norm(value):
    return " ".join(str(value or "").lower().split())


def _int(value, default=0):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _join(items, limit=4):
    items = [str(i) for i in items if i]
    if not items:
        return ""
    if len(items) <= limit:
        return ", ".join(items)
    return f"{', '.join(items[:limit])} and {len(items) - limit} more"


def _date(value):
    return value.strftime("%d %b %Y") if value else ""


def _row(key, label, status, detail, weight, group):
    return {"key": key, "label": label, "status": status, "status_label": STATUS_LABELS[status],
            "detail": detail, "weight": weight, "group": group}


def _fields(ssq):
    try:
        return ssq.template.field_map()
    except Exception:  # pragma: no cover - a broken schema never blocks the page
        return {}


def _staff_rows(ssq):
    data = (ssq.submitted_data or {}) if ssq.status == "submitted" else (ssq.data or {})
    rows = data.get("staffing_plan") or []
    return [r for r in rows if isinstance(r, dict) and str(r.get("name") or "").strip()]


def _students_per_stream(programme):
    try:
        if programme.category_id and programme.category.students_per_stream:
            return int(programme.category.students_per_stream)
    except Exception:  # pragma: no cover
        pass
    return DEFAULT_STAFF_PER_STUDENT


def _handling_department(inst):
    try:
        if inst.handling_department_id:
            return inst.handling_department
        return inst.derive_handling_department()
    except Exception:  # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------
def _submission_checks(ssq, add, fields, data):
    """What the institution actually put in the form (weight 2–3 each)."""
    from . import onboarding_ssq

    if ssq.status == "submitted":
        ok = bool(ssq.verify_submission())
        add("snapshot", "Submitted snapshot is intact", PASS if ok else FAIL,
            "The checksum matches the record NBTE froze at submission." if ok else
            "The checksum does not match the frozen record — investigate before clearing.", 3, "submission")
    else:
        add("snapshot", "Not submitted yet", WARN,
            "This self-study is still a draft — it is with the institution for correction.", 3, "submission")

    required = [f for f in fields.values() if f.get("required")]
    problems = onboarding_ssq.submission_problems(ssq)
    if required:
        detail = (f"All {len(required)} required items are answered." if not problems else
                  f"{len(problems)} required item(s) still empty: {_join(_labels(fields, problems))}.")
        add("required", "Every required item is answered", PASS if not problems else FAIL, detail, 3, "submission")

    readiness = fields.get("readiness_declaration")
    if readiness is not None:
        ok = _norm(data.get("readiness_declaration")) == "yes"
        add("readiness", "Institution declares it is ready", PASS if ok else FAIL,
            "The institution answered “Yes” — it confirms it can run the programme as described." if ok else
            "The readiness declaration is not “Yes”: the institution does not confirm it can run the "
            "programme as described.", 2, "submission")

    file_fields = [f for f in fields.values() if f.get("type") == "file"]
    if file_fields:
        counts = onboarding_ssq.evidence_counts(ssq)
        missing_required = [f["label"] for f in file_fields if f.get("required") and not counts.get(f["key"])]
        missing_any = [f["label"] for f in file_fields if not counts.get(f["key"])]
        status = FAIL if missing_required else (WARN if missing_any else PASS)
        detail = ("Every evidence field has a file attached." if status == PASS else
                  f"No file for: {_join(missing_required or missing_any)}."
                  + (" Evidence is required." if missing_required else " These fields are optional."))
        add("evidence", "Evidence files are attached", status, detail, 2, "submission")

    long_fields = [f for f in fields.values() if f.get("type") == "textarea"]
    if long_fields:
        thin = [f["label"] for f in long_fields
                if len(str(data.get(f["key"]) or "").strip()) < THIN_ANSWER]
        add("depth", "Long answers are substantive", PASS if not thin else WARN,
            f"Every long answer runs to at least {THIN_ANSWER} characters." if not thin else
            f"{len(thin)} of {len(long_fields)} long answer(s) under {THIN_ANSWER} characters: "
            f"{_join(thin)}.", 1, "submission")


def _programme_checks(ssq, add, fields, data, programme):
    """The submission against NBTE's programme record (weight 1–3 each)."""
    if "programme_name" in fields:
        said = str(data.get("programme_name") or "").strip()
        ok = bool(said) and _norm(said) == _norm(programme.name)
        add("identity", "Programme name matches the record", PASS if ok else FAIL,
            f"“{said or '—'}” agrees with NBTE's programme record." if ok else
            f"The form says “{said or '—'}”; the programme record says “{programme.name}”.", 2, "programme")

    if "programme_level" in fields and programme.level_id:
        said = str(data.get("programme_level") or "").strip()
        ok = bool(said) and _norm(said) == _norm(programme.level.name)
        add("level", "Award level matches the record", PASS if ok else WARN,
            f"“{said or '—'}” agrees with the record." if ok else
            f"The form says “{said or '—'}”; the programme record says “{programme.level.name}”.",
            1, "programme")

    staff = []
    if "staffing_plan" in fields:
        staff = _staff_rows(ssq)
        add("staff", "At least one programme staff member is named", PASS if staff else FAIL,
            f"{len(staff)} staff member(s) named in the staffing plan." if staff else
            "Nobody is named in the staffing plan — there is no team to run the programme.", 2, "programme")
        if staff:
            unqualified = [r for r in staff if not str(r.get("qualification") or "").strip()]
            add("qualification", "Every listed staff member carries a qualification",
                PASS if not unqualified else WARN,
                "All listed staff carry a qualification." if not unqualified else
                f"{len(unqualified)} of {len(staff)} listed without a qualification.", 1, "programme")

    if "planned_intake" in fields and staff:
        intake = _int(data.get("planned_intake"))
        if intake > 0:
            per = _students_per_stream(programme)
            capacity = len(staff) * per
            add("ratio", "Staffing matches the planned intake", PASS if capacity >= intake else WARN,
                f"{intake} planned student(s) · {len(staff)} staff member(s) · about {per} students per staff "
                f"member (≈ {capacity} supportable at NBTE's ratio).", 2, "programme")

    problems = []
    if programme.expiry_date and programme.expiry_date < timezone.localdate():
        problems.append(f"accreditation expired on {_date(programme.expiry_date)}")
    if programme.status == "dropped":
        problems.append("the programme is marked dropped")
    add("programme_record", "Programme record is in good standing", PASS if not problems else FAIL,
        f"Status “{programme.get_status_display()}”"
        + (f" — expires {_date(programme.expiry_date)}" if programme.expiry_date else "") + "."
        if not problems else "; ".join(problems).capitalize() + ".", 2, "programme")


def _institution_checks(inst, add):
    """What NBTE already holds about the institution (weight 1 each)."""
    from institutions import profile as inst_profile
    from institutions import services as inst_services

    dept = _handling_department(inst)
    add("routing", "Routed to a handling department", PASS if dept else WARN,
        f"{dept.name} handles {inst.name}." if dept else
        "No handling department could be derived, so nobody is assigned to review it.", 1, "institution")

    board = inst_profile.role_board(inst)
    mandatory = [b for b in board if b["role"].is_mandatory]
    if mandatory:
        missing = [b["role"].name for b in mandatory if not b["ok"]]
        add("management", "Mandatory management posts are filled", PASS if not missing else WARN,
            f"{len(mandatory) - len(missing)} of {len(mandatory)} mandatory posts complete."
            + (f" Incomplete: {_join(missing)}." if missing else ""), 1, "institution")

    summary = inst_services.checklist_summary(inst_services.checklist(inst))
    if summary["mandatory"]:
        missing = [r["kind"].name for r in summary["missing"]]
        add("documents", "Mandatory registration documents are on file",
            PASS if summary["complete"] else WARN,
            f"{summary['ready']} of {summary['mandatory']} mandatory documents accepted."
            + (f" Not ready: {_join(missing)}." if missing else ""), 1, "institution")

    need = (("official_email", "official e-mail"), ("phone", "phone number"),
            ("head_of_institution", "head of institution"))
    missing = [label for kind, label in need if inst.contact(kind) is None]
    add("contacts", "Contact details are on record", PASS if not missing else WARN,
        "Official e-mail, phone number and head of institution are on record." if not missing else
        f"Missing from the institution record: {_join(missing)}.", 1, "institution")


def _labels(fields, keys):
    return [fields.get(k, {}).get("label", k) for k in keys]


# ---------------------------------------------------------------------------
# What NBTE already holds (the "pulled" facts shown next to the score)
# ---------------------------------------------------------------------------
def _facts(inst, programme):
    from institutions import profile as inst_profile
    from institutions import services as inst_services

    from . import register_link
    from .models import QARequest
    from institutions.models import Programme

    inst_facts, prog_facts = [], []
    dept = _handling_department(inst)
    summary = inst_services.checklist_summary(inst_services.checklist(inst))
    board = inst_profile.role_board(inst)
    mandatory_posts = [b for b in board if b["role"].is_mandatory]
    filled_posts = [b for b in mandatory_posts if b["ok"]]

    def put(group, label, value):
        group.append((label, value if value not in (None, "") else "—"))

    put(inst_facts, "Code", inst.code)
    put(inst_facts, "Ownership", getattr(inst.ownership, "name", ""))
    put(inst_facts, "Type", getattr(inst.institution_type, "name", ""))
    put(inst_facts, "Category", getattr(inst.category, "name", ""))
    put(inst_facts, "Location", ", ".join(x for x in [getattr(inst.state, "name", ""), inst.lga] if x))
    put(inst_facts, "Established", inst.year_established or "")
    put(inst_facts, "Institution status", inst.status_label)
    put(inst_facts, "Handling department", getattr(dept, "name", ""))
    try:
        programmes = Programme.unscoped.filter(institution=inst).exclude(status="dropped").count()
    except Exception:  # pragma: no cover
        programmes = 0
    put(inst_facts, "Programmes on record", programmes)
    if summary["mandatory"]:
        put(inst_facts, "Mandatory documents", f"{summary['ready']} of {summary['mandatory']} accepted")
    if mandatory_posts:
        put(inst_facts, "Management posts", f"{len(filled_posts)} of {len(mandatory_posts)} complete")
    try:
        requests = QARequest.unscoped.filter(institution=inst).count()
    except Exception:  # pragma: no cover
        requests = 0
    put(inst_facts, "QA requests", requests)

    put(prog_facts, "Award level", getattr(programme.level, "name", ""))
    put(prog_facts, "Category", getattr(programme.category, "name", ""))
    put(prog_facts, "Programme status", programme.get_status_display())
    put(prog_facts, "Approved streams", programme.approved_streams or "")
    put(prog_facts, "Commenced", _date(programme.commencement_date))
    put(prog_facts, "Accreditation expires", _date(programme.expiry_date))
    put(prog_facts, "Professional body", getattr(programme.professional_body, "name", ""))
    resources = register_link.register_summary(programme)
    if resources:
        put(prog_facts, "NBTE minimum list",
            f"{resources['items']} items ({resources['critical']} critical) across "
            f"{resources['facilities']} facilit{'y' if resources['facilities'] == 1 else 'ies'} · "
            f"{resources['level']} quantities")
    else:
        put(prog_facts, "NBTE minimum list", "No published list for this programme yet")

    return {"institution": inst_facts, "programme": prog_facts}


# ---------------------------------------------------------------------------
# Compared with (an earlier self-study, or the honest "this is the first")
# ---------------------------------------------------------------------------
def _compare(ssq, *, score, depth=0):
    from .models import OnboardingSSQ, SSQ

    inst = ssq.application.institution
    rows, first = [], True

    prior = (OnboardingSSQ.unscoped.filter(programme_id=ssq.programme_id,
                                            application__institution=inst)
             .exclude(pk=ssq.pk).order_by("-submitted_at", "-id").first())
    if prior is not None and prior.status == "submitted":
        first = False
        earlier = None
        if depth < 1:            # never recurse: a prior study's own prior may be this one
            try:
                earlier = check(prior, depth=depth + 1, compare=False)["score"]
            except Exception:  # pragma: no cover - a broken comparison never blocks the page
                earlier = None
        detail = (f"Submitted {_date(prior.submitted_at)} for {prior.programme}."
                  if prior.submitted_at else "An earlier self-study is on record.")
        if earlier is not None:
            delta = score - earlier
            detail += (f" It scored {earlier}/100; this one scores {score}/100 "
                       f"({'+' if delta >= 0 else ''}{delta}).")
        rows.append({"label": "Earlier self-study for this programme", "detail": detail})
    elif prior is not None:
        first = False
        rows.append({"label": "Earlier self-study for this programme",
                     "detail": f"An earlier record exists ({prior.get_status_display().lower()}) and is not "
                               "counted as a submission."})

    formal = (SSQ.unscoped.filter(qa_request_programme__programme_id=ssq.programme_id,
                                  qa_request_programme__qa_request__institution=inst)
              .exclude(status="draft").order_by("-submitted_at", "-id").first())
    if formal is not None:
        first = False
        rows.append({"label": "Formal SSQ on record",
                     "detail": f"{formal.qa_request.reference} · {formal.get_status_display()} · "
                               f"{_date(formal.submitted_at) or 'no submission date'}."})

    others = list(OnboardingSSQ.unscoped.filter(application_id=ssq.application_id).exclude(pk=ssq.pk)[:9])
    if others:
        counts = [len(_staff_rows(o)) for o in others]
        average = round(sum(counts) / len(counts), 1)
        rows.append({"label": "Other self-studies in this application",
                     "detail": f"{len(others)} other programme self-stud{'y' if len(others) == 1 else 'ies'} · "
                               f"average {average} staff member(s) named · this one names "
                               f"{len(_staff_rows(ssq))}."})

    return {"first": first, "rows": rows,
            "note": f"First self-study for this programme at {inst.name} — measured against NBTE's bare "
                    "minimums and the institution's own record." if first else ""}


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------
def check(ssq, *, depth=0, compare=True):
    """``{score, meets, band, verdict, checks, failed_checks, facts, compare}``.

    Read-only; never raises (a data source that fails leaves its check out).
    """
    inst = ssq.application.institution
    programme = ssq.programme
    fields = _fields(ssq)
    data = (ssq.submitted_data or {}) if ssq.status == "submitted" else (ssq.data or {})
    rows = []

    def add(key, label, status, detail, weight, group):
        rows.append(_row(key, label, status, detail, weight, group))

    for section, args in ((_submission_checks, (ssq, add, fields, data)),
                          (_programme_checks, (ssq, add, fields, data, programme))):
        try:
            section(*args)
        except Exception:  # pragma: no cover - a broken data source never blocks the page
            logger.exception("onboarding check: %s failed for %s", section.__name__, ssq.pk)
            rows = [r for r in rows if r["key"] not in _KEYS_OF.get(section, ())]
    try:
        _institution_checks(inst, add)
    except Exception:  # pragma: no cover
        logger.exception("onboarding check: institution checks failed for %s", ssq.pk)
        rows = [r for r in rows if r["group"] != "institution"]

    total = sum(r["weight"] for r in rows)
    got = sum(r["weight"] * (1 if r["status"] == PASS else 0.5 if r["status"] == WARN else 0) for r in rows)
    score = int(round(100 * got / total)) if total else 100
    failed = [r for r in rows if r["status"] == FAIL]
    warned = [r for r in rows if r["status"] == WARN]
    meets = not failed and score >= MEETS_AT
    band = "major" if not meets else ("good" if score >= GOOD_AT else "fair")

    facts, comparison = {}, None
    try:
        facts = _facts(inst, programme)
    except Exception:  # pragma: no cover
        logger.exception("onboarding check: facts failed for %s", ssq.pk)
    if compare:
        try:
            comparison = _compare(ssq, score=score, depth=depth)
        except Exception:  # pragma: no cover
            logger.exception("onboarding check: comparison failed for %s", ssq.pk)

    return {"checks": rows, "checks_total": len(rows),
            "passed": len([r for r in rows if r["status"] == PASS]),
            "warned": len(warned), "failed": len(failed),
            "failed_checks": failed, "warned_checks": warned,
            "score": score, "meets": meets, "band": band, "threshold": MEETS_AT,
            "verdict": "Meets the bare minimum" if meets else "Below the bare minimum",
            "facts": facts, "compare": comparison}


_KEYS_OF = {_submission_checks: ("snapshot", "required", "readiness", "evidence", "depth"),
            _programme_checks: ("identity", "level", "staff", "qualification", "ratio", "programme_record")}


def latest_override(review):
    """The override on the assessment the director is looking at
    (``{score, reason, by, at}``), or None.

    Only the newest ``assessed`` row counts: an officer who sends it back and
    re-sends without an override clears the earlier one off the card.
    """
    if review is None:
        return None
    for row in reversed(list(review.history or [])):
        if row.get("event") != "assessed":
            continue
        if not row.get("override"):
            return None
        return {"score": row.get("check_score"), "reason": row.get("override_reason", ""),
                "by": row.get("by_name", ""), "at": row.get("at", "")}
    return None
