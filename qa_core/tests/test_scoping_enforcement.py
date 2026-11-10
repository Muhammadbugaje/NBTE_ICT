"""Scoping enforcement (Master Plan §4) — the three layers, all required in CI.

1. Model audit: every institution-bearing model in the QA apps uses
   InstitutionScopedManager as its default manager.
2. Source audit: views / portal views / forms / api modules of the QA apps
   never touch an institution-bearing model except through ``.for_user(``,
   and never use ``.unscoped`` / ``._base_manager``. A deliberately failing
   fixture proves the audit catches ``Institution.objects.all()`` in a view.
3. Behaviour: as institution A's user (and as a DPAP officer), requesting
   B's objects on every portal URL and every internal QA URL answers 404 —
   never 200, never 403 (403 leaks existence).
"""
import ast
import datetime
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone

from accounts.scoping import scope_to_institution
from qa_core.scoping import InstitutionScopedManager, UnscopedQueryError, institution_bearing_models

QA_APPS = ("qa_core", "institutions", "payments", "accreditation", "inspection", "complaints")
AUDITED_FILES = ("views.py", "portal_views.py", "forms.py", "portal_forms.py", "api.py",
                 "qa_views.py", "qa_portal_views.py",  # Phase 3 (SOP-02) view modules
                 "case_views.py")  # Phase 4 (SOP-03) view module
QUERY_METHODS = {"all", "filter", "get", "exclude", "first", "last", "count", "exists", "values",
                 "values_list", "aggregate", "annotate", "order_by", "update", "delete", "iterator",
                 "get_or_create", "update_or_create", "in_bulk", "select_related", "prefetch_related"}


# =========================================================================== layer 1
def test_layer1_every_institution_bearing_model_uses_the_scoped_manager():
    bearing = [m for m in institution_bearing_models() if m._meta.app_label in QA_APPS]
    labels = {m._meta.label for m in bearing}
    assert {"institutions.Institution", "qa_core.TimelineEvent"} <= labels
    for model in bearing:
        assert type(model._default_manager) is InstitutionScopedManager, model._meta.label
        assert hasattr(model, "unscoped"), model._meta.label


def test_layer1_no_qa_model_with_an_institution_fk_forgets_institution_path():
    from institutions.models import Institution

    for model in apps.get_models():
        if model._meta.app_label not in QA_APPS or model is Institution:
            continue
        # A model may name institution FKs that are attributes, not ownership
        # (Phase 5: ResourcePerson.home_institution, used for conflict of interest).
        attributes = set(getattr(model, "INSTITUTION_FK_ATTRIBUTES", ()))
        has_fk = any(getattr(f, "related_model", None) is Institution and not f.auto_created
                     and f.name not in attributes for f in model._meta.get_fields())
        if has_fk:
            assert hasattr(model, "INSTITUTION_PATH"), f"{model._meta.label} needs INSTITUTION_PATH"


# =========================================================================== layer 2
def _bearing_names():
    return {m.__name__ for m in institution_bearing_models() if m._meta.app_label in QA_APPS}


def _chain(node):
    """Attribute/call chain of an expression as a list of names, outermost last."""
    parts = []
    while True:
        if isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Subscript):
            node = node.value
        elif isinstance(node, ast.Name):
            parts.append(node.id)
            break
        else:
            break
    return list(reversed(parts))


def audit_source(source, filename="<source>", model_names=None):
    """Return a list of violations for one module's source."""
    model_names = set(model_names if model_names is not None else _bearing_names())
    if "# scoped-by-caller" in source:
        return []
    violations = []
    tree = ast.parse(source, filename)
    seen = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Attribute, ast.Call)):
            continue
        chain = _chain(node)
        if not chain or chain[0] not in model_names:
            continue
        key = (node.lineno, chain[0])
        if "unscoped" in chain or "_base_manager" in chain:
            if key not in seen:
                violations.append(f"{filename}:{node.lineno}: {'.'.join(chain)} bypasses scoping")
                seen.add(key)
            continue
        if len(chain) >= 3 and chain[1] in ("objects", "_default_manager") and chain[2] != "for_user" \
                and chain[2] in QUERY_METHODS:
            if key not in seen:
                violations.append(f"{filename}:{node.lineno}: {'.'.join(chain)} without .for_user()")
                seen.add(key)
    return violations


def _audited_files():
    root = Path(settings.BASE_DIR)
    for app in QA_APPS:
        for name in AUDITED_FILES:
            path = root / app / name
            if path.exists():
                yield path


