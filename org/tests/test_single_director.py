"""Part 1a: one director per department, one ES (org.single_holder)."""
import io

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def _role(code, name, rank):
    from accounts.models import Role
    return Role.objects.get_or_create(code=code, defaults={"name": name, "rank": rank})[0]


def _user(email, role, dept):
    from accounts.models import User
    return User.objects.create(email=email, first_name=email.split("@")[0], last_name="T", user_type="staff",
                               role=role, department=dept, must_change_password=False)


@pytest.fixture
def org_world(db):
    from staff.models import Department
    d = Department.objects.get_or_create(code="ntkd", defaults={"name": "Test Department"})[0]
    roles = {"director": _role("director", "Director", 100), "es": _role("executive-secretary", "ES", 999),
             "staff": _role("staff", "General Staff", 10), "officer": _role("officer", "Officer", 20)}
    es = _user("es@t.local", roles["es"], None)
    return d, roles, es


def test_second_director_in_a_department_is_refused(org_world):
    import org
    from org.services import OrgRuleError
    d, roles, es = org_world
    first = _user("first@t.local", roles["officer"], d)
    second = _user("second@t.local", roles["officer"], d)
    org.services.assign_director(d, first, es, reason="test")
    with pytest.raises(OrgRuleError, match="already has a director: .*Vacate the seat first"):
        org.services.assign_role(es, second, "director", "test")
    with pytest.raises(OrgRuleError, match="already has a director"):  # no ES override bypasses it
        org.services.assign_role(es, second, "director", "test", override=True)
    with pytest.raises(OrgRuleError):
        org.services.assign_director(d, second, es, reason="test")
    assert "already has a director" in " ".join(org.services.role_problems(es, second, roles["director"]))


def test_second_es_is_refused(org_world):
    import org
    from org.services import OrgRuleError
    d, roles, es = org_world
    other = _user("other@t.local", roles["officer"], d)
    with pytest.raises(OrgRuleError, match="already held by"):
        org.services.assign_role(None, other, "executive-secretary", "test")


def test_command_vacates_a_duplicated_seat(org_world, tmp_path, django_capture_on_commit_callbacks):
    from accounts.models import User
    from core.models import AuditLogEntry
    from notifications.models import Notification
    from org.management.commands import fix_duplicate_directors as cmd
    from staff.models import Department
    d, roles, es = org_world
    a = _user("a@t.local", roles["director"], d)   # created directly: the old data the rule now forbids
    b = _user("b@t.local", roles["director"], d)
    Department.objects.filter(pk=d.pk).update(director=a)
    cmd.REPORT = tmp_path / "duplicate_directors.md"

    call_command("fix_duplicate_directors", "--dry-run", stdout=io.StringIO())
    assert User.objects.get(pk=b.pk).role.code == "director"           # dry run changes nothing
    assert "a@t.local" in cmd.REPORT.read_text()

    out = io.StringIO()
    with django_capture_on_commit_callbacks(execute=True):
        call_command("fix_duplicate_directors", "--apply", "--by", "es@t.local", stdout=out)
    assert {User.objects.get(pk=u.pk).role.code for u in (a, b)} == {"staff"}
    assert Department.objects.get(pk=d.pk).director_id is None
    assert AuditLogEntry.objects.filter(note__contains="fix_duplicate_directors").count() >= 3
    assert Notification.objects.filter(recipient=es, title__startswith="Seat vacated").exists()
    out = io.StringIO()
    call_command("fix_duplicate_directors", "--dry-run", stdout=out)
    assert "No duplicates found" in out.getvalue()
