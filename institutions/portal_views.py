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
from core.forms_dynamic import DynamicFormMixin
from .profile import profile_gate
from qa_core.portal_views import _institution_for, _shell_context, institution_required
from qa_core.ui import htmx_fragment, is_htmx, paginate, render_qa, toast

from . import services
from . import change_requests as change_requests_service
from .forms import QAForm, InstitutionProfileFields, state_choices
from .models import (
    DocumentKind,
    Institution,
    InstitutionCategory,
    InstitutionContact,
    InstitutionChangeRequest,
    InstitutionDocument,
    InstitutionOwnership,
    InstitutionUser,
    ManagementRole,
    Programme,
)

PERSON_TITLE_CHOICES = [
    ("", "Select title"),
    ("Mr", "Mr"),
    ("Mrs", "Mrs"),
    ("Ms", "Ms"),
    ("Miss", "Miss"),
    ("Dr", "Dr"),
    ("Prof", "Prof"),
    ("Engr", "Engr"),
    ("Arc", "Arc"),
    ("Barr", "Barr"),
    ("Rev", "Rev"),
    ("Chief", "Chief"),
    ("Alhaji", "Alhaji"),
    ("Hajiya", "Hajiya"),
]


def _inst(request):
    return Institution.objects.for_user(request.user).select_related(
        "ownership", "institution_type", "category", "state", "handling_department").first()


def _open_application(request, institution):
    from accreditation.models import Application

    return (Application.objects.for_user(request.user).filter(institution=institution)
            .exclude(status="withdrawn").order_by("-created_at").first())


def _profile_editable(request, institution):
    """Keep the institution's profile editable until its first submission."""
    return not services.institution_team_locked(institution)


# ===========================================================================
# Dashboard data
# ===========================================================================
def dashboard_context(request, institution):
    from payments.models import PaymentObligation

    user = request.user
    actions = []
    app = _open_application(request, institution)
    from . import profile

    setup_open = profile.gate_enabled() and not profile.profile_complete(institution)
    if setup_open:  # v2.1 X13: the setup checklist comes first
        _steps, pct = profile.setup_checklist(institution)
        actions.append({"tone": "info", "title": "Finish setting up your portal",
                        "body": f"{pct}% done. Tell NBTE who runs {institution.name}; everything else unlocks "
                                "when you finish.", "url": reverse("qa_portal:setup"), "cta": "Continue setup", "kind": "application"})
    elif institution.status in (Institution.PROPOSED, Institution.UNDER_REVIEW):
        from accreditation import onboarding_v3

        if app is None:
            actions.append({"tone": "info", "title": "Start your onboarding application",
                            "body": ("Complete your profile, then the Chief Executive pays the required fees before "
                                     "document uploads unlock." if onboarding_v3.latest_flow_is_v3() else
                                     "Complete your profile, upload the statutory documents and submit."),
                            "url": reverse("qa_portal:application"), "cta": "Start", "kind": "application"})
        elif app.status == "draft":
            if app.return_remark:
                actions.append({"tone": "danger", "title": "Your application was returned",
                                "body": app.return_remark, "url": reverse("qa_portal:application"),
                                "cta": "Fix and resubmit", "kind": "application"})
            else:
                summary = services.checklist_summary(services.checklist(institution))
                actions.append({"tone": "info", "title": "Finish and submit your application",
                                "body": f"{summary['ready']} of {summary['mandatory']} required documents uploaded.",
                                "url": reverse("qa_portal:application"), "cta": "Continue", "kind": "application"})
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
    # v2.2: the journey tracker, figures at a glance, who at NBTE follows you, and help.
    from qa_core import spotlight

    journey = spotlight.progress(institution, app)
    if institution.status == Institution.APPROVED and not journey.get("case"):
        journey = None   # onboarding is history for an approved institution; show it only while a case runs
    open_bills = [o for o in obligations if not o.is_settled and o.status != "cancelled"]
    from decimal import Decimal

    owed = sum((o.balance for o in open_bills), Decimal("0"))
    kpis = [
        {"label": "Programmes", "value": len(programmes), "url": reverse("qa_portal:programme_board")
         if institution.status == Institution.APPROVED else ""},
        {"label": "Accredited", "value": sum(1 for p in programmes if p.status in ("accredited", "approved")),
         "url": ""},
        {"label": "To pay", "value": owed, "money": True, "url": reverse("qa_portal:payments") if open_bills else "",
         "tone": "warn" if owed else ""},
        {"label": "Documents returned", "value": len(docs), "tone": "danger" if docs else "",
         "url": reverse("qa_portal:documents") if docs else ""},
        {"label": "Expiring in 6 months", "value": len(expiring), "tone": "warn" if expiring else "", "url": ""},
    ]
    from assignments.models import WorkAssignment

    officer = (WorkAssignment.objects.filter(institution=institution, kind=WorkAssignment.OVERSIGHT_ICT,
                                             status__in=WorkAssignment.OPEN).select_related("assignee")
               .order_by("-assigned_at").first())
    contacts = {"handling": institution.handling_department, "ict_officer": officer.assignee if officer else None}
    next_state = _dashboard_state(request, institution, app, setup_open)
    if next_state is not None:  # v2.3: the "next step" card already says it; do not repeat it in the to-do list
        actions = [a for a in actions if a.get("kind") != "application"]
    return {"next_state": next_state,
            "bills_owed": owed, "bills_open": len(open_bills),"action_items": actions, "programme_board": sorted(board.items()), "programme_count": len(programmes),
            "expiring": sorted(expiring, key=lambda p: p.expiry_date)[:6], "application": app,
            "open_bills": open_bills[:3], "journey": journey, "kpis": kpis, "nbte_contacts": contacts}


