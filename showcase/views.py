"""Serves and manages showcase image files. Files live in protected storage
(same pattern as review finding H1), so they are never reachable under
/media/. The public image view streams them or hands them to nginx via
X-Accel-Redirect in production. Management views (list/create/update/delete)
are staff-only and gated by the ``showcase.add_showcaseimage`` permission."""
import logging
import mimetypes
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .forms import ShowcaseImageForm
from .models import ShowcaseImage
from .services import is_publicly_visible

logger = logging.getLogger(__name__)


def _can_manage(user):
    return user.is_authenticated and (
        user.is_superuser
        or user.has_perm("showcase.manage_showcase")
        or user.has_perm("showcase.add_showcaseimage")
    )


def _require_manage(user):
    if not _can_manage(user):
        raise PermissionDenied("You do not have permission to manage showcase images.")


# ---------- public streaming view (unchanged behavior) --------------------

@require_GET
def image(request, pk):
    obj = get_object_or_404(ShowcaseImage, pk=pk)
    if not obj.image:
        raise Http404
    if not is_publicly_visible(obj) and not (
        request.user.is_authenticated and request.user.is_staff
    ):
        raise Http404
    name = obj.image.name
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if not ctype.startswith("image/"):
        raise Http404
    if settings.USE_X_ACCEL_REDIRECT:
        response = HttpResponse(content_type=ctype)
        response["X-Accel-Redirect"] = settings.PROTECTED_MEDIA_URL + quote(name)
    else:
        response = FileResponse(obj.image.open("rb"), content_type=ctype)
    response["Cache-Control"] = "public, max-age=86400"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = "inline"
    return response


# ---------- in-app management --------------------------------------------

@staff_member_required
def manage_list(request):
    _require_manage(request.user)

    qs = ShowcaseImage.objects.all().order_by("order", "-created_at")
    placement = request.GET.get("placement")
    if placement in ShowcaseImage.PLACEMENT_CODES:
        qs = qs.filter(placements__regex=r"(^|,)%s(,|$)" % placement)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(title__icontains=q) | Q(caption__icontains=q) | Q(alt_text__icontains=q)
        )

    paginator = Paginator(qs, 24)
    page = paginator.get_page(request.GET.get("page") or 1)

    return render(request, "showcase/manage/list.html", {
        "page": page,
        "paginator": paginator,
        "placements": ShowcaseImage.PLACEMENT_CHOICES,
        "placement_filter": placement or "",
        "q": q,
        "counts": {
            "total": ShowcaseImage.objects.count(),
            "active": ShowcaseImage.objects.filter(is_active=True).count(),
        },
    })


@staff_member_required
def manage_create(request):
    _require_manage(request.user)

    form = ShowcaseImageForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.created_by = request.user
        obj.save()
        logger.info("showcase: created #%s (%s) by %s", obj.pk, obj, request.user)
        messages.success(request, f"“{obj}” added.")
        return redirect("showcase:manage_list")

    return render(request, "showcase/manage/form.html", {
        "form": form, "is_create": True, "obj": None,
    })


@staff_member_required
def manage_update(request, pk):
    _require_manage(request.user)

    obj = get_object_or_404(ShowcaseImage, pk=pk)
    form = ShowcaseImageForm(request.POST or None, request.FILES or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        logger.info("showcase: updated #%s (%s) by %s", obj.pk, obj, request.user)
        messages.success(request, f"“{obj}” updated.")
        return redirect("showcase:manage_list")

    return render(request, "showcase/manage/form.html", {
        "form": form, "is_create": False, "obj": obj,
    })


@staff_member_required
@require_POST
def manage_delete(request, pk):
    _require_manage(request.user)

    obj = get_object_or_404(ShowcaseImage, pk=pk)
    name = str(obj)
    obj.delete()
    logger.info("showcase: deleted #%s (%s) by %s", pk, name, request.user)
    messages.success(request, f"“{name}” deleted.")
    return redirect("showcase:manage_list")


@staff_member_required
@require_POST
def manage_toggle(request, pk):
    _require_manage(request.user)

    obj = get_object_or_404(ShowcaseImage, pk=pk)
    obj.is_active = not obj.is_active
    obj.save(update_fields=["is_active", "updated_at"])
    logger.info("showcase: toggled #%s (%s) -> %s by %s",
                obj.pk, obj, obj.is_active, request.user)
    messages.success(
        request,
        f"“{obj}” is now {'shown' if obj.is_active else 'hidden'}.",
    )
    return redirect("showcase:manage_list")