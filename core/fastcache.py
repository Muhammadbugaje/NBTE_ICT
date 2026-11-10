"""Fast, self-refreshing cache for the QA area (one module, built on core.cache epochs).

* ``cached(ns, key, builder, ttl, user=None)``: versioned get-or-build. Keys embed the namespace epoch, so
  bumping the epoch orphans every old entry at once (works on LocMem and Redis; no pattern deletes).
* ``watch(ns, "app.Model", ...)``: bump ``ns`` on post_save / post_delete of those models, so edits show
  immediately. TTL is only a safety net.
* Anything per-person MUST pass ``user=`` (the key then includes the user id); never share across users.
"""
from __future__ import annotations

import logging

from django.apps import apps
from django.db.models.signals import post_delete, post_save

from core import cache as _c

log = logging.getLogger(__name__)

NAV = "qa_nav"          # sidebar data per user (counts, menu visibility)
LOOKUPS = "qa_lookups"  # flows/stages/rates/master lists shared by everybody
BANNERS = "qa_banners"  # per-user platform banners
INST = "qa_inst"        # per-institution summaries (portal setup checklist); bumped by institutions-app writes
HOME = "qa_home"        # per-user home/dashboard widgets; bumped by any write in the QA data apps
HOME_APPS = ("accreditation", "complaints", "honoraria", "payments", "assignments", "documents", "inspection",
             "institutions", "tasks", "leave", "qa_core")

_WATCHED = {
    NAV: ["qa_core.PendingAction", "qa_core.ActionPolicy", "qa_core.QATaskLink", "accreditation.SSQ",
          "accreditation.QARequest", "accreditation.Application", "accreditation.DepartmentalClearance",
          "accreditation.QACase", "payments.PaymentVerification", "assignments.WorkAssignment",
          "complaints.ComplaintStage", "complaints.Complaint", "institutions.BodyProgrammeRequest",
          "inspection.PostVisitReport", "documents.OfficialLetter", "tasks.Task", "tasks.TaskAssignee",
          "accounts.User", "accounts.Role", "staff.Staff", "staff.Department", "institutions.Institution"],
    LOOKUPS: ["qa_core.FlowDefinition", "qa_core.FlowStage", "qa_core.FlowInstance", "config.ConfigEntry",
              "accreditation.RateTable", "staff.Department", "staff.Designation"],
    BANNERS: ["documents.StaffSignature", "honoraria.PayeeBankAccount", "accounts.User"],
}


def cached(ns, key, builder, ttl=30, user=None):
    """Versioned get-or-build; a cache outage falls back to calling ``builder``."""
    try:
        return _c.get_or_set_context(ns, (key,), builder, ttl=ttl, user=user)
    except Exception:  # noqa: BLE001 - caching is best effort
        log.debug("fastcache fallback for %s", ns, exc_info=True)
        return builder()


def invalidate(ns):
    try:
        _c.invalidate(ns)
    except Exception:  # noqa: BLE001
        pass


def watch(ns, *labels):
    for label in labels:
        try:
            model = apps.get_model(label)
        except (LookupError, ValueError):
            continue
        uid = f"fastcache-{ns}-{label}"
        post_save.connect(lambda sender, **kw: invalidate(ns), sender=model, weak=False, dispatch_uid=uid + "-s")
        post_delete.connect(lambda sender, **kw: invalidate(ns), sender=model, weak=False, dispatch_uid=uid + "-d")


def install():
    """Called once from CoreConfig.ready()."""
    _connect_flow_gen()
    for model in apps.get_app_config("institutions").get_models():
        watch(INST, model._meta.label)
    watch(INST, "accounts.User")
    for app in HOME_APPS:
        try:
            for model in apps.get_app_config(app).get_models():
                if model._meta.model_name in ("auditlog", "auditentry", "usersession", "notification"):
                    continue
                watch(HOME, model._meta.label)
        except LookupError:
            continue
    for ns, labels in _WATCHED.items():
        watch(ns, *labels)


def _connect_flow_gen():
    try:
        model = apps.get_model("qa_core.FlowInstance")
    except LookupError:
        return

    def bump(**_kw):
        from accreditation import case_services

        case_services._bump_fi_gen()

    post_save.connect(bump, sender=model, weak=False, dispatch_uid="fastcache-fi-gen-s")
    post_delete.connect(bump, sender=model, weak=False, dispatch_uid="fastcache-fi-gen-d")