def _dashboard_state(request, institution, app, setup_open):
    """v2.3: the dashboard's "where you are / what next" card (display only)."""
    if setup_open:
        from . import profile

        _steps, pct = profile.setup_checklist(institution)
        return {"title": "Finish setting up your portal", "who": "You", "mine": True, "tone": "gold",
                "icon": "users", "pct": pct, "step": 0, "steps": 0,
                "body": f"{pct}% done. Tell NBTE who runs {institution.name} — it takes one sitting, and "
                        "everything else unlocks when you finish.",
                "cta_label": "Continue setup", "cta_url": reverse("qa_portal:setup"), "cta_icon": "arrow"}
    try:
        from accreditation import onboarding_v2, onboarding_v3
        from accreditation.portal_views import _obligations, journey_state

        if institution.status == Institution.APPROVED and (app is None or app.status in ("ssq_submitted", "withdrawn")):
            return None  # onboarding is history; the dashboard's to-do list leads instead
        head = onboarding_v2.header(app, institution) if (app is None or onboarding_v2.is_v2(app)) else None
        returned = 0
        if app is not None:
            from .models import DocumentReview

            returned = DocumentReview.objects.for_user(request.user).filter(
                application=app, status=DocumentReview.RETURNED).count()
        self_studies = None
        if app is not None and app.status == "ssq_in_progress":
            from accreditation.models import OnboardingSSQ

            self_studies = list(OnboardingSSQ.objects.for_user(request.user).filter(application=app)[:1])
        return journey_state(institution, app, head=head, obligations=_obligations(request, app),
                             is_ce=services.is_chief_executive(request.user), returned=returned,
                             docs=services.checklist_summary(services.checklist(institution)),
                             self_studies=self_studies,
                             hold=services.active_hold(app) if app is not None else None,
                             v3=onboarding_v3.is_v3(app) if app is not None else onboarding_v3.latest_flow_is_v3())
    except Exception:  # the dashboard must never break on the summary card
        import logging

        logging.getLogger(__name__).exception("dashboard state failed")
        return None


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
        if services.is_chief_executive(request.user):
            messages.error(request, "Your profile is locked after application submission. Submit a change request.")
            return redirect(f"{reverse('qa_portal:change_requests_new')}?type=profile")
        raise Http404
    ctx = _shell_context(request, "institution", inst=inst, editable=editable, form=form,
                         contacts=services.contacts_of(inst), addresses=services.addresses_of(inst),
                         members=services.members_of(inst), lgas=services.lgas_for(inst.state),
                         is_ce=services.is_chief_executive(request.user),
                         can_manage_users=_can_manage_users(request) and not services.institution_team_locked(inst))
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
    # Decision 32 / D-3: the Chief Executive can do everything the administrator can.
    return role in ("inst-admin", "inst-chief-executive") or bool(link and link.is_primary_contact)


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
@htmx_fragment("qa_portal:institution", "#qa-users")
def users(request):
    inst = _inst(request)
    locked = services.institution_team_locked(inst)
    can_manage = _can_manage_users(request) and not locked
    form = InviteUserForm(request.POST or None)
    if request.method == "POST":
        if locked and _can_manage_users(request):
            if services.is_chief_executive(request.user):
                messages.error(request, "Institution users are locked after application submission. Submit a change request.")
                return redirect(f"{reverse('qa_portal:change_requests_new')}?type=user_invite")
            messages.error(request, "Institution users are locked. Ask the Chief Executive to submit an NBTE change request.")
            return redirect("qa_portal:change_requests")
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
                                                 invite_form=InviteUserForm(), can_manage_users=True, inst=inst,
                                                 team_locked=False,
                                                 can_submit_change_request=services.is_chief_executive(request.user)))
                return toast(response, "success", "Invitation sent",
                             f"{form.cleaned_data['email']} will receive a link to set a password.")
        return render(request, "qa_portal/institution/_users.html",
                      _shell_context(request, "institution", members=services.members_of(inst), invite_form=form,
                                     can_manage_users=True, inst=inst, team_locked=False,
                                     can_submit_change_request=services.is_chief_executive(request.user)), status=422)
    return render(request, "qa_portal/institution/_users.html",
                  _shell_context(request, "institution", members=services.members_of(inst),
                                 invite_form=form, can_manage_users=can_manage, inst=inst, team_locked=locked,
                                 can_submit_change_request=services.is_chief_executive(request.user)))


