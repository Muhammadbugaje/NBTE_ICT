"""v2.1 X23: memo approvals by Directors (and above) are blocked until their
signature is on file. The routing tests below predate X23 and test routing,
not signatures, so the block is switched off for them (the X23 tests in
messaging/test_x23_memo_signature.py keep it on)."""
import pytest


@pytest.fixture(autouse=True)
def _signatures_not_enforced_for_routing_tests(request, monkeypatch):
    if request.node.get_closest_marker("enforce_signatures"):
        return
    monkeypatch.setattr("documents.signature.blocking_enabled", lambda: False)
