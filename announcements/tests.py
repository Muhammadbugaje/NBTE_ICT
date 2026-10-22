"""Phase 9: announcements — models, services, views, sidebar badge."""
import datetime as dt

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from notifications.models import Notification
from staff.models import Department

from . import services
from .models import (
    Announcement,
    AnnouncementAck,
    AnnouncementAttachment,
    AnnouncementPoll,
    AnnouncementRead,
    PollOption,
    PollVote,
    audience_users,
)

pytestmark = pytest.mark.django_db
PW = "Xx-123456789"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def mk(email, dept=None, role=None, perms=(), **kw):
    u = User.objects.create_user(email=email, password=PW, first_name=email.split("@")[0].title(),
                                 last_name="Test", department=dept, role=role, **kw)
    u.must_change_password = False
    u.save()
    for label in perms:
        app, code = label.split(".")
        u.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=code))
    return User.objects.get(pk=u.pk)  # fresh permission cache


@pytest.fixture
def ict():
    return Department.objects.get_or_create(code="ict-ann", defaults={"name": "ICT Ann"})[0]


@pytest.fixture
def hr():
    return Department.objects.get_or_create(code="hr-ann", defaults={"name": "HR Ann"})[0]


@pytest.fixture
def officer_role():
    return Role.objects.get_or_create(code="ann-officer", defaults={"name": "Ann Officer", "rank": 20})[0]


@pytest.fixture
def director_role():
    return Role.objects.get_or_create(code="ann-director", defaults={"name": "Ann Director", "rank": 100})[0]


@pytest.fixture
def poster(ict):
    return mk("poster@x.org", ict, perms=["announcements.post_announcement"])


@pytest.fixture
def reporter(hr):
    return mk("reporter@x.org", hr, perms=["announcements.view_announcement_report"])


@pytest.fixture
def alice(ict, officer_role):
    return mk("alice@x.org", ict, officer_role)


@pytest.fixture
def bob(hr, director_role):
    return mk("bob@x.org", hr, director_role)


@pytest.fixture
def institution_user(hr):
    from institutions.models import Institution

    inst = Institution.unscoped.create(code="ANN", name="Ann Polytechnic", status="approved",
                                       handling_department=hr)
    return User.objects.create_user(email="inst@x.org", password=PW, user_type="institution", institution=inst)


@pytest.fixture
def resource_person():
    return User.objects.create_user(email="rp@x.org", password=PW, user_type="resource_person")


def ann(creator, publish=True, **kw):
    kw.setdefault("title", "Office closure")
    kw.setdefault("body", "Line one\nLine two")
    roles = kw.pop("roles", None)
    a = Announcement.objects.create(created_by=creator, **kw)
    if roles:
        a.roles.set(roles)
    if publish:
        services.publish(a, actor=creator)
        a.refresh_from_db()
    return a


def login(client, user):
    client.force_login(user)
    return client


# ---------------------------------------------------------------------------
# Audience resolution / visibility
# ---------------------------------------------------------------------------
def test_all_staff_audience_includes_only_active_staff(poster, alice, bob, institution_user, resource_person):
    inactive = mk("gone@x.org", is_active=False)
    a = ann(poster)
    users = set(audience_users(a))
    assert {poster, alice, bob} <= users
    assert institution_user not in users and resource_person not in users and inactive not in users


def test_department_audience(poster, alice, bob, ict):
    a = ann(poster, audience=Announcement.DEPARTMENT, department=ict)
    assert set(audience_users(a)) == {poster, alice}
    assert Announcement.objects.visible_to(alice).filter(pk=a.pk).exists()
    assert not Announcement.objects.visible_to(bob).filter(pk=a.pk).exists()


def test_roles_audience(poster, alice, bob, director_role):
    a = ann(poster, audience=Announcement.ROLES, roles=[director_role])
    assert list(audience_users(a)) == [bob]
    assert list(Announcement.objects.visible_to(bob)) == [a]
    assert not Announcement.objects.visible_to(alice).exists()


def test_roles_audience_no_duplicates_with_many_roles(poster, bob, director_role, officer_role):
    a = ann(poster, audience=Announcement.ROLES, roles=[director_role, officer_role])
    assert Announcement.objects.visible_to(bob).count() == 1
    assert Announcement.objects.visible_to(bob).first() == a