class InstitutionChangeRequestForm(QAForm):
    change_type = forms.ChoiceField(choices=InstitutionChangeRequest.CHANGE_TYPES, label="What needs to change?")
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Reason for the request")
    management_role = forms.ModelChoiceField(
        queryset=ManagementRole.objects.none(), required=False, label="Management role")
    title = forms.CharField(max_length=30, required=False, label="Title")
    full_name = forms.CharField(max_length=150, required=False, label="Full name")
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=30, required=False)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    role = forms.ChoiceField(choices=InviteUserForm.base_fields["role"].choices, required=False)
    job_title = forms.CharField(max_length=120, required=False)
    institution_department = forms.CharField(max_length=120, required=False, label="Department")
    grade_level = forms.IntegerField(required=False, min_value=1, max_value=17)
    membership_id = forms.ModelChoiceField(queryset=InstitutionUser.objects.none(), required=False,
                                                          label="Institution user to remove")

    def __init__(self, *args, institution, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["management_role"].queryset = ManagementRole.objects.filter(
            is_active=True, is_chief_executive=False, is_ict_head=False).order_by("sort_order", "name")
        self.fields["membership_id"].queryset = InstitutionUser.unscoped.filter(
            institution=institution, is_active_link=True, account_kind=InstitutionUser.STAFF_USER
        ).select_related("user", "institution_role").order_by("user__first_name", "user__last_name")
        self.fields["membership_id"].label_from_instance = lambda row: (
            f"{row.user.get_full_name() or row.user.email} — "
            f"{row.institution_role.name if row.institution_role_id else 'Institution user'}")

    def clean(self):
        data = super().clean()
        kind = data.get("change_type")
        required = {
            InstitutionChangeRequest.MANAGEMENT: ("management_role", "full_name"),
            InstitutionChangeRequest.USER_INVITE: ("first_name", "last_name", "email", "role"),
            InstitutionChangeRequest.USER_REMOVE: ("membership_id",),
        }.get(kind, ())
        for field in required:
            if not data.get(field):
                self.add_error(field, "This field is required for the selected change.")
        return data


@institution_required
def change_requests(request):
    inst = _inst(request)
    rows = InstitutionChangeRequest.objects.for_user(request.user).filter(institution=inst).select_related(
        "requested_by", "assigned_to", "decided_by")
    return render(request, "qa_portal/institution/change_requests.html",
                  _shell_context(request, "institution", inst=inst, requests=rows,
                                 can_submit_change_request=services.is_chief_executive(request.user)))


@institution_required
@require_http_methods(["GET", "POST"])
def change_requests_new(request):
    inst = _inst(request)
    if not services.is_chief_executive(request.user):
        raise Http404("Only the Chief Executive can submit institution change requests.")
    if not services.institution_team_locked(inst):
        messages.info(request, "You can make these changes directly until the first application is submitted.")
        return redirect("qa_portal:management")
    selected_type = request.POST.get("change_type") or request.GET.get("type")
    if selected_type not in dict(InstitutionChangeRequest.CHANGE_TYPES):
        return redirect("qa_portal:change_requests")
    form = InstitutionChangeRequestForm(request.POST or None, institution=inst,
                                        initial={"change_type": selected_type})
    profile_form = PortalProfileForm(
        request.POST or None, initial=_profile_initial(inst), prefix="profile") \
        if selected_type == InstitutionChangeRequest.PROFILE else None
    valid = form.is_valid() if request.method == "POST" else False
    if request.method == "POST" and valid and profile_form is not None:
        valid = profile_form.is_valid()
    if request.method == "POST" and valid:
        data = form.cleaned_data
        payload = {}
        if selected_type == InstitutionChangeRequest.PROFILE:
            profile_data = profile_form.cleaned_data
            payload = {"profile": {key: value.pk if getattr(value, "pk", None) else value
                                   for key, value in profile_data.items()}}
        elif selected_type == InstitutionChangeRequest.MANAGEMENT:
            payload = {"role_code": data["management_role"].code, "title": data["title"],
                       "full_name": data["full_name"], "email": data["email"], "phone": data["phone"]}
        elif selected_type == InstitutionChangeRequest.USER_INVITE:
            payload = {key: data.get(key) for key in ("first_name", "last_name", "email", "role", "job_title",
                                                       "institution_department", "grade_level")}
        elif selected_type == InstitutionChangeRequest.USER_REMOVE:
            payload = {"membership_id": data["membership_id"].pk}
        try:
            row = change_requests_service.submit(inst, actor=request.user, change_type=selected_type,
                                                 data=payload, reason=data["reason"])
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                for field, errors in exc.message_dict.items():
                    form.add_error(field if field in form.fields else None, errors)
            else:
                form.add_error(None, getattr(exc, "messages", [str(exc)]))
        else:
            messages.success(request, f"Change request {row.reference} was sent to NBTE for review.")
            return redirect("qa_portal:change_requests")
    return render(request, "qa_portal/institution/change_request_form.html",
                  _shell_context(request, "institution", inst=inst, form=form, profile_form=profile_form,
                                 selected_type=selected_type))


@institution_required
def lga_options(request):
    """<option> list for the LGA field, refreshed when the state changes."""
    from staff.models import StateOfOrigin

    state = StateOfOrigin.objects.filter(pk=request.GET.get("state") or 0).first()
    return render(request, "qa_core/partials/_lga_options.html", {"lgas": services.lgas_for(state)})


@require_http_methods(["GET"])
def public_lga_options(request):
    """X49 slice: <option> rows for the LGA <select> on the public register
    page — no sign-in; rate-limited per network (qa.public.lga_rate_per_minute)."""
    from django.http import HttpResponse
    from django.utils.cache import patch_cache_control, patch_vary_headers

    from staff.models import StateOfOrigin

    try:
        limit = int(config.get("qa.public.lga_rate_per_minute", 60))
    except (TypeError, ValueError):
        limit = 60
    ip = ratelimit.client_ip(request)
    if ratelimit.is_limited("qa-public-lga", ip, limit):
        response = HttpResponse("Too many requests. Wait a minute and try again.", status=429,
                                content_type="text/plain")
        response["Retry-After"] = "60"
        return response
    ratelimit.hit("qa-public-lga", ip, 60)
    state = StateOfOrigin.objects.filter(pk=request.GET.get("state") or 0).first()
    response = render(request, "qa_portal/_lga_select_options.html",
                      {"lgas": services.lgas_for(state), "state": state, "selected": request.GET.get("lga", "")})
    # v2.1 X49: the server caches each state's list (version bumped on edit);
    # browsers keep it one minute so a new LGA appears within a minute.
    patch_cache_control(response, public=True, max_age=60)
    patch_vary_headers(response, ["HX-Request"])
    return response


# ===========================================================================
# Documents
# ===========================================================================
DOC_STATES = [("", "Any status"), ("missing", "Missing"), ("submitted", "Awaiting review"),
              ("accepted", "Accepted"), ("returned", "Returned"), ("expired", "Needs refresh")]


def _uploads_allowed(request, inst, row):
    if getattr(request, "is_impersonating", False):
        return False
    app = _open_application(request, inst)
    if row["state"] == "returned":
        # O8: a returned document can always be re-uploaded, even while NBTE
        # reviews the application; it goes back to that department only (O9).
        return True
    app = _open_application(request, inst)
    if app is None and inst.status == Institution.PROPOSED:
        from accreditation import onboarding_v3

        if onboarding_v3.latest_flow_is_v3():
            return False
    if app is not None:
        from accreditation import onboarding_v3

        if onboarding_v3.document_upload_blocked(app):
            return False
    if row["state"] in ("missing", "expired"):
        return app is None or app.status in ("draft",) or inst.status != Institution.UNDER_REVIEW
    return app is None or app.status == "draft"


@institution_required
@profile_gate  # v2.1 X13
def documents(request):
    inst = _inst(request)
    rows = services.checklist(inst)
    for row in rows:
        row["can_upload"] = _uploads_allowed(request, inst, row)
        row["upload_lock_reason"] = _upload_lock_reason(request, inst, row)
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
@profile_gate  # v2.1 X13
@require_POST
def document_upload(request, kind_id):
    inst = _inst(request)
    kind = DocumentKind.objects.filter(pk=kind_id, is_active=True).first()
    rows = {r["kind"].pk: r for r in services.checklist(inst)}
    if kind is None or kind.pk not in rows:
        raise Http404
    row = rows[kind.pk]
    if not _uploads_allowed(request, inst, row):
        app = _open_application(request, inst)
        from accreditation import onboarding_v3

        message = ("Required uploads unlock after NBTE Finance confirms the institutional and processing fees."
                   if app is not None and onboarding_v3.document_upload_blocked(app)
                   else "Documents are locked while NBTE reviews your application.")
        response = render(request, "qa_portal/documents/_row.html",
                          _shell_context(request, "documents", row=_decorate_row(request, inst, row),
                                         error=message))
        return toast(response, "error", "Upload not allowed", message)
    upload = request.FILES.get("file")
    app = _open_application(request, inst)
    try:
        doc = services.upload_document(inst, kind, upload, user=request.user, application=app)
    except ValidationError as exc:
        response = render(request, "qa_portal/documents/_row.html",
                          _shell_context(request, "documents", row=_decorate_row(request, inst, row),
                                         error=" ".join(exc.messages)), status=422)
        return toast(response, "error", "Not uploaded", " ".join(exc.messages))
    fresh = {r["kind"].pk: r for r in services.checklist(inst)}[kind.pk]
    routed = getattr(doc, "routed_department", None)
    response = render(request, "qa_portal/documents/_row.html",
                      _shell_context(request, "documents", row=_decorate_row(request, inst, fresh), just_uploaded=True,
                                     routed_department=routed))
    response["HX-Trigger-After-Swap"] = "qa:documents-changed"
    if routed is not None:
        return toast(response, "success", f"{kind.name} sent back to {routed.name}",
                     "Sent back to NBTE for review. You do not need to resubmit your application.")
    return toast(response, "success", f"{kind.name} uploaded", "NBTE will review it with your application.")


def _decorate_row(request, inst, row):
    row["can_upload"] = _uploads_allowed(request, inst, row)
    row["upload_lock_reason"] = _upload_lock_reason(request, inst, row)
    row["max_mb"] = services.max_document_mb(row["kind"])
    row["accept"] = ",".join("." + e for e in row["kind"].extensions())
    return row


def _upload_lock_reason(request, inst, row):
    if row["state"] == "returned":
        return ""
    app = _open_application(request, inst)
    if app is None and inst.status == Institution.PROPOSED:
        from accreditation import onboarding_v3

        if onboarding_v3.latest_flow_is_v3():
            return "Start the application and wait for NBTE Finance to confirm payment before uploading documents."
    if app is not None:
        from accreditation import onboarding_v3

        if onboarding_v3.document_upload_blocked(app):
            return "Uploads unlock after NBTE Finance confirms the institutional and processing fees."
    return ""


@institution_required
@htmx_fragment("qa_portal:documents")
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
class RegistrationForm(DynamicFormMixin, InstitutionProfileFields):
    """Self-registration (A5, rebuilt for X12/X46/X49/X50).

    X50 (spec §13.18.1, D-22): only what NBTE needs to register. Removed —
    the separate institution type (merged into "Type", the category), the
    website (kept on the profile), the head's single name (the Chief
    Executive card), the administrator's position and department (the Head
    of ICT role defines both). Shown only when relevant (core.forms_dynamic,
    the same rules on the server): the RC / CAC number for private ownership
    (then required), the year established for an institution that is already
    operating (then required)."""

    STATUS_CHOICES = [("proposed", "Proposed: not yet operating"),
                      ("existing", "Already operating")]
    REMOVED = ("institution_type", "website", "head_name")
    ADMIN_JOB_TITLE = "Head of ICT"

    operating_status = forms.ChoiceField(choices=STATUS_CHOICES, required=False, initial="proposed",
                                         label="Is the institution already operating?", widget=forms.RadioSelect)
    admin_first_name = forms.CharField(max_length=150, label="Your first name")
    admin_last_name = forms.CharField(max_length=150, label="Your last name")
    admin_email = forms.EmailField(label="Your e-mail (you will sign in with it)")
    admin_phone = forms.CharField(max_length=30, label="Your phone")
    # v2.1 X12 (F.13): the registrant is the Head of ICT (portal administrator);
    # the Chief Executive gets an invitation e-mail and is the only one who
    # declares, pays and submits.
    ce_title = forms.ChoiceField(choices=PERSON_TITLE_CHOICES, required=False, label="Title")
    ce_full_name = forms.CharField(max_length=150, label="Chief Executive's full name")
    ce_email = forms.EmailField(label="Chief Executive's e-mail")
    ce_phone = forms.CharField(max_length=30, label="Chief Executive's phone")
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    declaration = forms.BooleanField(label="I am authorised to register this institution with NBTE, and the "
                                           "information I give is true.")
    website_hp = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1",
                                                                                "autocomplete": "off"}))

    def __init__(self, *args, **kwargs):
        private = [str(pk) for pk in InstitutionOwnership.objects.filter(is_active=True, is_public=False)
                   .values_list("pk", flat=True)]
        self.show_if = {"cac_rc_number": {"ownership": private},
                        "year_established": {"operating_status": ["existing"]}}
        super().__init__(*args, **kwargs)
        for name in self.REMOVED:
            del self.fields[name]
        cat = self.fields["category"]
        cat.label = "Institution type"
        cat.queryset = InstitutionCategory.objects.filter(is_active=True).select_related("institution_type")
        cat.label_from_instance = lambda c: (f"{c.institution_type.name} · {c.name}"
                                             if c.institution_type_id and c.institution_type.name != c.name
                                             else c.name)
        # Required once visible (core.forms_dynamic decides visibility in clean()).
        self.fields["cac_rc_number"]._dynamic_required = True
        self.fields["cac_rc_number"].widget.attrs["data-dynamic-required"] = "true"
        self.fields["cac_rc_number"].label = "RC / CAC number"
        self.fields["cac_rc_number"].help_text = "Private institutions only."
        self.fields["year_established"]._dynamic_required = True
        self.fields["year_established"].widget.attrs["data-dynamic-required"] = "true"
        self.fields["year_established"].label = "Year established"
        self.fields["address_line1"].label = "Physical address"
        self.fields["address_town"].label = "Town / City"

    def clean(self):
        from django.contrib.auth import password_validation

        from accounts.models import User

        data = super().clean()
        if not data.get("operating_status"):
            data["operating_status"] = "proposed"
        cat = data.get("category")
        if cat is not None:
            if not cat.institution_type_id:
                self.add_error("category", "This type has no institution type. Choose another.")
            else:
                data["institution_type"] = cat.institution_type
        if data.get("website_hp"):
            raise ValidationError("Please try again.")
        email = (data.get("admin_email") or "").lower()
        if email and User.objects.filter(email__iexact=email).exists():
            self.add_error("admin_email", "This e-mail already has an account. Sign in instead.")
        ce_email = (data.get("ce_email") or "").lower()
        if ce_email and ce_email == email:
            self.add_error("ce_email", "The Chief Executive needs their own e-mail (two separate accounts).")
        elif ce_email and User.objects.filter(email__iexact=ce_email).exists():
            self.add_error("ce_email", "This e-mail already has an account.")
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


