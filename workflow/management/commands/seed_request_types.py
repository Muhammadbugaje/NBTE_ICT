"""Seed the standard request categories, chains, and request types.

Idempotent — re-running updates existing rows by `code` without duplicating.

Cross-department requests get a requester-side chain attached automatically
(`internal-4-step`), so the request first runs through the requester's own
department before being handed off to the receiving department.

Form schemas are defined per request type. The workflow's dynamic form
builder renders them, and the schema's `asset_picker` field type scopes
choices to the requester's own assigned assets (or all assets for
superusers).

Usage:
    python manage.py seed_request_types
"""
import copy

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Role
from staff.models import Department
from workflow.models import (
    ApprovalChain,
    ApprovalStep,
    RequestCategory,
    RequestType,
)


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
CATEGORIES = [
    # code, name, icon, sort
    ("ict", "ICT", "server", 10),
    ("fleet", "Fleet & Vehicles", "car", 20),
    ("facilities", "Facilities", "building", 30),
    ("hr", "Human Resources", "users", 40),
    ("leave", "Leave", "calendar", 45),
    ("finance", "Finance", "credit-card", 50),
    ("general", "General", "folder", 100),
]


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------
def _step(position, name, kind="approval", approver_kind="role_in_department",
          role_code=None, scope="requester", is_optional=False, instructions=""):
    return {
        "position": position, "name": name, "kind": kind,
        "approver_kind": approver_kind, "role_code": role_code,
        "scope": scope, "is_optional": is_optional, "instructions": instructions,
    }


CHAINS = [
    (
        "internal-4-step",
        "Internal request (4-step)",
        "Requester's head → deputy → director. Standard internal chain, also "
        "used as the requester-side chain for cross-department requests.",
        [
            _step(1, "Line head approval", approver_kind="requester_head"),
            _step(2, "Deputy Director approval", approver_kind="requester_deputy",
                  is_optional=True),
            _step(3, "Director approval", approver_kind="requester_director"),
        ],
    ),
    (
        "ict-fulfillment",
        "ICT service request",
        "ICT-specific chain: receiving director approves, then assigns a staff "
        "member, who marks fulfilment, then the requester confirms handover.",
        [
            _step(1, "ICT Director approval", approver_kind="receiving_director"),
            _step(2, "Assign to staff", kind="assignment",
                  approver_kind="receiving_director", scope="receiving"),
            _step(3, "Mark fulfilled", kind="fulfillment",
                  approver_kind="receiving_director", scope="receiving"),
            _step(4, "Handover confirmation", kind="closure",
                  approver_kind="role_in_department", role_code="staff"),
        ],
    ),
    (
        "fleet-trip",
        "Fleet trip request",
        "Fleet Director approves a trip request, assigns a driver, then "
        "confirms return.",
        [
            _step(1, "Fleet Director approval", approver_kind="receiving_director"),
            _step(2, "Assign driver", kind="assignment",
                  approver_kind="receiving_director", scope="receiving"),
            _step(3, "Mark trip complete", kind="fulfillment",
                  approver_kind="receiving_director", scope="receiving"),
            _step(4, "Confirm vehicle returned", kind="closure",
                  approver_kind="requester_director"),
        ],
    ),
]


# ---------------------------------------------------------------------------
# Form schemas
# ---------------------------------------------------------------------------
# Each schema is {"fields": [...]}. Field types supported by the dynamic
# form builder:
#   text, textarea, number, date, select, checkbox, asset_picker, user_picker
#
# For asset_picker: `category_codes` narrows to specific asset categories
# (e.g. ["computer", "laptop"]). Regular users see only their assigned
# assets; superusers and access-all-departments users see every asset.
# ---------------------------------------------------------------------------

ICT_BROKEN_COMPUTER_SCHEMA = {
    "fields": [
        {"key": "asset", "label": "Which device?",
         "type": "asset_picker", "required": True,
         "category_codes": ["computer", "laptop"],
         "help": "Only devices currently assigned to you are shown."},
        {"key": "problem_type", "label": "What's wrong?",
         "type": "select", "required": True,
         "options": ["Won't turn on", "Won't boot past logo", "Very slow",
                     "Overheating / fan noise", "Screen issue",
                     "Keyboard or mouse not working", "Battery issue",
                     "Software / OS issue", "Other"]},
        {"key": "urgency", "label": "How urgent is this?",
         "type": "select", "required": True,
         "options": ["Can wait a week", "Need it this week",
                     "Blocking my work"]},
        {"key": "tried_steps", "label": "Anything you've already tried?",
         "type": "textarea", "required": False, "rows": 3,
         "help": "Optional — e.g. restart, different power socket."},
    ]
}

ICT_PRINTER_SCHEMA = {
    "fields": [
        {"key": "asset", "label": "Which printer?",
         "type": "asset_picker", "required": True,
         "category_codes": ["printer"],
         "help": "Only printers currently assigned to you are shown."},
        {"key": "problem_type", "label": "What's wrong?",
         "type": "select", "required": True,
         "options": ["Not printing at all", "Paper jam",
                     "Toner / ink issue", "Print quality",
                     "Not connecting to network", "Other"]},
        {"key": "needs_paper", "label": "Needs paper restock",
         "type": "checkbox", "required": False},
    ]
}

