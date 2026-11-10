"""Load counting for the rotation (X27/X28).

``open_load(user, kind)`` counts open ``WorkAssignment`` rows of a kind. Until
the back-fill (assignments/0002 data step, Phase P.1) has created rows for a
kind, the legacy sources are counted instead so the rotation is right on
today's data:

* ``ssq_review``             open ``inspection.SSQReviewAssignment`` rows;
* ``visit_panel`` /
  ``physical_verification``  live ``inspection.QAMapping`` rows (visit duties);
* ``oversight_ict``          open ``QACase`` rows tracked by the person;
* ``document_analysis``      no legacy source (0).

Recent work and the last time someone was chosen read ``WorkAssignment``
plus decided ``RotationDecision`` rows not linked to a WorkAssignment, so a
rotation used before the work wiring exists is still fair.

v2.3 speed pass: a page that ranks several rotation suggestions asked these
per-person questions once per person per job — dozens of small queries. The
``batch()`` context (with the same WHERE clauses, grouped by person through
``annotate/Count``) now answers them for everyone at once; anyone the page
never grouped falls back to the per-person query above, so both paths give
the same numbers.
"""
# scoped-by-caller
from __future__ import annotations

import contextlib
import contextvars
import datetime

from django.db.models import Count, Max
from django.utils import timezone

from .models_rotation import VISIT_KINDS, ConflictDeclaration, RotationDecision


def _wa():
    from .models import WorkAssignment
    return WorkAssignment


def _has_work_rows(kind):
    return _wa().objects.filter(kind=kind).exists()


def _legacy_open(user, kind):
    if kind == "ssq_review":
        from inspection.models import SSQReviewAssignment
        return SSQReviewAssignment.unscoped.filter(reviewer=user, completed_at__isnull=True).count()
    if kind in VISIT_KINDS:
        from inspection.models import VISIT_DUTIES, QAMapping
        duties = ("physical_verification",) if kind == "physical_verification" else VISIT_DUTIES
        return (QAMapping.unscoped.filter(status__in=QAMapping.LIVE, duty__in=duties)
                .filter(qa_personnel__staff__user=user).count())
    if kind == "oversight_ict":
        from accreditation.models import QACase
        return QACase.unscoped.filter(tracking_ict_officers=user).exclude(status="closed").count()
    if kind == "complaint_task":
        from complaints.models import Complaint
        return Complaint.unscoped.filter(current_assignee=user, closed_at__isnull=True).count()
    return 0


def open_load(user, kind):
    """Open work of ``kind`` held by ``user`` now."""
    b = active()
    if b is not None and b.covers(user):
        return b.open_load(user, kind)
    WA = _wa()
    n = WA.objects.filter(assignee=user, kind=kind, status__in=WA.OPEN).count()
    if n or _has_work_rows(kind):
        return n
    return _legacy_open(user, kind)


def open_load_all(user):
    """Open work of every kind (for the board's 'n open')."""
    b = active()
    if b is not None and b.covers(user):
        return b.open_all(user)
    WA = _wa()
    n = WA.objects.filter(assignee=user, status__in=WA.OPEN).count()
    if n:
        return n
    return sum(_legacy_open(user, k) for k in ("ssq_review", "visit_panel", "oversight_ict"))


def recent_count(user, kind, days):
    """Assignments of ``kind`` given to ``user`` in the last ``days`` days."""
    b = active()
    if b is not None and b.covers(user):
        return b.recent_count(user, kind, days)
    since = timezone.now() - datetime.timedelta(days=days)
    WA = _wa()
    n = WA.objects.filter(assignee=user, kind=kind, assigned_at__gte=since).exclude(
        status__in=(WA.DECLINED,)).count()
    n += RotationDecision.objects.filter(chosen_user=user, kind=kind, decided_at__gte=since,
                                         work_assignment__isnull=True).count()
    return n


