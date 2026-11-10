"""v2.1 Phase E (E.4: M1-M8) - internal / external / general memos.

Only the ES sends a memo to institutions; a director's external memo waits for
the ES; the institution filter builder counts and delivers to exactly the
matching institutions; institutions read, acknowledge and download the signed
letter in the portal Memos inbox. Lightweight organisation built per test.
"""
import io

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import Client

from accounts.models import Role, User
from messaging import memos as memo_service
from messaging import routing
from messaging.memo_audience import (describe_institution_filter, institutions_matching, parse_institution_filter,
                                     staff_matching)
from messaging.models import Memo, MemoInstitutionRecipient

pytestmark = pytest.mark.django_db
PW = "x-pass-12345"


@pytest.fixture
def world():
    from institutions.models import (Institution, InstitutionOwnership, InstitutionType, InstitutionUser,
                                     Programme)
    from staff.models import Department

    call_command("seed_roles", "--sync", verbosity=0, stdout=io.StringIO())
    Role.objects.get_or_create(code="executive-secretary", defaults={"name": "Executive Secretary", "rank": 999})
    roles = {r.code: r for r in Role.objects.all()}
    dpap = Department.objects.get_or_create(code="t-dpap", defaults={"name": "Polytechnic Programmes"})[0]
    hr = Department.objects.get_or_create(code="t-hr", defaults={"name": "Human Resources"})[0]

    def mk(email, role=None, dept=None, **kw):
        u = User.objects.create_user(email, PW, first_name=email.split("@")[0].title(), last_name="T",
                                     role=roles.get(role) if role else None, department=dept, **kw)
        u.must_change_password = False
        u.save()
        return User.objects.get(pk=u.pk)

    types = {c: InstitutionType.objects.get_or_create(code=c, defaults={"name": n})[0]
             for c, n in (("polytechnic", "Polytechnic"), ("college-nursing", "College of Nursing"))}
    fed = InstitutionOwnership.objects.get_or_create(code="federal", defaults={"name": "Federal"})[0]

    def inst(code, typ, n_progs, *, accredited=0, status="approved"):
        i = Institution.unscoped.create(code=code, name=f"{code} Institute", handling_department=dpap,
                                        status=status, institution_type=types[typ], ownership=fed)
        for k in range(n_progs):
            Programme.unscoped.create(institution=i, name=f"Prog {k}",
                                      status="accredited" if k < accredited else "proposed")
        return i

    w = {
        "es": mk("es@t.org", "executive-secretary"),
        "dir": mk("dir@t.org", "director", dpap),
        "off": mk("off@t.org", "officer", dpap),
        "hr_off": mk("hroff@t.org", "officer", hr),
        "admin": mk("admin@t.org", None, None, is_superuser=True, is_staff=True),
        "p_big": inst("PBIG", "polytechnic", 7, accredited=2),
        "p_six": inst("PSIX", "polytechnic", 6),
        "p_small": inst("PSML", "polytechnic", 5),
        "n_big": inst("NBIG", "college-nursing", 9, accredited=1),
        "dpap": dpap, "hr": hr,
    }
    w["p_gone"] = inst("PGON", "polytechnic", 8, status="delisted")
    # dropped programmes do not count
    for k in range(3):
        Programme.unscoped.create(institution=w["p_small"], name=f"Old {k}", status="dropped")
    w["inst_user"] = mk("head@pbig.org", user_type="institution", institution=w["p_big"])
    InstitutionUser.unscoped.create(institution=w["p_big"], user=w["inst_user"])
    w["other_inst_user"] = mk("head@psix.org", user_type="institution", institution=w["p_six"])
    InstitutionUser.unscoped.create(institution=w["p_six"], user=w["other_inst_user"])
    return w


POLY_GT5 = {"types": ["polytechnic"], "min_programmes": 5}


def external(sender, **kw):
    defaults = dict(sender=sender, subject="Accreditation calendar", body="Dear Head of Institution,\n\nPlease note.",
                    visibility=Memo.NO_STAFF, kind=Memo.EXTERNAL, institution_scope=Memo.INST_FILTER,
                    institution_filter=POLY_GT5)
    defaults.update(kw)
    return memo_service.create_memo(**defaults)


