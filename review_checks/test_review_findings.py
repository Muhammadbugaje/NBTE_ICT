"""REVIEW REPRODUCTION TESTS - READ BEFORE EDITING.

CONTRACT: every test in this file asserts the ORIGINAL, FAULTY behaviour found
in the 15 Oct 2026 code review. While a bug exists, its test PASSES. Once the
bug is fixed, its test is EXPECTED TO FAIL - that failure is the signal that the
fix worked. Do NOT "fix" these tests to make them pass again; the real
regression tests for each fix live in the */test_fix_*.py files.

After the remediation, 15 of these 17 tests fail as intended. Two still pass
by design: test_memo_editable_while_awaiting (M4 blocks edits only once an
approver has signed; this test edits before anyone signs) and
test_edit_roles_does_not_change_role (it posts the old `groups` field, which is
now rejected, so the role correctly stays unchanged).

Map: H1 html_attachment | H3 change_user_holder | H4 own_absence | H5
staff_create_with_account | H6 import_* | H7 departmentless_user | H8
general_staff_asset | M1 view_all_requests | M2 internal_workflow_comment |
M3 ticket_internal_note | M4 memo_editable | M6 edit_roles | M9
add_dependency | M10 tasks_views_has_no_logger | M12 weekly_summary |
M13 profile_duplicate_email
"""
import io
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from staff.models import Department, Staff

pytestmark = pytest.mark.django_db

# Phase 0 (T-TEST-1): these 15 reproduce bugs that are FIXED (see the contract
# above), so they fail by design. strict=True: if one starts passing again the
# bug is back and the suite goes red.
OBSOLETE = pytest.mark.xfail(
    strict=True,
    reason="T-TEST-1: reproduces a 15 Oct 2026 review bug that has been fixed; "
           "regression tests live in */test_fix_*.py",
)


@pytest.fixture
def ict():
    return Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]


@pytest.fixture
def roles(db):
    from django.core.management import call_command
    call_command("seed_roles", "--sync", verbosity=0)
    return {r.code: r for r in Role.objects.all()}


def mk(email, dept=None, role=None, **kw):
    u = User.objects.create_user(email=email, password="Xx-123456789", first_name="F",
                                 last_name="L", department=dept, role=role, **kw)
    u.must_change_password = False
    u.save()
    return u


def login(client, u):
    client.force_login(u)
    return client


# 1. Staff form "create account" crashes (auto Staff + OneToOne) -------------
@OBSOLETE
def test_staff_create_with_account_crashes(client, ict, roles):
    admin = mk("su@x.org", is_superuser=True, is_staff=True)
    login(client, admin)
    data = {"employee_no": "E1", "first_name": "A", "last_name": "B",
            "email": "newstaff@x.org", "department": ict.pk,
            "contact_preference": "email", "create_account": "on"}
    client.raise_request_exception = True
    with pytest.raises(Exception) as exc:
        client.post(reverse("staff:create"), data)
    assert "UNIQUE" in str(exc.value) or "unique" in str(exc.value).lower()


# 2. Staff import: blank optional cell rejects the row; department ignored ----
@OBSOLETE
def test_staff_import_blank_phone_rejects_row(ict):
    from staff.imports import StaffImport
    res = StaffImport().run([{"employee_no": "E9", "first_name": "A", "last_name": "B",
                             "email": "e9@x.org", "phone": "", "department": "ICT"}])
    assert res.created == 0 and len(res.rejected) == 1, res.rejected


@OBSOLETE
def test_staff_import_department_is_dropped(ict):
    from staff.imports import StaffImport
    cols = {c.field: None for c in StaffImport.columns}
    # fill every text column so blank-None does not bite
    row = {"employee_no": "E10", "first_name": "A", "last_name": "B", "email": "e10@x.org",
           "phone": "1", "department": "ICT", "subsection": "s", "designation": "d",
           "grade_level": "g", "office_location": "o", "duties": "x",
           "contact_preference": "email", "emergency_contact_name": "n",
           "emergency_contact_phone": "p"}
    res = StaffImport().run([row])
    assert res.created == 1, res.rejected
    assert Staff.objects.get(employee_no="E10").department_id is None


# 3. Staff can edit their own attendance record ------------------------------
@OBSOLETE
def test_staff_can_mark_own_absence_present(client, ict, roles):
    from attendance.models import AttendanceRecord
    u = mk("staff@x.org", ict, roles["staff"])
    rec = AttendanceRecord.objects.create(user=u, date=timezone.localdate() - timedelta(days=3),
                                          status=AttendanceRecord.ABSENT)
    login(client, u)
    client.post(reverse("attendance:manual_edit", args=[rec.pk]), {"status": "present"})
    rec.refresh_from_db()
    assert rec.status == "present"