def last_assigned_at(user, kind, member=None):
    """When ``user`` was last given work of ``kind`` (None = never)."""
    b = active()
    if b is not None and b.covers(user):
        return b.last_assigned(user, kind, member)
    WA = _wa()
    stamps = [getattr(member, "last_assigned_at", None),
              WA.objects.filter(assignee=user, kind=kind).aggregate(m=Max("assigned_at"))["m"],
              RotationDecision.objects.filter(chosen_user=user, kind=kind).aggregate(m=Max("decided_at"))["m"]]
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else None


def last_chosen(pool):
    """The person chosen by the pool's latest decision (anti-streak)."""
    row = (RotationDecision.objects.filter(pool=pool, decided_at__isnull=False, chosen_user__isnull=False)
           .order_by("-decided_at", "-pk").values_list("chosen_user_id", flat=True).first())
    return row


def reviewed_ssq_of(user, institution=None, subject=None):
    """True when ``user`` reviewed an SSQ of the subject's case/request (R-02)."""
    b = active()
    if b is not None and b.covers(user):
        return user.pk in reviewer_set(b, subject, institution)
    from inspection.models import SSQReviewAssignment
    qs = SSQReviewAssignment.unscoped.filter(reviewer=user)
    qa_request_id = getattr(subject, "qa_request_id", None)
    if qa_request_id:
        return qs.filter(ssq__qa_request_programme__qa_request_id=qa_request_id).exists()
    if institution is not None:
        return qs.filter(ssq__qa_request_programme__qa_request__institution=institution).exists() or (
            _wa().objects.filter(assignee=user, kind="ssq_review", institution=institution).exists())
    return False


def on_visit_panel_of(user, institution=None, subject=None):
    """True when ``user`` is (or was) on the visit panel of the institution / case."""
    b = active()
    if b is not None and b.covers(user):
        return user.pk in panel_set(b, institution,
                                    getattr(subject, "pk", None)
                                    if subject.__class__.__name__ == "QACase" else None)
    from inspection.models import VISIT_DUTIES, QAMapping
    qs = QAMapping.unscoped.filter(duty__in=VISIT_DUTIES, qa_personnel__staff__user=user,
                                   status__in=("proposed", "active", "released"))
    case_id = getattr(subject, "pk", None) if subject.__class__.__name__ == "QACase" else None
    if case_id:
        qs = qs.filter(case_id=case_id)
    elif institution is not None:
        qs = qs.filter(case__institution=institution)
    else:
        return False
    if qs.exists():
        return True
    return bool(institution is not None and _wa().objects.filter(
        assignee=user, kind__in=VISIT_KINDS, institution=institution).exists())


def visits_in_window(user, start, end):
    """Live visit bookings of ``user`` overlapping [start, end]."""
    b = active()
    if b is not None and b.covers(user):
        return list(b.visits_map(start, end).get(user.pk) or [])
    from inspection.models import QAMapping
    WA = _wa()
    rows = list(QAMapping.unscoped.filter(status__in=QAMapping.LIVE, qa_personnel__staff__user=user,
                                          from_date__lte=end, to_date__gte=start)
                .values_list("from_date", "to_date"))
    for meta in WA.objects.filter(assignee=user, kind__in=VISIT_KINDS, status__in=WA.OPEN).values_list(
            "meta", flat=True):
        try:
            a = datetime.date.fromisoformat((meta or {}).get("from", ""))
            b = datetime.date.fromisoformat((meta or {}).get("to", "") or (meta or {}).get("from", ""))
        except (TypeError, ValueError):
            continue
        if a <= end and b >= start:
            rows.append((a, b))
    return rows


def served_institution_since(user, institution, since, kinds=None):
    """True when ``user`` was given work (of ``kinds``) at ``institution`` after ``since``."""
    if institution is None:
        return False
    b = active()
    if b is not None and b.covers(user):
        return user.pk in served_set(b, institution, since, kinds)
    wa = _wa().objects.filter(assignee=user, institution=institution, assigned_at__gte=since).exclude(
        status__in=("declined", "cancelled"))
    dec = RotationDecision.objects.filter(chosen_user=user, institution=institution, decided_at__gte=since,
                                          work_assignment__isnull=True)
    if kinds:
        wa, dec = wa.filter(kind__in=kinds), dec.filter(kind__in=kinds)
    return wa.exists() or dec.exists()


