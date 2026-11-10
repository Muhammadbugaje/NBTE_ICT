"""X23 (owner decision): a Director approves a memo only with a signature on file."""
import pytest
from django.core.exceptions import ValidationError

from documents import signature as SIG
from messaging import routing
from messaging.models import Memo

from .test_memo_directorate_routing import org, send  # noqa: F401

pytestmark = [pytest.mark.django_db, pytest.mark.enforce_signatures]


def test_x23_memo_approval_blocked_until_signature(org):  # noqa: F811
    memo = send(org["a.dir"], to=org["b.off"])
    approver = routing.actor_for_memo(memo)
    assert approver == org["b.dir"]
    with pytest.raises(ValidationError) as exc:
        routing.approve_step(memo, approver, "ok")
    assert "My profile → Signature" in " ".join(exc.value.messages)
    memo.refresh_from_db()
    assert memo.status == Memo.AWAITING
    SIG.save_demo(approver)
    routing.approve_step(memo, approver, "ok")
    memo.refresh_from_db()
    assert memo.status == Memo.PUBLISHED
