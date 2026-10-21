"""Money helpers: Decimal parsing, ₦ formatting and amounts in words
(naira and kobo), as printed on slips and advices (Master Plan §10.2)."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
        "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
SCALES = [(10 ** 12, "trillion"), (10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")]
CENT = Decimal("0.01")


def to_money(value):
    """Decimal rounded to kobo, or raise ValueError."""
    if isinstance(value, Decimal):
        d = value
    else:
        text = str(value if value is not None else "").replace(",", "").replace("₦", "").replace("NGN", "").strip()
        if not text:
            raise ValueError("Enter an amount.")
        try:
            d = Decimal(text)
        except (InvalidOperation, ValueError):
            raise ValueError(f"'{value}' is not an amount.") from None
    if not d.is_finite():
        raise ValueError("Enter a real amount.")
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def naira(value):
    """₦2,000,000.00"""
    d = to_money(value)
    sign = "-" if d < 0 else ""
    return f"{sign}₦{abs(d):,.2f}"


def _under_thousand(n):
    words = []
    hundreds, rest = divmod(n, 100)
    if hundreds:
        words.append(f"{ONES[hundreds]} hundred")
    if rest:
        if hundreds:
            words.append("and")
        if rest < 20:
            words.append(ONES[rest])
        else:
            tens, ones = divmod(rest, 10)
            words.append(TENS[tens] + (f"-{ONES[ones]}" if ones else ""))
    return " ".join(words)


def integer_words(n):
    n = int(n)
    if n == 0:
        return "zero"
    if n < 0:
        return "minus " + integer_words(-n)
    parts = []
    for size, name in SCALES:
        if n >= size:
            count, n = divmod(n, size)
            parts.append(f"{integer_words(count)} {name}")
    if n:
        if parts and n < 100:
            parts.append("and")
        parts.append(_under_thousand(n))
    return " ".join(parts)


def amount_in_words(value):
    """'Two million naira only' / 'One thousand and five naira, fifty kobo only'."""
    d = to_money(value)
    whole = int(abs(d))
    kobo = int(((abs(d) - whole) * 100).to_integral_value(rounding=ROUND_HALF_UP))
    text = f"{integer_words(whole)} naira"
    if kobo:
        text += f", {integer_words(kobo)} kobo"
    text += " only"
    if d < 0:
        text = "minus " + text
    return text[0].upper() + text[1:]
