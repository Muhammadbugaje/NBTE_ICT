"""insights/ (staff door): the signed-in user's own insights from
dashboards.insights (arrival_stats, task_state_counts, ticket_trend) - the
same numbers as the "My workspace" charts (api/docs/insights.md)."""
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from ..serializers_insights import InsightsQuerySerializer, InsightsSerializer
from .common import AUTHED_ERRORS, errors


class InsightsView(APIView):
    @extend_schema(operation_id="insights", parameters=[InsightsQuerySerializer],
                   responses={200: InsightsSerializer, **errors(400), **AUTHED_ERRORS})
    def get(self, request):
        from dashboards import insights

        if getattr(request.user, "user_type", "staff") != "staff":
            raise PermissionDenied()
        s = InsightsQuerySerializer(data=request.query_params)
        s.is_valid(raise_exception=True)
        period = insights.clean_period(s.validated_data.get("period"))
        user = request.user
        return Response(InsightsSerializer({
            "period": period,
            "period_label": insights.PERIOD_LABELS[period],
            "arrivals": insights.arrival_stats(user, period),
            "tasks": insights.task_state_counts(user, period),
            "tickets": insights.ticket_trend(user, period),
        }).data)
