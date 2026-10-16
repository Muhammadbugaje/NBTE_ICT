"""Stage 3 — groups & permissions: resolver, rank rules, has_perm wiring,
views, audit, seed command.

(Placed at accounts/test_groups_permissions.py, not accounts/tests/…: a
`tests/` package would clash with the existing accounts/tests.py module.)
"""
import pytest
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.urls import reverse

from accounts import permissions_service as ps
from accounts.models import GroupProfile, Role, User, UserPermissionOverride
from core.models import AuditLogEntry
from staff.models import Department

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def P(label):
    app, code = label.split(".")
    return Permission.objects.get(content_type__app_label=app, codename=code)


def fresh(user):
    return User.objects.get(pk=user.pk)


def mk(email, dept=None, role=None, **kw):
    u = User.objects.create_user(email=email, password="Xx-123456789", first_name="F",
                                 last_name="L", department=dept, role=role, **kw)
    u.must_change_password = False
    u.save()
    return u


def grant(user, *labels):
    for lab in labels:
        UserPermissionOverride.objects.create(user=user, permission=P(lab), effect="grant")


@pytest.fixture
def ict():
    return Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]


@pytest.fixture
def roles():
    call_command("seed_roles", "--sync", verbosity=0)
    return {r.code: r for r in Role.objects.all()}


@pytest.fixture
def people(ict, roles):
    """The user types the resolver must handle."""
    return {
        "norole": mk("norole@x.org", ict),
        "staff": mk("staff@x.org", ict, roles["staff"]),
        "director": mk("director@x.org", ict, roles["director"]),
        "es": mk("es@x.org", ict, roles["executive-secretary"]),
        # No Chairman role exists: the Chairman is an account with the
        # chairman permission granted individually (review L1).
        "chairman": mk("chair@x.org", ict),
        "superuser": mk("su@x.org", ict, is_superuser=True, is_staff=True),
        "direct": mk("direct@x.org", ict),
    }


@pytest.fixture
def test_group():
    g = Group.objects.create(name="Test Group")
    g.permissions.add(P("tickets.view_ticket"), P("tasks.view_task"), P("staff.add_staff"))
    return g


# ---------------------------------------------------------------------------
# resolver unit tests
# ---------------------------------------------------------------------------
def test_no_role_user_has_nothing(people):
    u = people["norole"]
    assert ps.effective_permissions(u) == set()
    assert ps.inherited_permissions(u) == set()
    assert ps.direct_permissions(u) == set()
    assert list(ps.effective_groups(u)) == []


def test_role_baseline_is_inherited(people, roles):
    d = people["director"]
    baseline = {f"{p.content_type.app_label}.{p.codename}" for p in roles["director"].permissions.all()}
    assert baseline and ps.inherited_permissions(d) == baseline
    assert ps.effective_permissions(d) == baseline
    assert ps.direct_permissions(d) == set()


def test_role_group_permissions_are_inherited(people, roles, test_group):
    roles["director"].groups.add(test_group)
    d = fresh(people["director"])
    assert "tickets.view_ticket" in ps.inherited_permissions(d)
    assert "tickets.view_ticket" in ps.effective_permissions(d)
    assert "tickets.view_ticket" not in ps.direct_permissions(d)
    assert test_group in ps.effective_groups(d)


def test_direct_group_and_direct_permission(people, test_group):
    u = people["direct"]
    u.groups.add(test_group)
    u.user_permissions.add(P("assets.view_asset"))
    u = fresh(u)
    assert {"tickets.view_ticket", "assets.view_asset"} <= ps.direct_permissions(u)
    assert ps.inherited_permissions(u) == set()
    assert {"tickets.view_ticket", "assets.view_asset"} <= ps.effective_permissions(u)
    assert list(ps.effective_groups(u)) == [test_group]


def test_effective_groups_is_union_without_duplicates(people, roles, test_group):
    roles["staff"].groups.add(test_group)
    u = people["staff"]
    u.groups.add(test_group)
    assert list(ps.effective_groups(fresh(u))) == [test_group]


def test_revoke_override_beats_every_source(people, roles, test_group):
    roles["director"].groups.add(test_group)
    d = people["director"]
    d.groups.add(test_group)
    d.user_permissions.add(P("tickets.view_ticket"))
    UserPermissionOverride.objects.create(user=d, permission=P("tickets.view_ticket"), effect="revoke")
    d = fresh(d)
    assert "tickets.view_ticket" not in ps.effective_permissions(d)
    assert not d.has_perm("tickets.view_ticket")