# --------------------------------------------------------------------------- M5
def test_m5_filter_builder_counts(world):
    qs = institutions_matching(POLY_GT5)
    assert set(qs.values_list("code", flat=True)) == {"PBIG", "PSIX"}   # PSML has 5 current; PGON delisted
    assert describe_institution_filter(POLY_GT5) == "Polytechnics with more than 5 programmes"
    assert set(institutions_matching({"types": ["college-nursing"]}).values_list("code", flat=True)) == {"NBIG"}
    acc = institutions_matching({"accreditation": "accredited"})
    assert set(acc.values_list("code", flat=True)) == {"PBIG", "NBIG"}
    none = institutions_matching({"types": ["polytechnic"], "accreditation": "none"})
    assert set(none.values_list("code", flat=True)) == {"PSIX", "PSML"}
    # the form parser understands the builder's if_* fields
    from django.http import QueryDict
    qd = QueryDict("if_types=polytechnic&if_min_programmes=5&if_new_only=1")
    assert parse_institution_filter(qd) == {"types": ["polytechnic"], "min_programmes": 5, "new_only": True}


def test_m5_live_count_endpoint(world):
    c = Client()
    c.force_login(world["dir"])
    r = c.get("/messages/memos/institution-count/", {"institution_scope": "filter", "if_types": "polytechnic",
                                                    "if_min_programmes": "5"})
    assert r.status_code == 200
    html = r.content.decode()
    assert 'data-total="2"' in html and "Polytechnics with more than 5 programmes" in html
    c.force_login(world["off"])          # officers cannot write to institutions
    assert c.get("/messages/memos/institution-count/").status_code == 403


# --------------------------------------------------------------------------- M2 / M7
def test_m2_only_es_sends_external(world):
    assert memo_service.can_send_external_memo(world["es"])
    for who in ("dir", "off", "admin"):
        assert not memo_service.can_send_external_memo(world[who])
    # an officer cannot even pick the external type
    assert memo_service.allowed_memo_kinds(world["off"]) == [Memo.INTERNAL]
    with pytest.raises(PermissionDenied):
        external(world["off"])
    # the ES's own external memo goes straight out, "From the Executive Secretary"
    memo = external(world["es"])
    memo_service.publish_memo(memo, world["es"])
    memo.refresh_from_db()
    assert memo.status == Memo.PUBLISHED and memo.signed_as == "from"
    assert memo.memo_recipients.count() == 0     # external = institutions only
    # nothing reaches institutions without the ES, even when called directly
    m2 = external(world["admin"])
    with pytest.raises(PermissionDenied):
        routing.publish_memo(m2, actor=world["admin"])


# --------------------------------------------------------------------------- M3
def test_m3_director_draft_needs_es(world):
    memo = external(world["dir"])
    memo_service.publish_memo(memo, world["dir"])
    memo.refresh_from_db()
    assert memo.status == Memo.AWAITING
    assert routing.actor_for_memo(memo) == world["es"]
    assert MemoInstitutionRecipient.objects.filter(memo=memo).count() == 0
    # an administrator cannot approve for the ES
    assert not routing.can_act_on_memo(memo, world["admin"])
    with pytest.raises(PermissionDenied):
        routing.approve_step(memo, world["admin"], "ok")
    # the ES previews the watermarked letter before approving
    assert memo_service.can_download_document(memo, world["es"])
    routing.approve_step(memo, world["es"], "Approved")
    memo.refresh_from_db()
    assert memo.status == Memo.PUBLISHED
    assert memo.es_signatory == world["es"] and memo.signed_as == "approved"
    assert set(memo.institution_recipients.values_list("institution__code", flat=True)) == {"PBIG", "PSIX"}


def test_m3_general_memo_reaches_staff_and_institutions(world):
    memo = memo_service.create_memo(sender=world["dir"], subject="Public holiday", body="Offices close on Monday.",
                                    visibility=Memo.ALL_STAFF, kind=Memo.GENERAL, institution_scope=Memo.INST_ALL)
    memo_service.publish_memo(memo, world["dir"])
    routing.approve_step(memo, world["es"], "")
    memo.refresh_from_db()
    assert memo.status == Memo.PUBLISHED
    assert memo.memo_recipients.filter(user=world["off"]).exists()
    assert memo.institution_recipients.count() == 4                 # every institution but the delisted one


