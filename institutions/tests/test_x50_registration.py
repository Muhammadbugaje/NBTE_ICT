"""v2.1 X50 (Part 2.4): the registration form asks only what NBTE needs, and
shows a field only when it is relevant; the server applies the same rules."""
import pytest
from django.test import Client


def _data(lookups, **over):
    from institutions import services

    data = {"name": "Zonkwa Technical College", "ownership": lookups.federal.pk, "category": lookups.poly_cat.pk,
            "state": lookups.kaduna.pk, "lga": services.lgas_for(lookups.kaduna)[0], "address_line1": "1 Road",
            "official_email": "info@ztc.edu.ng", "phone": "0803", "ce_title": "Dr", "ce_full_name": "Amina Yusuf",
            "ce_email": "ce@ztc.edu.ng", "ce_phone": "0803 1", "admin_first_name": "Ibrahim",
            "admin_last_name": "Musa", "admin_email": "ict@ztc.edu.ng", "admin_phone": "0803 2",
            "password1": "Very-long-pass-2026!", "password2": "Very-long-pass-2026!", "declaration": "on"}
    data.update(over)
    return {k: v for k, v in data.items() if v is not None}


def _form(data):
    from institutions.portal_views import RegistrationForm

    return RegistrationForm(data)


def test_x50_register_fields_removed(lookups):
    form = _form(None)
    for name in ("institution_type", "website", "head_name", "admin_job_title", "admin_department"):
        assert name not in form.fields, name
    assert form.fields["category"].label == "Institution type"


def test_x50_hidden_fields_not_required(lookups):
    form = _form(_data(lookups))  # public ownership, not yet operating: no RC / CAC, no year
    assert form.is_valid(), form.errors
    assert "cac_rc_number" not in form.cleaned_data and "year_established" not in form.cleaned_data
    assert form.cleaned_data["institution_type"] == lookups.poly_cat.institution_type
    # A value typed into a hidden field is dropped, not stored.
    form = _form(_data(lookups, cac_rc_number="RC1", year_established="1999"))
    assert form.is_valid(), form.errors
    assert "cac_rc_number" not in form.cleaned_data and "year_established" not in form.cleaned_data


def test_x50_relevant_fields_required(lookups):
    form = _form(_data(lookups, ownership=lookups.private.pk))
    assert not form.is_valid() and "cac_rc_number" in form.errors
    form = _form(_data(lookups, operating_status="existing"))
    assert not form.is_valid() and "year_established" in form.errors
    form = _form(_data(lookups, ownership=lookups.private.pk, cac_rc_number="RC77", operating_status="existing",
                       year_established="2001"))
    assert form.is_valid(), form.errors
    assert form.cleaned_data["cac_rc_number"] == "RC77" and form.cleaned_data["year_established"] == 2001


def test_x50_dynamic_required_fields_have_client_validation_metadata(lookups):
    form = _form(None)
    for name in ("cac_rc_number", "year_established"):
        assert form.fields[name].widget.attrs["data-dynamic-required"] == "true"


def test_x50_register_page_is_dynamic(lookups, portal_host):
    html = Client(HTTP_HOST=portal_host).get("/register/").content.decode()
    assert "js/forms-dynamic.js" in html
    assert html.count("data-show-if") >= 2
    for gone in ('name="website"', 'name="admin_job_title"', 'name="admin_department"', 'name="institution_type"',
                 'name="head_name"'):
        assert gone not in html, gone
    assert 'name="operating_status"' in html and 'name="cac_rc_number"' in html
    assert html.count('data-dynamic-required="true"') == 2


def test_x50_registration_still_works(lookups, portal_host, django_capture_on_commit_callbacks):
    from institutions.models import Institution, InstitutionUser

    with django_capture_on_commit_callbacks(execute=True):
        r = Client(HTTP_HOST=portal_host).post("/register/", _data(lookups))
    assert r.status_code == 302 and r["Location"].endswith("/setup/"), r.content[:2000]
    inst = Institution.unscoped.get(name="Zonkwa Technical College")
    assert inst.institution_type_id == lookups.poly_cat.institution_type_id
    assert inst.cac_rc_number == "" and inst.year_established is None and inst.website == ""
    ict = InstitutionUser.unscoped.get(institution=inst, account_kind="ict_head")
    assert ict.job_title == "Head of ICT" and ict.user.email == "ict@ztc.edu.ng"


@pytest.mark.parametrize("status,year,expected", [("existing", "2010", 2010), ("proposed", "2010", None)])
def test_x50_year_only_for_operating(lookups, portal_host, status, year, expected):
    from institutions.models import Institution

    r = Client(HTTP_HOST=portal_host).post("/register/", _data(
        lookups, name=f"Year {status} College", official_email=f"info@{status}.edu.ng", ce_email=f"ce@{status}.edu.ng",
        admin_email=f"ict@{status}.edu.ng", operating_status=status, year_established=year))
    assert r.status_code == 302, r.content[:2000]
    assert Institution.unscoped.get(name=f"Year {status} College").year_established == expected