def _register_lga_context(form):
    """X49: the LGA <select> of the register page — the chosen state's LGAs,
    or (no state yet) every LGA grouped by state for browsers without JS."""
    from staff.models import StateOfOrigin

    state = None
    if form.is_bound:
        state = StateOfOrigin.objects.filter(pk=form.data.get("state") or 0).first()
    return {"lga_state": state, "lgas": services.lgas_for(state) if state else [],
            "lga_groups": [] if state else services.lga_groups(),
            "lga_selected": form.data.get("lga", "") if form.is_bound else ""}


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
                    "year_established": d.get("year_established"), "cac_rc_number": d.get("cac_rc_number") or "",
                    "website": "",  # X50: collected on the profile, not at registration
                    "contacts": {"official_email": {"value": d["official_email"]}, "phone": {"value": d["phone"]},
                                 "head_of_institution": {"name": d["ce_full_name"], "value": d["ce_email"]}},
                    "address": {"line1": d["address_line1"], "town": d.get("address_town", ""), "lga": d["lga"],
                                "state": d["state"]},
                    "admin": {"first_name": d["admin_first_name"], "last_name": d["admin_last_name"],
                              "email": d["admin_email"], "job_title": RegistrationForm.ADMIN_JOB_TITLE,
                              "department": "", "phone": d.get("admin_phone", ""),
                              "role": "inst-admin"},
                    "chief_executive": {"title": d.get("ce_title", ""), "full_name": d["ce_full_name"],
                                        "email": d["ce_email"], "phone": d["ce_phone"]},
                }, origin="self", activate_admin=True, admin_password=d["password1"], request=request)
            except ValidationError as exc:
                # X50: a service error may name a field the trimmed form no longer has.
                form.add_error(None, exc if not hasattr(exc, "error_dict")
                               or set(exc.error_dict) <= set(form.fields) else ValidationError(exc.messages))
            else:
                user = inst.primary_user()
                login(request, user, backend="accounts.backends.RolePermissionBackend")
                messages.success(request, f"Welcome! {inst.name} is registered (code {inst.code}). "
                                          "The Chief Executive's set-up link was sent to their own e-mail. "
                                          "Next: finish setup.")
                request.session["qa_celebrate"] = inst.code   # v2.2: one light confetti burst on arrival
                return redirect("qa_portal:setup")
    from qa_core.portal_public import public_context

    ctx = public_context(request, "register")  # the public layout: nav, contacts, registration flag
    ctx.update(_shell_context(request, "register", form=form, enabled=enabled,
                              registration_closed_message=ctx["registration_closed_message"],
                              **_register_lga_context(form)))
    return render(request, "qa_portal/register.html", ctx)


