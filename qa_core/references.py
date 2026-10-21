"""Reference-number generator (Master Plan §5.1 ReferenceFormat/Counter, §10.5).

Patterns are editable settings rows; a changed pattern applies only to new
references (issued references are stored strings and never change).

Tokens:
    {YEAR}      four-digit year (also the counter period when reset is yearly)
    {INST}      institution code, e.g. KDP
    {DEPT}      department code, upper-cased, e.g. DPAP
    {SEQ:n}     the counter, zero-padded to n digits ({SEQ} = no padding)
    {CHK97}     two-digit ISO 7064 mod 97-10 check over every letter/digit before it
    {CHK11}     one-character mod-11 check (0-9 or X)
    {CHK}       the check named by the format's `check_digit`

Counters are incremented atomically (an UPDATE … SET last_value = last_value
+ 1 inside the issuing transaction, with `select_for_update()` where the
database supports it), so concurrent issuers never receive the same number.
Gaps from rolled-back transactions are acceptable and documented.
"""
from __future__ import annotations

import random
import re
import time

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone

TOKEN_RE = re.compile(r"\{(YEAR|INST|DEPT|SEQ(?::(\d{1,2}))?|CHK97|CHK11|CHK)\}")
UNKNOWN_TOKEN_RE = re.compile(r"\{[^}]*\}")


class ReferenceError_(ValueError):
    """Invalid pattern or missing context (named to avoid shadowing builtins)."""


ReferenceFormatError = ReferenceError_


# ---------------------------------------------------------------------------
# Check digits
# ---------------------------------------------------------------------------
def _alnum(text):
    return "".join(ch for ch in text.upper() if ch.isalnum())


def _to_digits(text):
    """Letters become two-digit numbers (A=10 … Z=35), as in IBANs."""
    out = []
    for ch in _alnum(text):
        out.append(ch if ch.isdigit() else str(ord(ch) - 55))
    return "".join(out)


def mod97_check(body):
    """ISO 7064 mod 97-10 check digits (two characters) for `body`."""
    digits = _to_digits(body)
    if not digits:
        raise ReferenceError_("Nothing to compute a check digit over.")
    return f"{98 - (int(digits + '00') % 97):02d}"


def mod97_valid(body, check):
    try:
        return int(_to_digits(body) + f"{int(check):02d}") % 97 == 1
    except (ValueError, TypeError):
        return False


def mod11_check(body):
    """Weighted mod-11 check character (weights 2..7 repeating, from the right)."""
    digits = _to_digits(body)
    if not digits:
        raise ReferenceError_("Nothing to compute a check digit over.")
    total = sum(int(d) * (2 + i % 6) for i, d in enumerate(reversed(digits)))
    value = (11 - total % 11) % 11
    return "X" if value == 10 else str(value)


def mod11_valid(body, check):
    return mod11_check(body) == str(check).upper()


# ---------------------------------------------------------------------------
# Pattern validation and rendering
# ---------------------------------------------------------------------------
def validate_pattern(pattern, check_digit="none", allowed_chars=""):
    """Raise ReferenceError_ unless the pattern can produce unique references."""
    if not pattern or not pattern.strip():
        raise ReferenceError_("The pattern is empty.")
    tokens = [m.group(1).split(":")[0] for m in TOKEN_RE.finditer(pattern)]
    leftovers = UNKNOWN_TOKEN_RE.sub("", TOKEN_RE.sub("", pattern))
    unknown = [t for t in UNKNOWN_TOKEN_RE.findall(TOKEN_RE.sub("", pattern))]
    if unknown:
        raise ReferenceError_(f"Unknown placeholder(s): {', '.join(unknown)}.")
    if "{" in leftovers or "}" in leftovers:
        raise ReferenceError_("Unbalanced braces in the pattern.")
    if tokens.count("SEQ") != 1:
        raise ReferenceError_("The pattern must contain exactly one {SEQ} (it is what makes references unique).")
    checks = [t for t in tokens if t.startswith("CHK")]
    if len(checks) > 1:
        raise ReferenceError_("Use at most one check-digit placeholder.")
    if checks:
        if pattern.rstrip().rfind("{CHK") < pattern.find("{SEQ"):
            raise ReferenceError_("The check digit must come after {SEQ}.")
        if checks[0] == "CHK" and check_digit == "none":
            raise ReferenceError_("{CHK} needs a check-digit method.")
        if checks[0] == "CHK97" and check_digit not in ("mod97", "none"):
            raise ReferenceError_("{CHK97} needs check_digit = mod97.")
        if checks[0] == "CHK11" and check_digit not in ("mod11", "none"):
            raise ReferenceError_("{CHK11} needs check_digit = mod11.")
    elif check_digit != "none":
        raise ReferenceError_("Add {CHK} to the pattern, or set the check digit to none.")
    if allowed_chars:
        try:
            re.compile(f"^[{allowed_chars}]+$")
        except re.error:
            raise ReferenceError_("Allowed characters is not a valid character class.")
    # Literal text must itself be allowed.
    if allowed_chars:
        literal = TOKEN_RE.sub("", pattern)
        if literal and not re.fullmatch(f"[{allowed_chars}]+", literal):
            raise ReferenceError_("The pattern contains characters outside the allowed set.")
    return True


