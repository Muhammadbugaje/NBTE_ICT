"""Institution portal pages (portal host): dashboard data, institution
profile, contacts and addresses, users, documents, timeline and
self-registration. Every read goes through ``Model.objects.for_user`` (§4);
writes go through ``institutions.services``."""
from __future__ import annotations

import datetime

from django import forms
from django.contrib import messages
from django.contrib.auth import login
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

import config
from core import ratelimit
from qa_core.portal_views import _institution_for, _shell_context, institution_required
from qa_core.ui import is_htmx, paginate, render_qa, toast

from . import services
from .forms import QAForm, InstitutionProfileFields, state_choices
from .models import (
    DocumentKind,
    Institution,
    InstitutionContact,
    InstitutionDocument,
    InstitutionOwnership,
    InstitutionUser,
    Programme,
)


def _inst(request):
    return Institution.objects.for_user(request.user).select_related(
        "ownership", "institution_type", "category", "state", "handling_department").first()


def _open_application(request, institution):
    from accreditation.models import Application

    return (Application.objects.for_user(request.user).filter(institution=institution)
            .exclude(status="withdrawn").order_by("-created_at").first())


def _profile_editable(request, institution):
    """Institutions edit their own profile only before they submit; after that,
    changes are NBTE actions (Update Contact, Update Address …, §6)."""
    if institution.status not in (Institution.PROPOSED,):
        return False
    app = _open_application(request, institution)
    return app is None or app.status == "draft"


# ===========================================================================
# Dashboard data
# ===========================================================================
def dashboard_context(request, institution):
    from payments.models import PaymentObligation

    user = request.user
    actions = []
    app = _open_application(request, institution)
    if institution.status in (Institution.PROPOSED, Institution.UNDER_REVIEW):
        if app is None:
            actions.append({"tone": "info", "title": "Start your onboarding application",
                            "body": "Complete your profile, upload the statutory documents and submit.",
                            "url": reverse("qa_portal:application"), "cta": "Start"})
        elif app.status == "draft":
            if app.return_remark:
                actions.append({"tone": "danger", "title": "Your application was returned",
                                "body": app.return_remark, "url": reverse("qa_portal:application"),
                                "cta": "Fix and resubmit"})
            else:
                summary = services.checklist_summary(services.checklist(institution))
                actions.append({"tone": "info", "title": "Finish and submit your application",
                                "body": f"{summary['ready']} of {summary['mandatory']} required documents uploaded.",
                                "url": reverse("qa_portal:application"), "cta": "Continue"})
    obligations = list(PaymentObligation.objects.for_user(user).filter(institution=institution)
                       .order_by("-created_at")[:20])
    for ob in obligations:
        if ob.status in ("awaiting_payment", "rejected", "partially_paid"):
            label = {"rejected": "Payment not confirmed — pay again or upload new proof",
                     "partially_paid": "Part payment received — pay the balance"}.get(ob.status, "Payment due")
            from payments.money import naira

            actions.append({"tone": "danger" if ob.status == "rejected" else "warn", "title": label,
                            "body": f"{ob.description} · {naira(ob.balance)} · ref {ob.reference}",
                            "url": reverse("qa_portal:payment", args=[ob.pk]), "cta": "Open"})
    docs = [r for r in services.checklist(institution) if r["state"] == "returned"]
    for row in docs:
        actions.append({"tone": "danger", "title": f"{row['kind'].name} was returned",
                        "body": row["document"].review_remark, "url": reverse("qa_portal:documents"),
                        "cta": "Upload again"})
    programmes = list(Programme.objects.for_user(user).filter(institution=institution).select_related("level"))
    board = {}
    for p in programmes:
        board.setdefault(p.get_status_display(), 0)
        board[p.get_status_display()] += 1
    soon = timezone.localdate() + datetime.timedelta(days=180)
    expiring = [p for p in programmes if p.expiry_date and p.expiry_date <= soon and p.status != "dropped"]
    return {"action_items": actions, "programme_board": sorted(board.items()), "programme_count": len(programmes),
            "expiring": sorted(expiring, key=lambda p: p.expiry_date)[:6], "application": app,
            "open_bills": [o for o in obligations if not o.is_settled and o.status != "cancelled"][:3]}