# ===========================================================================
# Page batching (v2.3 speed pass).
#
# The batcher answers the same questions for every person on the page with
# one grouped query per question (annotate/Count, identical WHERE clauses).
# ``batch()`` must only wrap the building of a page's data; each render
# follows the block, so numbers never leak between requests. Asking for a
# person the batch does not "cover" (never registered) falls back to the
# per-person query above — the numbers are always the same either way.
# ===========================================================================
_ACTIVE = contextvars.ContextVar("rotation-load-batch", default=None)


@contextlib.contextmanager
def batch(users=(), kinds=()):
    """Pre-read per-person numbers in bulk for this page's people and kinds."""
    b = Batcher()
    b.preload(list(users), list(kinds))
    token = _ACTIVE.set(b)
    try:
        yield b
    finally:
        _ACTIVE.reset(token)


def active():
    """The page's batcher (or None)."""
    return _ACTIVE.get()


def department_staff_cached(dept, builder):
    """batcher-aware department staff (used by the board and candidates)."""
    b = active()
    if b is not None:
        return b.department_staff(dept, builder)
    return builder(dept)


class Batcher:
    """One request's numbers, grouped. Lives for one request only."""

    def __init__(self):
        self.users = {}          # uid -> user
        self.kinds = set()
        self._has_rows = {}      # kind -> bool
        self._open = {}          # kind -> {uid: n}
        self._open_all = {}      # uid -> n
        self._recent = {}        # (kind, days) -> {uid: n}
        self._last = {}          # kind -> {uid: dt}
        self._availability = {}  # (start, end) -> {uid: availability dict}
        self._visits = {}        # (start, end) -> {uid: [(from, to)]}
        self._sets = {}          # shared: user sets / conflict rows / lookup maps
        self._covered = set()    # the people preload grouped (uids)
        self._kinds_seen = None  # {kind with WorkAssignment rows}, one distinct query

    def register(self, users):
        """Keep users; their individual asks then hit the batch."""
        for u in users:
            uid = getattr(u, "pk", None)
            if u is not None and uid is not None:
                self.users.setdefault(uid, u)

    def covers(self, user):
        """True when this batch grouped ``user`` (safe to answer from maps)."""
        return user is not None and getattr(user, "pk", None) in self._covered

    def preload(self, users, kinds):
        """One grouped query covers the open load of everyone (all kinds, so
        ``open_load_all`` also sees work outside the page's pool kinds)."""
        self.register(users)
        ids = list(self.users)
        self.kinds |= {k for k in kinds if k}
        self._covered = {uid for uid in ids}
        if not ids or not self.kinds:
            return
        WA = _wa()
        rows = (WA.objects.filter(assignee_id__in=ids, status__in=WA.OPEN)
                .values("assignee_id", "kind").annotate(n=Count("pk")))
        by_kind = {}
        for row in rows:
            by_kind.setdefault(row["kind"], {})[row["assignee_id"]] = row["n"]
        for kind in self.kinds:
            per = dict(by_kind.get(kind, {}))
            if not self._exists_rows(kind):
                per = _legacy_open_map(ids, kind)  # the kind still rides on legacy sources
            self._open[kind] = per
        combined = {uid: sum(per.get(uid, 0) for per in by_kind.values()) for uid in ids}
        # People with no open work row still carry legacy duties (the board's
        # "n open" must count them, exactly as open_load_all does).
        zero = [uid for uid in ids if not combined.get(uid)]
        if zero:
            for legacy_kind in ("ssq_review", "visit_panel", "oversight_ict"):
                for uid, n in _legacy_open_map(zero, legacy_kind).items():
                    combined[uid] = combined.get(uid, 0) + n
        self._open_all = combined

    def _exists_rows(self, kind):
        """Has this kind any WorkAssignment row — answered for ALL kinds by
        one ``kind``-distinct query instead of an exists() per kind."""
        if self._kinds_seen is None:
            self._kinds_seen = {k for k, in _wa().objects.values_list("kind").distinct()}
        return kind in self._kinds_seen

    # -- the per-person numbers, grouped ----------------------------------
    def open_load(self, user, kind):
        per = self._open.get(kind)
        if per is None:
            ids = sorted(self.users)
            if self._exists_rows(kind):
                rows = (_wa().objects.filter(assignee_id__in=ids, kind=kind, status__in=_wa().OPEN)
                        .values("assignee_id").annotate(n=Count("pk")))
                per = {row["assignee_id"]: row["n"] for row in rows}
            else:
                per = _legacy_open_map(ids, kind)
            self._open[kind] = per
        return per.get(user.pk, 0)

    def open_all(self, user):
        # Covered people were grouped across every kind at preload time.
        return self._open_all.get(user.pk, 0)

    def recent_count(self, user, kind, days):
        key = (kind, int(days))
        per = self._recent.get(key)
        if per is None:
            WA = _wa()
            ids = sorted(self.users)
            since = timezone.now() - datetime.timedelta(days=days)
            rows = (WA.objects.filter(kind=kind, assignee_id__in=ids, assigned_at__gte=since).exclude(
                status__in=(WA.DECLINED,)).values("assignee_id").annotate(n=Count("pk")))
            per = {row["assignee_id"]: row["n"] for row in rows}
            dec = (RotationDecision.objects.filter(kind=kind, chosen_user_id__in=ids, decided_at__gte=since,
                                                   work_assignment__isnull=True)
                   .values("chosen_user_id").annotate(n=Count("pk")))
            for row in dec:
                per[row["chosen_user_id"]] = per.get(row["chosen_user_id"], 0) + row["n"]
            self._recent[key] = per
        return per.get(user.pk, 0)

    def last_assigned(self, user, kind, member):
        per = self._last.get(kind)
        if per is None:
            WA = _wa()
            ids = sorted(self.users)
            rows = (WA.objects.filter(kind=kind, assignee_id__in=ids)
                    .values("assignee_id").annotate(m=Max("assigned_at")))
            per = {row["assignee_id"]: row["m"] for row in rows}
            dec = (RotationDecision.objects.filter(kind=kind, chosen_user_id__in=ids)
                   .values("chosen_user_id").annotate(m=Max("decided_at")))
            for row in dec:
                have = per.get(row["chosen_user_id"])
                if row["m"] and (have is None or have < row["m"]):
                    per[row["chosen_user_id"]] = row["m"]
            self._last[kind] = per
        stamps = [getattr(member, "last_assigned_at", None), per.get(user.pk)]
        stamps = [s for s in stamps if s]
        return max(stamps) if stamps else None

    # -- windows (availability and visit bookings) -------------------------
    def availability_map(self, start, end):
        """{uid: attendance.availability dict} for one window."""
        from attendance import availability_many

        key = (start, end)
        m = self._availability.get(key)
        if m is None:
            users = list(self.users.values())
            m = availability_many(users, start, end) if users else {}
            self._availability[key] = m
        return m

    def visits_map(self, start, end):
        """{uid: [(from, to)]} live visit bookings for one window."""
        key = (start, end)
        rows = self._visits.get(key)
        if rows is None:
            from inspection.models import QAMapping

            ids = sorted(self.users)
            out = {}
            if ids:
                qrows = (QAMapping.unscoped.filter(status__in=QAMapping.LIVE,
                                                   qa_personnel__staff__user_id__in=ids,
                                                   from_date__lte=end, to_date__gte=start)
                         .values_list("qa_personnel__staff__user_id", "from_date", "to_date"))
                for uid, a, b_ in qrows:
                    if uid:
                        out.setdefault(uid, []).append((a, b_))
                WA = _wa()
                mrows = (WA.objects.filter(kind__in=VISIT_KINDS, status__in=WA.OPEN, assignee_id__in=ids)
                         .values_list("meta", "assignee_id"))
                for meta, uid in mrows:
                    try:
                        a = datetime.date.fromisoformat((meta or {}).get("from", ""))
                        b_ = datetime.date.fromisoformat((meta or {}).get("to", "")
                                                         or (meta or {}).get("from", ""))
                    except (TypeError, ValueError):
                        continue
                    if a <= end and b_ >= start:
                        out.setdefault(uid, []).append((a, b_))
            self._visits[key] = out
        return self._visits[key]

    # -- constraint lookups shared by the page's suggestions ---------------
    def conflict_rows(self, institution):
        """Live conflict-of-interest rows for one institution (all people)."""
        key = ("coi", institution.pk)
        rows = self._sets.get(key)
        if rows is None:
            rows = list(ConflictDeclaration.objects.live().filter(institution_id=institution.pk))
            self._sets[key] = rows
        return rows

    def department_staff(self, dept, builder):
        """The (active, eligible) staff of one department, asked once per page."""
        key = ("dept-staff", dept.pk)
        staff = self._sets.get(key)
        if staff is None:
            staff = list(builder(dept))
            self._sets[key] = staff
        return staff

    def staff_profile_of(self, user):
        """The user's ``staff`` row (for the state-of-origin conflict rule) —
        one grouped query for everyone on the page, per-user fallback below."""
        key = "staff-profiles"
        m = self._sets.get(key)
        if m is None:
            from staff.models import Staff
            ids = sorted(self.users)
            m = {s.user_id: s for s in Staff.objects.filter(user_id__in=ids)} if ids else {}
            self._sets[key] = m
        if user.pk in self._covered:
            return m.get(user.pk)
        if user.pk not in m:  # never grouped: one query now, remembered
            from staff.models import Staff
            m.setdefault(user.pk, Staff.objects.filter(user_id=user.pk).first())
        return m.get(user.pk)

    def cached_pool(self, code, department, builder):
        """A pool looked up from code + department, once per page. A lookup
        that raised DoesNotExist is remembered and raised again."""
        key = ("pool", code, getattr(department, "pk", None))
        if key not in self._sets:
            try:
                self._sets[key] = builder()
            except Exception as e:
                self._sets[key] = e
                raise
        pool = self._sets[key]
        if isinstance(pool, Exception):
            raise pool
        return pool

    def memo(self, key, builder):
        """Cheap catch-all: build once per page, keep the answer."""
        if key not in self._sets:
            self._sets[key] = builder()
        return self._sets[key]

    def member_disciplines(self, member, member_pks=()):
        """{member pk: {discipline ids}} across the pool (one M2M query)."""
        key = "member-disciplines"
        m = self._sets.get(key)
        if m is None:
            through = member.disciplines.through
            m = {}
            for mid, did in through.objects.filter(rotationmember_id__in=list(member_pks)).values_list(
                    "rotationmember_id", "discipline_id"):
                m.setdefault(mid, set()).add(did)
            self._sets[key] = m
        return m.get(member.pk, set())

    def qa_personnel_of(self, user, member_user_ids=()):
        """The user's QAPersonnel row (one query for the pool; the first row
        per person, as ``QAPersonnel…filter(staff__user=user).first()``)."""
        key = "qa-personnel"
        m = self._sets.get(key)
        if m is None:
            from inspection.models import QAPersonnel
            ids = sorted(self.users) or list(member_user_ids)
            m = {}
            for p in (QAPersonnel.objects.filter(staff__user_id__in=ids)
                      .select_related("staff__user__role", "staff__designation")):
                m.setdefault(p.staff.user_id, p)
            self._sets[key] = m
        return m.get(user.pk)


