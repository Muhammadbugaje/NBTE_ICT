"""Portal door: complaints and the knowledge base (complaints.portal_views).

Institutions see their institution's complaints, resource persons their own
(``complaints.services.visible_to``); internal notes never reach the app.
"""
from django.db.models import F
from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import serializers_portal as sp
from ..services import portal_complaints as svc
from complaints import services as complaint_services
from .common import AUTHED_ERRORS, errors
from .portal_common import Forbidden, binary, body, page, paged, query, website_rules

ERR_404 = {**errors(404), **AUTHED_ERRORS}
ERR_400_404 = {**errors(400, 404), **AUTHED_ERRORS}
TAG = ["portal: complaints"]


def _readonly(request):
    if getattr(request, "is_impersonating", False):
        raise Forbidden("Read-only impersonation.")


def _detail(request, c, code=status.HTTP_200_OK):
    c.refresh_from_db()
    return Response(sp.ComplaintDetailSerializer(svc.complaint_detail_payload(request, c)).data, status=code)


def _django_fields(exc):
    """complaints.portal_views._msg keeps every message; here field errors keep their field."""
    if hasattr(exc, "message_dict"):
        raise serializers.ValidationError({k: v for k, v in exc.message_dict.items()}) from None
    raise serializers.ValidationError({"non_field_errors": exc.messages}) from None


class ComplaintsView(APIView):
    @extend_schema(operation_id="portal_complaints_list", tags=TAG, parameters=[sp.ComplaintFilterSerializer],
                   responses={200: paged(sp.ComplaintRowSerializer), **ERR_400_404})
    def get(self, request):
        svc.external_or_404(request.user)
        params = query(sp.ComplaintFilterSerializer, request)
        return page(self, request, svc.complaints_queryset(request.user, params), svc.complaint_row,
                    sp.ComplaintRowSerializer)

    @extend_schema(operation_id="portal_complaints_create", tags=TAG,
                   request={"multipart/form-data": sp.ComplaintCreateSerializer,
                            "application/json": sp.ComplaintCreateSerializer},
                   responses={201: sp.ComplaintDetailSerializer, **ERR_400_404})
    def post(self, request):
        """Raise a complaint (optionally about a record: ``about`` = kind:id)."""
        from django.core.exceptions import ValidationError

        from complaints import services

        svc.external_or_404(request.user)
        _readonly(request)
        data = body(sp.ComplaintCreateSerializer, request)
        layers = services.layers_for(request.user)
        layer = next((lay for lay in layers if lay.pk == data.get("layer")), None)
        if layer is None:
            raise serializers.ValidationError({"layer": ["Choose what the complaint is about."]})
        _about, _kind, related = svc.resolve_about(request.user, data.get("about", ""))
        sent = data.get("answers") or {}
        answers = {f.get("key"): sent.get(f.get("key"), "") for f in (layer.custom_fields or [])}
        try:
            with website_rules():
                complaint = services.raise_complaint(
                    request.user, layer=layer, subject=data.get("subject", ""), body=data.get("body", ""),
                    priority="", related=related, answers=answers, files=request.FILES.getlist("files"))
        except ValidationError as exc:
            _django_fields(exc)
        return _detail(request, services.get_visible(request.user, complaint.pk), status.HTTP_201_CREATED)


class ComplaintNewView(APIView):
    @extend_schema(operation_id="portal_complaints_new", tags=TAG, parameters=[sp.NewComplaintQuerySerializer],
                   responses={200: sp.NewComplaintSerializer, **ERR_400_404})
    def get(self, request):
        """The raise form: complaint layers (with their extra questions), the
        suggested layer for ?about=kind:id, and popular help articles."""
        svc.external_or_404(request.user)
        params = query(sp.NewComplaintQuerySerializer, request)
        return Response(sp.NewComplaintSerializer(svc.new_complaint_payload(request.user,
                                                                            params.get("about", ""))).data)


class ComplaintSuggestView(APIView):
    @extend_schema(operation_id="portal_complaints_suggest", tags=TAG, parameters=[sp.SuggestQuerySerializer],
                   responses={200: sp.ArticleListSerializer, **ERR_400_404})
    def get(self, request):
        """Knowledge-base suggestions while the subject is typed."""
        from complaints import services
        from complaints.models import ComplaintLayer

        svc.external_or_404(request.user)
        params = query(sp.SuggestQuerySerializer, request)
        text = f"{params.get('subject', '')} {params.get('body', '')[:300]}"
        layer = ComplaintLayer.objects.filter(pk=params.get("layer") or 0).first()
        articles = services.kb_search(text, audience=getattr(request.user, "user_type", "institution"), layer=layer)
        return Response(sp.ArticleListSerializer({"results": [svc.article_dict(a) for a in articles]}).data)