def test_draft_not_visible(poster, alice, client):
    a = ann(poster, publish=False)
    assert not Announcement.objects.visible_to(alice).exists()
    login(client, alice)
    assert client.get(reverse("announcements:detail", args=[a.pk])).status_code == 404
    assert a.title not in client.get(reverse("announcements:list")).content.decode()


def test_expired_hidden_from_list_but_published_queryset_keeps_it(poster, alice):
    a = ann(poster)
    Announcement.objects.filter(pk=a.pk).update(expires_at=timezone.now() - dt.timedelta(minutes=1))
    assert not Announcement.objects.visible_to(alice).exists()
    assert Announcement.objects.visible_to(alice, include_expired=True).exists()


def test_external_users_never_see_announcements(poster, institution_user, resource_person):
    ann(poster)
    assert not Announcement.objects.visible_to(institution_user).exists()
    assert not Announcement.objects.visible_to(resource_person).exists()
    assert not Announcement.objects.unread_for(resource_person).exists()


def test_external_user_cannot_open_pages(poster, resource_person, client):
    a = ann(poster)
    login(client, resource_person)
    for url in (reverse("announcements:list"), reverse("announcements:detail", args=[a.pk])):
        resp = client.get(url)
        assert resp.status_code in (302, 403, 404)
        assert a.title not in resp.content.decode()


# ---------------------------------------------------------------------------
# Publish + notifications
# ---------------------------------------------------------------------------
def test_publish_notifies_audience_once(poster, alice, bob, ict):
    a = ann(poster, publish=False, audience=Announcement.DEPARTMENT, department=ict, kind="urgent")
    assert services.publish(a, actor=poster) == 1  # alice; the poster is not notified
    assert services.publish(a, actor=poster) == 0  # second publish is a no-op
    n = Notification.objects.get(recipient=alice)
    assert n.url == f"/announcements/{a.pk}/"
    assert n.title.startswith("Urgent:")
    assert n.level == "warning"
    assert not Notification.objects.filter(recipient=bob).exists()


def test_publish_query_count_is_flat(poster, django_assert_max_num_queries):
    for i in range(40):
        mk(f"s{i}@x.org")
    a = ann(poster, publish=False)
    with django_assert_max_num_queries(16):  # constant, independent of audience size
        assert services.publish(a, actor=poster) == 40


# ---------------------------------------------------------------------------
# Acknowledge / read
# ---------------------------------------------------------------------------
def test_acknowledge_is_idempotent(poster, alice):
    a = ann(poster, requires_ack=True)
    _, created = services.acknowledge(alice, a)
    assert created
    _, created = services.acknowledge(alice, a)
    assert not created
    assert AnnouncementAck.objects.filter(announcement=a, user=alice).count() == 1
    assert AnnouncementRead.objects.filter(announcement=a, user=alice).exists()


def test_acknowledge_rules(poster, alice, bob, ict):
    a = ann(poster)  # no ack requested
    with pytest.raises(ValidationError):
        services.acknowledge(alice, a)
    b = ann(poster, requires_ack=True, audience=Announcement.DEPARTMENT, department=ict)
    with pytest.raises(PermissionDenied):
        services.acknowledge(bob, b)


