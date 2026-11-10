"""Staff ID card print styles — the registry (B.11: E1-E4, design 12).

Modelled on assets/label_styles.py. To add a style: (1) a partial under
templates/staff/id_cards/ that draws ONE card face, (2) one StaffIdStyle entry
below, (3) its CSS in static/css/staff-id.css. The picker, the print page and
the @page size all read this list.

    cr80   CR80 card, 85.6 x 54 mm, one card per page (PVC card printers);
           front then back.
    a4x8   A4 portrait sheet, 2 x 4 CR80 cards (office printer, cut and
           laminate); fronts on one page, backs mirrored on the next.
    badge  Portrait badge 54 x 86 mm with a lanyard slot, one per page.

Print at 100% ("Actual size"): the page size comes from @page, so the cards
come out at their real size.
"""
from __future__ import annotations

from dataclasses import dataclass

import config

MAX_CARDS = 400
DEFAULT_STYLE = "cr80"


@dataclass(frozen=True)
class StaffIdStyle:
    key: str
    name: str
    description: str
    width_mm: float
    height_mm: float
    layout: str = "single"          # "single" | "sheet"
    orientation: str = "landscape"  # card orientation: landscape | portrait
    columns: int = 1
    rows: int = 1
    margin_top_mm: float = 0
    margin_left_mm: float = 0
    gap_mm: float = 0
    both_sides: bool = True

    @property
    def is_sheet(self):
        return self.layout == "sheet"

    @property
    def per_page(self):
        return self.columns * self.rows if self.is_sheet else 1

    @property
    def page_size_css(self):
        return "210mm 297mm" if self.is_sheet else f"{self.width_mm:g}mm {self.height_mm:g}mm"

    @property
    def size_label(self):
        if self.is_sheet:
            return f"210 × 297 mm, {self.columns} × {self.rows}"
        return f"{self.width_mm:g} × {self.height_mm:g} mm"


STYLES = [
    StaffIdStyle("cr80", "CR80 card", "For PVC card printers", 85.6, 54),
    StaffIdStyle("a4x8", "A4 sheet of 8", "Office printer, then cut and laminate", 85.6, 54,
                 layout="sheet", columns=2, rows=4, margin_top_mm=28.5, margin_left_mm=15.4, gap_mm=8),
    StaffIdStyle("badge", "Badge", "Portrait, lanyard slot", 54, 86, orientation="portrait"),
]
BY_KEY = {s.key: s for s in STYLES}


def get_style(key):
    return BY_KEY.get(key or "", BY_KEY[DEFAULT_STYLE])


def card_settings():
    """Text printed on every card (settings, not code). NBTE facts are placeholders."""
    return {
        "address": config.get("hr.staff_id.address", "Plot B Bida Road, Kaduna"),
        "return_text": config.get(
            "hr.staff_id.return_text",
            "If found, please return it to the Human Resources Department, NBTE, "
            "Plot B Bida Road, Kaduna. [PHONE TO CONFIRM]"),
        "valid_years": _int(config.get("hr.staff_id.valid_years", 3), 3),
        "signatory": config.get("hr.staff_id.signatory_label", "For: Executive Secretary"),
    }


def _int(v, d):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


def cards_for(staff_list, request=None):
    """One dict per staff member for the card partials."""
    from datetime import date

    from django.urls import reverse

    from accounts import staff_id

    cs = card_settings()
    today = date.today()
    valid_until = date(today.year + cs["valid_years"] - 1, 12, 31)
    cards = []
    for s in staff_list:
        photo = ""
        if s.photo:
            try:
                photo = s.photo.url
            except Exception:
                photo = ""
        qr = ""
        if s.user_id and s.user.is_active:
            url = reverse("staff_verify", args=[staff_id.make_print_token(s.user)])
            qr = staff_id.qr_svg(request.build_absolute_uri(url) if request else url)
        first, last = (s.first_name or "?")[:1], (s.last_name or "")[:1]
        cards.append({
            "pk": s.pk, "name": s.full_name, "initials": (first + last).upper(),
            "designation": s.designation.name if s.designation_id else "NBTE staff",
            "department": s.department.name if s.department_id else "",
            "grade": s.grade_level, "staff_number": s.employee_no, "photo_url": photo,
            "gender": {"M": "male", "F": "female"}.get(s.gender, "neutral"),
            "qr_svg": qr, "valid_until": valid_until, **cs,
        })
    return cards


def pages_for(style, cards):
    """Group cards into printed pages: [[card, …], …]."""
    n = style.per_page
    return [cards[i:i + n] for i in range(0, len(cards), n)]
