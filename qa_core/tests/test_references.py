"""Reference generator (Master Plan §5.1, §10.5): patterns, mod-97, and
20 concurrent issuers never receiving the same number."""
import threading

import pytest
from django.db import connection

from qa_core import references
from qa_core.models import ReferenceCounter, ReferenceFormat


# --------------------------------------------------------------------- check digits
def test_mod97_round_trip_and_iso_vector():
    # ISO 7064 mod 97-10 is the IBAN scheme: GB82 WEST 1234 5698 7654 32.
    body = "WEST12345698765432GB"
    assert references.mod97_check(body) == "82"
    assert references.mod97_valid(body, "82")
    assert not references.mod97_valid(body, "83")


def test_mod97_catches_single_digit_typos_and_transpositions():
    good = references.render("NBTE-QAP-{YEAR}-{INST}-{SEQ:6}-{CHK97}", seq=123, year=2026, inst="KDP")
    assert references.verify(good)
    body, check = good[:-2], good[-2:]
    caught = 0
    total = 0
    digits = [i for i, ch in enumerate(body) if ch.isdigit()]
    for i in digits:
        for d in "0123456789":
            if d == body[i]:
                continue
            total += 1
            caught += not references.verify(body[:i] + d + body[i + 1:] + check)
    assert caught == total  # every single-digit substitution is detected
    for i, j in zip(digits, digits[1:]):
        if j == i + 1 and body[i] != body[j]:
            swapped = body[:i] + body[j] + body[i] + body[j + 1:]
            assert not references.verify(swapped + check)


def test_verify_tolerates_case_and_separators():
    ref = references.render("NBTE-QAP-{YEAR}-{INST}-{SEQ:6}-{CHK97}", seq=42, year=2026, inst="KDP")
    assert references.verify(ref.lower())
    assert references.verify(ref.replace("-", " "))
    assert references.verify(ref.replace("-", ""))


def test_mod11():
    ref = references.render("X-{SEQ:4}{CHK11}", seq=77, check_digit="mod11")
    assert references.verify(ref, "mod11")


# --------------------------------------------------------------------- patterns
@pytest.mark.parametrize("pattern, check, allowed", [
    ("NBTE/QA/{YEAR}", "none", ""),                    # no {SEQ}
    ("{SEQ}-{SEQ}", "none", ""),                       # two {SEQ}
    ("{SEQ}-{FOO}", "none", ""),                       # unknown placeholder
    ("{CHK97}-{SEQ}", "mod97", ""),                    # check before SEQ
    ("{SEQ}", "mod97", ""),                            # method without placeholder
    ("A/{SEQ}", "none", "A-Z0-9-"),                    # literal outside allowed set
    ("{SEQ}-{CHK97}-{CHK11}", "mod97", ""),            # two checks
])
def test_invalid_patterns(pattern, check, allowed):
    with pytest.raises(references.ReferenceError_):
        references.validate_pattern(pattern, check, allowed)


def test_payment_reference_shape_matches_plan():
    ref = references.render("NBTE-QAP-{YEAR}-{INST}-{SEQ:6}-{CHK97}", seq=123, year=2026, inst="KDP",
                            check_digit="mod97", allowed_chars="A-Z0-9.=-")
    assert ref.startswith("NBTE-QAP-2026-KDP-000123-")
    assert len(ref.rsplit("-", 1)[1]) == 2
    assert "/" not in ref  # Paystack-safe


@pytest.mark.django_db
def test_issue_uses_seeded_formats(qa_seed):
    first = references.issue("payment", inst="KDP")
    second = references.issue("payment", inst="DMP")
    assert first.endswith(references.mod97_check(first[:-2]))
    assert int(first.split("-")[4]) + 1 == int(second.split("-")[4])  # one series across institutions
    a = references.issue("application", inst="KDP")
    b = references.issue("application", inst="DMP")
    assert a.endswith("/00001") and b.endswith("/00001")  # per-institution series
    with pytest.raises(references.ReferenceError_):
        references.issue("application")  # needs an institution
    assert references.issue("pending_action").startswith("PA-")


@pytest.mark.django_db
def test_changed_pattern_applies_to_new_references_only(qa_seed):
    old = references.issue("complaint")
    fmt = ReferenceFormat.objects.get(code="complaint")
    fmt.pattern = "CMP-{YEAR}-{SEQ:4}"
    fmt.save()
    new = references.issue("complaint")
    assert old.startswith("NBTE-CMP-") and new.startswith("CMP-")


# --------------------------------------------------------------------- concurrency
@pytest.mark.django_db(transaction=True)
def test_20_threads_never_share_a_number(qa_seed):
    results, errors = [], []
    barrier = threading.Barrier(20)

    def worker():
        try:
            barrier.wait(timeout=10)
            for _ in range(5):
                results.append(references.issue("payment", inst="KDP"))
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(repr(exc))
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not errors, errors[:3]
    assert len(results) == 100
    assert len(set(results)) == 100, "duplicate reference issued"
    seqs = sorted(int(r.split("-")[4]) for r in results)
    assert seqs == list(range(seqs[0], seqs[0] + 100))  # gap-free when nothing rolls back
    assert all(references.verify(r) for r in results)
    counter = ReferenceCounter.objects.get(format__code="payment")
    assert counter.last_value == seqs[-1]
