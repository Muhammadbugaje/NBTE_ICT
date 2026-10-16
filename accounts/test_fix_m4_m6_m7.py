"""Regression tests for review findings M4 (memos), M6 (roles) and M7 (department scope)."""
import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from staff.models import Department, Staff

pytestmark = pytest.mark.django_db


@pytest.fixture
def ict():
    return Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]


@pytest.fixture
def roles():
    from django.core.management import call_command
    call_command("seed_roles", "--sync", verbosity=0)
    return {r.code: r for r in Role.objects.all()}


def mk(email, dept=None, role=None, **kw):
    u = User.objects.create_user(email=email, password="Xx-123456789", first_name="F",
                                 last_name="L", department=dept, role=role, **kw)
    u.must_change_password = False
    u.save()
    return u
from django.core.exceptions import ValidationError


# M4
def _memo(ict, roles):
    from messaging import memos as ms
    from messaging.models import Memo
    sender = mk("snd-m4@x.org", ict, roles["staff"])
    target = mk("tgt-m4@x.org", ict, roles["staff"])
    return ms.create_memo(sender=sender, subject="Original", body="Original body",
                          visibility=Memo.SPECIFIC_USER, target_user=target), sender


def test_memo_frozen_after_first_approval(ict, roles):
    from messaging import memos as ms
    from messaging.models import Memo, MemoApproval
    m, sender = _memo(ict, roles)
    m.status = Memo.AWAITING
    m.save()
    approver = mk("appr-m4@x.org", ict, roles["director"])
    MemoApproval.objects.create(memo=m, actor=approver, decision=MemoApproval.APPROVE)
    with pytest.raises(ValidationError):
        ms.update_memo(m, sender, subject="Changed", body="Changed")
    # unchanged values (e.g. the edit form re-posting everything) are fine
    ms.update_memo(m, sender, subject="Original", body="Original body", priority="high")


def test_memo_editable_before_any_approval_and_when_returned(ict, roles):
    from messaging import memos as ms
    from messaging.models import Memo
    m, sender = _memo(ict, roles)
    ms.update_memo(m, sender, body="Draft edit")
    m.status = Memo.RETURNED
    m.save()
    ms.update_memo(m, sender, body="Returned edit")
    m.refresh_from_db()
    assert m.body == "Returned edit"


# M6
def test_edit_roles_sets_role(client, ict, roles):
    su = mk("su-m6@x.org", is_superuser=True)
    target = mk("t-m6@x.org", ict, roles["staff"])
    client.force_login(su)
    client.post(reverse("accounts:user_edit_roles", args=[target.pk]), {"role": roles["officer"].pk})
    target.refresh_from_db()
    assert target.role.code == "officer"


def test_edit_roles_cannot_grant_higher_rank(client, ict, roles):
    from django.contrib.auth.models import Permission
    from accounts.models import UserPermissionOverride
    hr = mk("hr-m6@x.org", ict, roles["admin-staff"])
    for code in ("view_user", "change_user"):
        UserPermissionOverride.objects.create(
            user=hr, effect="grant",
            permission=Permission.objects.get(codename=code, content_type__app_label="accounts"))
    target = mk("t-m6b@x.org", ict, roles["staff"])
    client.force_login(hr)
    client.post(reverse("accounts:user_edit_roles", args=[target.pk]), {"role": roles["director"].pk})
    target.refresh_from_db()
    assert target.role.code == "staff"


def test_portal_role_scope_uses_role(ict, roles):
    from portal import services
    from portal.models import Announcement, AnnouncementCategory, Scope
    author = mk("auth-m6@x.org", ict, roles["director"])
    officer = mk("off-m6@x.org", ict, roles["officer"])
    other = mk("oth-m6@x.org", ict, roles["staff"])
    cat = AnnouncementCategory.objects.create(name="General m6")
    ann = Announcement.objects.create(title="For officers", body="x", author=author, category=cat,
                                      visibility=Scope.ROLE, publish_at=timezone.now())
    ann.roles.add(roles["officer"])
    assert services.live_announcements(user=officer).filter(pk=ann.pk).exists()
    assert not services.live_announcements(user=other).filter(pk=ann.pk).exists()
    assert services.is_announcement_visible_to(ann, officer)


def test_portal_forms_offer_role_scope():
    from portal.forms import _visibility_choices
    from portal.models import Scope
    assert Scope.ROLE in [c[0] for c in _visibility_choices()]


# M7
def test_view_staff_without_access_all_sees_own_department_only(ict, roles):
    from staff.permissions import staff_visible_to, view_level
    fin = Department.objects.create(name="Fin7", code="fin7")
    head = mk("divhead-m7@x.org", ict, roles["division-head"])  # view_staff, no access_all
    outsider = mk("out-m7@x.org", fin, roles["staff"])
    insider = mk("in-m7@x.org", ict, roles["staff"])
    head = User.objects.get(pk=head.pk)
    visible = staff_visible_to(head)
    assert visible.filter(pk=insider.staff_profile.pk).exists()
    assert not visible.filter(pk=outsider.staff_profile.pk).exists()
    assert view_level(head, outsider.staff_profile) == ""


def test_user_list_scoped(client, ict, roles):
    from django.contrib.auth.models import Permission
    from accounts.models import UserPermissionOverride
    fin = Department.objects.create(name="Fin7b", code="fin7b")
    hr = mk("hr-m7@x.org", ict, roles["staff"])
    UserPermissionOverride.objects.create(
        user=hr, effect="grant",
        permission=Permission.objects.get(codename="view_user", content_type__app_label="accounts"))
    outsider = mk("out-m7b@x.org", fin, roles["staff"])
    client.force_login(hr)
    assert b"out-m7b@x.org" not in client.get(reverse("accounts:user_list")).content
    assert client.get(reverse("accounts:user_detail", args=[outsider.pk])).status_code == 404


def test_incoming_only_for_department_leads(client, ict, roles):
    client.force_login(mk("plain-m7@x.org", ict, roles["staff"]))
    assert client.get(reverse("workflow:incoming")).status_code == 403
    client.force_login(mk("dir-m7@x.org", ict, roles["director"]))
    assert client.get(reverse("workflow:incoming")).status_code == 200