def test_superuser_and_inactive(people):
    su = people["superuser"]
    assert ps.effective_permissions(su) == {
        f"{a}.{c}" for a, c in Permission.objects.values_list("content_type__app_label", "codename")}
    staff = people["staff"]
    staff.is_active = False
    staff.save()
    assert ps.effective_permissions(fresh(staff)) == set()


def test_cache_is_per_instance_and_invalidated(people, test_group):
    u = people["direct"]
    assert "tickets.view_ticket" not in ps.effective_permissions(u)
    u.groups.add(test_group)
    # Same instance: cached until invalidated (one request = one instance).
    assert "tickets.view_ticket" not in ps.effective_permissions(u)
    ps.invalidate(u)
    assert "tickets.view_ticket" in ps.effective_permissions(u)
    # A new instance (= a new request) never sees stale data.
    assert "tickets.view_ticket" in ps.effective_permissions(fresh(u))


# ---------------------------------------------------------------------------
# has_perm wiring (security-sensitive): one check per user type
# ---------------------------------------------------------------------------
def test_has_perm_unchanged_for_every_user_type_without_groups(people, roles):
    """Before any group is assigned, has_perm answers exactly as the role
    baseline + overrides did before Stage 3."""
    assert not people["norole"].has_perm("staff.view_staff")
    assert people["staff"].has_perm("assets.view_asset")
    assert not people["staff"].has_perm("staff.view_staff")
    assert people["director"].has_perm("accounts.approve_requests")
    assert people["es"].has_perm("accounts.view_all_requests")
    assert not people["chairman"].has_perm("accounts.view_chairman_dashboard")
    grant(people["chairman"], "accounts.view_chairman_dashboard")
    assert fresh(people["chairman"]).has_perm("accounts.view_chairman_dashboard")
    assert people["superuser"].has_perm("anything.at_all") is True
    people["direct"].user_permissions.add(P("tickets.view_ticket"))
    assert fresh(people["direct"]).has_perm("tickets.view_ticket")


def test_role_group_reaches_every_holder_via_has_perm(ict, roles, test_group):
    d1 = mk("d1@x.org", ict, roles["director"])
    d2 = mk("d2@x.org", ict, roles["director"])
    staff = mk("s1@x.org", ict, roles["staff"])
    for label in ("tickets.view_ticket", "tasks.view_task", "staff.add_staff"):
        assert not d1.has_perm(label)
    roles["director"].groups.add(test_group)
    for u in (d1, d2):
        u = fresh(u)
        assert u.has_perms(["tickets.view_ticket", "tasks.view_task", "staff.add_staff"])
        assert "tickets.view_ticket" in u.get_all_permissions()
        assert "tickets.view_ticket" in u.get_group_permissions()
    assert not fresh(staff).has_perm("tickets.view_ticket")


def test_direct_group_reaches_user_via_has_perm(people, test_group):
    u = people["staff"]
    u.groups.add(test_group)
    assert fresh(u).has_perm("staff.add_staff")


# ---------------------------------------------------------------------------
# rank / ownership rules
# ---------------------------------------------------------------------------
@pytest.fixture
def assigner(ict, roles):
    """An office head who may assign groups but holds only a few permissions."""
    u = mk("head@x.org", ict, roles["office-head-ts"])
    grant(u, "accounts.assign_groups", "accounts.view_user")
    return fresh(u)


def test_can_act_on_user_matches_h3(people, assigner):
    assert ps.can_act_on_user(assigner, people["staff"])
    assert not ps.can_act_on_user(assigner, people["director"])
    assert not ps.can_act_on_user(assigner, people["es"])
    assert not ps.can_act_on_user(assigner, people["superuser"])
    assert ps.can_act_on_user(people["superuser"], people["es"])
    assert not ps.can_act_on_user(people["staff"], people["director"])