# ===========================================================================
# Institution (profile, contacts, addresses, users)
# ===========================================================================
class PortalProfileForm(InstitutionProfileFields):
    registrar_name = forms.CharField(max_length=150, required=False, label="Registrar")
    bursar_name = forms.CharField(max_length=150, required=False, label="Bursar")


@institution_required
@require_http_methods(["GET", "POST"])
def institution(request):
    inst = _inst(request)
    editable = _profile_editable(request, inst)
    form = None
    if editable:
        initial = _profile_initial(inst)
        form = PortalProfileForm(request.POST or None, initial=initial)
        if request.method == "POST":
            if form.is_valid():
                try:
                    _save_profile(inst, form.cleaned_data, request.user)
                except ValidationError as exc:
                    form.add_error(None, exc)
                else:
                    if is_htmx(request):
                        response = render(request, "qa_portal/institution/_profile_form.html",
                                          _shell_context(request, "institution", form=PortalProfileForm(
                                              initial=_profile_initial(_inst(request))), saved=True,
                                              lgas=services.lgas_for(inst.state)))
                        return toast(response, "success", "Profile saved")
                    messages.success(request, "Profile saved.")
                    return redirect("qa_portal:institution")
            if is_htmx(request):
                return render(request, "qa_portal/institution/_profile_form.html",
                              _shell_context(request, "institution", form=form, lgas=services.lgas_for(inst.state)),
                              status=422)
    elif request.method == "POST":
        raise Http404
    ctx = _shell_context(request, "institution", inst=inst, editable=editable, form=form,
                         contacts=services.contacts_of(inst), addresses=services.addresses_of(inst),
                         members=services.members_of(inst), lgas=services.lgas_for(inst.state),
                         can_manage_users=_can_manage_users(request))
    return render(request, "qa_portal/institution/page.html", ctx)


def _profile_initial(inst):
    contact = {c.kind: c for c in services.contacts_of(inst)}
    addr = inst.address("permanent_site") or inst.address()
    return {
        "name": inst.name, "ownership": inst.ownership_id, "institution_type": inst.institution_type_id,
        "category": inst.category_id, "state": inst.state_id, "lga": inst.lga,
        "year_established": inst.year_established, "cac_rc_number": inst.cac_rc_number, "website": inst.website,
        "official_email": getattr(contact.get("official_email"), "value", ""),
        "phone": getattr(contact.get("phone"), "value", ""),
        "head_name": getattr(contact.get("head_of_institution"), "name", ""),
        "registrar_name": getattr(contact.get("registrar"), "name", ""),
        "bursar_name": getattr(contact.get("bursar"), "name", ""),
        "address_line1": addr.line1 if addr else "", "address_town": addr.town if addr else "",
    }


def _save_profile(inst, d, user):
    from django.db import transaction

    with transaction.atomic():
        name = d["name"].strip()
        if name != inst.name and Institution.objects.for_user(user).filter(pk=inst.pk).exists():
            services.ensure_unique_name(name, exclude=inst.pk)
        inst.name = name
        inst.ownership, inst.institution_type, inst.category = d["ownership"], d["institution_type"], d["category"]
        inst.state, inst.lga = d["state"], d["lga"]
        inst.year_established = d.get("year_established")
        inst.cac_rc_number = d.get("cac_rc_number", "")
        inst.website = d.get("website", "")
        inst.recompute_zone()
        inst.save()
        current = {c.kind: c for c in services.contacts_of(inst)}
        wanted = {"official_email": (d["official_email"].lower(), ""), "phone": (d["phone"], ""),
                  "head_of_institution": ("", d["head_name"]), "registrar": ("", d.get("registrar_name", "")),
                  "bursar": ("", d.get("bursar_name", ""))}
        for kind, (value, name_) in wanted.items():
            if not value and not name_:
                continue
            c = current.get(kind)
            if c is None or c.value != value or c.name != name_:
                services.set_contact(inst, kind, value, name_, actor=user)
        addr = inst.address("permanent_site")
        if addr is None or addr.line1 != d["address_line1"] or addr.town != d.get("address_town", "") \
                or addr.state_id != d["state"].pk or addr.lga != d["lga"]:
            services.set_address(inst, "permanent_site", line1=d["address_line1"], town=d.get("address_town", ""),
                                 lga=d["lga"], state=d["state"], actor=user)