def test_acknowledge_view_htmx_swaps_card(poster, alice, client):
    a = ann(poster, requires_ack=True)
    login(client, alice)
    url = reverse("announcements:acknowledge", args=[a.pk])
    resp = client.post(url, {"view": "card"}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert f'id="ann-{a.pk}"' in body and "You acknowledged" in body
    resp = client.post(url, {"view": "detail"}, HTTP_HX_REQUEST="true")
    assert "You acknowledged this" in resp.content.decode()
    assert AnnouncementAck.objects.filter(announcement=a, user=alice).count() == 1


def test_acknowledge_view_plain_post_redirects(poster, alice, client):
    a = ann(poster, requires_ack=True)
    login(client, alice)
    resp = client.post(reverse("announcements:acknowledge", args=[a.pk]))
    assert resp.status_code == 302
    assert AnnouncementAck.objects.filter(announcement=a, user=alice).exists()


def test_detail_marks_read(poster, alice, client):
    a = ann(poster)
    login(client, alice)
    resp = client.get(reverse("announcements:detail", args=[a.pk]))
    assert resp.status_code == 200
    assert "Line one\nLine two" in resp.content.decode()
    assert AnnouncementRead.objects.filter(announcement=a, user=alice).count() == 1
    client.get(reverse("announcements:detail", args=[a.pk]))
    assert AnnouncementRead.objects.filter(announcement=a, user=alice).count() == 1


# ---------------------------------------------------------------------------
# Polls
# ---------------------------------------------------------------------------
def _poll(a, multiple=False, n=3):
    return services.save_poll(a, question="Pick a date?", options=[f"Opt {i}" for i in range(n)],
                              allow_multiple=multiple)


def test_single_choice_vote_replaces_previous(poster, alice):
    a = ann(poster)
    poll = _poll(a)
    o1, o2, _ = poll.options.all()
    services.vote(alice, poll, [o1.pk])
    services.vote(alice, poll, [o2.pk])
    assert list(PollVote.objects.filter(poll=poll, user=alice).values_list("option_id", flat=True)) == [o2.pk]
    with pytest.raises(ValidationError):
        services.vote(alice, poll, [o1.pk, o2.pk])


def test_multiple_choice_vote(poster, alice, bob):
    a = ann(poster)
    poll = _poll(a, multiple=True)
    o1, o2, o3 = poll.options.all()
    services.vote(alice, poll, [o1.pk, o2.pk])
    services.vote(bob, poll, [o1.pk])
    assert PollVote.objects.filter(poll=poll, user=alice).count() == 2
    services.vote(alice, poll, [o3.pk])  # new selection replaces the set
    assert set(services.my_votes(alice, poll)) == {o3.pk}
    res = poll.results()
    assert res["voters"] == 2
    assert [r["votes"] for r in res["rows"]] == [1, 0, 1]
    assert [r["percent"] for r in res["rows"]] == [50, 0, 50]


def test_vote_validation(poster, alice, bob, ict):
    a = ann(poster)
    poll = _poll(a)
    other = _poll(ann(poster, title="Other"))
    with pytest.raises(ValidationError):
        services.vote(alice, poll, [])
    with pytest.raises(ValidationError):
        services.vote(alice, poll, [other.options.first().pk])
    poll.closes_at = timezone.now() - dt.timedelta(minutes=1)
    poll.save()
    with pytest.raises(ValidationError):
        services.vote(alice, poll, [poll.options.first().pk])
    dept_only = ann(poster, title="ICT only", audience=Announcement.DEPARTMENT, department=ict)
    dpoll = _poll(dept_only)
    with pytest.raises(PermissionDenied):
        services.vote(bob, dpoll, [dpoll.options.first().pk])


def test_poll_is_generic_without_announcement(alice):
    poll = AnnouncementPoll.objects.create(question="Standalone?")
    opt = PollOption.objects.create(poll=poll, text="Yes")
    PollOption.objects.create(poll=poll, text="No", position=1)
    assert services.vote(alice, poll, [opt.pk]) == [opt.pk]


def test_vote_view_htmx_returns_results(poster, alice, client):
    a = ann(poster)
    poll = _poll(a)
    o = poll.options.first()
    login(client, alice)
    resp = client.post(reverse("announcements:vote", args=[a.pk]), {"option": o.pk}, HTTP_HX_REQUEST="true")
    body = resp.content.decode()
    assert resp.status_code == 200
    assert f'id="poll-{a.pk}"' in body and "100%" in body and "1 voter" in body


def test_save_poll_locks_options_after_votes(poster, alice):
    a = ann(poster)
    poll = _poll(a)
    services.vote(alice, poll, [poll.options.first().pk])
    services.save_poll(a, question="New question", options=["X", "Y"])
    poll.refresh_from_db()
    assert poll.question == "New question"
    assert list(poll.options.values_list("text", flat=True)) == ["Opt 0", "Opt 1", "Opt 2"]


# ---------------------------------------------------------------------------
# Permissions on create / edit / report
# ---------------------------------------------------------------------------
def test_non_poster_gets_403_on_create_and_report(poster, alice, client):
    a = ann(poster)
    login(client, alice)
    assert client.get(reverse("announcements:create")).status_code == 403
    assert client.post(reverse("announcements:create"), {"title": "x"}).status_code == 403
    assert client.get(reverse("announcements:report", args=[a.pk])).status_code == 403
    assert client.post(reverse("announcements:remind", args=[a.pk])).status_code == 403
    assert client.get(reverse("announcements:edit", args=[a.pk])).status_code == 403


def test_report_perm_holder_and_creator_can_see_report(poster, reporter, client):
    a = ann(poster)
    login(client, reporter)
    assert client.get(reverse("announcements:report", args=[a.pk])).status_code == 200
    login(client, poster)
    assert client.get(reverse("announcements:report", args=[a.pk])).status_code == 200


def test_poster_cannot_edit_someone_elses(poster, ict, client):
    other = mk("other-poster@x.org", ict, perms=["announcements.post_announcement"])
    a = ann(poster)
    login(client, other)
    assert client.get(reverse("announcements:edit", args=[a.pk])).status_code == 403


def _form_data(**kw):
    data = {"title": "Town hall", "body": "Friday 10am.\nMain hall.", "kind": "action_needed",
            "audience": "all_staff", "requires_ack": "on"}
    data.update(kw)
    return data


def test_create_draft_then_publish(poster, alice, client):
    login(client, poster)
    resp = client.post(reverse("announcements:create"), _form_data(action="draft"))
    assert resp.status_code == 302
    a = Announcement.objects.get(title="Town hall")
    assert a.is_draft and a.created_by == poster
    assert not Notification.objects.filter(recipient=alice).exists()
    resp = client.post(reverse("announcements:edit", args=[a.pk]), _form_data(action="publish"))
    assert resp.status_code == 302
    a.refresh_from_db()
    assert a.is_published
    assert Notification.objects.filter(recipient=alice, url=a.get_absolute_url()).count() == 1


def test_create_with_poll_and_attachment(poster, client):
    login(client, poster)
    upload = SimpleUploadedFile("brief.pdf", b"%PDF-1.4 test", content_type="application/pdf")
    resp = client.post(reverse("announcements:create"), _form_data(
        action="publish", poll_question="Which day?", poll_option_1="Mon", poll_option_2="Tue",
        poll_option_3="", attachment=upload,
    ))
    assert resp.status_code == 302, resp.content.decode()[:2000]
    a = Announcement.objects.get(title="Town hall")
    assert list(a.poll.options.values_list("text", flat=True)) == ["Mon", "Tue"]
    att = a.attachments.get()
    assert att.original_name == "brief.pdf"


def test_form_validation(poster, client, ict):
    login(client, poster)
    resp = client.post(reverse("announcements:create"), _form_data(audience="department"))
    assert resp.status_code == 200 and "Choose the department" in resp.content.decode()
    resp = client.post(reverse("announcements:create"), _form_data(poll_question="Q?", poll_option_1="Only"))
    assert "at least two options" in resp.content.decode()
    resp = client.post(reverse("announcements:create"), _form_data(audience="roles"))
    assert "at least one role" in resp.content.decode()
    bad = SimpleUploadedFile("evil.exe", b"MZ", content_type="application/octet-stream")
    resp = client.post(reverse("announcements:create"), _form_data(attachment=bad))
    assert "not allowed" in resp.content.decode()
    assert not Announcement.objects.exists()


def test_attachment_download_scoped_to_audience(poster, alice, bob, ict, client):
    a = ann(poster, audience=Announcement.DEPARTMENT, department=ict)
    att = AnnouncementAttachment.objects.create(
        announcement=a, file=SimpleUploadedFile("memo.pdf", b"%PDF-1.4", content_type="application/pdf"))
    assert att.can_download(alice)
    assert att.can_download(poster)
    assert not att.can_download(bob)


# ---------------------------------------------------------------------------
# Report + reminders
# ---------------------------------------------------------------------------
def test_report_counts(poster, alice, bob):
    a = ann(poster, requires_ack=True)
    poll = _poll(a)
    services.acknowledge(alice, a)
    services.mark_read(bob, a)
    services.vote(bob, poll, [poll.options.first().pk])
    r = services.report(a)
    assert r["audience_size"] == 3  # poster, alice, bob
    assert r["read"] == 3  # the poster counts as read and acknowledged on publish
    assert r["acknowledged"] == 2
    assert {u.pk for u in r["pending"]} == {bob.pk}
    assert r["poll_results"]["voters"] == 1


def test_report_page_and_csv(poster, alice, client):
    a = ann(poster, requires_ack=True)
    login(client, poster)
    resp = client.get(reverse("announcements:report", args=[a.pk]))
    assert resp.status_code == 200 and alice.email in resp.content.decode()
    resp = client.get(reverse("announcements:report", args=[a.pk]) + "?format=csv")
    assert resp["Content-Type"].startswith("text/csv")
    text = resp.content.decode("utf-8-sig")
    assert "Name,Email,Department,Role,Read,Acknowledged" in text
    assert alice.email in text


def test_reminders_go_only_to_pending(poster, alice, bob, client):
    a = ann(poster, requires_ack=True)
    services.acknowledge(alice, a)
    Notification.objects.all().delete()
    login(client, poster)
    resp = client.post(reverse("announcements:remind", args=[a.pk]))
    assert resp.status_code == 302
    recipients = set(Notification.objects.values_list("recipient_id", flat=True))
    assert recipients == {bob.pk}
    assert Notification.objects.filter(recipient=bob, title__startswith="Reminder").exists()


def test_reminder_needs_published(poster):
    a = ann(poster, publish=False)
    with pytest.raises(ValidationError):
        services.remind_pending(a)


# ---------------------------------------------------------------------------
# List filters + sidebar badge
# ---------------------------------------------------------------------------
def test_list_filters_and_pinned_first(poster, alice, client):
    old_pinned = ann(poster, title="Pinned rule", pinned=True)
    needs_ack = ann(poster, title="Sign the register", requires_ack=True)
    read_one = ann(poster, title="Already read")
    services.mark_read(alice, read_one)
    login(client, alice)

    body = client.get(reverse("announcements:list")).content.decode()
    assert body.index("Pinned rule") < body.index("Sign the register")

    body = client.get(reverse("announcements:list") + "?filter=unread").content.decode()
    assert "Sign the register" in body and "Pinned rule" in body and "Already read" not in body

    body = client.get(reverse("announcements:list") + "?filter=ack").content.decode()
    assert "Sign the register" in body and "Pinned rule" not in body

    services.acknowledge(alice, needs_ack)
    body = client.get(reverse("announcements:list") + "?filter=ack").content.decode()
    assert "Sign the register" not in body
    assert old_pinned  # silence linters


def test_drafts_filter_only_for_posters(poster, alice, client):
    ann(poster, title="Secret draft", publish=False)
    login(client, alice)
    assert "Secret draft" not in client.get(reverse("announcements:list") + "?filter=drafts").content.decode()
    login(client, poster)
    assert "Secret draft" in client.get(reverse("announcements:list") + "?filter=drafts").content.decode()


def test_sidebar_badge_counts_unread(poster, alice, ict, hr):
    from core.context_processors import sidebar_counts

    a = ann(poster)
    ann(poster, title="Second")
    ann(poster, title="HR only", audience=Announcement.DEPARTMENT, department=hr)
    ann(poster, title="Draft", publish=False)
    req = RequestFactory().get("/")
    req.user = alice
    assert sidebar_counts(req)["sidebar_counts"]["announcements_unread"] == 2
    services.mark_read(alice, a)
    assert sidebar_counts(req)["sidebar_counts"]["announcements_unread"] == 1


def test_sidebar_link_rendered(poster, alice, client):
    ann(poster)
    login(client, alice)
    body = client.get(reverse("announcements:list")).content.decode()
    assert 'href="/announcements/"' in body
    assert "app-nav-badge" in body


def test_home_banner_shows_unacknowledged_action(poster, alice, client):
    ann(poster, title="Submit your forms", kind="action_needed", requires_ack=True)
    login(client, alice)
    resp = client.get(reverse("core:home"))
    assert resp.status_code == 200
    assert "Submit your forms" in resp.content.decode()


def test_poster_outside_audience_sees_sent_filter(poster, alice, hr, client):
    a = ann(poster, title="For HR only", audience=Announcement.DEPARTMENT, department=hr)
    login(client, poster)
    assert "For HR only" not in client.get(reverse("announcements:list")).content.decode()
    body = client.get(reverse("announcements:list") + "?filter=sent").content.decode()
    assert "For HR only" in body and "Acknowledge</button>" not in body
    assert client.get(reverse("announcements:detail", args=[a.pk])).status_code == 200
    assert not AnnouncementRead.objects.filter(announcement=a, user=poster).exists()
    login(client, alice)
    assert client.get(reverse("announcements:list") + "?filter=sent").status_code == 200


def test_publish_marks_poster_read_and_acked(poster):
    a = ann(poster, requires_ack=True)
    assert AnnouncementRead.objects.filter(announcement=a, user=poster).exists()
    assert AnnouncementAck.objects.filter(announcement=a, user=poster).exists()