# ---------- shared bulk helpers (every user set is cached per request) -----
def _legacy_open_map(ids, kind):
    """Legacy source counts for many people at once (same WHERE as
    ``_legacy_open``)."""
    if not ids:
        return {}
    if kind == "ssq_review":
        from inspection.models import SSQReviewAssignment
        rows = (SSQReviewAssignment.unscoped.filter(completed_at__isnull=True, reviewer_id__in=ids)
                .values("reviewer_id").annotate(n=Count("pk")))
        return {row["reviewer_id"]: row["n"] for row in rows}
    if kind in VISIT_KINDS:
        from inspection.models import VISIT_DUTIES, QAMapping
        duties = ("physical_verification",) if kind == "physical_verification" else VISIT_DUTIES
        rows = (QAMapping.unscoped.filter(status__in=QAMapping.LIVE, duty__in=duties,
                                          qa_personnel__staff__user_id__in=ids)
                .values("qa_personnel__staff__user_id").annotate(n=Count("pk")))
        return {row["qa_personnel__staff__user_id"]: row["n"] for row in rows
                if row["qa_personnel__staff__user_id"]}
    if kind == "oversight_ict":
        from accreditation.models import QACase
        rows = (QACase.unscoped.filter(tracking_ict_officers__id__in=ids).exclude(status="closed")
                .values("tracking_ict_officers__id").annotate(n=Count("pk")))
        return {row["tracking_ict_officers__id"]: row["n"] for row in rows
                if row["tracking_ict_officers__id"]}
    if kind == "complaint_task":
        from complaints.models import Complaint
        rows = (Complaint.unscoped.filter(current_assignee_id__in=ids, closed_at__isnull=True)
                .values("current_assignee_id").annotate(n=Count("pk")))
        return {row["current_assignee_id"]: row["n"] for row in rows
                if row["current_assignee_id"]}
    return {}


