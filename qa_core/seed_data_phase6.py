"""Phase 6 seed data: professional bodies and joint QA (additive)."""

# ---------------------------------------------------------------------------
# Permissions (additive; never removed)
# ---------------------------------------------------------------------------
ROLE_PERMISSIONS = {
    "curriculum-officer": ["institutions.manage_professional_bodies", "institutions.view_professionalbody"],
    "director": ["institutions.manage_professional_bodies"],
}
GROUP_PERMISSIONS = {
    "QA Settings Admin": ["institutions.manage_professional_bodies"],
}

# Demo professional bodies (seed_startup; idempotent by name).
DEMO_BODIES = [
    {"name": "Pharmacy Council of Nigeria", "acronym": "PCN", "contact_person": "The Registrar",
     "email": "registrar@pcn.example.org", "phone": "", "address": "Plot 7/9 Industrial Layout, Idu, Abuja",
     "programmes": [("Pharmacy Technician", "nd")]},
    {"name": "Council for the Regulation of Engineering in Nigeria", "acronym": "COREN",
     "contact_person": "The Registrar", "email": "registrar@coren.example.org", "phone": "",
     "address": "Plot 1, COREN Close, Abuja",
     "programmes": [("Electrical/Electronic Engineering Technology", None)]},
]
