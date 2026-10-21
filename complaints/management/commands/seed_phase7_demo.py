"""seed_phase7_demo — the Phase 7 demo: three open complaints, dashboard history.

    python manage.py seed_phase7_demo            # complaints + 12 months of confirmed demo revenue
    python manage.py seed_phase7_demo --no-history

What it does (idempotent — safe to run again):
1. runs ``seed_startup --with-demo`` (complaint flow, layers, routing,
   escalation, knowledge base, settings, permissions and the demo accounts);
2. raises three complaints from the demo institution's administrator:
   * Payment issues — on track (routed to Finance, just received);
   * SSQ submission issues — acknowledged, 70% of its SLA used (near breach);
   * Workflow delays (Critical) — breached and escalated to the Director,
     the Deputy Director/Division Head and the ES;
3. adds twelve months of confirmed demo bills so the ES / Director
   dashboards draw a revenue history (``--no-history`` skips it).

Everything is DemoTag'ed: ``seed_startup --reset-demo`` removes it.
Refused when DEBUG is false unless ``--i-understand-production``.
"""
import io

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create the Phase 7 demo complaints (one per SLA state) and the dashboard revenue history."

    def add_arguments(self, parser):
        parser.add_argument("--no-history", action="store_true", help="Do not add the demo revenue history.")
        parser.add_argument("--skip-startup", action="store_true", help="Do not run seed_startup first.")
        parser.add_argument("--i-understand-production", action="store_true",
                            help="Allow running when DEBUG is false.")

    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["i_understand_production"]:
            raise CommandError("seed_phase7_demo creates demo data; it is refused when DEBUG is false. "
                               "Add --i-understand-production if you really mean it.")
        if not opts["skip_startup"]:
            call_command("seed_startup", "--with-demo", stdout=io.StringIO(), stderr=io.StringIO())
            self.stdout.write("seed_startup --with-demo: done.")
        from complaints import demo, services
        from complaints.models import Complaint
        from qa_core.models import DemoTag

        import contextlib

        # The demo sends its notifications to the console mail log; keep the summary readable.
        with contextlib.redirect_stdout(io.StringIO()):
            result = demo.build_demo()
            history = 0 if opts["no_history"] else demo.build_revenue_history()
        w = self.stdout.write
        w(self.style.SUCCESS("NBTE QA — Phase 7 demo ready"))
        w(f"  Demo complaints: {result['total']} (+{result['created']} new)")
        from django.contrib.contenttypes.models import ContentType

        ids = DemoTag.objects.filter(content_type=ContentType.objects.get_for_model(Complaint)).values_list(
            "object_id", flat=True)
        for c in Complaint.unscoped.filter(pk__in=list(ids)).select_related("layer", "current_department"):
            state = services.sla_state(c.open_stage())
            w(f"    {c.reference:<22}{c.layer.name:<30}{c.status:<16}{state['state']:<10}"
              f"{getattr(c.current_department, 'name', '—')}")
        if not opts["no_history"]:
            w(f"  Revenue history: +{history} confirmed demo bills")
        w("  Internal: http://localhost:8000/qa/complaints/   Portal: the QA portal host, /complaints/")
        w("  The SLA sweep runs from Celery beat, or by hand: python manage.py complaints_sla_sweep")
