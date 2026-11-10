"""v2.1 Phase P rotation and review-chain endpoints (staff door). Same rules
as the web pages: assignments.rotation / assignments.chain decide.

    GET  rotation/pools/                         pools whose board the caller may see
    GET  rotation/suggest/?pool=&institution=    ranked candidates (logs a RotationDecision; assigners only)
    POST rotation/decisions/<id>/                {chosen, reason} confirm or override
    GET  chains/<id>/                            steps and allowed actions
    POST chains/<id>/act/                        {action: submit|approve|send_back|reject, remark, to_step}
"""
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from .common import AUTHED_ERRORS, errors


def _pool_json(p):
    return {"code": p.code, "kind": p.kind, "name": p.name,
            "department": p.department.code if p.department_id else None, "auto_assign": p.auto_assign}


def _may_run_pool(user, pool):
    """Same rule as the Rotation page (assignments.rotation_queue): only the
    Director of the pool's department, or the ES / super admin."""
    from assignments import rotation_queue
    if not rotation_queue.can_open(user):
        return False
    if rotation_queue.is_top(user):
        return True
    return pool.department_id is not None and pool.department_id in {
        d.pk for d in rotation_queue.departments_for(user)}


class PoolsView(APIView):
    @extend_schema(operation_id="rotation_pools", responses={200: OpenApiTypes.OBJECT, **AUTHED_ERRORS})
    def get(self, request):
        from assignments import rotation
        return Response({"results": [_pool_json(p) for p in rotation.visible_pools(request.user)]})


class SuggestQuery(serializers.Serializer):
    pool = serializers.CharField()
    institution = serializers.IntegerField(required=False)


class SuggestView(APIView):
    @extend_schema(operation_id="rotation_suggest", parameters=[SuggestQuery],
                   responses={200: OpenApiTypes.OBJECT, **errors(400, 404), **AUTHED_ERRORS})
    def get(self, request):
        from assignments import rotation
        from assignments.models import RotationPool
        q = SuggestQuery(data=request.query_params)
        q.is_valid(raise_exception=True)
        pool = RotationPool.objects.filter(code=q.validated_data["pool"]).first()
        if pool is None or not rotation.visible_pools(request.user).filter(pk=pool.pk).exists():
            raise Http404
        if not rotation.can_assign(request.user, pool) or not _may_run_pool(request.user, pool):
            raise PermissionDenied("Only the department's Director (or the ES) assigns this work.")
        inst = None
        if q.validated_data.get("institution"):
            from institutions.models import Institution
            inst = Institution.objects.for_user(request.user).filter(pk=q.validated_data["institution"]).first()
            if inst is None:
                raise Http404
        s = rotation.suggest(pool, subject=inst, institution=inst)
        d = s.decision
        return Response({"decision": d.pk, "pool": _pool_json(pool), "suggested": d.suggested_user_id,
                         "candidates": d.candidates})


class DecideBody(serializers.Serializer):
    chosen = serializers.IntegerField()
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class DecideView(APIView):
    @extend_schema(operation_id="rotation_decide", request=DecideBody,
                   responses={200: OpenApiTypes.OBJECT, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        from assignments import rotation
        from assignments.models import RotationDecision
        d = get_object_or_404(RotationDecision, pk=pk)
        if not rotation.can_assign(request.user, d.pool) or not _may_run_pool(request.user, d.pool):
            raise Http404
        body = DecideBody(data=request.data)
        body.is_valid(raise_exception=True)
        d = rotation.record_decision(d, by=request.user, chosen=body.validated_data["chosen"],
                                     override_reason=body.validated_data["reason"])
        return Response({"decision": d.pk, "chosen": d.chosen_user_id, "overridden": d.overridden,
                         "constraint_overridden": d.constraint_overridden})


def _chain(request, pk):
    from assignments.models import ReviewChain
    from assignments.views_rotation import can_view_chain
    c = get_object_or_404(ReviewChain.objects.select_related("policy"), pk=pk)
    if not can_view_chain(request.user, c):
        raise Http404
    return c


def _chain_json(c, user):
    from assignments import chain
    p = chain.panel(c, user)
    return {"id": c.pk, "policy": c.policy.code, "state": c.state, "current_step": c.current_step,
            "steps": [{"key": r["key"], "label": r["label"], "state": r["state"], "who": r["who"], "when": r["when"],
                       "last": ({"action": r["last"].action, "actor": r["last"].actor_id,
                                 "remark": r["last"].remark, "at": r["last"].at.isoformat()} if r["last"] else None)}
                      for r in p["rows"]],
            "can_act": p["can_act"], "return_targets": p["targets"]}


class ChainView(APIView):
    @extend_schema(operation_id="chain_detail", responses={200: OpenApiTypes.OBJECT, **errors(404), **AUTHED_ERRORS})
    def get(self, request, pk):
        return Response(_chain_json(_chain(request, pk), request.user))


class ChainActBody(serializers.Serializer):
    action = serializers.ChoiceField(choices=["submit", "approve", "send_back", "reject"])
    remark = serializers.CharField(required=False, allow_blank=True, default="")
    to_step = serializers.CharField(required=False, allow_blank=True, default="")


class ChainActView(APIView):
    @extend_schema(operation_id="chain_act", request=ChainActBody,
                   responses={200: OpenApiTypes.OBJECT, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        from assignments import chain
        c = _chain(request, pk)
        body = ChainActBody(data=request.data)
        body.is_valid(raise_exception=True)
        v = body.validated_data
        from django.core.exceptions import PermissionDenied as DjangoDenied
        try:
            if v["action"] == "submit":
                chain.submit(c, actor=request.user, note=v["remark"])
            elif v["action"] == "approve":
                chain.approve(c, actor=request.user, remark=v["remark"])
            elif v["action"] == "send_back":
                chain.send_back(c, actor=request.user, to_step=v["to_step"] or None, remark=v["remark"])
            else:
                chain.reject(c, actor=request.user, remark=v["remark"])
        except DjangoDenied as exc:
            raise PermissionDenied(str(exc))
        except ValidationError:
            raise
        c.refresh_from_db()
        return Response(_chain_json(c, request.user))