# ===========================================================================
# v2.1 X13 (F.14): setup checklist (/setup/) and the management team
# ===========================================================================
class ManagementStaffForm(QAForm):
    role = forms.ModelChoiceField(queryset=None, empty_label=None)
    title = forms.ChoiceField(choices=PERSON_TITLE_CHOICES, required=False, label="Title")
    full_name = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=30, required=False)

    def __init__(self, *args, institution=None, **kwargs):
        from django.db.models import Count

        from .models import ManagementRole, ManagementStaff

        super().__init__(*args, **kwargs)
        roles = ManagementRole.objects.filter(is_active=True)
        if institution is not None:
            filled = dict(ManagementStaff.unscoped.filter(institution=institution, is_current=True)
                          .values("role_id").annotate(total=Count("pk")).values_list("role_id", "total"))
            available_ids = [role.pk for role in roles if filled.get(role.pk, 0) < role.max_holders]
            selected = (self.data.get(self.add_prefix("role")) if self.is_bound
                        else self.initial.get("role"))
            try:
                selected_id = int(getattr(selected, "pk", selected))
            except (TypeError, ValueError):
                selected_id = None
            if selected_id and selected_id in filled:
                available_ids.append(selected_id)
            roles = roles.filter(pk__in=available_ids)
        self.fields["role"].queryset = roles.order_by("sort_order", "name")