# --------------------------------------------------------------------------- M4
def test_m4_portal_delivery(world):
    from notifications.models import Notification

    memo = external(world["es"], requires_acknowledgment=True)
    memo_service.publish_memo(memo, world["es"])
    assert Notification.objects.filter(recipient=world["inst_user"]).exists()
    host = settings.QA_PORTAL_HOST.split(":")[0]
    c = Client(HTTP_HOST=host)
    c.force_login(world["inst_user"])
    r = c.get("/memos/")
    assert r.status_code == 200 and memo.subject in r.content.decode()
    r = c.get(f"/memos/{memo.pk}/")
    assert r.status_code == 200 and "Accreditation calendar".upper() in r.content.decode()
    receipt = MemoInstitutionRecipient.objects.get(memo=memo, institution=world["p_big"])
    assert receipt.read_at is not None and receipt.read_by == world["inst_user"]
    r = c.get(f"/memos/{memo.pk}/letter.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert c.post(f"/memos/{memo.pk}/acknowledge/").status_code == 302
    receipt.refresh_from_db()
    assert receipt.acknowledged_by == world["inst_user"]
    # a polytechnic outside the filter never sees it
    w = world
    MemoInstitutionRecipient.objects.filter(memo=memo, institution=w["p_six"]).delete()
    c2 = Client(HTTP_HOST=host)
    c2.force_login(w["other_inst_user"])
    assert c2.get(f"/memos/{memo.pk}/").status_code == 404


def test_m4_selected_institutions_validation(world):
    with pytest.raises(ValidationError):
        external(world["es"], institution_scope=Memo.INST_SELECTED, institution_filter={})
    memo = external(world["es"], institution_scope=Memo.INST_SELECTED, target_institutions=[world["n_big"]])
    memo_service.publish_memo(memo, world["es"])
    assert list(memo.institution_recipients.values_list("institution__code", flat=True)) == ["NBIG"]
    with pytest.raises(ValidationError):
        external(world["es"], institution_filter={"types": ["college-nursing"], "min_programmes": 50})


# --------------------------------------------------------------------------- M6
def test_m6_staff_filter(world):
    flt = {"departments": [world["hr"].pk]}
    assert list(staff_matching(flt)) == [world["hr_off"]]
    assert not staff_matching({}).exists()
    memo = memo_service.create_memo(sender=world["es"], subject="HR circular", body="For HR staff.",
                                    visibility=Memo.STAFF_FILTER, staff_filter=flt)
    memo_service.publish_memo(memo, world["es"])
    memo.refresh_from_db()
    assert list(memo.memo_recipients.values_list("user__email", flat=True)) == ["hroff@t.org"]
    with pytest.raises(PermissionDenied):            # staff filters are a broadcast
        memo_service.create_memo(sender=world["off"], subject="x" * 5, body="y" * 5,
                                 visibility=Memo.STAFF_FILTER, staff_filter=flt)


# --------------------------------------------------------------------------- M8
def test_m8_external_letterhead_pdf_and_word(world):
    memo = external(world["dir"])
    memo_service.publish_memo(memo, world["dir"])
    routing.approve_step(memo, world["es"], "")
    memo.refresh_from_db()
    from messaging.memo_documents import render_memo_docx, render_memo_pdf, verification_for

    pdf = render_memo_pdf(memo, institution=world["p_big"])
    assert pdf[:4] == b"%PDF"
    text = _pdf_text(pdf)
    assert "NATIONAL BOARD FOR TECHNICAL EDUCATION" in text
    assert "Approved by the Executive Secretary" in text
    assert "PBIG Institute" in text
    # v2.1 Phase F (X22): the registry gives each institution copy its own QR + short code
    v = verification_for(memo, world["p_big"])
    assert v["url"].endswith(f"/v/{v['document'].token}/") and v["code"].startswith("NBTE-")
    assert verification_for(memo, world["p_big"])["code"] == v["code"]   # once per memo + institution
    assert v["code"] in _pdf_text(render_memo_pdf(memo, institution=world["p_big"]))
    docx = render_memo_docx(memo)
    assert docx[:2] == b"PK"
    # internal memos keep the internal letterhead
    internal = memo_service.create_memo(sender=world["dir"], subject="Internal", body="Body text",
                                        visibility=Memo.SPECIFIC_USER, target_user=world["off"])
    assert "INTERNAL MEMORANDUM" in _pdf_text(render_memo_pdf(internal))


def _pdf_text(pdf):
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        import fitz
        return "".join(p.get_text() for p in fitz.open(stream=pdf, filetype="pdf"))
    import io as _io
    return "".join(p.extract_text() or "" for p in PdfReader(_io.BytesIO(pdf)).pages)
