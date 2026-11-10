"""core/ics.py: RFC 5545 output (no database)."""
import datetime as dt

from django.utils import timezone

from core.ics import CalEvent, build_calendar, escape_text, fold, google_calendar_url, ics_response


def _timed():
    start = timezone.make_aware(dt.datetime(2026, 11, 20, 17, 0))  # Africa/Lagos (UTC+1)
    return CalEvent(uid="query-1@test", summary="Answer query; urgent, please", start=start,
                    end=start + dt.timedelta(minutes=30), description="Line one\nLine two",
                    url="https://qa.example/queries/1/")


def test_calendar_structure_crlf_and_reminders():
    text = build_calendar([_timed()], "NBTE")
    assert text.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n")
    assert text.endswith("END:VCALENDAR\r\n")
    assert "\n" not in text.replace("\r\n", "")
    assert "BEGIN:VEVENT" in text and "UID:query-1@test" in text and "DTSTAMP:" in text
    assert "DTSTART:20261120T160000Z" in text and "DTEND:20261120T163000Z" in text
    assert text.count("BEGIN:VALARM") == 2
    assert "TRIGGER:-P1D" in text and "TRIGGER:-PT1H" in text and "ACTION:DISPLAY" in text
    assert "SUMMARY:Answer query\\; urgent\\, please" in text
    assert "DESCRIPTION:Line one\\nLine two" in text


def test_all_day_event_has_exclusive_end_and_morning_reminders():
    ev = CalEvent(uid="inspection-1@test", summary="Visit", start=dt.date(2026, 11, 9), end=dt.date(2026, 11, 11))
    text = build_calendar([ev])
    assert "DTSTART;VALUE=DATE:20261109" in text and "DTEND;VALUE=DATE:20261112" in text
    assert "TRIGGER:-PT15H" in text and "TRIGGER:PT9H" in text


def test_cancelled_events_have_no_alarms():
    ev = CalEvent(uid="x@test", summary="Gone", start=dt.date(2026, 1, 2), status="CANCELLED")
    text = build_calendar([ev])
    assert "STATUS:CANCELLED" in text and "VALARM" not in text


def test_folding_and_escaping():
    line = "DESCRIPTION:" + "é" * 80
    folded = fold(line)
    assert all(len(part.encode("utf-8")) <= 75 for part in folded.split("\r\n"))
    assert folded.replace("\r\n ", "") == line
    assert escape_text("a\\b;c,d\r\ne") == "a\\\\b\\;c\\,d\\ne"


def test_response_and_google_link():
    r = ics_response([_timed()], "nbte query/1.ics")
    assert r["Content-Type"].startswith("text/calendar")
    assert r["Content-Disposition"] == 'attachment; filename="nbtequery1.ics"'
    url = google_calendar_url(_timed())
    assert url.startswith("https://calendar.google.com/calendar/render?action=TEMPLATE")
    assert "dates=20261120T160000Z%2F20261120T163000Z" in url
