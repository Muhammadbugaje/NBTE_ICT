"""Add-to-calendar helper: RFC 5545 iCalendar (.ics) files and Google links.

One small, dependency-free builder used by every "Add to calendar" button
(inspection visits, task due dates, complaint SLA deadlines, QA queries) and
by the personal subscription feed (core/calendar_views.py).

    ev = CalEvent(uid="inspection-12@nbte", summary="Visit: Yaba College",
                  start=date(2026, 11, 9), end=date(2026, 11, 11))
    ics_response([ev], "visit.ics")          # a download with reminders
    google_calendar_url(ev)                  # "Add to Google Calendar" link

Reminders (VALARM) are added to every event so the person's phone reminds
them ahead of time:
  · timed events (a deadline at 17:00): 1 day before and 1 hour before;
  · all-day events (a visit, a due date): 09:00 the day before and 09:00 on
    the day (an all-day event starts at midnight, so "1 hour before" would
    fire at 23:00 the night before).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.http import HttpResponse
from django.utils import timezone

PRODID = "-//NBTE//NBTE ICT System//EN"
TIMED_ALARMS = (("-P1D", "Tomorrow"), ("-PT1H", "In 1 hour"))
ALL_DAY_ALARMS = (("-PT15H", "Tomorrow"), ("PT9H", "Today"))


@dataclass
class CalEvent:
    uid: str
    summary: str
    start: dt.date | dt.datetime
    end: dt.date | dt.datetime | None = None
    description: str = ""
    location: str = ""
    url: str = ""
    status: str = "CONFIRMED"          # CONFIRMED | TENTATIVE | CANCELLED
    alarms: bool = True
    updated: dt.datetime | None = None
    categories: tuple = field(default_factory=tuple)

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, dt.datetime)

    def resolved_end(self):
        """DTEND: exclusive for all-day events (the day after the last day);
        30 minutes after a timed start when no end is given."""
        if self.all_day:
            last = self.end or self.start
            if isinstance(last, dt.datetime):
                last = last.date()
            return max(last, self.start) + dt.timedelta(days=1)
        if self.end is None:
            return self.start + dt.timedelta(minutes=30)
        return self.end


# ---------------------------------------------------------------------------
# Formatting (RFC 5545 §3.3.5, §3.3.11, §3.1)
# ---------------------------------------------------------------------------
def _utc(value: dt.datetime) -> str:
    if timezone.is_naive(value):
        value = timezone.make_aware(value)
    return value.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _date(value: dt.date) -> str:
    return value.strftime("%Y%m%d")


def escape_text(value) -> str:
    """TEXT escaping: backslash, semicolon, comma and newlines."""
    s = str(value or "")
    s = s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return s.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")


def fold(line: str) -> str:
    """Fold a content line at 75 octets (continuation lines start with a
    space), never splitting a UTF-8 character."""
    out, current, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        limit = 75 if not out else 74
        if size + n > limit:
            out.append(current)
            current, size = "", 0
        current += ch
        size += n
    out.append(current)
    return "\r\n ".join(out)


def _event_lines(ev: CalEvent, stamp: str) -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{ev.uid}", f"DTSTAMP:{stamp}"]
    end = ev.resolved_end()
    if ev.all_day:
        lines += [f"DTSTART;VALUE=DATE:{_date(ev.start)}", f"DTEND;VALUE=DATE:{_date(end)}",
                  "TRANSP:TRANSPARENT"]
    else:
        lines += [f"DTSTART:{_utc(ev.start)}", f"DTEND:{_utc(end)}"]
    lines.append(f"SUMMARY:{escape_text(ev.summary)}")
    if ev.description:
        lines.append(f"DESCRIPTION:{escape_text(ev.description)}")
    if ev.location:
        lines.append(f"LOCATION:{escape_text(ev.location)}")
    if ev.url:
        lines.append(f"URL:{ev.url}")
    if ev.categories:
        lines.append("CATEGORIES:" + ",".join(escape_text(c) for c in ev.categories))
    if ev.updated:
        lines.append(f"LAST-MODIFIED:{_utc(ev.updated)}")
        # SEQUENCE lets a calendar replace an older copy of the same UID.
        lines.append(f"SEQUENCE:{int(ev.updated.timestamp()) // 60 % 2_000_000_000}")
    lines.append(f"STATUS:{ev.status}")
    if ev.alarms and ev.status != "CANCELLED":
        for trigger, when in (ALL_DAY_ALARMS if ev.all_day else TIMED_ALARMS):
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"TRIGGER:{trigger}",
                      f"DESCRIPTION:{escape_text(when + ': ' + ev.summary)}", "END:VALARM"]
    lines.append("END:VEVENT")
    return lines


def build_calendar(events, name: str = "NBTE", *, feed: bool = False) -> str:
    """The whole VCALENDAR text, CRLF line endings, folded lines."""
    stamp = _utc(timezone.now())
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{escape_text(name)}", "X-WR-TIMEZONE:Africa/Lagos"]
    if feed:
        lines += ["REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"]
    for ev in events:
        lines += _event_lines(ev, stamp)
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(ln) for ln in lines) + "\r\n"


def ics_response(events, filename: str, name: str = "NBTE", *, feed: bool = False) -> HttpResponse:
    """A text/calendar response. Single items download as an attachment (the
    phone offers "Add to calendar"); feeds are served inline for subscribing."""
    response = HttpResponse(build_calendar(events, name, feed=feed), content_type="text/calendar; charset=utf-8")
    safe = "".join(c for c in filename if c.isalnum() or c in "-_.") or "event.ics"
    response["Content-Disposition"] = f'{"inline" if feed else "attachment"}; filename="{safe}"'
    response["Cache-Control"] = "private, no-store" if not feed else "private, max-age=900"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def google_calendar_url(ev: CalEvent) -> str:
    """https://calendar.google.com/... "create event" link for the same event
    (Google cannot import our reminders; it applies the user's defaults)."""
    end = ev.resolved_end()
    if ev.all_day:
        dates = f"{_date(ev.start)}/{_date(end)}"
    else:
        dates = f"{_utc(ev.start)}/{_utc(end)}"
    details = ev.description
    if ev.url:
        details = (details + "\n\n" if details else "") + ev.url
    params = {"action": "TEMPLATE", "text": ev.summary, "dates": dates}
    if details:
        params["details"] = details[:1500]
    if ev.location:
        params["location"] = ev.location
    return "https://calendar.google.com/calendar/render?" + urlencode(params)