def _can_edit_team(request):
    """Chief Executive and Head of ICT (G3.e): edit profile and management staff."""
    if getattr(request, "is_impersonating", False):
        return False
    link = InstitutionUser.objects.for_user(request.user).filter(user=request.user).first()
    return bool(link and link.account_kind in (InstitutionUser.CHIEF_EXECUTIVE, InstitutionUser.ICT_HEAD)) \
        or _can_manage_users(request)


def _is_ict_head(request, inst):
    if getattr(request, "is_impersonating", False):
        return False
    return InstitutionUser.objects.for_user(request.user).filter(
        institution=inst, user=request.user, account_kind=InstitutionUser.ICT_HEAD, is_active_link=True,
    ).exists()


def _setup_context(request, inst, **extra):
    from . import profile

    team_locked = services.institution_team_locked(inst)
    steps, percent = profile.setup_checklist(inst)
    shown = [s for s in steps if not s.hidden]
    links = {k: profile.account_link(inst, k) for k in ("chief_executive", "ict_head")}
    board = profile.role_board(inst)
    nxt = next((s for s in shown if not s.ok), None)
    next_url = nxt.action_url if nxt else ""
    if nxt is not None and nxt.code == "management":
        first = next((b for b in board if b["role"].is_mandatory and not b["ok"]), None)
        if first is not None:
            next_url = f"{nxt.action_url}?role={first['role'].code}"
    ctx = _shell_context(request, "institution", inst=inst, steps=shown, percent=percent,
                         done=sum(1 for s in shown if s.ok), total=len(shown),
                         next_step=nxt, next_url=next_url, remaining=len(shown) - sum(1 for s in shown if s.ok),
                         board=board, complete=all(s.ok for s in shown),
                         ce_link=links["chief_executive"], ict_link=links["ict_head"],
                         ce_state=profile.account_state(links["chief_executive"]),
                         ict_state=profile.account_state(links["ict_head"]),
                         is_ce=services.is_chief_executive(request.user),
                         can_edit=_can_edit_team(request) and not team_locked,
                         team_locked=team_locked,
                         can_submit_change_request=services.is_chief_executive(request.user),
                         form=extra.pop("form", None) or ManagementStaffForm(institution=inst), **extra)
    return ctx


