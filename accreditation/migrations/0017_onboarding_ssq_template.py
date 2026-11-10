from django.db import migrations
from django.utils import timezone


STARTER_SCHEMA = {
    "intro": (
        "Provide a programme-level self-study for NBTE onboarding. This starter form records the institution's "
        "description and readiness declaration; it does not define or replace NBTE accreditation standards."
    ),
    "sections": [
        {
            "key": "programme_profile",
            "title": "Programme profile",
            "description": "Confirm the programme details and describe its objectives and delivery plan.",
            "department": False,
            "fields": [
                {"key": "programme_name", "type": "text", "label": "Programme name", "required": True,
                 "prefill_from": "programme.name"},
                {"key": "programme_level", "type": "text", "label": "Award level", "required": True,
                 "prefill_from": "programme.level"},
                {"key": "programme_objectives", "type": "textarea", "label": "Programme objectives",
                 "required": True, "validation": {"min_length": 20, "max_length": 3000}},
                {"key": "delivery_mode", "type": "choice", "label": "Planned delivery mode", "required": True,
                 "options": [
                     {"value": "in_person", "label": "In person"},
                     {"value": "blended", "label": "Blended"},
                     {"value": "online", "label": "Online"},
                 ]},
                {"key": "planned_intake", "type": "integer", "label": "Planned first-year intake",
                 "required": True, "validation": {"min": 0, "max": 100000}},
            ],
        },
        {
            "key": "staffing",
            "title": "Staffing plan",
            "description": "Describe the proposed teaching team and identify its initial members.",
            "department": False,
            "fields": [
                {"key": "staffing_summary", "type": "textarea", "label": "Staffing summary", "required": True,
                 "validation": {"min_length": 20, "max_length": 3000}},
                {"key": "staffing_plan", "type": "table", "label": "Initial programme staff", "required": True,
                 "min_rows": 1, "max_rows": 100, "columns": [
                     {"key": "name", "label": "Name", "type": "text"},
                     {"key": "qualification", "label": "Qualification", "type": "text"},
                     {"key": "role", "label": "Proposed role", "type": "text"},
                 ]},
            ],
        },
        {
            "key": "learning_resources",
            "title": "Learning resources",
            "description": "Summarise the facilities and learning resources planned for the programme.",
            "department": False,
            "fields": [
                {"key": "facilities_summary", "type": "textarea", "label": "Facilities and practical learning",
                 "required": True, "validation": {"min_length": 20, "max_length": 3000}},
                {"key": "learning_resources_summary", "type": "textarea",
                 "label": "Library, ICT and student support", "required": True,
                 "validation": {"min_length": 20, "max_length": 3000}},
                {"key": "readiness_declaration", "type": "yes_no", "label": "Is the institution ready to provide "
                 "the programme as described?", "required": True},
            ],
        },
    ],
}


def seed_onboarding_template(apps, schema_editor):
    QAType = apps.get_model("accreditation", "QAType")
    SSQTemplate = apps.get_model("accreditation", "SSQTemplate")
    qa_type, _ = QAType.objects.get_or_create(
        code="onboarding_ssq",
        defaults={
            "name": "SOP-01 Programme Self-Study",
            "description": "Internal onboarding self-study template, not a formal SOP-02 QA request type.",
            "flow_code": "qa_request",
            "is_active": False,
            "sort_order": 90,
        },
    )
    template, _ = SSQTemplate.objects.get_or_create(
        qa_type=qa_type,
        version=1,
        defaults={
            "name": "SOP-01 Programme Self-Study (Starter)",
            "schema": STARTER_SCHEMA,
            "status": "published",
            "is_current": True,
            "published_at": timezone.now(),
            "notes": (
                "Workflow starter only. Have NBTE validate the prompts against its approved self-study "
                "requirements before production use; this is not an accreditation decision standard."
            ),
        },
    )
    QAType.objects.filter(pk=qa_type.pk).update(ssq_template_id=template.pk, is_active=False)


class Migration(migrations.Migration):

    dependencies = [
        ("accreditation", "0016_onboardingssq"),
    ]

    operations = [
        migrations.RunPython(seed_onboarding_template, migrations.RunPython.noop),
    ]