def test_layer2_source_audit_of_the_qa_apps_is_clean():
    files = list(_audited_files())
    assert any(f.name == "portal_views.py" for f in files)
    problems = []
    for path in files:
        problems += audit_source(path.read_text(), str(path.relative_to(settings.BASE_DIR)))
    assert not problems, "\n".join(problems)


DELIBERATELY_BAD_VIEW = '''
from institutions.models import Institution

def institution_list(request):
    return render(request, "x.html", {"rows": Institution.objects.all()})
'''


def test_layer2_deliberately_failing_fixture_is_caught():
    """Definition of done: the enforcement test fails if someone writes
    Institution.objects.all() in a view."""
    problems = audit_source(DELIBERATELY_BAD_VIEW, "bad_views.py")
    assert problems and "Institution.objects.all" in problems[0]
    assert audit_source(DELIBERATELY_BAD_VIEW.replace(".all()", ".for_user(request.user)"), "ok.py") == []
    assert audit_source("from institutions.models import Institution\nInstitution.unscoped.all()", "b.py")
    assert audit_source("from institutions.models import Institution\nInstitution._base_manager.all()", "b.py")
    assert audit_source("# scoped-by-caller\nInstitution.objects.all()", "services/x.py") == []


@pytest.mark.django_db
def test_layer2_runtime_guard_raises_on_unscoped_evaluation(qa_seed, make_institution):
    from institutions.models import Institution
    from qa_core.models import TimelineEvent

    make_institution("AAA")
    for qs in (Institution.objects.all(), Institution.objects.filter(code="AAA"),
               TimelineEvent.objects.all()):
        with pytest.raises(UnscopedQueryError):
            list(qs)
    with pytest.raises(UnscopedQueryError):
        Institution.objects.count()
    with pytest.raises(UnscopedQueryError):
        Institution.objects.exists()
    with pytest.raises(UnscopedQueryError):
        Institution.objects.get(code="AAA")
    assert Institution.unscoped.filter(code="AAA").count() == 1  # the explicit system path


@pytest.mark.django_db
def test_layer2_production_mode_logs_instead_of_raising(qa_seed, make_institution, settings, caplog):
    from institutions.models import Institution

    settings.QA_SCOPING_STRICT = False
    make_institution("AAA")
    with caplog.at_level("ERROR", logger="qa_core.scoping"):
        assert len(list(Institution.objects.all())) == 1
    assert "Unscoped query on institutions.Institution" in caplog.text


# =========================================================================== scope_to_institution
@pytest.fixture
def tenants(qa_seed, make_institution, make_user):
    a = make_institution("AAA", "dpap", "Alpha Polytechnic")
    b = make_institution("BBB", "vtsd", "Bravo Technical College")
    return {
        "a": a, "b": b,
        "a_user": make_user("admin@aaa.local", user_type="institution", institution=a, role="inst-admin"),
        "b_user": make_user("admin@bbb.local", user_type="institution", institution=b, role="inst-admin"),
        "dpap_officer": make_user("o@dpap.local", role="officer", dept="dpap", groups=["QA Desk"]),
        "vtsd_officer": make_user("o@vtsd.local", role="officer", dept="vtsd", groups=["QA Desk"]),
        "rp": make_user("rp@x.local", user_type="resource_person", role="resource-person"),
        "nodept": make_user("n@x.local", role="officer"),
        "monitor": make_user("m@ict.local", role="ict-qa-monitor", dept="ict"),
    }


@pytest.mark.django_db
def test_scope_to_institution_rules(tenants):
    from django.contrib.auth.models import AnonymousUser

    from institutions.models import Institution

    codes = lambda user: set(Institution.objects.for_user(user).values_list("code", flat=True))  # noqa: E731
    assert codes(tenants["a_user"]) == {"AAA"}
    assert codes(tenants["b_user"]) == {"BBB"}
    assert codes(tenants["dpap_officer"]) == {"AAA"}       # derived from department (§4)
    assert codes(tenants["vtsd_officer"]) == {"BBB"}
    assert codes(tenants["rp"]) == set()                    # no engagements yet: fail closed
    assert codes(tenants["nodept"]) == set()               # no department: fail closed
    assert codes(tenants["monitor"]) == {"AAA", "BBB"}     # qa_core.view_all_institutions
    assert codes(AnonymousUser()) == set()
    # A field path works the same way.
    from qa_core import timeline
    timeline.record(tenants["a"], "x", public_text="A", visibility="public")
    timeline.record(tenants["b"], "x", public_text="B", visibility="public")
    from qa_core.models import TimelineEvent
    assert [e.public_text for e in TimelineEvent.objects.for_user(tenants["a_user"])] == ["A"]
    # The plain helper fails closed for an institution user without an institution.
    tenants["a_user"].institution_id = None
    assert not scope_to_institution(Institution.unscoped.all(), tenants["a_user"], field=None).exists()


