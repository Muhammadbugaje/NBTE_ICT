"""Pay again after a failed / reversed payment.

Schema: a withdrawn claim is kept on the Finance queue item and on the proof
(withdrawn_at / withdrawn_by / withdrawn_reason) instead of being deleted.

Data: published `payment` flows are never re-seeded (seed_startup skips
them), so the new institution transition `report_payment_failed` (every
"claimed" stage → awaiting_payment, remark required) is added here to every
existing payment flow version. Fresh databases get it from
qa_core.seed_data.PAYMENT_FLOW; this step is then a no-op. Additive only.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


CLAIMED_STAGES = ("provider_processing", "provider_reported_paid", "proof_submitted",
                  "awaiting_finance_confirmation", "finance_confirmation_overdue")
ACTION = "report_payment_failed"
LABEL = "My payment failed or was reversed — pay again"


def add_transitions(apps, schema_editor):
    FlowDefinition = apps.get_model("qa_core", "FlowDefinition")
    FlowStage = apps.get_model("qa_core", "FlowStage")
    FlowTransition = apps.get_model("qa_core", "FlowTransition")
    for flow in FlowDefinition.objects.filter(code="payment"):
        dst = FlowStage.objects.filter(flow=flow, code="awaiting_payment").first()
        if dst is None:
            continue
        for order, code in enumerate(CLAIMED_STAGES):
            src = FlowStage.objects.filter(flow=flow, code=code).first()
            if src is None or FlowTransition.objects.filter(from_stage=src, action_code=ACTION).exists():
                continue
            FlowTransition.objects.create(flow=flow, from_stage=src, to_stage=dst, action_code=ACTION,
                                          label=LABEL, requires_remark=True, sort_order=1100 + order)


def remove_transitions(apps, schema_editor):
    FlowTransition = apps.get_model("qa_core", "FlowTransition")
    FlowTransition.objects.filter(flow__code="payment", action_code=ACTION).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0010_v25_cancellation_refunds'),
        ('qa_core', '0007_v23_settings'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentverification',
            name='withdrawn_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='paymentverification',
            name='withdrawn_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='paymentverification',
            name='withdrawn_reason',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='proofofpayment',
            name='withdrawn_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='proofofpayment',
            name='withdrawn_reason',
            field=models.TextField(blank=True),
        ),
        migrations.RunPython(add_transitions, remove_transitions),
    ]
