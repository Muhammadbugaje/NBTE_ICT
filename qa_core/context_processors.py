"""Sidebar data for the internal QA menu (cheap; empty on the portal host).

Phase 2 adds the New Institution and TVET Support menus (§6, driven by
ActionPolicy rows, so labels and groups are editable), Applications (SOP-01)
and Finance (QA).
"""
from django.utils.functional import SimpleLazyObject


def qa_nav(request):
    user = getattr(request, "user", None)
    if (user is None or not user.is_authenticated or getattr(request, "is_qa_portal", False)
            or getattr(user, "user_type", "staff") != "staff"):
        return {"qa_nav": {}}

    cached = getattr(request, "_qa_nav_lazy", None)
    if cached is not None:
        return {"qa_nav": cached}

    def build_uncached():
        from .permissions import (
            can_read_outbox, can_view_applications, can_view_finance, can_view_institutions, can_view_pending,
            can_view_qa_settings,
        )

        data = {"settings": can_view_qa_settings(user), "pending": can_view_pending(user), "awaiting": 0,
                "institutions": can_view_institutions(user), "applications": can_view_applications(user),
                "finance": can_view_finance(user), "outbox": can_read_outbox(user)}
        from accreditation.qa_views import can_view_qa_requests

        data["qa_requests"] = can_view_qa_requests(user)  # Phase 3: SOP-02
        from accreditation import case_services

        data["qa_cases"] = case_services.can_view_cases(user)  # Phase 4: SOP-03
        if data["qa_cases"]:
            from accreditation.case_views import can_view_drift

            data["qa_cases_mine"] = len(case_services.my_stage_links(user))
            data["qa_drift"] = can_view_drift(user)
        from inspection import services as insp  # Phase 5: inspection and resource persons

        data["inspection"] = insp.can_view_people(user) or insp.can_view_service(user)
        if data["inspection"]:
            data["inspection_people"] = insp.can_view_people(user)
            data["inspection_service"] = insp.can_view_service(user)
            data["inspection_endorse"] = insp.endorsement_count(user)
        from institutions.pb_views import _can_view as can_view_bodies  # Phase 6: professional bodies

        data["professional_bodies"] = can_view_bodies(user)
        from institutions.pb_views import can_manage as can_manage_bodies, can_manage_master

        data["professional_bodies_manage"] = can_manage_bodies(user) or can_manage_master(user)  # Part 1b
        data["programmes"] = data["professional_bodies"] or can_manage_master(user)  # v2.1 Part 5
        data["programmes_pending"] = 0
        if data["programmes"] and user.is_authenticated and (data["professional_bodies_manage"]
                                                             or can_manage_master(user)):
            from institutions.models import BodyProgrammeRequest

            data["programmes_pending"] = BodyProgrammeRequest.objects.filter(
                status=BodyProgrammeRequest.PENDING).count()
        from complaints import views as complaint_views  # Phase 7: complaints

        data["complaints"] = complaint_views.can_open(user)
        if data["complaints"]:
            from complaints.models import ComplaintStage

            data["complaints_mine"] = ComplaintStage.objects.filter(
                assigned_to=user, left_at__isnull=True, complaint__closed_at__isnull=True,
                pauses_sla=False).values("complaint_id").distinct().count()
            data["complaints_reports"] = complaint_views.can_report(user)
        from assignments.rotation_queue import can_open as can_open_rotation  # directors + ES only

        data["rotation"] = can_open_rotation(user)
        from documents.services import can_view_register  # v2.1 Phase F (X24-X26): issued documents

        data["documents_register"] = can_view_register(user)
        from documents import official_letters  # Director (own department), ES, super admins

        data["official_letters"] = official_letters.can_open_queue(user)
        if data["official_letters"]:
            data["official_letters_count"] = official_letters.scoped(user).count()
        data["show"] = any(data[k] for k in ("settings", "pending", "institutions", "applications", "finance",
                                             "qa_requests", "qa_cases", "inspection", "complaints", "rotation",
                                             "documents_register", "official_letters", "programmes"))
        if data["qa_requests"]:
            from accreditation.models import SSQ

            data["ssq_waiting"] = SSQ.objects.for_user(user).filter(status="submitted", is_current=True).count()
        counts = {}
        if data["pending"] or data["institutions"]:
            from django.db.models import Count
            from django.utils import timezone

            from .actions import can_decide, visible_to

            open_items = list(visible_to(user).filter(status="pending", expires_at__gt=timezone.now())
                              .exclude(initiated_by=user)[:200])
            impersonating = bool(getattr(request, "is_impersonating", False))
            decidable = [pa for pa in open_items if can_decide(pa, user, impersonating=impersonating)[0]]
            data["awaiting"] = len(decidable)
            for pa in decidable:
                counts[pa.policy_id] = counts.get(pa.policy_id, 0) + 1
            del Count
        if data["institutions"]:
            data["menus"] = _menus(user, counts)
        if data["applications"] and user.department_id:
            from accreditation.services import clearance_rows

            data["clearance_waiting"] = len(clearance_rows(user, status="pending"))
        if data["finance"]:
            from payments.models import PaymentVerification

            data["finance_open"] = PaymentVerification.objects.for_user(user).filter(is_open=True).count()
        return data

    def build():
        # Per-user (and per-impersonation) key; refreshed instantly by core.fastcache signals, 30 s safety TTL.
        from core import fastcache

        return fastcache.cached(fastcache.NAV, f"i{int(bool(getattr(request, 'is_impersonating', False)))}",
                                build_uncached, ttl=30, user=user)

    lazy = SimpleLazyObject(build)
    request._qa_nav_lazy = lazy  # one build per request (sidebar + workspace switcher)
    return {"qa_nav": lazy}


