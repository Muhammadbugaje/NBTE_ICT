"""Seeded document templates and rules (v2.1 X22, X24; spec G5.d).

``ensure(apps=None)`` creates every template and rule that is missing and never
changes a row an administrator has edited (natural keys: template code +
version 1, rule event code). It runs from the data migration and from
``seed_nbte`` (documents.seed_sets), so it is idempotent.

Bodies use ``{placeholders}`` filled from the issuing context; a missing
placeholder prints as ``[PLACEHOLDER]`` so a gap is visible, never silent.
Bracketed words such as [CONDITIONS] are NBTE wording still to be confirmed.
"""

TEMPLATES = [
    # code, title, kind, audience, letterhead, signatory_rule, provisional_default, subject_line, body
    ("payment_slip", "Payment slip", "payment_slip", "institution", "receipt", "none", False,
     "Payment slip {reference}", ""),
    ("fee_receipt", "Official receipt", "receipt", "institution", "receipt", "actor", False,
     "Official receipt · {fee}",
     "NBTE confirms that the payment below has been received and confirmed by the Director of Finance. "
     "Keep this receipt with your records; anyone can confirm it is genuine by scanning the QR code."),
    ("payment_advice", "Payment advice", "payment_advice", "institution", "receipt", "actor", False,
     "Payment advice {reference}", ""),
    ("institution_approval_letter", "Registration approval letter", "letter", "institution", "external_letter",
     "actor", True,
     "Approval of registration: {institution}",
     "I am directed to inform you that the National Board for Technical Education has approved the "
     "registration of {institution} ({code}) on the Board's Quality Assurance Portal.\n\n"
     "Your institution is now handled by the {department}. You may submit quality-assurance requests "
     "for your programmes through the portal.\n\n"
     "This approval is subject to [CONDITIONS OF REGISTRATION] and to the Board's guidelines in force.\n\n"
     "Please accept the assurances of the Board's highest regards."),
    ("programme_clearance_letter", "Programme clearance letter", "letter", "institution", "external_letter",
     "actor", True,
     "Clearance of programmes: {application_reference}",
     "Following the review of your application {application_reference}, I am directed to inform you that the "
     "Board has cleared the following programme(s) of {institution} for processing:\n\n{programmes}\n\n"
     "The programme processing fee is now on the Payments page of the portal. Processing continues once "
     "the Director of Finance confirms the payment.\n\n"
     "Please accept the assurances of the Board's highest regards."),
    ("approval_to_commence", "Approval to commence", "letter", "institution", "external_letter", "es", True,
     "Approval to commence {programme}",
     "I am directed to inform you that the Board has approved the commencement of the {programme} programme "
     "at {institution}, with effect from {commencement}.\n\n"
     "The approval follows the resource inspection of [VISIT DATE] and is subject to the conditions in the "
     "attached schedule: [CONDITIONS OF APPROVAL]. Approved streams: {streams}.\n\n"
     "Please accept the assurances of the Executive Secretary's highest regards."),
    ("provisional_approval_pending_body", "Provisional approval (awaiting professional body)", "letter",
     "institution", "external_letter", "es", True,
     "Provisional approval: {programme}",
     "I am directed to inform you that the Board has considered the {programme} programme at {institution} and "
     "approved it provisionally, subject to the approval letter of the professional body that regulates it.\n\n"
     "The approval to commence will be issued as soon as that body's letter is received by the Board. Until "
     "then the programme may not admit students. Approved streams: {streams}; proposed commencement: "
     "{commencement}.\n\n"
     "Please accept the assurances of the Executive Secretary's highest regards."),
    ("external_memo", "External memo", "memo", "institution", "external_letter", "es", False,
     "{subject}", ""),
]

RULES = [
    # event_code, template_code, description, provisional (None = template default), send_to_institution
    ("payment.slip_generated", "payment_slip", "Every payment slip (invoice) is registered with a QR.", None, True),
    ("payment.confirmed", "fee_receipt",
     "When the Director of Finance confirms a payment: the official receipt (replaces the Payment Advice PDF).",
     None, True),
    ("institution.approved", "institution_approval_letter",
     "Registration approved (two-step Approve Institution): provisional letter, official copy to follow.", None, True),
    ("onboarding.programmes_cleared", "programme_clearance_letter",
     "All programmes of an onboarding application cleared.", None, True),
    ("qa_case.decision_approved", "approval_to_commence",
     "Stage 17: each approved decision gets a provisional approval-to-commence letter (placeholder wording).",
     None, True),
    ("qa_case.decision_pending_body", "provisional_approval_pending_body",
     "D-19: an approved programme still waiting for its professional body's letter gets a provisional letter; "
     "the approval to commence follows when the body uploads its letter.", None, True),
    ("memo.external_published", "external_memo",
     "External/general memos approved by the ES: one registry entry per institution copy.", False, True),
]


def ensure(apps=None):
    """Create missing templates and rules. Returns (templates_created, rules_created)."""
    if apps is None:
        from .models import DocumentRule, DocumentTemplate
    else:
        DocumentTemplate = apps.get_model("documents", "DocumentTemplate")
        DocumentRule = apps.get_model("documents", "DocumentRule")
    t_made = r_made = 0
    for (code, title, kind, audience, letterhead, signatory, provisional, subject_line, body) in TEMPLATES:
        if not DocumentTemplate.objects.filter(code=code).exists():
            DocumentTemplate.objects.create(code=code, version=1, title=title, kind=kind, audience=audience,
                                            letterhead=letterhead, signatory_rule=signatory,
                                            provisional_default=provisional, subject_line=subject_line, body=body)
            t_made += 1
    for event_code, template_code, description, provisional, send in RULES:
        if not DocumentRule.objects.filter(event_code=event_code).exists():
            DocumentRule.objects.create(event_code=event_code, template_code=template_code, description=description,
                                        provisional=provisional, send_to_institution=send)
            r_made += 1
    return t_made, r_made
