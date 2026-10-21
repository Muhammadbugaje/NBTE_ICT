"""Internal QA URLs (NBTE host), mounted at /qa/ with namespace "qa".

The portal host never sees these: it uses qa_core.portal_urls instead.
"""
from django.urls import path

from . import views, workspace

app_name = "qa"

urlpatterns = [
    # Phase 6: the workspace switcher (main system ↔ QA sidebar)
    path("workspace/", workspace.switch, name="workspace"),
    # Settings → QA
    path("settings/", views.settings_home, name="settings"),
    path("settings/rules/", views.settings_rules, name="settings_rules"),
    path("settings/rules/<int:pk>/", views.setting_row, name="setting_row"),
    path("settings/rules/<int:pk>/edit/", views.setting_edit, name="setting_edit"),
    path("settings/rules/<int:pk>/save/", views.setting_save, name="setting_save"),
    path("settings/rules/<int:pk>/history/", views.setting_history, name="setting_history"),
    path("settings/formulas/", views.settings_formulas, name="settings_formulas"),
    path("settings/formulas/<int:pk>/test/", views.formula_test, name="formula_test"),
    path("settings/formulas/<int:pk>/save/", views.formula_save, name="formula_save"),
    path("settings/references/", views.settings_references, name="settings_references"),
    path("settings/references/<int:pk>/edit/", views.reference_edit, name="reference_edit"),
    path("settings/references/check/", views.reference_check, name="reference_check"),
    path("settings/workflows/", views.settings_workflows, name="settings_workflows"),
    path("settings/workflows/<int:pk>/", views.workflow_detail, name="workflow_detail"),
    path("settings/templates/", views.settings_templates, name="settings_templates"),
    path("settings/templates/<int:pk>/edit/", views.template_edit, name="template_edit"),
    path("settings/templates/<int:pk>/preview/", views.template_preview, name="template_preview"),
    path("settings/templates/<int:pk>/toggle/", views.template_toggle, name="template_toggle"),
    # Pending confirmations
    path("pending/", views.pending_list, name="pending_list"),
    path("pending/<int:pk>/", views.pending_detail, name="pending_detail"),
    path("pending/<int:pk>/confirm/", views.pending_confirm, name="pending_confirm"),
    path("pending/<int:pk>/reject/", views.pending_reject, name="pending_reject"),
    path("pending/<int:pk>/withdraw/", views.pending_withdraw, name="pending_withdraw"),
    # Phase 2: console e-mail outbox (e-mails are logged, not sent)
    path("outbox/", views.outbox, name="outbox"),
]
