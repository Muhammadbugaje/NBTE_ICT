"""v2.2: fill "Fees by institution category" from the settings NBTE already
had, so switching to the table never changes a bill silently. Categories with
no figure yet get an inactive row (the older formula still applies) until
Finance fills it in and switches it on. Idempotent:
existing rows are never overwritten."""
from decimal import Decimal, InvalidOperation

# SOP Annex A, Table A1: institutional fee and programme fee per programme.
REGISTRATION_FEE = {"polytechnic": "4000000", "monotechnic": "4000000",
                    "college-health": "2000000", "college-nursing": "2000000"}
PROGRAMME_FEE = {"polytechnic": "2000000", "monotechnic": "2000000"}
PROGRAMME_FEE_OTHER = "1000000"
HEALTH_TYPES = ("college-health", "college-nursing")


def _dec(value):
    try:
        raw = str(value if value is not None else "0").strip().strip('"') or "0"
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return Decimal("0")


def seed_type_fees(InstitutionType, InstitutionTypeFee, ConfigEntry=None, activate=True):
    def cfg(key, default="0"):
        if ConfigEntry is None:
            return default
        row = ConfigEntry.objects.filter(key=key, scope="global").values_list("value", flat=True).first()
        return row if row not in (None, "") else default

    made = 0
    for t in InstitutionType.objects.all():
        if InstitutionTypeFee.objects.filter(institution_type=t).exists():
            continue
        reg = _dec(cfg(f"qa.fee.institutional_processing_v3.base.{t.code}"))
        if reg <= 0:
            reg = _dec(REGISTRATION_FEE.get(t.code, "0"))
        proc = _dec(cfg(f"qa.fee.institutional_processing_v3.processing.{t.code}"))
        if proc <= 0:
            proc = _dec(cfg(f"qa.fee.institutional_processing.processing.{t.code}",
                            cfg("qa.fee.institutional_processing.processing")))
        if t.code in PROGRAMME_FEE:
            prog = _dec(PROGRAMME_FEE[t.code])
        elif t.code in HEALTH_TYPES:
            prog = _dec(PROGRAMME_FEE_OTHER)
        else:
            prog = Decimal("0")
        InstitutionTypeFee.objects.create(
            institution_type=t, registration_fee=reg, registration_processing_fee=proc,
            programme_fee=prog,
            programme_processing_fee=_dec(cfg("qa.fee.programme_processing.processing")),
            extra_stream_fee=_dec(cfg("qa.fee.programme.per_additional_stream")),
            qa_request_base_fee=_dec(cfg("qa.fee.qa_request.base")),
            qa_request_fee_per_programme=_dec(cfg("qa.fee.qa_request.per_programme")),
            qa_request_processing_fee=_dec(cfg("qa.fee.qa_request_processing.processing")),
            is_confirmed=False,
            # A category with no figure yet keeps using the older ownership
            # formula until Finance fills the row in and switches it on.
            is_active=activate and reg > 0,
            notes="Starting figures carried over from the earlier fee settings — confirm with Finance.")
        made += 1
    return made
