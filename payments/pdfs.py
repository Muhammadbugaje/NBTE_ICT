"""Payment slip (Appendix C) and Payment Advice PDFs (Master Plan §10.2).

ReportLab and qrcode are existing dependencies. The naira sign needs a
Unicode font, so DejaVu Sans (free licence, bundled in ``payments/fonts``)
is registered once; everything else is drawn on a plain canvas so the
layout is exact and fast.

Slip contents (§10.2 step 2): institution name and code; application / QA
request reference; the payment reference (large, plus a Code 128 barcode of
the same value); fee description; amount in figures and in words; bank name,
account name and account number; the Remita RRR when Remita is enabled; issue
and due dates; a QR code with a signed verification URL; the NBTE logo.
"""
from __future__ import annotations

import io
from pathlib import Path

from django.conf import settings
from django.utils import timezone

GREEN = "#1F6B3E"
GREEN_DARK = "#134326"
GREEN_SOFT = "#F0F7F2"
GREEN_LINE = "#B3D7BD"
INK = "#0F172A"
MUTED = "#5B6577"
LINE = "#D9DCE0"
AMBER_SOFT = "#FFF7E6"
AMBER_LINE = "#F2C66D"
AMBER_INK = "#7A4B00"

FONT_DIR = Path(__file__).resolve().parent / "fonts"
_FONTS = {}