@institution_required
def setup(request):
    inst = _inst(request)
    # v2.2: the Chief Executive's set-up link is never shown on the
    # institution side (it would let the Head of ICT set the CE's password).
    request.session.pop("qa_ce_activation_link_once", None)
    activation = None
    if activation:
        from qa_core import timeline

        timeline.record(inst, "credentials.activation_link_revealed", actor=request.user, visibility="internal",
                        internal_text=f"One-time Chief Executive setup link displayed to the Head of ICT for {activation['email']}.")
    celebrate = request.session.pop("qa_celebrate", None) == inst.code
    response = render(request, "qa_portal/setup.html",
                      _setup_context(request, inst, ce_activation=activation, celebrate=celebrate))
    if activation:
        response["Cache-Control"] = "no-store, private"
        response["Pragma"] = "no-cache"
    return response


@institution_required
@require_http_methods(["GET", "POST"])
def management(request):
    from . import profile
    from .models import ManagementRole

    inst = _inst(request)
    locked = services.institution_team_locked(inst)
    can_edit = _can_edit_team(request) and not locked
    initial = {}
    role_code = request.GET.get("role", "")
    if role_code:
        role = ManagementRole.objects.filter(code=role_code, is_active=True).first()
        if role:
            initial["role"] = role.pk
    form = ManagementStaffForm(request.POST or None, initial=initial, institution=inst)
    if request.method == "POST":
        if locked and _can_edit_team(request):
            if services.is_chief_executive(request.user):
                messages.error(request, "Management changes are locked after application submission. Submit a change request.")
                return redirect(f"{reverse('qa_portal:change_requests_new')}?type=management")
            messages.error(request, "Management changes are locked. Ask the Chief Executive to submit an NBTE change request.")
            return redirect("qa_portal:change_requests")
        if not can_edit:
            raise Http404
        if form.is_valid():
            try:
                row = profile.set_holder(inst, form.cleaned_data["role"], form.cleaned_data, actor=request.user)
            except ValidationError as exc:
                if hasattr(exc, "error_dict"):
                    for f, errs in exc.message_dict.items():
                        form.add_error(f if f in form.fields else None, errs)
                else:
                    form.add_error(None, exc)
            else:
                messages.success(request, f"{row.role.name}: {row.display_name} saved.")
                return redirect("qa_portal:management")
    history = list(services_history(inst))
    ctx = _shell_context(request, "institution", inst=inst, board=profile.role_board(inst), form=form, can_edit=can_edit,
                         history=history, team_locked=locked,
                         can_submit_change_request=services.is_chief_executive(request.user))
    return render(request, "qa_portal/institution/management.html", ctx, status=422 if form.errors else 200)


def services_history(inst):
    from .models import ManagementStaff

    return (ManagementStaff.unscoped.filter(institution=inst, is_current=False).select_related("role")
            .order_by("-end_date", "-id")[:30])


@institution_required
@require_POST
def ce_resend(request):
    """X12 (F.13): the Head of ICT resends the Chief Executive's invitation (rate-limited)."""
    inst = _inst(request)
    if not _is_ict_head(request, inst):
        raise Http404
    try:
        user = services.resend_ce_invitation(inst, actor=request.user, request=request)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, f"A new set-up link was e-mailed to the Chief Executive ({user.email}). "
                                  "Only they can open it.")
    return redirect("qa_portal:setup")
