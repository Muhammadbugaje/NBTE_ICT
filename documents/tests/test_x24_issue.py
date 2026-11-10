"""X23 enforcement + X24: every approval rule issues a registered, signed
document with a working QR; signatures are the ones in force when issued."""
import pytest
from django.core.exceptions import ValidationError

from documents import catalogue
from documents import issue as issuing
from documents import signature as SIG
from documents import verify
from documents.models import DocumentRule, IssuedDocument

from .conftest import pdf_images, pdf_text

pytestmark = pytest.mark.django_db

RULE_EVENTS = [row[0] for row in catalogue.RULES]


def test_seeded_rules_and_templates_exist():
    assert set(DocumentRule.objects.values_list("event_code", flat=True)) >= set(RULE_EVENTS)
    for rule in DocumentRule.objects.filter(event_code__in=RULE_EVENTS):
        assert rule.template is not None, rule.event_code
    assert catalogue.ensure() == (0, 0)  # idempotent


@pytest.mark.parametrize("event_code", RULE_EVENTS)
def test_x24_rules_issue_documents(light, event_code):
    """Every seeded rule produces an IssuedDocument with a working QR."""
    rule = DocumentRule.objects.get(event_code=event_code)
    doc = issuing.approval_recorded(event_code, light.inst, actor=light.director, institution=light.inst,
                                    context={"programme": "ND Computer Science", "application_reference": "APP-1",
                                             "programmes": "• ND Computer Science", "fee": "Test fee",
                                             "subject": "Test memo", "department": "Polytechnic Programmes"})
    assert isinstance(doc, IssuedDocument)
    assert doc.event_code == event_code and doc.institution_id == light.inst.pk
    assert len(doc.token) >= 24 and doc.short_code.startswith("NBTE-") and len(doc.short_code) == 14
    assert doc.digest and len(doc.digest) == 64
    expected_provisional = rule.provisional if rule.provisional is not None else rule.template.provisional_default
    assert doc.provisional is expected_provisional
    with doc.file.open("rb") as fh:
        pdf = fh.read()
    assert pdf[:4] == b"%PDF"
    text = pdf_text(pdf)
    assert doc.short_code in text and "Scan to verify" in text
    assert ("PROVISIONAL" in text) is doc.provisional
    assert pdf_images(pdf) >= 1  # the QR (and a signature when the signer has one)
    # the QR's URL resolves through the registry; it carries no personal data
    url = issuing.verify_url(doc.token)
    assert url.endswith(f"/v/{doc.token}/") and "@" not in url
    found, _legacy, kind, _value = verify.lookup(url)
    assert kind == "token" and found.pk == doc.pk
    assert verify.lookup(doc.short_code.lower().replace("-", " "))[0].pk == doc.pk


def test_x23_signature_block_and_signature_in_pdf(light):
    """A Director without a signature cannot approve a document-generating
    step (friendly message); once drawn, the PDF carries it."""
    with pytest.raises(SIG.SignatureMissing) as exc:
        issuing.precheck("institution.approved", light.unsigned)
    assert "My profile" in str(exc.value)
    with pytest.raises(ValidationError):
        issuing.approval_recorded("institution.approved", light.inst, actor=light.unsigned, institution=light.inst)
    assert not IssuedDocument.unscoped.filter(event_code="institution.approved").exists()
    # an officer below Director rank is never blocked
    issuing.precheck("institution.approved", light.officer)
    # after adding a signature it works and the image is embedded
    from documents.testing import give_signature

    give_signature(light.unsigned)
    doc = issuing.approval_recorded("institution.approved", light.inst, actor=light.unsigned, institution=light.inst)
    with doc.file.open("rb") as fh:
        pdf = fh.read()
    assert pdf_images(pdf) >= 2  # QR + signature
    assert doc.signatories[0]["user_id"] == light.unsigned.pk and doc.signatories[0]["signature_id"]


def test_x23_signature_at_issue_time(light):
    """A signature replaced today does not change what yesterday's document records."""
    from documents.testing import give_signature

    doc = issuing.approval_recorded("institution.approved", light.inst, actor=light.director, institution=light.inst)
    first = doc.signatories[0]["signature_id"]
    assert first == light.director_sig.pk
    give_signature(light.director, ink="black")
    assert SIG.signature_at(light.director, doc.issued_at).pk == first
    doc.refresh_from_db()
    assert doc.signatories[0]["signature_id"] == first


def test_x24_two_step_confirm_asks_for_the_signature_first(light):
    """Approve Institution declares its document event; confirming it runs
    the signature precheck before the pending action changes state."""
    from institutions.handlers import ApproveInstitutionHandler

    assert ApproveInstitutionHandler.document_event == "institution.approved"
    import inspect

    from qa_core import actions

    assert "precheck(event, user)" in inspect.getsource(actions.confirm)