ICT_NETWORK_SCHEMA = {
    "fields": [
        {"key": "affected_area", "label": "Affected area",
         "type": "text", "required": True,
         "placeholder": "e.g. Block B, first floor"},
        {"key": "problem_type", "label": "What's the issue?",
         "type": "select", "required": True,
         "options": ["No internet", "Slow connection",
                     "Wi-Fi password issue", "Cable damage",
                     "Router/switch issue", "Other"]},
    ]
}

ICT_SOFTWARE_INSTALL_SCHEMA = {
    "fields": [
        {"key": "software_name", "label": "Software name",
         "type": "text", "required": True,
         "placeholder": "e.g. AutoCAD 2024"},
        {"key": "asset", "label": "Install on which device?",
         "type": "asset_picker", "required": True,
         "category_codes": ["computer", "laptop"]},
        {"key": "justification", "label": "Why is it needed?",
         "type": "textarea", "required": True, "rows": 3},
    ]
}

ICT_NEW_EQUIPMENT_SCHEMA = {
    "fields": [
        {"key": "equipment_type", "label": "Type of equipment",
         "type": "select", "required": True,
         "options": ["Computer", "Laptop", "Printer", "Scanner",
                     "Networking equipment", "Furniture", "Other"]},
        {"key": "quantity", "label": "Quantity",
         "type": "number", "required": True, "step": "1"},
        {"key": "justification", "label": "Why is it needed?",
         "type": "textarea", "required": True, "rows": 4},
        {"key": "estimated_cost", "label": "Estimated cost (₦)",
         "type": "number", "required": False},
    ]
}

FLEET_TRIP_SCHEMA = {
    "fields": [
        {"key": "asset", "label": "Which vehicle?",
         "type": "asset_picker", "required": True,
         "category_codes": ["vehicle"],
         "help": "Only vehicles you hold are shown. If you don't hold a "
                 "vehicle, leave blank and the Fleet office will assign one."},
        {"key": "departure_date", "label": "Departure date",
         "type": "date", "required": True},
        {"key": "return_date", "label": "Expected return date",
         "type": "date", "required": True},
        {"key": "destination", "label": "Destination",
         "type": "text", "required": True,
         "placeholder": "e.g. Zaria, Kaduna State"},
        {"key": "purpose", "label": "Purpose of the trip",
         "type": "textarea", "required": True, "rows": 3},
        {"key": "passengers", "label": "Number of passengers",
         "type": "number", "required": True, "step": "1"},
    ]
}

ANNUAL_LEAVE_SCHEMA = {
    "fields": [
        {"key": "start_date", "label": "First day of leave",
         "type": "date", "required": True},
        {"key": "end_date", "label": "Last day of leave",
         "type": "date", "required": True},
    ]
}

FLEET_MAINTENANCE_SCHEMA = {
    "fields": [
        {"key": "asset", "label": "Which vehicle?",
         "type": "asset_picker", "required": True,
         "category_codes": ["vehicle"]},
        {"key": "problem_type", "label": "Type of maintenance",
         "type": "select", "required": True,
         "options": ["Routine service", "Engine", "Tyres",
                     "Brakes", "Electrical", "Bodywork", "Other"]},
        {"key": "description", "label": "Describe the issue",
         "type": "textarea", "required": True, "rows": 4},
    ]
}

FACILITIES_REPAIR_SCHEMA = {
    "fields": [
        {"key": "location", "label": "Where?",
         "type": "text", "required": True,
         "placeholder": "e.g. Block B, Room 12"},
        {"key": "repair_type", "label": "What needs fixing?",
         "type": "select", "required": True,
         "options": ["Door", "Window", "Light / electricity",
                     "Plumbing", "Painting", "Furniture",
                     "Roof", "Other"]},
        {"key": "description", "label": "Describe the issue",
         "type": "textarea", "required": True, "rows": 4},
    ]
}

FACILITIES_AC_SCHEMA = {
    "fields": [
        {"key": "asset", "label": "Which unit?",
         "type": "asset_picker", "required": False,
         "category_codes": ["appliance"],
         "help": "Optional — select if the unit is on the asset register."},
        {"key": "location", "label": "Location",
         "type": "text", "required": True,
         "placeholder": "e.g. Director's office, Block A"},
        {"key": "problem_type", "label": "What's the issue?",
         "type": "select", "required": True,
         "options": ["Not cooling", "Leaking water", "Making noise",
                     "Won't turn on", "Remote issue", "Other"]},
        {"key": "description", "label": "Describe the issue",
         "type": "textarea", "required": True, "rows": 3},
    ]
}


