"""Code 128 barcode as inline SVG. Pure Python, no dependency.

Used by the "barcode strip" asset label. Encodes with code set B, which covers
every printable ASCII character (space .. ~) - plenty for asset tags such as
ICT-COMPUT-00001. Anything outside that range is replaced by "?" so a stray
character in a tag can never break printing.

Each entry of PATTERNS is the 11-module bar/space pattern of Code 128 symbol
values 0..105 (1 = bar, 0 = space). Verified against the reference
implementation in python-barcode for the tags used by the tests.
"""
from __future__ import annotations

from django.utils.html import escape

PATTERNS = (
    "11011001100", "11001101100", "11001100110", "10010011000",
    "10010001100", "10001001100", "10011001000", "10011000100",
    "10001100100", "11001001000", "11001000100", "11000100100",
    "10110011100", "10011011100", "10011001110", "10111001100",
    "10011101100", "10011100110", "11001110010", "11001011100",
    "11001001110", "11011100100", "11001110100", "11101101110",
    "11101001100", "11100101100", "11100100110", "11101100100",
    "11100110100", "11100110010", "11011011000", "11011000110",
    "11000110110", "10100011000", "10001011000", "10001000110",
    "10110001000", "10001101000", "10001100010", "11010001000",
    "11000101000", "11000100010", "10110111000", "10110001110",
    "10001101110", "10111011000", "10111000110", "10001110110",
    "11101110110", "11010001110", "11000101110", "11011101000",
    "11011100010", "11011101110", "11101011000", "11101000110",
    "11100010110", "11101101000", "11101100010", "11100011010",
    "11101111010", "11001000010", "11110001010", "10100110000",
    "10100001100", "10010110000", "10010000110", "10000101100",
    "10000100110", "10110010000", "10110000100", "10011010000",
    "10011000010", "10000110100", "10000110010", "11000010010",
    "11001010000", "11110111010", "11000010100", "10001111010",
    "10100111100", "10010111100", "10010011110", "10111100100",
    "10011110100", "10011110010", "11110100100", "11110010100",
    "11110010010", "11011011110", "11011110110", "11110110110",
    "10101111000", "10100011110", "10001011110", "10111101000",
    "10111100010", "11110101000", "11110100010", "10111011110",
    "10111101110", "11101011110", "11110101110", "11010000100",
    "11010010000", "11010011100",
)
STOP = "11000111010"
TERMINATION = "11"
START_B = 104


def _symbols(text):
    clean = "".join(ch if 32 <= ord(ch) <= 126 else "?" for ch in (text or ""))
    values = [START_B] + [ord(ch) - 32 for ch in clean]
    checksum = START_B
    for position, value in enumerate(values[1:], start=1):
        checksum += position * value
    values.append(checksum % 103)
    return clean, values


def code128_modules(text):
    """The full bar/space string (quiet zones not included)."""
    _, values = _symbols(text)
    return "".join(PATTERNS[v] for v in values) + STOP + TERMINATION


def code128_svg(text, *, module=1.0, height=40.0, quiet=10, css_class="lbl-barcode-svg"):
    """Return an inline <svg> for `text`.

    The SVG uses a viewBox in module units and `preserveAspectRatio="none"`,
    so CSS can stretch it to any label size while the bars stay crisp and the
    proportions between bars stay exact (width scales uniformly).
    """
    modules = code128_modules(text)
    total = len(modules) + 2 * quiet
    parts = []
    i = 0
    while i < len(modules):
        if modules[i] == "1":
            j = i
            while j < len(modules) and modules[j] == "1":
                j += 1
            parts.append(f"M{quiet + i} 0h{j - i}v{height:g}h-{j - i}z")
            i = j
        else:
            i += 1
    path = "".join(parts)
    label = escape(_symbols(text)[0])
    return (
        f'<svg class="{css_class}" xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {total} {height:g}" preserveAspectRatio="none" '
        f'role="img" aria-label="Barcode {label}" shape-rendering="crispEdges">'
        f'<path d="{path}" fill="#000"/></svg>'
    )
