"""Attendance. ``attendance.availability(user, date_from, date_to)`` is the
public availability query used by the Phase P rotation engine (G7)."""


def availability(user, date_from, date_to):
    from .availability_query import availability as _availability
    return _availability(user, date_from, date_to)


def availability_many(users, date_from, date_to):
    """``availability`` for many people with one window (one leave query)."""
    from .availability_query import availability_many as _many
    return _many(users, date_from, date_to)
