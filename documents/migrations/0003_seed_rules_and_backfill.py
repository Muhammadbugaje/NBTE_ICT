"""v2.1 Phase F (X22, X24): seed the document templates/rules and register
every payment slip and Payment Advice issued before v2.1. Their printed QR
codes carry a signed token; it is kept as ``legacy_token`` so they still
verify (Scan and the old /verify/<token>/ page)."""
import hashlib
import json
import secrets

from django.db import migrations

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _code(taken):
    while True:
        body = "".join(secrets.choice(ALPHABET) for _ in range(8))
        code = f"NBTE-{body[:4]}-{body[4:]}"
        if code not in taken:
            taken.add(code)
            return code


def _legacy(qr_payload):
    text = (qr_payload or "").strip().rstrip("/")
    return text.rsplit("/verify/", 1)[1].split("/")[0] if "/verify/" in text else ""


def _digest(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


PUBLIC = ["kind", "reference", "institution", "code", "amount", "signatories", "issued_at", "status"]


def forwards(apps, schema_editor):
    from documents import catalogue

    catalogue.ensure(apps)
    Template = apps.get_model("documents", "DocumentTemplate")
    Issued = apps.get_model("documents", "IssuedDocument")
    ContentType = apps.get_model("contenttypes", "ContentType")
    # _base_manager: plain in a migration, and unscoped when tests call this with the live app registry.
    try:
        Slip = apps.get_model("payments", "PaymentSlip")
        Advice = apps.get_model("payments", "PaymentAdvice")
        Obligation = apps.get_model("payments", "PaymentObligation")
    except LookupError:
        return
    ob_ct, _ = ContentType.objects.get_or_create(app_label="payments", model="paymentobligation")
    adv_ct, _ = ContentType.objects.get_or_create(app_label="payments", model="paymentadvice")
    taken = set(Issued._base_manager.values_list("short_code", flat=True))
    known = set(Issued._base_manager.exclude(legacy_token="").values_list("legacy_token", flat=True))
    slip_tpl = Template.objects.filter(code="payment_slip").order_by("-version").first()
    adv_tpl = Template.objects.filter(code="payment_advice").order_by("-version").first()

    def make(kind, tpl, ref, inst, amount, issued_at, legacy, file, ct, pk, extra):
        payload = {"kind": kind, "title": extra.pop("title"), "reference": ref, "institution": inst.name,
                   "code": inst.code, "amount": f"{amount:.2f}", "issued_at": issued_at.isoformat(),
                   "provisional": False, "signatories": extra.pop("signatories", []), "legacy": True, **extra}
        Issued._base_manager.create(
            kind=kind, template=tpl, reference=ref, short_code=_code(taken), token=secrets.token_urlsafe(18),
            legacy_token=legacy, title=payload["title"][:200], audience="institution", institution=inst,
            subject_ct=ct, subject_id=pk, amount=amount, signatories=[], payload=payload,
            digest=_digest(payload), public_fields=PUBLIC, status="valid", provisional=False,
            issued_at=issued_at, file=file.name if file else "", original_name=(file.name.rsplit("/", 1)[-1]
                                                                                if file else ""))

    for slip in Slip._base_manager.select_related("obligation__institution").order_by("pk"):
        legacy = _legacy(slip.qr_payload)
        ref = f"{slip.obligation.reference}-S{slip.version}"
        if not legacy or legacy in known or Issued._base_manager.filter(reference=ref).exists():
            continue
        ob = slip.obligation
        make("payment_slip", slip_tpl, ref, ob.institution, slip.amount, slip.generated_at, legacy, slip.file,
             ob_ct, ob.pk, {"title": f"Payment slip · {ob.description}", "payment_reference": ob.reference,
                            "fee": ob.description, "slip_version": slip.version})
    for adv in Advice._base_manager.select_related("obligation__institution").order_by("pk"):
        legacy = _legacy(adv.qr_payload)
        if not legacy or legacy in known or Issued._base_manager.filter(reference=adv.reference).exists():
            continue
        ob = adv.obligation
        make("payment_advice", adv_tpl, adv.reference, ob.institution, adv.amount, adv.generated_at, legacy,
             adv.file, adv_ct, adv.pk, {"title": f"Payment advice · {ob.description}",
                                        "payment_reference": ob.reference, "fee": ob.description})


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0002_issued_documents"),
        ("payments", "0001_payments"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
