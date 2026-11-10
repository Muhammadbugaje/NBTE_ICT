"""v2.1 S-26: the e-mailed sign-in code page (only when security.2fa.email_code_enabled)."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from . import twofactor


@login_required
@require_http_methods(["GET", "POST"])
def verify_code(request):
    if not request.session.get(twofactor.PENDING):
        return redirect("core:home")
    error = ""
    if request.method == "POST":
        ok, error = twofactor.verify(request, request.POST.get("code", ""))
        if ok:
            return redirect("core:home")
    return render(request, "core/verify_code.html", {"error": error, "email": request.user.email})