def served_set(b, institution, since, kinds):
    """{uid} who got work (of ``kinds``) at ``institution`` after ``since``."""
    key = ("served", institution.pk, tuple(kinds or ()), since.strftime("%Y%m%d%H%M%S"))
    out = b._sets.get(key)
    if out is None:
        WA = _wa()
        wa = (WA.objects.filter(institution_id=institution.pk, assigned_at__gte=since)
              .exclude(status__in=("declined", "cancelled")))
        dec = (RotationDecision.objects.filter(institution_id=institution.pk, decided_at__gte=since,
                                               work_assignment__isnull=True))
        if kinds:
            wa, dec = wa.filter(kind__in=kinds), dec.filter(kind__in=kinds)
        out = set(wa.values_list("assignee_id", flat=True)) | set(dec.values_list("chosen_user_id", flat=True))
        b._sets[key] = out
    return out


def panel_set(b, institution=None, case_id=None):
    """{uid} who are (or were) on the visit panel of the institution / case."""
    from inspection.models import VISIT_DUTIES, QAMapping

    key = ("panel", case_id, getattr(institution, "pk", None))
    out = b._sets.get(key)
    if out is None:
        qs = QAMapping.unscoped.filter(duty__in=VISIT_DUTIES, status__in=("proposed", "active", "released"))
        if case_id:
            qs = qs.filter(case_id=case_id)
        elif institution is not None:
            qs = qs.filter(case__institution_id=institution.pk)
        out = set(qs.values_list("qa_personnel__staff__user_id", flat=True)) - {None}
        if case_id is None and institution is not None:
            out |= set(_wa().objects.filter(kind__in=VISIT_KINDS, institution_id=institution.pk)
                       .values_list("assignee_id", flat=True))
        b._sets[key] = out
    return out


def reviewer_set(b, subject=None, institution=None):
    """{uid} who reviewed an SSQ of the subject's request / institution."""
    from inspection.models import SSQReviewAssignment
    from .models import WorkAssignment

    key = ("reviewer", getattr(subject, "qa_request_id", None), getattr(institution, "pk", None))
    out = b._sets.get(key)
    if out is None:
        out = set()
        if getattr(subject, "qa_request_id", None):
            out = set(SSQReviewAssignment.unscoped.filter(
                ssq__qa_request_programme__qa_request_id=subject.qa_request_id)
                .values_list("reviewer_id", flat=True))
        elif institution is not None:
            out = set(SSQReviewAssignment.unscoped.filter(
                          ssq__qa_request_programme__qa_request__institution_id=institution.pk)
                      .values_list("reviewer_id", flat=True))
            out |= set(WorkAssignment.objects.filter(kind="ssq_review", institution_id=institution.pk)
                       .values_list("assignee_id", flat=True))
        b._sets[key] = out
    return out