def test_can_assign_group_rules(people, assigner, roles, test_group):
    small = Group.objects.create(name="Small")
    small.permissions.add(P("staff.view_staff"))               # the office head holds this
    # holds all the group's permissions + target below -> allowed
    assert ps.can_assign_group(assigner, people["staff"], small)
    # group carries permissions the assigner doesn't hold -> refused
    assert not ps.can_assign_group(assigner, people["staff"], test_group)
    # target outranks the assigner -> refused
    assert not ps.can_assign_group(assigner, people["director"], small)
    # roles: same seniority rule
    assert ps.can_assign_group(assigner, roles["staff"], small)
    assert not ps.can_assign_group(assigner, roles["director"], small)
    # no accounts.assign_groups -> refused for everyone
    for t in ("norole", "staff", "director", "es", "chairman"):
        assert not ps.can_assign_group(people[t], people["norole"], small)
    # superuser may always
    assert ps.can_assign_group(people["superuser"], people["es"], test_group)


def test_can_assign_permission_rules(people, assigner):
    assert ps.can_assign_permission(assigner, people["staff"], P("staff.view_staff"))
    assert not ps.can_assign_permission(assigner, people["staff"], P("staff.delete_staff"))
    assert not ps.can_assign_permission(assigner, people["director"], P("staff.view_staff"))
    assert not ps.can_assign_permission(people["staff"], people["norole"], P("assets.view_asset"))
    assert ps.can_assign_permission(people["superuser"], people["es"], "staff.delete_staff")


# ---------------------------------------------------------------------------
# migration-backed permission
# ---------------------------------------------------------------------------
def test_assign_groups_permission_exists():
    assert Permission.objects.filter(content_type__app_label="accounts", codename="assign_groups").exists()


# ---------------------------------------------------------------------------
# views (end to end)
# ---------------------------------------------------------------------------
@pytest.fixture
def admin_user(ict):
    return mk("admin@x.org", ict, is_superuser=True, is_staff=True)


def test_group_list_view(client, admin_user, test_group):
    client.force_login(admin_user)
    resp = client.get(reverse("accounts:groups:list"), {"q": "Test"})
    assert resp.status_code == 200
    assert b"Test Group" in resp.content and b"data-group-delete" in resp.content
    assert client.get(reverse("accounts:groups:list"), {"app": "tickets"}).status_code == 200


def test_group_list_requires_permission(client, people):
    client.force_login(people["staff"])
    assert client.get(reverse("accounts:groups:list")).status_code == 403


def test_group_create_view_and_audit(client, admin_user):
    client.force_login(admin_user)
    resp = client.post(reverse("accounts:groups:create"), {
        "name": "Help desk leads", "description": "Leads of the help desk",
        "permissions": [P("tickets.view_ticket").pk, P("tickets.change_ticket").pk],
    })
    g = Group.objects.get(name="Help desk leads")
    assert resp.status_code == 302 and resp["Location"] == reverse("accounts:groups:detail", args=[g.pk])
    assert g.permissions.count() == 2
    assert GroupProfile.objects.get(group=g).description == "Leads of the help desk"
    entry = AuditLogEntry.objects.filter(model_label="auth.Group", object_pk=str(g.pk)).latest("id")
    assert entry.actor == admin_user and entry.action == "create"
    assert set(entry.changes["after"]) == {"tickets.view_ticket", "tickets.change_ticket"}
    # signals are suppressed while the view logs: exactly one entry
    assert AuditLogEntry.objects.filter(model_label="auth.Group", object_pk=str(g.pk)).count() == 1


def test_group_create_save_and_add_another(client, admin_user):
    client.force_login(admin_user)
    resp = client.post(reverse("accounts:groups:create"), {"name": "One", "save_add": "1"})
    assert resp["Location"] == reverse("accounts:groups:create")


def test_group_create_duplicate_name_rejected(client, admin_user, test_group):
    client.force_login(admin_user)
    resp = client.post(reverse("accounts:groups:create"), {"name": "test group"})
    assert resp.status_code == 200 and b"already exists" in resp.content


def test_group_update_view(client, admin_user, test_group):
    client.force_login(admin_user)
    resp = client.post(reverse("accounts:groups:update", args=[test_group.pk]), {
        "name": "Test Group", "permissions": [P("tickets.view_ticket").pk],
    })
    assert resp.status_code == 302
    assert set(ps.group_permissions(test_group)) == {"tickets.view_ticket"}
    entry = AuditLogEntry.objects.filter(model_label="auth.Group").latest("id")
    assert set(entry.changes["removed"]) == {"tasks.view_task", "staff.add_staff"}