# ---------------------------------------------------------------------------
# Request types
# ---------------------------------------------------------------------------
# Each is (code, name, category, handling_dept, chain_code, flags)
# `flags` may include a `form_schema` key that will be extracted and stored
# on the model as a separate field.
REQUEST_TYPES = [
    # ---- ICT ----
    ("ict-broken-computer", "Broken computer", "ict", "ict", "ict-fulfillment",
     {"is_cross_department": True, "form_schema": ICT_BROKEN_COMPUTER_SCHEMA}),
    ("ict-printer", "Printer problem", "ict", "ict", "ict-fulfillment",
     {"is_cross_department": True, "form_schema": ICT_PRINTER_SCHEMA}),
    ("ict-network", "Network / internet issue", "ict", "ict", "ict-fulfillment",
     {"is_cross_department": True, "form_schema": ICT_NETWORK_SCHEMA}),
    ("ict-software-install", "Software installation", "ict", "ict", "ict-fulfillment",
     {"is_cross_department": True, "form_schema": ICT_SOFTWARE_INSTALL_SCHEMA}),
    ("ict-new-equipment", "Request new equipment", "ict", "ict", "ict-fulfillment",
     {"is_cross_department": True, "requires_finance_approval": True,
      "form_schema": ICT_NEW_EQUIPMENT_SCHEMA}),

    # ---- Fleet ----
    ("fleet-vehicle-trip", "Vehicle trip", "fleet", "ict", "fleet-trip",
     {"is_cross_department": True, "form_schema": FLEET_TRIP_SCHEMA}),
    ("fleet-vehicle-maintenance", "Vehicle maintenance", "fleet", "ict", "fleet-trip",
     {"is_cross_department": True, "requires_finance_approval": True,
      "form_schema": FLEET_MAINTENANCE_SCHEMA}),

    # ---- Facilities ----
    ("facilities-office-repair", "Office repair", "facilities", "ict", "ict-fulfillment",
     {"is_cross_department": True, "form_schema": FACILITIES_REPAIR_SCHEMA}),
    ("facilities-ac", "Air conditioner issue", "facilities", "ict", "ict-fulfillment",
     {"is_cross_department": True, "form_schema": FACILITIES_AC_SCHEMA}),

    # ---- Human resources ----
    ("annual-leave", "Annual leave application", "leave", "hr", "internal-4-step",
     {"requires_hr_approval": True, "form_schema": ANNUAL_LEAVE_SCHEMA}),
]


class Command(BaseCommand):
    help = "Seed request categories, chains, request types, and form schemas."

    @transaction.atomic
    def handle(self, *args, **opts):
        # Deep-copy so repeated invocations in a long-lived process don't
        # mutate the module-level list (we pop `form_schema` out of flags).
        request_types = copy.deepcopy(REQUEST_TYPES)

        # --- categories -------------------------------------------------
        for code, name, icon, sort in CATEGORIES:
            cat = RequestCategory.objects.filter(code=code).first()
            created = cat is None
            if cat is None:
                cat = RequestCategory.objects.filter(name=name).first()
            if cat is None:
                cat = RequestCategory(code=code, name=name)
            cat.code = code
            cat.name = name
            cat.icon = icon
            cat.sort_order = sort
            cat.is_active = True
            cat.save()
            self.stdout.write(f"{'created' if created else 'exists '} category {code}")

        # --- chains -----------------------------------------------------
        for code, name, description, steps in CHAINS:
            chain, created = ApprovalChain.objects.update_or_create(
                code=code,
                defaults={"name": name, "description": description,
                          "is_active": True},
            )
            self.stdout.write(f"{'created' if created else 'exists '} chain {code}")

            for step in steps:
                role = None
                if step.get("role_code"):
                    role = Role.objects.filter(code=step["role_code"]).first()

                ApprovalStep.objects.update_or_create(
                    chain=chain, position=step["position"],
                    defaults={
                        "name": step["name"],
                        "kind": step.get("kind", "approval"),
                        "approver_kind": step["approver_kind"],
                        "approver_role": role,
                        "scope": step.get("scope", "requester"),
                        "is_optional": step.get("is_optional", False),
                        "instructions": step.get("instructions", ""),
                    },
                )

        # --- request types ----------------------------------------------
        default_requester_chain = ApprovalChain.objects.filter(
            code="internal-4-step"
        ).first()

        for code, name, category_code, dept_code, chain_code, flags in request_types:
            category = RequestCategory.objects.filter(code=category_code).first()
            dept = Department.objects.filter(code=dept_code).first()
            chain = ApprovalChain.objects.filter(code=chain_code).first()
            if not (category and dept and chain):
                self.stderr.write(
                    f"  skip {code}: missing category={category_code} "
                    f"dept={dept_code} chain={chain_code}"
                )
                continue

            # Pull form_schema out of flags so we can pass it as its own field.
            form_schema = flags.pop("form_schema", {}) if isinstance(flags, dict) else {}

            defaults = {
                "name": name,
                "category": category,
                "handling_department": dept,
                "approval_chain": chain,
                "is_active": True,
                "form_schema": form_schema,
                **flags,
            }

            # Cross-department types also get the requester-side chain.
            if flags.get("is_cross_department"):
                defaults["requester_chain"] = default_requester_chain

            RequestType.objects.update_or_create(code=code, defaults=defaults)
            self.stdout.write(
                f"upserted type {code}"
                + (f"  ({len(form_schema.get('fields', []))} schema fields)"
                   if form_schema else "")
            )

        self.stdout.write(self.style.SUCCESS("Seed complete."))