"""X49: LGA model, Settings -> States & LGAs, DB-backed register dropdown."""
import io

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client

pytestmark = pytest.mark.django_db
PW = "Geo-test-2026!"


@pytest.fixture
def geo(db):
    call_command("seed_nbte", "--only", "geo", stdout=io.StringIO())


def _admin():
    from accounts.models import User

    u = User(email="geo.admin@demo.nbte.local", first_name="Ict", last_name="Admin", user_type="staff",
             is_superuser=True, is_staff=True, must_change_password=False)
    u.set_password(PW)
    u.save()
    return u


def _portal_client():
    from django.conf import settings

    return Client(HTTP_HOST=settings.QA_PORTAL_HOST.split(":")[0])


def test_x49_public_lga_endpoint_anonymous(geo):
    from staff.models import StateOfOrigin

    kd = StateOfOrigin.objects.get(code="kd")
    r = _portal_client().get("/register/lgas/", {"state": kd.pk})
    assert r.status_code == 200
    html = r.content.decode()
    assert html.count("<option") >= 23 and "Zaria" in html and "Ikeja" not in html
    fct = StateOfOrigin.objects.get(code="fc")
    html = _portal_client().get("/register/lgas/", {"state": fct.pk}).content.decode()
    assert "Gwagwalada" in html and html.count("<option") == 7


def test_x49_validate_lga_from_model(geo):
    from institutions import services
    from staff.models import StateOfOrigin

    kd = StateOfOrigin.objects.get(code="kd")
    services.validate_lga(kd, "zaria")                             # case-insensitive
    with pytest.raises(ValidationError) as exc:
        services.validate_lga(kd, "Ikeja")
    assert "Lagos" in str(exc.value).title() or "LAGOS" in str(exc.value)
    lga = kd.lgas.get(name="Zaria")
    lga.is_active = False
    lga.save()
    with pytest.raises(ValidationError):
        services.validate_lga(kd, "Zaria")


def test_x49_cache_bust_on_edit(geo):
    from institutions import services
    from staff.models import LGA, StateOfOrigin

    kd = StateOfOrigin.objects.get(code="kd")
    assert "New Demo LGA" not in services.lgas_for(kd)             # cached now
    c = Client()
    c.force_login(_admin())
    r = c.post(f"/settings/geography/states/kd/lgas/save/", {"name": "New Demo LGA"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and "New Demo LGA" in r.content.decode()
    assert LGA.objects.filter(state=kd, name="New Demo LGA").exists()
    assert "New Demo LGA" in services.lgas_for(kd)                 # cache version bumped
    html = _portal_client().get("/register/lgas/", {"state": kd.pk}).content.decode()
    assert "New Demo LGA" in html
    # duplicate refused
    r = c.post(f"/settings/geography/states/kd/lgas/save/", {"name": "new demo lga"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 400


def test_x49_settings_page_permission_and_full_page(geo):
    from accounts.models import User

    plain = User.objects.create_user(email="plain.geo@demo.nbte.local", password=PW, must_change_password=False)
    c = Client()
    c.force_login(plain)
    assert c.get("/settings/geography/").status_code == 403
    c.force_login(_admin())
    html = c.get("/settings/geography/?state=kd").content.decode()
    assert "States &amp; LGAs" in html and "Zaria" in html and "Zonal offices" in html
    frag = c.get("/settings/geography/states/la/", HTTP_HX_REQUEST="true").content.decode()
    assert "Ikeja" in frag and "<html" not in frag
    assert c.get("/settings/geography/states/la/").status_code == 302   # pushed URL -> full page


def test_x49_delete_used_lga_deactivates(geo):
    from institutions.models import Institution
    from staff.models import Department, LGA, StateOfOrigin

    kd = StateOfOrigin.objects.get(code="kd")
    lga = LGA.objects.get(state=kd, name="Zaria")
    dept = Department.objects.get_or_create(code="dpap", defaults={"name": "DPAP"})[0]
    Institution.unscoped.create(code="GEO", name="Geo Demo Polytechnic", handling_department=dept, state=kd,
                                lga="Zaria", lga_ref=lga, status="approved")
    c = Client()
    c.force_login(_admin())
    c.post(f"/settings/geography/states/kd/lgas/{lga.pk}/delete/")
    lga.refresh_from_db()
    assert lga.is_active is False
    spare = LGA.objects.create(state=kd, name="Spare Demo LGA")
    c.post(f"/settings/geography/states/kd/lgas/{spare.pk}/delete/")
    assert not LGA.objects.filter(pk=spare.pk).exists()


def test_x49_backfill_lga_ref_report(db):
    from institutions.models import Institution
    from staff.geo import backfill_lga_refs
    from staff.models import Department, StateOfOrigin

    call_command("seed_nbte", "--only", "geo.states", stdout=io.StringIO())
    kd = StateOfOrigin.objects.get(code="kd")
    dept = Department.objects.get_or_create(code="dpap", defaults={"name": "DPAP"})[0]
    good = Institution.unscoped.create(code="BF1", name="Backfill One", handling_department=dept, state=kd,
                                       lga="  zaria ", status="approved")
    bad = Institution.unscoped.create(code="BF2", name="Backfill Two", handling_department=dept, state=kd,
                                      lga="Atlantis", status="approved")
    out = io.StringIO()
    call_command("seed_nbte", "--only", "geo.lgas", stdout=out)       # fills lga_ref, warns about the rest
    good.refresh_from_db()
    bad.refresh_from_db()
    assert good.lga_ref and good.lga_ref.name == "Zaria" and good.lga == "  zaria "
    assert bad.lga_ref is None
    report = backfill_lga_refs()
    assert report["already"] >= 1 and any(r[3] == "Atlantis" for r in report["unmatched"])


def test_dynamic_form_predicates():
    from django import forms

    from core.forms_dynamic import DynamicFormMixin, dependent_select, is_visible

    class F(DynamicFormMixin, forms.Form):
        is_public = forms.BooleanField(required=False)
        status = forms.ChoiceField(choices=[("existing", "Existing"), ("approved", "Approved")])
        cac_rc_number = forms.CharField(required=True)
        approval_letter = forms.CharField(required=True)
        state = forms.CharField(required=False)
        lga = forms.CharField(required=False)
        show_if = {"cac_rc_number": {"is_public": False}, "approval_letter": {"status": ["approved"]}}

    f = F(data={"is_public": "on", "status": "existing"})
    assert f.is_valid(), f.errors                                       # hidden fields are not required
    assert "cac_rc_number" not in f.cleaned_data
    f = F(data={"status": "approved"})
    assert not f.is_valid() and set(f.errors) == {"cac_rc_number", "approval_letter"}
    f = F(data={"status": "approved", "cac_rc_number": "RC123", "approval_letter": "x"})
    assert f.is_valid()
    assert 'data-show-if="{&quot;is_public&quot;: false}"' in str(F()["cac_rc_number"])
    dependent_select(f, "state", "lga", "/register/lgas/")
    assert f.fields["state"].widget.attrs["hx-get"] == "/register/lgas/"
    assert is_visible({"a": [1, 2]}, {"a": "2"}) and not is_visible({"a": True}, {"a": ""})