def test_group_edit_cannot_add_unheld_permission(client, ict, roles):
    editor = mk("ed@x.org", ict, roles["office-head-ts"])
    grant(editor, "auth.add_group", "auth.change_group", "auth.view_group")
    client.force_login(editor)
    resp = client.post(reverse("accounts:groups:create"), {
        "name": "Sneaky", "permissions": [P("staff.delete_staff").pk],
    })
    assert resp.status_code == 200 and b"only add permissions you hold" in resp.content
    assert not Group.objects.filter(name="Sneaky").exists()


def test_group_delete_view(client, admin_user, test_group):
    client.force_login(admin_user)
    assert client.get(reverse("accounts:groups:delete", args=[test_group.pk])).status_code == 200
    resp = client.post(reverse("accounts:groups:delete", args=[test_group.pk]))
    assert resp.status_code == 302 and not Group.objects.filter(pk=test_group.pk).exists()
    assert AuditLogEntry.objects.filter(model_label="auth.Group", action="delete").exists()


def test_group_detail_view(client, admin_user, test_group, roles, ict):
    roles["director"].groups.add(test_group)
    mk("member@x.org", ict, roles["director"])
    client.force_login(admin_user)
    resp = client.get(reverse("accounts:groups:detail", args=[test_group.pk]))
    assert resp.status_code == 200
    assert b"Director of Department" in resp.content
    assert b"Through role" in resp.content            # the director shows as a member via the role
    assert b"tickets.view_ticket" in resp.content


def test_role_groups_edit_view_and_has_perm(client, admin_user, roles, ict, test_group):
    director = mk("dd@x.org", ict, roles["director"])
    client.force_login(admin_user)
    assert client.get(reverse("accounts:groups:roles")).status_code == 302
    url = reverse("accounts:groups:role_groups", args=[roles["director"].pk])
    assert client.get(url).status_code == 200
    resp = client.post(url, {"groups": [test_group.pk]})
    assert resp.status_code == 302
    assert list(roles["director"].groups.all()) == [test_group]
    assert fresh(director).has_perms(["tickets.view_ticket", "tasks.view_task", "staff.add_staff"])
    entry = AuditLogEntry.objects.filter(model_label="accounts.Role").latest("id")
    assert entry.actor == admin_user and entry.changes["groups_after"] == ["Test Group"]
    assert "tickets.view_ticket" in entry.changes["added"]


def test_lower_rank_cannot_change_higher_role(client, assigner, roles, test_group):
    small = Group.objects.create(name="Small")
    small.permissions.add(P("staff.view_staff"))
    client.force_login(assigner)
    resp = client.post(reverse("accounts:groups:role_groups", args=[roles["director"].pk]),
                       {"groups": [small.pk]})
    assert resp.status_code == 403 and b"ranked above you" in resp.content
    assert not roles["director"].groups.exists()


def test_role_groups_crafted_post_of_unheld_group_rejected(client, assigner, roles, test_group):
    client.force_login(assigner)
    resp = client.post(reverse("accounts:groups:role_groups", args=[roles["staff"].pk]),
                       {"groups": [test_group.pk]})
    assert resp.status_code == 200 and b"can&#x27;t assign these groups" in resp.content
    assert not roles["staff"].groups.exists()


def test_user_groups_edit_view(client, admin_user, people, test_group):
    target = people["staff"]
    client.force_login(admin_user)
    url = reverse("accounts:groups:user_groups", args=[target.pk])
    assert client.get(url).status_code == 200
    resp = client.post(url, {"groups": [test_group.pk], "permissions": [P("assets.add_asset").pk]})
    assert resp.status_code == 302
    t = fresh(target)
    assert t.has_perm("staff.add_staff") and t.has_perm("assets.add_asset")
    entry = AuditLogEntry.objects.filter(model_label="accounts.User", object_pk=str(t.pk)).latest("id")
    assert entry.actor == admin_user
    assert "staff.add_staff" in entry.changes["effective_added"]
    assert entry.changes["groups_after"] == ["Test Group"]


def test_lower_rank_cannot_act_on_higher_user(client, assigner, people):
    small = Group.objects.create(name="Small")
    small.permissions.add(P("staff.view_staff"))
    client.force_login(assigner)
    url = reverse("accounts:groups:user_groups", args=[people["director"].pk])
    resp = client.post(url, {"groups": [small.pk]})
    assert resp.status_code == 403 and b"ranked above you" in resp.content
    assert not people["director"].groups.exists()