def _fonts():
    if _FONTS:
        return _FONTS
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    try:
        pdfmetrics.registerFont(TTFont("QASans", str(FONT_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("QASans-Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFont(TTFont("QAMono-Bold", str(FONT_DIR / "DejaVuSansMono-Bold.ttf")))
        _FONTS.update(regular="QASans", bold="QASans-Bold", mono="QAMono-Bold", naira="₦")
    except Exception:  # fonts missing: fall back to the base-14 fonts and "NGN "
        _FONTS.update(regular="Helvetica", bold="Helvetica-Bold", mono="Courier-Bold", naira="NGN ")
    return _FONTS


def _money(value):
    f = _fonts()
    return f"{f['naira']}{value:,.2f}"


def _logo():
    from config.branding import pdf_logo_path  # v2.1 A5: formal brand slot

    path = pdf_logo_path("formal")
    return str(path) if path else None


def _qr_image(payload):
    import qrcode
    from reportlab.lib.utils import ImageReader

    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


def _wrap(text, font, size, width):
    from reportlab.pdfbase.pdfmetrics import stringWidth

    words, lines, line = (text or "").split(), [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if stringWidth(candidate, font, size) <= width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines or [""]


class _Doc:
    """Tiny canvas helper with the slip's typographic scale."""

    def __init__(self, title, subject):
        from reportlab.lib.colors import HexColor
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        self.HexColor = HexColor
        self.buf = io.BytesIO()
        self.w, self.h = A4
        self.c = canvas.Canvas(self.buf, pagesize=A4, pageCompression=1)
        self.c.setTitle(title)
        self.c.setAuthor("National Board for Technical Education")
        self.c.setSubject(subject)
        self.c.setCreator("NBTE Quality Assurance System")
        self.f = _fonts()
        self.margin = 42

    def color(self, hex_):
        return self.HexColor(hex_)

    def text(self, x, y, value, *, size=10, bold=False, mono=False, color=INK, align="left"):
        c = self.c
        font = self.f["mono"] if mono else (self.f["bold"] if bold else self.f["regular"])
        c.setFont(font, size)
        c.setFillColor(self.color(color))
        if align == "right":
            c.drawRightString(x, y, value)
        elif align == "center":
            c.drawCentredString(x, y, value)
        else:
            c.drawString(x, y, value)
        return font

    def rect(self, x, y, w, h, *, fill=None, stroke=None, radius=6, width=0.8):
        c = self.c
        if fill:
            c.setFillColor(self.color(fill))
        if stroke:
            c.setStrokeColor(self.color(stroke))
            c.setLineWidth(width)
        c.roundRect(x, y, w, h, radius, stroke=1 if stroke else 0, fill=1 if fill else 0)

    def header(self, title, kicker):
        c, m = self.c, self.margin
        top = self.h - m
        logo = _logo()
        if logo:
            c.drawImage(logo, m, top - 46, width=46, height=46, mask="auto", preserveAspectRatio=True)
        self.text(m + 58, top - 16, "NATIONAL BOARD FOR TECHNICAL EDUCATION", size=11, bold=True, color=GREEN_DARK)
        self.text(m + 58, top - 32, "Quality Assurance", size=10, color=MUTED)
        self.text(self.w - m, top - 18, title, size=14, bold=True, color=GREEN, align="right")
        self.text(self.w - m, top - 34, kicker, size=8.5, color=MUTED, align="right")
        c.setStrokeColor(self.color(GREEN))
        c.setLineWidth(2)
        c.line(m, top - 58, self.w - m, top - 58)
        return top - 74

    def watermark(self):
        logo = _logo()
        if not logo:
            return
        c = self.c
        c.saveState()
        try:
            c.setFillAlpha(0.05)
            c.setStrokeAlpha(0.05)
        except AttributeError:
            pass
        size = 300
        c.drawImage(logo, (self.w - size) / 2, (self.h - size) / 2 - 40, width=size, height=size, mask="auto",
                    preserveAspectRatio=True)
        c.restoreState()

    def row(self, x, y, label, value, *, label_w=118, width=None, size=10, bold=False, mono=False):
        self.text(x, y, label, size=8.5, color=MUTED)
        width = width or (self.w - self.margin - x - label_w)
        font = self.f["mono"] if mono else (self.f["bold"] if bold else self.f["regular"])
        lines = _wrap(str(value or "—"), font, size, width)
        for i, line in enumerate(lines):
            self.text(x + label_w, y - i * (size + 3), line, size=size, bold=bold, mono=mono)
        return y - max(1, len(lines)) * (size + 3) - 7

    def footer(self, reference):
        m = self.margin
        stamp = timezone.localtime().strftime("%d %b %Y, %H:%M")
        self.c.setStrokeColor(self.color(LINE))
        self.c.setLineWidth(0.6)
        self.c.line(m, m + 14, self.w - m, m + 14)
        self.text(m, m, f"Generated {stamp} · {reference}", size=7.5, color=MUTED)
        self.text(self.w - m, m, "NBTE Quality Assurance Portal", size=7.5, color=MUTED, align="right")

    def finish(self):
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()


def _breakdown(doc, x, y, width, lines, total, *, total_label="Total"):
    """F.4 (P1): the bill lines (institution fee + processing fee …) and the
    total, as on the portal bill. Returns the new y."""
    for line in lines:
        doc.text(x, y, str(line["label"]), size=10)
        doc.text(x + width, y, _money(line["amount"]), size=10, bold=True, align="right")
        if line.get("description"):
            doc.text(x, y - 12, str(line["description"])[:90], size=8, color=MUTED)
            y -= 12
        y -= 10
        doc.c.setStrokeColor(doc.color(LINE))
        doc.c.setLineWidth(0.5)
        doc.c.setDash(2, 2)
        doc.c.line(x, y, x + width, y)
        doc.c.setDash()
        y -= 14
    doc.c.setStrokeColor(doc.color(INK))
    doc.c.setLineWidth(1.4)
    doc.c.line(x, y + 8, x + width, y + 8)
    doc.text(x, y - 6, total_label, size=11, bold=True)
    doc.text(x + width, y - 6, _money(total), size=11, bold=True, align="right")
    return y - 26


def _stamp(doc, cx, cy, text="PAID"):
    c = doc.c
    c.saveState()
    c.translate(cx, cy)
    c.rotate(10)
    c.setStrokeColor(doc.color(GREEN))
    c.setFillColor(doc.color(GREEN))
    c.setLineWidth(2.4)
    c.roundRect(-38, -15, 76, 30, 7, stroke=1, fill=0)
    c.setFont(doc.f["bold"], 15)
    c.drawCentredString(0, -5, text)
    c.restoreState()


def _barcode(doc, value, x, y, width):
    from reportlab.graphics.barcode import code128

    bar_width = 1.0
    bc = code128.Code128(value, barHeight=34, barWidth=bar_width, humanReadable=False, quiet=False)
    while bc.width > width and bar_width > 0.45:
        bar_width -= 0.05
        bc = code128.Code128(value, barHeight=34, barWidth=bar_width, humanReadable=False, quiet=False)
    bc.drawOn(doc.c, x, y)
    return bc


# ---------------------------------------------------------------------------
# Payment slip (Appendix C)
# ---------------------------------------------------------------------------
def render_slip(slip):
    ob = slip.obligation
    inst = ob.institution
    doc = _Doc(f"Payment slip {ob.reference}", "NBTE Quality Assurance payment slip")
    doc.watermark()
    m = doc.margin
    y = doc.header("PAYMENT SLIP", f"Version {slip.version} · issued {timezone.localtime(slip.generated_at):%d %b %Y}")
    full_w = doc.w - 2 * m

    # Who and what
    y = doc.row(m, y, "Institution", f"{inst.name} ({inst.code})", bold=True, size=11)
    if ob.source_reference:
        y = doc.row(m, y, "Application / request", ob.source_reference, mono=True)
    y = doc.row(m, y, "Fee", ob.description)
    y -= 4

    # Payment reference card with barcode, and the QR code beside it
    qr_size = 118
    card_h = 108
    card_w = full_w - qr_size - 16
    doc.rect(m, y - card_h, card_w, card_h, fill=GREEN_SOFT, stroke=GREEN_LINE)
    doc.text(m + 16, y - 22, "PAYMENT REFERENCE", size=8.5, bold=True, color=GREEN)
    doc.text(m + 16, y - 46, ob.reference, size=19, mono=True, color=INK)
    _barcode(doc, slip.barcode_value, m + 16, y - card_h + 14, card_w - 32)
    doc.c.drawImage(_qr_image(slip.qr_payload), doc.w - m - qr_size, y - qr_size + 6, width=qr_size, height=qr_size)
    if getattr(slip, "short_code", ""):  # v2.1 X26: the short code is typed on the Scan page
        doc.text(doc.w - m - qr_size / 2, y - qr_size - 6, "Scan to verify, or enter", size=7.5, color=MUTED,
                 align="center")
        doc.text(doc.w - m - qr_size / 2, y - qr_size - 17, slip.short_code, size=8, bold=True, align="center")
    else:
        doc.text(doc.w - m - qr_size / 2, y - qr_size - 6, "Scan to confirm this", size=7.5, color=MUTED,
                 align="center")
        doc.text(doc.w - m - qr_size / 2, y - qr_size - 16, "slip is genuine", size=7.5, color=MUTED, align="center")
    y -= card_h + 26

    # Amount
    amount_h = 62
    doc.rect(m, y - amount_h, full_w, amount_h, stroke=LINE)
    doc.text(m + 16, y - 20, "AMOUNT", size=8.5, bold=True, color=MUTED)
    doc.text(m + 16, y - 44, _money(slip.amount), size=22, bold=True, color=GREEN_DARK)
    words = _wrap(slip.amount_in_words, doc.f["regular"], 10, full_w / 2 - 20)
    wy = y - 22
    doc.text(m + full_w / 2, wy, "In words", size=8.5, color=MUTED)
    for i, line in enumerate(words[:3]):
        doc.text(m + full_w / 2, wy - 15 - i * 13, line, size=10, color=INK)
    y -= amount_h + 18
    lines = ob.breakdown
    if len(lines) > 1:
        y = _breakdown(doc, m, y, full_w, lines, slip.amount if slip.amount == ob.amount else ob.amount)

    # Pay to
    doc.text(m, y, "PAY TO", size=8.5, bold=True, color=GREEN)
    y -= 18
    bank = slip.bank_snapshot or {}
    y = doc.row(m, y, "Bank", bank.get("bank_name", ""))
    y = doc.row(m, y, "Account name", bank.get("account_name", ""))
    y = doc.row(m, y, "Account number", bank.get("account_number", ""), mono=True, size=12)
    if slip.remita_rrr:
        y = doc.row(m, y, "Remita RRR", slip.remita_rrr, mono=True, size=12)
    issued = timezone.localtime(slip.generated_at).strftime("%d %b %Y")
    due = timezone.localtime(ob.due_at).strftime("%d %b %Y")
    y = doc.row(m, y, "Issued / Due", f"{issued}  /  {due}")
    y = doc.row(m, y, "Slip valid until", timezone.localtime(slip.expires_at).strftime("%d %b %Y"))
    y -= 6

    # Instructions
    doc.text(m, y, "INSTRUCTIONS", size=8.5, bold=True, color=GREEN)
    y -= 16
    steps = [
        "Quote the PAYMENT REFERENCE in your narration or teller exactly as printed.",
        "After paying, sign in to the NBTE Quality Assurance Portal, open Payments and press "
        "“I have paid” to upload your teller or transfer receipt.",
        "Online payments (Remita, Paystack) are recorded automatically, but every payment still needs the "
        "Director of Finance’s confirmation before it counts.",
    ]
    for n, step in enumerate(steps, start=1):
        lines = _wrap(step, doc.f["regular"], 9.5, full_w - 18)
        doc.text(m, y, f"{n}.", size=9.5, bold=True, color=GREEN)
        for i, line in enumerate(lines):
            doc.text(m + 16, y - i * 12.5, line, size=9.5)
        y -= len(lines) * 12.5 + 5

    # Important notice
    notice_h = 46
    y -= 6
    doc.rect(m, y - notice_h, full_w, notice_h, fill=AMBER_SOFT, stroke=AMBER_LINE)
    doc.text(m + 14, y - 18, "Important", size=9.5, bold=True, color=AMBER_INK)
    doc.text(m + 14, y - 33, "This slip is not proof of payment. Every payment is confirmed by the Director of "
                             "Finance.", size=9, color=AMBER_INK)
    doc.footer(f"{ob.reference} · slip v{slip.version}")
    return doc.finish()


# ---------------------------------------------------------------------------
# Payment Advice (A4/R-26)
# ---------------------------------------------------------------------------
def render_advice(advice):
    """The official receipt (design 08b): crest, receipt number, who paid,
    what for, method, confirmation, the bill lines, PAID stamp and QR."""
    ob = advice.obligation
    inst = ob.institution
    item = advice.verification
    doc = _Doc(f"Official receipt {advice.reference}", "NBTE Quality Assurance official receipt")
    doc.watermark()
    m = doc.margin
    full_w = doc.w - 2 * m
    y = doc.header("OFFICIAL RECEIPT", f"{advice.reference} · {timezone.localtime(advice.generated_at):%d %b %Y}")

    banner_h = 54
    doc.rect(m, y - banner_h, full_w, banner_h, fill=GREEN_SOFT, stroke=GREEN_LINE)
    doc.text(m + 16, y - 22, "PAYMENT RECEIVED AND CONFIRMED", size=9, bold=True, color=GREEN)
    doc.text(m + 16, y - 41, _money(advice.amount), size=18, bold=True, color=GREEN_DARK)
    doc.text(doc.w - m - 16, y - 41, advice.reference, size=12, mono=True, align="right")
    y -= banner_h + 22

    qr_size = 104
    y0 = y
    right = doc.w - m - qr_size
    doc.c.drawImage(_qr_image(advice.qr_payload), right, y - qr_size + 8, width=qr_size, height=qr_size)
    _stamp(doc, right + qr_size / 2, y - qr_size - 26)
    kv_w = full_w - qr_size - 24
    y = doc.row(m, y, "Receipt no.", advice.reference, mono=True, width=kv_w - 118)
    y = doc.row(m, y, "Paid by", f"{inst.name} ({inst.code})", bold=True, width=kv_w - 118)
    y = doc.row(m, y, "For", " + ".join(str(line["label"]).lower() if i else str(line["label"])
                                       for i, line in enumerate(ob.breakdown)), width=kv_w - 118)
    y = doc.row(m, y, "Payment reference", ob.reference, mono=True, width=kv_w - 118)
    if ob.source_reference:
        y = doc.row(m, y, "Application / request", ob.source_reference, mono=True, width=kv_w - 118)
    method = ""
    if item is not None and item.attempt_id:
        method = f"{item.attempt.get_provider_display().split(' (')[0]}, ref {item.attempt.provider_reference}"
    elif item is not None:
        method = dict(item.SOURCE_CHOICES).get(item.source, item.source)
    y = doc.row(m, y, "Method", method or "—", width=kv_w - 118)
    if item is not None and item.final_decided_at:
        y = doc.row(m, y, "Confirmed", timezone.localtime(item.final_decided_at).strftime("%d %b %Y, %H:%M"),
                    width=kv_w - 118)
    if item is not None and item.final_approver_id:
        approver = item.final_approver.get_full_name() or item.final_approver.email
        y = doc.row(m, y, "Confirmed by", f"{approver}, Director of Finance", width=kv_w - 118)
    y = min(y, y0 - qr_size - 62) - 6

    lines = ob.breakdown
    if advice.amount == ob.amount:
        y = _breakdown(doc, m, y, full_w, lines, advice.amount, total_label="Total paid")
    else:
        y = _breakdown(doc, m, y, full_w, lines, ob.amount, total_label="Bill total")
        y = doc.row(m, y, "Received now", _money(advice.amount), bold=True)
    from .money import amount_in_words

    y = doc.row(m, y, "In words", amount_in_words(advice.amount))
    if ob.balance and ob.balance > 0:
        y = doc.row(m, y, "Balance outstanding", _money(ob.balance), bold=True)
    y -= 6
    for i, line in enumerate(_wrap("Scan the code to confirm on NBTE’s verification page that this receipt is "
                                   "genuine. It is issued automatically when the Director of Finance confirms a "
                                   "payment; keep it with your records.", doc.f["regular"], 9, full_w)):
        doc.text(m, y - i * 12, line, size=9, color=MUTED)
    doc.footer(advice.reference)
    return doc.finish()