def render(pattern, *, seq, year=None, inst="", dept="", check_digit="none", allowed_chars=""):
    """Render `pattern` for a given sequence number (no counter involved)."""
    year = year or timezone.localdate().year
    out = []
    pos = 0
    for match in TOKEN_RE.finditer(pattern):
        out.append(pattern[pos:match.start()])
        token, width = match.group(1).split(":")[0], match.group(2)
        if token == "YEAR":
            out.append(f"{int(year):04d}")
        elif token == "INST":
            if not inst:
                raise ReferenceError_("This reference needs an institution code.")
            out.append(str(inst).upper())
        elif token == "DEPT":
            if not dept:
                raise ReferenceError_("This reference needs a department code.")
            out.append(str(dept).upper().replace("-", ""))
        elif token == "SEQ":
            out.append(str(int(seq)).zfill(int(width)) if width else str(int(seq)))
        else:
            method = {"CHK97": "mod97", "CHK11": "mod11"}.get(token, check_digit)
            body = "".join(out)
            out.append(mod97_check(body) if method == "mod97" else mod11_check(body))
        pos = match.end()
    out.append(pattern[pos:])
    result = "".join(out)
    if allowed_chars and not re.fullmatch(f"[{allowed_chars}]+", result):
        raise ReferenceError_(f"'{result}' contains characters outside the allowed set.")
    return result


def example(fmt, *, inst="KDP", dept="dpap", seq=123, year=None):
    """A sample rendering for the settings page."""
    return render(fmt.pattern, seq=seq, year=year, inst=inst, dept=dept,
                  check_digit=fmt.check_digit, allowed_chars=fmt.allowed_chars)


def verify(reference, check_digit="mod97"):
    """True if `reference` carries a valid trailing check (tolerant of case and
    separators — tellers and bank narrations mangle both)."""
    text = _alnum(reference)
    if check_digit == "mod97":
        if len(text) < 3 or not text[-2:].isdigit():
            return False
        return mod97_valid(text[:-2], text[-2:])
    if check_digit == "mod11":
        if len(text) < 2:
            return False
        return mod11_valid(text[:-1], text[-1])
    return True


# ---------------------------------------------------------------------------
# Issuing
# ---------------------------------------------------------------------------
def _period_key(fmt, year):
    return str(year) if fmt.reset_period == "yearly" else ""


def _scope_key(fmt, inst, dept):
    if fmt.sequence_scope == "institution":
        if not inst:
            raise ReferenceError_(f"{fmt.code} references are numbered per institution; give one.")
        return str(inst).upper()
    if fmt.sequence_scope == "department":
        if not dept:
            raise ReferenceError_(f"{fmt.code} references are numbered per department; give one.")
        return str(dept).lower()
    return ""


def _next_value(fmt, period, scope, max_wait_seconds=10.0):
    """Atomically take the next counter value. Retries briefly when SQLite
    reports a locked database (development only; PostgreSQL waits on the
    row lock instead)."""
    from .models import ReferenceCounter

    deadline = time.monotonic() + max_wait_seconds
    delay = 0.002
    while True:
        try:
            with transaction.atomic():
                try:
                    counter = (ReferenceCounter.objects.select_for_update()
                               .get(format=fmt, period_key=period, scope_key=scope))
                except ReferenceCounter.DoesNotExist:
                    try:
                        with transaction.atomic():
                            counter = ReferenceCounter.objects.create(
                                format=fmt, period_key=period, scope_key=scope, last_value=0)
                    except IntegrityError:
                        counter = (ReferenceCounter.objects.select_for_update()
                                   .get(format=fmt, period_key=period, scope_key=scope))
                ReferenceCounter.objects.filter(pk=counter.pk).update(last_value=F("last_value") + 1)
                return ReferenceCounter.objects.values_list("last_value", flat=True).get(pk=counter.pk)
        except OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() > deadline:
                raise
            time.sleep(delay + random.random() * delay)
            delay = min(delay * 1.6, 0.05)


def _retry_on_sqlite_lock(func, max_wait_seconds=10.0):
    """Run `func`, retrying while SQLite reports a locked table (development
    and tests only: SQLite's shared-cache test database locks whole tables,
    even for reads). PostgreSQL never raises this; it waits on the row lock."""
    deadline = time.monotonic() + max_wait_seconds
    delay = 0.002
    while True:
        try:
            return func()
        except OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() > deadline:
                raise
            time.sleep(delay + random.random() * delay)
            delay = min(delay * 1.6, 0.05)


def issue(code, *, inst="", dept="", when=None):
    """Issue the next reference for format `code` and return it."""
    from .models import ReferenceFormat

    fmt = _retry_on_sqlite_lock(lambda: ReferenceFormat.objects.filter(code=code, is_active=True).first())
    if fmt is None:
        raise ReferenceError_(f"No active reference format '{code}'.")
    year = (when or timezone.localdate()).year
    seq = _next_value(fmt, _period_key(fmt, year), _scope_key(fmt, inst, dept))
    return render(fmt.pattern, seq=seq, year=year, inst=inst, dept=dept,
                  check_digit=fmt.check_digit, allowed_chars=fmt.allowed_chars)
