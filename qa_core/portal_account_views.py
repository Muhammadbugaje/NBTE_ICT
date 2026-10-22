"""Phase 9: QA portal account-security endpoints (signed-in devices,
notification settings, account deletion requests and passkeys).

Thin wrappers: the work is done by the shared helpers in
accounts/security_views.py and the service modules they call. These are
separate functions (not the internal views re-used) because the portal
URLconf must not share a view with the internal one
(qa_core/tests/test_host_routing.py).
"""
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts import security_views as shared

from .portal_views import portal_login_required


def _back(anchor):
    return redirect(reverse("qa_portal:account") + f"#{anchor}")


@portal_login_required
@require_POST
def device_revoke(request, pk):
    shared.do_revoke_device(request, pk)
    return _back("p9-devices")


@portal_login_required
@require_POST
def devices_revoke_others(request):
    shared.do_revoke_others(request)
    return _back("p9-devices")


@portal_login_required
@require_POST
def notification_settings(request):
    from notifications.settings_views import preference_form

    if shared.is_impersonating(request):
        messages.error(request, shared.IMPERSONATION_MESSAGE)
        return _back("p9-notifications")
    form = preference_form(request.user, "portal", request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, "Notification settings saved.")
    else:
        messages.error(request, "Some notification settings were not valid: "
                       + "; ".join(f"{k}: {' '.join(v)}" for k, v in form.errors.items()))
    return _back("p9-notifications")


@portal_login_required
@require_POST
def deletion_request(request):
    shared.do_request_deletion(request)
    return _back("p9-deletion")


@portal_login_required
@require_POST
def deletion_withdraw(request):
    shared.do_withdraw_deletion(request)
    return _back("p9-deletion")


@portal_login_required
@require_POST
def passkey_rename(request, pk):
    shared.do_passkey_rename(request, pk)
    return _back("p9-passkeys")


@portal_login_required
@require_POST
def passkey_remove(request, pk):
    shared.do_passkey_remove(request, pk)
    return _back("p9-passkeys")


@require_POST
def passkey_register_begin(request):
    return shared.passkey_register_begin_json(request)


@require_POST
def passkey_register_finish(request):
    return shared.passkey_register_finish_json(request)


@require_POST
def passkey_sign_in_begin(request):
    return shared.passkey_auth_begin_json(request)


@require_POST
def passkey_sign_in_finish(request):
    return shared.passkey_auth_finish_json(request, reverse("qa_portal:dashboard"))