class HelpView(APIView):
    @extend_schema(operation_id="portal_help_list", tags=TAG, parameters=[sp.HelpQuerySerializer],
                   responses={200: sp.ArticleListSerializer, **ERR_400_404})
    def get(self, request):
        """The knowledge base (search with ?q=)."""
        from complaints import services
        from complaints.portal_views import _articles

        svc.external_or_404(request.user)
        q = (query(sp.HelpQuerySerializer, request).get("q") or "").strip()
        audience = getattr(request.user, "user_type", "institution")
        articles = (services.kb_search(q, audience=audience, limit=20) if q
                    else list(_articles(request.user).select_related("layer")))
        return Response(sp.ArticleListSerializer({"results": [svc.article_dict(a) for a in articles]}).data)


class HelpArticleView(APIView):
    @extend_schema(operation_id="portal_help_retrieve", tags=TAG,
                   responses={200: sp.ArticleDetailSerializer, **ERR_404})
    def get(self, request, slug):
        from complaints.portal_views import _articles

        svc.external_or_404(request.user)
        article = _articles(request.user).filter(slug=slug).first()
        if article is None:
            raise Http404
        return Response(sp.ArticleDetailSerializer(svc.article_dict(article, body=True)).data)


class HelpVoteSerializer(serializers.Serializer):
    helpful = serializers.BooleanField()

    class Meta:
        ref_name = "PortalHelpVote"


class HelpArticleVoteView(APIView):
    @extend_schema(operation_id="portal_help_vote", tags=TAG, request=HelpVoteSerializer,
                   responses={204: None, **ERR_400_404})
    def post(self, request, slug):
        """"Was this helpful?" (the article page's vote)."""
        from complaints.portal_views import _articles

        svc.external_or_404(request.user)
        article = _articles(request.user).filter(slug=slug).first()
        if article is None:
            raise Http404
        data = body(HelpVoteSerializer, request)
        field = "helpful" if data["helpful"] else "not_helpful"
        type(article).objects.filter(pk=article.pk).update(**{field: F(field) + 1})
        return Response(status=status.HTTP_204_NO_CONTENT)


class ComplaintDetailView(APIView):
    @extend_schema(operation_id="portal_complaints_retrieve", tags=TAG,
                   responses={200: sp.ComplaintDetailSerializer, **ERR_404})
    def get(self, request, pk):
        return _detail(request, svc.complaint_or_404(request.user, pk))


class ComplaintReplyView(APIView):
    @extend_schema(operation_id="portal_complaints_reply", tags=TAG,
                   request={"multipart/form-data": sp.ComplaintReplySerializer,
                            "application/json": sp.ComplaintReplySerializer},
                   responses={201: sp.ComplaintDetailSerializer, **ERR_400_404})
    def post(self, request, pk):
        from complaints import services

        c = svc.complaint_or_404(request.user, pk)
        _readonly(request)
        data = body(sp.ComplaintReplySerializer, request)
        with website_rules():
            services.reply(c, request.user, data.get("body", ""), files=request.FILES.getlist("files"))
        return _detail(request, c, status.HTTP_201_CREATED)


class ComplaintActionView(APIView):
    @extend_schema(operation_id="portal_complaints_action", tags=TAG, request=sp.ComplaintActionSerializer,
                   responses={200: sp.ComplaintDetailSerializer, **ERR_400_404})
    def post(self, request, pk, action):
        """confirm (with an optional 1–5 satisfaction) | reopen (remark) | withdraw — only the ``actions`` offered."""
        from complaints import services

        c = svc.complaint_or_404(request.user, pk)
        _readonly(request)
        if action not in ("confirm", "reopen", "withdraw"):
            raise Forbidden("Not available.")
        data = body(sp.ComplaintActionSerializer, request)
        with website_rules():
            services.act(c, action, request.user, remark=data.get("remark", ""))
        rating = data.get("satisfaction")
        if action == "confirm" and rating:
            complaint_services.record_satisfaction(c, request.user, int(rating))
        return _detail(request, c)


class ComplaintFileView(APIView):
    @extend_schema(operation_id="portal_complaints_file", tags=TAG, responses={200: binary(), **ERR_404})
    def get(self, request, pk, file_pk):
        """An attachment of a public message (never an internal one)."""
        from complaints.models import ComplaintAttachment, ComplaintMessage
        from complaints.portal_views import attachment_response

        c = svc.complaint_or_404(request.user, pk)
        att = ComplaintAttachment.objects.filter(pk=file_pk, complaint=c).select_related("message").first()
        if att is None or not att.file or (att.message_id and att.message.visibility != ComplaintMessage.PUBLIC):
            raise Http404
        return attachment_response(request, att, channel="mobile app")
