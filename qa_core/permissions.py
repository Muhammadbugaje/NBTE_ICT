"""Who may open the Phase 1 QA screens (Master Plan §12–§14)."""
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

SETTINGS_VIEW_PERMS = (
    "qa_core.edit_qa_settings",
    "qa_core.edit_qa_dual_control_settings",
    "qa_core.manage_flows",
    "qa_core.publish_flow_version",
    "qa_core.manage_reference_formats",
    "qa_core.manage_notification_templates",
    # Phase 3: the SSQ template builder and the curriculum catalogue
    "accreditation.manage_ssq_templates",
    "accreditation.publish_ssq_template",
    "accreditation.manage_catalogue",
    "accreditation.manage_rate_table",  # Phase 4: Settings → Rate table
    # Phase 7: Settings → Complaints
    "complaints.manage_complaint_layers",
    "complaints.manage_routing_rules",
    "complaints.manage_escalation_rules",
    "complaints.manage_knowledge_base",
)

PENDING_VIEW_PERMS = (
    "qa_core.view_pending_actions",
    "qa_core.edit_qa_settings",
    "qa_core.edit_qa_dual_control_settings",
    "qa_core.manage_reference_formats",
    "qa_core.confirm_any_department",
)


def _is_staff(user):
    return user.is_authenticated and getattr(user, "user_type", "staff") == "staff"


def can_view_qa_settings(user):
    return _is_staff(user) and (user.is_superuser or any(user.has_perm(p) for p in SETTINGS_VIEW_PERMS))


def can_view_pending(user):
    if not _is_staff(user):
        return False
    if user.is_superuser or any(user.has_perm(p) for p in PENDING_VIEW_PERMS):
        return True
    # X47: anyone who confirms or approves something (e.g. the TVET Support groups), and the ES.
    import org
    if org.is_es(user) or org.is_super_admin(user):
        return True
    return any(p.split(".", 1)[-1].startswith(("confirm_", "approve_")) for p in user.get_all_permissions())


def can_see_qa_menu(user):
    return can_view_qa_settings(user) or can_view_pending(user)


def require(check):
    """Login required, then `check(user)` or 403."""
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not check(request.user):
                raise PermissionDenied("You do not have access to this QA screen.")
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


def require_or_404(check):
    """For object routes: login required, then `check(user)` or **404** —
    a 403 would confirm that the object exists (Master Plan §4, layer 3)."""
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            from django.http import Http404

            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not check(request.user):
                raise Http404("Not found.")
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


def is_qa_staff(user):
    return _is_staff(user)


def can_view_institutions(user):
    """Institutions screens: any staff member who could see at least one
    institution (scoping does the rest) or who holds an institutions perm."""
    if not _is_staff(user):
        return False
    if user.is_superuser or user.has_perm("qa_core.view_all_institutions") or user.department_id:
        return True
    from assignments import access
    return access.full_viewer_everywhere(user)  # X2: ES / super admin without a department


def can_view_finance(user):
    return _is_staff(user) and (user.is_superuser or any(user.has_perm(p) for p in (
        "payments.view_obligation", "payments.verify_payment", "payments.final_confirm_payment",
        "payments.import_bank_statement")))


def can_view_applications(user):
    if not _is_staff(user):
        return False
    import org

    return (user.is_superuser or user.has_perm("accreditation.view_onboarding_application")
            or user.has_perm("accreditation.decide_departmental_clearance")
            or user.has_perm("qa_core.view_all_institutions") or org.is_director(user, "ict"))


def can_see_qa_menu(user):  # noqa: F811 (Phase 2 widens the Phase 1 rule)
    return (can_view_qa_settings(user) or can_view_pending(user) or can_view_finance(user)
            or can_view_applications(user))


def can_read_outbox(user):
    return _is_staff(user) and (user.is_superuser or user.has_perm("qa_core.view_oversight_board")
                                or user.has_perm("qa_core.edit_qa_settings"))
