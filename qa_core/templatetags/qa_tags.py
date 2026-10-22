"""Template helpers for the QA screens."""
from django import template

register = template.Library()

_SKIP = {"updated_at", "created_at", "updated_by", "created_by", "id"}


def _short(value, limit=90):
    text = "—" if value in (None, "") else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


@register.filter
def audit_changes(changes):
    """[(field, old, new)] from an AuditLogEntry.changes dict, readable and short."""
    rows = []
    if not isinstance(changes, dict):
        return rows
    for field, diff in changes.items():
        if field in _SKIP:
            continue
        if isinstance(diff, dict) and ("old" in diff or "new" in diff):
            rows.append((field.replace("_", " "), _short(diff.get("old")), _short(diff.get("new"))))
        else:
            rows.append((field.replace("_", " "), "", _short(diff)))
    return rows[:6]


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def initials(user):
    name = (getattr(user, "get_full_name", lambda: "")() or getattr(user, "email", "") or "?").strip()
    parts = [p for p in name.replace("@", " ").split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


# ---------------------------------------------------------------------------
# Phase 2 helpers
# ---------------------------------------------------------------------------
_TONES = {
    # institutions
    "proposed": "info", "under_review": "pending", "approved": "success", "suspended": "warn", "delisted": "danger",
    # documents / clearances
    "missing": "neutral", "submitted": "pending", "accepted": "success", "returned": "danger", "expired": "warn",
    "superseded": "neutral", "pending": "pending", "cleared": "success", "closed": "neutral",
    # onboarding
    "draft": "neutral", "clearance": "pending", "institutional_fee_due": "warn", "payment_pending_confirmation": "pending",
    "fee_confirmed": "success", "programme_selection": "info", "withdrawn": "neutral", "ssq_submitted": "success",
    # payments
    "issued": "neutral", "awaiting_payment": "warn", "provider_processing": "pending",
    "provider_reported_paid": "pending", "proof_submitted": "pending", "awaiting_finance_confirmation": "pending",
    "finance_confirmation_overdue": "danger", "confirmed": "success", "rejected": "danger", "partially_paid": "warn",
    "overpaid": "success", "refund_requested": "warn", "refunded": "neutral", "waived": "info", "cancelled": "neutral",
    "initiated": "neutral", "failed": "danger", "abandoned": "neutral", "requested": "warn", "paid": "success",
    # verification decisions
    "needs_info": "warn", "applied": "success",
    # programmes
    "accredited": "success", "interim": "info", "approval_to_commence": "info", "resource_inspected": "pending",
    "denied": "danger", "dropped": "neutral",
    # Phase 8: capacity results and decisions
    "adequate": "success", "inadequate": "danger", "approve": "success", "deny": "danger", "return": "warn",
    # statement lines
    "matched": "success", "ambiguous": "warn", "unmatched": "danger", "ignored": "neutral",
}
# Phase 3: QA requests, SSQs, queries, templates
_TONES.update({
    "programme_clearance": "pending", "fee_due": "warn", "ssq_in_progress": "info", "in_workflow": "pending",
    "decided": "success", "active": "success", "replaced": "neutral", "returned_to_department": "info",
    "submitted_by_department": "info", "analysed": "success", "open": "warn", "answered": "info",
    "published": "success", "retired": "neutral", "attached": "info", "removed": "neutral",
    "awaiting_sop03": "pending", "facility": "info", "equipment": "neutral",
})
_TONES.update({  # Phase 4: SOP-03 records
    "audited": "info", "es_approved": "success", "released": "success", "proposed_entry": "info",
    "es_approved_release": "success", "reconciled": "neutral", "superseded_task": "neutral",
})


@register.filter
def tone(status):
    """Chip tone class for any QA status: qa-tone-success / -warn / …"""
    return "qa-tone-" + _TONES.get(str(status or ""), "neutral")


@register.filter
def naira(value):
    from payments.money import naira as fmt

    try:
        return fmt(value)
    except (ValueError, TypeError):
        return "—"


@register.filter
def humanize_code(value):
    return str(value or "").replace("_", " ").replace("-", " ").strip().capitalize()


@register.filter
def field_type(bound_field):
    try:
        return bound_field.field.widget.__class__.__name__.lower()
    except AttributeError:
        return ""


@register.filter
def ssq_input_name(key, suffix=""):
    """Form-field name of an SSQ answer: f__<key>[__<suffix>]."""
    return f"f__{key}" + (f"__{suffix}" if suffix != "" else "")


@register.filter
def startswith(value, prefix):
    return str(value or "").startswith(str(prefix))


ICONS = {
    "bell": '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
    # Phase 7: complaints
    "message": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "inbox": '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 '
             '2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    "trend": '<polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/>',
    "help": '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>'
            '<line x1="12" y1="17" x2="12.01" y2="17"/>',
    "up": '<polyline points="18 15 12 9 6 15"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "minus": '<line x1="5" y1="12" x2="19" y2="12"/>',
    "map": '<polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"/><line x1="8" y1="2" x2="8" y2="18"/>'
           '<line x1="16" y1="6" x2="16" y2="22"/>',
    "trash": '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
    "rewind": '<polygon points="11 19 2 12 11 5 11 19"/><polygon points="22 19 13 12 22 5 22 19"/>',
    "x": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "phone": '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.79 19.79 0 0 1 '
             '2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 '
             '0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>',
    "home": '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
    "layers": '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/>'
              '<polyline points="2 12 12 17 22 12"/>',
    "type": '<polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/>',
    "ban": '<circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>',
    "rotate": '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
    "key": '<path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.78 7.78 5.5 5.5 0 0 1 7.78-7.78zm0 0L15.5 7.5m0 0l3 3L22 '
           '7l-3-3m-3.5 3.5L19 4"/>',
    "lock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "unlock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/>',
    "mail": '<path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/>'
            '<polyline points="22,6 12,13 2,6"/>',
    "eye": '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    "edit": '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/>'
              '<line x1="12" y1="3" x2="12" y2="15"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/>'
                '<line x1="12" y1="15" x2="12" y2="3"/>',
    "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
    "card": '<rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/>',
    "bank": '<path d="M3 21h18"/><path d="M3 10h18"/><path d="M5 6l7-3 7 3"/><path d="M4 10v11"/><path d="M20 10v11"/>'
            '<path d="M8 14v3"/><path d="M12 14v3"/><path d="M16 14v3"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
    "alert": '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>'
             '<line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "building": '<path d="M3 21h18"/><path d="M5 21V7l7-4 7 4v14"/><path d="M9 21v-6h6v6"/>',
    "users": '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>'
             '<path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "sheet": '<rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/>'
             '<line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>'
            '<path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "arrow": '<polyline points="9 18 15 12 9 6"/>',
    "back": '<polyline points="15 18 9 12 15 6"/>',
    "search": '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "flag": '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/>',
    "qr": '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>'
          '<rect x="3" y="14" width="7" height="7"/><path d="M14 14h3v3h-3zM20 14v3M14 20h7"/>',
}


@register.simple_tag
def icon(name, size=18, stroke="2"):
    """Inline Feather-style icon (aria-hidden)."""
    from django.utils.safestring import mark_safe

    paths = ICONS.get(name, ICONS["edit"])
    return mark_safe(f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                     f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                     f'{paths}</svg>')


@register.filter
def doc_state_label(state):
    return {"missing": "Not uploaded", "submitted": "Awaiting review", "accepted": "Accepted",
            "returned": "Returned", "expired": "Needs a new version"}.get(state, humanize_code(state))


@register.filter
def bf(form, name):
    """Bound field by name: {{ form|bf:"lga" }}."""
    try:
        return form[name]
    except KeyError:
        return None


@register.filter
def fieldsets_of(form):
    """[(title, [bound fields])]: the form's `fieldsets` or one group."""
    sets = getattr(form, "fieldsets", None)
    if not sets:
        return [("", [form[n] for n in form.fields])]
    used, out = set(), []
    for title, names in sets:
        out.append((title, [form[n] for n in names if n in form.fields]))
        used.update(names)
    rest = [form[n] for n in form.fields if n not in used]
    if rest:
        out.append(("", rest))
    return out


@register.filter
def get_attr(obj, name):
    value = getattr(obj, name, None)
    return value() if callable(value) else value


@register.filter
def split_pairs(text):
    """"a:A,b:B" → [("a","A"), ("b","B")] for small inline option lists."""
    return [tuple(part.split(":", 1)) for part in str(text).split(",") if ":" in part]


@register.filter
def split(text, sep=" "):
    return [p for p in str(text).split(sep) if p]


@register.filter
def who(user, default="—"):
    """A person's name for display: full name, else e-mail, else `default`
    (safe when the user is None — e.g. a system event)."""
    if not user:
        return default
    return (user.get_full_name() or "").strip() or getattr(user, "email", "") or default


# ---------------------------------------------------------------------------
# Phase 7: Settings hub — readable titles and group copy
# ---------------------------------------------------------------------------
SETTING_WORDS = {"sla": "SLA", "ssq": "SSQ", "ict": "ICT", "es": "ES", "qa": "QA", "id": "ID", "url": "URL",
                 "cqac": "CQAC", "nbte": "NBTE", "tvet": "TVET", "rp": "resource person", "pct": "%",
                 "smtp": "SMTP", "lga": "LGA", "cac": "CAC", "kb": "knowledge base"}

GROUP_COPY = {
    "Fees and payments": ("Money in", "What institutions pay, how they pay it, and who confirms the payment."),
    "Institutions and documents": ("Institutions", "Registration rules, the documents NBTE asks for, and who may "
                                                   "act for an institution."),
    "Workflow and SLA": ("Deadlines and routing", "How long each stage may take, reminders, and how work is "
                                                   "handed out."),
    "Eligibility and qualifications": ("Who may serve", "The §9 rules for resource persons and institution "
                                                        "officers. There is no manual override."),
    "Credentials, portal and impersonation": ("Portal and sign-in", "Self-registration, set-password links, "
                                                                     "impersonation and portal banners."),
    "Interaction (§20)": ("Screens", "Page sizes, request time-outs and other interface limits."),
    "Settings": ("Settings control", "Who may change settings and how changes are confirmed."),
    "Complaints": ("Complaints", "Service levels, escalation timing and what raisers can see."),
}


@register.filter
def setting_title(key):
    """``qa.portal.self_registration_enabled`` → "Self registration enabled"."""
    parts = [p for p in (key or "").split(".") if p and p != "qa"]
    tail = parts[-1] if parts else key
    if len(parts) >= 3:  # qa.fee.qa_request.base → "QA request base"
        tail = parts[-2] + "_" + parts[-1]
    words = tail.replace("-", "_").split("_")
    out = [SETTING_WORDS.get(w, w) for w in words if w]
    if not out:
        return key
    text = " ".join(out)
    return text[:1].upper() + text[1:]


@register.filter
def setting_area(key):
    """The middle part of the key, readable: ``qa.portal.x`` → "Portal"."""
    parts = [p for p in (key or "").split(".") if p and p != "qa"]
    if len(parts) < 2:
        return ""
    return " · ".join(SETTING_WORDS.get(p, p.replace("_", " ")).capitalize() for p in parts[:-1])


@register.filter
def group_title(group):
    name = (group or "General").replace("QA · ", "")
    return GROUP_COPY.get(name, (name, ""))[0]


@register.filter
def group_blurb(group):
    name = (group or "General").replace("QA · ", "")
    return GROUP_COPY.get(name, (name, ""))[1]