# 4. Any role-holder in the owning department can edit/reassign assets --------
@OBSOLETE
def test_general_staff_can_change_department_asset_state(client, ict, roles):
    from assets.models import Asset, AssetCategory
    cat = AssetCategory.objects.create(name="Laptop", code="laptop")
    a = Asset.objects.create(asset_tag="ICT-LAP-00001", category=cat, owner_department=ict,
                             state="in_service")
    u = mk("officer@x.org", ict, roles["staff"])
    login(client, u)
    client.post(reverse("assets:set_state", args=[a.asset_tag]), {"state": "retired", "reason": "x"})
    a.refresh_from_db()
    assert a.state == "retired"


# 5. Adding a task dependency crashes (sliced queryset in ModelChoiceField) ---
@OBSOLETE
def test_add_dependency_crashes(client, ict, roles):
    from tasks.models import Task
    su = mk("su2@x.org", is_superuser=True)
    t1 = Task.objects.create(reference="TSK-1", title="a", created_by=su)
    t2 = Task.objects.create(reference="TSK-2", title="b", created_by=su)
    login(client, su)
    client.post(reverse("tasks:add_dependency", args=[t1.pk]), {"depends_on": t2.pk})
    assert not t1.dependency_links.exists()


# 6. Workflow "internal" comments are shown to the requester ----------------
@OBSOLETE
def test_internal_workflow_comment_visible_to_requester(client, ict, roles):
    from workflow.models import ApprovalChain, Request, RequestCategory, RequestComment, RequestType
    req_user = mk("req@x.org", ict, roles["staff"])
    other = mk("dir@x.org", ict, roles["director"])
    cat = RequestCategory.objects.create(name="C", code="c")
    chain = ApprovalChain.objects.create(name="ch", code="ch")
    rt = RequestType.objects.create(name="T", code="t", category=cat, handling_department=ict,
                                    approval_chain=chain)
    r = Request.objects.create(reference="REQ-1", request_type=rt, requester=req_user,
                               subject="s", body="b", receiving_department=ict)
    RequestComment.objects.create(request=r, author=other, body="SECRET-INTERNAL", is_internal=True)
    login(client, req_user)
    resp = client.get(reverse("workflow:detail", args=[r.pk]))
    assert b"SECRET-INTERNAL" in resp.content


# 7. Ticket internal note text leaks to the raiser via the timeline ----------
@OBSOLETE
def test_ticket_internal_note_in_raiser_timeline(client, ict, roles):
    from tickets.models import TicketCategory
    from tickets import services as ts
    raiser = mk("raiser@x.org", ict, roles["staff"])
    head = mk("head@x.org", ict, roles["director"])
    cat = TicketCategory.objects.create(code="hw", name="HW", handling_department=ict)
    t = ts.create_ticket(raised_by=raiser, category=cat, subject="printer", body="broken")
    ts.add_internal_comment(t, head, "STAFF-ONLY-NOTE")
    login(client, raiser)
    resp = client.get(reverse("tickets:detail", args=[t.pk]))
    assert b"STAFF-ONLY-NOTE" in resp.content


# 8. "Edit roles" page uses Django groups -> role never changes --------------
def test_edit_roles_does_not_change_role(client, roles):
    from django.contrib.auth.models import Group
    su = mk("su3@x.org", is_superuser=True)
    target = mk("t@x.org", role=roles["staff"])
    g = Group.objects.create(name="Director")
    login(client, su)
    resp = client.post(reverse("accounts:user_edit_roles", args=[target.pk]), {"groups": [g.pk]})
    target.refresh_from_db()
    assert target.role.code == "staff"


# 9. Profile: changing email to an existing one -> server error -------------
@OBSOLETE
def test_profile_duplicate_email_500(client):
    mk("taken@x.org")
    u = mk("me@x.org")
    login(client, u)
    client.raise_request_exception = True
    with pytest.raises(Exception):
        client.post(reverse("accounts:profile"), {"first_name": "A", "last_name": "B",
                                                  "email": "taken@x.org"})