INSPECTION_SCREENS = {"inspection.edit_qa_personnel": "qa_insp:personnel",
                      "inspection.edit_qa_mapping": "qa_insp:finder",
                      "inspection.override_post_visit_personnel": "qa_insp:inspections"}


def _menus(user, counts):
    from django.urls import reverse

    from .models import ActionPolicy

    groups = {"New Institution": [], "TVET Support": []}
    for policy in ActionPolicy.objects.filter(menu_group__in=groups.keys()).order_by("menu_group", "label"):
        can_start = user.is_superuser or not policy.initiate_permission or user.has_perm(policy.initiate_permission)
        can_confirm = bool(policy.requires_second_officer and (user.is_superuser or (
            policy.confirm_permission and user.has_perm(policy.confirm_permission))))
        if not (can_start or can_confirm):
            continue
        if policy.action_code == "institutions.add_tvet":
            url = reverse("qa_inst:add_tvet")
        elif policy.action_code == "institutions.bulk_import":
            url = reverse("qa_inst:import")
        elif policy.action_code in INSPECTION_SCREENS:  # Phase 5: live screens
            url = reverse(INSPECTION_SCREENS[policy.action_code])
        else:
            url = reverse("qa_inst:launcher", args=[policy.action_code])
        item = {"label": policy.label, "url": url, "code": policy.action_code, "enabled": policy.is_enabled,
                "reason": "" if policy.is_enabled else policy.description, "can_start": can_start}
        groups[policy.menu_group].append(item)
        if can_confirm:
            label = (policy.label[len("Initiate "):] if policy.label.startswith("Initiate ")
                     else f"Confirm {policy.label}")
            groups[policy.menu_group].append({
                "label": label, "confirm": True, "count": counts.get(policy.action_code, 0),
                "url": reverse("qa:pending_list") + f"?status=pending&action={policy.action_code}",
                "enabled": policy.is_enabled, "reason": "" if policy.is_enabled else policy.description,
                "code": policy.action_code,
            })
    return [{"group": g, "items": items} for g, items in groups.items() if items]


def qa_portal_link(request):
    """`qa_portal_url` for the public landing page (button + footer link):
    settings.QA_PORTAL_URL, or https://QA_PORTAL_HOST when it is blank."""

    def build():
        from .hosts import portal_base_url

        return portal_base_url() + "/"

    return {"qa_portal_url": SimpleLazyObject(build)}
