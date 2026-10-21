"""The staff file — one timeline of everything that happened to a staff member.

    events = timeline(staff)          # newest first
    held = items_held(staff)          # assets currently assigned

Sources: record created, sign-in account created, nominal-roll imports,
state changes, promotions and rollbacks, transfers (department / office /
zonal office), assets assigned and returned, and messages / notifications
sent from the staff page.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

from django.urls import NoReverseMatch, reverse
from django.utils import timezone

KINDS = {
    "career": "Career",
    "assets": "Items",
    "contact": "Messages",
    "record": "Record",
}


@dataclass
class Event:
    when: datetime
    kind: str            # career | assets | contact | record
    icon: str            # promotion | transfer | state | asset | return | message | notify | import | create | account
    title: str
    detail: str = ""
    by: str = ""
    url: str = ""
    tone: str = ""       # "" | "warn"
    extra: dict = field(default_factory=dict)

    @property
    def kind_label(self):
        return KINDS.get(self.kind, self.kind)


def _dt(value):
    if isinstance(value, datetime):
        return timezone.localtime(value) if timezone.is_aware(value) else timezone.make_aware(value)
    if isinstance(value, date):
        return timezone.make_aware(datetime.combine(value, time(12, 0)))
    return timezone.now()


def _who(user):
    if user is None:
        return ""
    return user.get_full_name() or user.email


def _place(dept, office):
    parts = [p.name for p in (dept, office) if p is not None]
    return " · ".join(parts) or "—"


def timeline(staff, limit=200):
    events = []
    events.append(Event(_dt(staff.created_at), "record", "create", "Staff record created",
                        detail=f"File no. {staff.employee_no}"))

    user = staff.user if staff.user_id else None
    if user is not None:
        events.append(Event(_dt(user.date_joined), "record", "account", "Sign-in account created",
                            detail="Signs in with the file number" if not user.email or
                            user.email.endswith(".invalid") else f"Signs in as {user.email}"))

    for h in staff.status_history.select_related("from_state", "to_state", "changed_by"):
        title = (f"State: {h.from_state.name} → {h.to_state.name}" if h.from_state and h.to_state
                 else f"State set to {h.to_state.name if h.to_state else '—'}")
        events.append(Event(_dt(h.changed_at), "career", "state", title, detail=h.reason,
                            by=_who(h.changed_by)))

    for p in staff.promotions.select_related("from_designation", "to_designation", "approved_by", "reverses"):
        grade = f" · GL {p.from_grade_level or '—'} → {p.to_grade_level or '—'}" if (
            p.from_grade_level or p.to_grade_level) else ""
        title = ("Promotion rolled back: " if p.is_reversal else "Promoted: ") + \
            f"{p.from_designation.name if p.from_designation else '—'} → {p.to_designation.name}"
        detail = p.reason
        if p.cascade_source_id:
            detail = ("Cascade. " + detail).strip()
        events.append(Event(_dt(p.effective_date), "career", "promotion", title + grade, detail=detail,
                            by=_who(p.approved_by), tone="warn" if p.is_reversal else ""))

    for t in staff.transfers.select_related("from_department", "to_department", "from_office",
                                            "to_office", "recorded_by"):
        title = f"Transferred: {_place(t.from_department, t.from_office)} → {_place(t.to_department, t.to_office)}"
        detail = t.reason or {"edit": "Changed on the staff record",
                              "import": "Changed by a nominal roll import"}.get(t.source, "")
        events.append(Event(_dt(t.effective_date), "career", "transfer", title, detail=detail,
                            by=_who(t.recorded_by)))

    if user is not None:
        try:
            from assets.models import AssetAssignment
        except Exception:  # assets app missing
            AssetAssignment = None
        if AssetAssignment is not None:
            for a in (AssetAssignment.objects.filter(assigned_to=user)
                      .select_related("asset", "asset__category", "assigned_by", "returned_to")):
                url = _asset_url(a.asset)
                events.append(Event(_dt(a.assigned_at), "assets", "asset", f"Given {a.asset}",
                                    detail=a.notes or (f"Location: {a.location_on_assign}"
                                                       if a.location_on_assign else ""),
                                    by=_who(a.assigned_by), url=url))
                if a.returned_at:
                    cond = a.get_condition_on_return_display() if a.condition_on_return else ""
                    events.append(Event(_dt(a.returned_at), "assets", "return", f"Returned {a.asset}",
                                        detail=f"Condition: {cond}" if cond else "",
                                        by=_who(a.returned_to), url=url))

    from core.models import AuditLogEntry
    entries = AuditLogEntry.objects.filter(
        model_label="staff.Staff", object_pk=str(staff.pk),
        action__in=["import_nominal_roll", "staff_contact"]).select_related("actor")
    for e in entries:
        ch = e.changes or {}
        if e.action == "import_nominal_roll":
            sheet = (ch.get("source") or "").strip() or "untitled sheet"
            title = ("Added from the nominal roll" if ch.get("created")
                     else "Updated from the nominal roll")
            fields = ch.get("fields") or []
            detail = f"Sheet “{sheet}”, row {ch.get('row')}"
            if fields and not ch.get("created"):
                detail += " · " + ", ".join(f.replace("_", " ") for f in fields[:6])
            events.append(Event(_dt(e.timestamp), "record", "import", title, detail=detail,
                                by=_who(e.actor) if e.actor_id else ""))
        else:
            channel = ch.get("channel", "message")
            title = ("Message sent: " if channel == "message" else "Notification sent: ") + \
                (ch.get("subject") or "(no subject)")
            events.append(Event(_dt(e.timestamp), "contact", "message" if channel == "message" else "notify",
                                title, detail=ch.get("preview", ""), by=_who(e.actor) or e.actor_repr))

    events.sort(key=lambda ev: ev.when, reverse=True)
    return events[:limit]


def _asset_url(asset):
    try:
        return reverse("assets:detail", args=[asset.asset_tag])
    except NoReverseMatch:
        return ""


def items_held(staff):
    if not staff.user_id:
        return []
    try:
        from assets.models import AssetAssignment
    except Exception:
        return []
    out = []
    for a in (AssetAssignment.objects.filter(assigned_to_id=staff.user_id, returned_at__isnull=True)
              .select_related("asset", "asset__category")):
        a.detail_url = _asset_url(a.asset)
        out.append(a)
    return out


def counts(events):
    c = {k: 0 for k in KINDS}
    for e in events:
        c[e.kind] = c.get(e.kind, 0) + 1
    return c