def test_assigner_can_give_held_group_to_junior_but_not_unheld(client, assigner, people, test_group):
    small = Group.objects.create(name="Small")
    small.permissions.add(P("staff.view_staff"))
    client.force_login(assigner)
    url = reverse("accounts:groups:user_groups", args=[people["staff"].pk])
    assert client.post(url, {"groups": [small.pk]}).status_code == 302
    assert list(people["staff"].groups.all()) == [small]
    resp = client.post(url, {"groups": [small.pk, test_group.pk]})
    assert resp.status_code == 200 and b"can&#x27;t assign these groups" in resp.content
    assert list(people["staff"].groups.all()) == [small]
    resp = client.post(url, {"groups": [small.pk], "permissions": [P("staff.delete_staff").pk]})
    assert resp.status_code == 200 and b"only grant permissions you hold" in resp.content


def test_locked_items_survive_a_save_by_a_junior_assigner(client, assigner, people, test_group):
    """A group the assigner may not touch keeps its state when they save."""
    target = people["staff"]
    target.groups.add(test_group)   # added earlier by an admin
    client.force_login(assigner)
    resp = client.post(reverse("accounts:groups:user_groups", args=[target.pk]), {})
    assert resp.status_code == 302
    assert test_group in fresh(target).groups.all()


def test_permission_matrix_view(client, admin_user, roles, test_group):
    roles["director"].groups.add(test_group)
    client.force_login(admin_user)
    resp = client.get(reverse("accounts:groups:matrix"))
    assert resp.status_code == 200
    assert b"matrix-data" in resp.content and b"Director of Department" in resp.content
    data = resp.context["detail"]
    key = f"{roles['director'].pk}:tickets"
    assert key in data and any(p["code"] == "view_ticket" for p in data[key]["perms"])


def test_user_access_list_view(client, admin_user, people):
    client.force_login(admin_user)
    resp = client.get(reverse("accounts:groups:users"), {"q": "staff"})
    assert resp.status_code == 200 and b"staff@x.org" in resp.content


def test_sidebar_section_visibility(client, admin_user, people):
    client.force_login(admin_user)
    assert b"User Management" in client.get(reverse("core:home")).content
    client.force_login(people["staff"])
    assert b"User Management" not in client.get(reverse("core:home")).content


# ---------------------------------------------------------------------------
# admin parity & audit via other paths
# ---------------------------------------------------------------------------
def test_admin_paths_are_audited(roles, test_group):
    roles["staff"].groups.add(test_group)
    e = AuditLogEntry.objects.filter(model_label="accounts.Role").latest("id")
    assert e.changes == {"groups_added": ["Test Group"]}
    test_group.permissions.add(P("assets.view_asset"))
    e = AuditLogEntry.objects.filter(model_label="auth.Group").latest("id")
    assert e.changes == {"permissions_added": ["assets.view_asset"]}


def test_admin_pages_render(client, admin_user, roles, test_group):
    client.force_login(admin_user)
    assert client.get(reverse("admin:auth_group_change", args=[test_group.pk])).status_code == 200
    resp = client.get(reverse("admin:accounts_role_change", args=[roles["director"].pk]))
    assert resp.status_code == 200 and b'name="groups"' in resp.content


# ---------------------------------------------------------------------------
# seed command
# ---------------------------------------------------------------------------
def test_seed_default_groups_is_idempotent(roles, capsys):
    call_command("seed_default_groups")
    out = capsys.readouterr().out
    names = ["Staff", "Director", "Deputy Director", "Office Head",
             "Executive Secretary", "Chairman", "Superuser"]
    assert set(Group.objects.values_list("name", flat=True)) >= set(names)
    assert Group.objects.get(name="Director") in roles["director"].groups.all()
    assert set(Group.objects.get(name="Office Head").roles.values_list("code", flat=True)) == {
        "office-head-ts", "office-head-dev", "office-head-admin"}
    assert not Group.objects.get(name="Chairman").roles.exists()
    assert "created" in out
    before = {g.name: set(g.permissions.values_list("pk", flat=True)) for g in Group.objects.all()}
    Group.objects.get(name="Staff").permissions.add(P("tickets.view_ticket"))   # admin customised it
    call_command("seed_default_groups")
    out = capsys.readouterr().out
    assert out.count("already exists") == len(names)
    assert P("tickets.view_ticket") in Group.objects.get(name="Staff").permissions.all()  # not overwritten
    assert {g.name for g in Group.objects.all()} == set(before)
