"""v2.3 Phase 1: query budgets. The task list and the sidebar memo badge must
cost the same number of queries whether there are 2 rows or 20."""
import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from tasks.conftest import mk
from tasks.models import Task, TaskAssignee

pytestmark = pytest.mark.django_db
_ref = {"n": 0}


def _tasks(creator, dept, count, assignees):
    for _ in range(count):
        _ref["n"] += 1
        parent = Task.objects.create(reference=f"TSK-QB-{_ref['n']:04d}", title=f"t{_ref['n']}",
                                     created_by=creator, owner_department=dept, state=Task.OPEN)
        for user in assignees:
            TaskAssignee.objects.create(task=parent, user=user)
        _ref["n"] += 1
        Task.objects.create(reference=f"TSK-QB-{_ref['n']:04d}", title="child", created_by=creator,
                            owner_department=dept, state=Task.OPEN, parent=parent)


def _count(client, url):
    client.get(url)  # warm per-user caches (sidebar), as a real second visit would be
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200
    return len(ctx)


def test_task_list_queries_do_not_grow_with_rows(people, depts):
    director = people["director"]
    officers = [people["officer"], people["officer2"]]
    client = Client()
    client.force_login(director)

    _tasks(director, depts["ict"], 2, officers)
    small = _count(client, "/tasks/")
    _tasks(director, depts["ict"], 10, officers)
    cache.clear()
    large = _count(client, "/tasks/")

    assert large <= small + 2, f"/tasks/ grew from {small} to {large} queries with 10 more rows (N+1)"
    assert large <= 60, f"/tasks/ used {large} queries (budget 60)"


def test_kanban_queries_do_not_grow_with_rows(people, depts):
    director = people["director"]
    client = Client()
    client.force_login(director)
    _tasks(director, depts["ict"], 2, [people["officer"]])
    small = _count(client, "/tasks/?view=kanban")
    _tasks(director, depts["ict"], 8, [people["officer"]])
    cache.clear()
    large = _count(client, "/tasks/?view=kanban")
    assert large <= small + 2, f"kanban grew from {small} to {large} queries"


def test_memo_badge_is_cached_per_user(people, monkeypatch):
    from messaging import memos as memo_service
    from messaging.templatetags.memo_tags import memo_badge_count

    calls = {"n": 0}
    real = memo_service.awaiting_memos_for

    def counting(user):
        calls["n"] += 1
        return real(user)

    monkeypatch.setattr(memo_service, "awaiting_memos_for", counting)
    es = people["es"]
    first = memo_badge_count(es)
    second = memo_badge_count(es)
    assert first == second
    assert calls["n"] == 1, "the badge must not re-resolve every memo's approver on each page"
    memo_badge_count(people["director"])  # a different person never shares the cached value
    assert calls["n"] == 2


def test_config_reads_cost_one_query_per_scope(db):
    import config
    from config.models import ConfigEntry
    from config.registry import clear_local

    for i in range(15):
        ConfigEntry.objects.create(key=f"test.qb.key{i}", value=str(i), value_type="int")
    cache.clear()
    clear_local()
    with CaptureQueriesContext(connection) as ctx:
        values = [config.get(f"test.qb.key{i}") for i in range(15)]
        missing = config.get("test.qb.absent", "fallback")
    assert values == list(range(15))
    assert missing == "fallback"
    # one query for the scope + one for the key that does not exist (then cached)
    assert len(ctx) <= 2, f"15 settings cost {len(ctx)} queries (expected 2)"
    with CaptureQueriesContext(connection) as again:
        config.get("test.qb.absent", "fallback")
        config.get("test.qb.key3")
    assert len(again) == 0


def test_config_write_is_visible_at_once(db):
    import config
    from config.models import ConfigEntry

    entry = ConfigEntry.objects.create(key="test.qb.live", value="1", value_type="int")
    assert config.get("test.qb.live") == 1
    entry.value = "2"
    entry.save()
    assert config.get("test.qb.live") == 2
    entry.delete()
    assert config.get("test.qb.live", "gone") == "gone"


def test_config_row_written_behind_our_back_is_found(db):
    """bulk_create skips signals; a key never read before must still be found."""
    import config
    from config.models import ConfigEntry

    assert config.get("test.qb.other", 0) == 0 or True  # warm the scope map first
    config.get("some.unrelated.key", None)
    ConfigEntry.objects.bulk_create([ConfigEntry(key="test.qb.bulk", value="7", value_type="int", scope="global")])
    assert config.get("test.qb.bulk") == 7