def _can_manage_users(request):
    if getattr(request, "is_impersonating", False):
        return False
    link = InstitutionUser.objects.for_user(request.user).filter(user=request.user).first()
    role = getattr(request.user.role, "code", "")
    return role == "inst-admin" or bool(link and link.is_primary_contact)


class InviteUserForm(QAForm):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    role = forms.ChoiceField(choices=[("inst-academic-dept", "Academic department (SSQ sections)"),
                                      ("inst-chief-executive", "Chief Executive (submits SSQs)"),
                                      ("inst-admin", "Institutional Administrator")])
    job_title = forms.CharField(max_length=120, required=False)
    institution_department = forms.CharField(
        max_length=120, required=False, label="Department",
        help_text="The nominee's department in the institution (checked for an Institutional Administrator).")
    grade_level = forms.IntegerField(required=False, min_value=1, max_value=17,
                                     help_text="Required for an Institutional Administrator (R-03).")

    def clean(self):
        data = super().clean()
        if data.get("role") == "inst-admin":
            try:
                minimum = int(config.get("qa.institution.admin_min_grade_level", 12))
            except (TypeError, ValueError):
                minimum = 12
            grade = data.get("grade_level")
            if not grade:
                self.add_error("grade_level", "Enter the nominee's grade level.")
            elif grade < minimum:
                self.add_error("grade_level", f"An Institutional Administrator must be on grade level {minimum} or "
                                              "above (R-03).")
        return data


@institution_required
@require_http_methods(["GET", "POST"])
def users(request):
    inst = _inst(request)
    can_manage = _can_manage_users(request)
    form = InviteUserForm(request.POST or None)
    if request.method == "POST":
        if not can_manage:
            raise Http404
        if form.is_valid():
            try:
                services.invite_user(inst, form.cleaned_data, actor=request.user, request=request)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                response = render(request, "qa_portal/institution/_users.html",
                                  _shell_context(request, "institution", members=services.members_of(inst),
                                                 invite_form=InviteUserForm(), can_manage_users=True, inst=inst))
                return toast(response, "success", "Invitation sent",
                             f"{form.cleaned_data['email']} will receive a link to set a password.")
        return render(request, "qa_portal/institution/_users.html",
                      _shell_context(request, "institution", members=services.members_of(inst), invite_form=form,
                                     can_manage_users=True, inst=inst), status=422)
    return render(request, "qa_portal/institution/_users.html",
                  _shell_context(request, "institution", members=services.members_of(inst),
                                 invite_form=form, can_manage_users=can_manage, inst=inst))


@institution_required
def lga_options(request):
    """<option> list for the LGA field, refreshed when the state changes."""
    from staff.models import StateOfOrigin

    state = StateOfOrigin.objects.filter(pk=request.GET.get("state") or 0).first()
    return render(request, "qa_core/partials/_lga_options.html", {"lgas": services.lgas_for(state)})


# ===========================================================================
# Documents
# ===========================================================================
DOC_STATES = [("", "Any status"), ("missing", "Missing"), ("submitted", "Awaiting review"),
              ("accepted", "Accepted"), ("returned", "Returned"), ("expired", "Needs refresh")]


def _uploads_allowed(request, inst, row):
    if getattr(request, "is_impersonating", False):
        return False
    app = _open_application(request, inst)
    if row["state"] in ("missing", "returned", "expired"):
        return app is None or app.status in ("draft",) or inst.status != Institution.UNDER_REVIEW
    return app is None or app.status == "draft"


