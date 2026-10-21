"""Asset label styles - the registry.

To add a style: (1) add a partial template under templates/assets/labels/ that
draws ONE label (see `_small.html`), (2) add one `LabelStyle(...)` entry to
`LABEL_STYLES` below, (3) add its CSS to static/css/asset-labels.css. Nothing
else changes: the picker, the preview, the page size and the remembered choice
all read this list.

`layout`:
    "single"  one label per printed page; the page IS the label (roll or
              tag printers). `width_mm` x `height_mm` is the page size.
    "sheet"   many labels on one A4 page, laid out as a grid. The grid numbers
              (`columns`, `rows`, margins, gaps) match a common 3 x 8 sticky
              label sheet (63.5 x 33.9 mm cells). `width_mm`/`height_mm` is the
              size of one cell.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_STYLE = "standard"
MAX_COPIES = 50
MAX_LABELS = 500       # hard cap on labels per print job
PX_PER_MM = 96 / 25.4  # CSS pixels per millimetre

A4_WIDTH_MM = 210
A4_HEIGHT_MM = 297


@dataclass(frozen=True)
class LabelStyle:
    key: str
    name: str
    description: str
    template: str            # partial that draws one label
    width_mm: float
    height_mm: float
    layout: str = "single"   # "single" | "sheet"
    # sheet only
    columns: int = 1
    rows: int = 1
    margin_top_mm: float = 0
    margin_left_mm: float = 0
    gap_x_mm: float = 0
    gap_y_mm: float = 0

    @property
    def is_sheet(self):
        return self.layout == "sheet"

    @property
    def per_page(self):
        return self.columns * self.rows if self.is_sheet else 1

    @property
    def size_label(self):
        if self.is_sheet:
            return f"A4 sheet, {self.columns} x {self.rows}"
        return f"{self.width_mm:g} x {self.height_mm:g} mm"

    @property
    def page_size_css(self):
        """Value for the CSS `size:` descriptor of @page."""
        if self.is_sheet:
            return "A4 portrait"
        return f"{self.width_mm:g}mm {self.height_mm:g}mm"

    # --- thumbnail geometry (the picker draws the real label, scaled down) ---
    THUMB_WIDTH_PX = 150

    @property
    def thumb_scale(self):
        return round(self.THUMB_WIDTH_PX / (self.width_mm * PX_PER_MM), 4)

    @property
    def thumb_height_px(self):
        return round(self.height_mm * PX_PER_MM * self.thumb_scale) + 2

    @property
    def thumb_native_width_px(self):
        return round(self.width_mm * PX_PER_MM)

    @property
    def thumb_native_height_px(self):
        return round(self.height_mm * PX_PER_MM)


LABEL_STYLES = [
    LabelStyle(
        key="small",
        name="Small tag",
        description="QR + asset tag. For small devices, cables and peripherals.",
        template="assets/labels/_small.html",
        width_mm=50, height_mm=25,
    ),
    LabelStyle(
        key="standard",
        name="Standard label",
        description="QR + tag, name, department and serial number.",
        template="assets/labels/_standard.html",
        width_mm=70, height_mm=40,
    ),
    LabelStyle(
        key="large",
        name="Large label",
        description="QR + full details, with the organisation name and logo.",
        template="assets/labels/_large.html",
        width_mm=100, height_mm=60,
    ),
    LabelStyle(
        key="barcode",
        name="Barcode strip",
        description="Linear barcode with the tag number large, for scanners.",
        template="assets/labels/_barcode.html",
        width_mm=80, height_mm=20,
    ),
    LabelStyle(
        key="sheet",
        name="A4 sheet of small labels",
        description="3 x 8 grid on A4. Print many labels at once.",
        template="assets/labels/_cell.html",
        width_mm=63.5, height_mm=33.9,
        layout="sheet",
        columns=3, rows=8,
        margin_top_mm=15.1, margin_left_mm=7.2,
        gap_x_mm=2.5, gap_y_mm=0,
    ),
]

STYLES_BY_KEY = {s.key: s for s in LABEL_STYLES}


def get_style(key):
    """The style for `key`, or the default for anything unknown."""
    return STYLES_BY_KEY.get((key or "").strip().lower()) or STYLES_BY_KEY[DEFAULT_STYLE]


def clamp_copies(raw, default=1):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(MAX_COPIES, value))