@pytest.mark.django_db
def test_resource_person_scope_follows_registered_engagements(tenants):
    from accounts import scoping
    from institutions.models import Institution

    def resolver(user):
        return [tenants["b"].pk] if user.pk == tenants["rp"].pk else []

    scoping.register_engagement_resolver(resolver)
    try:
        assert list(Institution.objects.for_user(tenants["rp"]).values_list("code", flat=True)) == ["BBB"]
    finally:
        scoping._engagement_resolvers.remove(resolver)


@pytest.mark.django_db
def test_user_type_check_constraint(qa_seed, make_institution):
    from django.db import IntegrityError, transaction

    from accounts.models import User

    inst = make_institution("CCC")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("x@x.local", "pw", user_type="institution")  # no institution
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("y@x.local", "pw", user_type="staff", institution=inst)
    User.objects.create_user("z@x.local", "pw", user_type="resource_person", institution=inst)  # allowed (COI)
    User.objects.create_user("w@x.local", "pw", user_type="resource_person")


# =========================================================================== layer 3
def _patterns(urlconf, prefix=""):
    def walk(patterns, pre):
        for p in patterns:
            if isinstance(p, URLResolver):
                yield from walk(p.url_patterns, pre + str(p.pattern))
            elif isinstance(p, URLPattern):
                yield pre + str(p.pattern), p
    return list(walk(get_resolver(urlconf).url_patterns, prefix))


def _fill(route, pk):
    import re
    path = re.sub(r"<int:\w+>", str(pk), route)
    path = re.sub(r"<[^>]+>", "x", path)
    return "/" + path


# Settings rows, flows, formats and templates are global configuration, not
# institution data; they are the same for every tenant by design (§14).
GLOBAL_CONFIG_ROUTES = ("qa/settings/",)


@pytest.mark.django_db
def test_layer3_cross_tenant_requests_answer_404(tenants, make_user, portal_host):
    from config.models import ConfigEntry
    from qa_core import actions, timeline

    # B's records: a public timeline event and a pending action in B's department.
    timeline.record(tenants["b"], "x", public_text="Bravo secret milestone", visibility="public")
    timeline.record(tenants["a"], "x", public_text="Alpha milestone", visibility="public")
    vtsd_admin = make_user("sa@vtsd.local", role="officer", dept="vtsd", groups=["QA Settings Admin"])
    b_pending = actions.initiate("config.update_setting", actor=vtsd_admin,
                                 target=ConfigEntry.objects.get(key="qa.impersonation.max_minutes"),
                                 payload={"value": "20"})
    assert b_pending.scope_department.code == "vtsd"

    # --- portal URLconf, as A's institution user, with B's primary keys
    portal = Client(HTTP_HOST=portal_host)
    portal.force_login(tenants["a_user"])
    portal_routes = [r for r, _p in _patterns(settings.QA_PORTAL_URLCONF) if not r.startswith(("static", "^static"))]
    for route in portal_routes:
        if "<" not in route:
            continue
        for pk in (tenants["b"].pk, tenants["b_user"].pk, b_pending.pk):
            r = portal.get(_fill(route, pk))
            assert r.status_code not in (200, 403) or "password/set" in route, (route, r.status_code)
    page = portal.get("/dashboard/").content.decode()
    assert "Alpha milestone" in page and "Bravo" not in page and "BBB" not in page

    # --- internal QA URLconf, as a DPAP officer, with B's (VTSD) objects
    dpap = Client(HTTP_HOST="localhost")
    dpap.force_login(tenants["dpap_officer"])
    checked = 0
    for route, _p in _patterns(settings.ROOT_URLCONF):
        if not route.startswith("qa/") or "<int:" not in route or route.startswith(GLOBAL_CONFIG_ROUTES):
            continue
        path = _fill(route, b_pending.pk)
        for method in ("get", "post"):
            r = getattr(dpap, method)(path, {"remark": "x"})
            if r.status_code == 405:
                continue
            assert r.status_code == 404, (method, path, r.status_code)
            checked += 1
    assert checked >= 4  # detail, confirm, reject, withdraw
    b_pending.refresh_from_db()
    assert b_pending.status == "pending"  # untouched

    # The same item is visible to its own department.
    vtsd_director = make_user("d@vtsd.local", role="director", dept="vtsd")
    own = Client(HTTP_HOST="localhost")
    own.force_login(vtsd_director)
    assert own.get(reverse("qa:pending_detail", args=[b_pending.pk])).status_code == 200