@institution_required
def documents(request):
    inst = _inst(request)
    rows = services.checklist(inst)
    for row in rows:
        row["can_upload"] = _uploads_allowed(request, inst, row)
        row["max_mb"] = services.max_document_mb(row["kind"])
        row["accept"] = ",".join("." + e for e in row["kind"].extensions())
    state = request.GET.get("state", "")
    dept = request.GET.get("dept", "")
    need = request.GET.get("need", "")
    q = (request.GET.get("q") or "").strip().lower()
    shown = [r for r in rows if (not state or r["state"] == state)
             and (not dept or (r["department"] and r["department"].code == dept))
             and (not need or (need == "required") == r["mandatory"])
             and (not q or q in r["kind"].name.lower())]
    departments = sorted({(r["department"].code, r["department"].name) for r in rows if r["department"]},
                         key=lambda x: x[1])
    history = list(InstitutionDocument.objects.for_user(request.user).filter(institution=inst)
                   .select_related("kind", "uploaded_by").order_by("kind__name", "-version")[:200])
    page = paginate(request, shown)  # Phase 6 (§20): 25 rows with "Show all"
    ctx = _shell_context(request, "documents", inst=inst, rows=page["rows"], page=page, all_rows=rows,
                         summary=services.checklist_summary(rows), filters=request.GET, states=DOC_STATES,
                         departments=departments, history=history)
    return render_qa(request, "qa_portal/documents/page.html", ctx,
                     {"qa-doc-rows": "qa_portal/documents/_rows.html"})


@institution_required
@require_POST
def document_upload(request, kind_id):
    inst = _inst(request)
    kind = DocumentKind.objects.filter(pk=kind_id, is_active=True).first()
    rows = {r["kind"].pk: r for r in services.checklist(inst)}
    if kind is None or kind.pk not in rows:
        raise Http404
    row = rows[kind.pk]
    if not _uploads_allowed(request, inst, row):
        response = render(request, "qa_portal/documents/_row.html",
                          _shell_context(request, "documents", row=_decorate_row(request, inst, row),
                                         error="Documents are locked while NBTE reviews your application."))
        return toast(response, "error", "Upload not allowed", "Documents are locked while NBTE reviews them.")
    upload = request.FILES.get("file")
    app = _open_application(request, inst)
    try:
        services.upload_document(inst, kind, upload, user=request.user, application=app)
    except ValidationError as exc:
        response = render(request, "qa_portal/documents/_row.html",
                          _shell_context(request, "documents", row=_decorate_row(request, inst, row),
                                         error=" ".join(exc.messages)), status=422)
        return toast(response, "error", "Not uploaded", " ".join(exc.messages))
    fresh = {r["kind"].pk: r for r in services.checklist(inst)}[kind.pk]
    response = render(request, "qa_portal/documents/_row.html",
                      _shell_context(request, "documents", row=_decorate_row(request, inst, fresh), just_uploaded=True))
    response["HX-Trigger-After-Swap"] = "qa:documents-changed"
    return toast(response, "success", f"{kind.name} uploaded", "NBTE will review it with your application.")


def _decorate_row(request, inst, row):
    row["can_upload"] = _uploads_allowed(request, inst, row)
    row["max_mb"] = services.max_document_mb(row["kind"])
    row["accept"] = ",".join("." + e for e in row["kind"].extensions())
    return row


@institution_required
def documents_summary(request):
    inst = _inst(request)
    rows = services.checklist(inst)
    return render(request, "qa_portal/documents/_summary.html",
                  _shell_context(request, "documents", summary=services.checklist_summary(rows)))


# ===========================================================================
# Timeline
# ===========================================================================
TIMELINE_KINDS = [("", "All activity"), ("application", "Application"), ("payment", "Payments"),
                  ("document", "Documents"), ("institution", "Institution record")]
PERIODS = {"30d": 30, "90d": 90, "1y": 365}


@institution_required
def timeline(request):
    from qa_core.models import TimelineEvent

    qs = TimelineEvent.objects.for_user(request.user).filter(visibility="public")
    kind = request.GET.get("kind", "")
    if kind == "application":
        qs = qs.filter(Q(event_code__startswith="application") | Q(event_code__startswith="clearance"))
    elif kind == "payment":
        qs = qs.filter(event_code__startswith="payment")
    elif kind == "document":
        qs = qs.filter(event_code__startswith="document")
    elif kind == "institution":
        qs = qs.filter(Q(event_code__startswith="institution") | Q(event_code__startswith="contact")
                       | Q(event_code__startswith="address") | Q(event_code__startswith="portal"))
    period = request.GET.get("period", "")
    if period in PERIODS:
        qs = qs.filter(at__gte=timezone.now() - datetime.timedelta(days=PERIODS[period]))
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(public_text__icontains=q)
    by = request.GET.get("by", "")
    if by == "you":
        qs = qs.filter(actor__user_type="institution")
    elif by == "nbte":
        qs = qs.exclude(actor__user_type="institution")
    page = paginate(request, qs.order_by("-at", "-id"))
    ctx = _shell_context(request, "timeline", page=page, filters=request.GET, kinds=TIMELINE_KINDS)
    return render_qa(request, "qa_portal/timeline.html", ctx, {"qa-timeline-rows": "qa_portal/_timeline_rows.html"})