# 10. view_all_requests holder sees list but gets 403 on detail -------------
@OBSOLETE
def test_view_all_requests_holder_cannot_open_detail(client, ict, roles):
    from workflow.models import ApprovalChain, Request, RequestCategory, RequestType
    fin = Department.objects.create(name="Finance", code="finance")
    es = mk("es@x.org", fin, roles["executive-secretary"])
    req_user = mk("req2@x.org", ict, roles["staff"])
    cat = RequestCategory.objects.create(name="C2", code="c2")
    chain = ApprovalChain.objects.create(name="ch2", code="ch2")
    rt = RequestType.objects.create(name="T2", code="t2", category=cat, handling_department=ict,
                                    approval_chain=chain)
    r = Request.objects.create(reference="REQ-2", request_type=rt, requester=req_user,
                               subject="s", body="b", receiving_department=ict)
    login(client, es)
    assert client.get(reverse("workflow:all_requests")).status_code == 200
    assert client.get(reverse("workflow:detail", args=[r.pk])).status_code == 403


# 11. A user with no department can assign themselves one ------------------
@OBSOLETE
def test_departmentless_user_can_pick_any_department(client, ict, roles):
    u = mk("nodept@x.org", None, roles["staff"])
    login(client, u)
    staff = u.staff_profile
    data = {"employee_no": staff.employee_no, "first_name": "A", "last_name": "B",
            "email": "nodept@x.org", "department": ict.pk, "contact_preference": "email"}
    client.post(reverse("staff:update", args=[staff.pk]), data)
    u.refresh_from_db()
    assert u.department_id == ict.pk


# 13. A user with change_user can reset a superuser's password -------------
@OBSOLETE
def test_change_user_holder_can_reset_superuser(client, ict, roles):
    from django.contrib.auth.models import Permission
    from accounts.models import UserPermissionOverride
    boss = mk("boss@x.org", is_superuser=True)
    hr = mk("hr@x.org", ict, roles["staff"])
    for code in ("view_user", "change_user"):
        UserPermissionOverride.objects.create(
            user=hr, permission=Permission.objects.get(codename=code, content_type__app_label="accounts"),
            effect="grant")
    login(client, hr)
    client.post(reverse("accounts:user_reset_password", args=[boss.pk]),
                {"action": "set", "password": "Taken0ver!!", "send_email": ""})
    boss.refresh_from_db()
    assert boss.check_password("Taken0ver!!")


# 14. Weekly attendance summary can never send (Department has no head) ----
@OBSOLETE
def test_weekly_summary_never_sends(ict, roles):
    from attendance.models import AttendanceRecord
    from attendance.services import send_weekly_summary
    mk("dir2@x.org", ict, roles["director"])
    u = mk("s2@x.org", ict, roles["staff"])
    today = timezone.localdate()
    last_week = today - timedelta(days=today.weekday() + 5)
    AttendanceRecord.objects.create(user=u, date=last_week, status="present")
    assert send_weekly_summary(for_date=today) == 0


# 15. Kanban unexpected-error path references an undefined logger ----------
@OBSOLETE
def test_tasks_views_has_no_logger():
    import tasks.views as tv
    assert not hasattr(tv, "logger")


# 16. Memo text can be changed while it is mid-approval ---------------------
def test_memo_editable_while_awaiting(ict, roles):
    from messaging import memos as ms
    from messaging.models import Memo
    sender = mk("snd@x.org", ict, roles["staff"])
    target = mk("tgt@x.org", ict, roles["staff"])
    m = ms.create_memo(sender=sender, subject="Original", body="Original body",
                       visibility=Memo.SPECIFIC_USER, target_user=target)
    m.status = Memo.AWAITING
    m.save()
    ms.update_memo(m, sender, subject="Changed", body="Changed after approval")
    m.refresh_from_db()
    assert m.body == "Changed after approval"


# 17. Uploaded .html attachment is served as text/html from our own origin --
@OBSOLETE
def test_html_attachment_is_public_text_html(client, ict, roles, settings):
    import mimetypes
    from workflow.models import ApprovalChain, Request, RequestCategory, RequestType
    from workflow import services as ws
    u = mk("att@x.org", ict, roles["staff"])
    cat = RequestCategory.objects.create(name="C4", code="c4")
    chain = ApprovalChain.objects.create(name="ch4", code="ch4")
    rt = RequestType.objects.create(name="T4", code="t4", category=cat, handling_department=ict,
                                    approval_chain=chain)
    r = Request.objects.create(reference="REQ-4", request_type=rt, requester=u,
                               subject="s", body="b", receiving_department=ict)
    att = ws.add_attachment(r, u, SimpleUploadedFile("x.html", b"<script>alert(1)</script>",
                                                     content_type="text/html"))
    assert att.file.url.startswith("/media/") and att.file.name.endswith(".html")
    assert mimetypes.guess_type(att.file.name)[0] == "text/html"