# ===========================================================================
# Self-registration (proposed institutions, A5)
# ===========================================================================
class RegistrationForm(InstitutionProfileFields):
    admin_first_name = forms.CharField(max_length=150, label="Your first name")
    admin_last_name = forms.CharField(max_length=150, label="Your last name")
    admin_email = forms.EmailField(label="Your e-mail (you will sign in with it)")
    admin_job_title = forms.CharField(max_length=120, label="Your position")
    admin_department = forms.CharField(max_length=120, required=False, label="Your department",
                                       help_text="Your department in the institution.")
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    declaration = forms.BooleanField(label="I am authorised to register this institution with NBTE, and the "
                                           "information I give is true.")
    website_hp = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1",
                                                                                "autocomplete": "off"}))

    def clean(self):
        from django.contrib.auth import password_validation

        from accounts.models import User

        data = super().clean()
        if data.get("website_hp"):
            raise ValidationError("Please try again.")
        email = (data.get("admin_email") or "").lower()
        if email and User.objects.filter(email__iexact=email).exists():
            self.add_error("admin_email", "This e-mail already has an account. Sign in instead.")
        if data.get("password1") != data.get("password2"):
            self.add_error("password2", "The passwords do not match.")
        elif data.get("password1"):
            try:
                password_validation.validate_password(data["password1"])
            except ValidationError as exc:
                self.add_error("password1", exc)
        name = (data.get("name") or "").strip()
        if name:
            try:
                services.ensure_unique_name(name)
            except ValidationError as exc:
                self.add_error("name", exc)
        return data


@require_http_methods(["GET", "POST"])
def register(request):
    enabled = str(config.get("qa.portal.self_registration_enabled", True)).lower() in ("true", "1", "yes")
    if request.user.is_authenticated:
        return redirect("qa_portal:dashboard")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and enabled:
        ip = ratelimit.client_ip(request)
        if ratelimit.is_limited("qa-register", ip, 5):
            form.add_error(None, "Too many registrations from this network. Try again in an hour.")
        elif form.is_valid():
            ratelimit.hit("qa-register", ip, 3600)
            d = form.cleaned_data
            try:
                inst = services.create_institution({
                    "name": d["name"], "ownership": d["ownership"], "institution_type": d["institution_type"],
                    "category": d["category"], "state": d["state"], "lga": d["lga"],
                    "year_established": d.get("year_established"), "cac_rc_number": d.get("cac_rc_number", ""),
                    "website": d.get("website", ""),
                    "contacts": {"official_email": {"value": d["official_email"]}, "phone": {"value": d["phone"]},
                                 "head_of_institution": {"name": d["head_name"], "value": ""}},
                    "address": {"line1": d["address_line1"], "town": d.get("address_town", ""), "lga": d["lga"],
                                "state": d["state"]},
                    "admin": {"first_name": d["admin_first_name"], "last_name": d["admin_last_name"],
                              "email": d["admin_email"], "job_title": d["admin_job_title"],
                              "department": d.get("admin_department", "")},
                }, origin="self", activate_admin=True, admin_password=d["password1"])
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                user = inst.primary_user()
                login(request, user, backend="accounts.backends.RolePermissionBackend")
                messages.success(request, f"Welcome! {inst.name} is registered (code {inst.code}). "
                                          "Next: complete your application.")
                return redirect("qa_portal:application")
    from qa_core.portal_public import public_context

    ctx = public_context(request, "register")  # the public layout: nav, contacts, registration flag
    ctx.update(_shell_context(request, "register", form=form, enabled=enabled,
                              registration_closed_message=ctx["registration_closed_message"],
                              lgas=services.lgas_for(form.cleaned_data.get("state") if form.is_bound and hasattr(form, "cleaned_data") else None)))
    return render(request, "qa_portal/register.html", ctx)
